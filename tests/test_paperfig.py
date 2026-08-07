"""paperfig 回归测试：核心几何 + 6 个 bugbot 修复的边界用例。

运行：python -m pytest tests/ -q   （或 python tests/test_paperfig.py）
不联网，不调用生图 API。
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperfig.cutout import cutout_white_bg
from paperfig.fonts import (measure_markup_mm, measure_mm, parse_markup, split_runs,
                          strip_markup, wrap_text)
from paperfig.lint import lint
from paperfig.render import render
from paperfig.spec import FigureSpec, Rect, load_spec
from paperfig.theme import load_theme


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
    from paperfig.spec import SpecError
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
    from paperfig.spec import SpecError
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
    from paperfig.spec import SpecError
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
    from paperfig.studio import element_ranges
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
    from paperfig.studio import StudioServer
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
    from paperfig.spec import SpecError
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


def test_arrow_visual_anchor_accent_and_stack(tmp_path):
    """锚点落在 accent/stack 视觉外边界，而非逻辑 rect。"""
    from paperfig.render import visual_rect_for

    spec = load_spec(_write(tmp_path, """
figure: {width: 140, height: 80}
theme: {preset: topconf}
elements:
  - {type: box, id: src, rect: [10, 20, 30, 20], title: S, accent: left}
  - {type: box, id: dst, rect: [70, 20, 30, 20], title: D, accent: left, stack: 2}
  - {type: arrow, id: ar, from: src.right, to: dst.left, route: hv}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    src = next(e for e in spec.elements if e.id == "src")
    dst = next(e for e in spec.elements if e.id == "dst")
    vr_src, vr_dst = visual_rect_for(src), visual_rect_for(dst)
    assert abs(vr_src.x - (src.rect.x - min(1.1, src.rect.w * 0.08))) < 1e-6
    assert abs(vr_dst.right - (dst.rect.right + 1.5 * 2)) < 1e-6

    pts = dict(res.arrow_segments)["ar"]
    # 终点在 dst 视觉左边；起点在 src 视觉右边（无 stack，= logical right）
    assert abs(pts[-1][0] - vr_dst.x) < 1e-3
    assert abs(pts[0][0] - vr_src.right) < 1e-3


def test_arrow_orthogonal_entry_hv_to_left(tmp_path):
    """hv 指向 left 时末段必须水平，且整条折线无斜线段。"""
    from paperfig.render import _segments_axis_aligned

    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
elements:
  - {type: box, id: a, rect: [10, 10, 28, 14], title: A}
  - {type: box, id: b, rect: [70, 40, 28, 20], title: B}
  - {type: arrow, id: ar, from: a.right, to: b.left@0.3, route: hv}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    pts = dict(res.arrow_segments)["ar"]
    assert _segments_axis_aligned(pts), f"hv 折线含斜线段: {pts}"
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    assert abs(y1 - y2) < 1e-6, f"末段应水平，得到 dy={y2 - y1}"
    assert abs(x2 - x1) >= 3.0 - 1e-3, f"垂直进入段应 ≥3mm，得到 {abs(x2 - x1)}"
    # 首段离开 right：应水平
    (sx0, sy0), (sx1, sy1) = pts[0], pts[1]
    assert abs(sy0 - sy1) < 1e-6
    assert not any(i.code == "arrow-approach" for i in lint(spec, res))


def test_arrow_z_route_fully_orthogonal(tmp_path):
    """z 路由（right→left、y 不对齐）全部为水平/垂直段。"""
    from paperfig.render import _segments_axis_aligned

    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
elements:
  - {type: box, id: a, rect: [10, 12, 28, 14], title: A}
  - {type: box, id: b, rect: [70, 42, 28, 20], title: B}
  - {type: arrow, id: ar, from: a.right, to: b.left@0.25, route: z}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    pts = dict(res.arrow_segments)["ar"]
    assert len(pts) >= 4
    assert _segments_axis_aligned(pts), f"z 折线含斜线段: {pts}"
    # 末段水平进入 left
    assert abs(pts[-2][1] - pts[-1][1]) < 1e-6
    assert abs(pts[-1][0] - pts[-2][0]) >= 3.0 - 1e-3


