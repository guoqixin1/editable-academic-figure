"""scifig 回归测试：核心几何 + 6 个 bugbot 修复的边界用例。

运行：python -m pytest tests/ -q   （或 python tests/test_scifig.py）
不联网，不调用生图 API。
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scifig.cutout import cutout_white_bg
from scifig.fonts import (measure_markup_mm, measure_mm, parse_markup, split_runs,
                          strip_markup, wrap_text)
from scifig.lint import lint
from scifig.render import render
from scifig.spec import FigureSpec, Rect, load_spec
from scifig.theme import load_theme


def _write(tmp, text):
    p = Path(tmp) / "f.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ── fonts ───────────────────────────────────────────────

def test_split_runs_mixed():
    runs = split_runs("Encoder编码器α→β")
    classes = [c for _, c in runs]
    assert "latin" in classes and "cjk" in classes and "symbol" in classes


def test_measure_positive_and_monotonic():
    assert measure_mm("A", 7) > 0
    assert measure_mm("AAAA", 7) > measure_mm("AA", 7)


def test_wrap_respects_width():
    lines = wrap_text("这是一段需要自动换行的中文文本用于测试换行逻辑", 7, 20)
    assert len(lines) >= 2
    assert all(l.width_mm <= 20 * 1.02 for l in lines)


# ── theme（bug #4：字符串简写）───────────────────────────

def test_theme_string_shorthand():
    th = load_theme("warm")
    assert th.name == "warm"


def test_theme_dict_override():
    th = load_theme({"preset": "sci", "ink": "#000000"})
    assert th.ink == "#000000"


# ── Rect 几何 ────────────────────────────────────────────

def test_rect_intersection():
    a = Rect(0, 0, 10, 10)
    b = Rect(5, 5, 10, 10)
    assert abs(a.intersection_area(b) - 25) < 1e-6
    assert Rect(0, 0, 10, 10).intersection_area(Rect(20, 20, 5, 5)) == 0


# ── render + lint 基本闭环 ──────────────────────────────

BASIC = """
figure: {width: 100, height: 60}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 20, 30, 20], title: 输入, body: 文本}
  - {type: box, id: b, rect: [65, 20, 30, 20], title: 输出, body: 结果}
  - {type: arrow, from: a.right, to: b.left}
"""


def test_render_basic(tmp_path):
    spec = load_spec(_write(tmp_path, BASIC))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert (tmp_path / "o.png").exists()
    issues = lint(spec, res)
    assert [i for i in issues if i.level == "E"] == []


def test_lint_detects_overlap(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
elements:
  - {type: box, id: a, rect: [5, 20, 40, 20], title: A}
  - {type: box, id: b, rect: [30, 20, 40, 20], title: B}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    codes = {i.code for i in lint(spec, res)}
    assert "node-overlap" in codes


def test_lint_detects_missing_asset(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: asset, id: a, rect: [10, 10, 40, 40], src: nonexistent}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    assert "nonexistent" in res.missing_assets
    assert any(i.code == "asset-missing" for i in lint(spec, res))


# ── bug #1：箭头两端重合不崩溃 ──────────────────────────

def test_arrow_degenerate_no_crash(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
elements:
  - {type: box, id: a, rect: [10, 10, 30, 20]}
  - {type: box, id: b, rect: [10, 10, 30, 20], title: overlap}
  - {type: arrow, id: deg, from: a.center, to: b.center, label: L}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)  # 不应抛异常
    assert (tmp_path / "o.png").exists()


def test_all_shapes_render(tmp_path):
    for shape in ["rect", "stadium", "diamond", "cylinder", "parallelogram",
                  "hexagon", "ellipse", "trapezoid"]:
        spec = load_spec(_write(tmp_path, f"""
figure: {{width: 60, height: 40}}
elements:
  - {{type: box, id: s, rect: [10, 8, 40, 24], title: 形状, body: test, shape: {shape}}}
