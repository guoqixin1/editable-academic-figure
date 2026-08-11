"""spec → SVG → PNG 渲染引擎。

设计原则：
- 所有几何由 spec 显式给出（mm），渲染器不做任何"智能"布局，保证可控与可复现；
- 文本用 fonts.py 的度量结果逐 run 发排，中西文混排不丢字、不错位；
- 素材图以 base64 内嵌，按 contain 规则适配到指定矩形；
- 素材缺失时渲染占位框（虚线+id），布局调优可以先于素材生成进行。
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFilter

from .fonts import (FAMILY_SVG, PT_TO_MM, LINE_HEIGHT, SCRIPT_SCALE, SUB_SHIFT,
                    SUP_SHIFT, line_ascent_mm, measure_markup_mm, measure_mm,
                    parse_markup, split_runs, text_block_height_mm, wrap_text)
from .routing import (RouteRequest, max_label_path_dist, pick_best_label, route_all)
from .spec import (ArrowEl, AssetEl, BadgeEl, BoxEl, FigureSpec, GroupEl,
                   LegendEl, MarkerEl, NetworkEl, PanelEl, PanelLabelEl, Rect,
                   ScatterEl, SketchEl, TextEl, TokensEl, parse_anchor)
from .theme import Theme, Variant, load_theme


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
    smallcaps: bool = False
    letter_spacing: float = 0.0  # mm，字符间距（smallcaps 用）

    @property
    def width(self) -> float:
        w = measure_markup_mm(self.text, self.pt, self.bold)
        if self.letter_spacing and self.text:
            # 约 n-1 个间距（markup 标记字符忽略，按可见字符近似）
            n = max(len(self.text.replace("_{", "").replace("^{", "").replace("}", "")) - 1, 0)
            w += n * self.letter_spacing
        return w

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
        ls = f' letter-spacing="{self.letter_spacing:.4f}"' if self.letter_spacing else ""
        parts = [f'<text fill="{self.color}"{style}{ls}>']
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
                if self.letter_spacing and run:
                    x += max(len(run) - 1, 0) * self.letter_spacing
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
        # 视觉外边界（含 accent 外扩、stack 叠影）；箭头锚点用这个，不用逻辑 rect
        self.node_visual_rects: dict[str, Rect] = {}
        self.arrow_segments: list[tuple[str, list[tuple[float, float]]]] = []
        self.arrow_ends: list[tuple[str, str, str]] = []  # (id, start_side, end_side)
        # 箭头标签胶囊（含 padding），供 lint 查压字 / 盖尖端
        self.arrow_label_boxes: list[tuple[str, Rect, str]] = []
        # box 内 sketch / accent 色条 / 独立 sketch：(owner_id, kind, rect)
        # kind 为 sketch 名（waveform/…）或 accent-left / accent-top
        self.sketch_rects: list[tuple[str, str, Rect]] = []
        # panel 标题文字带（标题行 + 分隔线/色条）：落标硬拒 + lint
        self.panel_title_bands: list[tuple[str, Rect]] = []
        self.asset_boxes: dict[str, Rect] = {}       # 素材实际显示区域
        self.missing_assets: list[str] = []
        self.placeholder_assets: list[str] = []      # 意图性占位槽（待手动插入实验图）
        self.overflow_boxes: list[str] = []          # 文本溢出的 box id
        # 渲染期软警告（level, code, msg），由 lint 合并；如 route-avoid-fallback
        self.soft_issues: list[tuple[str, str, str]] = []
        # base 混合模式
        self.base_mode: bool = False
        # 文字底板几何（owner_id, plate_rect），供后续 lint
        self.text_plates: list[tuple[str, Rect]] = []


def _is_ghost(el, base_mode: bool) -> bool:
    """base 模式下 box/asset/panel 默认幽灵；元素 ghost: false 恢复实体。"""
    if not base_mode:
        return False
    g = getattr(el, "ghost", None)
    return True if g is None else bool(g)


def _use_plate(el, base_mode: bool) -> bool:
    """base 模式下默认开文字底板；元素 plate: false 关闭。"""
    if not base_mode:
        return False
    p = getattr(el, "plate", None)
    return True if p is None else bool(p)


def _plate_svg(r: Rect, th: Theme) -> str:
    return (
        f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
        f'rx="{th.plate_radius:.3f}" fill="{th.plate_fill}" '
        f'fill-opacity="{th.plate_opacity}"/>'
    )


def _union_span_plate(spans: list[_TextSpan], th: Theme) -> Rect | None:
    """文字 span 包围盒并集 + plate_pad。"""
    bbs = [s.bbox() for s in spans if s.text.strip()]
    if not bbs:
        return None
    x0 = min(b.x for b in bbs)
    y0 = min(b.y for b in bbs)
    x1 = max(b.right for b in bbs)
    y1 = max(b.bottom for b in bbs)
    return Rect(x0, y0, x1 - x0, y1 - y0).expanded(th.plate_pad)


def _embed_base_image(spec: FigureSpec) -> str:
    """底稿全画布底层：data URI 内嵌，铺满 figure 尺寸（mm）。"""
    path = spec.resolve_base_image()
    if path is None:
        return ""
    if not path.is_file():
        raise FileNotFoundError(
            f"底稿文件不存在: {path}（spec.base.image={spec.base.image!r}，"
            f"路径相对 spec 目录 {spec.path.parent}）"
        )
    suf = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suf, "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f'<image x="0" y="0" width="{spec.width}" height="{spec.height}" '
        f'preserveAspectRatio="none" data-base="1" '
        f'xlink:href="data:{mime};base64,{data}" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink"/>'
    )


# 叠影每层向右下错位（与 _render_box 一致）
_STACK_OFF_MM = 1.5
# 折线首/末段沿锚定边法向离开/进入的最短长度（mm）
_MIN_APPROACH_MM = 3.0


def _box_accent_thickness(el: BoxEl, r: Rect) -> float:
    """accent 色条厚度 mm（与绘制逻辑一致）。"""
    if el.accent == "left":
        return min(1.1, r.w * 0.08)
    if el.accent == "top":
        return min(1.1, r.h * 0.12)
    return 0.0


def visual_rect_for(el) -> Rect:
    """元素视觉外边界：accent 向外扩、stack 叠影扩 right/bottom。"""
    r = el.rect
    x, y, w, h = r.x, r.y, r.w, r.h
    if isinstance(el, BoxEl):
        aw = _box_accent_thickness(el, r)
        if el.accent == "left" and aw > 0:
            x -= aw
            w += aw
        elif el.accent == "top" and aw > 0:
            y -= aw
            h += aw
        if el.stack > 0:
            off = _STACK_OFF_MM * el.stack
            w += off
            h += off
    return Rect(x, y, w, h)


def render(spec: FigureSpec, out_png: str | Path | None = None,
           grid: bool = False, dpi: int | None = None) -> RenderResult:
    theme = load_theme(spec.theme_cfg)
    fs = spec.font_scale
    res = RenderResult()
    base_mode = spec.base is not None
    res.base_mode = base_mode

    body: list[str] = []

    # 先解析全部节点矩形（箭头锚点、group 需要）
    for el in spec.elements:
        if isinstance(el, (BoxEl, AssetEl, PanelEl, TokensEl, NetworkEl, ScatterEl, SketchEl)):
            res.node_rects[el.id] = el.rect
            # 幽灵盒不画 accent/stack，视觉边界 = 逻辑 rect
            if isinstance(el, BoxEl) and _is_ghost(el, base_mode):
                res.node_visual_rects[el.id] = el.rect
            else:
                res.node_visual_rects[el.id] = visual_rect_for(el)

    # 绘制顺序：panel 最底 → group → box/asset/tokens/network/scatter/sketch → arrow → 标注层
    panels = [e for e in spec.elements if isinstance(e, PanelEl)]
    groups = [e for e in spec.elements if isinstance(e, GroupEl)]
    nodes = [e for e in spec.elements
             if isinstance(e, (BoxEl, AssetEl, TokensEl, NetworkEl, ScatterEl, SketchEl))]
    arrows = [e for e in spec.elements if isinstance(e, ArrowEl)]
    texts = [e for e in spec.elements
             if isinstance(e, (TextEl, PanelLabelEl, MarkerEl, BadgeEl, LegendEl))]

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
        elif isinstance(n, SketchEl):
            s = _render_sketch(n, theme, fs, res)
        else:
            s = _render_asset(n, spec, theme, fs, res)
        body.append(_wrap_el(n.id, s))

    # 箭头：先批量避障路由 → 两阶段落标 → 再绘制（旧 route 路径像素不变）
    avoid_paths = _precompute_avoid_routes(arrows, spec, res)
    arrow_geoms = [_resolve_arrow_geometry(a, res, avoid_paths) for a in arrows]
    auto_labels = _place_auto_arrow_labels(arrows, arrow_geoms, spec, res, theme, fs)
    for a, geom in zip(arrows, arrow_geoms):
        body.append(_wrap_el(
            a.id,
            _render_arrow(a, theme, fs, res,
                          precomputed=geom,
                          precomputed_label=auto_labels.get(a.id)),
        ))
    for t in texts:
        if isinstance(t, TextEl):
            s = _render_text(t, theme, fs, res)
        elif isinstance(t, MarkerEl):
            s = _render_marker(t)
        elif isinstance(t, BadgeEl):
            s = _render_badge(t, theme, fs, res)
        elif isinstance(t, LegendEl):
            s = _render_legend(t, theme, fs, res)
        else:
            s = _render_panel_label(t, theme, fs, res)
        body.append(_wrap_el(t.id, s))

    # 主题/figure 级浅网格底（isosystem 等）；studio 调试网格仍用 grid=True
    use_theme_grid = spec.grid_bg if spec.grid_bg is not None else theme.grid_bg
    if use_theme_grid:
        body.insert(0, _render_theme_grid(spec, theme))
    if grid:
        body.append(_render_grid(spec))

    # 底稿打底：白底之上、一切矢量之下
    base_img = _embed_base_image(spec) if base_mode else ""

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{spec.width}mm" height="{spec.height}mm" '
        f'viewBox="0 0 {spec.width} {spec.height}">'
        f'<rect x="0" y="0" width="{spec.width}" height="{spec.height}" fill="{spec.background}"/>'
        + base_img
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


# ---------------------------------------------------------------- helpers (shadow / seed / lw)

def _use_shadow(el_shadow: bool | None, th: Theme) -> bool:
    return th.default_shadow if el_shadow is None else el_shadow


def _variant_lw(v: Variant, th: Theme) -> float:
    return v.lw if v.lw is not None else th.lw_box


def _stable_seed(*parts) -> int:
    """由 id / 坐标等生成可复现 seed。"""
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16)


# soft-shadow 光栅参数（mm）；cairosvg 不支持 feDropShadow/feGaussianBlur
_SHADOW_BLUR_MM = 0.85
_SHADOW_Y_OFF_MM = 0.30       # ≤0.4mm
_SHADOW_X_OFF_MM = 0.0
_SHADOW_OPACITY = 0.16        # 模糊前峰值；模糊后外缘更淡，密集排布不脏
_SHADOW_PPM = 24.0            # px/mm，保证 600dpi 下晕边仍平滑


@lru_cache(maxsize=256)
def _shadow_png_data(w_mm: float, h_mm: float, corner: float) -> tuple[str, float, float]:
    """生成高斯模糊投影 PNG（base64）及图像尺寸（mm）。按 (w,h,corner) 缓存。"""
    pad = _SHADOW_BLUR_MM * 2.4
    img_w_mm = w_mm + 2.0 * pad + abs(_SHADOW_X_OFF_MM)
    img_h_mm = h_mm + 2.0 * pad + abs(_SHADOW_Y_OFF_MM)
    iw = max(1, int(math.ceil(img_w_mm * _SHADOW_PPM)))
    ih = max(1, int(math.ceil(img_h_mm * _SHADOW_PPM)))

    mask = Image.new("L", (iw, ih), 0)
    draw = ImageDraw.Draw(mask)
    x0 = pad * _SHADOW_PPM
    y0 = pad * _SHADOW_PPM
    x1 = (pad + w_mm) * _SHADOW_PPM
    y1 = (pad + h_mm) * _SHADOW_PPM
    rad = max(0.0, corner) * _SHADOW_PPM
    fill_v = max(0, min(255, int(round(255 * _SHADOW_OPACITY))))
    draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=fill_v)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=_SHADOW_BLUR_MM * _SHADOW_PPM))

    shadow = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    shadow.putalpha(mask)
    buf = io.BytesIO()
    shadow.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), img_w_mm, img_h_mm


def _soft_shadow_svg(r: Rect, corner: float) -> str:
    """PIL 高斯模糊 soft drop-shadow，base64 嵌入 SVG。

    cairosvg 对 feDropShadow / feGaussianBlur 支持极差；矢量多层半透明
    rect/stroke 会留下可辨的灰卡/环状边缘。改为光栅模糊光晕后嵌入，
    边缘平滑渐隐，整体仅保留很小的 y 向偏移。
    """
    # 量化尺寸以便缓存命中（0.05mm）
    w_q = round(r.w * 20) / 20
    h_q = round(r.h * 20) / 20
    c_q = round(max(0.0, corner) * 20) / 20
    data, img_w, img_h = _shadow_png_data(w_q, h_q, c_q)
    pad = _SHADOW_BLUR_MM * 2.4
    ix = r.x - pad + _SHADOW_X_OFF_MM
    iy = r.y - pad + _SHADOW_Y_OFF_MM
    return (
        f'<image x="{ix:.3f}" y="{iy:.3f}" width="{img_w:.3f}" height="{img_h:.3f}" '
        f'preserveAspectRatio="none" '
        f'xlink:href="data:image/png;base64,{data}" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink"/>'
    )


def _hatch_pattern_svg(pid: str, color: str) -> str:
    return (
        f'<defs><pattern id="{pid}" patternUnits="userSpaceOnUse" width="2.4" height="2.4" '
        f'patternTransform="rotate(45)">'
        f'<line x1="0" y1="0" x2="0" y2="2.4" stroke="{color}" stroke-width="0.18" '
        f'stroke-opacity="0.45"/></pattern></defs>')


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
    lw = _variant_lw(v, th)
    ghost = _is_ghost(el, res.base_mode)
    out = []

    if not ghost:
        if _use_shadow(el.shadow, th):
            out.append(_soft_shadow_svg(r, th.corner_radius))
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
            out.append(_shape_svg(el.shape, sr, el.fill or v.fill, stroke, lw, th.corner_radius))
        out.append(_shape_svg(el.shape, r, fill, stroke, lw, th.corner_radius))

        # accent 色条画在逻辑框*外侧*，与箭头视觉锚点一致（尖端落在色条外缘）
        if el.accent == "left":
            aw = _box_accent_thickness(el, r)
            accent_r = Rect(r.x - aw, r.y, aw, r.h)
            res.sketch_rects.append((el.id, "accent-left", accent_r))
            out.append(f'<rect x="{accent_r.x:.3f}" y="{accent_r.y:.3f}" width="{accent_r.w:.3f}" '
                       f'height="{accent_r.h:.3f}" '
                       f'rx="{min(th.corner_radius, aw / 2):.3f}" fill="{stroke}"/>')
        elif el.accent == "top":
            ah = _box_accent_thickness(el, r)
            accent_r = Rect(r.x, r.y - ah, r.w, ah)
            res.sketch_rects.append((el.id, "accent-top", accent_r))
            out.append(f'<rect x="{accent_r.x:.3f}" y="{accent_r.y:.3f}" width="{accent_r.w:.3f}" '
                       f'height="{accent_r.h:.3f}" fill="{stroke}"/>')

    inner_w, avail_h = _shape_inner(el.shape, r, th.box_pad_x, th.box_pad_y)
    # accent 已外置，内容区不再为色条让位
    content_x0 = r.x + th.box_pad_x
    title_pt = (el.title_size or th.size_title) * fs
    body_pt = (el.body_size or th.size_body) * fs

    title_lines = wrap_text(el.title, title_pt, inner_w, bold=True) if el.title else []
    body_lines = wrap_text(el.body, body_pt, inner_w) if el.body else []

    # 幽灵模式：不画 icon/sketch（底稿已有形象）
    has_icon = (not ghost) and bool(el.icon) and el.icon_h > 0
    icon_h = el.icon_h if has_icon else 0.0
    icon_gap = 1.2 if has_icon else 0.0
    text_h = (text_block_height_mm(len(title_lines), title_pt)
              + (0.6 if title_lines and body_lines else 0.0)
              + text_block_height_mm(len(body_lines), body_pt))
    has_sketch = (not ghost) and bool(el.sketch)
    sketch_gap = 1.0 if has_sketch and (title_lines or body_lines or has_icon) else 0.0
    # sketch 占用标题/正文下方剩余空间
    sketch_h = 0.0
    if has_sketch:
        used = icon_h + icon_gap + text_h + sketch_gap
        sketch_h = max(avail_h - used, min(8.0, avail_h * 0.35))
        if el.valign == "top":
            sketch_h = max(avail_h - used, 6.0)
    content_h = icon_h + icon_gap + text_h + sketch_gap + sketch_h

    if content_h > avail_h + 0.05 and not has_sketch:
        res.overflow_boxes.append(el.id)
    elif has_sketch and (icon_h + icon_gap + text_h) > avail_h + 0.05:
        res.overflow_boxes.append(el.id)

    # header_fill：标题区浅底 + 分隔线（幽灵模式跳过）
    if (not ghost) and el.header_fill and title_lines:
        hh = text_block_height_mm(len(title_lines), title_pt) + th.box_pad_y + 0.8
        hh = min(hh, r.h * 0.45)
        out.append(f'<path d="M {r.x + th.corner_radius:.3f},{r.y:.3f} '
                   f'H {r.right - th.corner_radius:.3f} '
                   f'A {th.corner_radius},{th.corner_radius} 0 0 1 {r.right:.3f},{r.y + th.corner_radius:.3f} '
                   f'V {r.y + hh:.3f} H {r.x:.3f} V {r.y + th.corner_radius:.3f} '
                   f'A {th.corner_radius},{th.corner_radius} 0 0 1 {r.x + th.corner_radius:.3f},{r.y:.3f} Z" '
                   f'fill="{stroke}" fill-opacity="0.10"/>')
        out.append(f'<line x1="{r.x + 0.4:.3f}" y1="{r.y + hh:.3f}" x2="{r.right - 0.4:.3f}" '
                   f'y2="{r.y + hh:.3f}" stroke="{stroke}" stroke-width="0.15" stroke-opacity="0.55"/>')

    if el.valign == "top":
        # 标题贴顶：box 作容器/子卡（内部再放其它元素）
        y = r.y + th.box_pad_y
    else:
        # 圆柱顶盖占位，文字整体略下移以视觉居中
        cy = r.cy + (_cyl_cap_ry(r) * 0.5 if el.shape == "cylinder" else 0.0)
        y = cy - content_h / 2

    if has_icon:
        icon_path = spec.resolve_asset(el.icon)
        slot = Rect(content_x0, y, inner_w, icon_h)
        out.append(_embed_image(icon_path, slot, "center", "middle", el.icon + "@" + el.id, res))
        y += icon_h + icon_gap

    text_out: list[str] = []
    text_spans_here: list[_TextSpan] = []
    blocks = [b for b in ((title_lines, title_pt, True), (body_lines, body_pt, False)) if b[0]]
    for bi, (lines, pt, bold) in enumerate(blocks):
        if bi > 0:
            y += 0.6  # title 与 body 间距（仅块间，与 content_h 估算一致）
        lh = pt * PT_TO_MM * LINE_HEIGHT
        for ln in lines:
            asc = line_ascent_mm(ln.text or "x", pt, bold)
            if el.align == "left":
                x = content_x0
            else:
                x = content_x0 + inner_w / 2 - ln.width_mm / 2
            span = _TextSpan(x=x, baseline=y + asc, text=ln.text, pt=pt, bold=bold,
                             color=el.text_color or v.text)
            res.text_spans.append(span)
            text_spans_here.append(span)
            text_out.append(span.to_svg())
            y += lh

    # 幽灵盒文字底板：title+body 合并一块板
    if ghost and _use_plate(el, res.base_mode) and text_spans_here:
        plate = _union_span_plate(text_spans_here, th)
        if plate is not None:
            res.text_plates.append((el.id, plate))
            out.append(_plate_svg(plate, th))
    out.extend(text_out)

    if has_sketch and sketch_h > 2.0:
        y += sketch_gap
        sk_rect = Rect(content_x0, y, inner_w, min(sketch_h, r.bottom - th.box_pad_y - y))
        if sk_rect.h > 2.0:
            res.sketch_rects.append((el.id, el.sketch, sk_rect))
            out.append(_draw_sketch(el.sketch, sk_rect, stroke, stroke,
                                    _stable_seed(el.id, el.sketch, r.x, r.y), th))

    return "".join(out)


# ---------------------------------------------------------------- asset

def _render_asset(el: AssetEl, spec: FigureSpec, th: Theme, fs: float, res: RenderResult) -> str:
    path = spec.resolve_asset(el.src)
    out = []
    ghost = _is_ghost(el, res.base_mode)
    cap_pt = th.size_caption * fs
    cap_h = 0.0
    cap_lines = []
    if el.caption:
        cap_lines = wrap_text(el.caption, cap_pt, el.rect.w)
        cap_h = text_block_height_mm(len(cap_lines), cap_pt) + 0.8

    img_rect = Rect(el.rect.x, el.rect.y, el.rect.w, el.rect.h - cap_h)
    if not ghost:
        if el.placeholder and not path.exists():
            # 意图性占位：真实实验结果（频谱图/照片/mesh 等）后续手动插入
            res.placeholder_assets.append(el.src)
            out.append(_placeholder_slot(img_rect, f"[{Path(el.src).stem}]", res))
        else:
            out.append(_embed_image(path, img_rect, el.halign, el.valign, el.src, res,
                                    frame=el.frame, theme=th))
    else:
        # 幽灵：不画图，仍记几何供锚点/避障
        res.asset_boxes[el.src] = img_rect

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

def _endpoint_node_id(ep: str | tuple[float, float]) -> str | None:
    if not isinstance(ep, str):
        return None
    return parse_anchor(ep)[0]


def _use_auto_label(el: ArrowEl) -> bool:
    """兼容性：仅 route:avoid 默认开启 auto 落标；显式 label_offset 则尊重手动。"""
    if not el.label:
        return False
    if el.label_offset_explicit:
        return False
    if el.label_pos == "auto":
        return True
    if el.label_pos is not None:
        return False
    return el.route == "avoid"


def _estimate_text_bbox(el: TextEl, fs: float) -> Rect:
    """独立 text 的粗包围盒（渲染前估算，供避障/落标）。"""
    pt = el.size * fs * (0.92 if el.smallcaps else 1.0)
    ls = 0.35 if el.smallcaps else 0.0
    raw = el.text.upper() if el.smallcaps else el.text
    lines = raw.split("\n") if not el.max_w else [
        ln.text for ln in wrap_text(raw, pt, el.max_w, el.bold)
    ]
    if not lines:
        lines = [""]
    widths = [
        measure_markup_mm(ln, pt, el.bold) + max(len(ln) - 1, 0) * ls
        for ln in lines
    ]
    tw = max(widths) if widths else 0.0
    lh = pt * PT_TO_MM * LINE_HEIGHT
    th = lh * len(lines)
    x, y = el.at
    if el.anchor == "start":
        lx = x
    elif el.anchor == "end":
        lx = x - tw
    else:
        lx = x - tw / 2
    asc = line_ascent_mm(lines[0] or "x", pt, el.bold)
    return Rect(lx, y, tw, max(th, asc))


def _collect_routing_obstacles(spec: FigureSpec, res: RenderResult
                               ) -> list[tuple[str, Rect]]:
    """路由障碍：节点视觉外边界 + group + sketch/accent 内容 + 独立 text。

    panel 为背景容器不计入。sketch/accent 用独立 id（owner@kind），即使端点盒
    整盒豁免，路径仍不可穿过其内容区。
    """
    out: list[tuple[str, Rect]] = []
    solid = (BoxEl, AssetEl, TokensEl, NetworkEl, ScatterEl, SketchEl, GroupEl)
    for el in spec.elements:
        if isinstance(el, solid):
            vr = res.node_visual_rects.get(el.id) or res.node_rects.get(el.id)
            if vr is not None:
                out.append((el.id, vr))
        elif isinstance(el, TextEl) and el.text.strip():
            out.append((el.id, _estimate_text_bbox(el, spec.font_scale)))
    for owner, kind, rect in res.sketch_rects:
        out.append((f"{owner}@{kind}", rect))
    return out


def _precompute_avoid_routes(
    arrows: list[ArrowEl],
    spec: FigureSpec,
    res: RenderResult,
) -> dict[str, list[tuple[float, float]]]:
    """批量 route:avoid；失败记 soft_issues 并让调用方降级 auto。"""
    visual = res.node_visual_rects or res.node_rects
    reqs: list[RouteRequest] = []
    for el in arrows:
        if el.route != "avoid" or el.style == "block":
            continue
        x1, y1, s1 = _anchor_point(
            el.from_, visual, res.node_rects, toward=_ref_center(el.to, res.node_rects))
        x2, y2, s2 = _anchor_point(
            el.to, visual, res.node_rects, toward=_ref_center(el.from_, res.node_rects))
        if _is_flush_pair(x1, y1, s1, x2, y2, s2):
            continue
        exclude = set()
        for ep in (el.from_, el.to):
            nid = _endpoint_node_id(ep)
            if nid:
                exclude.add(nid)
        reqs.append(RouteRequest(
            id=el.id, x1=x1, y1=y1, s1=s1, x2=x2, y2=y2, s2=s2,
            exclude_ids=exclude,
        ))
    if not reqs:
        return {}
    obstacles = _collect_routing_obstacles(spec, res)
    canvas = Rect(0, 0, spec.width, spec.height)
    results = route_all(reqs, obstacles, canvas)
    paths: dict[str, list[tuple[float, float]]] = {}
    for rid, rr in results.items():
        if rr.fallback:
            res.soft_issues.append(("W", "route-avoid-fallback", rr.message))
        else:
            paths[rid] = rr.points
    return paths


@dataclass
class _ArrowGeom:
    x1: float
    y1: float
    s1: str
    x2: float
    y2: float
    s2: str
    pts: list[tuple[float, float]]
    flush: bool = False
    arc_ctrl: tuple[float, float] | None = None
    skip: bool = False  # 退化无法绘制


def _resolve_arrow_geometry(
    el: ArrowEl,
    res: RenderResult,
    avoid_paths: dict[str, list[tuple[float, float]]],
) -> _ArrowGeom:
    """解析箭头折线（含 avoid 预计算结果 / 降级 auto），不绘制。"""
    visual = res.node_visual_rects or res.node_rects
    x1, y1, s1 = _anchor_point(
        el.from_, visual, res.node_rects, toward=_ref_center(el.to, res.node_rects))
    x2, y2, s2 = _anchor_point(
        el.to, visual, res.node_rects, toward=_ref_center(el.from_, res.node_rects))

    if el.style == "block":
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, [(x1, y1), (x2, y2)])

    flush = _is_flush_pair(x1, y1, s1, x2, y2, s2)
    arc_ctrl: tuple[float, float] | None = None

    if el.route == "avoid" and el.id in avoid_paths:
        pts = list(avoid_paths[el.id])
        # avoid 路径已正交且法向进出；仅钉 tip，不做 resnap（会破坏末段法向）
        if len(pts) >= 2:
            pts[0], pts[-1] = (x1, y1), (x2, y2)
            pts = _dedupe(pts)
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, pts, flush=False)

    if el.route == "arc" and not el.via:
        dx, dy = x2 - x1, y2 - y1
        chord = math.hypot(dx, dy)
        if chord < 1e-6:
            return _ArrowGeom(x1, y1, s1, x2, y2, s2, [(x1, y1)], skip=True)
        nx, ny = -dy / chord, dx / chord
        arc_ctrl = ((x1 + x2) / 2 + nx * el.bend * chord,
                    (y1 + y2) / 2 + ny * el.bend * chord)
        pts = []
        for i in range(9):
            t = i / 8
            mt = 1 - t
            pts.append((mt * mt * x1 + 2 * mt * t * arc_ctrl[0] + t * t * x2,
                        mt * mt * y1 + 2 * mt * t * arc_ctrl[1] + t * t * y2))
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, pts, arc_ctrl=arc_ctrl)

    if flush:
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, [(x1, y1), (x2, y2)], flush=True)

    # avoid 失败降级：忽略 via，走 auto（与 route:auto 相同）
    if el.route == "avoid":
        pts = _dedupe(_route_points(x1, y1, s1, x2, y2, s2, "auto"))
        if len(pts) >= 2:
            pts = _resnap_endpoints(pts, (x1, y1), (x2, y2), s1, s2)
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, pts)

    if el.via:
        pts = _adjust_via_ortho_end(_dedupe([(x1, y1), *el.via, (x2, y2)]), s2)
        if pts:
            pts = list(pts)
            pts[0], pts[-1] = (x1, y1), (x2, y2)
            pts = _dedupe(pts)
        return _ArrowGeom(x1, y1, s1, x2, y2, s2, pts)

    route = "straight" if el.route == "arc" else el.route
    if route == "straight":
        pts = _dedupe([(x1, y1), (x2, y2)])
    else:
        pts = _dedupe(_route_points(x1, y1, s1, x2, y2, s2, route))
        if len(pts) >= 2:
            pts = _resnap_endpoints(pts, (x1, y1), (x2, y2), s1, s2)
    return _ArrowGeom(x1, y1, s1, x2, y2, s2, pts)


def _shared_panel_bounds(
    el: ArrowEl, spec: FigureSpec, res: RenderResult,
) -> Rect | None:
    """两端点同属一个 panel 时返回该 panel.rect，否则 None。"""
    nids = [_endpoint_node_id(el.from_), _endpoint_node_id(el.to)]
    if not all(nids):
        return None
    panels = [p for p in spec.elements if isinstance(p, PanelEl)]
    if not panels:
        return None
    shared: PanelEl | None = None
    for nid in nids:
        nr = res.node_rects.get(nid) or res.node_visual_rects.get(nid)
        if nr is None:
            return None
        found: PanelEl | None = None
        for p in panels:
            if (p.rect.contains_point(nr.cx, nr.cy)
                    or (p.rect.x - 0.2 <= nr.x and p.rect.y - 0.2 <= nr.y
                        and p.rect.right + 0.2 >= nr.right
                        and p.rect.bottom + 0.2 >= nr.bottom)):
                found = p
                break
        if found is None:
            return None
        if shared is None:
            shared = found
        elif shared.id != found.id:
            return None
    return shared.rect if shared is not None else None


def _place_auto_arrow_labels(
    arrows: list[ArrowEl],
    geoms: list[_ArrowGeom],
    spec: FigureSpec,
    res: RenderResult,
    th: Theme,
    fs: float,
) -> dict[str, tuple["_TextSpan", Rect]]:
    """两阶段落标：全部路径确定后，按碰撞打分为 auto 标签选位。

    距离硬上限内选位；全硬拒时软冲突最小兜底并记 arrow-label-crowded。
    """
    need = [(el, g) for el, g in zip(arrows, geoms)
            if _use_auto_label(el) and len(g.pts) >= 2 and not g.skip]
    if not need:
        return {}

    boxes: list[Rect] = []
    for el in spec.elements:
        if isinstance(el, (BoxEl, AssetEl, TokensEl, NetworkEl, ScatterEl, SketchEl)):
            vr = res.node_visual_rects.get(el.id) or res.node_rects.get(el.id)
            if vr is not None:
                boxes.append(vr)
    # sketch / accent 内容区：落标硬障（与整盒不同，端点盒内也拒）
    content_obstacles = [rect for _, _, rect in res.sketch_rects]
    title_bands = [band for _, band in getattr(res, "panel_title_bands", []) or []]
    # 已渲染的盒子标题/正文 + 独立 text，都作为落标障碍
    texts: list[Rect] = []
    for sp in res.text_spans:
        if sp.text.strip() and not sp.diagnostic:
            texts.append(sp.bbox())
    for el in spec.elements:
        if isinstance(el, TextEl) and el.text.strip():
            texts.append(_estimate_text_bbox(el, fs))

    # 其它箭头线段（含非 auto 标签箭头）
    all_segs: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for el, g in zip(arrows, geoms):
        if len(g.pts) >= 2:
            all_segs[el.id] = list(zip(g.pts, g.pts[1:]))

    # 画布内边距：标签不得越出
    margin = 0.8
    canvas_bounds = Rect(margin, margin,
                         max(spec.width - 2 * margin, 0.1),
                         max(spec.height - 2 * margin, 0.1))

    placed: dict[str, tuple[_TextSpan, Rect]] = {}
    other_caps: list[Rect] = []

    for el, g in need:
        pt_size = th.size_arrow_label * fs
        w = measure_markup_mm(el.label, pt_size)
        asc = line_ascent_mm(el.label, pt_size)
        h = pt_size * PT_TO_MM * LINE_HEIGHT
        _, _, arrow_width = _resolve_arrow_paint(el, th)
        weight_mul = {"thin": 0.65, "normal": 1.0, "heavy": 1.55}.get(el.weight, 1.0)
        lw = arrow_width if arrow_width is not None else th.lw_arrow * weight_mul
        scale = max(1.0, (lw / th.lw_arrow) ** 0.75)
        head_len = th.arrow_head_len * scale
        other_segs = []
        for aid, segs in all_segs.items():
            if aid != el.id:
                other_segs.extend(segs)
        endpoint_boxes: list[Rect] = []
        for ep in (el.from_, el.to):
            nid = _endpoint_node_id(ep)
            if nid:
                vr = res.node_visual_rects.get(nid) or res.node_rects.get(nid)
                if vr is not None:
                    endpoint_boxes.append(vr)
        # 两端点同 panel → 标签不得跑出该 panel
        panel_r = _shared_panel_bounds(el, spec, res)
        if panel_r is not None:
            # 与画布取交，并略内缩避开描边
            ix = max(canvas_bounds.x, panel_r.x + 0.4)
            iy = max(canvas_bounds.y, panel_r.y + 0.4)
            ir = min(canvas_bounds.right, panel_r.right - 0.4)
            ib = min(canvas_bounds.bottom, panel_r.bottom - 0.4)
            bounds = Rect(ix, iy, max(ir - ix, 0.1), max(ib - iy, 0.1))
        else:
            bounds = canvas_bounds
        hard_lim = max_label_path_dist(h)
        best = pick_best_label(
            g.pts, w, h, asc, boxes, texts, other_segs, other_caps,
            head_keep=max(head_len + 1.2, 2.8),
            endpoint_boxes=endpoint_boxes,
            content_obstacles=content_obstacles,
            title_bands=title_bands,
            bounds=bounds,
            max_path_dist=hard_lim,
        )
        if best is None:
            continue
        if best.crowded:
            res.soft_issues.append((
                "W", "arrow-label-crowded",
                f"箭头 '{el.id}' 标签 “{el.label[:14]}” 距离上限内候选均冲突，"
                f"已折中放置；请拉开箭头间距或删除标签",
            ))
        span = _TextSpan(x=best.x, baseline=best.baseline, text=el.label,
                         pt=pt_size, bold=False, color=th.muted)
        placed[el.id] = (span, best.cap)
        other_caps.append(best.cap)
    return placed


def _anchor_point(ep: str | tuple[float, float],
                  visual_rects: dict[str, Rect],
                  logical_rects: dict[str, Rect] | None = None,
                  toward: tuple[float, float] | None = None,
                  ) -> tuple[float, float, str]:
    """返回 (x, y, side)。锚在视觉外边界；t 比例仍按逻辑 rect 边长。

    ep 为裸节点 id（未写 .side）时按 `toward`（对方端点参考点）相对逻辑中心
    自动选朝向对方的边，落在该边中点——调用方只写 `from: enc, to: dec` 即可。
    """
    if not isinstance(ep, str):
        return ep[0], ep[1], "free"
    node, side, t = parse_anchor(ep)
    vr = visual_rects[node]
    lr = (logical_rects or visual_rects)[node]
    if side is None:
        tx, ty = toward if toward is not None else (lr.cx, lr.cy)
        dx, dy = tx - lr.cx, ty - lr.cy
        if abs(dx) >= abs(dy):
            side = "right" if dx >= 0 else "left"
        else:
            side = "bottom" if dy >= 0 else "top"
        t = 0.5
    if side == "left":
        return vr.x, lr.y + t * lr.h, "left"
    if side == "right":
        return vr.right, lr.y + t * lr.h, "right"
    if side == "top":
        return lr.x + t * lr.w, vr.y, "top"
    if side == "bottom":
        return lr.x + t * lr.w, vr.bottom, "bottom"
    return lr.cx, lr.cy, "center"


def _side_outward(side: str) -> tuple[float, float]:
    return {
        "left": (-1.0, 0.0), "right": (1.0, 0.0),
        "top": (0.0, -1.0), "bottom": (0.0, 1.0),
    }.get(side, (0.0, 0.0))


def _resolve_route(s1: str, s2: str, x1: float, y1: float, x2: float, y2: float,
                   route: str) -> str:
    if route != "auto":
        return route
    horiz = {"left", "right"}
    vert = {"top", "bottom"}
    if s1 in horiz and s2 in horiz:
        return "straight" if abs(y1 - y2) < 0.5 else "z"
    if s1 in vert and s2 in vert:
        return "straight" if abs(x1 - x2) < 0.5 else "zv"
    if s1 in horiz and s2 in vert:
        return "hv"
    if s1 in vert and s2 in horiz:
        return "vh"
    # free/center：仍走正交折线（避免 auto 退化成斜线）
    if s1 in horiz or s2 in horiz:
        return "straight" if abs(y1 - y2) < 0.5 else "z"
    if s1 in vert or s2 in vert:
        return "straight" if abs(x1 - x2) < 0.5 else "zv"
    if abs(y1 - y2) < 0.5 or abs(x1 - x2) < 0.5:
        return "straight"
    return "z"


def _route_points_raw(x1: float, y1: float, x2: float, y2: float,
                      route: str) -> list[tuple[float, float]]:
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


def _approach_point(tip: tuple[float, float], side: str, min_len: float) -> tuple[float, float]:
    """锚定边外侧、用于垂直进入/离开的拐点。"""
    ox, oy = _side_outward(side)
    return tip[0] + ox * min_len, tip[1] + oy * min_len


def _fit_stub_len(side: str, tip: tuple[float, float], other: tuple[float, float],
                  min_len: float) -> float:
    """法向 stub 长度：优先 min_len；两端过近时对称缩小，极近则 0。"""
    if side not in ("left", "right", "top", "bottom"):
        return 0.0
    if side in ("left", "right"):
        avail = abs(other[0] - tip[0])
    else:
        avail = abs(other[1] - tip[1])
    if avail < 1.0:
        return 0.0
    # 两侧对称预留，中间至少 1mm 走廊，单侧不超过 min_len
    return min(min_len, max(0.0, (avail - 1.0) / 2.0))


def _is_flush_pair(x1: float, y1: float, s1: str, x2: float, y2: float, s2: str) -> bool:
    """相对边锚点在法向上间距 <0.5mm（stack 外缘贴邻盒）。"""
    if s1 == "right" and s2 == "left" and abs(x1 - x2) < 0.5:
        return True
    if s1 == "left" and s2 == "right" and abs(x1 - x2) < 0.5:
        return True
    if s1 == "bottom" and s2 == "top" and abs(y1 - y2) < 0.5:
        return True
    if s1 == "top" and s2 == "bottom" and abs(y1 - y2) < 0.5:
        return True
    return False


def _last_segment_parallel_to_side(a: tuple[float, float], b: tuple[float, float],
                                   side: str) -> bool:
    """末段是否平行于锚定边（贴边滑行）。对角斜线不算。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if side in ("left", "right"):
        return abs(dx) <= 1e-6 and abs(dy) > 1e-6  # 竖直 = 平行于左右边
    if side in ("top", "bottom"):
        return abs(dy) <= 1e-6 and abs(dx) > 1e-6  # 水平 = 平行于上下边
    return False