def test_arrow_approach_lint_flags_diagonal_via(tmp_path):
    """显式 via 斜线末段：arrow-approach 必须告警（夹角 >15°）。"""
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
elements:
  - {type: box, id: a, rect: [10, 20, 24, 16], title: A}
  - {type: box, id: b, rect: [80, 40, 24, 16], title: B}
  - {type: arrow, id: ar, from: a.right, to: b.left, via: [[50, 28]]}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    pts = dict(res.arrow_segments)["ar"]
    # via 保持用户意图 → 末段为斜线
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    assert abs(x0 - x1) > 1e-3 and abs(y0 - y1) > 1e-3
    assert any(i.code == "arrow-approach" for i in lint(spec, res))


def test_arrow_label_avoids_tip_keepout(tmp_path):
    """短进入 stub + 标签不得让胶囊盖住尖端（否则会出现悬空错觉）。"""
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 70}
elements:
  - {type: box, id: a, rect: [8, 20, 28, 28], title: Src, accent: left}
  - {type: box, id: b, rect: [55, 18, 30, 32], title: Dst, accent: left}
  - {type: arrow, id: ar, from: a.right, to: b.left@0.72, route: hv, label: "8 sents"}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=120)
    tip = dict(res.arrow_segments)["ar"][-1]
    caps = [c for aid, c, lbl in res.arrow_label_boxes if aid == "ar"]
    assert caps, "应记录箭头标签胶囊"
    cap = caps[0]
    assert not (cap.x - 0.05 <= tip[0] <= cap.right + 0.05
                and cap.y - 0.05 <= tip[1] <= cap.bottom + 0.05), (
        f"标签胶囊盖住尖端: tip={tip} cap={cap}")
    assert not any(i.code == "arrow-label-tip" for i in lint(spec, res))


def test_arrow_endpoints_resnap_to_visual(tmp_path):
    """ortho 改写后 tip 必须精确落在目标视觉边。"""
    from paperfig.render import visual_rect_for
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 70}
elements:
  - {type: box, id: a, rect: [8, 12, 28, 14], title: A}
  - {type: box, id: b, rect: [60, 30, 28, 20], title: B, accent: left}
  - {type: arrow, id: ar, from: a.right, to: b.left@0.6, route: hv}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    b = next(e for e in spec.elements if e.id == "b")
    vr = visual_rect_for(b)
    tip = dict(res.arrow_segments)["ar"][-1]
    assert abs(tip[0] - vr.x) < 1e-6
    assert not any(i.code == "arrow-gap" for i in lint(spec, res))


def test_arrow_head_color_matches_shaft(tmp_path):
    """自定义色 + dashed 时头部 fill 与杆 stroke 同色。"""
    import re
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 40}
elements:
  - {type: box, id: a, rect: [8, 10, 24, 16]}
  - {type: box, id: b, rect: [60, 10, 24, 16]}
  - {type: arrow, id: ar, from: a.right, to: b.left, style: dashed, color: "#D55E00"}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    # 取该箭头的 g 片段
    m = re.search(r'<g data-el="ar">(.*?)</g>', res.svg, re.S)
    assert m, "未找到箭头组"
    frag = m.group(1)
    stroke = re.search(r'stroke="(#[0-9A-Fa-f]+)"', frag)
    fills = re.findall(r'<polygon[^>]*fill="(#[0-9A-Fa-f]+)"', frag)
    assert stroke and fills
    assert all(f.upper() == stroke.group(1).upper() for f in fills)


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


# ── route: avoid + auto label ────────────────────────────

def test_route_avoid_goes_around_obstacle(tmp_path):
    """中间有墙时 avoid 路径绕行，不穿墙。"""
    from paperfig.lint import _segment_hits_rect
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 30, 25, 20], title: A, body: in}
  - {type: box, id: wall, rect: [45, 20, 25, 40], title: Wall, body: block}
  - {type: box, id: b, rect: [90, 30, 25, 20], title: B, body: out}
  - {type: arrow, id: ar, from: a.right, to: b.left, route: avoid}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    pts = dict(res.arrow_segments)["ar"]
    assert len(pts) >= 3
    wall = res.node_rects["wall"]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        assert not _segment_hits_rect(x1, y1, x2, y2, wall.expanded(-0.4))
    assert not any(i.code == "arrow-through-node" for i in lint(spec, res))