"""))
        res = render(spec, out_png=tmp_path / f"{shape}.png", dpi=100)
        assert (tmp_path / f"{shape}.png").exists()
        assert [i for i in lint(spec, res) if i.level == "E"] == [], f"{shape} 有 E 级问题"


# ── 记号 _{}/^{} ─────────────────────────────────────────

def test_parse_markup():
    segs = parse_markup("L_{InfoNCE}")
    assert segs == [("L", "n"), ("InfoNCE", "sub")]
    assert parse_markup("ℝ^{(B V) H W C}") == [("ℝ", "n"), ("(B V) H W C", "sup")]
    assert strip_markup("Z_{s} 和 z^{2}") == "Z_{s} 和 z^{2}".replace("_{", "").replace("^{", "").replace("}", "")


def test_markup_width_smaller_than_full():
    # 下标片段以更小字号计宽，总宽应小于把记号当普通字符
    w_markup = measure_markup_mm("L_{InfoNCE}", 8)
    w_plain = measure_mm("LInfoNCE", 8)
    assert w_markup < w_plain
    assert w_markup > measure_mm("L", 8)


def test_markup_renders_in_svg(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 30}
elements:
  - {type: text, id: t, at: [40, 12], text: "z ∈ ℝ^{(B V) H W C}", size: 7}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=120)
    # 记号语法字符不应出现在最终 SVG 文本里
    assert "^{" not in res.svg and "_{" not in res.svg
    assert (tmp_path / "o.png").exists()


# ── panel / tokens / marker ─────────────────────────────

def test_panel_tokens_marker_render(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 60}
elements:
  - {type: panel, id: p, rect: [4, 4, 112, 52], title: "Stage 1. 预训练", variant: highlight}
  - {type: box, id: enc, rect: [12, 22, 30, 12], title: "编码器 E_{s}"}
  - {type: tokens, id: z, rect: [12, 40, 28, 5], n: 7, label: "Z_{s}"}
  - {type: marker, at: [46, 24], icon: fire, size: 4.5}
  - {type: marker, at: [60, 24], icon: snow, size: 4.5}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=120)
    assert (tmp_path / "o.png").exists()
    assert [i for i in lint(spec, res) if i.level == "E"] == []
    assert "linearGradient" not in res.svg  # 本例未用渐变


def test_tokens_colors_and_sizes(tmp_path):
    # 逐格颜色（masked 黑格）+ 逐格尺寸（U-Net 特征金字塔）
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 80}
elements:
  - {type: tokens, id: masked, rect: [10, 10, 40, 6], n: 5, colors: ["#111", "#DDD", "#111", "#DDD", "#111"]}
  - {type: tokens, id: pyr, rect: [10, 30, 30, 40], n: 4, sizes: [40, 30, 20, 10]}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert res.svg.count("#111") >= 3
    assert [i for i in lint(spec, res) if i.level == "E"] == []


def test_all_markers_render(tmp_path):
    for icon in ["fire", "snow", "lock", "check", "cross"]:
        spec = load_spec(_write(tmp_path, f"""
figure: {{width: 40, height: 40}}
elements:
  - {{type: marker, id: m, at: [20, 20], icon: {icon}, size: 10}}
"""))
        res = render(spec, out_png=tmp_path / f"{icon}.png", dpi=100)
        assert (tmp_path / f"{icon}.png").exists()


def test_bad_marker_rejected(tmp_path):
    import pytest
    from scifig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 40, height: 40}
elements:
  - {type: marker, id: m, at: [20, 20], icon: rocket}
"""))


# ── 渐变 ─────────────────────────────────────────────────

def test_gradient_box(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 30}
elements:
  - {type: box, id: g, rect: [10, 8, 60, 14], title: 融合, gradient: ["#B7CDE8", "#F3C89D"]}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert "linearGradient" in res.svg
    assert "#B7CDE8" in res.svg and "#F3C89D" in res.svg


def test_bad_gradient_rejected(tmp_path):
    import pytest
    from scifig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 30}
elements:
  - {type: box, id: g, rect: [10, 8, 60, 14], gradient: ["#fff"]}
