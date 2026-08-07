"""图形 spec 的数据模型、加载与校验。

spec 是一份 YAML，声明画布、主题、素材请求与元素列表。
布局坐标全部显式（mm），这是"可控"的来源：渲染结果与 spec 一一对应，
可以手工微调任何一个数值后重渲。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def intersection_area(self, o: "Rect") -> float:
        dx = min(self.right, o.right) - max(self.x, o.x)
        dy = min(self.bottom, o.bottom) - max(self.y, o.y)
        return dx * dy if dx > 0 and dy > 0 else 0.0

    def contains_point(self, px: float, py: float, margin: float = 0.0) -> bool:
        return (self.x + margin) < px < (self.right - margin) and (self.y + margin) < py < (self.bottom - margin)

    def expanded(self, pad: float) -> "Rect":
        return Rect(self.x - pad, self.y - pad, self.w + 2 * pad, self.h + 2 * pad)


def _rect(v, ctx: str) -> Rect:
    if not (isinstance(v, (list, tuple)) and len(v) == 4):
        raise SpecError(f"{ctx}: rect 必须是 [x, y, w, h] 四元数组，得到 {v!r}")
    x, y, w, h = (float(t) for t in v)
    if w <= 0 or h <= 0:
        raise SpecError(f"{ctx}: rect 宽高必须为正，得到 {v!r}")
    return Rect(x, y, w, h)


class SpecError(ValueError):
    pass


@dataclass
class AssetRequest:
    id: str
    prompt: str
    aspect: str = "1:1"
    candidates: int = 3
    shadow: str = "keep"  # keep | remove，见 cutout.py


@dataclass
class BoxEl:
    id: str
    rect: Rect
    title: str = ""
    body: str = ""
    variant: str = "primary"
    shape: str = "rect"          # rect | stadium | diamond | cylinder | parallelogram | hexagon | ellipse | trapezoid
    icon: str | None = None      # 素材 id 或路径，渲染在盒内文本上方
    icon_h: float = 10.0         # 图标高度 mm
    title_size: float | None = None
    body_size: float | None = None
    align: str = "center"        # center | left
    valign: str = "middle"       # middle | top（top：标题贴顶，做容器/子卡标题）
    gradient: list[str] | None = None   # 两色渐变填充 [c1, c2]，覆盖 variant.fill
    gradient_dir: str = "h"      # h | v
    fill: str | None = None      # 覆盖 variant 填充色（渐变色系列等）
    stroke: str | None = None    # 覆盖 variant 描边色
    text_color: str | None = None
    stack: int = 0               # 背后叠影层数（层叠卡片/文档效果）
    # 视觉增强（默认保持旧行为）
    shadow: bool | None = None   # None=跟随 theme.default_shadow
    accent: str | None = None    # left | top：色条（取 variant 边框色）
    header_fill: bool = False    # 标题区浅底 + 分隔线
    sketch: str | None = None    # 内嵌单色缩略图 kind（见 SketchEl）


@dataclass
class AssetEl:
    id: str
    rect: Rect
    src: str                     # 素材 id 或路径
    caption: str = ""
    halign: str = "center"       # left | center | right
    valign: str = "middle"       # top | middle | bottom
    frame: bool = False          # 是否加细边框
    placeholder: bool = False    # 真实实验结果的占位槽（体检降为提示，不算错误）


@dataclass
class PanelEl:
    """带色条标题的分区面板（Stage 1 / Stage 2 这类布局的容器）。"""
    id: str
    rect: Rect
    title: str = ""
    variant: str = "primary"     # header 用 variant.stroke 色，body 用 variant.fill
    header_fill: str | None = None
    fill: str | None = None
    title_size: float | None = None
    header_h: float = 7.0        # 标题条高度 mm
    header_style: str = "banner"  # banner | smallcaps（顶会克制风分区标签）
    shadow: bool | None = None   # None=跟随 theme.default_shadow


@dataclass
class TokensEl:
    """一排小方块：token 序列 / query 序列 / 特征图条组（U-Net 画法）。"""
    id: str
    rect: Rect
    n: int = 8
    direction: str = "h"         # h | v
    variant: str = "secondary"
    colors: list[str] | None = None   # 逐格颜色，循环使用（画 masked token 等）
    gap: float = 0.7             # 格间距 mm
    sizes: list[float] | None = None  # 逐格交叉轴尺寸 mm（画特征图金字塔），居中对齐
    label: str = ""              # 序列名（如 Z_{s}），画在条带左/上侧


@dataclass
class MarkerEl:
    """内置矢量角标：fire(可训练) snow(冻结) lock check cross oplus otimes wifi。"""
    id: str
    at: tuple[float, float]      # 图标中心
    icon: str = "fire"
    size: float = 5.0            # mm
    color: str | None = None     # 覆盖默认配色


@dataclass
class NetworkEl:
    """迷你多层感知机示意（DDPG/MLP 模块内的小网络图）。"""
    id: str
    rect: Rect
    layers: list[int] = field(default_factory=lambda: [3, 4, 3])
    variant: str = "primary"
    node_fill: str | None = None   # 节点填充（默认白）
    color: str | None = None       # 节点描边与连线颜色（默认 variant.stroke）
    direction: str = "v"           # v: 层自上而下 | h: 层自左而右


@dataclass
class ScatterCluster:
    at: tuple[float, float]        # 椭圆中心（相对 rect 的 0~1 比例）
    rx: float = 0.2                # 半轴（相对 rect.w / rect.h 的比例）
    ry: float = 0.15
    rot: float = 0.0               # 旋转角度（度）
    n: int = 25
    color: str = "#3B6EA5"


@dataclass
class ScatterEl:
    """聚类散点示意（嵌入空间 / 数据聚类），可复现（seed 固定）。"""
    id: str
    rect: Rect
    clusters: list[ScatterCluster] = field(default_factory=list)
    seed: int = 42
    dot_r: float = 0.5             # 散点半径 mm
    outline: str = "dashed"        # dashed | solid | none


@dataclass
class BadgeEl:
    """编号圆点（步骤 1/2/3、序号 a/b/c）。"""
    id: str
    at: tuple[float, float]        # 圆心
    text: str = "1"
    size: float = 5.0              # 直径 mm
    color: str | None = None       # 圆底色（默认主题 primary 描边色）
    text_color: str = "#FFFFFF"


@dataclass
class TextEl:
    id: str
    at: tuple[float, float]
    text: str
    size: float = 7.0
    bold: bool = False
    italic: bool = False
    color: str | None = None
    anchor: str = "middle"       # start | middle | end
    max_w: float | None = None   # 给定则自动换行
    rotate: float = 0.0          # 绕 at 点旋转（度）；-90 即竖排（自下而上读）
    smallcaps: bool = False      # 大写 + letter-spacing 模拟 small-caps


@dataclass
class ArrowEl:
    id: str
    from_: str | tuple[float, float]
    to: str | tuple[float, float]
    route: str = "auto"          # auto | straight | hv | vh | z | zv | arc | avoid
    style: str = "solid"         # solid | dashed | dotted | block(空心/实心粗箭头)
    label: str = ""
    color: str | None = None
    head: str = "arrow"          # arrow | none
    bidir: bool = False
    label_offset: float = 1.4    # 标签相对线的垂直偏移 mm
    via: list[tuple[float, float]] = field(default_factory=list)  # 手动途经点（残差/绕线）
    width: float | None = None   # 线宽 mm 覆盖（粗箭头）；block 样式下为箭杆宽
    fill: str | None = None      # block 样式的填充色（默认白 → 空心箭头）
    bend: float = 0.25           # arc 路由的弯曲度（弦长比例，负值反侧）
    label_bg: bool = True        # 标签浅色胶囊底（默认开，与旧版白底一致）
    weight: str = "normal"       # thin | normal | heavy
    # label_pos: None=未写（avoid 默认 auto，其它保持旧落标）；"auto"=碰撞打分落标
    label_pos: str | None = None
    label_offset_explicit: bool = False  # YAML 是否显式写了 label_offset


@dataclass
class GroupEl:
    id: str
    members: list[str] = field(default_factory=list)
    rect: Rect | None = None     # 与 members 二选一
    label: str = ""
    pad: float = 2.5
    style: str = "dashed"        # dashed | solid
    fill: str | None = None
    color: str | None = None     # 描边/标签颜色覆盖（彩色虚线分区框）
    label_pos: str = "top"       # top(框外上方) | inside-top | inside-bottom
    label_size: float | None = None
    lw: float | None = None      # 线宽覆盖
    hatch: bool = False          # 斜线底纹（SVG pattern）
    shadow: bool | None = None


@dataclass
class PanelLabelEl:
    id: str
    at: tuple[float, float]
    text: str


# 单色缩略图词汇表（信息密度核心）
_SKETCH_KINDS = {
    "waveform", "bars", "heatmap", "scatter", "curve", "curve_desc",
    "grid", "matrix", "tree", "distribution", "spectrum", "layers",
    "nested", "dots_flow",
}


@dataclass
class SketchEl:
    """程序化单色缩略图（waveform/bars/heatmap/...），可复现（seed 固定）。"""
    id: str
    rect: Rect
    kind: str = "waveform"
    color: str | None = None
    stroke_color: str | None = None
    label: str = ""
    seed: int | None = None      # None → 由 id/坐标哈希


@dataclass
class LegendItem:
    swatch: str                  # box | line | dashed | arrow | dot
    label: str
    color: str = "#333333"


@dataclass
class LegendEl:
    """自动排版的色块+文字图例。"""
    id: str
    at: tuple[float, float]
    items: list[LegendItem] = field(default_factory=list)
    columns: int = 1
    frame: bool = True           # 浅底圆角外框
    fill: str | None = None
    stroke: str | None = None
    size: float = 6.0            # 文字 pt


Element = (BoxEl | AssetEl | TextEl | ArrowEl | GroupEl | PanelLabelEl | PanelEl
           | TokensEl | MarkerEl | NetworkEl | ScatterEl | BadgeEl | SketchEl | LegendEl)


@dataclass
class FigureSpec:
    path: Path
    width: float
    height: float
    dpi: int
    background: str
    font_scale: float
    theme_cfg: dict
    assets_dir: Path
    asset_requests: list[AssetRequest]
    elements: list[Element]

    def find(self, el_id: str) -> Element | None:
        for el in self.elements:
            if getattr(el, "id", None) == el_id:
                return el
        return None

    def resolve_asset(self, ref: str) -> Path:
        """素材引用 → 文件路径。裸 id 在 assets_dir 找 {id}.png，否则视为相对 spec 的路径。"""
        if re.fullmatch(r"[A-Za-z0-9_\-]+", ref):
            return self.assets_dir / f"{ref}.png"
        return (self.path.parent / ref).resolve()


_ALLOWED_KEYS = {
    "box": {"type", "id", "rect", "title", "body", "variant", "shape", "icon", "icon_h",
            "title_size", "body_size", "align", "valign", "gradient", "gradient_dir",
            "fill", "stroke", "text_color", "stack", "shadow", "accent", "header_fill",
            "sketch"},
    "asset": {"type", "id", "rect", "src", "caption", "halign", "valign", "frame", "placeholder"},
    "text": {"type", "id", "at", "text", "size", "bold", "italic", "color", "anchor",
             "max_w", "rotate", "smallcaps"},
    "arrow": {"type", "id", "from", "to", "route", "style", "label", "color",
              "head", "bidir", "label_offset", "label_pos", "via", "width", "fill",
              "bend", "label_bg", "weight"},
    "group": {"type", "id", "members", "rect", "label", "pad", "style", "fill",
              "color", "label_pos", "label_size", "lw", "hatch", "shadow"},
    "panel_label": {"type", "id", "at", "text"},
    "panel": {"type", "id", "rect", "title", "variant", "header_fill", "fill",
              "title_size", "header_h", "header_style", "shadow"},
    "tokens": {"type", "id", "rect", "n", "direction", "variant", "colors", "gap",
               "sizes", "label"},
    "marker": {"type", "id", "at", "icon", "size", "color"},
    "network": {"type", "id", "rect", "layers", "variant", "node_fill", "color", "direction"},
    "scatter": {"type", "id", "rect", "clusters", "seed", "dot_r", "outline"},
    "badge": {"type", "id", "at", "text", "size", "color", "text_color"},
    "sketch": {"type", "id", "rect", "kind", "color", "stroke_color", "label", "seed"},
    "legend": {"type", "id", "at", "items", "columns", "frame", "fill", "stroke", "size"},
}

_SHAPES = {"rect", "stadium", "diamond", "cylinder", "parallelogram", "hexagon", "ellipse", "trapezoid"}
_MARKER_ICONS = {"fire", "snow", "lock", "check", "cross", "oplus", "otimes", "wifi"}
_ROUTES = {"auto", "straight", "hv", "vh", "z", "zv", "arc", "avoid"}
_ARROW_LABEL_POS = {"auto"}
_ARROW_STYLES = {"solid", "dashed", "dotted", "block"}
_ARROW_WEIGHTS = {"thin", "normal", "heavy"}
_PANEL_HEADER_STYLES = {"banner", "smallcaps"}
_LEGEND_SWATCHES = {"box", "line", "dashed", "arrow", "dot"}
_BOX_ACCENTS = {"left", "top"}

_ANCHOR_RE = re.compile(r"^([A-Za-z0-9_\-]+)\.(left|right|top|bottom|center)(?:@([0-9.]+))?$")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")   # 裸节点 id：不写 .side，渲染时按几何自动选边


def _check_keys(d: dict, etype: str, ctx: str) -> None:
    extra = set(d) - _ALLOWED_KEYS[etype]
    if extra:
        raise SpecError(f"{ctx}: 未知字段 {sorted(extra)}（{etype} 可用字段: {sorted(_ALLOWED_KEYS[etype])}）")


def _point(v, ctx: str) -> tuple[float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        raise SpecError(f"{ctx}: 坐标必须是 [x, y]，得到 {v!r}")
    return float(v[0]), float(v[1])


def _endpoint(v, ctx: str) -> str | tuple[float, float]:
    if isinstance(v, str):
        if not (_ANCHOR_RE.match(v) or _BARE_ID_RE.match(v)):
            raise SpecError(
                f"{ctx}: 锚点格式应为 'nodeid'（自动选朝向对方的边）、'nodeid.side' 或 'nodeid.side@t'"
                f"（side ∈ left/right/top/bottom/center），得到 {v!r}"
            )
        return v
    return _point(v, ctx)


def load_spec(path: str | os.PathLike, text: str | None = None) -> FigureSpec:
    """加载 spec。给定 text 时解析该文本（studio 预览未保存的编辑用），
    但相对路径（assets_dir、素材引用）仍按 path 所在目录解析。"""
    p = Path(path).resolve()
    if text is None:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise SpecError("spec 顶层必须是 mapping")

    fig = raw.get("figure") or {}
    width = float(fig.get("width", 180))
    height = float(fig.get("height", 100))
    dpi = int(fig.get("dpi", 600))
    background = fig.get("background", "#FFFFFF")
    font_scale = float(fig.get("font_scale", 1.0))
    assets_dir = fig.get("assets_dir")
    assets_dir = (p.parent / assets_dir).resolve() if assets_dir else (p.parent / "assets")

    asset_requests = []
    for i, a in enumerate(raw.get("assets") or []):
        ctx = f"assets[{i}]"
        if "id" not in a or "prompt" not in a:
            raise SpecError(f"{ctx}: 需要 id 和 prompt")
        extra = set(a) - {"id", "prompt", "aspect", "candidates", "shadow"}
        if extra:
            raise SpecError(f"{ctx}: 未知字段 {sorted(extra)}")
        asset_requests.append(AssetRequest(
            id=str(a["id"]), prompt=str(a["prompt"]),
            aspect=str(a.get("aspect", "1:1")),
            candidates=int(a.get("candidates", 3)),
            shadow=str(a.get("shadow", "keep")),
        ))

    elements: list[Element] = []
    ids: set[str] = set()
    auto_n = 0

    def _auto_id(prefix: str) -> str:
        nonlocal auto_n
        auto_n += 1
        return f"_{prefix}{auto_n}"

    for i, e in enumerate(raw.get("elements") or []):
        etype = e.get("type")
        ctx = f"elements[{i}]({etype})"
        if etype not in _ALLOWED_KEYS:
            raise SpecError(f"{ctx}: 未知元素类型，可用: {sorted(_ALLOWED_KEYS)}")
        _check_keys(e, etype, ctx)
        eid = str(e.get("id") or _auto_id(etype))
        if eid in ids:
            raise SpecError(f"{ctx}: id '{eid}' 重复")
        ids.add(eid)

        if etype == "box":
            if "rect" not in e:
                raise SpecError(f"{ctx}: box 需要 rect")
            shape = str(e.get("shape", "rect"))
            if shape not in _SHAPES:
                raise SpecError(f"{ctx}: 未知 shape '{shape}'（可选 {sorted(_SHAPES)}）")
            gradient = e.get("gradient")
            if gradient is not None:
                if not (isinstance(gradient, (list, tuple)) and len(gradient) == 2):
                    raise SpecError(f"{ctx}: gradient 必须是两个颜色 [c1, c2]")
                gradient = [str(c) for c in gradient]
            accent = e.get("accent")
            if accent is not None:
                accent = str(accent)
                if accent not in _BOX_ACCENTS:
                    raise SpecError(f"{ctx}: accent 必须是 left/top，得到 {accent!r}")
            sketch = e.get("sketch")
            if sketch is not None:
                sketch = str(sketch)
                if sketch not in _SKETCH_KINDS:
                    raise SpecError(f"{ctx}: 未知 sketch kind '{sketch}'（可选 {sorted(_SKETCH_KINDS)}）")
            shadow = e.get("shadow")
            elements.append(BoxEl(
                id=eid, rect=_rect(e["rect"], ctx),
                title=str(e.get("title", "")), body=str(e.get("body", "")),
                variant=str(e.get("variant", "primary")), shape=shape,
                icon=e.get("icon"), icon_h=float(e.get("icon_h", 10.0)),
                title_size=e.get("title_size"), body_size=e.get("body_size"),
                align=str(e.get("align", "center")), valign=str(e.get("valign", "middle")),
                gradient=gradient, gradient_dir=str(e.get("gradient_dir", "h")),
                fill=e.get("fill"), stroke=e.get("stroke"),
                text_color=e.get("text_color"), stack=int(e.get("stack", 0)),
                shadow=bool(shadow) if shadow is not None else None,
                accent=accent, header_fill=bool(e.get("header_fill", False)),
                sketch=sketch,
            ))
        elif etype == "asset":
            if "rect" not in e or "src" not in e:
                raise SpecError(f"{ctx}: asset 需要 rect 和 src")
            elements.append(AssetEl(
                id=eid, rect=_rect(e["rect"], ctx), src=str(e["src"]),
                caption=str(e.get("caption", "")),
                halign=str(e.get("halign", "center")), valign=str(e.get("valign", "middle")),
                frame=bool(e.get("frame", False)),
                placeholder=bool(e.get("placeholder", False)),
            ))
        elif etype == "panel":
            if "rect" not in e:
                raise SpecError(f"{ctx}: panel 需要 rect")
            header_style = str(e.get("header_style", "banner"))
            if header_style not in _PANEL_HEADER_STYLES:
                raise SpecError(f"{ctx}: header_style 必须是 banner/smallcaps")
            shadow = e.get("shadow")
            elements.append(PanelEl(
                id=eid, rect=_rect(e["rect"], ctx), title=str(e.get("title", "")),
                variant=str(e.get("variant", "primary")),
                header_fill=e.get("header_fill"), fill=e.get("fill"),
                title_size=e.get("title_size"),
                header_h=float(e.get("header_h", 7.0)),
                header_style=header_style,
                shadow=bool(shadow) if shadow is not None else None,
            ))
        elif etype == "tokens":
            if "rect" not in e:
                raise SpecError(f"{ctx}: tokens 需要 rect")
            n = int(e.get("n", 8))
            if n < 1:
                raise SpecError(f"{ctx}: tokens.n 必须 ≥1")
            direction = str(e.get("direction", "h"))
            if direction not in ("h", "v"):
                raise SpecError(f"{ctx}: tokens.direction 必须是 h 或 v")
            colors = [str(c) for c in e["colors"]] if e.get("colors") else None
            sizes = [float(s) for s in e["sizes"]] if e.get("sizes") else None
            elements.append(TokensEl(
                id=eid, rect=_rect(e["rect"], ctx), n=n, direction=direction,
                variant=str(e.get("variant", "secondary")), colors=colors,
                gap=float(e.get("gap", 0.7)), sizes=sizes, label=str(e.get("label", "")),
            ))
        elif etype == "marker":
            icon = str(e.get("icon", "fire"))
            if icon not in _MARKER_ICONS:
                raise SpecError(f"{ctx}: 未知 icon '{icon}'（可选 {sorted(_MARKER_ICONS)}）")
            elements.append(MarkerEl(
                id=eid, at=_point(e.get("at"), ctx), icon=icon,
                size=float(e.get("size", 5.0)), color=e.get("color"),
            ))
        elif etype == "text":
            elements.append(TextEl(
                id=eid, at=_point(e.get("at"), ctx), text=str(e.get("text", "")),
                size=float(e.get("size", 7.0)), bold=bool(e.get("bold", False)),
                italic=bool(e.get("italic", False)),
                color=e.get("color"), anchor=str(e.get("anchor", "middle")),
                max_w=float(e["max_w"]) if e.get("max_w") is not None else None,
                rotate=float(e.get("rotate", 0.0)),
                smallcaps=bool(e.get("smallcaps", False)),
            ))
        elif etype == "arrow":
            if "from" not in e or "to" not in e:
                raise SpecError(f"{ctx}: arrow 需要 from 和 to")
            route = str(e.get("route", "auto"))
            if route not in _ROUTES:
                raise SpecError(f"{ctx}: 未知 route '{route}'（可选 {sorted(_ROUTES)}）")
            style = str(e.get("style", "solid"))
            if style not in _ARROW_STYLES:
                raise SpecError(f"{ctx}: 未知 style '{style}'（可选 {sorted(_ARROW_STYLES)}）")
            weight = str(e.get("weight", "normal"))
            if weight not in _ARROW_WEIGHTS:
                raise SpecError(f"{ctx}: weight 必须是 thin/normal/heavy")
            via = [_point(p, f"{ctx}.via[{k}]") for k, p in enumerate(e.get("via") or [])]
            label_offset_explicit = "label_offset" in e
            label_pos = e.get("label_pos", None)
            if label_pos is not None:
                label_pos = str(label_pos)
                if label_pos not in _ARROW_LABEL_POS:
                    raise SpecError(
                        f"{ctx}: arrow.label_pos 必须是 {sorted(_ARROW_LABEL_POS)}（或省略）")
            elements.append(ArrowEl(
                id=eid, from_=_endpoint(e["from"], ctx), to=_endpoint(e["to"], ctx),
                route=route, style=style,
                label=str(e.get("label", "")), color=e.get("color"),
                head=str(e.get("head", "arrow")), bidir=bool(e.get("bidir", False)),
                label_offset=float(e.get("label_offset", 1.4)), via=via,
                width=float(e["width"]) if e.get("width") is not None else None,
                fill=e.get("fill"), bend=float(e.get("bend", 0.25)),
                label_bg=bool(e.get("label_bg", True)), weight=weight,
                label_pos=label_pos,
                label_offset_explicit=label_offset_explicit,
            ))
        elif etype == "group":
            members = [str(m) for m in (e.get("members") or [])]
            rect = _rect(e["rect"], ctx) if e.get("rect") else None
            if not members and rect is None:
                raise SpecError(f"{ctx}: group 需要 members 或 rect")
            label_pos = str(e.get("label_pos", "top"))
            if label_pos not in ("top", "inside-top", "inside-bottom"):
                raise SpecError(f"{ctx}: label_pos 必须是 top/inside-top/inside-bottom")
            shadow = e.get("shadow")
            elements.append(GroupEl(
                id=eid, members=members, rect=rect, label=str(e.get("label", "")),
                pad=float(e.get("pad", 2.5)), style=str(e.get("style", "dashed")),
                fill=e.get("fill"), color=e.get("color"), label_pos=label_pos,
                label_size=e.get("label_size"),
                lw=float(e["lw"]) if e.get("lw") is not None else None,
                hatch=bool(e.get("hatch", False)),
                shadow=bool(shadow) if shadow is not None else None,
            ))
        elif etype == "sketch":
            if "rect" not in e:
                raise SpecError(f"{ctx}: sketch 需要 rect")
            kind = str(e.get("kind", "waveform"))
            if kind not in _SKETCH_KINDS:
                raise SpecError(f"{ctx}: 未知 sketch kind '{kind}'（可选 {sorted(_SKETCH_KINDS)}）")
            elements.append(SketchEl(
                id=eid, rect=_rect(e["rect"], ctx), kind=kind,
                color=e.get("color"), stroke_color=e.get("stroke_color"),
                label=str(e.get("label", "")),
                seed=int(e["seed"]) if e.get("seed") is not None else None,
            ))
        elif etype == "legend":
            items_raw = e.get("items") or []
            if not items_raw:
                raise SpecError(f"{ctx}: legend 需要非空 items")
            items = []
            for k, it in enumerate(items_raw):
                ictx = f"{ctx}.items[{k}]"
                if not isinstance(it, dict):
                    raise SpecError(f"{ictx}: 必须是 mapping")
                extra = set(it) - {"swatch", "color", "label"}
                if extra:
                    raise SpecError(f"{ictx}: 未知字段 {sorted(extra)}")
                sw = str(it.get("swatch", "box"))
                if sw not in _LEGEND_SWATCHES:
                    raise SpecError(f"{ictx}: swatch 必须是 {sorted(_LEGEND_SWATCHES)}")
                items.append(LegendItem(
                    swatch=sw, label=str(it.get("label", "")),
                    color=str(it.get("color", "#333333")),
                ))
            elements.append(LegendEl(
                id=eid, at=_point(e.get("at"), ctx), items=items,
                columns=int(e.get("columns", 1)),
                frame=bool(e.get("frame", True)),
                fill=e.get("fill"), stroke=e.get("stroke"),
                size=float(e.get("size", 6.0)),
            ))
        elif etype == "network":
            if "rect" not in e:
                raise SpecError(f"{ctx}: network 需要 rect")
            layers = [int(v) for v in (e.get("layers") or [3, 4, 3])]
            if not layers or any(v < 1 for v in layers) or len(layers) < 2:
                raise SpecError(f"{ctx}: layers 必须是 ≥2 层、每层 ≥1 节点的整数列表")
            direction = str(e.get("direction", "v"))
            if direction not in ("h", "v"):
                raise SpecError(f"{ctx}: network.direction 必须是 h 或 v")
            elements.append(NetworkEl(
                id=eid, rect=_rect(e["rect"], ctx), layers=layers,
                variant=str(e.get("variant", "primary")),
                node_fill=e.get("node_fill"), color=e.get("color"), direction=direction,
            ))
        elif etype == "scatter":
            if "rect" not in e or not e.get("clusters"):
                raise SpecError(f"{ctx}: scatter 需要 rect 和 clusters")
            clusters = []
            for k, c in enumerate(e["clusters"]):
                cctx = f"{ctx}.clusters[{k}]"
                extra = set(c) - {"at", "rx", "ry", "rot", "n", "color"}
                if extra:
                    raise SpecError(f"{cctx}: 未知字段 {sorted(extra)}")
                clusters.append(ScatterCluster(
                    at=_point(c.get("at", [0.5, 0.5]), cctx),
                    rx=float(c.get("rx", 0.2)), ry=float(c.get("ry", 0.15)),
                    rot=float(c.get("rot", 0.0)), n=int(c.get("n", 25)),
                    color=str(c.get("color", "#3B6EA5")),
                ))
            outline = str(e.get("outline", "dashed"))
            if outline not in ("dashed", "solid", "none"):
                raise SpecError(f"{ctx}: scatter.outline 必须是 dashed/solid/none")
            elements.append(ScatterEl(
                id=eid, rect=_rect(e["rect"], ctx), clusters=clusters,
                seed=int(e.get("seed", 42)), dot_r=float(e.get("dot_r", 0.5)),
                outline=outline,
            ))
        elif etype == "badge":
            elements.append(BadgeEl(
                id=eid, at=_point(e.get("at"), ctx), text=str(e.get("text", "1")),
                size=float(e.get("size", 5.0)), color=e.get("color"),
                text_color=str(e.get("text_color", "#FFFFFF")),
            ))
        elif etype == "panel_label":
            elements.append(PanelLabelEl(id=eid, at=_point(e.get("at"), ctx), text=str(e.get("text", "a"))))

    theme_cfg = raw.get("theme") or {}
    if isinstance(theme_cfg, str):
        theme_cfg = {"preset": theme_cfg}

    spec = FigureSpec(
        path=p, width=width, height=height, dpi=dpi, background=background,
        font_scale=font_scale, theme_cfg=theme_cfg,
        assets_dir=assets_dir, asset_requests=asset_requests, elements=elements,
    )
    _validate_refs(spec)
    return spec


def _validate_refs(spec: FigureSpec) -> None:
    # panel/tokens/network/scatter/sketch 也可作为箭头锚点与 group 成员
    node_ids = {el.id for el in spec.elements
                if isinstance(el, (BoxEl, AssetEl, PanelEl, TokensEl, NetworkEl, ScatterEl, SketchEl))}
    # group（含 members 推导矩形）也可作为箭头锚点：框对框连线
    anchor_ids = node_ids | {el.id for el in spec.elements if isinstance(el, GroupEl)}
    for el in spec.elements:
        if isinstance(el, ArrowEl):
            for ep, name in ((el.from_, "from"), (el.to, "to")):
                if isinstance(ep, str):
                    m = _ANCHOR_RE.match(ep)
                    node = m.group(1) if m else ep
                    if node not in anchor_ids:
                        raise SpecError(f"arrow '{el.id}' 的 {name} 引用了不存在的节点 '{node}'")
        elif isinstance(el, GroupEl):
            for m in el.members:
                if m not in node_ids:
                    raise SpecError(f"group '{el.id}' 的成员 '{m}' 不存在（成员必须是 box/asset/panel/tokens/network/scatter/sketch 的 id）")


def parse_anchor(s: str) -> tuple[str, str | None, float]:
    """解析锚点：'id.side[@t]' → (id, side, t)；裸 'id' → (id, None, 0.5)（None 表示自动选边）。"""
    m = _ANCHOR_RE.match(s)
    if m:
        return m.group(1), m.group(2), float(m.group(3)) if m.group(3) else 0.5
    return s, None, 0.5