def test_route_avoid_orthogonal_ports(tmp_path):
    """首段垂直离开源边、末段垂直进入目标边。"""
    from paperfig.lint import _end_approach_ok
    spec = load_spec(_write(tmp_path, """
figure: {width: 120, height: 80}
theme: sci
elements:
  - {type: box, id: a, rect: [10, 10, 30, 20], title: A, body: x}
  - {type: box, id: mid, rect: [50, 35, 20, 30], title: M, body: y}
  - {type: box, id: b, rect: [85, 10, 30, 20], title: B, body: z}
  - {type: arrow, id: ar, from: a.right, to: b.left, route: avoid}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    pts = dict(res.arrow_segments)["ar"]
    sides = {aid: (s1, s2) for aid, s1, s2 in res.arrow_ends}
    s1, s2 = sides["ar"]
    assert s1 == "right" and s2 == "left"
    # 全正交
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert abs(x0 - x1) < 1e-6 or abs(y0 - y1) < 1e-6
    assert _end_approach_ok(pts, s2)
    # 首段水平离开 right
    assert abs(pts[0][1] - pts[1][1]) < 1e-6
    assert pts[1][0] > pts[0][0]


def test_route_avoid_nudging_separates_parallel(tmp_path):
    """共享走廊的平行箭头被 nudge 错开。"""
    from paperfig.routing import nudge_paths, NUDGE_GAP_MM
    paths = {
        "a": [(10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (50.0, 40.0)],
        "b": [(10.0, 20.0), (30.0, 20.0), (30.0, 50.0), (50.0, 50.0)],
    }
    out = nudge_paths(paths)
    # 中间竖直段（非 stub）应被错开
    ax = {i: out["a"][i][0] for i in range(len(out["a"]))}
    # 至少路径仍正交且端点不变
    assert out["a"][0] == paths["a"][0] and out["a"][-1] == paths["a"][-1]
    assert out["b"][0] == paths["b"][0] and out["b"][-1] == paths["b"][-1]
    # 若存在共享竖直走廊，x 应不同
    segs_a = [(out["a"][i], out["a"][i + 1]) for i in range(len(out["a"]) - 1)]
    segs_b = [(out["b"][i], out["b"][i + 1]) for i in range(len(out["b"]) - 1)]
    vert_a = [s for s in segs_a if abs(s[0][0] - s[1][0]) < 1e-6 and 0 < s[0][0] < 50]
    vert_b = [s for s in segs_b if abs(s[0][0] - s[1][0]) < 1e-6 and 0 < s[0][0] < 50]
    if vert_a and vert_b:
        assert abs(vert_a[0][0][0] - vert_b[0][0][0]) >= NUDGE_GAP_MM - 0.05


def test_route_avoid_fallback_warning(tmp_path):
    """无解时降级 auto 并产生 route-avoid-fallback 警告，不崩溃。"""
    from paperfig.routing import route_orthogonal_avoid, RouteRequest, route_all
    from paperfig.spec import Rect
    # 通高墙把左右口袋隔开，端点在墙外 → A* 必失败
    obstacles = [("wall", Rect(20, 0, 60, 100))]
    req = RouteRequest(
        id="ar", x1=5, y1=50, s1="right", x2=95, y2=50, s2="left",
        exclude_ids=set(),
    )
    assert route_orthogonal_avoid(req, obstacles, Rect(0, 0, 100, 100)) is None
    batch = route_all([req], obstacles, Rect(0, 0, 100, 100))
    assert batch["ar"].fallback is True

    # 渲染路径：强制失败场景用巨大通高墙 + 极窄口袋
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 40}
theme: sci
elements:
  - {type: box, id: a, rect: [1, 15, 8, 10], title: A, body: x}
  - {type: box, id: b, rect: [91, 15, 8, 10], title: B, body: y}
  - {type: box, id: wall, rect: [15, 0, 70, 40], title: W, body: block}
  - {type: arrow, id: ar, from: a.right, to: b.left, route: avoid}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=80)
    issues = lint(spec, res)
    assert any(i.code == "route-avoid-fallback" for i in issues)
    assert (tmp_path / "o.png").exists()
    assert "ar" in dict(res.arrow_segments)  # 已降级画出路径


def test_label_auto_picks_collision_free(tmp_path):
    """碰撞打分选中不压盒子的候选位。"""
    from paperfig.routing import pick_best_label
    from paperfig.spec import Rect
    pts = [(40.0, 20.0), (55.0, 20.0), (55.0, 50.0), (70.0, 50.0)]
    boxes = [Rect(10, 10, 30, 20), Rect(70, 40, 30, 20)]
    texts = [Rect(42, 18, 18, 4)]
    best = pick_best_label(pts, 12.0, 3.5, 2.5, boxes, texts, [], [])
    assert best is not None
    assert sum(best.cap.intersection_area(b) for b in boxes) < 0.05
    assert sum(best.cap.intersection_area(t) for t in texts) < 0.05


def test_label_manual_offset_respected(tmp_path):
    """显式 label_offset 时不走 auto，行为与旧落标一致。"""
    spec_auto = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 15, 25, 20], title: A, body: x}
  - {type: box, id: b, rect: [70, 15, 25, 20], title: B, body: y}
  - {type: arrow, id: ar, from: a.right, to: b.left, route: avoid, label: hi}
"""))
    spec_manual = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 15, 25, 20], title: A, body: x}
  - {type: box, id: b, rect: [70, 15, 25, 20], title: B, body: y}
  - {type: arrow, id: ar, from: a.right, to: b.left, route: avoid, label: hi, label_offset: -2.2}