def _adjust_via_ortho_end(pts: list[tuple[float, float]], s2: str,
                          min_len: float = _MIN_APPROACH_MM) -> list[tuple[float, float]]:
    """via 路径：末段改为垂直进入目标边，并保证 tip 仍是视觉锚点。

    - 末段平行于锚定边（贴边滑行）→ 扳最后一个 via，使末段做法向进入
    - 末段已是斜线 → 不改路（保留用户 via 意图；lint 报 arrow-approach）
    - 末段已正交但 tip 微偏 → 仅把 tip 坐标钉回（由调用方传入的 tip）
    """
    if len(pts) < 3 or s2 not in ("left", "right", "top", "bottom"):
        return pts
    tip = pts[-1]
    prev = pts[-2]
    if not _last_segment_parallel_to_side(prev, tip, s2):
        return pts
    if s2 in ("left", "right"):
        # 保持 via 的 x，把 y 扳到与终点齐平 → 末段水平；tip 不变
        nx, ny = prev[0], tip[1]
        ox, _ = _side_outward(s2)
        if (nx - tip[0]) * ox < min_len - 1e-6:
            nx = tip[0] + ox * min_len
        return pts[:-2] + [(nx, ny), tip]
    nx, ny = tip[0], prev[1]
    _, oy = _side_outward(s2)
    if (ny - tip[1]) * oy < min_len - 1e-6:
        ny = tip[1] + oy * min_len
    return pts[:-2] + [(nx, ny), tip]


