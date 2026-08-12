"""出版级配色与排版主题。

所有长度单位 mm，字号单位 pt。默认参数按 180mm 双栏图设计，
印刷缩放后正文仍 ≥6pt（多数期刊的下限要求）。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field


@dataclass
class Variant:
    fill: str
    stroke: str
    text: str
    lw: float | None = None  # 覆盖 theme.lw_box（如 topconf primary 0.4 / muted 0.28）


_SCI_VARIANTS = {
    "primary": Variant(fill="#E3ECF7", stroke="#3B6EA5", text="#1F2933"),
    "secondary": Variant(fill="#E9F0EA", stroke="#5B8266", text="#1F2933"),
    "accent": Variant(fill="#FCF0E1", stroke="#C77D2E", text="#1F2933"),
    "highlight": Variant(fill="#FBE9E7", stroke="#B5493A", text="#1F2933"),
    "plain": Variant(fill="#FFFFFF", stroke="#8B96A5", text="#1F2933"),
    "dark": Variant(fill="#33415C", stroke="#33415C", text="#FFFFFF"),
}

_WARM_VARIANTS = {
    "primary": Variant(fill="#FBEEDB", stroke="#B8722C", text="#3A2E22"),
    "secondary": Variant(fill="#EFE7DA", stroke="#8A7A5E", text="#3A2E22"),
    "accent": Variant(fill="#E7EEE9", stroke="#4E7D62", text="#3A2E22"),
    "highlight": Variant(fill="#F7E3E0", stroke="#A94438", text="#3A2E22"),
    "plain": Variant(fill="#FFFFFF", stroke="#9C9285", text="#3A2E22"),
    "dark": Variant(fill="#54432E", stroke="#54432E", text="#FFFFFF"),
}

_MONO_VARIANTS = {
    "primary": Variant(fill="#E8E8E8", stroke="#3D3D3D", text="#1A1A1A"),
    "secondary": Variant(fill="#F4F4F4", stroke="#6E6E6E", text="#1A1A1A"),
    "accent": Variant(fill="#D8D8D8", stroke="#2A2A2A", text="#1A1A1A"),
    "highlight": Variant(fill="#CFCFCF", stroke="#111111", text="#111111"),
    "plain": Variant(fill="#FFFFFF", stroke="#8A8A8A", text="#1A1A1A"),
    "dark": Variant(fill="#3D3D3D", stroke="#3D3D3D", text="#FFFFFF"),
}

# 顶会克制风：白填充 + 彩色/灰色细边框（Okabe-Ito 默认）
_TOPCONF_PALETTE = {
    "primary": "#0072B2",
    "secondary": "#E69F00",
    "tertiary": "#009E73",
    "text": "#333333",
    "fill": "#FFFFFF",
    "section_bg": "#F7F7F7",
    "border": "#CCCCCC",
    "arrow": "#4D4D4D",
}

# 现代柔彩风（ICLR/NeurIPS 2024-25）pastel token 色
_AIRY_PALETTE = {
    "primary": "#5B8DB8",
    "secondary": "#E05555",
    "tertiary": "#1A9988",
    "text": "#2D3436",
    "fill": "#FFFFFF",
    "section_bg": "#FFFFFF",
    "border": "#CFD8DC",
    "arrow": "#555555",
    # 强调文字色（airy 专用扩展）
    "coral": "#E05555",
    "teal": "#1A9988",
    "purple": "#6A5ACD",
}

# Soft Pastel academic（NeurIPS 方法图默认）：Okabe 描边 + 浅填
_NEURIPS_PALETTE = {
    "primary": "#0072B2",
    "secondary": "#E69F00",
    "tertiary": "#009E73",
    "sky": "#56B4E9",
    "purple": "#CC79A7",
    "vermillion": "#D55E00",
    "text": "#333333",
    "fill": "#FFFFFF",
    "section_bg": "#F7F7F5",
    "border": "#DDDDDD",
    "arrow": "#4D4D4D",
}

# Anthropic/Distill 暖编辑风
_EDITORIAL_PALETTE = {
    "primary": "#6A6A6A",
    "secondary": "#6A9BCC",
    "tertiary": "#2F5B4F",
    "accent": "#D97757",
    "text": "#141413",
    "fill": "#FFFDF8",
    "section_bg": "#F2EDE4",
    "border": "#E8E4DC",
    "arrow": "#4A4A4A",
    "canvas": "#FAF9F5",
}

# 浅晒图等距/系统风
_ISOSYSTEM_PALETTE = {
    "primary": "#3D5A80",
    "secondary": "#EE6C4D",
    "tertiary": "#293241",
    "text": "#1F2A37",
    "fill": "#FFFFFF",
    "section_bg": "#E8EEF5",
    "border": "#9AA8B8",
    "arrow": "#3D5A80",
    "grid": "#D0D7E2",
    "canvas": "#F4F7FA",
}

# 工程线稿风：灰阶结构 + 单一钢蓝强调（搭配 technical-lineart / journal-schematic 底稿）
_LINEART_PALETTE = {
    "primary": "#3D6B99",      # steel blue：accent / 徽章 / 关键路径
    "secondary": "#4A5568",    # 中灰结构边
    "tertiary": "#4A5568",
    "text": "#333333",
    "title": "#1A202C",
    "fill": "#F7F8FA",
    "section_bg": "#F7F8FA",
    "border": "#4A5568",
    "arrow": "#2A2E35",        # 主实线；辅虚线见 arrow_aux / arrow_styles
    "canvas": "#FFFFFF",
}

# mainstream §1.2 Soft Pastel 基准填色（stroke → fill）
_KNOWN_PASTEL_FILLS = {
    "#0072B2": "#E8F4FD",
    "#E69F00": "#FFF3E0",
    "#009E73": "#E6F5F0",
    "#56B4E9": "#E3F2FD",
    "#CC79A7": "#F3E5F5",
    "#D55E00": "#FCE8E6",
    "#E07A3D": "#FCE8D8",
    "#90A4AE": "#ECEFF1",
    "#8C8C8C": "#F5F5F5",
    "#4A90D9": "#E8F4FD",
    "#3D5A80": "#E8EEF5",
    "#EE6C4D": "#FDE8E2",
    "#D97757": "#F8E6DF",
    "#6A9BCC": "#E8F1F8",
    "#2F5B4F": "#E4EDE9",
}


def _norm_hex(c: str) -> str:
    s = str(c).strip()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s.upper()


def pastel_fill_from_stroke(stroke: str, mix: float = 0.14) -> str:
    """由描边色派生浅填（约 12–18% 饱和 tint）。已知 Okabe 色用基准表。"""
    key = _norm_hex(stroke)
    if key in _KNOWN_PASTEL_FILLS:
        return _KNOWN_PASTEL_FILLS[key]
    m = re.fullmatch(r"#([0-9A-F]{6})", key)
    if not m:
        return "#F5F5F5"
    r = int(m.group(1)[0:2], 16)
    g = int(m.group(1)[2:4], 16)
    b = int(m.group(1)[4:6], 16)
    t = max(0.10, min(0.18, mix))
    return "#{:02X}{:02X}{:02X}".format(
        round(255 * (1 - t) + r * t),
        round(255 * (1 - t) + g * t),
        round(255 * (1 - t) + b * t),
    )


_NEURIPS_ARROW_STYLES: dict[str, dict] = {
    "data": {"style": "solid", "color": "#4D4D4D", "width": 0.24},
    "control": {"style": "solid", "color": "#7B8794", "width": 0.20},
    "feedback": {"style": "dashed", "color": "#0072B2", "width": 0.20},
    "optional": {"style": "dotted", "color": "#999999", "width": 0.16},
    "error": {"style": "dashed", "color": "#D94A4A", "width": 0.22},
}


def _variants_border_style(pal: dict[str, str]) -> dict[str, Variant]:
    """白填充 + 角色色边框（topconf 设计语言）。"""
    fill = pal.get("fill", "#FFFFFF")
    text = pal.get("text", "#333333")
    return {
        "primary": Variant(fill=fill, stroke=pal["primary"], text=text, lw=0.4),
        "secondary": Variant(fill=fill, stroke=pal["secondary"], text=text, lw=0.4),
        "accent": Variant(fill=fill, stroke=pal.get("tertiary", pal["secondary"]), text=text, lw=0.35),
        "highlight": Variant(fill=fill, stroke="#D55E00", text=text, lw=0.35),
        "plain": Variant(fill=fill, stroke=pal.get("border", "#CCCCCC"), text=text, lw=0.28),
        "muted": Variant(fill=fill, stroke=pal.get("border", "#CCCCCC"), text=text, lw=0.28),
        "dark": Variant(fill=text, stroke=text, text=fill, lw=0.35),
        "tertiary": Variant(fill=fill, stroke=pal.get("tertiary", pal["primary"]), text=text, lw=0.35),
    }


def _variants_airy(pal: dict[str, str]) -> dict[str, Variant]:
    """pastel 填充 + 略深 1px 边框（airy 设计语言）。"""
    text = pal.get("text", "#2D3436")
    return {
        "primary": Variant(fill="#BBDEFB", stroke="#90CAF9", text=text, lw=0.28),
        "secondary": Variant(fill="#FFD0D0", stroke="#EF9A9A", text=text, lw=0.28),
        "accent": Variant(fill="#FFF3C4", stroke="#FFE082", text=text, lw=0.28),
        "highlight": Variant(fill="#E1BEE7", stroke="#CE93D8", text=text, lw=0.28),
        "plain": Variant(fill="#FFFFFF", stroke=pal.get("border", "#CFD8DC"), text=text, lw=0.25),
        "muted": Variant(fill="#F5F5F5", stroke="#B0BEC5", text=text, lw=0.25),
        "dark": Variant(fill="#C8E6C9", stroke="#A5D6A7", text=text, lw=0.28),
        "tertiary": Variant(fill="#C8E6C9", stroke="#A5D6A7", text=text, lw=0.28),
    }


def _variants_pastel(pal: dict[str, str]) -> dict[str, Variant]:
    """Soft Pastel：浅填 + 同色系深描边（neurips 设计语言）。"""
    text = pal.get("text", "#333333")
    border = pal.get("border", "#DDDDDD")

    def role(stroke: str, lw: float | None = None, fill: str | None = None) -> Variant:
        return Variant(
            fill=fill or pastel_fill_from_stroke(stroke),
            stroke=stroke,
            text=text,
            lw=lw,
        )

    primary = pal.get("primary", "#0072B2")
    secondary = pal.get("secondary", "#E69F00")
    tertiary = pal.get("tertiary", "#009E73")
    sky = pal.get("sky", "#56B4E9")
    purple = pal.get("purple", "#CC79A7")
    vermillion = pal.get("vermillion", "#D55E00")
    return {
        "primary": role(primary),
        "secondary": role(secondary),
        "tertiary": role(tertiary),
        "sky": role(sky),
        "purple": role(purple),
        "vermillion": role(vermillion),
        "accent": role(purple),
        "highlight": role(vermillion),
        "plain": Variant(fill=pal.get("fill", "#FFFFFF"), stroke=border, text=text),
        "muted": Variant(fill="#F7F7F5", stroke=border, text=text),
        "section": Variant(fill=pal.get("section_bg", "#F7F7F5"), stroke=border, text=text),
        "trainable": role("#E07A3D"),
        "frozen": role("#90A4AE"),
        "ours": role(primary, lw=0.28),
        "baseline": Variant(fill="#F5F5F5", stroke="#8C8C8C", text=text),
        "dark": Variant(fill=text, stroke=text, text="#FFFFFF"),
    }


def _variants_editorial(pal: dict[str, str]) -> dict[str, Variant]:
    """暖纸编辑风：纸色填充 + 暖灰描边；clay accent 仅 highlight。"""
    text = pal.get("text", "#141413")
    fill = pal.get("fill", "#FFFDF8")
    border = pal.get("border", "#E8E4DC")
    clay = pal.get("accent", "#D97757")
    return {
        "primary": Variant(fill=fill, stroke=pal.get("primary", "#6A6A6A"), text=text),
        "secondary": Variant(fill=fill, stroke=pal.get("secondary", "#6A9BCC"), text=text),
        "tertiary": Variant(fill=fill, stroke=pal.get("tertiary", "#2F5B4F"), text=text),
        "accent": Variant(fill=pastel_fill_from_stroke(clay), stroke=clay, text=text),
        "highlight": Variant(fill=pastel_fill_from_stroke(clay), stroke=clay, text=text),
        "plain": Variant(fill=fill, stroke=border, text=text),
        "muted": Variant(fill=pal.get("section_bg", "#F2EDE4"), stroke=border, text=text),
        "dark": Variant(fill=text, stroke=text, text="#FFFDF8"),
    }


def _variants_isosystem(pal: dict[str, str]) -> dict[str, Variant]:
    """浅晒图系统风：白填 + 钢蓝描边；橙 accent 仅强调。"""
    text = pal.get("text", "#1F2A37")
    fill = pal.get("fill", "#FFFFFF")
    primary = pal.get("primary", "#3D5A80")
    accent = pal.get("secondary", "#EE6C4D")
    border = pal.get("border", "#9AA8B8")
    return {
        "primary": Variant(fill=fill, stroke=primary, text=text, lw=0.30),
        "secondary": Variant(fill=pastel_fill_from_stroke(accent), stroke=accent, text=text, lw=0.30),
        "tertiary": Variant(fill=fill, stroke=pal.get("tertiary", "#293241"), text=text, lw=0.28),
        "accent": Variant(fill=pastel_fill_from_stroke(accent), stroke=accent, text=text, lw=0.30),
        "highlight": Variant(fill=pastel_fill_from_stroke(accent), stroke=accent, text=text, lw=0.32),
        "plain": Variant(fill=fill, stroke=border, text=text, lw=0.25),
        "muted": Variant(fill=pal.get("section_bg", "#E8EEF5"), stroke=border, text=text, lw=0.25),
        "dark": Variant(fill=pal.get("tertiary", "#293241"), stroke=pal.get("tertiary", "#293241"),
                        text="#FFFFFF", lw=0.28),
    }


def _variants_lineart(pal: dict[str, str]) -> dict[str, Variant]:
    """工程线稿：极浅灰/白填 + 中灰细边；仅 accent/primary 用钢蓝。"""
    text = pal.get("text", "#333333")
    fill = pal.get("fill", "#F7F8FA")
    white = "#FFFFFF"
    steel = pal.get("primary", "#3D6B99")
    gray = pal.get("border", "#4A5568")
    return {
        "primary": Variant(fill=white, stroke=steel, text=text, lw=0.35),
        "secondary": Variant(fill=fill, stroke=gray, text=text, lw=0.35),
        "tertiary": Variant(fill=fill, stroke=gray, text=text, lw=0.35),
        "accent": Variant(fill=white, stroke=steel, text=text, lw=0.35),
        "highlight": Variant(fill=white, stroke=steel, text=text, lw=0.35),
        "plain": Variant(fill=white, stroke=gray, text=text, lw=0.35),
        "muted": Variant(fill=fill, stroke=gray, text=text, lw=0.30),
        "dark": Variant(fill="#1A202C", stroke="#1A202C", text="#FFFFFF", lw=0.30),
    }


_TOPCONF_VARIANTS = _variants_border_style(_TOPCONF_PALETTE)
_AIRY_VARIANTS = _variants_airy(_AIRY_PALETTE)
_NEURIPS_VARIANTS = _variants_pastel(_NEURIPS_PALETTE)
_EDITORIAL_VARIANTS = _variants_editorial(_EDITORIAL_PALETTE)
_ISOSYSTEM_VARIANTS = _variants_isosystem(_ISOSYSTEM_PALETTE)
_LINEART_VARIANTS = _variants_lineart(_LINEART_PALETTE)

# 变体语言：决定 palette 覆盖时如何重建 variants
_PASTEL_PRESETS = frozenset({"neurips"})
_AIRY_PRESETS = frozenset({"airy"})
_EDITORIAL_PRESETS = frozenset({"editorial"})
_ISOSYSTEM_PRESETS = frozenset({"isosystem"})
_LINEART_PRESETS = frozenset({"lineart"})


@dataclass
class Theme:
    name: str = "sci"
    variants: dict[str, Variant] = field(default_factory=lambda: copy.deepcopy(_SCI_VARIANTS))

    ink: str = "#1F2933"           # 默认文字
    muted: str = "#52606D"         # 次要文字（caption、组标签）
    arrow: str = "#445263"
    group_stroke: str = "#8492A6"
    group_fill: str = "none"

    # 字号（pt）
    size_panel_label: float = 10.0
    size_title: float = 7.5
    size_body: float = 6.5
    size_caption: float = 6.0
    size_arrow_label: float = 6.0
    size_group_label: float = 6.8

    # 线宽（mm）
    lw_box: float = 0.28
    lw_arrow: float = 0.32
    lw_group: float = 0.24

    corner_radius: float = 1.6     # 盒子圆角 mm
    box_pad_x: float = 2.2         # 盒内水平留白 mm
    box_pad_y: float = 1.6

    arrow_head_len: float = 1.9    # 箭头长 mm
    arrow_head_w: float = 1.5      # 箭头底宽 mm

    # 视觉增强（默认关闭，保证旧 preset 行为不变）
    default_shadow: bool = False
    palette: dict[str, str] = field(default_factory=dict)

    # 箭头语义预设 / panel 编号 / 图例默认 / 画布
    arrow_styles: dict[str, dict] = field(default_factory=dict)
    panel_case: str = "ml"                 # ml | lower | upper
    default_legend_style: str = "card"     # card | inline
    canvas: str | None = None              # figure 级底色（未显式 background 时采用）
    grid_bg: bool = False
    grid_color: str = "#D0D7E2"
    grid_step: float = 5.0
    grid_lw: float = 0.12

    # lint font-small 软下限（pt）；None=沿用 IDEAL_MIN_FONT_PT(6.0) 旧行为
    lint_min_font: float | None = None

    # base 模式文字底板（半透明白圆角，保证插画上可读）
    plate_fill: str = "#FFFFFF"
    plate_opacity: float = 0.92
    plate_pad: float = 1.2       # mm，文字包围盒外扩
    plate_radius: float = 1.2    # mm

    # 字体体系（SVG font-family / font-weight；度量仍走 fonts.py 的 Liberation/DejaVu）
    # Lato 实测覆盖 π/θ/τ/Δ/β/λ；∥(U+2225) 不在 Lato → latin 以外的 symbol run 仍用 DejaVu；
    # cairo toy：按字重映射面名（Regular→LatoPFRegular，500→Lato/Medium，600→Lato Semibold），
    # 并追加 Liberation Sans 兜底（cairosvg 只取首项；其它 SVG 查看器可读列表）。
    font_family: str = "Liberation Sans"
    title_weight: int = 700      # box/panel 标题、panel_label
    body_weight: int = 400       # body / 普通 text
    label_weight: int = 700      # 箭头标签、badge、legend、group 标签
    # panel smallcaps 字距（mm）；text.smallcaps 按 0.35/0.45 比例缩放
    smallcaps_letter_spacing: float = 0.45

    # 箭头虚线样式（dashed/dotted 无显式 color 时用 arrow_aux）
    arrow_aux: str | None = None
    lw_arrow_aux: float | None = None   # None=沿用 lw_arrow
    arrow_dasharray: str = "1.6,1.1"
    arrow_dotarray: str = "0.35,0.95"


_PRESETS = {
    "sci": _SCI_VARIANTS,
    "warm": _WARM_VARIANTS,
    "mono": _MONO_VARIANTS,
    "topconf": _TOPCONF_VARIANTS,
    "airy": _AIRY_VARIANTS,
    "neurips": _NEURIPS_VARIANTS,
    "editorial": _EDITORIAL_VARIANTS,
    "isosystem": _ISOSYSTEM_VARIANTS,
    "lineart": _LINEART_VARIANTS,
}

_PRESET_PALETTES = {
    "topconf": _TOPCONF_PALETTE,
    "airy": _AIRY_PALETTE,
    "neurips": _NEURIPS_PALETTE,
    "editorial": _EDITORIAL_PALETTE,
    "isosystem": _ISOSYSTEM_PALETTE,
    "lineart": _LINEART_PALETTE,
}

_PRESET_DEFAULTS: dict[str, dict] = {
    "topconf": {
        "ink": "#333333",
        "muted": "#666666",
        "arrow": "#4D4D4D",
        "group_stroke": "#CCCCCC",
        "group_fill": "#F7F7F7",
        "size_panel_label": 11.0,
        "size_title": 8.5,
        "size_body": 6.5,
        "size_caption": 6.0,
        "size_arrow_label": 6.0,
        "size_group_label": 7.0,
        "lw_box": 0.35,
        "lw_arrow": 0.35,
        "lw_group": 0.22,
        "corner_radius": 1.4,
        "default_shadow": False,
    },
    "airy": {
        "ink": "#2D3436",
        "muted": "#636E72",
        "arrow": "#555555",
        "group_stroke": "#CFD8DC",
        "group_fill": "#FFFFFF",
        "size_panel_label": 11.0,
        "size_title": 9.0,
        "size_body": 7.0,
        "size_caption": 6.5,
        "size_arrow_label": 6.5,
        "size_group_label": 7.5,
        "lw_box": 0.28,
        "lw_arrow": 0.30,
        "lw_group": 0.22,
        "corner_radius": 3.0,
        "default_shadow": True,
        "box_pad_x": 2.4,
        "box_pad_y": 1.8,
    },
    "neurips": {
        "ink": "#333333",
        "muted": "#666666",
        "arrow": "#4D4D4D",
        "group_stroke": "#DDDDDD",
        "group_fill": "#F7F7F5",
        "size_panel_label": 8.5,
        "size_title": 7.2,
        "size_body": 6.3,
        "size_caption": 5.8,
        "size_arrow_label": 5.8,
        "size_group_label": 6.8,
        "lw_box": 0.22,
        "lw_arrow": 0.24,
        "lw_group": 0.16,
        "corner_radius": 1.2,
        "box_pad_x": 2.0,
        "box_pad_y": 1.4,
        "arrow_head_len": 1.7,
        "arrow_head_w": 1.3,
        "default_shadow": False,
        "arrow_styles": copy.deepcopy(_NEURIPS_ARROW_STYLES),
        "panel_case": "ml",
        "default_legend_style": "inline",
        "canvas": "#FFFFFF",
        "lint_min_font": 5.5,  # Nature 下限 5pt + 0.5 缓冲；允许印刷档 5.8
    },
    "editorial": {
        "ink": "#141413",
        "muted": "#6A6A6A",
        "arrow": "#4A4A4A",
        "group_stroke": "#E8E4DC",
        "group_fill": "#F2EDE4",
        "size_panel_label": 9.0,
        "size_title": 8.5,
        "size_body": 7.0,
        "size_caption": 6.5,
        "size_arrow_label": 6.5,
        "size_group_label": 7.0,
        "lw_box": 0.22,
        "lw_arrow": 0.24,
        "lw_group": 0.18,
        "corner_radius": 1.4,
        "box_pad_x": 2.2,
        "box_pad_y": 1.8,
        "default_shadow": False,
        "default_legend_style": "inline",
        "canvas": "#FAF9F5",
        "lint_min_font": 5.5,
        "arrow_styles": {
            "data": {"style": "solid", "color": "#4A4A4A", "width": 0.24},
            "control": {"style": "solid", "color": "#6A6A6A", "width": 0.20},
            "feedback": {"style": "dashed", "color": "#6A9BCC", "width": 0.20},
            "optional": {"style": "dotted", "color": "#999999", "width": 0.16},
            "error": {"style": "dashed", "color": "#D97757", "width": 0.22},
        },
    },
    "isosystem": {
        "ink": "#1F2A37",
        "muted": "#5A6A7A",
        "arrow": "#3D5A80",
        "group_stroke": "#9AA8B8",
        "group_fill": "#E8EEF5",
        "size_panel_label": 9.0,
        "size_title": 8.0,
        "size_body": 6.5,
        "size_caption": 6.0,
        "size_arrow_label": 6.0,
        "size_group_label": 7.0,
        "lw_box": 0.30,
        "lw_arrow": 0.32,
        "lw_group": 0.18,
        "corner_radius": 1.0,
        "box_pad_x": 2.0,
        "box_pad_y": 1.4,
        "default_shadow": False,
        "default_legend_style": "inline",
        "canvas": "#F4F7FA",
        "lint_min_font": 5.5,
        "grid_bg": False,  # figure/theme 显式 grid_bg: true 开启
        "grid_color": "#D0D7E2",
        "grid_step": 5.0,
        "grid_lw": 0.12,
        "arrow_styles": {
            "data": {"style": "solid", "color": "#3D5A80", "width": 0.32},
            "control": {"style": "solid", "color": "#9AA8B8", "width": 0.22},
            "feedback": {"style": "dashed", "color": "#3D5A80", "width": 0.24},
            "optional": {"style": "dotted", "color": "#9AA8B8", "width": 0.16},
            "error": {"style": "dashed", "color": "#EE6C4D", "width": 0.28},
        },
    },
    "lineart": {
        # 克制工程线稿：近直角、灰阶细边、单一钢蓝强调；无糖果色/无强制 smallcaps
        "ink": "#1A202C",
        "muted": "#4A5568",
        "arrow": "#2A2E35",
        "group_stroke": "#4A5568",
        "group_fill": "#F7F8FA",
        "size_panel_label": 8.5,
        "size_title": 7.2,
        "size_body": 6.3,
        "size_caption": 5.8,
        "size_arrow_label": 5.8,
        "size_group_label": 6.8,
        "lw_box": 0.35,
        "lw_arrow": 0.50,
        "lw_group": 0.22,
        "corner_radius": 0.8,   # box/panel/legend 近直角
        "box_pad_x": 2.0,
        "box_pad_y": 1.4,
        "arrow_head_len": 1.7,
        "arrow_head_w": 1.3,
        "default_shadow": False,  # 需要时极浅（全局 blur 已 ≤0.3mm y / 低透明）
        # Lato：标题 Semibold(600)/正文 Regular(400)/标签 Medium(500)
        "font_family": "Lato",
        "title_weight": 600,
        "body_weight": 400,
        "label_weight": 500,
        "smallcaps_letter_spacing": 0.315,  # 0.45×0.7，收紧约 30%
        "arrow_aux": "#5A6472",
        "lw_arrow_aux": 0.45,
        "arrow_dasharray": "2.6,1.3",
        "arrow_dotarray": "0.55,1.1",
        "arrow_styles": {
            "data": {"style": "solid", "color": "#2A2E35", "width": 0.50},
            "control": {"style": "solid", "color": "#5A6472", "width": 0.45},
            "feedback": {"style": "dashed", "color": "#2F5A85", "width": 0.45},
            "optional": {"style": "dotted", "color": "#5A6472", "width": 0.45},
            "error": {"style": "dashed", "color": "#4A5568", "width": 0.45},
        },
        "panel_case": "ml",
        "default_legend_style": "inline",
        "canvas": "#FFFFFF",
        "lint_min_font": 5.5,  # 与 neurips 同
        "plate_fill": "#FFFFFF",
        "plate_opacity": 0.92,
        "plate_pad": 1.2,
        "plate_radius": 0.6,
    },
}


def _rebuild_variants(name: str, pal: dict[str, str]) -> dict[str, Variant]:
    if name in _AIRY_PRESETS:
        return _variants_airy(pal)
    if name in _PASTEL_PRESETS:
        return _variants_pastel(pal)
    if name in _EDITORIAL_PRESETS:
        return _variants_editorial(pal)
    if name in _ISOSYSTEM_PRESETS:
        return _variants_isosystem(pal)
    if name in _LINEART_PRESETS:
        return _variants_lineart(pal)
    return _variants_border_style(pal)


def _apply_palette(th: Theme, pal: dict[str, str]) -> None:
    """用语义色板重建 variants 与主题色。"""
    th.palette = dict(pal)
    th.variants = _rebuild_variants(th.name, pal)
    th.ink = pal.get("text", th.ink)
    th.arrow = pal.get("arrow", th.arrow)
    th.group_fill = pal.get("section_bg", th.group_fill)
    th.group_stroke = pal.get("border", th.group_stroke)
    th.muted = pal.get("muted_text", th.muted)
    if "muted_text" not in pal and "text" in pal:
        if th.name in ("topconf", "neurips"):
            th.muted = "#666666"
        elif th.name == "editorial":
            th.muted = "#6A6A6A"
        elif th.name == "lineart":
            th.muted = "#4A5568"
    if th.name == "lineart" and "title" in pal:
        th.ink = pal["title"]
    if "canvas" in pal:
        th.canvas = pal["canvas"]
    if "grid" in pal:
        th.grid_color = pal["grid"]


def load_theme(cfg: dict | str | None) -> Theme:
    """从 spec 的 theme 段构建主题。支持 preset 选择与逐项覆盖。

    theme 既可写成 mapping（`theme: {preset: warm, ink: "#000"}`），
    也可写成字符串简写（`theme: warm`）。

    新能力：
      - preset: topconf | airy | neurips | editorial | isosystem | lineart（保留 sci/warm/mono）
      - palette: {primary, secondary, ...} 语义色板覆盖
      - arrow_styles / panel_case / default_legend_style / canvas / grid_bg
      - font_family / title_weight / body_weight / label_weight / smallcaps_letter_spacing
      - arrow_aux / lw_arrow_aux / arrow_dasharray / arrow_dotarray
    """
    if isinstance(cfg, str):
        cfg = {"preset": cfg}
    cfg = cfg or {}
    preset = cfg.get("preset", "sci")
    if preset not in _PRESETS:
        raise ValueError(f"未知主题 preset: {preset}（可选 {list(_PRESETS)}）")
    th = Theme(name=preset, variants=copy.deepcopy(_PRESETS[preset]))

    # preset 专属默认（在用户覆盖之前）
    for key, val in _PRESET_DEFAULTS.get(preset, {}).items():
        setattr(th, key, copy.deepcopy(val) if isinstance(val, dict) else val)

    if preset in _PRESET_PALETTES:
        th.palette = dict(_PRESET_PALETTES[preset])

    # 语义色板覆盖（可只给部分 role）
    if "palette" in cfg and cfg["palette"]:
        base = dict(_PRESET_PALETTES.get(preset, _TOPCONF_PALETTE))
        base.update({str(k): str(v) for k, v in cfg["palette"].items()})
        _apply_palette(th, base)

    for key, val in cfg.items():
        if key in ("preset", "variants", "palette"):
            continue
        if not hasattr(th, key):
            raise ValueError(f"未知主题字段: {key}")
        if key == "arrow_styles" and isinstance(val, dict):
            merged = dict(th.arrow_styles)
            for sk, sv in val.items():
                merged[str(sk)] = {**merged.get(str(sk), {}), **dict(sv)}
            th.arrow_styles = merged
            continue
        setattr(th, key, val)

    for vname, vcfg in (cfg.get("variants") or {}).items():
        base = th.variants.get(vname) or Variant(fill="#FFFFFF", stroke="#888888", text=th.ink)
        th.variants[vname] = Variant(
            fill=vcfg.get("fill", base.fill),
            stroke=vcfg.get("stroke", base.stroke),
            text=vcfg.get("text", base.text),
            lw=vcfg.get("lw", base.lw),
        )
    return th