"""))
    ra = render(spec_auto, out_png=tmp_path / "a.png", dpi=80)
    rm = render(spec_manual, out_png=tmp_path / "m.png", dpi=80)
    cap_a = next(c for aid, c, _ in ra.arrow_label_boxes if aid == "ar")
    cap_m = next(c for aid, c, _ in rm.arrow_label_boxes if aid == "ar")
    # 手动负 offset → 标签在线下方，与默认 auto 位置应不同（或至少 offset 标志生效）
    el = next(e for e in spec_manual.elements if e.id == "ar")
    assert el.label_offset_explicit is True
    assert el.label_offset == -2.2
    # 手动路径使用旧 layout：胶囊中心相对折线的侧应偏向下方
    pts = dict(rm.arrow_segments)["ar"]
    mid_y = (pts[0][1] + pts[-1][1]) / 2
    assert cap_m.y + cap_m.h / 2 >= mid_y - 0.5 or abs(cap_a.y - cap_m.y) > 0.3


def test_label_pos_auto_only_default_on_avoid(tmp_path):
    """兼容性：普通箭头未写 label_pos 时不启用 auto（与旧行为一致）。"""
    from paperfig.render import _use_auto_label
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
elements:
  - {type: box, id: a, rect: [5, 15, 25, 20]}
  - {type: box, id: b, rect: [70, 15, 25, 20]}
  - {type: arrow, id: old, from: a.right, to: b.left, label: x}
  - {type: arrow, id: av, from: a.right, to: b.left, route: avoid, label: y}
  - {type: arrow, id: exp, from: a.right, to: b.left, route: auto, label: z, label_pos: auto}
"""))
    old = next(e for e in spec.elements if e.id == "old")
    av = next(e for e in spec.elements if e.id == "av")
    exp = next(e for e in spec.elements if e.id == "exp")
    assert _use_auto_label(old) is False
    assert _use_auto_label(av) is True
    assert _use_auto_label(exp) is True


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
    from paperfig.spec import SpecError
    with pytest.raises(SpecError):
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 60}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 20]}
  - {type: arrow, from: a.right, to: ghost.left}