def _resnap_endpoints(pts: list[tuple[float, float]],
                      p1: tuple[float, float], p2: tuple[float, float],
                      s1: str = "free", s2: str = "free",
                      tol: float = 0.08) -> list[tuple[float, float]]:
    """路径改写后强制首尾回到视觉锚点。

    仅当邻段已接近轴对齐时，才把相邻拐点扳齐（吸收微漂）；
    真斜线 via 原样保留，避免把用户故意的斜线改成正交。
    """
    if len(pts) < 2:
        return pts
    out = list(pts)
    out[0] = p1
    out[-1] = p2
    # 单段微斜：沿边滑动使轴对齐（真斜线两向都 >tol 则保留）
    if len(out) == 2:
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        if abs(dx) > tol and abs(dy) > tol:
            return _dedupe(out)
        if abs(dy) <= abs(dx):
            if s2 in ("left", "right"):
                out[0] = (p1[0], p2[1])
            elif s1 in ("left", "right"):
                out[-1] = (p2[0], p1[1])
            else:
                out[0] = (p1[0], p2[1])
        else:
            if s2 in ("top", "bottom"):
                out[0] = (p2[0], p1[1])
            elif s1 in ("top", "bottom"):
                out[-1] = (p1[0], p2[1])
            else:
                out[0] = (p2[0], p1[1])
        return _dedupe(out)
    prev = out[-2]
    dx, dy = p2[0] - prev[0], p2[1] - prev[1]
    if abs(dy) <= tol and abs(dx) > tol and s2 in ("left", "right", "free"):
        out[-2] = (prev[0], p2[1])
    elif abs(dx) <= tol and abs(dy) > tol and s2 in ("top", "bottom", "free"):
        out[-2] = (p2[0], prev[1])
    nxt = out[1]
    dx, dy = nxt[0] - p1[0], nxt[1] - p1[1]
    if abs(dy) <= tol and abs(dx) > tol:
        out[1] = (nxt[0], p1[1])
    elif abs(dx) <= tol and abs(dy) > tol:
        out[1] = (p1[0], nxt[1])
    return _dedupe(out)