"""))


# ── 占位槽 ───────────────────────────────────────────────

def test_placeholder_asset_is_warning_not_error(tmp_path):
    # 意图性占位（真实实验图待插入）应为 W 级，不阻塞
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
elements:
  - {type: asset, id: exp, rect: [10, 8, 40, 30], src: assets/result.png, placeholder: true, caption: 实验结果}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    codes = {(i.level, i.code) for i in lint(spec, res)}
    assert ("W", "asset-placeholder") in codes
    assert ("E", "asset-missing") not in codes
    assert "assets/result.png" in res.placeholder_assets


def test_missing_nonplaceholder_is_error(tmp_path):
    # 非占位、又找不到文件 → 仍是 E 级错误
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
elements:
  - {type: asset, id: exp, rect: [10, 8, 40, 30], src: ghost}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert any(i.level == "E" and i.code == "asset-missing" for i in lint(spec, res))


# ── 论文复现向新特性 ─────────────────────────────────────

def test_box_fill_stroke_override(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 30}
elements:
  - {type: box, id: a, rect: [10, 8, 26, 14], shape: ellipse, title: data, fill: "#3E6595", stroke: "#28425F", text_color: "#FFFFFF"}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert "#3E6595" in res.svg and "#28425F" in res.svg


def test_box_stack_shadows(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 40}
elements:
  - {type: box, id: doc, rect: [10, 8, 30, 20], variant: plain, stack: 2, title: doc}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    # 主体 + 2 层叠影 = 3 个 rect（不含画布背景）
    assert res.svg.count("<rect") >= 4


def test_box_valign_top(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: box, id: card, rect: [10, 8, 60, 44], variant: plain, title: Decision, valign: top}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    ttl = [s for s in res.text_spans if s.text == "Decision"][0]
    assert ttl.baseline < 8 + 44 / 3  # 标题贴顶而非垂直居中


def test_group_color_and_inside_label(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
elements:
  - {type: group, id: g, rect: [8, 6, 60, 38], color: "#C0392B", label: "A: Clustering", label_pos: inside-bottom}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert 'stroke="#C0392B"' in res.svg
    lbl = [s for s in res.text_spans if "Clustering" in s.text][0]
    assert 6 < lbl.baseline < 44  # 标签画在框内


def test_text_rotate_and_italic(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 60, height: 60}
elements:
  - {type: text, id: v, at: [30, 30], text: Historical Data, size: 7, rotate: -90}
  - {type: text, id: it, at: [30, 52], text: Obs, size: 7, italic: true}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert "rotate(-90" in res.svg
    assert "font-style=\"italic\"" in res.svg
    rot = [s for s in res.text_spans if s.rotate][0]
    bb = rot.bbox()
    assert bb.h > bb.w  # 旋转 90° 后包络高大于宽


def test_arrow_width_block_arc(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
elements:
  - {type: arrow, id: thick, from: [10, 10], to: [50, 10], width: 1.2}
  - {type: arrow, id: blk, from: [10, 30], to: [10, 48], style: block, width: 4, fill: "#FFFFFF", color: "#333333"}
  - {type: arrow, id: arc, from: [40, 30], to: [80, 50], route: arc, bend: 0.3}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert 'stroke-width="1.2"' in res.svg
    assert "Q " in res.svg  # 弧线用二次贝塞尔
    segs = dict(res.arrow_segments)
    assert len(segs["arc"]) == 9   # 弧线采样 9 点供 lint
    assert len(segs["blk"]) == 2


def test_arrow_auto_side_bare_id(tmp_path):
    # 裸 id from/to：自动选朝向对方的边（a 在 b 左上 → a.right、b.left/top）
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 90}
elements:
  - {type: box, id: a, rect: [10, 10, 30, 16], title: A}
  - {type: box, id: b, rect: [80, 60, 30, 16], title: B}
  - {type: arrow, id: ar, from: a, to: b}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    segs = dict(res.arrow_segments)
    start, end = segs["ar"][0], segs["ar"][-1]
    assert start == (40.0, 18.0)          # a 的右边中点（朝向 b）
    assert abs(end[0] - 80.0) < 1e-6      # b 的左边（朝向 a）
    assert [i for i in lint(spec, res) if i.level == "E"] == []


def test_arrow_auto_side_same_row_is_straight(tmp_path):
    # 同排等高两盒裸 id 连接 → 干净水平直线（无斜线"没对上"）
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 40}
elements:
  - {type: box, id: a, rect: [10, 12, 30, 16], title: A}
  - {type: box, id: b, rect: [80, 12, 30, 16], title: B}
  - {type: arrow, id: ar, from: a, to: b}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    pts = dict(res.arrow_segments)["ar"]
    assert all(abs(p[1] - pts[0][1]) < 1e-6 for p in pts)  # 所有点 y 相同 → 水平


def test_arrow_bare_id_bad_ref_rejected(tmp_path):
    import pytest
    from scifig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 20]}
  - {type: arrow, from: a, to: ghost}
"""))


