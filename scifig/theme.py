"""出版级配色与排版主题。

所有长度单位 mm，字号单位 pt。默认参数按 180mm 双栏图设计，
印刷缩放后正文仍 ≥6pt（多数期刊的下限要求）。
"""

from __future__ import annotations

import copy
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


_TOPCONF_VARIANTS = _variants_border_style(_TOPCONF_PALETTE)
_AIRY_VARIANTS = _variants_airy(_AIRY_PALETTE)


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


_PRESETS = {
    "sci": _SCI_VARIANTS,
    "warm": _WARM_VARIANTS,
    "mono": _MONO_VARIANTS,
    "topconf": _TOPCONF_VARIANTS,
    "airy": _AIRY_VARIANTS,
}

_PRESET_PALETTES = {
    "topconf": _TOPCONF_PALETTE,
    "airy": _AIRY_PALETTE,
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
}


def _apply_palette(th: Theme, pal: dict[str, str]) -> None:
    """用 8-role 语义色板重建 variants 与主题色。"""
    th.palette = dict(pal)
    if th.name == "airy":
        th.variants = _variants_airy(pal)
    else:
        # topconf 及任何"边框着色"预设；sci/warm/mono 若给 palette 也切到边框风
        th.variants = _variants_border_style(pal)
    th.ink = pal.get("text", th.ink)
    th.arrow = pal.get("arrow", th.arrow)
    th.group_fill = pal.get("section_bg", th.group_fill)
    th.group_stroke = pal.get("border", th.group_stroke)
    th.muted = pal.get("muted_text", th.muted)
    if "muted_text" not in pal and "text" in pal:
        # 次要注释色：略浅于正文字色
        th.muted = "#666666" if th.name == "topconf" else th.muted


def load_theme(cfg: dict | str | None) -> Theme:
    """从 spec 的 theme 段构建主题。支持 preset 选择与逐项覆盖。

    theme 既可写成 mapping（`theme: {preset: warm, ink: "#000"}`），
    也可写成字符串简写（`theme: warm`）。

    新能力：
      - preset: topconf | airy（保留 sci/warm/mono）
      - palette: {primary, secondary, ...} 语义色板覆盖
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
        setattr(th, key, val)

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
