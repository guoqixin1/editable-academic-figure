"""spec → SVG → PNG 渲染引擎。

设计原则：
- 所有几何由 spec 显式给出（mm），渲染器不做任何"智能"布局，保证可控与可复现；
- 文本用 fonts.py 的度量结果逐 run 发排，中西文混排不丢字、不错位；
- 素材图以 base64 内嵌，按 contain 规则适配到指定矩形；
- 素材缺失时渲染占位框（虚线+id），布局调优可以先于素材生成进行。
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from pathlib import Path

import cairosvg
from PIL import Image

from .fonts import (FAMILY_SVG, PT_TO_MM, LINE_HEIGHT, SCRIPT_SCALE, SUB_SHIFT,
                    SUP_SHIFT, line_ascent_mm, measure_markup_mm, measure_mm,
                    parse_markup, split_runs, text_block_height_mm, wrap_text)
from .spec import (ArrowEl, AssetEl, BadgeEl, BoxEl, FigureSpec, GroupEl,
                   MarkerEl, NetworkEl, PanelEl, PanelLabelEl, Rect, ScatterEl,
                   TextEl, TokensEl, parse_anchor)
from .theme import Theme, load_theme


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _wrap_el(el_id: str, svg: str) -> str:
    """给元素套 data-el 分组：studio 里点选/拖拽定位用，对静态导出无影响。"""
    return f'<g data-el="{_esc(el_id)}">{svg}</g>' if svg else svg


@dataclass
class _TextSpan:
    """已定位的一行文本（支持 _{}/^{} 记号），可直接转 SVG 并供 lint 使用。"""
    x: float          # 行起点（已按 anchor 折算）
    baseline: float
    text: str
    pt: float
    bold: bool
    color: str
    diagnostic: bool = False  # 占位标签等临时文本，不参与字号体检
    italic: bool = False
    rotate: float = 0.0       # 绕 (rot_cx, rot_cy) 旋转（度）
    rot_cx: float = 0.0
    rot_cy: float = 0.0

    @property
    def width(self) -> float:
        return measure_markup_mm(self.text, self.pt, self.bold)

    def bbox(self) -> Rect:
        asc = line_ascent_mm(self.text, self.pt, self.bold)
        h = self.pt * PT_TO_MM * LINE_HEIGHT
        r = Rect(self.x, self.baseline - asc, self.width, h)
        if not self.rotate:
            return r
        # 旋转后的轴对齐包络（lint 用）
        a = math.radians(self.rotate)
        ca, sa = math.cos(a), math.sin(a)
        xs, ys = [], []
        for px, py in ((r.x, r.y), (r.right, r.y), (r.x, r.bottom), (r.right, r.bottom)):
            dx, dy = px - self.rot_cx, py - self.rot_cy
            xs.append(self.rot_cx + dx * ca - dy * sa)
            ys.append(self.rot_cy + dx * sa + dy * ca)
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def to_svg(self) -> str:
        base_mm = self.pt * PT_TO_MM
        style = (" font-weight=\"bold\"" if self.bold else "") + \
                (" font-style=\"italic\"" if self.italic else "")
        parts = [f'<text fill="{self.color}"{style}>']
        x = self.x
        for seg, mode in parse_markup(self.text):
            if not seg:
                continue
            pt = self.pt * (SCRIPT_SCALE if mode != "n" else 1.0)
            shift = SUB_SHIFT if mode == "sub" else (SUP_SHIFT if mode == "sup" else 0.0)
            y = self.baseline + shift * base_mm
            for run, cls in split_runs(seg):
                fam = FAMILY_SVG[cls]
                parts.append(
                    f'<tspan x="{x:.3f}" y="{y:.3f}" font-size="{pt * PT_TO_MM:.4f}" '
                    f'font-family="{_esc(fam)}" xml:space="preserve">{_esc(run)}</tspan>')
                x += measure_mm(run, pt, self.bold)
        parts.append("</text>")
        svg = "".join(parts)
        if self.rotate:
            svg = (f'<g transform="rotate({self.rotate:.2f} {self.rot_cx:.3f} {self.rot_cy:.3f})">'
                   + svg + "</g>")
        return svg


class RenderResult:
    """渲染产物 + 供 lint 使用的几何记录。"""

    def __init__(self) -> None:
        self.svg: str = ""
        self.text_spans: list[_TextSpan] = []
        self.node_rects: dict[str, Rect] = {}
        self.arrow_segments: list[tuple[str, list[tuple[float, float]]]] = []
        self.asset_boxes: dict[str, Rect] = {}       # 素材实际显示区域
        self.missing_assets: list[str] = []
        self.placeholder_assets: list[str] = []      # 意图性占位槽（待手动插入实验图）
        self.overflow_boxes: list[str] = []          # 文本溢出的 box id


def render(spec: FigureSpec, out_png: str | Path | None = None,
           grid: bool = False, dpi: int | None = None) -> RenderResult:
    theme = load_theme(spec.theme_cfg)
    fs = spec.font_scale
    res = RenderResult()

    body: list[str] = []

    # 先解析全部节点矩形（箭头锚点、group 需要）
    for el in spec.elements:
        if isinstance(el, (BoxEl, AssetEl, PanelEl, TokensEl, NetworkEl, ScatterEl)):
            res.node_rects[el.id] = el.rect

    # 绘制顺序：panel 最底 → group → box/asset/tokens/network/scatter → arrow → 标注层
    panels = [e for e in spec.elements if isinstance(e, PanelEl)]
    groups = [e for e in spec.elements if isinstance(e, GroupEl)]
    nodes = [e for e in spec.elements
             if isinstance(e, (BoxEl, AssetEl, TokensEl, NetworkEl, ScatterEl))]
    arrows = [e for e in spec.elements if isinstance(e, ArrowEl)]
    texts = [e for e in spec.elements
             if isinstance(e, (TextEl, PanelLabelEl, MarkerEl, BadgeEl))]

    for p in panels:
        body.append(_wrap_el(p.id, _render_panel(p, theme, fs, res)))
    for g in groups:
        body.append(_wrap_el(g.id, _render_group(g, spec, theme, fs, res)))
    for n in nodes:
        if isinstance(n, BoxEl):
            s = _render_box(n, spec, theme, fs, res)
        elif isinstance(n, TokensEl):
            s = _render_tokens(n, theme, fs, res)
        elif isinstance(n, NetworkEl):
            s = _render_network(n, theme)
        elif isinstance(n, ScatterEl):
            s = _render_scatter(n)
        else:
            s = _render_asset(n, spec, theme, fs, res)
        body.append(_wrap_el(n.id, s))
    for a in arrows:
        body.append(_wrap_el(a.id, _render_arrow(a, theme, fs, res)))
    for t in texts:
        if isinstance(t, TextEl):
            s = _render_text(t, theme, fs, res)
        elif isinstance(t, MarkerEl):
            s = _render_marker(t)
        elif isinstance(t, BadgeEl):
            s = _render_badge(t, theme, fs, res)
        else:
            s = _render_panel_label(t, theme, fs, res)
        body.append(_wrap_el(t.id, s))

    if grid:
        body.append(_render_grid(spec))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{spec.width}mm" height="{spec.height}mm" '
        f'viewBox="0 0 {spec.width} {spec.height}">'
        f'<rect x="0" y="0" width="{spec.width}" height="{spec.height}" fill="{spec.background}"/>'
        + "".join(body) + "</svg>"
    )
    res.svg = svg

    if out_png:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        the_dpi = dpi or spec.dpi
        scale = the_dpi / 25.4  # px per mm
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out_png),
                         output_width=round(spec.width * scale),
                         output_height=round(spec.height * scale))
    return res


# ---------------------------------------------------------------- shapes

def _cyl_cap_ry(r: Rect) -> float:
    return min(r.h * 0.13, r.w * 0.5)


def _shape_svg(shape: str, r: Rect, fill: str, stroke: str, lw: float, corner: float) -> str:
    """输出形状本体 SVG（不含文字）。所有形状内切于 rect 包围盒。"""
    style = f'fill="{fill}" stroke="{stroke}" stroke-width="{lw}" stroke-linejoin="round"'
    if shape == "rect":
        return (f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
                f'rx="{corner}" {style}/>')
    if shape == "stadium":
        return (f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
                f'rx="{r.h / 2:.3f}" {style}/>')
    if shape == "ellipse":
        return (f'<ellipse cx="{r.cx:.3f}" cy="{r.cy:.3f}" rx="{r.w / 2:.3f}" '
                f'ry="{r.h / 2:.3f}" {style}/>')
    if shape == "diamond":
        pts = f"{r.cx:.3f},{r.y:.3f} {r.right:.3f},{r.cy:.3f} {r.cx:.3f},{r.bottom:.3f} {r.x:.3f},{r.cy:.3f}"
        return f'<polygon points="{pts}" {style}/>'
    if shape == "parallelogram":
        s = r.w * 0.16
        pts = f"{r.x + s:.3f},{r.y:.3f} {r.right:.3f},{r.y:.3f} {r.right - s:.3f},{r.bottom:.3f} {r.x:.3f},{r.bottom:.3f}"
        return f'<polygon points="{pts}" {style}/>'
    if shape == "hexagon":
        d = r.w * 0.14
        pts = (f"{r.x + d:.3f},{r.y:.3f} {r.right - d:.3f},{r.y:.3f} {r.right:.3f},{r.cy:.3f} "
               f"{r.right - d:.3f},{r.bottom:.3f} {r.x + d:.3f},{r.bottom:.3f} {r.x:.3f},{r.cy:.3f}")
        return f'<polygon points="{pts}" {style}/>'
    if shape == "trapezoid":
        # 上宽下窄漏斗形（backbone / 下采样），需要反向可把 rect 上下想象颠倒后用 v 向渐变辅助
        d = r.w * 0.18
        pts = (f"{r.x:.3f},{r.y:.3f} {r.right:.3f},{r.y:.3f} "
               f"{r.right - d:.3f},{r.bottom:.3f} {r.x + d:.3f},{r.bottom:.3f}")
        return f'<polygon points="{pts}" {style}/>'
    if shape == "cylinder":
        ry = _cyl_cap_ry(r)
        rx = r.w / 2
        top = r.y + ry
        bot = r.bottom - ry
        body = (f'<path d="M {r.x:.3f},{top:.3f} L {r.x:.3f},{bot:.3f} '
                f'A {rx:.3f},{ry:.3f} 0 0 0 {r.right:.3f},{bot:.3f} '
                f'L {r.right:.3f},{top:.3f} A {rx:.3f},{ry:.3f} 0 0 0 {r.x:.3f},{top:.3f} Z" {style}/>')
        cap = (f'<ellipse cx="{r.cx:.3f}" cy="{top:.3f}" rx="{rx:.3f}" ry="{ry:.3f}" {style}/>')
        return body + cap
    raise ValueError(f"未知 shape: {shape}")


def _shape_inner(shape: str, r: Rect, pad_x: float, pad_y: float) -> tuple[float, float]:
    """形状内可用文字区域 (inner_w, avail_h)，文字始终以 r 中心对齐。"""
    if shape == "rect":
        return r.w - 2 * pad_x, r.h - 2 * pad_y
    if shape == "stadium":
        return max(r.w - r.h, r.w * 0.5), r.h - 2 * pad_y
    if shape == "ellipse":
        return r.w * 0.70, r.h * 0.72
    if shape == "diamond":
        return r.w * 0.60, r.h * 0.58
    if shape == "parallelogram":
        return r.w - 2 * (r.w * 0.16) - 2 * pad_x, r.h - 2 * pad_y
    if shape == "hexagon":
        return r.w - 2 * (r.w * 0.14) - pad_x, r.h - 2 * pad_y
    if shape == "trapezoid":
        return r.w - 2 * (r.w * 0.18) - pad_x, r.h - 2 * pad_y
    if shape == "cylinder":
        ry = _cyl_cap_ry(r)
        return r.w - 2 * pad_x, r.h - 2 * ry - pad_y
    raise ValueError(f"未知 shape: {shape}")


# ---------------------------------------------------------------- box

def _render_box(el: BoxEl, spec: FigureSpec, th: Theme, fs: float, res: RenderResult) -> str:
    v = th.variants.get(el.variant)
    if v is None:
        raise ValueError(f"box '{el.id}': 未知 variant '{el.variant}'（可选 {list(th.variants)}）")
    r = el.rect
    fill = el.fill or v.fill
    stroke = el.stroke or v.stroke
    out = []
    if el.gradient:
        gid = f"grad_{el.id}"
        x2, y2 = ("100%", "0%") if el.gradient_dir == "h" else ("0%", "100%")
        out.append(
            f'<defs><linearGradient id="{gid}" x1="0%" y1="0%" x2="{x2}" y2="{y2}">'
            f'<stop offset="0%" stop-color="{el.gradient[0]}"/>'
            f'<stop offset="100%" stop-color="{el.gradient[1]}"/>'
            f'</linearGradient></defs>')
        fill = f"url(#{gid})"
    # 叠影（层叠卡片/文档效果）：由远及近画在主体后面，向右下错位
    for k in range(el.stack, 0, -1):
        off = 1.5 * k
        sr = Rect(r.x + off, r.y + off, r.w, r.h)
        out.append(_shape_svg(el.shape, sr, el.fill or v.fill, stroke, th.lw_box, th.corner_radius))
    out.append(_shape_svg(el.shape, r, fill, stroke, th.lw_box, th.corner_radius))

    inner_w, avail_h = _shape_inner(el.shape, r, th.box_pad_x, th.box_pad_y)
    title_pt = (el.title_size or th.size_title) * fs
    body_pt = (el.body_size or th.size_body) * fs

    title_lines = wrap_text(el.title, title_pt, inner_w, bold=True) if el.title else []
    body_lines = wrap_text(el.body, body_pt, inner_w) if el.body else []

    has_icon = bool(el.icon) and el.icon_h > 0
    icon_h = el.icon_h if has_icon else 0.0
    icon_gap = 1.2 if has_icon else 0.0
    text_h = (text_block_height_mm(len(title_lines), title_pt)
              + (0.6 if title_lines and body_lines else 0.0)
              + text_block_height_mm(len(body_lines), body_pt))
    content_h = icon_h + icon_gap + text_h

    if content_h > avail_h + 0.05:
        res.overflow_boxes.append(el.id)

    if el.valign == "top":
        # 标题贴顶：box 作容器/子卡（内部再放其它元素）
        y = r.y + th.box_pad_y
    else:
        # 圆柱顶盖占位，文字整体略下移以视觉居中
        cy = r.cy + (_cyl_cap_ry(r) * 0.5 if el.shape == "cylinder" else 0.0)
        y = cy - content_h / 2

    if has_icon:
        icon_path = spec.resolve_asset(el.icon)
        slot = Rect(r.x + th.box_pad_x, y, inner_w, icon_h)
        out.append(_embed_image(icon_path, slot, "center", "middle", el.icon + "@" + el.id, res))
        y += icon_h + icon_gap

    blocks = [b for b in ((title_lines, title_pt, True), (body_lines, body_pt, False)) if b[0]]
    for bi, (lines, pt, bold) in enumerate(blocks):
        if bi > 0:
            y += 0.6  # title 与 body 间距（仅块间，与 content_h 估算一致）
        lh = pt * PT_TO_MM * LINE_HEIGHT
        for ln in lines:
            asc = line_ascent_mm(ln.text or "x", pt, bold)
            if el.align == "left":
                x = r.x + th.box_pad_x
            else:
                x = r.cx - ln.width_mm / 2
            span = _TextSpan(x=x, baseline=y + asc, text=ln.text, pt=pt, bold=bold,
                             color=el.text_color or v.text)
            res.text_spans.append(span)
            out.append(span.to_svg())
            y += lh

    return "".join(out)


# ---------------------------------------------------------------- asset

def _render_asset(el: AssetEl, spec: FigureSpec, th: Theme, fs: float, res: RenderResult) -> str:
    path = spec.resolve_asset(el.src)
    out = []
    cap_pt = th.size_caption * fs
    cap_h = 0.0
    cap_lines = []
    if el.caption:
        cap_lines = wrap_text(el.caption, cap_pt, el.rect.w)
        cap_h = text_block_height_mm(len(cap_lines), cap_pt) + 0.8

    img_rect = Rect(el.rect.x, el.rect.y, el.rect.w, el.rect.h - cap_h)
    if el.placeholder and not path.exists():
        # 意图性占位：真实实验结果（频谱图/照片/mesh 等）后续手动插入
        res.placeholder_assets.append(el.src)
        out.append(_placeholder_slot(img_rect, f"[{Path(el.src).stem}]", res))
    else:
        out.append(_embed_image(path, img_rect, el.halign, el.valign, el.src, res,
                                frame=el.frame, theme=th))

    y = img_rect.bottom + 0.8
    lh = cap_pt * PT_TO_MM * LINE_HEIGHT
    for ln in cap_lines:
        asc = line_ascent_mm(ln.text or "x", cap_pt)
        span = _TextSpan(x=el.rect.cx - ln.width_mm / 2, baseline=y + asc,
                         text=ln.text, pt=cap_pt, bold=False, color=th.muted)
        res.text_spans.append(span)
        out.append(span.to_svg())
        y += lh
    return "".join(out)


def _placeholder_slot(slot: Rect, label: str, res: RenderResult) -> str:
    """虚线占位槽 + 居中标签（标签属临时诊断文本，不参与字号体检）。"""
    pt = 6.0
    w = measure_mm(label, pt)
    # 标签超宽时缩小到槽内
    if w > slot.w - 2 and w > 0:
        pt = max(4.0, pt * (slot.w - 2) / w)
        w = measure_mm(label, pt)
    span = _TextSpan(x=slot.cx - w / 2, baseline=slot.cy + 1.0, text=label,
                     pt=pt, bold=False, color="#9AA5B1", diagnostic=True)
    res.text_spans.append(span)
    return (f'<rect x="{slot.x:.3f}" y="{slot.y:.3f}" width="{slot.w:.3f}" height="{slot.h:.3f}" '
            f'fill="#F5F7FA" stroke="#9AA5B1" stroke-width="0.2" stroke-dasharray="1.2,0.8"/>'
            + span.to_svg())


def _embed_image(path: Path, slot: Rect, halign: str, valign: str, ref: str,
                 res: RenderResult, frame: bool = False, theme: Theme | None = None) -> str:
    if not path.exists():
        res.missing_assets.append(ref)
        return _placeholder_slot(slot, f"[{ref}]", res)

    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(slot.w / iw, slot.h / ih)
    w, h = iw * scale, ih * scale
    x = {"left": slot.x, "center": slot.cx - w / 2, "right": slot.right - w}[halign]
    y = {"top": slot.y, "middle": slot.cy - h / 2, "bottom": slot.bottom - h}[valign]

    shown = Rect(x, y, w, h)
    res.asset_boxes[ref] = shown

    data = base64.b64encode(path.read_bytes()).decode()
    out = (f'<image x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
           f'xlink:href="data:image/png;base64,{data}" '
           f'xmlns:xlink="http://www.w3.org/1999/xlink" preserveAspectRatio="none"/>')
    if frame and theme:
        out += (f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
                f'fill="none" stroke="{theme.group_stroke}" stroke-width="0.2"/>')
    return out


# ---------------------------------------------------------------- arrow

def _anchor_point(ep: str | tuple[float, float], rects: dict[str, Rect],
                  toward: tuple[float, float] | None = None) -> tuple[float, float, str]:
    """返回 (x, y, side)。side 用于路由决策，坐标端点 side='free'。

    ep 为裸节点 id（未写 .side）时按 `toward`（对方端点参考点）方向**自动选朝向对方的那条边**，
    落在该边中点——这是消除"箭头没对上"的主力：调用方只写 `from: enc, to: dec` 即可。
    """
    if not isinstance(ep, str):
        return ep[0], ep[1], "free"
    node, side, t = parse_anchor(ep)
    r = rects[node]
    if side is None:
        tx, ty = toward if toward is not None else (r.cx, r.cy)
        dx, dy = tx - r.cx, ty - r.cy
        if abs(dx) >= abs(dy):
            side = "right" if dx >= 0 else "left"
        else:
            side = "bottom" if dy >= 0 else "top"
        t = 0.5
    if side == "left":
        return r.x, r.y + t * r.h, "left"
    if side == "right":
        return r.right, r.y + t * r.h, "right"
    if side == "top":
        return r.x + t * r.w, r.y, "top"
    if side == "bottom":
        return r.x + t * r.w, r.bottom, "bottom"
    return r.cx, r.cy, "center"


def _route_points(x1: float, y1: float, s1: str, x2: float, y2: float, s2: str,
                  route: str) -> list[tuple[float, float]]:
    if route == "auto":
        horiz = {"left", "right"}
        vert = {"top", "bottom"}
        if s1 in horiz and s2 in horiz:
            route = "straight" if abs(y1 - y2) < 0.5 else "z"
        elif s1 in vert and s2 in vert:
            route = "straight" if abs(x1 - x2) < 0.5 else "zv"
        elif s1 in horiz and s2 in vert:
            route = "hv"
        elif s1 in vert and s2 in horiz:
            route = "vh"
        else:
            route = "straight"

    if route == "straight":
        return [(x1, y1), (x2, y2)]
    if route == "hv":
        return [(x1, y1), (x2, y1), (x2, y2)]
    if route == "vh":
        return [(x1, y1), (x1, y2), (x2, y2)]
    if route == "z":
        mx = (x1 + x2) / 2
        return [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
    if route == "zv":
        my = (y1 + y2) / 2
        return [(x1, y1), (x1, my), (x2, my), (x2, y2)]
    raise ValueError(f"未知 route: {route}")


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [points[0]]
    for p in points[1:]:
        if abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    return out


def _ref_center(ep: str | tuple[float, float], rects: dict[str, Rect]) -> tuple[float, float]:
    """端点的参考中心，供对侧自动选边定向用（节点取几何中心，坐标端点取自身）。"""
    if not isinstance(ep, str):
        return ep[0], ep[1]
    node, _, _ = parse_anchor(ep)
    r = rects[node]
    return r.cx, r.cy


def _render_arrow(el: ArrowEl, th: Theme, fs: float, res: RenderResult) -> str:
    color = el.color or th.arrow
    # 先算对方参考中心：from 朝向 to、to 朝向 from，裸 id 端点据此自动选边
    x1, y1, s1 = _anchor_point(el.from_, res.node_rects, toward=_ref_center(el.to, res.node_rects))
    x2, y2, s2 = _anchor_point(el.to, res.node_rects, toward=_ref_center(el.from_, res.node_rects))

    if el.style == "block":
        return _render_block_arrow(el, (x1, y1), (x2, y2), color, th, fs, res)

    arc_ctrl: tuple[float, float] | None = None
    if el.route == "arc" and not el.via:
        # 二次贝塞尔弧线：控制点 = 弦中点 + 法向 × bend × 弦长
        dx, dy = x2 - x1, y2 - y1
        chord = math.hypot(dx, dy)
        if chord < 1e-6:
            res.arrow_segments.append((el.id, [(x1, y1)]))
            return ""
        nx, ny = -dy / chord, dx / chord
        arc_ctrl = ((x1 + x2) / 2 + nx * el.bend * chord,
                    (y1 + y2) / 2 + ny * el.bend * chord)
        # 供 lint / 标签用的采样折线
        pts = []
        for i in range(9):
            t = i / 8
            mt = 1 - t
            pts.append((mt * mt * x1 + 2 * mt * t * arc_ctrl[0] + t * t * x2,
                        mt * mt * y1 + 2 * mt * t * arc_ctrl[1] + t * t * y2))
    elif el.via:
        # 手动途经点：起点 → via... → 终点，直线连接（用于残差/绕线/总线）
        pts = _dedupe([(x1, y1), *el.via, (x2, y2)])
    else:
        route = "straight" if el.route == "arc" else el.route
        pts = _dedupe(_route_points(x1, y1, s1, x2, y2, s2, route))

    if len(pts) < 2:
        # 起止点重合（如两端锚到同一位置），无法绘制箭头
        res.arrow_segments.append((el.id, pts))
        return ""

    out = []
    lw = el.width or th.lw_arrow
    # 粗线时箭头头部按线宽比例放大，保持观感协调
    scale = max(1.0, (lw / th.lw_arrow) ** 0.75)
    head_len = th.arrow_head_len * scale
    head_w = th.arrow_head_w * scale

    def _shorten(a: tuple[float, float], b: tuple[float, float], d: float) -> tuple[float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L <= d:
            return a
        return b[0] - dx / L * d, b[1] - dy / L * d

    if arc_ctrl is not None:
        end = _shorten(arc_ctrl, (x2, y2), head_len * 0.72) if el.head == "arrow" else (x2, y2)
        start = (_shorten(arc_ctrl, (x1, y1), head_len * 0.72)
                 if el.head == "arrow" and el.bidir else (x1, y1))
        d = (f"M {start[0]:.3f},{start[1]:.3f} "
             f"Q {arc_ctrl[0]:.3f},{arc_ctrl[1]:.3f} {end[0]:.3f},{end[1]:.3f}")
    else:
        draw_pts = list(pts)
        if el.head == "arrow":
            draw_pts[-1] = _shorten(draw_pts[-2], draw_pts[-1], head_len * 0.72)
            if el.bidir:
                draw_pts[0] = _shorten(draw_pts[1], draw_pts[0], head_len * 0.72)
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in draw_pts)

    dash = ' stroke-dasharray="1.6,1.1"' if el.style == "dashed" else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
               f'{dash} stroke-linejoin="round" stroke-linecap="round"/>')

    if el.head == "arrow":
        tail2, tip2 = (arc_ctrl, (x2, y2)) if arc_ctrl is not None else (pts[-2], pts[-1])
        out.append(_arrow_head(tail2, tip2, color, th, head_len, head_w))
        if el.bidir:
            tail1, tip1 = (arc_ctrl, (x1, y1)) if arc_ctrl is not None else (pts[1], pts[0])
            out.append(_arrow_head(tail1, tip1, color, th, head_len, head_w))

    res.arrow_segments.append((el.id, pts))

    if el.label:
        # 放在最长线段中点，垂直偏移
        best = max(range(len(pts) - 1),
                   key=lambda i: math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
        (ax, ay), (bx, by) = pts[best], pts[best + 1]
        mx, my = (ax + bx) / 2, (ay + by) / 2
        pt_size = th.size_arrow_label * fs
        w = measure_markup_mm(el.label, pt_size)
        horiz = abs(bx - ax) >= abs(by - ay)
        if horiz:
            tx, ty_base = mx - w / 2, my - el.label_offset
        else:
            tx, ty_base = mx + el.label_offset, my - (pt_size * PT_TO_MM) / 2 + line_ascent_mm(el.label, pt_size) - 0.35 * pt_size * PT_TO_MM + pt_size * PT_TO_MM * 0.35
            ty_base = my + line_ascent_mm(el.label, pt_size) / 2 - 0.2
        span = _TextSpan(x=tx, baseline=ty_base, text=el.label, pt=pt_size, bold=False, color=th.muted)
        bb = span.bbox()
        out.append(f'<rect x="{bb.x - 0.5:.3f}" y="{bb.y - 0.1:.3f}" width="{bb.w + 1.0:.3f}" '
                   f'height="{bb.h + 0.2:.3f}" fill="#FFFFFF" fill-opacity="0.88"/>')
        res.text_spans.append(span)
        out.append(span.to_svg())

    return "".join(out)


def _arrow_head(a: tuple[float, float], b: tuple[float, float], color: str, th: Theme,
                head_len: float | None = None, head_w: float | None = None) -> str:
    hl = head_len if head_len is not None else th.arrow_head_len
    hw = head_w if head_w is not None else th.arrow_head_w
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    L1 = (b[0] - ux * hl, b[1] - uy * hl)
    p1 = (L1[0] + px * hw / 2, L1[1] + py * hw / 2)
    p2 = (L1[0] - px * hw / 2, L1[1] - py * hw / 2)
    return (f'<polygon points="{b[0]:.3f},{b[1]:.3f} {p1[0]:.3f},{p1[1]:.3f} {p2[0]:.3f},{p2[1]:.3f}" '
            f'fill="{color}"/>')


def _render_block_arrow(el: ArrowEl, p1: tuple[float, float], p2: tuple[float, float],
                        color: str, th: Theme, fs: float, res: RenderResult) -> str:
    """空心/实心粗箭头（block arrow）：始终直线，多用于短距离的强调流向。"""
    x1, y1 = p1
    x2, y2 = p2
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        res.arrow_segments.append((el.id, [p1]))
        return ""
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    px, py = -uy, ux
    sw = (el.width or 3.0) / 2          # 半箭杆宽
    hw = sw * 2.2                        # 半头宽
    hl = min(sw * 3.2, L * 0.45)         # 头长
    bx, by = x2 - ux * hl, y2 - uy * hl  # 头根部中心
    pts = [
        (x1 + px * sw, y1 + py * sw), (bx + px * sw, by + py * sw),
        (bx + px * hw, by + py * hw), (x2, y2),
        (bx - px * hw, by - py * hw), (bx - px * sw, by - py * sw),
        (x1 - px * sw, y1 - py * sw),
    ]
    fill = el.fill or "#FFFFFF"
    poly = " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    res.arrow_segments.append((el.id, [p1, p2]))
    out = [f'<polygon points="{poly}" fill="{fill}" stroke="{color}" '
           f'stroke-width="{th.lw_box}" stroke-linejoin="round"/>']
    if el.label:
        pt_size = th.size_arrow_label * fs
        w = measure_markup_mm(el.label, pt_size)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if abs(x2 - x1) >= abs(y2 - y1):
            tx, ty = mx - w / 2, my - sw - el.label_offset
        else:
            tx, ty = mx + hw + el.label_offset, my + line_ascent_mm(el.label, pt_size) / 2 - 0.2
        span = _TextSpan(x=tx, baseline=ty, text=el.label, pt=pt_size, bold=False, color=th.muted)
        res.text_spans.append(span)
        out.append(span.to_svg())
    return "".join(out)


# ---------------------------------------------------------------- group / text / panel label / grid

def _render_group(el: GroupEl, spec: FigureSpec, th: Theme, fs: float, res: RenderResult) -> str:
    if el.rect is not None:
        r = el.rect
    else:
        rects = [res.node_rects[m] for m in el.members]
        x0 = min(r.x for r in rects) - el.pad
        y0 = min(r.y for r in rects) - el.pad
        x1 = max(r.right for r in rects) + el.pad
        y1 = max(r.bottom for r in rects) + el.pad
        r = Rect(x0, y0, x1 - x0, y1 - y0)

    dash = ' stroke-dasharray="2.2,1.4"' if el.style == "dashed" else ""
    fill = el.fill or th.group_fill
    stroke = el.color or th.group_stroke
    lw = el.lw if el.lw is not None else th.lw_group
    out = [f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
           f'rx="{th.corner_radius + 0.6}" fill="{fill}" stroke="{stroke}" '
           f'stroke-width="{lw}"{dash}/>']
    res.node_rects[el.id] = r

    if el.label:
        pt = (el.label_size or th.size_group_label) * fs
        w = measure_markup_mm(el.label, pt, bold=True)
        asc = line_ascent_mm(el.label, pt, bold=True)
        if el.label_pos == "inside-top":
            lx, baseline = r.x + 2.5, r.y + 1.6 + asc
        elif el.label_pos == "inside-bottom":
            lx, baseline = r.cx - w / 2, r.bottom - 2.0
        else:  # top（框外上方）
            lx, baseline = r.x + 3.0, r.y - 1.2
        label_color = el.color or th.muted
        span = _TextSpan(x=lx, baseline=baseline, text=el.label, pt=pt, bold=True,
                         color=label_color)
        res.text_spans.append(span)
        out.append(span.to_svg())
    return "".join(out)


def _render_text(el: TextEl, th: Theme, fs: float, res: RenderResult) -> str:
    pt = el.size * fs
    color = el.color or th.ink
    x, y = el.at
    out = []
    lines = wrap_text(el.text, pt, el.max_w, el.bold) if el.max_w else [
        ln for ln in (type("L", (), {"text": t, "width_mm": measure_markup_mm(t, pt, el.bold)})()
                      for t in el.text.split("\n"))
    ]
    lh = pt * PT_TO_MM * LINE_HEIGHT
    cy = y
    for ln in lines:
        if el.anchor == "start":
            lx = x
        elif el.anchor == "end":
            lx = x - ln.width_mm
        else:
            lx = x - ln.width_mm / 2
        asc = line_ascent_mm(ln.text or "x", pt, el.bold)
        span = _TextSpan(x=lx, baseline=cy + asc, text=ln.text, pt=pt, bold=el.bold,
                         color=color, italic=el.italic,
                         rotate=el.rotate, rot_cx=x, rot_cy=y)
        res.text_spans.append(span)
        out.append(span.to_svg())
        cy += lh
    return "".join(out)


def _render_panel_label(el: PanelLabelEl, th: Theme, fs: float, res: RenderResult) -> str:
    pt = th.size_panel_label * fs
    x, y = el.at
    asc = line_ascent_mm(el.text, pt, True)
    span = _TextSpan(x=x, baseline=y + asc, text=el.text, pt=pt, bold=True, color=th.ink)
    res.text_spans.append(span)
    return span.to_svg()


# ---------------------------------------------------------------- panel / tokens / marker

def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _render_panel(el: PanelEl, th: Theme, fs: float, res: RenderResult) -> str:
    v = th.variants.get(el.variant)
    if v is None:
        raise ValueError(f"panel '{el.id}': 未知 variant '{el.variant}'（可选 {list(th.variants)}）")
    r = el.rect
    header_fill = el.header_fill or v.stroke
    body_fill = el.fill or v.fill
    cr = th.corner_radius + 0.4
    hh = min(el.header_h, r.h * 0.45)

    out = [
        # 面板体
        f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
        f'rx="{cr}" fill="{body_fill}" stroke="{header_fill}" stroke-width="{th.lw_box}"/>',
        # 标题条（仅上侧圆角）
        f'<path d="M {r.x + cr:.3f},{r.y:.3f} H {r.right - cr:.3f} '
        f'A {cr},{cr} 0 0 1 {r.right:.3f},{r.y + cr:.3f} V {r.y + hh:.3f} '
        f'H {r.x:.3f} V {r.y + cr:.3f} A {cr},{cr} 0 0 1 {r.x + cr:.3f},{r.y:.3f} Z" '
        f'fill="{header_fill}"/>',
    ]

    if el.title:
        pt = (el.title_size or th.size_title) * fs
        color = "#FFFFFF" if _luminance(header_fill) < 0.62 else th.ink
        w = measure_markup_mm(el.title, pt, bold=True)
        asc = line_ascent_mm(el.title, pt, bold=True)
        baseline = r.y + hh / 2 + asc / 2 - 0.3
        span = _TextSpan(x=r.cx - w / 2, baseline=baseline, text=el.title,
                         pt=pt, bold=True, color=color)
        res.text_spans.append(span)
        out.append(span.to_svg())
    return "".join(out)


def _render_tokens(el: TokensEl, th: Theme, fs: float, res: RenderResult) -> str:
    v = th.variants.get(el.variant)
    if v is None:
        raise ValueError(f"tokens '{el.id}': 未知 variant '{el.variant}'（可选 {list(th.variants)}）")
    r = el.rect
    n, gap = el.n, el.gap
    out = []

    if el.direction == "h":
        cell_main = (r.w - (n - 1) * gap) / n
        if cell_main <= 0.2:
            raise ValueError(f"tokens '{el.id}': 格子过窄（n 太大或 rect 太小）")
    else:
        cell_main = (r.h - (n - 1) * gap) / n
        if cell_main <= 0.2:
            raise ValueError(f"tokens '{el.id}': 格子过矮（n 太大或 rect 太小）")

    for i in range(n):
        fill = el.colors[i % len(el.colors)] if el.colors else v.fill
        cross = el.sizes[i % len(el.sizes)] if el.sizes else (r.h if el.direction == "h" else r.w)
        if el.direction == "h":
            x = r.x + i * (cell_main + gap)
            y = r.cy - cross / 2
            w, h = cell_main, cross
        else:
            x = r.cx - cross / 2
            y = r.y + i * (cell_main + gap)
            w, h = cross, cell_main
        out.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
                   f'rx="0.5" fill="{fill}" stroke="{v.stroke}" stroke-width="{th.lw_box * 0.75:.3f}"/>')

    if el.label:
        pt = th.size_caption * fs
        w = measure_markup_mm(el.label, pt, bold=True)
        asc = line_ascent_mm(el.label, pt, bold=True)
        if el.direction == "h":
            # 条带左侧
            span = _TextSpan(x=r.x - w - 1.6, baseline=r.cy + asc / 2 - 0.3,
                             text=el.label, pt=pt, bold=True, color=th.ink)
        else:
            # 条带上方
            span = _TextSpan(x=r.cx - w / 2, baseline=r.y - 1.2,
                             text=el.label, pt=pt, bold=True, color=th.ink)
        res.text_spans.append(span)
        out.append(span.to_svg())
    return "".join(out)


# 图标绘制在 100×100 单位坐标系内，中心 (50,50)
_MARKER_DEFAULT_COLOR = {
    "fire": "#E8590C", "snow": "#3B82C4", "lock": "#8A6D1B",
    "check": "#2E9E44", "cross": "#C0392B",
    "oplus": "#1F2933", "otimes": "#1F2933", "wifi": "#3B82C4",
}


def _marker_paths(icon: str, color: str) -> str:
    if icon == "fire":
        inner = "#FFC078"
        return (
            f'<path d="M50,4 C60,24 84,32 84,58 A34,34 0 1 1 16,58 C16,34 40,26 50,4 Z" fill="{color}"/>'
            f'<path d="M50,40 C55,51 67,55 67,67 A17,17 0 1 1 33,67 C33,55 45,51 50,40 Z" fill="{inner}"/>')
    if icon == "snow":
        import math as _m
        parts = []
        for k in range(6):
            a = _m.radians(k * 60)
            ca, sa = _m.cos(a), _m.sin(a)
            x2, y2 = 50 + 44 * ca, 50 + 44 * sa
            parts.append(f'<line x1="{50 + 6 * ca:.1f}" y1="{50 + 6 * sa:.1f}" '
                         f'x2="{x2:.1f}" y2="{y2:.1f}"/>')
            for s in (-1, 1):
                b = a + s * _m.radians(35)
                bx, by = 50 + 28 * ca, 50 + 28 * sa
                parts.append(f'<line x1="{bx:.1f}" y1="{by:.1f}" '
                             f'x2="{bx + 13 * _m.cos(b):.1f}" y2="{by + 13 * _m.sin(b):.1f}"/>')
        return (f'<g stroke="{color}" stroke-width="7" stroke-linecap="round" fill="none">'
                + "".join(parts) + "</g>")
    if icon == "lock":
        return (
            f'<path d="M30,46 V34 A20,20 0 0 1 70,34 V46" fill="none" stroke="{color}" '
            f'stroke-width="9" stroke-linecap="round"/>'
            f'<rect x="22" y="46" width="56" height="42" rx="7" fill="{color}"/>'
            f'<circle cx="50" cy="63" r="6.5" fill="#FFFFFF"/>'
            f'<rect x="47" y="63" width="6" height="13" rx="3" fill="#FFFFFF"/>')
    if icon == "check":
        return (f'<path d="M18,55 L42,77 L82,26" fill="none" stroke="{color}" '
                f'stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>')
    if icon == "cross":
        return (f'<g stroke="{color}" stroke-width="13" stroke-linecap="round">'
                f'<line x1="26" y1="26" x2="74" y2="74"/>'
                f'<line x1="74" y1="26" x2="26" y2="74"/></g>')
    if icon == "oplus":
        # ⊕ 拼接/求和算子
        return (f'<circle cx="50" cy="50" r="42" fill="#FFFFFF" stroke="{color}" stroke-width="7"/>'
                f'<g stroke="{color}" stroke-width="7" stroke-linecap="round">'
                f'<line x1="50" y1="22" x2="50" y2="78"/>'
                f'<line x1="22" y1="50" x2="78" y2="50"/></g>')
    if icon == "otimes":
        # ⊗ 逐元素乘算子
        return (f'<circle cx="50" cy="50" r="42" fill="#FFFFFF" stroke="{color}" stroke-width="7"/>'
                f'<g stroke="{color}" stroke-width="7" stroke-linecap="round">'
                f'<line x1="30" y1="30" x2="70" y2="70"/>'
                f'<line x1="70" y1="30" x2="30" y2="70"/></g>')
    if icon == "wifi":
        # 无线信号：三段弧 + 点（朝上发散）
        return (f'<g fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round">'
                f'<path d="M20,52 A42,42 0 0 1 80,52"/>'
                f'<path d="M32,66 A26,26 0 0 1 68,66"/></g>'
                f'<circle cx="50" cy="82" r="7" fill="{color}"/>')
    raise ValueError(f"未知 marker icon: {icon}")


def _render_marker(el: MarkerEl) -> str:
    color = el.color or _MARKER_DEFAULT_COLOR[el.icon]
    x, y = el.at
    s = el.size / 100.0
    return (f'<g transform="translate({x - el.size / 2:.3f},{y - el.size / 2:.3f}) scale({s:.5f})">'
            + _marker_paths(el.icon, color) + "</g>")


# ---------------------------------------------------------------- network / scatter / badge

def _render_network(el: NetworkEl, th: Theme) -> str:
    """迷你 MLP：相邻层全连接细线 + 圆形节点。direction=v 时层自上而下。"""
    v = th.variants.get(el.variant)
    if v is None:
        raise ValueError(f"network '{el.id}': 未知 variant '{el.variant}'（可选 {list(th.variants)}）")
    color = el.color or v.stroke
    node_fill = el.node_fill or "#FFFFFF"
    r = el.rect
    L = len(el.layers)

    # 各层节点圆心坐标
    centers: list[list[tuple[float, float]]] = []
    max_n = max(el.layers)
    if el.direction == "v":
        node_r = min(r.h / (L * 2.6), r.w / (max_n * 2.6))
        for li, n in enumerate(el.layers):
            y = r.y + node_r + (r.h - 2 * node_r) * (li / (L - 1))
            span = (n - 1) if n > 1 else 0
            step = (r.w - 2 * node_r) / max(max_n - 1, 1)
            x0 = r.cx - span * step / 2
            centers.append([(x0 + k * step, y) for k in range(n)])
    else:
        node_r = min(r.w / (L * 2.6), r.h / (max_n * 2.6))
        for li, n in enumerate(el.layers):
            x = r.x + node_r + (r.w - 2 * node_r) * (li / (L - 1))
            span = (n - 1) if n > 1 else 0
            step = (r.h - 2 * node_r) / max(max_n - 1, 1)
            y0 = r.cy - span * step / 2
            centers.append([(x, y0 + k * step) for k in range(n)])

    out = [f'<g stroke="{color}" stroke-width="{th.lw_box * 0.55:.3f}" opacity="0.75">']
    for a_layer, b_layer in zip(centers, centers[1:]):
        for ax, ay in a_layer:
            for bx, by in b_layer:
                out.append(f'<line x1="{ax:.3f}" y1="{ay:.3f}" x2="{bx:.3f}" y2="{by:.3f}"/>')
    out.append("</g>")
    for layer in centers:
        for cx, cy in layer:
            out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{node_r:.3f}" '
                       f'fill="{node_fill}" stroke="{color}" stroke-width="{th.lw_box:.3f}"/>')
    return "".join(out)


def _render_scatter(el: ScatterEl) -> str:
    """聚类散点：椭圆包络 + 高斯散点，seed 固定保证可复现。"""
    import random
    rng = random.Random(el.seed)
    r = el.rect
    out = []
    for c in el.clusters:
        cx = r.x + c.at[0] * r.w
        cy = r.y + c.at[1] * r.h
        rx = c.rx * r.w
        ry = c.ry * r.h
        parts = []
        if el.outline != "none":
            dash = ' stroke-dasharray="1.4,1.0"' if el.outline == "dashed" else ""
            parts.append(f'<ellipse cx="{cx:.3f}" cy="{cy:.3f}" rx="{rx:.3f}" ry="{ry:.3f}" '
                         f'fill="{c.color}" fill-opacity="0.10" stroke="{c.color}" '
                         f'stroke-width="0.25"{dash}/>')
        else:
            parts.append(f'<ellipse cx="{cx:.3f}" cy="{cy:.3f}" rx="{rx:.3f}" ry="{ry:.3f}" '
                         f'fill="{c.color}" fill-opacity="0.10"/>')
        for _ in range(c.n):
            # 极坐标高斯采样，截断在椭圆内
            rad = min(abs(rng.gauss(0, 0.42)), 0.92)
            ang = rng.uniform(0, 2 * math.pi)
            px = cx + rad * rx * math.cos(ang)
            py = cy + rad * ry * math.sin(ang)
            parts.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="{el.dot_r:.3f}" '
                         f'fill="{c.color}" fill-opacity="0.85"/>')
        if c.rot:
            out.append(f'<g transform="rotate({c.rot:.2f} {cx:.3f} {cy:.3f})">'
                       + "".join(parts) + "</g>")
        else:
            out.append("".join(parts))
    return "".join(out)


def _render_badge(el: BadgeEl, th: Theme, fs: float, res: RenderResult) -> str:
    fill = el.color or th.variants["primary"].stroke
    x, y = el.at
    rad = el.size / 2
    pt = el.size * 0.52 / PT_TO_MM
    w = measure_mm(el.text, pt, bold=True)
    asc = line_ascent_mm(el.text, pt, bold=True)
    # 编号是图形的一部分，不参与字号体检
    span = _TextSpan(x=x - w / 2, baseline=y + asc * 0.38, text=el.text, pt=pt,
                     bold=True, color=el.text_color, diagnostic=True)
    res.text_spans.append(span)
    return (f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{rad:.3f}" fill="{fill}"/>'
            + span.to_svg())


def _render_grid(spec: FigureSpec) -> str:
    out = ['<g opacity="0.55">']
    step = 10
    for x in range(0, int(spec.width) + 1, step):
        out.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{spec.height}" '
                   f'stroke="#FF00AA" stroke-width="0.08"/>')
        out.append(f'<text x="{x + 0.4}" y="2.2" font-size="1.8" fill="#FF00AA" '
                   f'font-family="DejaVu Sans">{x}</text>')
    for y in range(0, int(spec.height) + 1, step):
        out.append(f'<line x1="0" y1="{y}" x2="{spec.width}" y2="{y}" '
                   f'stroke="#FF00AA" stroke-width="0.08"/>')
        out.append(f'<text x="0.4" y="{y + 2.0}" font-size="1.8" fill="#FF00AA" '
                   f'font-family="DejaVu Sans">{y}</text>')
    out.append("</g>")
    return "".join(out)