def test_lint_row_near_misaligned(tmp_path):
    # 三盒本想同排顶对齐，一个 y 差 1.2mm → row-misaligned（近失）
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 40}
elements:
  - {type: box, id: a, rect: [10, 10, 24, 16], title: A}
  - {type: box, id: b, rect: [46, 11.2, 24, 16], title: B}
  - {type: box, id: c, rect: [82, 10, 24, 16], title: C}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    assert any(i.code == "row-misaligned" for i in lint(spec, res))


def test_lint_aligned_row_no_warning(tmp_path):
    # 完美对齐等距 → 不报 row-misaligned / uneven-gap
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 40}
elements:
  - {type: box, id: a, rect: [10, 10, 24, 16], title: A}
  - {type: box, id: b, rect: [46, 10, 24, 16], title: B}
  - {type: box, id: c, rect: [82, 10, 24, 16], title: C}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    codes = {i.code for i in lint(spec, res)}
    assert "row-misaligned" not in codes and "uneven-gap" not in codes


def test_lint_intentional_offset_not_flagged(tmp_path):
    # 明显有意的错落（阶梯，差远大于近失阈值）→ 不误报
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
elements:
  - {type: box, id: a, rect: [10, 10, 24, 14], title: A}
  - {type: box, id: b, rect: [46, 30, 24, 14], title: B}
  - {type: box, id: c, rect: [82, 50, 24, 14], title: C}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    assert not any(i.code in ("row-misaligned", "col-misaligned") for i in lint(spec, res))


def test_lint_uneven_gap_near_miss(tmp_path):
    # 列方向中心间距几乎相等却差 2mm → uneven-gap
    spec = load_spec(_write(tmp_path, """
figure: {width: 40, height: 120}
elements:
  - {type: box, id: a, rect: [8, 8, 24, 12], title: A}
  - {type: box, id: b, rect: [8, 40, 24, 12], title: B}
  - {type: box, id: c, rect: [8, 70, 24, 12], title: C}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    assert any(i.code == "uneven-gap" for i in lint(spec, res))


def test_network_and_badge(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: network, id: net, rect: [10, 8, 30, 24], layers: [3, 4, 2]}
  - {type: badge, at: [60, 12], text: "1", size: 5}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert res.svg.count("<circle") >= 10  # 9 节点 + badge 圆
    assert not any(i.level == "E" for i in lint(spec, res))


def test_scatter_deterministic(tmp_path):
    y = """
figure: {width: 80, height: 60}
elements:
  - {type: scatter, id: sc, rect: [8, 6, 60, 46], seed: 7, clusters: [
      {at: [0.4, 0.4], rx: 0.3, ry: 0.2, n: 20, color: "#C0392B"}]}
"""
    r1 = render(load_spec(_write(tmp_path, y)), out_png=tmp_path / "a.png", dpi=80)
    r2 = render(load_spec(_write(tmp_path, y)), out_png=tmp_path / "b.png", dpi=80)
    assert r1.svg == r2.svg  # seed 固定 → 逐像素可复现


def test_new_marker_icons(tmp_path):
    for icon in ["oplus", "otimes", "wifi"]:
        spec = load_spec(_write(tmp_path, f"""
figure: {{width: 30, height: 30}}
elements:
  - {{type: marker, id: m, at: [15, 15], icon: {icon}, size: 8}}
"""))
        res = render(spec, out_png=tmp_path / f"{icon}.png", dpi=100)
        assert (tmp_path / f"{icon}.png").exists()


def test_nested_containment_not_overlap(tmp_path):
    # box 作容器、子元素完全在内 → 不报 node-overlap
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
elements:
  - {type: box, id: card, rect: [10, 8, 80, 44], variant: plain, valign: top, title: Card}
  - {type: box, id: inner, rect: [20, 20, 30, 20], title: 内部}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert not any(i.code == "node-overlap" for i in lint(spec, res))


# ── studio ──────────────────────────────────────────────

def test_load_spec_text_override(tmp_path):
    p = _write(tmp_path, """
figure: {width: 50, height: 30}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 10], title: X}
""")
    spec = load_spec(p, text="""