def _arrow_label_layout(
    pts: list[tuple[float, float]],
    label: str,
    label_offset: float,
    pt_size: float,
    head_len: float,
    color: str,
) -> tuple["_TextSpan", Rect]:
    """在折线上放置标签，避开箭头尖端/起点保护区（防止胶囊盖住尖端造成悬空错觉）。"""
    tip = pts[-1]
    start = pts[0]
    nseg = len(pts) - 1
    scored: list[tuple[float, int]] = []
    for i in range(nseg):
        a, b = pts[i], pts[i + 1]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        d_tip = math.hypot(mx - tip[0], my - tip[1])
        penalty = 0.0
        if i == nseg - 1 and nseg >= 2:
            penalty += 12.0  # 绝不优先放在末段进入 stub 上
        if i == 0 and nseg >= 3:
            penalty += 2.0
        scored.append((length + 0.4 * min(d_tip, 14.0) - penalty, i))
    scored.sort(reverse=True)

    w = measure_markup_mm(label, pt_size)
    asc = line_ascent_mm(label, pt_size)
    keep = max(head_len + 1.2, 2.8)

    def _try(i: int, offset: float) -> tuple[_TextSpan, Rect] | None:
        a, b = pts[i], pts[i + 1]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        horiz = abs(b[0] - a[0]) >= abs(b[1] - a[1])
        if horiz:
            tx, ty = mx - w / 2, my - offset
        else:
            tx = mx + offset
            ty = my + asc / 2 - 0.2
        span = _TextSpan(x=tx, baseline=ty, text=label, pt=pt_size, bold=False, color=color)
        bb = span.bbox()
        cap = Rect(bb.x - 0.7, bb.y - 0.25, bb.w + 1.4, bb.h + 0.5)
        # 尖端落入胶囊 → 拒绝（这正是 8 sents「悬空」的根因）
        if cap.x - 0.05 <= tip[0] <= cap.right + 0.05 and cap.y - 0.05 <= tip[1] <= cap.bottom + 0.05:
            return None
        # 胶囊中心离 tip 过近也拒
        cx, cy = cap.x + cap.w / 2, cap.y + cap.h / 2
        if math.hypot(cx - tip[0], cy - tip[1]) < keep * 0.55:
            return None
        return span, cap

    offsets = [label_offset, -label_offset,
               abs(label_offset) + 1.2, -(abs(label_offset) + 1.2)]
    for _, i in scored:
        for off in offsets:
            got = _try(i, off)
            if got is not None:
                return got
    # 兜底：最长段 + 原 offset（与旧行为一致）
    best_i = scored[0][1] if scored else 0
    a, b = pts[best_i], pts[best_i + 1]
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    horiz = abs(b[0] - a[0]) >= abs(b[1] - a[1])
    if horiz:
        tx, ty = mx - w / 2, my - label_offset
    else:
        tx, ty = mx + label_offset, my + asc / 2 - 0.2
    span = _TextSpan(x=tx, baseline=ty, text=label, pt=pt_size, bold=False, color=color)
    bb = span.bbox()
    return span, Rect(bb.x - 0.7, bb.y - 0.25, bb.w + 1.4, bb.h + 0.5)


