"""走廊网格 + A* 正交避障路由（route: avoid）。

算法概要
--------
1. 障碍物：节点视觉外边界（accent/stack 已计入）与独立 text 包围盒，
   各自外扩 CLEARANCE_MM；起/终点所在盒（及包含端点的容器）豁免。
2. 走廊网格：障碍物边线 + 锚点/stub + 画布边收集 H/V 通道坐标，
   交点为节点；边不穿障则连通。
3. A*：四邻接正交；代价 = 段长 + TURN_PENALTY × 拐弯次数。
4. 首末 stub：沿源/目标边外法向离开/进入，保证垂直进出。
5. Nudging：共享走廊的平行段按序错开 ≥ NUDGE_GAP_MM。
6. 失败：返回 None，由调用方降级为 auto 并记 lint 警告。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .spec import Rect

# 可配置常量（日后可暴露到 theme / figure）
CLEARANCE_MM = 2.0
NUDGE_GAP_MM = 1.5
TURN_PENALTY = 12.0          # 每个拐弯等价额外 mm
MIN_APPROACH_MM = 3.0
_COORD_QUANT = 0.05         # 坐标量化，保证稳定可复现
_EPS = 1e-6


@dataclass
class RouteRequest:
    id: str
    x1: float
    y1: float
    s1: str
    x2: float
    y2: float
    s2: str
    exclude_ids: set[str] = field(default_factory=set)


@dataclass
class RouteResult:
    points: list[tuple[float, float]]
    fallback: bool = False
    message: str = ""


def _q(v: float) -> float:
    return round(v / _COORD_QUANT) * _COORD_QUANT


def _side_outward(side: str) -> tuple[float, float]:
    return {
        "left": (-1.0, 0.0), "right": (1.0, 0.0),
        "top": (0.0, -1.0), "bottom": (0.0, 1.0),
    }.get(side, (0.0, 0.0))


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not pts:
        return pts
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > _EPS or abs(p[1] - out[-1][1]) > _EPS:
            out.append(p)
    return out


def _compress_collinear(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = _dedupe(pts)
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        # 共线水平或竖直 → 跳过中点
        if abs(ay - by) < _EPS and abs(by - cy) < _EPS:
            continue
        if abs(ax - bx) < _EPS and abs(bx - cx) < _EPS:
            continue
        out.append(pts[i])
    out.append(pts[-1])
    return _dedupe(out)


def _point_in_rect(x: float, y: float, r: Rect, margin: float = 0.0) -> bool:
    return (r.x + margin) < x < (r.right - margin) and (r.y + margin) < y < (r.bottom - margin)


def _segment_hits_rect(x1: float, y1: float, x2: float, y2: float, r: Rect) -> bool:
    """开线段与矩形是否相交（端点落在边界上不算穿心）。"""
    if r.w <= 0 or r.h <= 0:
        return False
    # 快速排斥：完全在外侧
    if max(x1, x2) < r.x - _EPS or min(x1, x2) > r.right + _EPS:
        return False
    if max(y1, y2) < r.y - _EPS or min(y1, y2) > r.bottom + _EPS:
        return False
    # 轴对齐段：检查是否真正进入内部（非贴边滑行）
    if abs(y1 - y2) < _EPS:  # 水平
        y = y1
        if y <= r.y + _EPS or y >= r.bottom - _EPS:
            return False
        lo, hi = sorted((x1, x2))
        return hi > r.x + _EPS and lo < r.right - _EPS
    if abs(x1 - x2) < _EPS:  # 竖直
        x = x1
        if x <= r.x + _EPS or x >= r.right - _EPS:
            return False
        lo, hi = sorted((y1, y2))
        return hi > r.y + _EPS and lo < r.bottom - _EPS
    # 斜线（理论上不会出现）：Liang-Barsky
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - r.x), (dx, r.right - x1),
                 (-dy, y1 - r.y), (dy, r.bottom - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _fit_stub(side: str, tip: tuple[float, float], other: tuple[float, float],
              min_len: float = MIN_APPROACH_MM) -> float:
    if side not in ("left", "right", "top", "bottom"):
        return 0.0
    if side in ("left", "right"):
        avail = abs(other[0] - tip[0])
    else:
        avail = abs(other[1] - tip[1])
    if avail < 1.0:
        return 0.0
    return min(min_len, max(0.0, (avail - 1.0) / 2.0))


def _approach(tip: tuple[float, float], side: str, length: float) -> tuple[float, float]:
    ox, oy = _side_outward(side)
    return _q(tip[0] + ox * length), _q(tip[1] + oy * length)


def expand_obstacles(obstacles: list[tuple[str, Rect]], clearance: float = CLEARANCE_MM
                     ) -> list[tuple[str, Rect]]:
    return [(oid, r.expanded(clearance)) for oid, r in obstacles]


def build_corridor_graph(
    obstacles: list[Rect],
    xs_extra: list[float],
    ys_extra: list[float],
    canvas: Rect,
) -> tuple[list[float], list[float], set[tuple[int, int]], dict[tuple[int, int], list[tuple[int, int]]]]:
    """返回 (xs, ys, free_nodes, adjacency)。节点用 (ix, iy) 索引。"""
    xs: set[float] = {_q(canvas.x), _q(canvas.right)}
    ys: set[float] = {_q(canvas.y), _q(canvas.bottom)}
    for r in obstacles:
        xs.add(_q(r.x))
        xs.add(_q(r.right))
        ys.add(_q(r.y))
        ys.add(_q(r.bottom))
        # 走廊中线，增加绕行柔性
        xs.add(_q((r.x + r.right) / 2))
        ys.add(_q((r.y + r.bottom) / 2))
    for v in xs_extra:
        xs.add(_q(v))
    for v in ys_extra:
        ys.add(_q(v))

    # 画布内再补少量均匀采样，避免大空区无通道
    for i in range(1, 4):
        xs.add(_q(canvas.x + canvas.w * i / 4))
        ys.add(_q(canvas.y + canvas.h * i / 4))

    xs_l = sorted(x for x in xs if canvas.x - _EPS <= x <= canvas.right + _EPS)
    ys_l = sorted(y for y in ys if canvas.y - _EPS <= y <= canvas.bottom + _EPS)

    def free(ix: int, iy: int) -> bool:
        x, y = xs_l[ix], ys_l[iy]
        for r in obstacles:
            if _point_in_rect(x, y, r, margin=_EPS):
                return False
        return True

    nodes: set[tuple[int, int]] = {
        (ix, iy) for ix in range(len(xs_l)) for iy in range(len(ys_l))
        if free(ix, iy)
    }

    adj: dict[tuple[int, int], list[tuple[int, int]]] = {n: [] for n in nodes}
    # 水平边
    for iy in range(len(ys_l)):
        row = sorted(ix for ix in range(len(xs_l)) if (ix, iy) in nodes)
        for a, b in zip(row, row[1:]):
            x1, x2 = xs_l[a], xs_l[b]
            y = ys_l[iy]
            blocked = any(_segment_hits_rect(x1, y, x2, y, r) for r in obstacles)
            if not blocked:
                adj[(a, iy)].append((b, iy))
                adj[(b, iy)].append((a, iy))
    # 竖直边
    for ix in range(len(xs_l)):
        col = sorted(iy for iy in range(len(ys_l)) if (ix, iy) in nodes)
        for a, b in zip(col, col[1:]):
            y1, y2 = ys_l[a], ys_l[b]
            x = xs_l[ix]
            blocked = any(_segment_hits_rect(x, y1, x, y2, r) for r in obstacles)
            if not blocked:
                adj[(ix, a)].append((ix, b))
                adj[(ix, b)].append((ix, a))

    return xs_l, ys_l, nodes, adj


def _nearest_node(xs: list[float], ys: list[float], nodes: set[tuple[int, int]],
                  x: float, y: float,
                  obstacles: list[Rect] | None = None) -> tuple[int, int] | None:
    """找离 (x,y) 最近的自由网格节点；优先同轴（保持 stub 正交）。"""
    best = None
    best_d = float("inf")
    x, y = _q(x), _q(y)
    for ix, iy in nodes:
        px, py = xs[ix], ys[iy]
        # 同轴优先
        axis_pen = 0.0
        if abs(px - x) > _EPS and abs(py - y) > _EPS:
            axis_pen = 4.0
        d = abs(px - x) + abs(py - y) + axis_pen
        if obstacles is not None:
            # 连接点到目标的 stub 不得穿障
            if abs(py - y) < _EPS or abs(px - x) < _EPS:
                if any(_segment_hits_rect(x, y, px, py, r) for r in obstacles):
                    continue
            else:
                # 需 L 折：试两种正交
                ok = False
                for mid in ((px, y), (x, py)):
                    if (not any(_segment_hits_rect(x, y, mid[0], mid[1], r) for r in obstacles)
                            and not any(_segment_hits_rect(mid[0], mid[1], px, py, r)
                                        for r in obstacles)):
                        ok = True
                        break
                if not ok:
                    continue
        if d < best_d:
            best_d = d
            best = (ix, iy)
    return best


def _astar(xs: list[float], ys: list[float],
           adj: dict[tuple[int, int], list[tuple[int, int]]],
           start: tuple[int, int], goal: tuple[int, int]
           ) -> list[tuple[float, float]] | None:
    if start == goal:
        return [(xs[start[0]], ys[start[1]])]

    def heur(n: tuple[int, int]) -> float:
        return abs(xs[n[0]] - xs[goal[0]]) + abs(ys[n[1]] - ys[goal[1]])

    # state: (cost, counter, node, prev_dir)
    # prev_dir: (dx_sign, dy_sign) or None
    counter = 0
    open_h: list[tuple[float, int, tuple[int, int], tuple[int, int] | None]] = []
    heapq.heappush(open_h, (heur(start), counter, start, None))
    g_score: dict[tuple[tuple[int, int], tuple[int, int] | None], float] = {(start, None): 0.0}
    came: dict[tuple[tuple[int, int], tuple[int, int] | None],
               tuple[tuple[int, int], tuple[int, int] | None] | None] = {(start, None): None}
    best_goal_key = None
    best_goal_g = float("inf")

    while open_h:
        f, _, cur, pdir = heapq.heappop(open_h)
        key = (cur, pdir)
        g = g_score.get(key, float("inf"))
        if g + heur(cur) > f + 1e-6:
            continue
        if cur == goal:
            if g < best_goal_g:
                best_goal_g = g
                best_goal_key = key
            continue
        if best_goal_key is not None and g > best_goal_g + TURN_PENALTY:
            continue

        cx, cy = xs[cur[0]], ys[cur[1]]
        for nxt in adj.get(cur, []):
            nx, ny = xs[nxt[0]], ys[nxt[1]]
            dx = 0 if abs(nx - cx) < _EPS else (1 if nx > cx else -1)
            dy = 0 if abs(ny - cy) < _EPS else (1 if ny > cy else -1)
            ndir = (dx, dy)
            step = abs(nx - cx) + abs(ny - cy)
            turn = 0.0
            if pdir is not None and ndir != pdir:
                turn = TURN_PENALTY
            ng = g + step + turn
            nkey = (nxt, ndir)
            if ng + _EPS < g_score.get(nkey, float("inf")):
                g_score[nkey] = ng
                came[nkey] = key
                counter += 1
                heapq.heappush(open_h, (ng + heur(nxt), counter, nxt, ndir))

    if best_goal_key is None:
        return None

    # 回溯
    path_idx: list[tuple[int, int]] = []
    k: tuple[tuple[int, int], tuple[int, int] | None] | None = best_goal_key
    while k is not None:
        path_idx.append(k[0])
        k = came.get(k)
    path_idx.reverse()
    return [(xs[i], ys[j]) for i, j in path_idx]


def _raw_obstacles(
    obstacles: list[tuple[str, Rect]],
    tip1: tuple[float, float],
    tip2: tuple[float, float],
    exclude_ids: set[str],
) -> list[Rect]:
    raw: list[Rect] = []
    for oid, r in obstacles:
        if oid in exclude_ids:
            continue
        if _point_in_rect(tip1[0], tip1[1], r, margin=-0.2):
            continue
        if _point_in_rect(tip2[0], tip2[1], r, margin=-0.2):
            continue
        raw.append(r)
    return raw


def _shrink_stub_free(
    tip: tuple[float, float],
    side: str,
    length: float,
    active: list[Rect],
) -> tuple[tuple[float, float], float]:
    """缩短 stub 直至落点不在障碍内。"""
    if length <= _EPS or side not in ("left", "right", "top", "bottom"):
        return tip, 0.0
    lo, hi = 0.0, length
    best_len = 0.0
    best_pt = tip
    for _ in range(16):
        mid = (lo + hi) / 2
        pt = _approach(tip, side, mid)
        if any(_point_in_rect(pt[0], pt[1], r) for r in active):
            hi = mid
        else:
            best_len = mid
            best_pt = pt
            lo = mid
        if hi - lo < 0.05:
            break
    return best_pt, best_len


def _gap_channels(raw: list[Rect], canvas: Rect) -> tuple[list[float], list[float]]:
    """相邻盒子间隙中线 → 额外 H/V 通道（窄缝绕行关键）。"""
    xs: list[float] = []
    ys: list[float] = []
    # 按 x 排序找竖直间隙
    by_x = sorted(raw, key=lambda r: r.x)
    for a, b in zip(by_x, by_x[1:]):
        gap = b.x - a.right
        if 0.3 < gap < 40:
            xs.append(_q((a.right + b.x) / 2))
    by_y = sorted(raw, key=lambda r: r.y)
    for a, b in zip(by_y, by_y[1:]):
        gap = b.y - a.bottom
        if 0.3 < gap < 40:
            ys.append(_q((a.bottom + b.y) / 2))
    # 画布边缘走廊
    if raw:
        left = min(r.x for r in raw)
        right = max(r.right for r in raw)
        top = min(r.y for r in raw)
        bottom = max(r.bottom for r in raw)
        if left - canvas.x > 0.5:
            xs.append(_q((canvas.x + left) / 2))
        if canvas.right - right > 0.5:
            xs.append(_q((right + canvas.right) / 2))
        if top - canvas.y > 0.5:
            ys.append(_q((canvas.y + top) / 2))
        if canvas.bottom - bottom > 0.5:
            ys.append(_q((bottom + canvas.bottom) / 2))
    return xs, ys


def _route_with_clearance(
    req: RouteRequest,
    raw: list[Rect],
    canvas: Rect,
    clearance: float,
) -> list[tuple[float, float]] | None:
    tip1 = (_q(req.x1), _q(req.y1))
    tip2 = (_q(req.x2), _q(req.y2))
    active = [r.expanded(clearance) for r in raw]

    slen = _fit_stub(req.s1, tip1, tip2)
    elen = _fit_stub(req.s2, tip2, tip1)
    stub1 = _approach(tip1, req.s1, slen) if slen > _EPS else tip1
    stub2 = _approach(tip2, req.s2, elen) if elen > _EPS else tip2
    stub1, slen = _shrink_stub_free(tip1, req.s1, slen, active)
    stub2, elen = _shrink_stub_free(tip2, req.s2, elen, active)

    # 近对齐直连
    if abs(stub1[0] - stub2[0]) < _EPS or abs(stub1[1] - stub2[1]) < _EPS:
        if not any(_segment_hits_rect(stub1[0], stub1[1], stub2[0], stub2[1], r)
                   for r in active):
            return _compress_collinear([tip1, stub1, stub2, tip2])

    gap_xs, gap_ys = _gap_channels(raw, canvas)
    xs_extra = [tip1[0], tip2[0], stub1[0], stub2[0], *gap_xs]
    ys_extra = [tip1[1], tip2[1], stub1[1], stub2[1], *gap_ys]
    for s, (x, y) in ((req.s1, stub1), (req.s2, stub2)):
        ox, oy = _side_outward(s)
        xs_extra.append(_q(x + ox * max(clearance, 1.0)))
        ys_extra.append(_q(y + oy * max(clearance, 1.0)))

    # 不把网格扩到画布外（否则会绕到 figure 外「作弊」）
    xs, ys, nodes, adj = build_corridor_graph(active, xs_extra, ys_extra, canvas)
    if not nodes:
        return None

    n1 = _nearest_node(xs, ys, nodes, stub1[0], stub1[1], active)
    n2 = _nearest_node(xs, ys, nodes, stub2[0], stub2[1], active)
    if n1 is None or n2 is None:
        return None

    mid = _astar(xs, ys, adj, n1, n2)
    if mid is None:
        return None

    def _join(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
        if abs(a[0] - b[0]) < _EPS or abs(a[1] - b[1]) < _EPS:
            return [a, b]
        return [a, (b[0], a[1]), b]

    head = _join(stub1, mid[0])
    tail = _join(mid[-1], stub2)
    pts = [tip1] + head + mid[1:-1] + tail + [tip2]
    pts = _compress_collinear(pts)
    pts = _enforce_port_normals(pts, tip1, req.s1, tip2, req.s2)
    pts = _compress_collinear(pts)

    for a, b in zip(pts, pts[1:]):
        for r in active:
            if _segment_hits_rect(a[0], a[1], b[0], b[1], r):
                return None
    return pts


def route_orthogonal_avoid(
    req: RouteRequest,
    obstacles: list[tuple[str, Rect]],
    canvas: Rect,
    clearance: float = CLEARANCE_MM,
) -> list[tuple[float, float]] | None:
    """单条箭头避障正交路由。成功返回折线（含端点），失败返回 None。

    优先 clearance（默认 2mm）；窄缝图会依次降到 1.2 / 0.8 / 0.4 mm 再试，
    仍失败才返回 None（由调用方降级 auto）。
    """
    tip1 = (_q(req.x1), _q(req.y1))
    tip2 = (_q(req.x2), _q(req.y2))
    raw = _raw_obstacles(obstacles, tip1, tip2, req.exclude_ids)

    levels = []
    for c in (clearance, 1.2, 0.8, 0.4):
        if c not in levels and c > 0:
            levels.append(c)

    for c in levels:
        pts = _route_with_clearance(req, raw, canvas, c)
        if pts is not None:
            return pts
    return None


def _enforce_port_normals(
    pts: list[tuple[float, float]],
    tip1: tuple[float, float], s1: str,
    tip2: tuple[float, float], s2: str,
) -> list[tuple[float, float]]:
    """确保第一段离开源边、最后一段进入目标边为法向。"""
    if len(pts) < 2:
        return pts
    out = list(pts)
    out[0] = tip1
    out[-1] = tip2

    if s1 in ("left", "right") and len(out) >= 2:
        # 第一段应水平：把 out[1].y 钉到 tip1.y
        if abs(out[1][1] - tip1[1]) > _EPS:
            out[1] = (out[1][0], tip1[1])
        ox, _ = _side_outward(s1)
        if (out[1][0] - tip1[0]) * ox < -_EPS:
            # 方向反了：插入法向 stub
            stub = _approach(tip1, s1, MIN_APPROACH_MM)
            out = [tip1, stub] + out[1:]
    elif s1 in ("top", "bottom") and len(out) >= 2:
        if abs(out[1][0] - tip1[0]) > _EPS:
            out[1] = (tip1[0], out[1][1])
        _, oy = _side_outward(s1)
        if (out[1][1] - tip1[1]) * oy < -_EPS:
            stub = _approach(tip1, s1, MIN_APPROACH_MM)
            out = [tip1, stub] + out[1:]

    if s2 in ("left", "right") and len(out) >= 2:
        if abs(out[-2][1] - tip2[1]) > _EPS:
            out[-2] = (out[-2][0], tip2[1])
        ox, _ = _side_outward(s2)
        if (out[-2][0] - tip2[0]) * ox < -_EPS:
            stub = _approach(tip2, s2, MIN_APPROACH_MM)
            out = out[:-1] + [stub, tip2]
    elif s2 in ("top", "bottom") and len(out) >= 2:
        if abs(out[-2][0] - tip2[0]) > _EPS:
            out[-2] = (tip2[0], out[-2][1])
        _, oy = _side_outward(s2)
        if (out[-2][1] - tip2[1]) * oy < -_EPS:
            stub = _approach(tip2, s2, MIN_APPROACH_MM)
            out = out[:-1] + [stub, tip2]

    return _dedupe(out)


# ── Nudging ─────────────────────────────────────────────

@dataclass
class _SegRef:
    aid: str
    si: int          # segment index in path
    horiz: bool
    coord: float     # y if horiz else x
    a: float         # range start (x if horiz else y)
    b: float         # range end


def _path_segments(aid: str, pts: list[tuple[float, float]]) -> list[_SegRef]:
    segs = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if abs(y0 - y1) < _EPS and abs(x0 - x1) > _EPS:
            segs.append(_SegRef(aid, i, True, y0, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) < _EPS and abs(y0 - y1) > _EPS:
            segs.append(_SegRef(aid, i, False, x0, min(y0, y1), max(y0, y1)))
    return segs


def _ranges_overlap(a0: float, a1: float, b0: float, b1: float, tol: float = 0.5) -> bool:
    return not (a1 < b0 + tol or b1 < a0 + tol)


def nudge_paths(
    paths: dict[str, list[tuple[float, float]]],
    gap: float = NUDGE_GAP_MM,
) -> dict[str, list[tuple[float, float]]]:
    """对共享走廊的平行段错开，间距 ≥ gap。

    不挪动首段/末段（进出 stub），避免破坏垂直进出。
    """
    if len(paths) < 2:
        return paths

    work = {aid: [list(p) for p in pts] for aid, pts in paths.items()}
    all_segs: list[_SegRef] = []
    for aid, pts in paths.items():
        nseg = len(pts) - 1
        for s in _path_segments(aid, pts):
            # 跳过进出 stub
            if s.si == 0 or s.si == nseg - 1:
                continue
            all_segs.append(s)

    buckets: dict[tuple[bool, int], list[_SegRef]] = {}
    for s in all_segs:
        key = (s.horiz, int(round(s.coord / 0.25)))
        buckets.setdefault(key, []).append(s)

    offsets: dict[tuple[str, int], float] = {}
    for group in buckets.values():
        if len(group) < 2:
            continue
        used = [False] * len(group)
        for i, s in enumerate(group):
            if used[i]:
                continue
            cluster = [s]
            used[i] = True
            changed = True
            while changed:
                changed = False
                for j, t in enumerate(group):
                    if used[j]:
                        continue
                    if any(_ranges_overlap(c.a, c.b, t.a, t.b) for c in cluster):
                        cluster.append(t)
                        used[j] = True
                        changed = True
            if len(cluster) < 2:
                continue
            cluster.sort(key=lambda s: (s.aid, s.si))
            n = len(cluster)
            for k, s in enumerate(cluster):
                offsets[(s.aid, s.si)] = (k - (n - 1) / 2.0) * gap

    for (aid, si), off in offsets.items():
        pts = work[aid]
        n = len(pts)
        if si <= 0 or si >= n - 2:
            continue
        x0, y0 = paths[aid][si]
        x1, y1 = paths[aid][si + 1]
        horiz = abs(y0 - y1) < _EPS
        # 两端都不是 tip（si>=1 且 si+1 <= n-2）
        if horiz:
            pts[si][1] = paths[aid][si][1] + off
            pts[si + 1][1] = paths[aid][si + 1][1] + off
        else:
            pts[si][0] = paths[aid][si][0] + off
            pts[si + 1][0] = paths[aid][si + 1][0] + off

    result = {}
    for aid, pts_m in work.items():
        pts = [(_q(p[0]), _q(p[1])) for p in pts_m]
        pts[0] = paths[aid][0]
        pts[-1] = paths[aid][-1]
        snapped = [pts[0]]
        for p in pts[1:-1]:
            prev = snapped[-1]
            dx, dy = abs(p[0] - prev[0]), abs(p[1] - prev[1])
            if dx < 0.2:
                snapped.append((prev[0], p[1]))
            elif dy < 0.2:
                snapped.append((p[0], prev[1]))
            else:
                snapped.append(p)
        prev = snapped[-1]
        end = pts[-1]
        dx, dy = abs(end[0] - prev[0]), abs(end[1] - prev[1])
        if dx > _EPS and dy > _EPS:
            # 保持末段正交：按 tip 需要对齐的轴接
            snapped.append((end[0], prev[1]) if dx >= dy else (prev[0], end[1]))
        snapped.append(end)
        result[aid] = _compress_collinear(snapped)
    return result


def route_all(
    requests: list[RouteRequest],
    obstacles: list[tuple[str, Rect]],
    canvas: Rect,
) -> dict[str, RouteResult]:
    """批量路由 + nudging。失败条目 fallback=True、points 为空。"""
    raw: dict[str, list[tuple[float, float]]] = {}
    failed: dict[str, str] = {}
    req_by_id = {r.id: r for r in requests}

    for req in requests:
        pts = route_orthogonal_avoid(req, obstacles, canvas)
        if pts is None:
            failed[req.id] = (
                f"箭头 '{req.id}' 的 route:avoid 未找到无碰撞正交路径，已回退 auto"
            )
        else:
            raw[req.id] = pts

    if len(raw) >= 2:
        raw = nudge_paths(raw)

    # nudging 后重新钉死进出法向
    for aid, pts in list(raw.items()):
        req = req_by_id[aid]
        tip1, tip2 = pts[0], pts[-1]
        raw[aid] = _compress_collinear(
            _enforce_port_normals(pts, tip1, req.s1, tip2, req.s2))

    out: dict[str, RouteResult] = {}
    for req in requests:
        if req.id in failed:
            out[req.id] = RouteResult(points=[], fallback=True, message=failed[req.id])
        else:
            out[req.id] = RouteResult(points=raw[req.id], fallback=False)
    return out


# ── 标签候选打分 ─────────────────────────────────────────

@dataclass
class LabelCandidate:
    x: float          # 文本起点 x（与 _TextSpan 一致）
    baseline: float
    cap: Rect
    tag: str
    score: float = 0.0


def _point_seg_dist(p: tuple[float, float], a: tuple[float, float],
                    b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def label_candidates(
    pts: list[tuple[float, float]],
    label_w: float,
    label_h: float,
    asc: float,
    offsets: tuple[float, ...] = (
        1.6, -1.6, 2.4, -2.4, 3.6, -3.6, 5.0, -5.0, 7.0, -7.0, 9.0, -9.0,
    ),
) -> list[tuple[float, float, Rect, str, float]]:
    """生成候选：(text_x, baseline, cap, tag, segment_preference)。

    segment_preference 越大越优先（长段加分）。
    """
    out: list[tuple[float, float, Rect, str, float]] = []
    if len(pts) < 2:
        return out
    nseg = len(pts) - 1
    lengths = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(nseg)]
    max_len = max(lengths) if lengths else 1.0

    for i in range(nseg):
        a, b = pts[i], pts[i + 1]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        horiz = abs(b[0] - a[0]) >= abs(b[1] - a[1])
        pref = lengths[i] / max_len * 10.0
        if i == nseg - 1 and nseg >= 2:
            pref -= 8.0
        if i == 0 and nseg >= 3:
            pref -= 2.0
        for off in offsets:
            if horiz:
                tx = mx - label_w / 2
                # offset>0 → 线上方（baseline 在 my - off）
                ty = my - off
                bb_y = ty - asc
                cap = Rect(tx - 0.7, bb_y - 0.25, label_w + 1.4, label_h + 0.5)
                side = "above" if off > 0 else "below"
            else:
                tx = mx + off if off > 0 else mx + off - label_w
                ty = my + asc / 2 - 0.2
                bb_y = ty - asc
                cap = Rect(min(tx, mx) - 0.7 if off < 0 else tx - 0.7,
                           bb_y - 0.25, label_w + 1.4, label_h + 0.5)
                if off > 0:
                    cap = Rect(tx - 0.7, bb_y - 0.25, label_w + 1.4, label_h + 0.5)
                else:
                    cap = Rect(tx - 0.7, bb_y - 0.25, label_w + 1.4, label_h + 0.5)
                side = "right" if off > 0 else "left"
            out.append((tx, ty, cap, f"seg{i}:{side}:{off}", pref))

    # 拐角内外侧
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        for dx, dy, tag in (
            (2.0, -2.0 - asc, "NE"),
            (2.0, 2.0, "SE"),
            (-2.0 - label_w, -2.0 - asc, "NW"),
            (-2.0 - label_w, 2.0, "SW"),
        ):
            tx, ty = px + dx, py + dy + asc
            bb_y = ty - asc
            cap = Rect(tx - 0.7, bb_y - 0.25, label_w + 1.4, label_h + 0.5)
            out.append((tx, ty, cap, f"corner{i}:{tag}", 3.0))

    # 窄缝逃逸：沿路径包围盒外围滑移，宽标签塞不进短段时用
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    mid_x, mid_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    for dx, dy, tag, pref in (
        (-label_w - 1.2, -asc - 1.2, "escape:L-above", 4.0),
        (-label_w - 1.2, 1.5, "escape:L-below", 4.0),
        (1.2, -asc - 1.2, "escape:R-above", 3.5),
        (1.2, 1.5, "escape:R-below", 3.5),
        (-label_w / 2, -asc - 3.5, "escape:mid-above", 5.0),
        (-label_w / 2, 3.5, "escape:mid-below", 5.0),
        (-label_w / 2, -asc - 6.0, "escape:far-above", 3.0),
        (-label_w / 2, 6.0, "escape:far-below", 3.0),
    ):
        tx, ty = mid_x + dx, mid_y + dy + asc
        bb_y = ty - asc
        cap = Rect(tx - 0.7, bb_y - 0.25, label_w + 1.4, label_h + 0.5)
        out.append((tx, ty, cap, tag, pref))
    return out


def _seg_crosses_cap(
    a: tuple[float, float],
    b: tuple[float, float],
    cap: Rect,
    pad: float = 0.15,
) -> bool:
    """线段是否穿入标签胶囊（含描边容差）。"""
    return _segment_hits_rect(a[0], a[1], b[0], b[1], cap.expanded(pad))


def _label_hard_collision(
    cap: Rect,
    boxes: list[Rect],
    texts: list[Rect],
    other_caps: list[Rect],
    other_arrow_segs: list[tuple[tuple[float, float], tuple[float, float]]],
    own_segs: list[tuple[tuple[float, float], tuple[float, float]]] | None = None,
    tip: tuple[float, float] | None = None,
    box_pad: float = 0.35,
    endpoint_boxes: list[Rect] | None = None,
) -> bool:
    """硬碰撞：压非端点盒子（含边框带）/文字/其它标签，或被其它箭头线段穿过。

    端点盒子（箭头 from/to）只走软惩罚：窄缝标签几何上几乎必然贴近端点盒。
    """
    endpoints = endpoint_boxes or []
    for b in boxes:
        if any(abs(b.x - e.x) < 1e-6 and abs(b.y - e.y) < 1e-6
               and abs(b.w - e.w) < 1e-6 and abs(b.h - e.h) < 1e-6 for e in endpoints):
            continue
        if cap.intersection_area(b.expanded(box_pad)) > 0.02:
            return True
    for t in texts:
        if cap.intersection_area(t) > 0.05:
            return True
    for o in other_caps:
        if cap.intersection_area(o.expanded(0.3)) > 0.05:
            return True
    # 其它箭头：穿过或贴边 <0.8mm 都硬拒（避免 tok|q）
    for a, b in other_arrow_segs:
        if _seg_crosses_cap(a, b, cap, pad=0.8):
            return True
    if own_segs:
        for a, b in own_segs:
            # 折线竖直/水平段劈开胶囊文字区 → 硬拒
            inner = Rect(cap.x + 0.5, cap.y + 0.4,
                         max(cap.w - 1.0, 0.2), max(cap.h - 0.8, 0.2))
            if _segment_hits_rect(a[0], a[1], b[0], b[1], inner):
                return True
    if tip is not None:
        if (cap.x - 0.05 <= tip[0] <= cap.right + 0.05
                and cap.y - 0.05 <= tip[1] <= cap.bottom + 0.05):
            return True
    return False


def score_label_candidate(
    cap: Rect,
    tip: tuple[float, float],
    pts: list[tuple[float, float]],
    boxes: list[Rect],
    texts: list[Rect],
    arrow_segs: list[tuple[tuple[float, float], tuple[float, float]]],
    other_caps: list[Rect],
    seg_pref: float = 0.0,
    head_keep: float = 2.8,
    other_arrow_segs: list[tuple[tuple[float, float], tuple[float, float]]] | None = None,
    endpoint_boxes: list[Rect] | None = None,
) -> float:
    """越高越好。硬碰撞大幅扣分；其它箭头线段穿标签为重罚。"""
    s = 100.0 + seg_pref
    endpoints = endpoint_boxes or []

    def _is_endpoint(b: Rect) -> bool:
        return any(
            abs(b.x - e.x) < 1e-6 and abs(b.y - e.y) < 1e-6
            and abs(b.w - e.w) < 1e-6 and abs(b.h - e.h) < 1e-6
            for e in endpoints
        )

    for b in boxes:
        a = cap.intersection_area(b.expanded(0.35))
        if a <= 0:
            continue
        if _is_endpoint(b):
            # 端点盒：轻罚，避免被赶到远处；边框带比深入盒内多罚一点
            inner = Rect(b.x + 0.5, b.y + 0.5, max(b.w - 1.0, 0.1), max(b.h - 1.0, 0.1))
            deep = cap.intersection_area(inner)
            border = max(0.0, a - deep)
            s -= 8 + deep * 1.5 + border * 6
        else:
            s -= 120 + a * 8
    for t in texts:
        a = cap.intersection_area(t)
        if a > 0:
            s -= 90 + a * 5
    for o in other_caps:
        a = cap.intersection_area(o.expanded(0.3))
        if a > 0:
            s -= 70 + a * 3
    if cap.x - 0.05 <= tip[0] <= cap.right + 0.05 and cap.y - 0.05 <= tip[1] <= cap.bottom + 0.05:
        s -= 120
    cx, cy = cap.x + cap.w / 2, cap.y + cap.h / 2
    if math.hypot(cx - tip[0], cy - tip[1]) < head_keep * 0.55:
        s -= 60
    own_segs = list(zip(pts, pts[1:])) if len(pts) >= 2 else []
    foreign = list(other_arrow_segs) if other_arrow_segs is not None else [
        seg for seg in arrow_segs if seg not in own_segs
    ]
    # 其它箭头线段：穿过胶囊或贴太近 → 重罚（修复 tok|q 竖线劈标）
    for a, b in foreign:
        if _seg_crosses_cap(a, b, cap, pad=0.8):
            s -= 200
        elif _point_seg_dist((cx, cy), a, b) < 1.2:
            s -= 50
    segs = own_segs or list(arrow_segs)
    dmin = min(_point_seg_dist((cx, cy), a, b) for a, b in segs) if segs else 99.0
    if dmin < 0.25:
        s -= 40
    elif dmin > 5.0:
        s -= (dmin - 5.0) * 3
    else:
        s += 6
    return s


def pick_best_label(
    pts: list[tuple[float, float]],
    label_w: float,
    label_h: float,
    asc: float,
    boxes: list[Rect],
    texts: list[Rect],
    other_arrow_segs: list[tuple[tuple[float, float], tuple[float, float]]],
    other_caps: list[Rect],
    head_keep: float = 2.8,
    endpoint_boxes: list[Rect] | None = None,
) -> LabelCandidate | None:
    tip = pts[-1]
    own_segs = list(zip(pts, pts[1:]))
    all_segs = own_segs + list(other_arrow_segs)
    cands = label_candidates(pts, label_w, label_h, asc)
    best: LabelCandidate | None = None
    best_soft: LabelCandidate | None = None
    for tx, ty, cap, tag, pref in cands:
        hard = _label_hard_collision(
            cap, boxes, texts, other_caps, other_arrow_segs,
            own_segs=own_segs, tip=tip, endpoint_boxes=endpoint_boxes,
        )
        sc = score_label_candidate(
            cap, tip, pts, boxes, texts, all_segs, other_caps, pref, head_keep,
            other_arrow_segs=other_arrow_segs,
            endpoint_boxes=endpoint_boxes,
        )
        cand = LabelCandidate(x=tx, baseline=ty, cap=cap, tag=tag, score=sc)
        if not hard and (best is None or sc > best.score):
            best = cand
        if best_soft is None or sc > best_soft.score:
            best_soft = cand
    return best if best is not None else best_soft