"""))


# ── assets 风格包（离线，不调 API）──────────────────────

def test_style_pack_sci_contains_palette_and_sections():
    from paperfig.assets import build_full_prompt, build_style_pack, resolve_asset_palette

    pack = build_style_pack("sci")
    assert "STYLE SPECIFICATIONS:" in pack
    assert "#3B6EA5" in pack
    assert "restricted color palette" in pack.lower() or "Restricted color palette" in pack
    assert "one illustrator" in pack
    assert "icon-level abstraction" in pack
    assert "Outline lock:" in pack
    assert "Viewing angle (required):" in pack
    assert "three-quarter view" in pack
    assert "three-quarter or side view" not in pack  # 软建议已改为硬要求

    full = build_full_prompt("一台简洁的光学显微镜，侧面视角", theme_cfg="sci")
    assert full.startswith("一台简洁的光学显微镜")
    assert "STYLE SPECIFICATIONS:" in full
    assert "HARD CONSTRAINTS:" in full
    assert "#FFFFFF" in full
    assert "no PCB-level" in full
    assert "uniform outline weight (~2px)" in full
    assert "Viewing angle required:" in full
    assert resolve_asset_palette("sci") == ["#3B6EA5", "#5B8266", "#C77D2E", "#B5B5B5"]


def test_style_pack_palette_override_and_presets():
    from paperfig.assets import build_style_pack, resolve_asset_palette, resolve_preset

    assert resolve_preset("topconf") == "topconf"
    assert resolve_preset("airy") == "airy"
    assert resolve_preset("unknown_preset_xyz") == "sci"
    assert resolve_asset_palette("topconf") == ["#0072B2", "#E69F00", "#009E73"]
    assert resolve_asset_palette("airy") == ["#BBDEFB", "#FFD0D0", "#C8E6C9"]

    overridden = resolve_asset_palette({
        "preset": "sci",
        "palette": {"primary": "#112233", "secondary": "#445566", "accent": "#778899"},
    })
    # primary/secondary/accent 被覆盖，highlight 位保留 sci 默认 #B5B5B5
    assert overridden[0] == "#112233"
    assert overridden[1] == "#445566"
    assert overridden[2] == "#778899"
    assert "#B5B5B5" in overridden
    pack = build_style_pack({"preset": "sci", "palette": {"primary": "#112233"}})
    assert "#112233" in pack
    assert "#5B8266" in pack  # secondary 未覆盖则保留 sci 默认


def test_assets_style_override_and_yaml_load(tmp_path):
    from paperfig.assets import (
        build_style_pack, build_full_prompt, load_style_context_from_yaml,
        resolve_asset_palette,
    )

    custom = "isometric cute robot icons, thick 3px navy outline, candy colors"
    pack = build_style_pack("sci", assets_style=custom)
    assert custom in pack
    assert "flat vector-style scientific illustration" not in pack  # 默认风格被覆盖

    yaml_text = """
figure: {width: 100, height: 60, assets_dir: assets}
theme:
  preset: warm
  palette:
    primary: "#AA5500"
assets_style: "chunky sticker icons with 2px chocolate outline"
assets:
  - {id: chip, prompt: a microchip}
elements:
  - {type: box, id: a, rect: [5, 5, 30, 20], title: A}