def _segments_axis_aligned(pts: list[tuple[float, float]], tol: float = 1e-6) -> bool:
    """折线是否全部由水平/垂直段组成（无斜线）。"""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if abs(x0 - x1) > tol and abs(y0 - y1) > tol:
            return False
    return True


def _snap_axis_segments(pts: list[tuple[float, float]], tol: float = 0.05
                        ) -> list[tuple[float, float]]:
    """消除浮点/锚点取样造成的亚毫米微斜线，强制轴对齐（不改动真斜线）。

    端点保留精确锚点坐标；微偏差通过沿锚定边滑动起点/拐点吸收，
    避免留下 <tol 的退化残段（否则会触发 arrow-approach 误报）。
    """
    if len(pts) < 2:
        return pts
    start, end = pts[0], pts[-1]
    # 单段：微斜 → 滑到轴对齐，端点取精确 end
    if len(pts) == 2:
        dx, dy = end[0] - start[0], end[1] - start[1]
        if abs(dx) > tol and abs(dy) > tol:
            return [(start[0], start[1]), end]  # 真斜线
        if abs(dy) <= tol:
            return _dedupe([(start[0], end[1]), end])  # 水平，起点沿边滑动
        if abs(dx) <= tol:
            return _dedupe([(end[0], start[1]), end])  # 垂直
        return _dedupe([(start[0], start[1]), end])

    out: list[tuple[float, float]] = [start]
    for p in pts[1:-1]:
        prev = out[-1]
        dx, dy = p[0] - prev[0], p[1] - prev[1]
        if abs(dx) > tol and abs(dy) > tol:
            out.append(p)
        elif abs(dy) <= tol and abs(dx) > tol:
            out.append((p[0], prev[1]))
        elif abs(dx) <= tol and abs(dy) > tol:
            out.append((prev[0], p[1]))

    prev = out[-1]
    dx, dy = end[0] - prev[0], end[1] - prev[1]
    if abs(dx) > tol and abs(dy) > tol:
        # 真需要拐点
        if abs(dx) >= abs(dy):
            out.append((end[0], prev[1]))
        else:
            out.append((prev[0], end[1]))
        out.append(end)
    elif abs(dy) <= tol:
        # 水平进入：把 prev 的 y 扳到 end.y，避免微竖残段
        out[-1] = (prev[0], end[1])
        out.append(end)
    elif abs(dx) <= tol:
        out[-1] = (end[0], prev[1])
        out.append(end)
    else:
        out.append(end)
    return _dedupe(out)


def _ortho_route(x1: float, y1: float, s1: str, x2: float, y2: float, s2: str,
                 connector: str, min_len: float = _MIN_APPROACH_MM
                 ) -> list[tuple[float, float]]:
    """构造整条正交折线：首段沿出发边法向离开、末段沿到达边法向进入，
    中间用 hv/vh/z/zv 消化落差——绝不出现斜线段。
    """
    slen = _fit_stub_len(s1, (x1, y1), (x2, y2), min_len)
    elen = _fit_stub_len(s2, (x2, y2), (x1, y1), min_len)

    ax, ay = (x1, y1) if slen <= 1e-6 else _approach_point((x1, y1), s1, slen)
    bx, by = (x2, y2) if elen <= 1e-6 else _approach_point((x2, y2), s2, elen)

    conn = connector if connector in ("hv", "vh", "z", "zv") else "z"
    if abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6:
        mid: list[tuple[float, float]] = [(ax, ay)]
    elif abs(ay - by) < 1e-6 or abs(ax - bx) < 1e-6:
        mid = [(ax, ay), (bx, by)]
    else:
        mid = _route_points_raw(ax, ay, bx, by, conn)

    pts: list[tuple[float, float]] = [(x1, y1)]
    pts.extend(mid)
    if elen > 1e-6:
        pts.append((x2, y2))
    elif abs(pts[-1][0] - x2) > 1e-6 or abs(pts[-1][1] - y2) > 1e-6:
        pts.append((x2, y2))
    return _snap_axis_segments(_dedupe(pts))


def _route_points(x1: float, y1: float, s1: str, x2: float, y2: float, s2: str,
                  route: str) -> list[tuple[float, float]]:
    """生成折线；auto/hv/vh/z/zv 整条正交（straight 保持用户斜线意图）。"""
    resolved = _resolve_route(s1, s2, x1, y1, x2, y2, route)
    if resolved == "straight":
        # auto 近对齐走 straight：微斜线收成正交；显式 straight 保留斜线
        pts = _dedupe([(x1, y1), (x2, y2)])
        if route != "straight":
            return _snap_axis_segments(pts)
        return pts
    return _ortho_route(x1, y1, s1, x2, y2, s2, resolved)


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


def _resolve_arrow_paint(el: ArrowEl, th: Theme) -> tuple[str, str, float | None]:
    """解析箭头 style/color/width：semantic 预设 + 显式字段覆盖。"""
    style = el.style
    color = el.color
    width = el.width
    if el.semantic and th.arrow_styles:
        preset = th.arrow_styles.get(el.semantic) or {}
        if not el.style_explicit and preset.get("style"):
            style = str(preset["style"])
        if not el.color_explicit and preset.get("color"):
            color = str(preset["color"])
        if not el.width_explicit and preset.get("width") is not None:
            width = float(preset["width"])
    return style, (color or th.arrow), width


