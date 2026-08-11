"""渲染后自动体检（视觉评审闭环的客观部分）。

参考短视频工作流 phase9 的自评审思路，把"审稿人雷点"里能被
几何/度量捕获的项做成硬检查；构图审美等主观项留给多模态目检。

检查项：
  E 级（错误，必须修）
    - 文本溢出所在盒子
    - 文本相互重叠
    - 文本超出画布
    - 素材缺失
    - 箭头穿过无关节点（含 legend / 独立 sketch / asset；端点仅豁免法向 stub）
    - 箭头标签压 sketch/accent（arrow-label-over-sketch，交 >0.8mm²；base 模式跳过）
    - 箭头标签深入节点 inner（arrow-label-in-node；显式 label_offset 不豁免）
    - 箭头标签压到 panel 标题带（arrow-label-on-title）
    - base 底稿文字对比度（base-text-contrast：有效背景对比 <3.0 或无 plate 花纹繁忙；
      ghost:false 实体盒用填充色作有效背景）
  W 级（警告，建议修）
    - 字号低于下限（印刷缩放后 <6pt）
    - 节点重叠
    - 素材实际显示尺寸过小（槽位浪费）
    - 画布利用率过低 / 元素过挤（覆盖率按叶元素，剔除 panel/背景 group）
    - 九宫格空洞 / 失衡（region-empty / layout-imbalance）
    - 视觉丰度（R-*）：空盒子 / 无分区底 / 多色无图例（base 模式均停用）
    - 箭头体检：末段不垂直进入（arrow-approach）/ 端点悬空或压入（arrow-gap）
    - 箭头出口落在本盒 sketch 带且法向净空不足（arrow-exit-over-content；base 模式跳过）
    - 绕行穿空场（arrow-route-awkward）
    - 箭头标签盖住尖端（arrow-label-tip）/ 压到其他文字（arrow-label-over-text）
    - 箭头标签中心距折线过远（arrow-label-far，>6mm；含显式 offset）
    - 近距候选全硬拒已折中放置（arrow-label-crowded，由 render soft_issues 注入）
    - base 区域漂移（base-region-drift，skeleton）：骨架矩形与底稿墨迹对拍
    - 文字底板互叠（plate-overlap，交 >30%）
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .render import RenderResult, _is_ghost, visual_rect_for
from .routing import LABEL_DIST_FAR_LINT_MM, cap_path_distance
from .spec import (
    ArrowEl, AssetEl, BadgeEl, BoxEl, FigureSpec, GroupEl, LegendEl,
    MarkerEl, NetworkEl, PanelEl, Rect, ScatterEl, SketchEl, TextEl, TokensEl,
    parse_anchor,
)
from .theme import load_theme

# 箭头体检容差（mm）
_ARROW_GAP_FLOAT = 0.8       # 悬空超过此值 → W
_ARROW_GAP_PENETRATE = 0.5   # 深入视觉边界内部超过此值 → W
# 末段与锚定边法向夹角 >15° → arrow-approach（cos(15°)≈0.9659）
_ARROW_APPROACH_MAX_DEG = 15.0
_ARROW_APPROACH_MIN_COS = math.cos(math.radians(_ARROW_APPROACH_MAX_DEG))
_ARROW_LABEL_SKETCH_MIN = 0.8   # mm²：标签∩sketch/accent → E
_ARROW_LABEL_NODE_MIN = 0.8     # mm²：标签∩节点 inner → E
_ARROW_LABEL_TITLE_MIN = 0.5    # mm²：标签∩panel 标题带 → E
_ENDPOINT_BORDER_MM = 1.0       # 端点盒边框带（与 routing 一致）
_EXIT_SKETCH_CLEARANCE = 2.5    # mm：出口到本盒 sketch 法向净空
_MIN_APPROACH_MM = 3.0          # 与 render/routing 法向 stub 一致

# 构图：叶元素九宫格
_REGION_EMPTY_OCC = 0.05
_REGION_EMPTY_NEIGHBOR = 0.30
_LAYOUT_IMBALANCE = 0.35
_SMALL_CANVAS_W = 120.0         # 小于此宽放宽九宫格阈值
_ROUTE_AWKWARD_DETOUR = 1.3
_ROUTE_AWKWARD_SEG = 15.0       # mm：中段落在低占用格
_ROUTE_AWKWARD_CELL_OCC = 0.08

# base 混合模式
_BASE_CONTRAST_MIN = 3.0          # WCAG 近似对比率下限 → 低于此 E
_BASE_BUSY_VAR = 0.012            # 无 plate 时底稿亮度方差上限（花纹繁忙）
_ARROW_LABEL_BG_FILL = "#FFFFFF"
_ARROW_LABEL_BG_OPACITY = 0.92
_PLATE_OVERLAP_FRAC = 0.30        # 两块文字底板重叠占较小者面积比例 → W
_DRIFT_RING_MM = 6.0              # 骨架矩形外扩环带宽度
_DRIFT_EMPTY_IN = 0.08            # 矩形内非白占比低于此 → 近空
_DRIFT_SPILL_FRAC = 0.40          # 环带墨迹占（内+环）比例；宽松，3px 偏移约 0.07
_DRIFT_SPILL_MIN_INK = 60         # 环带至少这么多非白像素才谈溢出
_INK_WHITE_LUMA = 0.92            # 高于此亮度视作"白/近白"底

MIN_FONT_PT = 5.5   # 旧主题 font-too-small 下限（lint_min_font 未设时）
IDEAL_MIN_FONT_PT = 6.0  # 旧主题 font-small 软下限（lint_min_font 未设时）
ABS_MIN_FONT_PT = 5.0    # 绝对硬底线：任何主题低于此仍报警

# 视觉丰度阈值
_RICHNESS_MIN_ELEMENTS = 8          # ≥ 此数才检查分区底
_RICHNESS_MIN_SEMANTIC_COLORS = 3   # ≥ 此数非 muted 色需 legend
_EMPTY_BOX_TITLE_EXEMPT_LEN = 18    # 长标题容器卡（含公式）豁免空盒检查
_EMPTY_BOX_AREA_EXEMPT = 300.0      # mm²；小标签条 / 单行操作盒不要求塞子内容
_MUTED_VARIANTS = frozenset({"muted", "plain", "baseline", "section"})
_NEUTRAL_HEX = frozenset({
    "#FFFFFF", "#FFF", "#FAFAFA", "#F7F7F7", "#F5F5F5", "#FBFCFE",
    "#EEEEEE", "#E0E0E0", "#CCCCCC", "#BDBDBD", "#B5B5B5", "#999999",
    "#888888", "#8492A6", "#757575", "#666666", "#4D4D4D", "#333333",
    "#263238", "#2A2A2A", "#000000", "#000", "#111111", "#1F2933",
    "#52606D", "#718096", "#A0AEC0", "#CBD5E0", "#EDF2F7", "#F7FAFC",
})


@dataclass
class Issue:
    level: str   # E | W
    code: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.msg}"


def lint(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    issues: list[Issue] = []
    base_mode = bool(getattr(res, "base_mode", False) or spec.base is not None)

    for ref in res.missing_assets:
        issues.append(Issue("E", "asset-missing", f"素材未找到: {ref}（渲染为占位框）"))

    for ref in res.placeholder_assets:
        issues.append(Issue("W", "asset-placeholder",
                            f"占位槽 '{ref}' 待手动插入真实实验图（把文件放到该路径即自动嵌入）"))

    for bid in res.overflow_boxes:
        issues.append(Issue("E", "text-overflow", f"box '{bid}' 内容高度超出盒子，加大盒子或缩短文字"))

    # 渲染期软警告（如 route-avoid-fallback）；最终路径仍走下方 arrow-* 检查
    for level, code, msg in getattr(res, "soft_issues", []) or []:
        issues.append(Issue(level, code, msg))

    issues += _check_text_overlap(res)
    issues += _check_canvas_bounds(spec, res)
    issues += _check_font_sizes(spec, res)
    issues += _check_node_overlap(spec, res)
    issues += _check_arrow_crossings(spec, res)
    issues += _check_arrow_geometry(spec, res)
    # base 幽灵盒无 sketch；停用出口/标签压 sketch 类检查，避免误报
    if not base_mode:
        issues += _check_arrow_exit_over_content(spec, res)
        issues += _check_arrow_label_over_sketch(res)
    issues += _check_arrow_label_occlusion(spec, res)
    issues += _check_arrow_label_in_node(spec, res)
    issues += _check_arrow_label_on_title(res)
    issues += _check_arrow_label_far(res)
    issues += _check_arrow_route_awkward(spec, res)
    issues += _check_asset_scale(res)
    issues += _check_density(spec, res)
    issues += _check_region_balance(spec, res)
    issues += _check_alignment(spec, res)
    issues += _check_visual_richness(spec, res, base_mode=base_mode)
    issues += _check_figurative_overload(spec, res)
    if base_mode:
        issues += _check_plate_overlap(res)
        issues += _check_base_text_contrast(spec, res)
        issues += _check_base_region_drift(spec, res)
    return issues


def _check_text_overlap(res: RenderResult) -> list[Issue]:
    issues = []
    spans = [(s, s.bbox()) for s in res.text_spans if s.text.strip()]
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            s1, b1 = spans[i]
            s2, b2 = spans[j]
            inter = b1.intersection_area(b2)
            if inter > 0.15 * min(b1.w * b1.h, b2.w * b2.h):
                issues.append(Issue(
                    "E", "text-overlap",
                    f"文本相互重叠: “{s1.text[:14]}” 与 “{s2.text[:14]}”"))
    return issues


def _check_canvas_bounds(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    issues = []
    for s in res.text_spans:
        if not s.text.strip():
            continue
        b = s.bbox()
        if b.x < -0.1 or b.right > spec.width + 0.1 or b.y < -0.1 or b.bottom > spec.height + 0.1:
            issues.append(Issue("E", "out-of-canvas", f"文本超出画布: “{s.text[:18]}”"))
    for nid, r in res.node_rects.items():
        if r.x < -0.01 or r.right > spec.width + 0.01 or r.y < -0.01 or r.bottom > spec.height + 0.01:
            issues.append(Issue("E", "out-of-canvas", f"节点 '{nid}' 超出画布"))
    return issues


def _check_font_sizes(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """字号体检：主题 lint_min_font 控制 font-small；ABS_MIN_FONT_PT 为硬底线。"""
    from .theme import load_theme
    th = load_theme(spec.theme_cfg)
    # None → 旧行为 soft=6.0；印刷主题设 5.5 以放行 caption/arrow 5.8
    soft_floor = th.lint_min_font if th.lint_min_font is not None else IDEAL_MIN_FONT_PT
    # 旧主题保留 5.5 的 font-too-small；印刷主题仅用绝对硬底线 5.0
    hard_floor = ABS_MIN_FONT_PT if th.lint_min_font is not None else MIN_FONT_PT

    issues = []
    seen: set[float] = set()
    for s in res.text_spans:
        if not s.text.strip() or s.pt in seen or getattr(s, "diagnostic", False):
            continue
        seen.add(s.pt)
        if s.pt < ABS_MIN_FONT_PT:
            # 绝对硬底线：任何主题（含把 lint_min_font 调得很低）都报警
            issues.append(Issue("W", "font-too-small",
                                f"{s.pt:.1f}pt 低于绝对下限 {ABS_MIN_FONT_PT}pt"
                                f"（如 “{s.text[:12]}”）"))
        elif s.pt < hard_floor:
            issues.append(Issue("W", "font-too-small",
                                f"{s.pt:.1f}pt 低于下限 {hard_floor}pt（如 “{s.text[:12]}”）"))
        elif s.pt < soft_floor:
            issues.append(Issue("W", "font-small",
                                f"{s.pt:.1f}pt 接近下限，印刷缩印后可能难以辨认"))
    return issues


def _contains(outer: Rect, inner: Rect, tol: float = 0.15) -> bool:
    return (outer.x - tol <= inner.x and outer.y - tol <= inner.y
            and outer.right + tol >= inner.right and outer.bottom + tol >= inner.bottom)


def _check_node_overlap(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    issues = []
    nodes = [(el.id, el.rect) for el in spec.elements if isinstance(el, (BoxEl, AssetEl))]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            id1, r1 = nodes[i]
            id2, r2 = nodes[j]
            # 完全包含 = 有意的容器嵌套（box 作面板背景、子卡内放图标），不算重叠
            if _contains(r1, r2) or _contains(r2, r1):
                continue
            inter = r1.intersection_area(r2)
            if inter > 0.06 * min(r1.w * r1.h, r2.w * r2.h):
                issues.append(Issue("W", "node-overlap", f"节点 '{id1}' 与 '{id2}' 重叠"))
    return issues


def _through_node_obstacles(spec: FigureSpec, res: RenderResult
                            ) -> list[tuple[str, Rect]]:
    """arrow-through-node 障碍集：box/asset/独立 sketch/legend 显示框。"""
    out: list[tuple[str, Rect]] = []
    for el in spec.elements:
        if isinstance(el, BoxEl):
            r = res.node_visual_rects.get(el.id) or el.rect
            out.append((el.id, r))
        elif isinstance(el, AssetEl):
            shown = res.asset_boxes.get(el.src)
            out.append((el.id, shown if shown is not None else el.rect))
        elif isinstance(el, SketchEl):
            out.append((el.id, el.rect))
        elif isinstance(el, LegendEl):
            r = res.node_rects.get(el.id)
            if r is not None:
                out.append((el.id, r))
    return out


def _is_normal_stub_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    tip: tuple[float, float],
    side: str,
    max_len: float = _MIN_APPROACH_MM,
) -> bool:
    """线段是否为端点处沿锚定边法向的 stub（长度 ≤ approach）。"""
    if side not in ("left", "right", "top", "bottom"):
        return False
    # 一端必须是 tip
    if (abs(a[0] - tip[0]) < 1e-6 and abs(a[1] - tip[1]) < 1e-6):
        other = b
    elif (abs(b[0] - tip[0]) < 1e-6 and abs(b[1] - tip[1]) < 1e-6):
        other = a
    else:
        return False
    dx, dy = other[0] - tip[0], other[1] - tip[1]
    L = math.hypot(dx, dy)
    if L < 1e-9 or L > max_len + 0.35:
        return False
    nx, ny = _side_normal(side)
    # 外法向（离开盒子）
    return (dx * nx + dy * ny) / L >= _ARROW_APPROACH_MIN_COS - 1e-9


def _check_arrow_crossings(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """箭头线段穿过无关节点；端点盒仅豁免出口法向 stub，回穿 inner 仍报。"""
    issues = []
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    side_by_id = {aid: (s1, s2) for aid, s1, s2 in getattr(res, "arrow_ends", [])}
    obstacles = _through_node_obstacles(spec, res)

    for aid, pts in res.arrow_segments:
        el = arrows.get(aid)
        if not pts or len(pts) < 2:
            continue
        start, end = pts[0], pts[-1]
        s1, s2 = side_by_id.get(aid, ("free", "free"))
        endpoint_ids: set[str] = set()
        if el:
            for ep in (el.from_, el.to):
                if isinstance(ep, str):
                    endpoint_ids.add(parse_anchor(ep)[0])

        for nid, r in obstacles:
            if nid.startswith("_group") or "@" in nid:
                continue
            # 容器嵌套：起/终点落在节点内部 → 穿出容器边界有意为之
            if r.contains_point(*start) or r.contains_point(*end):
                if nid not in endpoint_ids:
                    continue
            hit_inner = False
            for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
                core = r.expanded(-0.4)
                if not _segment_hits_rect(x1, y1, x2, y2, core):
                    continue
                if nid in endpoint_ids:
                    # 仅豁免端点法向 stub；回穿 inner 要报
                    if i == 0 and _is_normal_stub_segment(
                            (x1, y1), (x2, y2), start, s1):
                        continue
                    if i == len(pts) - 2 and _is_normal_stub_segment(
                            (x1, y1), (x2, y2), end, s2):
                        continue
                    # 贴边框带滑行（未深入 inner）放过
                    inner = Rect(
                        r.x + _ENDPOINT_BORDER_MM, r.y + _ENDPOINT_BORDER_MM,
                        max(r.w - 2 * _ENDPOINT_BORDER_MM, 0.1),
                        max(r.h - 2 * _ENDPOINT_BORDER_MM, 0.1),
                    )
                    if not _segment_hits_rect(x1, y1, x2, y2, inner):
                        continue
                hit_inner = True
                break
            if hit_inner:
                issues.append(Issue("E", "arrow-through-node",
                                    f"箭头 '{aid}' 穿过节点 '{nid}'"))
    return issues


def _segment_hits_rect(x1: float, y1: float, x2: float, y2: float, r: Rect) -> bool:
    if r.w <= 0 or r.h <= 0:
        return False
    # Liang-Barsky
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - r.x), (dx, r.right - x1), (-dy, y1 - r.y), (dy, r.bottom - y1)):
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


def _visual_rect_of(spec: FigureSpec, res: RenderResult, nid: str) -> Rect | None:
    if nid in res.node_visual_rects:
        return res.node_visual_rects[nid]
    el = spec.find(nid)
    if el is not None and hasattr(el, "rect") and el.rect is not None:
        return visual_rect_for(el)
    return res.node_rects.get(nid)


def _signed_gap_to_side(x: float, y: float, r: Rect, side: str) -> float:
    """端点相对视觉边的有符号距离：>0 在外侧（悬空），<0 在内侧（压入）。"""
    if side == "left":
        return r.x - x
    if side == "right":
        return x - r.right
    if side == "top":
        return r.y - y
    if side == "bottom":
        return y - r.bottom
    # center / free：到矩形的外距（在内为负）
    if r.x <= x <= r.right and r.y <= y <= r.bottom:
        return -min(x - r.x, r.right - x, y - r.y, r.bottom - y)
    dx = max(r.x - x, 0.0, x - r.right)
    dy = max(r.y - y, 0.0, y - r.bottom)
    return math.hypot(dx, dy)


def _side_normal(side: str) -> tuple[float, float]:
    """锚定边外法向（与 render._side_outward 一致）。"""
    return {
        "left": (-1.0, 0.0), "right": (1.0, 0.0),
        "top": (0.0, -1.0), "bottom": (0.0, 1.0),
    }.get(side, (0.0, 0.0))


def _end_approach_ok(pts: list[tuple[float, float]], side: str) -> bool:
    """末段方向与锚定边法向夹角是否 ≤15°（斜线末段必报）。"""
    if side not in ("left", "right", "top", "bottom") or len(pts) < 2:
        return True
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return False
    ux, uy = dx / L, dy / L
    nx, ny = _side_normal(side)
    # |dir · normal| = cos(夹角)；夹角 >15° → 不通过
    return abs(ux * nx + uy * ny) >= _ARROW_APPROACH_MIN_COS - 1e-9


def _check_arrow_geometry(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """箭头垂直进入 / 端点悬空或压入（W 级；含 straight 与显式 via）。"""
    issues: list[Issue] = []
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    side_by_id = {aid: (s1, s2) for aid, s1, s2 in getattr(res, "arrow_ends", [])}

    for aid, pts in res.arrow_segments:
        el = arrows.get(aid)
        if el is None or len(pts) < 2:
            continue
        s1, s2 = side_by_id.get(aid, ("free", "free"))

        # 贴齐接触（stack 外缘贴邻盒）时杆可能退化，不做 approach 检查
        flush = False
        if s1 in ("left", "right") and s2 in ("left", "right"):
            flush = abs(pts[0][0] - pts[-1][0]) < 0.5
        elif s1 in ("top", "bottom") and s2 in ("top", "bottom"):
            flush = abs(pts[0][1] - pts[-1][1]) < 0.5

        # arrow-approach：末段（及双向起点）相对锚定边；straight 也检测
        if not flush and not _end_approach_ok(pts, s2):
            issues.append(Issue(
                "W", "arrow-approach",
                f"箭头 '{aid}' 末段未垂直进入锚定边 {s2}（route={el.route}"
                f"{', via' if el.via else ''}）",
            ))
        if not flush and el.bidir and s1 in ("left", "right", "top", "bottom"):
            rev = list(reversed(pts))
            if not _end_approach_ok(rev, s1):
                issues.append(Issue(
                    "W", "arrow-approach",
                    f"箭头 '{aid}' 首段未垂直离开锚定边 {s1}",
                ))

        # arrow-gap：检查锚定到节点的端点（与渲染共用 res.arrow_segments 最终折线）
        for ep, side, tip in ((el.to, s2, pts[-1]), (el.from_, s1, pts[0])):
            if not isinstance(ep, str) or side in ("free", "center"):
                continue
            nid = parse_anchor(ep)[0]
            vr = _visual_rect_of(spec, res, nid)
            if vr is None:
                continue
            gap = _signed_gap_to_side(tip[0], tip[1], vr, side)
            if gap > _ARROW_GAP_FLOAT:
                issues.append(Issue(
                    "W", "arrow-gap",
                    f"箭头 '{aid}' 端点悬空 {gap:.2f}mm（>{_ARROW_GAP_FLOAT}mm），"
                    f"未触及 '{nid}' 的 {side} 视觉边",
                ))
            elif gap < -_ARROW_GAP_PENETRATE:
                issues.append(Issue(
                    "W", "arrow-gap",
                    f"箭头 '{aid}' 端点压入 '{nid}' 视觉边界 "
                    f"{-gap:.2f}mm（>{_ARROW_GAP_PENETRATE}mm）",
                ))

    return issues


def _check_arrow_label_occlusion(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """箭头标签胶囊盖住尖端 / 压到其他元素文字（W 级）。

    显式 label_offset 与 auto 落标一视同仁（渲染可按用户偏移，lint 不豁免）。
    """
    del spec
    issues: list[Issue] = []
    tip_by_id = {aid: pts[-1] for aid, pts in res.arrow_segments if len(pts) >= 2}
    label_texts = {lbl for _, _, lbl in getattr(res, "arrow_label_boxes", [])}

    for aid, cap, label in getattr(res, "arrow_label_boxes", []):
        tip = tip_by_id.get(aid)
        if tip is not None:
            if (cap.x - 0.05 <= tip[0] <= cap.right + 0.05
                    and cap.y - 0.05 <= tip[1] <= cap.bottom + 0.05):
                issues.append(Issue(
                    "W", "arrow-label-tip",
                    f"箭头 '{aid}' 标签 “{label[:16]}” 胶囊盖住尖端，"
                    f"会造成悬空/断头错觉",
                ))
        # 与其他文字重叠（不含自身标签）
        for s in res.text_spans:
            if not s.text.strip() or s.text == label:
                continue
            if s.text in label_texts and s.text == label:
                continue
            bb = s.bbox()
            inter = cap.intersection_area(bb)
            # 相对阈值 0.12，或绝对相交 >1.2mm² —— 取更敏感者
            min_area = min(cap.w * cap.h, bb.w * bb.h)
            if inter > 0.12 * min_area or inter > 1.2:
                issues.append(Issue(
                    "W", "arrow-label-over-text",
                    f"箭头 '{aid}' 标签 “{label[:14]}” 压到文字 “{s.text[:14]}”",
                ))
                break
    return issues


def _check_arrow_label_over_sketch(res: RenderResult) -> list[Issue]:
    """箭头标签胶囊压到 sketch / accent → E（交 >0.8mm²）。

    同一标签可对多个 owner/kind 各报一条（便于对照审计案例）。
    """
    issues: list[Issue] = []
    sketches = getattr(res, "sketch_rects", []) or []
    for aid, cap, label in getattr(res, "arrow_label_boxes", []):
        for owner, kind, sk in sketches:
            inter = cap.intersection_area(sk)
            if inter > _ARROW_LABEL_SKETCH_MIN:
                issues.append(Issue(
                    "E", "arrow-label-over-sketch",
                    f"箭头 '{aid}' 标签 “{label[:14]}” 压到 '{owner}' 的 {kind}"
                    f"（交 {inter:.2f}mm²）",
                ))
    return issues


def _check_arrow_label_in_node(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """箭头标签深入节点 inner（边框带≈1mm 外）→ E；显式 offset 不豁免。"""
    issues: list[Issue] = []
    nodes: list[tuple[str, Rect]] = []
    for el in spec.elements:
        if isinstance(el, (BoxEl, AssetEl, SketchEl)):
            r = res.node_visual_rects.get(el.id) or res.node_rects.get(el.id) or el.rect
            nodes.append((el.id, r))
        elif isinstance(el, LegendEl):
            r = res.node_rects.get(el.id)
            if r is not None:
                nodes.append((el.id, r))
    for aid, cap, label in getattr(res, "arrow_label_boxes", []):
        for nid, r in nodes:
            inner = Rect(
                r.x + _ENDPOINT_BORDER_MM, r.y + _ENDPOINT_BORDER_MM,
                max(r.w - 2 * _ENDPOINT_BORDER_MM, 0.1),
                max(r.h - 2 * _ENDPOINT_BORDER_MM, 0.1),
            )
            inter = cap.intersection_area(inner)
            if inter > _ARROW_LABEL_NODE_MIN:
                issues.append(Issue(
                    "E", "arrow-label-in-node",
                    f"箭头 '{aid}' 标签 “{label[:14]}” 深入节点 '{nid}' 内容区"
                    f"（交 {inter:.2f}mm²）；边框带可叠、勿压 inner/sketch",
                ))
                break
    return issues


def _check_arrow_label_on_title(res: RenderResult) -> list[Issue]:
    """箭头标签压到 panel 标题带（标题行+分隔线/色条）→ E。"""
    issues: list[Issue] = []
    bands = getattr(res, "panel_title_bands", []) or []
    if not bands:
        return issues
    for aid, cap, label in getattr(res, "arrow_label_boxes", []):
        for pid, band in bands:
            inter = cap.intersection_area(band)
            if inter > _ARROW_LABEL_TITLE_MIN:
                issues.append(Issue(
                    "E", "arrow-label-on-title",
                    f"箭头 '{aid}' 标签 “{label[:14]}” 压到 panel '{pid}' 标题带"
                    f"（交 {inter:.2f}mm²）；拉开间距或改 label_offset",
                ))
                break
    return issues


def _check_arrow_label_far(res: RenderResult) -> list[Issue]:
    """标签胶囊中心到所属折线距离 >6mm → W（含显式 label_offset）。"""
    issues: list[Issue] = []
    segs = {aid: pts for aid, pts in getattr(res, "arrow_segments", []) or []}
    for aid, cap, label in getattr(res, "arrow_label_boxes", []):
        pts = segs.get(aid)
        if not pts or len(pts) < 2:
            continue
        d = cap_path_distance(cap, pts)
        if d > LABEL_DIST_FAR_LINT_MM:
            issues.append(Issue(
                "W", "arrow-label-far",
                f"箭头 '{aid}' 标签 “{label[:14]}” 距折线 {d:.1f}mm"
                f"（>{LABEL_DIST_FAR_LINT_MM:g}mm）；像悬浮孤儿，请贴近路径或删标签",
            ))
    return issues


def _normal_clearance_to_rect(
    x: float, y: float, side: str, r: Rect,
) -> float | None:
    """端点相对矩形在锚定边法向上的外侧净空；切向不在带内则 None。"""
    if side == "right":
        if not (r.y - 1e-6 <= y <= r.bottom + 1e-6):
            return None
        return x - r.right  # >0 在 sketch 右侧外侧
    if side == "left":
        if not (r.y - 1e-6 <= y <= r.bottom + 1e-6):
            return None
        return r.x - x
    if side == "bottom":
        if not (r.x - 1e-6 <= x <= r.right + 1e-6):
            return None
        return y - r.bottom
    if side == "top":
        if not (r.x - 1e-6 <= x <= r.right + 1e-6):
            return None
        return r.y - y
    return None


def _check_arrow_exit_over_content(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """出口贴边但切向落在本盒 sketch 带且法向净空 <2.5mm → W。"""
    issues: list[Issue] = []
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    side_by_id = {aid: (s1, s2) for aid, s1, s2 in getattr(res, "arrow_ends", [])}
    sketches = getattr(res, "sketch_rects", []) or []
    # 只看真正的 sketch（不含 accent 色条）
    by_owner: dict[str, list[tuple[str, Rect]]] = {}
    for owner, kind, rect in sketches:
        if kind.startswith("accent"):
            continue
        by_owner.setdefault(owner, []).append((kind, rect))

    for aid, pts in res.arrow_segments:
        el = arrows.get(aid)
        if el is None or len(pts) < 2:
            continue
        s1, s2 = side_by_id.get(aid, ("free", "free"))
        for ep, side, tip in ((el.from_, s1, pts[0]), (el.to, s2, pts[-1])):
            if not isinstance(ep, str) or side not in ("left", "right", "top", "bottom"):
                continue
            nid = parse_anchor(ep)[0]
            for kind, sk in by_owner.get(nid, []):
                clr = _normal_clearance_to_rect(tip[0], tip[1], side, sk)
                if clr is None:
                    continue
                # 出口在 sketch 外侧但净空不足，或端点落在 sketch 投影带内
                if clr < _EXIT_SKETCH_CLEARANCE:
                    issues.append(Issue(
                        "W", "arrow-exit-over-content",
                        f"箭头 '{aid}' 在 '{nid}' 的 {side} 出口落在 {kind} 带内"
                        f"（法向净空 {clr:.1f}mm < {_EXIT_SKETCH_CLEARANCE}mm）；"
                        f"考虑改锚点 @t 或换边",
                    ))
                    break
    return issues


# 具象素材过载（ai_era §2.2：锚点 ≤3，面积约 15–30%）
_FIGURATIVE_MAX_ASSETS = 3
_FIGURATIVE_MAX_AREA_RATIO = 0.35


def _check_figurative_overload(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """R-figurative-overload：asset 数 >3 或总面积占画布 >35%。"""
    assets = [e for e in spec.elements if isinstance(e, AssetEl)]
    if not assets:
        return []
    issues: list[Issue] = []
    n = len(assets)
    area = 0.0
    for a in assets:
        shown = res.asset_boxes.get(a.src) or a.rect
        if shown is not None:
            area += shown.w * shown.h
    canvas_area = max(spec.width * spec.height, 1e-6)
    ratio = area / canvas_area
    if n > _FIGURATIVE_MAX_ASSETS or ratio > _FIGURATIVE_MAX_AREA_RATIO:
        issues.append(Issue(
            "W", "R-figurative-overload",
            f"具象素材 {n} 个、约占画布 {ratio:.0%}；建议 ≤{_FIGURATIVE_MAX_ASSETS} 个且面积 "
            f"≤{_FIGURATIVE_MAX_AREA_RATIO:.0%}，放在输入/输出侧作锚点",
        ))
    return issues


def _check_asset_scale(res: RenderResult) -> list[Issue]:
    issues = []
    for ref, shown in res.asset_boxes.items():
        if "@" in ref:  # box 内嵌图标不检查
            continue
        if shown.w < 12 and shown.h < 12:
            issues.append(Issue("W", "asset-tiny",
                                f"素材 '{ref}' 显示尺寸仅 {shown.w:.0f}×{shown.h:.0f}mm，考虑加大槽位或裁剪素材"))
    return issues


# 对齐/间距体检只抓"近失"（明显想对齐/等距却差一点），不碰有意的错落——后者靠目检。
_SNAP_TOL = 0.5      # mm：错位/间距差 ≤ 此值算已对齐/已等距，不报（亚像素级）
_NEAR_ALIGN = 2.0    # mm：错位在 (SNAP, NEAR] 内 = "几乎对齐却没对上" → 提示；> 视为有意错落，不报
_NEAR_GAP = 2.5      # mm：相邻间距极差在 (SNAP, NEAR] 内 = "几乎等距却差一点" → 提示；> 视为有意
_BAND_OVERLAP = 0.6  # 交叉轴重叠 ≥ 此比例才算同一行/列（锚定首元素，防阶梯误聚类）


def _overlap_frac(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = min(a1, b1) - max(a0, b0)
    denom = min(a1 - a0, b1 - b0)
    return inter / denom if denom > 1e-9 else 0.0


def _bands(nodes: list[tuple[str, Rect]], axis: str) -> list[list[tuple[str, Rect]]]:
    """把节点聚成行(axis='y')或列(axis='x')：按主轴中心排序，锚定每带首元素，
    交叉轴重叠 ≥ _BAND_OVERLAP 才并入——锚定式聚类避免"阶梯状"被误判成一行。"""
    if axis == "y":
        center, lo, hi = (lambda r: r.cy), (lambda r: r.y), (lambda r: r.bottom)
    else:
        center, lo, hi = (lambda r: r.cx), (lambda r: r.x), (lambda r: r.right)
    ordered = sorted(nodes, key=lambda n: center(n[1]))
    bands: list[list[tuple[str, Rect]]] = []
    for n in ordered:
        placed = False
        for band in bands:
            ar = band[0][1]  # 锚 = 该带首元素
            if _overlap_frac(lo(ar), hi(ar), lo(n[1]), hi(n[1])) >= _BAND_OVERLAP:
                band.append(n)
                placed = True
                break
        if not placed:
            bands.append([n])
    return bands


def _near_align_issue(ids: str, edges: list[list[float]], code: str, where: str, hint: str) -> Issue | None:
    """edges = [顶/左, 底/右, 中线] 三组坐标。取三种对齐里最接近的那种，
    若其错位落在"近失"区间 (SNAP, NEAR] → 提示 snap；已对齐或明显有意错落都不报。"""
    best = min(max(e) - min(e) for e in edges)
    if _SNAP_TOL < best <= _NEAR_ALIGN:
        return Issue("W", code, f"同{where}节点几乎对齐却差 {best:.1f}mm: {ids}——{hint}")
    return None


def _near_gap_issue(members: str, pitches: list[float], where: str) -> Issue | None:
    """pitches = 相邻节点**中心距**（不是边到边）——这样宽度不一但中心等距的布局不会被误报。"""
    if len(pitches) < 2 or min(pitches) <= 0:
        return None
    spread = max(pitches) - min(pitches)
    if _SNAP_TOL < spread <= _NEAR_GAP:
        return Issue("W", "uneven-gap",
                     f"同{where}节点中心间距几乎相等却差 {spread:.1f}mm: {members}——微调成等距更整齐")
    return None


def _check_alignment(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """同一行/列的 box/asset 的**近失**对齐/间距检查（"图片分布不好"里可机检的那类小瑕疵）。
    只抓"明显想对齐/等距却差 0.5–2mm"的情形；有意的错落（差距大）与已对齐（差距<0.5mm）都不报。"""
    issues: list[Issue] = []
    nodes = [(el.id, el.rect) for el in spec.elements if isinstance(el, (BoxEl, AssetEl))]
    if len(nodes) < 3:
        return issues

    for row in _bands(nodes, "y"):
        if len(row) < 3:
            continue
        row = sorted(row, key=lambda n: n[1].x)
        rects = [r for _, r in row]
        ids = ", ".join(i for i, _ in row)
        ai = _near_align_issue(ids, [[r.y for r in rects], [r.bottom for r in rects],
                                     [r.cy for r in rects]], "row-misaligned", "排", "统一 y 或 h 即可对齐")
        if ai:
            issues.append(ai)
        gi = _near_gap_issue(ids, [rects[i + 1].cx - rects[i].cx for i in range(len(rects) - 1)], "排")
        if gi:
            issues.append(gi)

    for col in _bands(nodes, "x"):
        if len(col) < 3:
            continue
        col = sorted(col, key=lambda n: n[1].y)
        rects = [r for _, r in col]
        ids = ", ".join(i for i, _ in col)
        ai = _near_align_issue(ids, [[r.x for r in rects], [r.right for r in rects],
                                     [r.cx for r in rects]], "col-misaligned", "列", "统一 x 或 w 即可对齐")
        if ai:
            issues.append(ai)
        gi = _near_gap_issue(ids, [rects[i + 1].cy - rects[i].cy for i in range(len(rects) - 1)], "列")
        if gi:
            issues.append(gi)

    return issues


def _leaf_element_rects(spec: FigureSpec, res: RenderResult) -> list[tuple[str, Rect]]:
    """叶元素 bbox：box/asset/legend/独立 sketch/tokens/network/scatter；
    排除 panel 与纯背景 group。"""
    leaves: list[tuple[str, Rect]] = []
    for el in spec.elements:
        if isinstance(el, PanelEl):
            continue
        if isinstance(el, GroupEl):
            continue  # group 作分区框/底，不算叶内容
        if isinstance(el, (BoxEl, TokensEl, NetworkEl, ScatterEl, SketchEl)):
            r = res.node_rects.get(el.id) or el.rect
            leaves.append((el.id, r))
        elif isinstance(el, AssetEl):
            shown = res.asset_boxes.get(el.src)
            leaves.append((el.id, shown if shown is not None else el.rect))
        elif isinstance(el, LegendEl):
            r = res.node_rects.get(el.id)
            if r is not None:
                leaves.append((el.id, r))
    return leaves


def _check_density(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """两个独立信号：
    - 叶元素包围盒对画布的覆盖率过低 → 四周留白太多（真正的"空"）；
    - 节点面积占画布过高 → 元素挤成一团。
    覆盖率剔除 PanelEl 与背景 group，避免分区底把稀疏图撑满。
    """
    issues = []
    canvas_area = spec.width * spec.height
    leaves = _leaf_element_rects(spec, res)

    xs0, ys0, xs1, ys1 = [], [], [], []
    node_area = 0.0
    for nid, r in leaves:
        node_area += r.w * r.h
        xs0.append(r.x); ys0.append(r.y); xs1.append(r.right); ys1.append(r.bottom)
    # 独立 text 仍计入包围盒（标注也是内容）
    for s in res.text_spans:
        if s.text.strip() and not getattr(s, "diagnostic", False):
            b = s.bbox()
            xs0.append(b.x); ys0.append(b.y); xs1.append(b.right); ys1.append(b.bottom)

    if not xs0:
        return issues

    bbox_w = max(xs1) - min(xs0)
    bbox_h = max(ys1) - min(ys0)
    coverage = (bbox_w * bbox_h) / canvas_area

    if coverage < 0.45:
        issues.append(Issue("W", "canvas-sparse",
                            f"叶内容仅覆盖画布 {coverage:.0%}，四周留白过多，考虑缩小画布尺寸"))

    if node_area / canvas_area > 0.82:
        issues.append(Issue("W", "canvas-crowded",
                            f"节点面积占画布 {node_area / canvas_area:.0%}，画面偏挤，考虑加大画布"))
    return issues


def _grid_occupancy(
    leaves: list[tuple[str, Rect]],
    width: float,
    height: float,
    n: int = 3,
) -> list[list[tuple[float, list[str]]]]:
    """3×3 九宫格叶占用率（交面积/格面积）与落入的 id 列表。"""
    cw, ch = width / n, height / n
    grid: list[list[tuple[float, list[str]]]] = []
    for row in range(n):
        row_cells: list[tuple[float, list[str]]] = []
        for col in range(n):
            cell = Rect(col * cw, row * ch, cw, ch)
            area = cell.w * cell.h
            ids: list[str] = []
            occ = 0.0
            for nid, r in leaves:
                inter = cell.intersection_area(r)
                if inter > 0:
                    ids.append(nid)
                    occ += inter
            row_cells.append((occ / area if area > 0 else 0.0, ids))
        grid.append(row_cells)
    return grid


def _check_region_balance(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """九宫格空洞 / 失衡（叶元素，排除 panel 与背景 group）。"""
    issues: list[Issue] = []
    leaves = _leaf_element_rects(spec, res)
    if len(leaves) < 3:
        return issues
    # 小画布放宽，避免单栏/小示意图误报
    small = spec.width < _SMALL_CANVAS_W
    empty_th = 0.02 if small else _REGION_EMPTY_OCC
    neighbor_th = 0.40 if small else _REGION_EMPTY_NEIGHBOR
    imbalance_th = 0.50 if small else _LAYOUT_IMBALANCE

    grid = _grid_occupancy(leaves, spec.width, spec.height)
    occs = [grid[r][c][0] for r in range(3) for c in range(3)]
    spread = max(occs) - min(occs)
    if spread > imbalance_th:
        issues.append(Issue(
            "W", "layout-imbalance",
            f"叶元素九宫格占用极差 {spread:.2f}（>{imbalance_th:.2f}）；"
            f"考虑把内容分散或裁掉空带",
        ))

    for r in range(3):
        for c in range(3):
            occ, _ids = grid[r][c]
            if occ >= empty_th:
                continue
            # 四邻（含对角）有高占用才报空洞，避免整片留白误报
            neighbors = []
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (-1, 1), (1, -1), (1, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 3 and 0 <= cc < 3:
                    neighbors.append(grid[rr][cc][0])
            if neighbors and max(neighbors) > neighbor_th:
                issues.append(Issue(
                    "W", "region-empty",
                    f"九宫格 r{r}c{c} 叶占用 {occ:.3f}（<{empty_th}）且邻格较满；"
                    f"考虑填内容或收画布",
                ))
    return issues


def _path_length(pts: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        for i in range(len(pts) - 1)
    )


def _corridor_leaf_occ(
    x1: float, y1: float, x2: float, y2: float,
    leaves: list[tuple[str, Rect]],
    half_w: float = 5.0,
) -> float:
    """线段两侧 half_w 走廊内的叶占用率。"""
    if abs(y2 - y1) <= abs(x2 - x1):
        # 近水平
        band = Rect(min(x1, x2), min(y1, y2) - half_w,
                    max(abs(x2 - x1), 0.1), abs(y2 - y1) + 2 * half_w)
    else:
        band = Rect(min(x1, x2) - half_w, min(y1, y2),
                    abs(x2 - x1) + 2 * half_w, max(abs(y2 - y1), 0.1))
    area = max(band.w * band.h, 1e-6)
    return sum(band.intersection_area(r) for _, r in leaves) / area


def _check_arrow_route_awkward(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """route:avoid 绕行比高且长段穿过近空走廊 → W。"""
    issues: list[Issue] = []
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    leaves = _leaf_element_rects(spec, res)
    if not leaves:
        return issues
    # 九宫格占用：辅助判断（长段中点落在低占用格）
    grid = _grid_occupancy(leaves, spec.width, spec.height)
    cw, ch = spec.width / 3, spec.height / 3

    for aid, pts in res.arrow_segments:
        el = arrows.get(aid)
        if el is None or el.route != "avoid" or len(pts) < 2:
            continue
        path = _path_length(pts)
        chord = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if chord < 1e-6:
            continue
        detour = path / chord
        if detour <= _ROUTE_AWKWARD_DETOUR:
            continue
        # 中段：非首末 stub；仅两段时两段都查
        segs = list(zip(pts, pts[1:]))
        mid = segs[1:-1] if len(segs) >= 3 else segs
        awkward = False
        for (x1, y1), (x2, y2) in mid:
            seg_len = math.hypot(x2 - x1, y2 - y1)
            if seg_len < _ROUTE_AWKWARD_SEG:
                continue
            # 走廊叶占用 <8%，或中点落在九宫格低占用格
            if _corridor_leaf_occ(x1, y1, x2, y2, leaves) < _ROUTE_AWKWARD_CELL_OCC:
                awkward = True
                break
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            col = min(2, max(0, int(mx / cw))) if cw > 0 else 0
            row = min(2, max(0, int(my / ch))) if ch > 0 else 0
            if grid[row][col][0] < _ROUTE_AWKWARD_CELL_OCC:
                awkward = True
                break
        if awkward:
            issues.append(Issue(
                "W", "arrow-route-awkward",
                f"箭头 '{aid}' route=avoid 绕行比 {detour:.2f}（>{_ROUTE_AWKWARD_DETOUR}）"
                f"且长段穿过近空格；考虑换锚点边或直连",
            ))
    return issues


def _strip_markup(s: str) -> str:
    """去掉 _{...} / ^{...} 标记后估标题可见长度。"""
    return re.sub(r"[_^]\{([^{}]*)\}", r"\1", s or "")


def _point_in_rect(x: float, y: float, r: Rect, tol: float = 0.3) -> bool:
    return (r.x - tol <= x <= r.right + tol) and (r.y - tol <= y <= r.bottom + tol)


def _box_has_inner_content(spec: FigureSpec, box: BoxEl) -> bool:
    """box 内是否落有子元素（容器卡豁免空盒检查）。"""
    for el in spec.elements:
        if el is box:
            continue
        if isinstance(el, (BoxEl, AssetEl, TokensEl, NetworkEl, ScatterEl, SketchEl)):
            if el.rect is not None and _contains(box.rect, el.rect):
                return True
        elif isinstance(el, (MarkerEl, BadgeEl, TextEl)):
            if _point_in_rect(el.at[0], el.at[1], box.rect):
                return True
    return False


def _is_filled_section(el: GroupEl) -> bool:
    """group 是否提供分区底色（显式 fill，或非 none）。"""
    if el.fill is None:
        return False
    return str(el.fill).strip().lower() not in ("", "none", "transparent")


def _norm_hex(c: str | None) -> str | None:
    if not c or not isinstance(c, str):
        return None
    s = c.strip()
    if not s.startswith("#"):
        return None
    return s.upper() if len(s) > 4 else s.upper()


def _is_semantic_color(c: str | None) -> bool:
    h = _norm_hex(c)
    if not h:
        return False
    return h not in _NEUTRAL_HEX


def _semantic_color_keys(spec: FigureSpec) -> set[str]:
    """统计非 muted 的 variant 名 + box/panel 上的自定义语义色。"""
    keys: set[str] = set()
    for el in spec.elements:
        if isinstance(el, (BoxEl, PanelEl, TokensEl, NetworkEl)):
            v = getattr(el, "variant", None) or "primary"
            if v not in _MUTED_VARIANTS:
                keys.add(f"variant:{v}")
        if isinstance(el, (BoxEl, PanelEl)):
            for attr in ("fill", "stroke"):
                c = getattr(el, attr, None)
                if _is_semantic_color(c):
                    keys.add(f"color:{_norm_hex(c)}")
            grad = getattr(el, "gradient", None)
            if grad:
                for c in grad:
                    if _is_semantic_color(c):
                        keys.add(f"color:{_norm_hex(c)}")
        if isinstance(el, ArrowEl) and _is_semantic_color(el.color):
            keys.add(f"color:{_norm_hex(el.color)}")
    return keys


def _check_visual_richness(
    spec: FigureSpec, res: RenderResult, *, base_mode: bool = False,
) -> list[Issue]:
    """视觉丰度软检查（一律 W 级，不阻断）。

    base 模式：形象在底稿里，停用 R-empty-box / R-no-section / R-no-legend。
    """
    del res  # 几何信息已在其他检查使用；丰度只看 spec 结构
    if base_mode:
        return []
    issues: list[Issue] = []

    # R-empty-box：既无 body 又无 sketch/icon，且非容器 / 非长标题 / 非小标签条
    for el in spec.elements:
        if not isinstance(el, BoxEl):
            continue
        if (el.body or "").strip() or el.sketch or el.icon or el.gradient:
            continue
        if _box_has_inner_content(spec, el):
            continue
        # 小标签条 / 单行操作盒（如 LayerNorm、Reshape）豁免——不应诱导塞装饰
        if el.rect.w * el.rect.h <= _EMPTY_BOX_AREA_EXEMPT:
            continue
        # 长标题容器卡（常见于通栏标题条）豁免
        title_len = len(_strip_markup(el.title).replace("\n", "").strip())
        if title_len >= _EMPTY_BOX_TITLE_EXEMPT_LEN:
            continue
        issues.append(Issue(
            "W", "R-empty-box",
            f"box '{el.id}' 无 body/sketch/icon，显得空心；"
            f"建议补充有语义的 body/sketch，或确认该盒为小标签盒可忽略",
        ))

    # R-no-section：元素较多但无 panel / 带 fill 的 group
    n_el = len(spec.elements)
    if n_el >= _RICHNESS_MIN_ELEMENTS:
        has_panel = any(isinstance(e, PanelEl) for e in spec.elements)
        has_filled_group = any(
            isinstance(e, GroupEl) and _is_filled_section(e) for e in spec.elements
        )
        if not has_panel and not has_filled_group:
            issues.append(Issue(
                "W", "R-no-section",
                f"共 {n_el} 个元素但无 panel / 带 fill 的 group，缺少分区底色；"
                f"建议用 panel（header_style: smallcaps）或 group + fill: \"#F7F7F7\"",
            ))

    # R-no-legend：≥3 种非 muted 语义色但无 legend
    color_keys = _semantic_color_keys(spec)
    has_legend = any(isinstance(e, LegendEl) for e in spec.elements)
    if len(color_keys) >= _RICHNESS_MIN_SEMANTIC_COLORS and not has_legend:
        issues.append(Issue(
            "W", "R-no-legend",
            f"使用了 {len(color_keys)} 种非 muted 语义色但无 legend；"
            f"建议加 type: legend（swatch+label），或把次要模块改回 muted/plain",
        ))

    return issues


# ── base 混合模式：对比度 / 区域漂移 / 底板重叠 ──────────────


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return 0.5, 0.5, 0.5
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return r, g, b
    except ValueError:
        return 0.5, 0.5, 0.5


def _srgb_to_lin(c: float | np.ndarray) -> float | np.ndarray:
    """sRGB → 线性光（WCAG 相对亮度）。"""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _luma01(r, g, b):
    """WCAG 相对亮度；标量或 ndarray。"""
    return (
        0.2126 * _srgb_to_lin(r)
        + 0.7152 * _srgb_to_lin(g)
        + 0.0722 * _srgb_to_lin(b)
    )


def _contrast_ratio(l1: float, l2: float) -> float:
    a, b = (l1, l2) if l1 >= l2 else (l2, l1)
    return (a + 0.05) / (b + 0.05)


def _load_base_rgb(spec: FigureSpec) -> tuple[np.ndarray, float, float] | None:
    """加载底稿为 float RGB [0,1]；无 image 或文件不存在则 None（跳过相关检查）。"""
    path = spec.resolve_base_image()
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        with Image.open(p) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    except OSError:
        return None
    if arr.size == 0 or arr.shape[0] < 2 or arr.shape[1] < 2:
        return None
    sx = arr.shape[1] / max(spec.width, 1e-6)
    sy = arr.shape[0] / max(spec.height, 1e-6)
    return arr, sx, sy


def _sample_rect_rgb(
    arr: np.ndarray, sx: float, sy: float, rect: Rect,
) -> np.ndarray | None:
    """按 mm→px 映射采样矩形像素；空则 None。"""
    h, w = arr.shape[:2]
    x0 = int(math.floor(rect.x * sx))
    y0 = int(math.floor(rect.y * sy))
    x1 = int(math.ceil(rect.right * sx))
    y1 = int(math.ceil(rect.bottom * sy))
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    patch = arr[y0:y1, x0:x1]
    return patch if patch.size else None


def _simple_luma(r, g, b):
    """感知近似亮度（墨迹/花纹方差用；非 WCAG）。"""
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ink_density(patch: np.ndarray) -> float:
    """非白像素占比（简单亮度 < _INK_WHITE_LUMA）。"""
    if patch is None or patch.size == 0:
        return 0.0
    luma = _simple_luma(patch[..., 0], patch[..., 1], patch[..., 2])
    return float(np.mean(luma < _INK_WHITE_LUMA))


def _effective_bg_luma(
    patch: np.ndarray,
    *,
    has_plate: bool,
    plate_rgb: tuple[float, float, float],
    plate_opacity: float,
) -> tuple[float, float]:
    """返回 (有效背景 WCAG 平均亮度, 底稿局部简单亮度方差)。"""
    base_simple = _simple_luma(patch[..., 0], patch[..., 1], patch[..., 2])
    var = float(np.var(base_simple))
    if has_plate:
        op = max(0.0, min(1.0, plate_opacity))
        pr, pg, pb = plate_rgb
        er = pr * op + patch[..., 0] * (1.0 - op)
        eg = pg * op + patch[..., 1] * (1.0 - op)
        eb = pb * op + patch[..., 2] * (1.0 - op)
        eff = _luma01(er, eg, eb)
        return float(np.mean(eff)), var
    return float(np.mean(_luma01(patch[..., 0], patch[..., 1], patch[..., 2]))), var


def _plate_for_span(
    bbox: Rect, plates: list[tuple[str, Rect]],
) -> Rect | None:
    """文字包围盒中心落在的底板；取面积最小者（更贴字）。"""
    cx, cy = bbox.cx, bbox.cy
    hits = [r for _, r in plates if r.x <= cx <= r.right and r.y <= cy <= r.bottom]
    if not hits:
        # 中心未命中时，用高重叠兜底
        best: Rect | None = None
        best_frac = 0.0
        area = max(bbox.w * bbox.h, 1e-6)
        for _, r in plates:
            inter = bbox.intersection_area(r)
            frac = inter / area
            if frac > best_frac:
                best_frac = frac
                best = r
        return best if best_frac >= 0.35 else None
    return min(hits, key=lambda r: r.w * r.h)


def _blend_rgb01(
    under: tuple[float, float, float],
    over: tuple[float, float, float],
    opacity: float,
) -> tuple[float, float, float]:
    op = max(0.0, min(1.0, opacity))
    return (
        under[0] * (1.0 - op) + over[0] * op,
        under[1] * (1.0 - op) + over[1] * op,
        under[2] * (1.0 - op) + over[2] * op,
    )


def _solid_fill_under_text(
    bbox: Rect, spec: FigureSpec, res: RenderResult, th,
) -> tuple[float, float, float] | None:
    """文字落在 ghost:false 实体元素内时，返回该层填充色 RGB[0,1]。

    覆盖 box / panel / 有外框的 legend；多层重叠取 elements 绘制顺序最靠上者。
    panel banner 标题带用 header_fill；box header_fill 用描边 10% 叠在 body 上。
    """
    if spec.base is None:
        return None
    cx, cy = bbox.cx, bbox.cy
    found: tuple[float, float, float] | None = None

    for el in spec.elements:
        if isinstance(el, BoxEl):
            if _is_ghost(el, True):
                continue
            r = res.node_rects.get(el.id) or el.rect
            if not (r.x <= cx <= r.right and r.y <= cy <= r.bottom):
                continue
            v = th.variants.get(el.variant)
            if v is None:
                continue
            if el.gradient and len(el.gradient) >= 2:
                a = _hex_to_rgb01(el.gradient[0])
                b = _hex_to_rgb01(el.gradient[1])
                body = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
            else:
                body = _hex_to_rgb01(el.fill or v.fill)
            if el.header_fill:
                # 与 render 一致：标题区 stroke@0.10 叠在 body 上
                hh = min(r.h * 0.45, th.box_pad_y + 6.0)
                if cy <= r.y + hh:
                    stroke = _hex_to_rgb01(el.stroke or v.stroke)
                    found = _blend_rgb01(body, stroke, 0.10)
                else:
                    found = body
            else:
                found = body

        elif isinstance(el, PanelEl):
            if _is_ghost(el, True):
                continue
            r = res.node_rects.get(el.id) or el.rect
            if not (r.x <= cx <= r.right and r.y <= cy <= r.bottom):
                continue
            v = th.variants.get(el.variant)
            if v is None:
                continue
            body = _hex_to_rgb01(el.fill or v.fill)
            header = _hex_to_rgb01(el.header_fill or v.stroke)
            if el.header_style != "smallcaps":
                hh = min(el.header_h, r.h * 0.45)
                found = header if cy <= r.y + hh else body
            else:
                found = body

        elif isinstance(el, LegendEl):
            legend_style = el.style or th.default_legend_style or "card"
            inline = legend_style == "inline"
            use_frame = el.frame if el.frame_explicit else (False if inline else el.frame)
            if not use_frame:
                continue
            r = res.node_rects.get(el.id)
            if r is None:
                continue
            if not (r.x <= cx <= r.right and r.y <= cy <= r.bottom):
                continue
            found = _hex_to_rgb01(el.fill or "#FAFAFA")

    return found


def _check_plate_overlap(res: RenderResult) -> list[Issue]:
    """文字底板互相重叠超过较小者 30% → W。"""
    issues: list[Issue] = []
    plates = list(getattr(res, "text_plates", []) or [])
    for i in range(len(plates)):
        id1, r1 = plates[i]
        a1 = max(r1.w * r1.h, 1e-6)
        for j in range(i + 1, len(plates)):
            id2, r2 = plates[j]
            if id1 == id2:
                continue
            a2 = max(r2.w * r2.h, 1e-6)
            inter = r1.intersection_area(r2)
            frac = inter / min(a1, a2)
            if frac > _PLATE_OVERLAP_FRAC:
                issues.append(Issue(
                    "W", "plate-overlap",
                    f"文字底板 '{id1}' 与 '{id2}' 重叠 {frac:.0%}（>{_PLATE_OVERLAP_FRAC:.0%}）；"
                    f"文字过挤，拉开间距或缩短文案",
                ))
    return issues


def _check_base_text_contrast(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """对落在底稿上的文字采样有效背景，对比率 <3.0 或无 plate 花纹繁忙 → E。

    有效背景：文字落在 ghost:false 实体元素内时用该元素填充色（不再采样底稿）；
    否则采样底稿。有 plate（含箭头 label_bg）时按 opacity 与上述底色混合。
    底稿缺失时跳过（render 会在 image 路径无效时报错）。
    """
    loaded = _load_base_rgb(spec)
    if loaded is None:
        return []
    arr, sx, sy = loaded
    th = load_theme(spec.theme_cfg)
    plate_rgb = _hex_to_rgb01(th.plate_fill)
    plate_op = float(th.plate_opacity)
    arrow_plate_rgb = _hex_to_rgb01(_ARROW_LABEL_BG_FILL)

    plates = list(getattr(res, "text_plates", []) or [])
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    # 箭头标签：label_bg 开着视同有 plate
    arrow_caps: dict[str, tuple[Rect, bool, str]] = {}
    for aid, cap, label in getattr(res, "arrow_label_boxes", []) or []:
        el = arrows.get(aid)
        arrow_caps[aid] = (cap, bool(el.label_bg) if el is not None else True, label)

    issues: list[Issue] = []
    seen_keys: set[str] = set()

    # 1) 箭头标签（用胶囊几何；label_bg → plate）
    for aid, (cap, has_plate, label) in arrow_caps.items():
        solid = _solid_fill_under_text(cap, spec, res, th)
        if solid is not None:
            patch = np.full((1, 1, 3), solid, dtype=np.float32)
        else:
            patch = _sample_rect_rgb(arr, sx, sy, cap)
        if patch is None:
            continue
        # 找对应 span 颜色
        color = None
        for s in res.text_spans:
            if s.text == label and not getattr(s, "diagnostic", False):
                if cap.intersection_area(s.bbox()) > 0:
                    color = s.color
                    break
        if not color:
            color = th.muted
        tr, tg, tb = _hex_to_rgb01(color)
        text_l = _luma01(tr, tg, tb)
        bg_l, var = _effective_bg_luma(
            patch, has_plate=has_plate,
            plate_rgb=arrow_plate_rgb, plate_opacity=_ARROW_LABEL_BG_OPACITY,
        )
        ratio = _contrast_ratio(text_l, bg_l)
        key = f"arrow:{aid}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if ratio < _BASE_CONTRAST_MIN:
            issues.append(Issue(
                "E", "base-text-contrast",
                f"箭头 '{aid}' 标签 “{label[:14]}” 与底稿有效背景对比 {ratio:.2f}"
                f"（<{_BASE_CONTRAST_MIN}）；开 label_bg 或换净空区",
            ))
        elif (not has_plate) and var > _BASE_BUSY_VAR:
            issues.append(Issue(
                "E", "base-text-contrast",
                f"箭头 '{aid}' 标签 “{label[:14]}” 无底板且底稿花纹繁忙"
                f"（亮度方差 {var:.3f}>{_BASE_BUSY_VAR}）；开 label_bg 或换净空",
            ))

    # 2) 其余文字 span（box/text/panel…）
    arrow_label_texts = {lab for _, _, lab in getattr(res, "arrow_label_boxes", []) or []}
    for s in res.text_spans:
        if not s.text.strip() or getattr(s, "diagnostic", False):
            continue
        # 箭头标签已在上面检查
        if s.text in arrow_label_texts:
            continue
        bbox = s.bbox()
        if bbox.w < 0.2 or bbox.h < 0.2:
            continue
        plate_r = _plate_for_span(bbox, plates)
        has_plate = plate_r is not None
        solid = _solid_fill_under_text(bbox, spec, res, th)
        if solid is not None:
            patch = np.full((1, 1, 3), solid, dtype=np.float32)
        else:
            patch = _sample_rect_rgb(arr, sx, sy, bbox)
        if patch is None:
            continue
        tr, tg, tb = _hex_to_rgb01(s.color)
        text_l = _luma01(tr, tg, tb)
        bg_l, var = _effective_bg_luma(
            patch, has_plate=has_plate,
            plate_rgb=plate_rgb, plate_opacity=plate_op,
        )
        ratio = _contrast_ratio(text_l, bg_l)
        key = f"span:{s.text[:24]}:{bbox.x:.1f}:{bbox.y:.1f}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        snippet = s.text.strip()[:14]
        if ratio < _BASE_CONTRAST_MIN:
            issues.append(Issue(
                "E", "base-text-contrast",
                f"文字 “{snippet}” 与底稿有效背景对比 {ratio:.2f}"
                f"（<{_BASE_CONTRAST_MIN}）；开 plate 或换净空区",
            ))
        elif (not has_plate) and var > _BASE_BUSY_VAR:
            issues.append(Issue(
                "E", "base-text-contrast",
                f"文字 “{snippet}” 无底板且底稿花纹繁忙"
                f"（亮度方差 {var:.3f}>{_BASE_BUSY_VAR}）；开 plate 或换净空",
            ))
    return issues


def _check_base_region_drift(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """skeleton 模式：布局矩形内外墨迹对拍；近空或大量溢出环带 → W。

    用墨迹像素计数比（spill = 环带/(内+环)），阈值宽松：好卡 <3px 偏移
    spill≈0.07，不应误伤。freeform / 无底稿跳过。
    ghost:false 实体盒不参与（底稿被矢量填充盖住，漂移无意义）。
    """
    if spec.base is None or spec.base.mode != "skeleton":
        return []
    loaded = _load_base_rgb(spec)
    if loaded is None:
        return []
    arr, sx, sy = loaded
    issues: list[Issue] = []

    # 与骨架导出一致：对拍 box/asset 色块矩形（panel 极浅底不做漂移判据）
    for el in spec.elements:
        if not isinstance(el, (BoxEl, AssetEl)):
            continue
        # 实体盒盖住底稿，不参与漂移对拍
        if not _is_ghost(el, True):
            continue
        r = res.node_rects.get(el.id) or el.rect
        if r.w < 4 or r.h < 4:
            continue
        inner = _sample_rect_rgb(arr, sx, sy, r)
        if inner is None:
            continue
        inner_luma = _simple_luma(inner[..., 0], inner[..., 1], inner[..., 2])
        ink_in = int(np.sum(inner_luma < _INK_WHITE_LUMA))
        dens_in = float(ink_in / max(inner_luma.size, 1))

        outer_patch_meta = _sample_rect_with_origin(arr, sx, sy, r.expanded(_DRIFT_RING_MM))
        if outer_patch_meta is None:
            continue
        outer_patch, ox0, oy0 = outer_patch_meta
        ring_mask = np.ones(outer_patch.shape[:2], dtype=bool)
        ix0 = int(math.floor(r.x * sx)) - ox0
        iy0 = int(math.floor(r.y * sy)) - oy0
        ix1 = int(math.ceil(r.right * sx)) - ox0
        iy1 = int(math.ceil(r.bottom * sy)) - oy0
        ix0 = max(0, ix0); iy0 = max(0, iy0)
        ix1 = min(outer_patch.shape[1], ix1); iy1 = min(outer_patch.shape[0], iy1)
        if ix1 > ix0 and iy1 > iy0:
            ring_mask[iy0:iy1, ix0:ix1] = False
        if not np.any(ring_mask):
            continue
        ring_luma = _simple_luma(
            outer_patch[..., 0], outer_patch[..., 1], outer_patch[..., 2],
        )
        ink_ring = int(np.sum((ring_luma < _INK_WHITE_LUMA) & ring_mask))
        total_ink = ink_in + ink_ring
        spill = (ink_ring / total_ink) if total_ink > 0 else 0.0

        near_empty = dens_in < _DRIFT_EMPTY_IN
        overflow = (
            spill >= _DRIFT_SPILL_FRAC
            and ink_ring >= _DRIFT_SPILL_MIN_INK
            and dens_in < 0.70
        )
        if near_empty or overflow:
            why = []
            if near_empty:
                why.append(f"内密度 {dens_in:.2f}<{_DRIFT_EMPTY_IN}")
            if overflow:
                why.append(f"环带墨迹溢出比 {spill:.0%}≥{_DRIFT_SPILL_FRAC:.0%}")
            issues.append(Issue(
                "W", "base-region-drift",
                f"模块 '{el.id}' 与底稿内容疑似漂移（{'; '.join(why)}）；"
                f"建议重抽/换卡或核对骨架对齐",
            ))
    return issues


def _sample_rect_with_origin(
    arr: np.ndarray, sx: float, sy: float, rect: Rect,
) -> tuple[np.ndarray, int, int] | None:
    """采样矩形像素并返回左上角像素坐标。"""
    h, w = arr.shape[:2]
    x0 = int(math.floor(rect.x * sx))
    y0 = int(math.floor(rect.y * sy))
    x1 = int(math.ceil(rect.right * sx))
    y1 = int(math.ceil(rect.bottom * sy))
    x0c = max(0, min(w - 1, x0))
    y0c = max(0, min(h - 1, y0))
    x1c = max(x0c + 1, min(w, x1))
    y1c = max(y0c + 1, min(h, y1))
    patch = arr[y0c:y1c, x0c:x1c]
    if patch.size == 0:
        return None
    return patch, x0c, y0c