"""
    p = _write(tmp_path, yaml_text)
    # 顶层 assets_style 不得让 load_spec 报错
    spec = load_spec(p)
    assert spec.theme_cfg.get("preset") == "warm"

    theme_cfg, assets_style = load_style_context_from_yaml(p)
    assert theme_cfg["preset"] == "warm"
    assert assets_style == "chunky sticker icons with 2px chocolate outline"
    assert "#AA5500" in resolve_asset_palette(theme_cfg)
    full = build_full_prompt("a microchip", theme_cfg=theme_cfg, assets_style=assets_style)
    assert "chunky sticker icons" in full
    assert "HARD CONSTRAINTS:" in full


# ── 视觉增强：topconf/airy / sketch / legend / shadow ──────

def test_theme_topconf_and_airy():
    tc = load_theme("topconf")
    assert tc.name == "topconf"
    assert tc.variants["primary"].fill.upper() == "#FFFFFF"
    assert tc.variants["primary"].stroke == "#0072B2"
    assert tc.variants["muted"].stroke == "#CCCCCC"
    assert tc.group_fill == "#F7F7F7"
    assert tc.size_title > tc.size_body
    assert tc.default_shadow is False

    ay = load_theme("airy")
    assert ay.default_shadow is True
    assert ay.corner_radius >= 2.5
    assert ay.variants["primary"].fill == "#BBDEFB"


def test_theme_palette_override():
    th = load_theme({"preset": "topconf", "palette": {"primary": "#00897B", "secondary": "#FFB300"}})
    assert th.variants["primary"].stroke == "#00897B"
    assert th.variants["secondary"].stroke == "#FFB300"
    assert th.variants["primary"].fill.upper() == "#FFFFFF"
    assert th.palette["primary"] == "#00897B"


def test_sketch_and_legend_render(tmp_path):
    kinds = ["waveform", "bars", "heatmap", "scatter", "curve", "curve_desc",
             "grid", "matrix", "tree", "distribution", "spectrum", "layers",
             "nested", "dots_flow"]
    els = []
    for i, k in enumerate(kinds):
        x = 4 + (i % 7) * 26
        y = 4 + (i // 7) * 28
        els.append(f'  - {{type: sketch, id: sk{i}, rect: [{x}, {y}, 22, 22], kind: {k}}}')
    els.append(
        '  - {type: legend, id: lg, at: [4, 62], items: ['
        '{swatch: box, color: "#0072B2", label: primary}, '
        '{swatch: line, color: "#E69F00", label: flow}, '
        '{swatch: dashed, color: "#999", label: skip}], columns: 1}')
    yml = "figure: {width: 190, height: 95}\ntheme: topconf\nelements:\n" + "\n".join(els)
    spec = load_spec(_write(tmp_path, yml))
    r1 = render(spec, out_png=tmp_path / "a.png", dpi=100)
    r2 = render(spec, out_png=tmp_path / "b.png", dpi=100)
    assert r1.svg == r2.svg  # seed 可复现
    assert (tmp_path / "a.png").exists()
    assert "primary" in r1.svg


def test_box_enhancements_and_panel_smallcaps(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 140, height: 70}
theme: topconf
elements:
  - {type: panel, id: p, rect: [4, 4, 60, 60], title: Encoder, header_style: smallcaps, fill: "#F7F7F7"}
  - {type: box, id: a, rect: [10, 18, 48, 36], title: Module, variant: primary,
     accent: left, header_fill: true, sketch: waveform, shadow: true, valign: top}
  - {type: box, id: b, rect: [72, 18, 40, 28], title: Soft, variant: secondary, shadow: true}
  - {type: arrow, from: a.right, to: b.left, style: dotted, weight: heavy, label: feat, label_bg: true}
  - {type: text, at: [92, 55], text: Stage, smallcaps: true, size: 7, color: "#0072B2"}
  - {type: group, id: g, rect: [70, 10, 64, 50], fill: "#F7F7F7", hatch: true, style: dashed}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=120)
    assert "ENCODER" in res.svg or "letter-spacing" in res.svg
    assert "stroke-dasharray=\"0.35,0.95\"" in res.svg  # dotted
    assert "pattern" in res.svg  # hatch
    assert "STAGE" in res.svg
    assert not any(i.level == "E" for i in lint(spec, res))


def test_airy_default_shadow(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 40}
theme: airy
elements:
  - {type: box, id: a, rect: [10, 8, 50, 24], title: Card, variant: primary}
"""))
    res = render(spec, out_png=tmp_path / "o.png", dpi=100)
    # soft shadow = PIL 高斯模糊 PNG 嵌入（非矢量灰卡）
    assert "data:image/png;base64," in res.svg
    assert 'xlink:href="data:image/png;base64,' in res.svg


def test_lint_richness_empty_box_and_exemptions(tmp_path):
    """R-empty-box：空心盒报警；有 body/sketch/子元素/小面积则豁免。"""
    # 空心大盒（30×20=600mm² > 300）→ W
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
theme: {preset: topconf}
elements:
  - {type: box, id: empty, rect: [10, 10, 30, 20], title: Mod, variant: primary}