figure: {width: 70, height: 40}
elements:
  - {type: box, id: b, rect: [5, 5, 20, 10], title: Y}
""")
    assert spec.width == 70 and spec.find("b") is not None
    # 相对路径解析仍基于原文件位置
    assert spec.path == p.resolve()


def test_svg_has_data_el_groups(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 40}
elements:
  - {type: box, id: enc, rect: [5, 5, 30, 14], title: E}
  - {type: arrow, id: ar, from: enc.right, to: [70, 12]}
  - {type: text, id: cap, at: [40, 30], text: 注}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    for eid in ("enc", "ar", "cap"):
        assert f'data-el="{eid}"' in res.svg


def test_studio_element_ranges(tmp_path):
    from scifig.studio import element_ranges
    text = """figure: {width: 80, height: 60}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 10], title: A}
  - {type: text, id: t, at: [40, 30], text: hi}
  - {type: arrow, id: ar, from: a.right, to: [70, 10]}
"""
    p = _write(tmp_path, text)
    spec = load_spec(p)
    rng = element_ranges(text, spec)
    assert [r["id"] for r in rng] == ["a", "t", "ar"]
    assert rng[0]["drag"] == "rect" and rng[0]["line0"] == 2
    assert rng[1]["drag"] == "at"
    assert rng[2]["drag"] is None  # 箭头锚定在节点上，不可拖


def test_studio_api_render_and_error(tmp_path):
    from scifig.studio import StudioServer
    p = _write(tmp_path, """
figure: {width: 60, height: 40}
elements:
  - {type: box, id: a, rect: [5, 5, 30, 14], title: A}
""")
    s = StudioServer(p)
    ok = s.api_render({"text": p.read_text(encoding="utf-8")})
    assert "svg" in ok and ok["elements"][0]["id"] == "a"
    assert ok["width"] == 60
    bad = s.api_render({"text": "figure: {width: 60"})
    assert "error" in bad
    bad2 = s.api_render({"text": "figure: {width: 60, height: 40}\nelements:\n  - {type: ufo}"})
    assert "error" in bad2


def test_arrow_from_inner_out_of_container_ok(tmp_path):
    # 从容器内的子元素连到容器外 → 不把"穿出容器边界"误报为穿线
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 60}
elements:
  - {type: box, id: card, rect: [10, 8, 60, 44], variant: plain, valign: top, title: Card}
  - {type: box, id: inner, rect: [20, 20, 30, 16], title: 内部}
  - {type: box, id: out, rect: [90, 20, 24, 16], title: 外部}
  - {type: arrow, from: inner.right, to: out.left}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    assert not any(i.code == "arrow-through-node" for i in lint(spec, res))


def test_bad_shape_rejected(tmp_path):
    import pytest
    from scifig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 60, height: 40}
elements:
  - {type: box, id: s, rect: [10, 8, 40, 24], shape: triangle}
"""))


