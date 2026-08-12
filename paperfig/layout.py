"""Flex 布局树：row/col/grid 递归求解，物化为绝对坐标 YAML。

LLM 友好的结构关系写法；求解后把 rect 写回 elements，去掉 layout 节，
得到与现网渲染器 100% 兼容的经典 spec。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

import yaml

from .spec import SpecError


_JUSTIFY = {"start", "center", "end", "space-between"}
_ALIGN = {"start", "center", "end", "stretch"}
_KINDS = {"row", "col", "grid"}

# 默认间距（mm）——论文图紧凑但不挤
DEFAULT_GAP = 4.0
DEFAULT_PAD = 0.0
COORD_QUANTUM = 0.01  # 物化量化，保证幂等


class LayoutError(SpecError):
    """布局求解失败（塞不下 / 缺尺寸 / 未知引用）。"""


def _q(v: float) -> float:
    return round(v / COORD_QUANTUM) * COORD_QUANTUM


def _parse_pad(v: Any, ctx: str) -> tuple[float, float, float, float]:
    """→ (top, right, bottom, left)。"""
    if v is None:
        return (0.0, 0.0, 0.0, 0.0)
    if isinstance(v, (int, float)):
        p = float(v)
        return (p, p, p, p)
    if isinstance(v, (list, tuple)):
        if len(v) == 2:
            y, x = float(v[0]), float(v[1])
            return (y, x, y, x)
        if len(v) == 4:
            return tuple(float(x) for x in v)  # type: ignore[return-value]
    raise LayoutError(f"{ctx}: pad 应为标量、[y,x] 或 [t,r,b,l]，得到 {v!r}")


@dataclass
class FlexNode:
    kind: str                          # row | col | grid | leaf
    id: str | None = None
    ref: str | None = None             # leaf → elements id
    w: float | None = None             # 固定宽 mm
    h: float | None = None
    flex: float | None = None          # 主轴权重
    gap: float = DEFAULT_GAP
    pad: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    justify: str = "start"
    align: str = "center"
    columns: int = 2                   # grid only
    children: list["FlexNode"] = field(default_factory=list)
    # 容器可视（panel/group）——求解后把 rect 写回同 id 元素
    visual: dict[str, Any] = field(default_factory=dict)
    # 求解结果
    x: float = 0.0
    y: float = 0.0
    solved_w: float = 0.0
    solved_h: float = 0.0

    @property
    def is_leaf(self) -> bool:
        return self.kind == "leaf"


def _parse_node(raw: Any, ctx: str) -> FlexNode:
    if not isinstance(raw, dict):
        raise LayoutError(f"{ctx}: 布局节点必须是 mapping")
    # leaf: {ref, w?, h?, flex?}
    if "ref" in raw:
        extra = set(raw) - {"ref", "w", "h", "flex", "id"}
        if extra:
            raise LayoutError(f"{ctx}: leaf 未知字段 {sorted(extra)}")
        ref = str(raw["ref"])
        w = float(raw["w"]) if raw.get("w") is not None else None
        h = float(raw["h"]) if raw.get("h") is not None else None
        flex = float(raw["flex"]) if raw.get("flex") is not None else None
        if w is None and h is None and flex is None:
            raise LayoutError(f"{ctx}: leaf '{ref}' 需要 w/h 或 flex")
        return FlexNode(
            kind="leaf", id=str(raw["id"]) if raw.get("id") else ref,
            ref=ref, w=w, h=h, flex=flex,
        )

    kind = str(raw.get("kind", raw.get("type", "")))
    # type: panel/group 是可视容器，kind 另写；若只写 type:row 则 kind=row
    visual_type = None
    if kind in ("panel", "group"):
        visual_type = kind
        kind = str(raw.get("kind", "col" if visual_type == "panel" else "row"))
    if kind not in _KINDS:
        # 允许 type: panel + 默认 col
        if raw.get("type") in ("panel", "group"):
            visual_type = str(raw["type"])
            kind = str(raw.get("kind", "col"))
        else:
            raise LayoutError(
                f"{ctx}: 需要 kind: row|col|grid 或 leaf {{ref:...}}，得到 kind/type={kind!r}"
            )
    if kind not in _KINDS:
        raise LayoutError(f"{ctx}: 未知 kind '{kind}'（可选 {sorted(_KINDS)}）")

    justify = str(raw.get("justify", "start"))
    align = str(raw.get("align", "center"))
    if justify not in _JUSTIFY:
        raise LayoutError(f"{ctx}: justify 必须是 {sorted(_JUSTIFY)}")
    if align not in _ALIGN:
        raise LayoutError(f"{ctx}: align 必须是 {sorted(_ALIGN)}")

    children_raw = raw.get("children") or []
    if not isinstance(children_raw, list) or not children_raw:
        raise LayoutError(f"{ctx}: 容器需要非空 children")

    visual: dict[str, Any] = {}
    if visual_type or raw.get("type") in ("panel", "group"):
        visual["type"] = visual_type or str(raw["type"])
        for k in ("title", "label", "variant", "fill", "header_style", "header_h",
                  "title_size", "header_fill", "style", "color", "pad", "shadow",
                  "label_pos", "label_size", "ghost", "plate"):
            if k in raw and k != "pad":
                visual[k] = raw[k]
        # panel 的内容 pad 与布局 pad 分开：layout pad 用 pad，视觉字段已拷
    elif "title" in raw or "label" in raw:
        # 裸标题 → 默认当 panel
        visual["type"] = "panel"
        for k in ("title", "variant", "fill", "header_style", "header_h", "title_size"):
            if k in raw:
                visual[k] = raw[k]

    node = FlexNode(
        kind=kind,
        id=str(raw["id"]) if raw.get("id") else None,
        w=float(raw["w"]) if raw.get("w") is not None else None,
        h=float(raw["h"]) if raw.get("h") is not None else None,
        flex=float(raw["flex"]) if raw.get("flex") is not None else None,
        gap=float(raw.get("gap", DEFAULT_GAP)),
        pad=_parse_pad(raw.get("pad", DEFAULT_PAD), ctx),
        justify=justify,
        align=align,
        columns=int(raw.get("columns", raw.get("cols", 2))),
        visual=visual,
    )
    if node.columns < 1:
        raise LayoutError(f"{ctx}: columns 必须 ≥1")
    for i, ch in enumerate(children_raw):
        node.children.append(_parse_node(ch, f"{ctx}.children[{i}]"))
    return node


def parse_layout(raw: dict[str, Any]) -> FlexNode:
    if not isinstance(raw, dict):
        raise LayoutError("layout: 必须是 mapping")
    return _parse_node(raw, "layout")


def _main_cross(kind: str) -> tuple[str, str]:
    if kind == "row":
        return "w", "h"
    return "h", "w"  # col / grid handled separately


def measure(node: FlexNode) -> tuple[float, float]:
    """自底向上计算固有尺寸（忽略 flex 拉伸，flex 子项按 0 固有主轴计）。"""
    if node.is_leaf:
        w = node.w if node.w is not None else 0.0
        h = node.h if node.h is not None else 0.0
        # flex-only leaf：固有主轴 0，交叉轴必须给定
        if node.w is None and node.h is None:
            raise LayoutError(
                f"layout leaf '{node.ref}': flex 子项至少要固定交叉轴尺寸（row 要 h，col 要 w）"
            )
        node.solved_w, node.solved_h = w, h
        return w, h

    pt, pr, pb, pl = node.pad
    sizes = [measure(c) for c in node.children]
    gap = node.gap

    if node.kind == "row":
        content_w = sum(s[0] for s in sizes) + gap * max(len(sizes) - 1, 0)
        content_h = max((s[1] for s in sizes), default=0.0)
    elif node.kind == "col":
        content_w = max((s[0] for s in sizes), default=0.0)
        content_h = sum(s[1] for s in sizes) + gap * max(len(sizes) - 1, 0)
    else:  # grid
        cols = node.columns
        rows_n = math.ceil(len(sizes) / cols) if sizes else 0
        col_ws = [0.0] * cols
        row_hs = [0.0] * rows_n
        for i, (cw, ch) in enumerate(sizes):
            r, c = divmod(i, cols)
            col_ws[c] = max(col_ws[c], cw)
            row_hs[r] = max(row_hs[r], ch)
        content_w = sum(col_ws) + gap * max(cols - 1, 0)
        content_h = sum(row_hs) + gap * max(rows_n - 1, 0)
        node._grid_col_ws = col_ws  # type: ignore[attr-defined]
        node._grid_row_hs = row_hs  # type: ignore[attr-defined]

    w = content_w + pl + pr
    h = content_h + pt + pb
    if node.w is not None:
        w = max(w, node.w)
    if node.h is not None:
        h = max(h, node.h)
    node.solved_w, node.solved_h = w, h
    return w, h


def _distribute_main(
    kind: str,
    children: list[FlexNode],
    inner_main: float,
    gap: float,
    justify: str,
    ctx: str,
) -> list[float]:
    """返回每个 child 的主轴尺寸（已含 flex 分配）。"""
    n = len(children)
    if n == 0:
        return []
    fixed_sum = 0.0
    flex_total = 0.0
    mains: list[float | None] = []
    for c in children:
        if kind == "row":
            if c.w is not None:
                mains.append(c.solved_w if c.solved_w else c.w)
                fixed_sum += mains[-1]  # type: ignore[operator]
            elif c.flex is not None:
                mains.append(None)
                flex_total += max(c.flex, 0.0)
            else:
                mains.append(c.solved_w)
                fixed_sum += c.solved_w
        else:
            if c.h is not None and c.flex is None:
                mains.append(c.solved_h if c.solved_h else c.h)
                fixed_sum += mains[-1]  # type: ignore[operator]
            elif c.flex is not None:
                mains.append(None)
                flex_total += max(c.flex, 0.0)
            else:
                mains.append(c.solved_h)
                fixed_sum += c.solved_h

    gaps = gap * max(n - 1, 0)
    remaining = inner_main - fixed_sum - gaps
    if remaining < -0.05:
        raise LayoutError(
            f"{ctx}: 主轴塞不下——"
            f"内容固有 {fixed_sum:.2f}mm + gap {gaps:.2f}mm > 可用 {inner_main:.2f}mm"
            f"（容器 kind={kind}）"
        )
    remaining = max(remaining, 0.0)

    out: list[float] = []
    for i, m in enumerate(mains):
        if m is not None:
            out.append(m)
        else:
            if flex_total <= 0:
                raise LayoutError(f"{ctx}: children[{i}] 声明了 flex 但权重和为 0")
            fl = children[i].flex or 0.0
            out.append(remaining * (fl / flex_total) if remaining > 0 else 0.0)

    used = sum(out) + gaps
    free = inner_main - used
    return out  # free 由 place 的 justify 消化


def place(node: FlexNode, x: float, y: float, w: float, h: float, ctx: str = "layout") -> None:
    """在给定框内摆放 node（可大于固有尺寸以消化 flex/justify）。"""
    node.x, node.y = x, y
    node.solved_w, node.solved_h = w, h
    if node.is_leaf:
        return

    pt, pr, pb, pl = node.pad
    inner_x, inner_y = x + pl, y + pt
    inner_w, inner_h = w - pl - pr, h - pt - pb
    if inner_w < -0.05 or inner_h < -0.05:
        raise LayoutError(
            f"{ctx}: pad 过大——容器 {w:.2f}×{h:.2f}mm 无法容纳 pad {node.pad}"
        )
    inner_w, inner_h = max(inner_w, 0.0), max(inner_h, 0.0)
    gap = node.gap
    children = node.children

    if node.kind == "grid":
        _place_grid(node, inner_x, inner_y, inner_w, inner_h, ctx)
        return

    main_is_x = node.kind == "row"
    inner_main = inner_w if main_is_x else inner_h
    inner_cross = inner_h if main_is_x else inner_w
    mains = _distribute_main(node.kind, children, inner_main, gap, node.justify, ctx)
    gaps_total = gap * max(len(children) - 1, 0)
    used = sum(mains) + gaps_total
    free = max(inner_main - used, 0.0)

    # justify → 起始偏移与间距
    if node.justify == "start":
        cursor = 0.0
        gap_extra = 0.0
    elif node.justify == "center":
        cursor = free / 2
        gap_extra = 0.0
    elif node.justify == "end":
        cursor = free
        gap_extra = 0.0
    else:  # space-between
        cursor = 0.0
        gap_extra = free / max(len(children) - 1, 1) if len(children) > 1 else 0.0
        if len(children) == 1:
            cursor = free / 2

    for i, child in enumerate(children):
        main_size = mains[i]
        # 交叉轴
        if main_is_x:
            cross_size = child.h if child.h is not None else child.solved_h
            if node.align == "stretch" and child.h is None:
                cross_size = inner_cross
            elif node.align == "stretch":
                cross_size = child.h if child.h is not None else inner_cross
            cross_size = cross_size or child.solved_h
            if node.align == "start":
                cy = inner_y
            elif node.align == "end":
                cy = inner_y + inner_cross - cross_size
            else:  # center / stretch
                cy = inner_y + (inner_cross - cross_size) / 2 if node.align != "stretch" else inner_y
                if node.align == "stretch":
                    cross_size = inner_cross
                    cy = inner_y
            cx = inner_x + cursor
            place(child, cx, cy, main_size, cross_size, f"{ctx}/{child.id or child.ref or i}")
        else:
            cross_size = child.w if child.w is not None else child.solved_w
            if node.align == "stretch":
                cross_size = inner_cross if child.w is None else child.w
                # stretch 填满交叉轴
                if child.w is None:
                    cross_size = inner_cross
            cross_size = cross_size or child.solved_w
            if node.align == "start":
                cx = inner_x
            elif node.align == "end":
                cx = inner_x + inner_cross - cross_size
            elif node.align == "stretch":
                cx = inner_x
                cross_size = inner_cross
            else:
                cx = inner_x + (inner_cross - cross_size) / 2
            cy = inner_y + cursor
            place(child, cx, cy, cross_size, main_size, f"{ctx}/{child.id or child.ref or i}")
        cursor += main_size + gap + gap_extra


def _place_grid(node: FlexNode, x: float, y: float, w: float, h: float, ctx: str) -> None:
    cols = node.columns
    children = node.children
    n = len(children)
    rows_n = math.ceil(n / cols) if n else 0
    if rows_n == 0:
        return
    gap = node.gap
    # 重新按可用空间分配列宽/行高：固定尺寸优先，其余均分
    col_fixed = [0.0] * cols
    row_fixed = [0.0] * rows_n
    for i, c in enumerate(children):
        r, col = divmod(i, cols)
        if c.w is not None:
            col_fixed[col] = max(col_fixed[col], c.w)
        else:
            col_fixed[col] = max(col_fixed[col], c.solved_w)
        if c.h is not None:
            row_fixed[r] = max(row_fixed[r], c.h)
        else:
            row_fixed[r] = max(row_fixed[r], c.solved_h)

    gap_w = gap * max(cols - 1, 0)
    gap_h = gap * max(rows_n - 1, 0)
    if sum(col_fixed) + gap_w > w + 0.05:
        raise LayoutError(
            f"{ctx}: grid 列宽合计 {sum(col_fixed):.2f}+gap {gap_w:.2f} > 可用宽 {w:.2f}mm"
        )
    if sum(row_fixed) + gap_h > h + 0.05:
        raise LayoutError(
            f"{ctx}: grid 行高合计 {sum(row_fixed):.2f}+gap {gap_h:.2f} > 可用高 {h:.2f}mm"
        )
    # 剩余空间均分到各列/行
    free_w = w - sum(col_fixed) - gap_w
    free_h = h - sum(row_fixed) - gap_h
    col_ws = [cw + free_w / cols for cw in col_fixed]
    row_hs = [rh + free_h / rows_n for rh in row_fixed]

    ys = [y]
    for rh in row_hs[:-1]:
        ys.append(ys[-1] + rh + gap)
    xs = [x]
    for cw in col_ws[:-1]:
        xs.append(xs[-1] + cw + gap)

    for i, child in enumerate(children):
        r, col = divmod(i, cols)
        cell_w, cell_h = col_ws[col], row_hs[r]
        cw = child.w if child.w is not None else child.solved_w
        ch = child.h if child.h is not None else child.solved_h
        if node.align == "stretch":
            cw, ch = cell_w, cell_h
            cx, cy = xs[col], ys[r]
        else:
            if node.justify == "start":
                cx = xs[col]
            elif node.justify == "end":
                cx = xs[col] + cell_w - cw
            else:
                cx = xs[col] + (cell_w - cw) / 2
            if node.align == "start":
                cy = ys[r]
            elif node.align == "end":
                cy = ys[r] + cell_h - ch
            else:
                cy = ys[r] + (cell_h - ch) / 2
        place(child, cx, cy, cw, ch, f"{ctx}/r{r}c{col}")


def collect_rects(node: FlexNode, out: dict[str, list[float]]) -> None:
    """收集 leaf ref → [x,y,w,h]，以及带 id 的容器框。"""
    if node.is_leaf and node.ref:
        out[node.ref] = [_q(node.x), _q(node.y), _q(node.solved_w), _q(node.solved_h)]
    elif node.id and not node.is_leaf:
        out[node.id] = [_q(node.x), _q(node.y), _q(node.solved_w), _q(node.solved_h)]
    for c in node.children:
        collect_rects(c, out)


def collect_visual_containers(node: FlexNode, out: list[FlexNode]) -> None:
    if not node.is_leaf and node.visual.get("type") and node.id:
        out.append(node)
    for c in node.children:
        collect_visual_containers(c, out)


def solve_layout(
    root: FlexNode,
    canvas_w: float | None,
    canvas_h: float | None,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float, dict[str, list[float]]]:
    """求解布局。返回 (width, height, rects)。

    canvas_w/h 为 None 时由内容撑开。
    """
    mw, mh = measure(root)
    ox, oy = origin
    if canvas_w is None and canvas_h is None:
        place(root, ox, oy, mw, mh, "layout")
        return _q(mw + ox), _q(mh + oy), _collect(root)
    fw = canvas_w if canvas_w is not None else mw
    fh = canvas_h if canvas_h is not None else mh
    if canvas_w is not None and mw > canvas_w + 0.05:
        raise LayoutError(
            f"layout: 内容固有宽 {mw:.2f}mm > figure.width {canvas_w:.2f}mm"
        )
    if canvas_h is not None and mh > canvas_h + 0.05:
        raise LayoutError(
            f"layout: 内容固有高 {mh:.2f}mm > figure.height {canvas_h:.2f}mm"
        )
    # 画布大于内容时，根节点吃满画布（便于 justify/align/flex）
    place(root, ox, oy, fw - ox if canvas_w else mw, fh - oy if canvas_h else mh, "layout")
    # 若只给了单边画布，另一边用内容
    out_w = canvas_w if canvas_w is not None else _q(root.solved_w + ox)
    out_h = canvas_h if canvas_h is not None else _q(root.solved_h + oy)
    return out_w, out_h, _collect(root)


def _collect(root: FlexNode) -> dict[str, list[float]]:
    rects: dict[str, list[float]] = {}
    collect_rects(root, rects)
    return rects


def document_has_layout(raw: dict[str, Any]) -> bool:
    return isinstance(raw.get("layout"), dict)


def resolve_document(raw: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """结构化 spec → 绝对坐标 spec（深拷贝）。无 layout 时原样返回。"""
    if not isinstance(raw, dict):
        raise LayoutError("spec 顶层必须是 mapping")
    if not document_has_layout(raw):
        return copy.deepcopy(raw)

    out = copy.deepcopy(raw)
    root = parse_layout(out["layout"])
    fig = out.setdefault("figure", {})
    if not isinstance(fig, dict):
        raise LayoutError("figure: 必须是 mapping")

    # width/height 可缺省
    cw = fig.get("width", None)
    ch = fig.get("height", None)
    cw_f = float(cw) if cw is not None else None
    ch_f = float(ch) if ch is not None else None

    width, height, rects = solve_layout(root, cw_f, ch_f)
    fig["width"] = width
    fig["height"] = height

    elements = out.get("elements")
    if not isinstance(elements, list):
        raise LayoutError("elements: 必须是 list")

    # 校验 leaf 引用
    el_ids = {str(e.get("id")) for e in elements if isinstance(e, dict) and e.get("id")}
    for ref, _ in rects.items():
        pass
    leaf_refs: set[str] = set()

    def _walk_refs(n: FlexNode) -> None:
        if n.is_leaf and n.ref:
            leaf_refs.add(n.ref)
        for c in n.children:
            _walk_refs(c)

    _walk_refs(root)
    missing = sorted(leaf_refs - el_ids)
    if missing:
        raise LayoutError(f"layout: 引用了不存在的元素 {missing}")

    # 写回几何：box/panel 等用 rect；legend/text/badge/marker 用 at（取左上角）
    _AT_TYPES = {"legend", "text", "badge", "marker", "panel_label"}
    by_id = {str(e["id"]): e for e in elements if isinstance(e, dict) and e.get("id")}
    for ref, rect in rects.items():
        el = by_id.get(ref)
        if el is None:
            # 容器 id 可能尚无对应元素 → 下面补 panel
            continue
        et = str(el.get("type", ""))
        if et in _AT_TYPES:
            if el.get("at") is not None and not force:
                continue
            el["at"] = [rect[0], rect[1]]
        else:
            if el.get("rect") is not None and not force:
                continue
            el["rect"] = rect

    # 布局树中的可视容器 → 若不存在则插入 panel/group
    visuals: list[FlexNode] = []
    collect_visual_containers(root, visuals)
    insert_at = 0
    for vn in visuals:
        assert vn.id
        rect = rects.get(vn.id)
        if rect is None:
            continue
        if vn.id in by_id:
            el = by_id[vn.id]
            if el.get("rect") is None or force:
                el["rect"] = rect
            # 补缺视觉字段
            for k, v in vn.visual.items():
                if k != "type" and k not in el:
                    el[k] = v
            if "type" not in el:
                el["type"] = vn.visual.get("type", "panel")
        else:
            new_el = {"type": vn.visual.get("type", "panel"), "id": vn.id, "rect": rect}
            for k, v in vn.visual.items():
                if k != "type":
                    new_el[k] = v
            elements.insert(insert_at, new_el)
            insert_at += 1
            by_id[vn.id] = new_el

    # 仍缺 rect 的几何元素 → 报错
    needs_rect = {"box", "asset", "panel", "tokens", "network", "scatter", "sketch"}
    for i, e in enumerate(elements):
        if not isinstance(e, dict):
            continue
        et = e.get("type")
        if et in needs_rect and e.get("rect") is None:
            raise LayoutError(
                f"elements[{i}]({et} id={e.get('id')!r}): 无 rect 且未被子 layout 放置"
            )

    # 去掉 layout 节（物化为纯绝对坐标）
    out.pop("layout", None)
    return out


def materialize_yaml(raw: dict[str, Any], *, force: bool = False) -> str:
    """resolve 后序列化为稳定 YAML 文本。"""
    resolved = resolve_document(raw, force=force)
    # 固定 key 顺序观感：figure, theme, assets..., elements
    return yaml.safe_dump(
        resolved,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=None,
        width=120,
    )


def load_and_resolve(path: str, text: str | None = None) -> tuple[dict[str, Any], bool]:
    """读 YAML，若有 layout 则 resolve。返回 (raw_dict, did_resolve)。"""
    from pathlib import Path
    p = Path(path)
    if text is None:
        text = p.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise LayoutError("spec 顶层必须是 mapping")
    if document_has_layout(raw):
        return resolve_document(raw), True
    return raw, False
