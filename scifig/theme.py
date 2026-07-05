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


_PRESETS = {
    "sci": _SCI_VARIANTS,
    "warm": _WARM_VARIANTS,
    "mono": _MONO_VARIANTS,
}


def load_theme(cfg: dict | str | None) -> Theme:
    """从 spec 的 theme 段构建主题。支持 preset 选择与逐项覆盖。

    theme 既可写成 mapping（`theme: {preset: warm, ink: "#000"}`），
    也可写成字符串简写（`theme: warm`）。
    """
    if isinstance(cfg, str):
        cfg = {"preset": cfg}
    cfg = cfg or {}
    preset = cfg.get("preset", "sci")
    if preset not in _PRESETS:
        raise ValueError(f"未知主题 preset: {preset}（可选 {list(_PRESETS)}）")
    th = Theme(name=preset, variants=copy.deepcopy(_PRESETS[preset]))

    for key, val in cfg.items():
        if key in ("preset", "variants"):
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
        )
    return th
