"""渲染后自动体检（视觉评审闭环的客观部分）。

参考短视频工作流 phase9 的自评审思路，把"审稿人雷点"里能被
几何/度量捕获的项做成硬检查；构图审美等主观项留给多模态目检。

检查项：
  E 级（错误，必须修）
    - 文本溢出所在盒子
    - 文本相互重叠
    - 文本超出画布
    - 素材缺失
    - 箭头穿过无关节点
  W 级（警告，建议修）
    - 字号低于下限（印刷缩放后 <6pt）
    - 节点重叠
    - 素材实际显示尺寸过小（槽位浪费）
    - 画布利用率过低 / 元素过挤
"""

from __future__ import annotations

from dataclasses import dataclass

from .render import RenderResult
from .spec import ArrowEl, AssetEl, BoxEl, FigureSpec, Rect

MIN_FONT_PT = 5.5   # 180mm 图按 1:1 排版时的裸下限
IDEAL_MIN_FONT_PT = 6.0


@dataclass
class Issue:
    level: str   # E | W
    code: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.msg}"


def lint(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    issues: list[Issue] = []

    for ref in res.missing_assets:
        issues.append(Issue("E", "asset-missing", f"素材未找到: {ref}（渲染为占位框）"))

    for ref in res.placeholder_assets:
        issues.append(Issue("W", "asset-placeholder",
                            f"占位槽 '{ref}' 待手动插入真实实验图（把文件放到该路径即自动嵌入）"))

    for bid in res.overflow_boxes:
        issues.append(Issue("E", "text-overflow", f"box '{bid}' 内容高度超出盒子，加大盒子或缩短文字"))

    issues += _check_text_overlap(res)
    issues += _check_canvas_bounds(spec, res)
    issues += _check_font_sizes(res)
    issues += _check_node_overlap(spec, res)
    issues += _check_arrow_crossings(spec, res)
    issues += _check_asset_scale(res)
    issues += _check_density(spec, res)
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


def _check_font_sizes(res: RenderResult) -> list[Issue]:
    issues = []
    seen: set[float] = set()
    for s in res.text_spans:
        if not s.text.strip() or s.pt in seen or getattr(s, "diagnostic", False):
            continue
        seen.add(s.pt)
        if s.pt < MIN_FONT_PT:
            issues.append(Issue("W", "font-too-small",
                                f"{s.pt:.1f}pt 低于下限 {MIN_FONT_PT}pt（如 “{s.text[:12]}”）"))
        elif s.pt < IDEAL_MIN_FONT_PT:
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


def _check_arrow_crossings(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """箭头线段穿过既非起点也非终点的节点。"""
    issues = []
    arrows = {el.id: el for el in spec.elements if isinstance(el, ArrowEl)}
    for aid, pts in res.arrow_segments:
        el = arrows.get(aid)
        exempt = set()
        if el:
            for ep in (el.from_, el.to):
                if isinstance(ep, str):
                    exempt.add(ep.split(".")[0])
        if not pts:
            continue
        start, end = pts[0], pts[-1]
        for nid, r in res.node_rects.items():
            if nid in exempt or nid.startswith("_group") or "@" in nid:
                continue
            node_el = spec.find(nid)
            if not isinstance(node_el, (BoxEl, AssetEl)):
                continue
            # 箭头起/终点落在该节点内部 → 端点节点是它的子元素（容器嵌套），
            # 穿出容器边界是有意行为，不算穿线
            if r.contains_point(*start) or r.contains_point(*end):
                continue
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                if _segment_hits_rect(x1, y1, x2, y2, r.expanded(-0.4)):
                    issues.append(Issue("E", "arrow-through-node",
                                        f"箭头 '{aid}' 穿过节点 '{nid}'"))
                    break
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


def _check_asset_scale(res: RenderResult) -> list[Issue]:
    issues = []
    for ref, shown in res.asset_boxes.items():
        if "@" in ref:  # box 内嵌图标不检查
            continue
        if shown.w < 12 and shown.h < 12:
            issues.append(Issue("W", "asset-tiny",
                                f"素材 '{ref}' 显示尺寸仅 {shown.w:.0f}×{shown.h:.0f}mm，考虑加大槽位或裁剪素材"))
    return issues


def _check_density(spec: FigureSpec, res: RenderResult) -> list[Issue]:
    """两个独立信号：
    - 内容包围盒对画布的覆盖率过低 → 四周留白太多（真正的"空"）；
    - 节点面积占内容包围盒过高 → 元素挤成一团。
    仅用节点面积占画布比会误伤连线密集的数据流/分布式图（大量留白是给箭头的）。
    """
    issues = []
    canvas_area = spec.width * spec.height

    xs0, ys0, xs1, ys1 = [], [], [], []
    node_area = 0.0
    for el in spec.elements:
        if isinstance(el, (BoxEl, AssetEl)):
            r = el.rect
            node_area += r.w * r.h
            xs0.append(r.x); ys0.append(r.y); xs1.append(r.right); ys1.append(r.bottom)
    for r in res.node_rects.values():  # 含 group 包围盒
        xs0.append(r.x); ys0.append(r.y); xs1.append(r.right); ys1.append(r.bottom)
    for s in res.text_spans:
        if s.text.strip():
            b = s.bbox()
            xs0.append(b.x); ys0.append(b.y); xs1.append(b.right); ys1.append(b.bottom)

    if not xs0:
        return issues

    bbox_w = max(xs1) - min(xs0)
    bbox_h = max(ys1) - min(ys0)
    coverage = (bbox_w * bbox_h) / canvas_area

    if coverage < 0.45:
        issues.append(Issue("W", "canvas-sparse",
                            f"内容仅覆盖画布 {coverage:.0%}，四周留白过多，考虑缩小画布尺寸"))

    if node_area / canvas_area > 0.82:
        issues.append(Issue("W", "canvas-crowded",
                            f"节点面积占画布 {node_area / canvas_area:.0%}，画面偏挤，考虑加大画布"))
    return issues