def _render_arrow(
    el: ArrowEl,
    th: Theme,
    fs: float,
    res: RenderResult,
    precomputed: _ArrowGeom | None = None,
    precomputed_label: tuple["_TextSpan", Rect] | None = None,
) -> str:
    style, color, arrow_width = _resolve_arrow_paint(el, th)
    if precomputed is None:
        precomputed = _resolve_arrow_geometry(el, res, {})
    x1, y1, s1 = precomputed.x1, precomputed.y1, precomputed.s1
    x2, y2, s2 = precomputed.x2, precomputed.y2, precomputed.s2
    pts = list(precomputed.pts)
    flush = precomputed.flush
    arc_ctrl = precomputed.arc_ctrl

    if style == "block":
        return _render_block_arrow(el, (x1, y1), (x2, y2), color, th, fs, res)

    if precomputed.skip or len(pts) < 2:
        res.arrow_segments.append((el.id, pts))
        res.arrow_ends.append((el.id, s1, s2))
        return ""

    out = []
    weight_mul = {"thin": 0.65, "normal": 1.0, "heavy": 1.55}.get(el.weight, 1.0)
    lw = arrow_width if arrow_width is not None else th.lw_arrow * weight_mul
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

    if style == "dashed":
        dash = ' stroke-dasharray="1.6,1.1"'
    elif style == "dotted":
        dash = ' stroke-dasharray="0.35,0.95"'
    else:
        dash = ""

    if flush and arc_ctrl is None:
        # 贴齐接触线：画平均位置上的短杆，头部分别法向扎入两侧视觉边
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        stub = max(head_len * 0.9, 1.0)
        if s1 in ("left", "right"):
            shaft = [(mx - stub, my), (mx + stub, my)]
        else:
            shaft = [(mx, my - stub), (mx, my + stub)]
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in shaft)
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
                   f'{dash} stroke-linejoin="round" stroke-linecap="round"/>')
        if el.head == "arrow":
            # 目标侧：头指向 tip2（微伸入视觉边），方向为进入该侧的法向
            t2 = _tip_inward((x2, y2), s2)
            if s2 == "left":
                out.append(_arrow_head((t2[0] - 1, t2[1]), t2, color, th, head_len, head_w))
            elif s2 == "right":
                out.append(_arrow_head((t2[0] + 1, t2[1]), t2, color, th, head_len, head_w))
            elif s2 == "top":
                out.append(_arrow_head((t2[0], t2[1] - 1), t2, color, th, head_len, head_w))
            elif s2 == "bottom":
                out.append(_arrow_head((t2[0], t2[1] + 1), t2, color, th, head_len, head_w))
            if el.bidir:
                t1 = _tip_inward((x1, y1), s1)
                if s1 == "right":
                    out.append(_arrow_head((t1[0] + 1, t1[1]), t1, color, th, head_len, head_w))
                elif s1 == "left":
                    out.append(_arrow_head((t1[0] - 1, t1[1]), t1, color, th, head_len, head_w))
                elif s1 == "bottom":
                    out.append(_arrow_head((t1[0], t1[1] + 1), t1, color, th, head_len, head_w))
                elif s1 == "top":
                    out.append(_arrow_head((t1[0], t1[1] - 1), t1, color, th, head_len, head_w))
    elif arc_ctrl is not None:
        tip2 = _tip_inward((x2, y2), s2) if el.head == "arrow" else (x2, y2)
        tip1 = _tip_inward((x1, y1), s1) if (el.head == "arrow" and el.bidir) else (x1, y1)
        end = _shorten(arc_ctrl, tip2, head_len * 0.72) if el.head == "arrow" else tip2
        start = (_shorten(arc_ctrl, tip1, head_len * 0.72)
                 if el.head == "arrow" and el.bidir else tip1)
        d = (f"M {start[0]:.3f},{start[1]:.3f} "
             f"Q {arc_ctrl[0]:.3f},{arc_ctrl[1]:.3f} {end[0]:.3f},{end[1]:.3f}")
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
                   f'{dash} stroke-linejoin="round" stroke-linecap="round"/>')
        if el.head == "arrow":
            out.append(_arrow_head(arc_ctrl, tip2, color, th, head_len, head_w))
            if el.bidir:
                out.append(_arrow_head(arc_ctrl, tip1, color, th, head_len, head_w))
    else:
        tip2 = _tip_inward(pts[-1], s2) if el.head == "arrow" else pts[-1]
        tip1 = _tip_inward(pts[0], s1) if (el.head == "arrow" and el.bidir) else pts[0]
        draw_pts = list(pts)
        if el.head == "arrow":
            draw_pts[-1] = _shorten(draw_pts[-2], tip2, head_len * 0.72)
            if el.bidir:
                draw_pts[0] = _shorten(draw_pts[1], tip1, head_len * 0.72)
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in draw_pts)
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
                   f'{dash} stroke-linejoin="round" stroke-linecap="round"/>')
        if el.head == "arrow":
            # head fill 必须与杆 stroke 同色；尖端微伸入视觉边
            out.append(_arrow_head(pts[-2], tip2, color, th, head_len, head_w))
            if el.bidir:
                out.append(_arrow_head(pts[1], tip1, color, th, head_len, head_w))

    res.arrow_segments.append((el.id, pts))
    res.arrow_ends.append((el.id, s1, s2))

    if el.label:
        pt_size = th.size_arrow_label * fs
        if precomputed_label is not None:
            span, cap = precomputed_label
        else:
            span, cap = _arrow_label_layout(
                pts, el.label, el.label_offset, pt_size, head_len, th.muted)
        if el.label_bg:
            out.append(
                f'<rect x="{cap.x:.3f}" y="{cap.y:.3f}" width="{cap.w:.3f}" '
                f'height="{cap.h:.3f}" rx="{cap.h * 0.45:.3f}" '
                f'fill="#FFFFFF" fill-opacity="0.92" stroke="#E8E8E8" stroke-width="0.08"/>')
        res.arrow_label_boxes.append((el.id, cap, el.label))
        res.text_spans.append(span)
        out.append(span.to_svg())

    return "".join(out)


def _tip_inward(tip: tuple[float, float], side: str, depth: float = 0.22
                ) -> tuple[float, float]:
    """尖端沿锚定边内法向微伸，消除 AA/描边造成的视觉悬空（≤ arrow-gap 压入容差）。"""
    if depth <= 0 or side not in ("left", "right", "top", "bottom"):
        return tip
    ox, oy = _side_outward(side)
    return tip[0] - ox * depth, tip[1] - oy * depth


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
    # fill/stroke 均用杆色，避免渲染器对无描边 polygon 取继承色
    return (f'<polygon points="{b[0]:.3f},{b[1]:.3f} {p1[0]:.3f},{p1[1]:.3f} {p2[0]:.3f},{p2[1]:.3f}" '
            f'fill="{color}" stroke="{color}" stroke-width="0.01"/>')


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
        x0 = min(rr.x for rr in rects) - el.pad
        y0 = min(rr.y for rr in rects) - el.pad
        x1 = max(rr.right for rr in rects) + el.pad
        y1 = max(rr.bottom for rr in rects) + el.pad
        r = Rect(x0, y0, x1 - x0, y1 - y0)

    dash = ' stroke-dasharray="2.2,1.4"' if el.style == "dashed" else ""
    fill = el.fill if el.fill is not None else th.group_fill
    stroke = el.color or th.group_stroke
    lw = el.lw if el.lw is not None else th.lw_group
    cr = th.corner_radius + 0.6
    out = []
    if _use_shadow(el.shadow, th):
        out.append(_soft_shadow_svg(r, cr))
    if el.hatch:
        pid = f"hatch_{el.id}"
        out.append(_hatch_pattern_svg(pid, stroke))
        # 底色（若 fill 为 none 则用极浅灰）+ hatch 叠层
        base_fill = fill if fill and fill != "none" else "#F7F7F7"
        out.append(f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
                   f'rx="{cr}" fill="{base_fill}" stroke="none"/>')
        out.append(f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
                   f'rx="{cr}" fill="url(#{pid})" stroke="{stroke}" stroke-width="{lw}"{dash}/>')
    else:
        out.append(f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
                   f'rx="{cr}" fill="{fill}" stroke="{stroke}" '
                   f'stroke-width="{lw}"{dash}/>')
    res.node_rects[el.id] = r
    res.node_visual_rects[el.id] = r

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
    raw = el.text.upper() if el.smallcaps else el.text
    ls = 0.35 if el.smallcaps else 0.0
    # smallcaps 时用略小字号
    if el.smallcaps:
        pt = pt * 0.92
    lines = wrap_text(raw, pt, el.max_w, el.bold) if el.max_w else [
        ln for ln in (type("L", (), {"text": t, "width_mm": measure_markup_mm(t, pt, el.bold)
                                     + (max(len(t) - 1, 0) * ls if ls else 0)})()
                      for t in raw.split("\n"))
    ]
    lh = pt * PT_TO_MM * LINE_HEIGHT
    cy = y
    text_spans_here: list[_TextSpan] = []
    text_out: list[str] = []
    for ln in lines:
        tw = ln.width_mm
        if el.anchor == "start":
            lx = x
        elif el.anchor == "end":
            lx = x - tw
        else:
            lx = x - tw / 2
        asc = line_ascent_mm(ln.text or "x", pt, el.bold)
        span = _TextSpan(x=lx, baseline=cy + asc, text=ln.text, pt=pt, bold=el.bold,
                         color=color, italic=el.italic,
                         rotate=el.rotate, rot_cx=x, rot_cy=y,
                         smallcaps=el.smallcaps, letter_spacing=ls)
        res.text_spans.append(span)
        text_spans_here.append(span)
        text_out.append(span.to_svg())
        cy += lh
    if _use_plate(el, res.base_mode) and text_spans_here:
        plate = _union_span_plate(text_spans_here, th)
        if plate is not None:
            res.text_plates.append((el.id, plate))
            out.append(_plate_svg(plate, th))
    out.extend(text_out)
    return "".join(out)


def _render_panel_label(el: PanelLabelEl, th: Theme, fs: float, res: RenderResult) -> str:
    text = el.text
    pt = th.size_panel_label * fs
    case = (th.panel_case or "ml").lower()
    if case == "lower":
        # Nature：小写 a/b/c，8 pt bold
        text = text.strip().strip("()").rstrip(").").lower()
        pt = 8.0 * fs
    elif case == "upper":
        # Science：大写 A/B/C，9 pt bold
        text = text.strip().strip("()").rstrip(").").upper()
        pt = 9.0 * fs
    x, y = el.at
    asc = line_ascent_mm(text, pt, True)
    span = _TextSpan(x=x, baseline=y + asc, text=text, pt=pt, bold=True, color=th.ink)
    res.text_spans.append(span)
    return span.to_svg()


# ---------------------------------------------------------------- panel / tokens / marker

def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _panel_title_band_rect(el: PanelEl, th: Theme, fs: float) -> Rect | None:
    """panel 标题文字带（标题行 + 分隔线 / banner 色条），供落标硬拒与 lint。"""
    if not el.title:
        return None
    r = el.rect
    if el.header_style == "smallcaps":
        label = el.title.upper()
        pt = (el.title_size or th.size_caption) * fs * 0.95
        asc = line_ascent_mm(label, pt, bold=True)
        # 与绘制一致：顶 pad + 字高 + 线下方余量
        band_h = min(r.h, 2.2 + asc + 1.4 + 1.0)
        return Rect(r.x, r.y, r.w, band_h)
    hh = min(el.header_h, r.h * 0.45)
    return Rect(r.x, r.y, r.w, hh)