def test_arrow_via_waypoints(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
elements:
  - {type: box, id: a, rect: [10, 10, 30, 16]}
  - {type: box, id: b, rect: [10, 40, 30, 16]}
  - {type: arrow, from: a.left, to: b.left, via: [[4, 18], [4, 48]], label: 残差}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    seg = dict(res.arrow_segments)
    # 途经点应出现在路径里，且不穿过任何节点
    pts = [p for _, segs in res.arrow_segments for p in segs]
    assert (4.0, 18.0) in pts
    assert not any(i.code == "arrow-through-node" for i in lint(spec, res))


def test_density_sparse_detected(tmp_path):
    # 一个小元素扔在大画布里 → 应报 canvas-sparse
    spec = load_spec(_write(tmp_path, """
figure: {width: 200, height: 200}
elements:
  - {type: box, id: a, rect: [90, 95, 20, 10], title: 孤岛}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=60)
    assert any(i.code == "canvas-sparse" for i in lint(spec, res))


def test_density_dataflow_not_flagged(tmp_path):
    # 连线留白但内容铺满画布的图不应误报 sparse（画布尺寸与内容匹配）
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 30}
elements:
  - {type: box, id: a, rect: [6, 8, 26, 16], title: A}
  - {type: box, id: b, rect: [88, 8, 26, 16], title: B}
  - {type: arrow, from: a.right, to: b.left}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    assert not any(i.code == "canvas-sparse" for i in lint(spec, res))


def test_arrow_label_all_routes(tmp_path):
    for route in ["straight", "hv", "vh", "z", "zv", "auto"]:
        spec = load_spec(_write(tmp_path, f"""
figure: {{width: 120, height: 80}}
elements:
  - {{type: box, id: a, rect: [10, 10, 30, 20]}}
  - {{type: box, id: b, rect: [80, 50, 30, 20]}}
  - {{type: arrow, from: a.right, to: b.left, route: {route}, label: 流}}
"""))
        res = render(spec, out_png=tmp_path / f"{route}.png", dpi=80)
        assert (tmp_path / f"{route}.png").exists()


# ── bug #2：抠图退化输入返回 False 不崩溃 ───────────────

def test_cutout_all_white(tmp_path):
    Image.new("RGB", (100, 100), (255, 255, 255)).save(tmp_path / "white.png")
    rep = cutout_white_bg(tmp_path / "white.png", tmp_path / "out.png")
    assert rep.ok is False  # 前景过小，优雅失败


def test_cutout_preserves_interior_white(tmp_path):
    # 蓝色方块中间挖一个纯白洞：洞不应被抠穿
    arr = np.full((120, 120, 3), 255, np.uint8)
    arr[30:90, 30:90] = (40, 90, 200)   # 蓝色主体
    arr[50:70, 50:70] = (255, 255, 255)  # 主体内部白洞
    Image.fromarray(arr).save(tmp_path / "in.png")
    rep = cutout_white_bg(tmp_path / "in.png", tmp_path / "out.png")
    assert rep.ok
    out = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA"))
    # 输出中心（对应内部白洞）应仍不透明
    cy, cx = out.shape[0] // 2, out.shape[1] // 2
    assert out[cy, cx, 3] > 200, "主体内部白色被错误抠掉"


def test_cutout_real_asset_no_halo():
    p = Path(__file__).resolve().parent.parent / "examples/demo_method/assets/microscope.png"
    if not p.exists():
        return
    a = np.asarray(Image.open(p).convert("RGBA"))
    alpha = a[:, :, 3]
    edge = (alpha > 20) & (alpha < 235)
    if edge.sum():
        near_white = (a[:, :, :3][edge] >= 245).all(axis=1).mean()
        assert near_white < 0.05, f"边缘白色 halo 占比过高: {near_white:.1%}"


# ── spec 校验 ────────────────────────────────────────────

def test_spec_rejects_bad_ref(tmp_path):
    import pytest
    from scifig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 20]}
  - {type: arrow, from: a.right, to: ghost.left}
"""))


if __name__ == "__main__":
    import tempfile
    import traceback

    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    passed = failed = 0
    for name, fn in fns:
        try:
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception:
            print(f"  FAIL {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
