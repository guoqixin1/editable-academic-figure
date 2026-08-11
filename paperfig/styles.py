"""底稿风格包注册表（混合模式 base.style）。

正向 / 负向正文逐字采用 refs/style-anchors/REPORT.md §3。
theme → 缺省风格映射见 DEFAULT_STYLE_BY_THEME。
"""

from __future__ import annotations

from typing import Any

# 合法包名
BASE_STYLE_NAMES = (
    "journal-schematic",
    "technical-lineart",
    "sci-flat-pro",
)

# theme.preset → 缺省 base.style（其余 / 未知名 → sci-flat-pro）
DEFAULT_STYLE_BY_THEME: dict[str, str] = {
    "neurips": "sci-flat-pro",
    "topconf": "sci-flat-pro",
    "sci": "sci-flat-pro",
    "editorial": "journal-schematic",
    "isosystem": "technical-lineart",
}

# 一句话速查（文档 / Brief）
STYLE_BLURBS: dict[str, str] = {
    "journal-schematic": (
        "Nature/Cell methods 风——技术性简化器物 + 低饱和点缀色 + 极克制具象"
        "（医学/生物管线首选）"
    ),
    "technical-lineart": (
        "OSDI/SOSP + ResNet/Transformer 工程制图——细线、单色或单强调色、纯模块块图"
        "（系统/RL/架构首选）"
    ),
    "sci-flat-pro": (
        "去卡通化专业扁平——可读色块、细描边、告别友好/贴纸先验（通用兜底）"
    ),
}

# §3 正向（整段粘贴）
_POSITIVE: dict[str, str] = {
    "journal-schematic": (
        "Publication-ready biomedical methods schematic in the style of Nature Methods "
        "pipeline figures and Nature Portfolio conceptual illustration principles. "
        "Orthographic or restrained equipment-catalog three-quarter view of real laboratory "
        "devices (microscope, sequencer, pipette, slide) as technical manual "
        "illustrations—accurate silhouettes, minimal detail hierarchy. White or near-white "
        "ground; muted editorial palette; large areas remain neutral grey/beige; at most "
        "three categorical accent hues used sparingly for pathway taxonomy. Hairline dark "
        "strokes (~0.5–1px), sharp or barely-rounded panels, no sticker outlines. "
        "Flat-to-subtle catalog shading on instruments only; no drop shadows. High "
        "information-density grid layout; leave plain light zones for later label plates. "
        "Prefer real-instrument simplification and abstract data glyphs over metaphors."
    ),
    "technical-lineart": (
        "Conference systems/ML architecture diagram in the style of OSDI/SOSP block "
        "schematics and CVPR/NeurIPS model figures (ResNet residual block, Transformer "
        "stacks). Pure 2D orthographic technical lineart: sharp rectangles, hairline black "
        "strokes, sparse fills in greyscale plus at most one muted accent for the critical "
        "path. Nested modules, dashed swimlanes, thin orthogonal connectors with small "
        "sharp arrowheads; optional light hatching for hardware density—like a patent "
        "drawing or engineering blueprint. White background, high information density, "
        "LaTeX-figure austerity. Represent agents, policies, buffers, and GPUs as labeled "
        "abstract modules—never characters. No isometric toys; no decorative icons."
    ),
    "sci-flat-pro": (
        "Professional scientific flat schematic for top-conference papers: geometric "
        "modules with consistent barely-rounded corners, thin dark technical outlines "
        "(~1px), two-tone flat fills without gradients, colorblind-safe categorical "
        "accents drawn from a restrained palette. Orthographic panel layout (not toy "
        "isometric). Objects are technical glyphs—monitors as simple rectangles with plot "
        "marks, organs as anatomical schematics, networks as node grids—not cute props. "
        "White ground, moderate density, editorial restraint. Think laboratory equipment "
        "catalog meets patent-drawing clarity, not consumer infographic."
    ),
}

# §3 负向清单（拼进 Avoid:）
_NEGATIVE: dict[str, str] = {
    "journal-schematic": (
        "mascot faces, smiley, cartoon character, anthropomorphic robot, storybook, "
        "sticker, marketing infographic, macaron pastel fills, candy colors, thick comic "
        "outlines, bubbly rounded UI cards, cute clipboard, award ribbon, balancing scales "
        "metaphor, kawaii, children's book, glossy 3D render, neon glow, glassmorphism, "
        "game asset, voxel, soft drop shadow sticker lift, friendly rounded shapes, "
        "flat vector illustration (generic), app icon set"
    ),
    "technical-lineart": (
        "mascot, smiley robot, cartoon character, storybook, sticker, marketing "
        "infographic, pastel macaron panels, thick outlines, rounded bubble cards, "
        "isometric cute props, scroll parchment, trophy ribbon, snowflake emoji metaphor, "
        "soft shadows, gradients for decoration, 3D render, friendly illustration, "
        "flat vector sticker set, children's book, vibrant rainbow blocks"
    ),
    "sci-flat-pro": (
        "mascot faces, smiley, cartoon character, storybook, sticker pack, marketing "
        "infographic, friendly rounded, bubbly UI, macaron pastels, thick 2–3px comic ink, "
        "soft drop shadows, glossy materials, neon, glassmorphism, children's book, "
        "game asset, anthropomorphic hardware"
    ),
}

BASE_STYLE_PACKS: dict[str, dict[str, str]] = {
    name: {
        "positive": _POSITIVE[name],
        "negative": _NEGATIVE[name],
        "blurb": STYLE_BLURBS[name],
    }
    for name in BASE_STYLE_NAMES
}


def _normalize_theme_preset(theme_cfg: Any) -> str:
    if theme_cfg is None:
        return "sci"
    if isinstance(theme_cfg, str):
        return theme_cfg.strip().lower() or "sci"
    if isinstance(theme_cfg, dict):
        return str(theme_cfg.get("preset") or "sci").strip().lower() or "sci"
    return "sci"


def resolve_base_style(
    style: str | None = None,
    theme_cfg: Any = None,
) -> str:
    """解析底稿风格包名：显式 style 优先，否则按 theme 映射，缺省 sci-flat-pro。"""
    if style is not None and str(style).strip():
        name = str(style).strip().lower()
        if name not in BASE_STYLE_PACKS:
            raise ValueError(
                f"未知 base.style '{style}'（可选: {', '.join(BASE_STYLE_NAMES)}）"
            )
        return name
    preset = _normalize_theme_preset(theme_cfg)
    return DEFAULT_STYLE_BY_THEME.get(preset, "sci-flat-pro")


def get_style_pack(name: str) -> dict[str, str]:
    """返回 {positive, negative, blurb}；未知名抛 ValueError。"""
    key = str(name).strip().lower()
    if key not in BASE_STYLE_PACKS:
        raise ValueError(
            f"未知风格包 '{name}'（可选: {', '.join(BASE_STYLE_NAMES)}）"
        )
    return BASE_STYLE_PACKS[key]


def format_base_style_section(style_name: str) -> str:
    """正向段（带 STYLE PACK 头），供 build_base_prompt 拼装。"""
    pack = get_style_pack(style_name)
    return (
        f"STYLE PACK ({style_name}):\n"
        f"{pack['positive']}"
    )


def format_avoid_section(style_name: str) -> str:
    """末尾 Avoid: 段。"""
    pack = get_style_pack(style_name)
    return f"Avoid: {pack['negative']}"