def _render_panel(el: PanelEl, th: Theme, fs: float, res: RenderResult) -> str:
    v = th.variants.get(el.variant)
    if v is None:
        raise ValueError(f"panel '{el.id}': 未知 variant '{el.variant}'（可选 {list(th.variants)}）")
    r = el.rect
    header_fill = el.header_fill or v.stroke
    body_fill = el.fill or v.fill
    cr = th.corner_radius + 0.4
    lw = _variant_lw(v, th)
    ghost = _is_ghost(el, res.base_mode)
    out = []
    if (not ghost) and _use_shadow(el.shadow, th):
        out.append(_soft_shadow_svg(r, cr))

    band = _panel_title_band_rect(el, th, fs)
    if band is not None:
        res.panel_title_bands.append((el.id, band))

    if ghost:
        # base 模式默认：只画标题文字，不画底色/边框；文字落底稿上则垫板
        if el.title:
            if el.header_style == "smallcaps":
                label = el.title.upper()
                pt = (el.title_size or th.size_caption) * fs * 0.95
                ls = 0.45
                w = measure_markup_mm(label, pt, bold=True) + max(len(label) - 1, 0) * ls
                asc = line_ascent_mm(label, pt, bold=True)
                lx = r.x + 2.5
                baseline = r.y + 2.2 + asc
                span = _TextSpan(x=lx, baseline=baseline, text=label, pt=pt, bold=True,
                                 color=header_fill, letter_spacing=ls, smallcaps=True)
            else:
                pt = (el.title_size or th.size_title) * fs
                w = measure_markup_mm(el.title, pt, bold=True)
                asc = line_ascent_mm(el.title, pt, bold=True)
                hh = min(el.header_h, r.h * 0.45)
                baseline = r.y + hh / 2 + asc / 2 - 0.3
                span = _TextSpan(x=r.cx - w / 2, baseline=baseline, text=el.title,
                                 pt=pt, bold=True, color=th.ink)
            res.text_spans.append(span)
            if _use_plate(el, res.base_mode):
                plate = _union_span_plate([span], th)
                if plate is not None:
                    res.text_plates.append((el.id, plate))
                    out.append(_plate_svg(plate, th))
            out.append(span.to_svg())
        return "".join(out)

    if el.header_style == "smallcaps":
        # 顶会克制风：无色条 banner，改为 small-caps 标签 + 细灰分隔线
        border = th.group_stroke if th.group_stroke != "none" else "#CCCCCC"
        out.append(
            f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
            f'rx="{cr}" fill="{body_fill}" stroke="{border}" stroke-width="{lw * 0.85:.3f}"/>')
        if el.title:
            label = el.title.upper()
            pt = (el.title_size or th.size_caption) * fs * 0.95
            ls = 0.45
            w = measure_markup_mm(label, pt, bold=True) + max(len(label) - 1, 0) * ls
            asc = line_ascent_mm(label, pt, bold=True)
            # 标签靠左上，下方细灰线
            lx = r.x + 2.5
            baseline = r.y + 2.2 + asc
            span = _TextSpan(x=lx, baseline=baseline, text=label, pt=pt, bold=True,
                             color=header_fill, letter_spacing=ls, smallcaps=True)
            res.text_spans.append(span)
            out.append(span.to_svg())
            ly = r.y + 2.2 + asc + 1.4
            out.append(f'<line x1="{r.x + 2.0:.3f}" y1="{ly:.3f}" x2="{r.right - 2.0:.3f}" '
                       f'y2="{ly:.3f}" stroke="{border}" stroke-width="0.18"/>')
        return "".join(out)

    hh = min(el.header_h, r.h * 0.45)
    out += [
        # 面板体
        f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
        f'rx="{cr}" fill="{body_fill}" stroke="{header_fill}" stroke-width="{lw}"/>',
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


def _render_theme_grid(spec: FigureSpec, th: Theme) -> str:
    """主题浅网格底（isosystem 晒图风）；无坐标标注。"""
    step = max(th.grid_step, 1.0)
    color = th.grid_color or "#D0D7E2"
    lw = th.grid_lw
    out = ['<g data-theme-grid="1">']
    x = 0.0
    while x <= spec.width + 1e-6:
        out.append(f'<line x1="{x:.3f}" y1="0" x2="{x:.3f}" y2="{spec.height}" '
                   f'stroke="{color}" stroke-width="{lw}"/>')
        x += step
    y = 0.0
    while y <= spec.height + 1e-6:
        out.append(f'<line x1="0" y1="{y:.3f}" x2="{spec.width}" y2="{y:.3f}" '
                   f'stroke="{color}" stroke-width="{lw}"/>')
        y += step
    out.append("</g>")
    return "".join(out)


# ---------------------------------------------------------------- sketch (单色缩略图词汇表)

def _render_sketch(el: SketchEl, th: Theme, fs: float, res: RenderResult) -> str:
    color = el.color or el.stroke_color or th.arrow
    stroke = el.stroke_color or color
    seed = el.seed if el.seed is not None else _stable_seed(el.id, el.kind, el.rect.x, el.rect.y)
    res.sketch_rects.append((el.id, el.kind, el.rect))
    out = [_draw_sketch(el.kind, el.rect, color, stroke, seed, th)]
    if el.label:
        pt = max(th.size_caption * fs * 0.95, 5.5)
        w = measure_markup_mm(el.label, pt)
        span = _TextSpan(x=el.rect.cx - w / 2, baseline=el.rect.bottom - 0.4,
                         text=el.label, pt=pt, bold=False, color=th.muted)
        res.text_spans.append(span)
        out.append(span.to_svg())
    return "".join(out)


def _draw_sketch(kind: str, r: Rect, color: str, stroke: str, seed: int, th: Theme) -> str:
    rng = random.Random(seed)
    pad = min(r.w, r.h) * 0.08
    ir = Rect(r.x + pad, r.y + pad, r.w - 2 * pad, r.h - 2 * pad)
    if ir.w <= 0.5 or ir.h <= 0.5:
        return ""
    fn = _SKETCH_DRAWERS.get(kind)
    if fn is None:
        raise ValueError(f"未知 sketch kind: {kind}")
    return fn(ir, color, stroke, rng, th)


def _sk_waveform(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n_peaks = rng.randint(3, 5)
    n = 28
    pts = []
    for i in range(n):
        t = i / (n - 1)
        x = r.x + t * r.w
        # 多峰正弦叠加
        y_n = 0.5
        for k in range(n_peaks):
            phase = k * 1.7 + 0.3
            amp = 0.32 / (1 + 0.15 * k)
            y_n += amp * math.sin(2 * math.pi * (n_peaks * 0.55) * t + phase)
        y_n = max(0.08, min(0.92, y_n))
        pts.append((x, r.y + (1 - y_n) * r.h))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    return (f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="0.35" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _sk_bars(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = rng.randint(4, 6)
    gap = r.w * 0.08
    bw = (r.w - gap * (n - 1)) / n
    heights = [0.35 + 0.55 * rng.random() for _ in range(n)]
    out = []
    for i, h in enumerate(heights):
        x = r.x + i * (bw + gap)
        bh = h * r.h
        y = r.bottom - bh
        out.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{bw:.3f}" height="{bh:.3f}" '
                   f'rx="0.25" fill="{color}" fill-opacity="0.55" stroke="{stroke}" '
                   f'stroke-width="0.18"/>')
    return "".join(out)


def _sk_heatmap(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = 5 if min(r.w, r.h) > 10 else 4
    gw, gh = r.w / n, r.h / n
    out = []
    for i in range(n):
        for j in range(n):
            # 中心偏亮/偏暗的渐变 + 噪声
            cx, cy = (i + 0.5) / n, (j + 0.5) / n
            dist = math.hypot(cx - 0.5, cy - 0.45)
            val = max(0.15, min(0.95, 0.85 - dist * 1.1 + rng.uniform(-0.12, 0.12)))
            op = 0.15 + 0.75 * val
            out.append(f'<rect x="{r.x + i * gw:.3f}" y="{r.y + j * gh:.3f}" '
                       f'width="{gw - 0.15:.3f}" height="{gh - 0.15:.3f}" '
                       f'fill="{color}" fill-opacity="{op:.3f}"/>')
    out.append(f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
               f'fill="none" stroke="{stroke}" stroke-width="0.15" stroke-opacity="0.4"/>')
    return "".join(out)


def _sk_scatter(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n_clusters = rng.randint(2, 3)
    centers = [(0.28 + 0.22 * k, 0.35 + 0.2 * (k % 2)) for k in range(n_clusters)]
    out = []
    total = rng.randint(8, 12)
    per = max(2, total // n_clusters)
    for ci, (cxn, cyn) in enumerate(centers):
        cx, cy = r.x + cxn * r.w, r.y + cyn * r.h
        for _ in range(per):
            px = cx + rng.gauss(0, r.w * 0.08)
            py = cy + rng.gauss(0, r.h * 0.08)
            px = max(r.x + 0.3, min(r.right - 0.3, px))
            py = max(r.y + 0.3, min(r.bottom - 0.3, py))
            op = 0.55 + 0.15 * (ci % 2)
            out.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="0.55" '
                       f'fill="{color}" fill-opacity="{op:.2f}"/>')
    return "".join(out)


def _sk_axes(r: Rect, stroke: str) -> str:
    return (f'<line x1="{r.x:.3f}" y1="{r.bottom:.3f}" x2="{r.right:.3f}" y2="{r.bottom:.3f}" '
            f'stroke="{stroke}" stroke-width="0.18" stroke-opacity="0.55"/>'
            f'<line x1="{r.x:.3f}" y1="{r.y:.3f}" x2="{r.x:.3f}" y2="{r.bottom:.3f}" '
            f'stroke="{stroke}" stroke-width="0.18" stroke-opacity="0.55"/>')


def _sk_curve(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    """上升收敛曲线。"""
    out = [_sk_axes(r, stroke)]
    n = 20
    pts = []
    for i in range(n):
        t = i / (n - 1)
        # 1 - exp(-kt) 上升收敛
        y_n = 1 - math.exp(-3.2 * t)
        y_n = max(0.05, min(0.92, y_n + rng.uniform(-0.02, 0.02) * (1 - t)))
        pts.append((r.x + t * r.w, r.bottom - y_n * r.h))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="0.38" '
               f'stroke-linecap="round" stroke-linejoin="round"/>')
    return "".join(out)


def _sk_curve_desc(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    """下降损失曲线。"""
    out = [_sk_axes(r, stroke)]
    n = 20
    pts = []
    for i in range(n):
        t = i / (n - 1)
        y_n = math.exp(-2.8 * t) * 0.85 + 0.08
        y_n += rng.uniform(-0.025, 0.025) * math.exp(-t)
        y_n = max(0.05, min(0.95, y_n))
        pts.append((r.x + t * r.w, r.bottom - y_n * r.h))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="0.38" '
               f'stroke-linecap="round" stroke-linejoin="round"/>')
    return "".join(out)


def _sk_grid(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = 4 if min(r.w, r.h) > 9 else 3
    gw, gh = r.w / n, r.h / n
    out = []
    for i in range(n):
        for j in range(n):
            op = 0.25 + 0.45 * rng.random()
            out.append(f'<rect x="{r.x + i * gw + 0.1:.3f}" y="{r.y + j * gh + 0.1:.3f}" '
                       f'width="{gw - 0.25:.3f}" height="{gh - 0.25:.3f}" '
                       f'fill="{color}" fill-opacity="{op:.2f}" stroke="{stroke}" '
                       f'stroke-width="0.12" stroke-opacity="0.5"/>')
    return "".join(out)


def _sk_matrix(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = 4
    gw, gh = r.w / n, r.h / n
    out = [f'<rect x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" height="{r.h:.3f}" '
           f'fill="none" stroke="{stroke}" stroke-width="0.2"/>']
    for i in range(n):
        for j in range(n):
            # 对角线偏深
            val = 0.25 + 0.55 * (1 - abs(i - j) / n) + rng.uniform(-0.08, 0.08)
            val = max(0.12, min(0.95, val))
            out.append(f'<rect x="{r.x + i * gw + 0.12:.3f}" y="{r.y + j * gh + 0.12:.3f}" '
                       f'width="{gw - 0.28:.3f}" height="{gh - 0.28:.3f}" '
                       f'fill="{color}" fill-opacity="{val:.2f}"/>')
    return "".join(out)


def _sk_tree(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    """3 层二叉树，叶节点小彩点。"""
    leaf_colors = ["#5B8DB8", "#E05555", "#E69F00", "#009E73", "#6A5ACD", "#90A4AE"]
    # 节点：root + 2 + 4
    levels = [
        [(0.5, 0.12)],
        [(0.28, 0.48), (0.72, 0.48)],
        [(0.14, 0.88), (0.38, 0.88), (0.62, 0.88), (0.86, 0.88)],
    ]
    nodes = [[(r.x + xn * r.w, r.y + yn * r.h) for xn, yn in lvl] for lvl in levels]
    out = []
    for li, layer in enumerate(nodes[:-1]):
        for i, (x, y) in enumerate(layer):
            for child in nodes[li + 1][i * 2: i * 2 + 2]:
                out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{child[0]:.3f}" y2="{child[1]:.3f}" '
                           f'stroke="{stroke}" stroke-width="0.22"/>')
    for li, layer in enumerate(nodes):
        for i, (x, y) in enumerate(layer):
            if li == 2:
                c = leaf_colors[i % len(leaf_colors)]
                out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="0.9" fill="{c}"/>')
            else:
                out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="0.75" fill="#FFFFFF" '
                           f'stroke="{stroke}" stroke-width="0.25"/>')
    return "".join(out)


def _sk_distribution(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    """钟形分布（折线）+ 可选条形。"""
    out = [_sk_axes(r, stroke)]
    n = 16
    pts = []
    for i in range(n):
        t = i / (n - 1)
        # 高斯钟形
        y_n = math.exp(-0.5 * ((t - 0.5) / 0.18) ** 2)
        pts.append((r.x + t * r.w, r.bottom - y_n * r.h * 0.92))
    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    # 填充阴影
    fill_d = d + f" L {r.right:.3f},{r.bottom:.3f} L {r.x:.3f},{r.bottom:.3f} Z"
    out.append(f'<path d="{fill_d}" fill="{color}" fill-opacity="0.18" stroke="none"/>')
    out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="0.35" '
               f'stroke-linejoin="round"/>')
    return "".join(out)


def _sk_spectrum(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = rng.randint(6, 10)
    gap = r.w * 0.04
    bw = (r.w - gap * (n - 1)) / n
    out = []
    for i in range(n):
        # 频谱：中间偏高
        t = i / max(n - 1, 1)
        h = 0.25 + 0.7 * math.exp(-0.5 * ((t - 0.4) / 0.25) ** 2) + rng.uniform(-0.08, 0.08)
        h = max(0.12, min(0.95, h))
        bh = h * r.h
        x = r.x + i * (bw + gap)
        out.append(f'<rect x="{x:.3f}" y="{r.bottom - bh:.3f}" width="{bw:.3f}" height="{bh:.3f}" '
                   f'fill="{color}" fill-opacity="0.65" stroke="{stroke}" stroke-width="0.12"/>')
    return "".join(out)


def _sk_layers(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    n = rng.randint(2, 3)
    gap = r.h * 0.18
    lh = (r.h - gap * (n - 1)) / n
    out = []
    for i in range(n):
        y = r.y + i * (lh + gap)
        # 略微错位宽度，示意堆叠
        inset = i * r.w * 0.04
        out.append(f'<rect x="{r.x + inset:.3f}" y="{y:.3f}" width="{r.w - 2 * inset:.3f}" '
                   f'height="{lh:.3f}" rx="0.4" fill="{color}" fill-opacity="{0.2 + 0.15 * i:.2f}" '
                   f'stroke="{stroke}" stroke-width="0.25"/>')
    return "".join(out)


def _sk_nested(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    out = []
    for i in range(3):
        inset = i * min(r.w, r.h) * 0.16
        out.append(f'<rect x="{r.x + inset:.3f}" y="{r.y + inset:.3f}" '
                   f'width="{r.w - 2 * inset:.3f}" height="{r.h - 2 * inset:.3f}" '
                   f'rx="0.5" fill="none" stroke="{stroke}" stroke-width="0.28" '
                   f'stroke-opacity="{0.9 - i * 0.2:.2f}"/>')
    return "".join(out)


def _sk_dots_flow(r: Rect, color: str, stroke: str, rng: random.Random, th: Theme) -> str:
    """宽点云 → 收敛线 → 窄列（压缩漏斗）。"""
    out = []
    # 左侧宽点云
    for _ in range(14):
        px = r.x + rng.uniform(0.02, 0.32) * r.w
        py = r.y + rng.uniform(0.08, 0.92) * r.h
        out.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="0.45" '
                   f'fill="{color}" fill-opacity="0.55"/>')
    # 收敛线
    mid_x = r.x + 0.55 * r.w
    out.append(f'<line x1="{r.x + 0.32 * r.w:.3f}" y1="{r.y + 0.15 * r.h:.3f}" '
               f'x2="{mid_x:.3f}" y2="{r.cy:.3f}" stroke="{stroke}" stroke-width="0.22"/>')
    out.append(f'<line x1="{r.x + 0.32 * r.w:.3f}" y1="{r.y + 0.85 * r.h:.3f}" '
               f'x2="{mid_x:.3f}" y2="{r.cy:.3f}" stroke="{stroke}" stroke-width="0.22"/>')
    # 右侧窄列 tokens
    n = 4
    tw, th_ = r.w * 0.12, r.h * 0.14
    for i in range(n):
        x = r.x + 0.72 * r.w
        y = r.y + (i + 0.5) * (r.h / n) - th_ / 2
        out.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{tw:.3f}" height="{th_:.3f}" '
                   f'rx="0.3" fill="{color}" fill-opacity="0.45" stroke="{stroke}" '
                   f'stroke-width="0.15"/>')
    return "".join(out)


_SKETCH_DRAWERS = {
    "waveform": _sk_waveform,
    "bars": _sk_bars,
    "heatmap": _sk_heatmap,
    "scatter": _sk_scatter,
    "curve": _sk_curve,
    "curve_desc": _sk_curve_desc,
    "grid": _sk_grid,
    "matrix": _sk_matrix,
    "tree": _sk_tree,
    "distribution": _sk_distribution,
    "spectrum": _sk_spectrum,
    "layers": _sk_layers,
    "nested": _sk_nested,
    "dots_flow": _sk_dots_flow,
}


# ---------------------------------------------------------------- legend

def _render_legend(el: LegendEl, th: Theme, fs: float, res: RenderResult) -> str:
    legend_style = el.style or th.default_legend_style or "card"
    inline = legend_style == "inline"

    if inline:
        pt = (el.size if el.size_explicit else 6.0) * fs
        cols = len(el.items) if el.columns == 1 else max(1, el.columns)
        # 学术 inline：一行色键，色块 3×3mm + 6pt 标签，无框无底
        sw = sh = 3.0
        gap_x = 3.0
        gap_y = 1.2
        text_gap = 1.0
        pad = 0.0
        use_frame = el.frame if el.frame_explicit else False
    else:
        pt = el.size * fs
        cols = max(1, el.columns)
        sw = 3.2
        sh = 2.2
        gap_x = 2.0
        gap_y = 1.6
        text_gap = 1.2
        pad = 2.0
        use_frame = el.frame

    items = el.items
    rows = math.ceil(len(items) / cols) if cols else 1

    # 预计算每列最大文字宽
    col_text_w = [0.0] * cols
    for i, it in enumerate(items):
        c = i % cols
        col_text_w[c] = max(col_text_w[c], measure_markup_mm(it.label, pt))

    col_w = [sw + text_gap + tw for tw in col_text_w]
    total_w = sum(col_w) + gap_x * (cols - 1) + 2 * pad
    total_h = rows * max(sh, pt * PT_TO_MM * LINE_HEIGHT) + gap_y * (rows - 1) + 2 * pad

    x0, y0 = el.at
    out = []
    fill = el.fill or "#FAFAFA"
    stroke = el.stroke or th.group_stroke
    if use_frame:
        out.append(f'<rect x="{x0:.3f}" y="{y0:.3f}" width="{total_w:.3f}" height="{total_h:.3f}" '
                   f'rx="1.2" fill="{fill}" stroke="{stroke}" stroke-width="0.2"/>')

    row_h = max(sh, pt * PT_TO_MM * LINE_HEIGHT)
    for i, it in enumerate(items):
        c = i % cols
        row = i // cols
        cx = x0 + pad + sum(col_w[:c]) + gap_x * c
        cy = y0 + pad + row * (row_h + gap_y)
        # swatch
        sx, sy = cx, cy + (row_h - sh) / 2
        if it.swatch == "box":
            out.append(f'<rect x="{sx:.3f}" y="{sy:.3f}" width="{sw:.3f}" height="{sh:.3f}" '
                       f'rx="0.35" fill="{it.color}" stroke="{it.color}" stroke-width="0.15"/>')
        elif it.swatch == "line":
            mid = sy + sh / 2
            out.append(f'<line x1="{sx:.3f}" y1="{mid:.3f}" x2="{sx + sw:.3f}" y2="{mid:.3f}" '
                       f'stroke="{it.color}" stroke-width="0.45" stroke-linecap="round"/>')
        elif it.swatch == "dashed":
            mid = sy + sh / 2
            out.append(f'<line x1="{sx:.3f}" y1="{mid:.3f}" x2="{sx + sw:.3f}" y2="{mid:.3f}" '
                       f'stroke="{it.color}" stroke-width="0.45" stroke-dasharray="0.9,0.6" '
                       f'stroke-linecap="round"/>')
        elif it.swatch == "arrow":
            mid = sy + sh / 2
            out.append(f'<line x1="{sx:.3f}" y1="{mid:.3f}" x2="{sx + sw - 0.6:.3f}" y2="{mid:.3f}" '
                       f'stroke="{it.color}" stroke-width="0.4" stroke-linecap="round"/>')
            out.append(f'<polygon points="{sx + sw:.3f},{mid:.3f} {sx + sw - 1.1:.3f},{mid - 0.7:.3f} '
                       f'{sx + sw - 1.1:.3f},{mid + 0.7:.3f}" fill="{it.color}"/>')
        else:  # dot
            out.append(f'<circle cx="{sx + sw / 2:.3f}" cy="{sy + sh / 2:.3f}" r="0.9" '
                       f'fill="{it.color}"/>')
        # label
        tx = cx + sw + text_gap
        asc = line_ascent_mm(it.label or "x", pt)
        span = _TextSpan(x=tx, baseline=cy + row_h / 2 + asc / 2 - 0.25,
                         text=it.label, pt=pt, bold=False, color=th.ink)
        res.text_spans.append(span)
        out.append(span.to_svg())

    # 记录包围盒供 lint
    res.node_rects[el.id] = Rect(x0, y0, total_w, total_h)
    res.node_visual_rects[el.id] = res.node_rects[el.id]
    return "".join(out)