"""))
    res = render(spec, dpi=80)
    issues = lint(spec, res)
    assert any(i.code == "R-empty-box" and i.level == "W" for i in issues)
    assert any("有语义" in i.msg or "小标签盒可忽略" in i.msg
               for i in issues if i.code == "R-empty-box")

    # 有 sketch → 豁免
    spec2 = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
theme: {preset: topconf}
elements:
  - {type: box, id: m, rect: [10, 10, 40, 30], title: Mod, sketch: waveform, valign: top}
"""))
    res2 = render(spec2, dpi=80)
    assert not any(i.code == "R-empty-box" for i in lint(spec2, res2))

    # 容器卡（子元素在内）→ 豁免
    spec3 = load_spec(_write(tmp_path, """
figure: {width: 100, height: 50}
theme: {preset: topconf}
elements:
  - {type: box, id: host, rect: [8, 8, 50, 36], title: Host, valign: top}
  - {type: sketch, id: sk, rect: [14, 18, 30, 18], kind: heatmap}
"""))
    res3 = render(spec3, dpi=80)
    assert not any(i.code == "R-empty-box" and "host" in i.msg for i in lint(spec3, res3))

    # 小标签条（30×10=300mm²）→ 面积豁免，不诱导塞装饰
    spec4 = load_spec(_write(tmp_path, """
figure: {width: 100, height: 40}
theme: {preset: topconf}
elements:
  - {type: box, id: ln, rect: [10, 10, 30, 10], title: LayerNorm, variant: muted}
"""))
    res4 = render(spec4, dpi=80)
    assert not any(i.code == "R-empty-box" for i in lint(spec4, res4))


def test_lint_richness_section_and_legend(tmp_path):
    """R-no-section / R-no-legend：多元素无分区、多色无图例。"""
    # 8+ 元素、无 panel/fill group、3+ variant → 两条 W；无 E
    spec = load_spec(_write(tmp_path, """
figure: {width: 160, height: 60}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 10, 25, 20], title: A, body: x, variant: primary}
  - {type: box, id: b, rect: [40, 10, 25, 20], title: B, body: y, variant: secondary}
  - {type: box, id: c, rect: [75, 10, 25, 20], title: C, body: z, variant: accent}
  - {type: box, id: d, rect: [110, 10, 25, 20], title: D, body: w, variant: highlight}
  - {type: arrow, from: a.right, to: b.left}
  - {type: arrow, from: b.right, to: c.left}
  - {type: arrow, from: c.right, to: d.left}
  - {type: text, at: [80, 5], text: title, size: 7}
"""))
    res = render(spec, dpi=80)
    codes = {i.code for i in lint(spec, res)}
    assert "R-no-section" in codes
    assert "R-no-legend" in codes
    assert not any(i.level == "E" for i in lint(spec, res))

    # 补 panel + legend → 两条消失
    spec2 = load_spec(_write(tmp_path, """
figure: {width: 160, height: 60}
theme: {preset: topconf}
elements:
  - {type: panel, id: p, rect: [2, 2, 156, 56], title: Pipeline, header_style: smallcaps, fill: "#F7F7F7"}
  - {type: box, id: a, rect: [8, 14, 28, 28], title: A, sketch: grid, valign: top, variant: primary}
  - {type: box, id: b, rect: [48, 14, 28, 28], title: B, sketch: layers, valign: top, variant: secondary}
  - {type: box, id: c, rect: [88, 14, 28, 28], title: C, body: out, variant: muted}
  - {type: arrow, from: a.right, to: b.left, label: "R^d", weight: heavy}
  - {type: arrow, from: b.right, to: c.left, style: dashed}
  - {type: legend, id: lg, at: [122, 16], items: [
      {swatch: box, color: "#0072B2", label: "core"},
      {swatch: box, color: "#E69F00", label: "aux"},
    ]}
"""))
    res2 = render(spec2, dpi=80)
    codes2 = {i.code for i in lint(spec2, res2)}
    assert "R-no-section" not in codes2
    assert "R-no-legend" not in codes2
    assert not any(i.level == "E" for i in lint(spec2, res2))


if __name__ == "__main__":
    import tempfile
    import traceback

    # 无 pytest 时提供简易 raises，保证 __main__ 可跑
    try:
        import pytest  # noqa: F401
    except ImportError:
        class _Pytest:
            class raises:
                def __init__(self, exc):
                    self.exc = exc
                def __enter__(self):
                    return self
                def __exit__(self, et, ev, tb):
                    if et is None:
                        raise AssertionError(f"未抛出 {self.exc}")
                    return issubclass(et, self.exc)
        sys.modules["pytest"] = _Pytest()  # type: ignore

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
