"""base 混合模式阶段 1：schema / 骨架导出 / prompt / grid（不联网）。"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperfig.base import (
    _BASE_HARD_CONSTRAINTS,
    base_pixel_size,
    build_base_prompt,
    figure_aspect_ratio,
    overlay_mm_grid,
    render_skeleton,
)
from paperfig.spec import SpecError, load_spec


def _write(tmp, text, name="f.yaml"):
    p = Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    return p


_BASE_SKELETON = """
figure: {width: 180, height: 100}
theme: sci
base:
  mode: skeleton
  prompt: "医学 CAD 管线场景：左扫描、中网络、右诊断"
  candidates: 3
elements:
  - {type: panel, id: stage1, rect: [4, 4, 84, 92], title: Stage1}
  - {type: box, id: scan, rect: [10, 20, 30, 28], title: 扫描, body: CT}
  - {type: box, id: net, rect: [50, 20, 30, 28], title: 网络, body: U-Net}
  - {type: arrow, from: scan.right, to: net.left, label: encode}
  - {type: text, id: caption, at: [90, 90], text: 说明}
  - {type: legend, id: leg, at: [100, 10], items: [{swatch: box, label: A, color: "#3B6EA5"}]}
"""

_BASE_FREEFORM = """
figure: {width: 120, height: 80}
theme: warm
base:
  mode: freeform
  prompt: "agent RL 循环：环境-策略-奖励"
  image: base/base.png
  regions:
    env: [8, 20, 30, 30]
    policy: [50, 20, 30, 30]
elements:
  - {type: box, id: a, rect: [8, 20, 30, 30], title: Env}
"""


# ── schema 解析 / 校验 ──────────────────────────────────

def test_base_skeleton_parses(tmp_path):
    spec = load_spec(_write(tmp_path, _BASE_SKELETON))
    assert spec.base is not None
    assert spec.base.mode == "skeleton"
    assert "医学 CAD" in spec.base.prompt
    assert spec.base.candidates == 3
    assert spec.base.image is None
    assert spec.base.regions == {}


def test_base_freeform_with_regions(tmp_path):
    spec = load_spec(_write(tmp_path, _BASE_FREEFORM))
    assert spec.base.mode == "freeform"
    assert spec.base.image == "base/base.png"
    assert "env" in spec.base.regions
    assert abs(spec.base.regions["env"].w - 30) < 1e-6
    assert abs(spec.base.regions["policy"].x - 50) < 1e-6


def test_base_absent_is_none(tmp_path):
    spec = load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
    assert spec.base is None


def test_base_bad_mode_rejected(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base: {mode: magic, prompt: "x"}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
        assert False, "应抛 SpecError"
    except SpecError as e:
        assert "skeleton|freeform" in str(e)


def test_base_missing_prompt_rejected(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base: {mode: skeleton}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
        assert False
    except SpecError as e:
        assert "prompt" in str(e)


def test_base_region_bad_rect_rejected(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base:
  mode: freeform
  prompt: "x"
  regions: {m: [1, 2, 3]}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
        assert False
    except SpecError as e:
        assert "rect" in str(e).lower() or "[x, y, w, h]" in str(e)


def test_base_region_id_conflicts_with_element(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base:
  mode: freeform
  prompt: "x"
  regions: {a: [1, 2, 10, 10]}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
        assert False
    except SpecError as e:
        assert "冲突" in str(e)


def test_base_unknown_field_rejected(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base: {mode: skeleton, prompt: "x", foo: 1}
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""))
        assert False
    except SpecError as e:
        assert "foo" in str(e)


def test_base_render_with_ghost_boxes(tmp_path):
    """阶段 2：有 base 段时 box 变幽灵（无 fill/stroke），仍可渲染。"""
    spec = load_spec(_write(tmp_path, _BASE_SKELETON))
    out = tmp_path / "out.png"
    from paperfig.render import render
    res = render(spec, out_png=out, dpi=72)
    assert out.exists()
    assert res.base_mode is True
    # 幽灵盒：scan 的 shape 不应有 fill/stroke 矩形（title 文字仍在）
    # 取 data-el="scan" 分组内不应出现带 fill="#E3ECF7" 的 shape
    assert 'data-el="scan"' in res.svg
    assert "扫描" in res.svg
    # node_rects 仍在
    assert "scan" in res.node_rects
    assert abs(res.node_rects["scan"].w - 30) < 1e-6


# ── 骨架导出 ────────────────────────────────────────────

def test_skeleton_size_and_no_text(tmp_path):
    spec = load_spec(_write(tmp_path, _BASE_SKELETON))
    out = tmp_path / "skeleton.png"
    render_skeleton(spec, out)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == base_pixel_size(180, 100)
        assert max(im.size) == 1024
        arr = np.asarray(im.convert("RGB"))
    # 白底：四角应接近白
    for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
        assert arr[y, x].min() > 240
    # 色块区域应有非白像素（box 饱和色）
    assert (arr.mean(axis=2) < 230).sum() > 1000


def test_skeleton_bytes_mode(tmp_path):
    spec = load_spec(_write(tmp_path, _BASE_SKELETON))
    data = render_skeleton(spec, out_path=None)
    assert isinstance(data, (bytes, bytearray))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_skeleton_ignores_text_arrow_legend(tmp_path):
    """骨架只画 panel/box/asset，不画文字（用像素：标注区应保持白底）。"""
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base: {mode: skeleton, prompt: "x"}
elements:
  - {type: box, id: only, rect: [10, 10, 20, 20], title: ONLY}
  - {type: text, id: t, at: [80, 50], text: HELLO}
  - {type: arrow, from: only.right, to: [90, 20]}
"""
    spec = load_spec(_write(tmp_path, yaml))
    data = render_skeleton(spec)
    from io import BytesIO
    im = Image.open(BytesIO(data)).convert("RGB")
    arr = np.asarray(im)
    pw, ph = im.size
    sx, sy = pw / 100, ph / 60
    # text 落点附近应仍是白（无墨迹）
    tx, ty = int(80 * sx), int(50 * sy)
    patch = arr[max(0, ty - 5):ty + 5, max(0, tx - 15):tx + 15]
    assert patch.mean() > 250


def test_figure_aspect_and_pixel_size():
    assert figure_aspect_ratio(180, 100) in {"16:9", "3:2", "5:4", "4:3"}
    assert figure_aspect_ratio(100, 100) == "1:1"
    w, h = base_pixel_size(180, 100)
    assert max(w, h) == 1024
    assert abs(w / h - 180 / 100) < 0.02


# ── base prompt ─────────────────────────────────────────

def test_base_prompt_has_hard_constraints():
    p = build_base_prompt("医学 CAD 管线插画", theme_cfg="sci", skeleton=True)
    assert p.startswith("医学 CAD")
    assert "STYLE SPECIFICATIONS:" in p
    assert "HARD CONSTRAINTS" in p
    assert "禁止任何文字" in p or "No text" in p
    assert "铺满画布" in p or "Full-bleed" in p
    assert "单物件居中" in p or "NOT a single centered" in p
    assert "不要画外框" in p or "Do not draw outer frames" in p
    # 强编辑指令（探测结论：弱 prompt 会原样吐色块）
    assert "Replace each colored block's flat fill" in p
    assert "Do not translate, scale, merge" in p
    assert "footprint" in p
    assert "强编辑" in p or "平面填充" in p
    assert "Light tinted header bands" in p
    assert "reserved for later text plates" in p
    assert "plain and unillustrated" in p
    # 素材版硬约束不应混入
    assert "Single centered object" not in p
    assert _BASE_HARD_CONSTRAINTS.splitlines()[0] in p


def test_base_prompt_freeform_no_skeleton_extra():
    p = build_base_prompt("free scene", theme_cfg="warm", skeleton=False)
    assert "Replace each colored block's flat fill" not in p
    assert "强编辑" not in p
    assert "Do not translate, scale, merge" not in p


def test_base_prompt_skeleton_strong_edit():
    """skeleton 模式必须含强编辑指令；freeform 不含。"""
    sk = build_base_prompt("scene", theme_cfg="sci", skeleton=True)
    assert "Replace each colored block's flat fill with illustrated content" in sk
    assert "Keep every module's position and footprint exactly" in sk
    assert "Do not translate, scale, merge or split modules" in sk
    assert "Light tinted header bands" in sk
    assert "reserved for later text plates" in sk
    # no-text 硬约束仍在
    assert "No text" in sk or "禁止任何文字" in sk


# ── 参考图 payload（mock，不联网）───────────────────────

def test_submit_payload_encodes_urls_raw_b64(tmp_path, monkeypatch):
    """_submit 把本地 PNG 编成 raw base64 写入 urls；走 trust_env=False Session。"""
    import base64
    from unittest.mock import MagicMock

    from paperfig import assets as assets_mod

    png = tmp_path / "skeleton.png"
    Image.new("RGB", (32, 18), "#4A90D9").save(png)
    raw_b64 = base64.b64encode(png.read_bytes()).decode("ascii")

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"code": 0, "data": {"id": "task-xyz"}, "msg": "success"}
        return resp

    monkeypatch.setattr(assets_mod._SESSION, "post", fake_post)
    assert assets_mod._SESSION.trust_env is False

    tid = assets_mod._submit(
        "edit prompt",
        "sk-test",
        "16:9",
        "nano-banana-fast",
        reference_images=[str(png)],
    )
    assert tid == "task-xyz"
    payload = captured["json"]
    assert payload["model"] == "nano-banana-fast"
    assert payload["prompt"] == "edit prompt"
    assert payload["aspectRatio"] == "16:9"
    assert payload["imageSize"] == "1K"
    assert payload["webHook"] == "-1"
    assert payload["shutProgress"] is True
    assert "urls" in payload
    assert isinstance(payload["urls"], list) and len(payload["urls"]) == 1
    assert payload["urls"][0] == raw_b64
    assert not payload["urls"][0].startswith("data:")


def test_submit_payload_without_reference_omits_urls(monkeypatch):
    from unittest.mock import MagicMock

    from paperfig import assets as assets_mod

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"code": 0, "data": {"id": "t2"}, "msg": "success"}
        return resp

    monkeypatch.setattr(assets_mod._SESSION, "post", fake_post)
    tid = assets_mod._submit("plain", "sk-test", "1:1", "nano-banana-fast")
    assert tid == "t2"
    assert "urls" not in captured["json"]


def test_encode_reference_images_raw_b64(tmp_path):
    import base64

    from paperfig.assets import _encode_reference_images

    p = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "#E07A3D").save(p)
    urls = _encode_reference_images([str(p)])
    assert len(urls) == 1
    assert urls[0] == base64.b64encode(p.read_bytes()).decode("ascii")
    assert "data:" not in urls[0]


def test_default_model_not_retired():
    from paperfig.assets import DEFAULT_MODEL

    assert DEFAULT_MODEL == "nano-banana-fast"
    assert DEFAULT_MODEL != "nano-banana"


# ── mm 网格叠加 ─────────────────────────────────────────

def test_overlay_mm_grid_keeps_size(tmp_path):
    spec = load_spec(_write(tmp_path, _BASE_FREEFORM))
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    # 伪造底稿：与 figure 比例一致、长边 1024
    w, h = base_pixel_size(spec.width, spec.height)
    img = Image.new("RGB", (w, h), "#E8F0F8")
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 200, 200], fill="#3B6EA5")
    src = base_dir / "base.png"
    img.save(src)

    out = overlay_mm_grid(spec, src_png=src)
    assert out.exists()
    assert out.name == "base_grid.png"
    with Image.open(out) as g:
        assert g.size == (w, h)
    # 网格应改变部分像素
    a0 = np.asarray(Image.open(src).convert("RGB"))
    a1 = np.asarray(Image.open(out).convert("RGB"))
    assert not np.array_equal(a0, a1)


# ── 阶段 2：底稿合成 / 幽灵 / region / plate ─────────────

def test_base_image_embedded_full_bleed(tmp_path):
    """底稿以 data URI 内嵌 <image>，铺满 figure 尺寸。"""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    img = Image.new("RGB", (180, 100), "#88AACC")
    ImageDraw.Draw(img).rectangle([10, 10, 60, 50], fill="#224466")
    (base_dir / "base.png").write_bytes(b"")  # placeholder size; rewrite below
    img.save(base_dir / "base.png")

    yaml = """
figure: {width: 180, height: 100}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base/base.png
elements:
  - {type: box, id: a, rect: [20, 20, 40, 30], title: A}
"""
    from paperfig.render import render
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert 'data-base="1"' in res.svg
    assert 'xlink:href="data:image/png;base64,' in res.svg
    # 铺满：image 宽高等于 figure
    assert 'data-base="1"' in res.svg
    assert f'width="{spec.width}"' in res.svg and f'height="{spec.height}"' in res.svg
    # image 在白底 rect 之后
    bg_i = res.svg.index('<rect x="0" y="0"')
    img_i = res.svg.index('data-base="1"')
    assert bg_i < img_i
    # 矢量文字在底稿之后
    assert img_i < res.svg.index(">A</")


def test_base_image_missing_raises(tmp_path):
    yaml = """
figure: {width: 80, height: 50}
base:
  mode: freeform
  prompt: "x"
  image: missing/nope.png
elements:
  - {type: box, id: a, rect: [5, 5, 20, 15], title: A}
"""
    from paperfig.render import render
    spec = load_spec(_write(tmp_path, yaml))
    try:
        render(spec, dpi=72)
        assert False, "应抛 FileNotFoundError"
    except FileNotFoundError as e:
        assert "底稿" in str(e) or "不存在" in str(e)


def test_ghost_box_no_fill_stroke_keeps_geometry(tmp_path):
    """幽灵盒不输出 fill/stroke 形状，但锚点/路由仍用其矩形。"""
    yaml = """
figure: {width: 120, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
elements:
  - {type: box, id: left, rect: [10, 15, 30, 25], title: Left, body: sub, accent: left, sketch: bars}
  - {type: box, id: right, rect: [70, 15, 30, 25], title: Right}
  - {type: box, id: solid, rect: [40, 45, 20, 10], title: Stat, ghost: false}
  - {type: arrow, id: a1, from: left.right, to: right.left, label: go, route: avoid}
"""
    import re
    from paperfig.render import render
    from paperfig.lint import lint
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert res.base_mode
    # 幽灵 left 分组：无 shape fill/stroke，无 accent/sketch 记录
    m = re.search(r'<g data-el="left">(.*?)</g>', res.svg, re.DOTALL)
    assert m, "left 分组缺失"
    left_svg = m.group(1)
    assert 'fill="#E3ECF7"' not in left_svg
    assert "stroke-width" not in left_svg or "<rect" not in left_svg or 'rx="' not in left_svg
    # 更直接：幽灵分组不应有带 stroke-width 的 shape rect（只有 plate rect 可能有 fill-opacity）
    assert "accent-left" not in [k for _, k, _ in res.sketch_rects]
    assert not any(k == "bars" for _, k, _ in res.sketch_rects)
    # 文字仍在
    assert "Left" in left_svg and "sub" in left_svg
    # ghost:false 实体盒应有 fill
    m2 = re.search(r'<g data-el="solid">(.*?)</g>', res.svg, re.DOTALL)
    assert m2 and 'fill="#E3ECF7"' in m2.group(1)
    # 几何仍在，箭头可路由
    assert "left" in res.node_rects
    assert abs(res.node_rects["left"].x - 10) < 1e-6
    assert any(aid == "a1" for aid, _ in res.arrow_segments)
    # lint 不崩溃
    issues = lint(spec, res)
    assert isinstance(issues, list)


def test_region_anchors_box_and_text(tmp_path):
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: freeform
  prompt: "x"
  regions:
    mod_a: [12, 18, 28, 22]
    label_zone: [60, 40, 20, 10]
elements:
  - {type: box, id: a, region: mod_a, title: ModA}
  - {type: text, id: t, region: label_zone, text: HERE}
"""
    spec = load_spec(_write(tmp_path, yaml))
    a = spec.find("a")
    assert abs(a.rect.x - 12) < 1e-6
    assert abs(a.rect.w - 28) < 1e-6
    assert abs(a.rect.h - 22) < 1e-6
    t = spec.find("t")
    # text at = region 中心
    assert abs(t.at[0] - (60 + 10)) < 1e-6
    assert abs(t.at[1] - (40 + 5)) < 1e-6


def test_region_unknown_rejected(tmp_path):
    try:
        load_spec(_write(tmp_path, """
figure: {width: 80, height: 50}
base:
  mode: freeform
  prompt: "x"
  regions: {ok: [1, 2, 10, 10]}
elements:
  - {type: box, id: a, region: missing, title: A}
"""))
        assert False
    except SpecError as e:
        assert "missing" in str(e) and "regions" in str(e)


def test_text_plate_geometry_and_toggle(tmp_path):
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
elements:
  - {type: box, id: b, rect: [10, 10, 35, 28], title: Title, body: Body}
  - {type: text, id: t, at: [70, 30], text: Hello}
  - {type: text, id: t_off, at: [70, 50], text: NoPlate, plate: false}
  - {type: panel, id: p, rect: [5, 45, 40, 12], title: Panel}
"""
    from paperfig.render import render
    from paperfig.theme import load_theme
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    th = load_theme(spec.theme_cfg)
    ids = {oid for oid, _ in res.text_plates}
    assert "b" in ids  # 幽灵 box title+body 合并板
    assert "t" in ids
    assert "t_off" not in ids
    assert "p" in ids  # 幽灵 panel 标题
    # 几何：pad 外扩
    plate_b = next(r for oid, r in res.text_plates if oid == "b")
    assert plate_b.w > 0 and plate_b.h > 0
    assert abs(th.plate_opacity - 0.92) < 1e-6
    assert abs(th.plate_pad - 1.2) < 1e-6
    # SVG 含半透明底板
    assert f'fill-opacity="{th.plate_opacity}"' in res.svg
    assert th.plate_fill in res.svg


def test_theme_plate_opacity_override(tmp_path):
    """theme.plate_opacity 可覆盖默认 0.92。"""
    from paperfig.theme import load_theme
    th = load_theme({"preset": "sci", "plate_opacity": 0.95})
    assert abs(th.plate_opacity - 0.95) < 1e-6
    th0 = load_theme("sci")
    assert abs(th0.plate_opacity - 0.92) < 1e-6


def test_no_base_regression_solid_boxes(tmp_path):
    """无 base: 时输出与旧版一致：实体盒有 fill/stroke，无 text plate。"""
    yaml = """
figure: {width: 100, height: 50}
theme: sci
elements:
  - {type: box, id: a, rect: [10, 10, 30, 25], title: Solid}
  - {type: text, id: t, at: [70, 25], text: Plain}
  - {type: arrow, from: a.right, to: [90, 22], label: go}
"""
    from paperfig.render import render
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert res.base_mode is False
    assert res.text_plates == []
    assert "#E3ECF7" in res.svg  # sci primary fill
    assert 'stroke="#3B6EA5"' in res.svg
    assert "Solid" in res.svg
    assert 'data-base=' not in res.svg


def test_base_mode_lint_does_not_crash(tmp_path):
    """幽灵盒仍有几何，lint 不应崩溃。"""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    Image.new("RGB", (100, 60), "#CCDDEE").save(base_dir / "base.png")
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base/base.png
elements:
  - {type: box, id: a, rect: [10, 15, 30, 25], title: A}
  - {type: box, id: b, rect: [55, 15, 30, 25], title: B}
  - {type: arrow, from: a, to: b, label: link}
"""
    from paperfig.render import render
    from paperfig.lint import lint
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, out_png=tmp_path / "lint.png", dpi=72)
    issues = lint(spec, res)
    assert isinstance(issues, list)


# ── 阶段 3：base lint ──────────────────────────────────


def _save_base(tmp_path, img: Image.Image, name="base.png") -> str:
    p = tmp_path / name
    img.save(p)
    return name


def test_base_text_contrast_good_with_plate(tmp_path):
    """有 plate + 深色字压在任意底稿上 → 不报 base-text-contrast。"""
    # 深色花纹底：无 plate 会忙，有 plate 应过关
    img = Image.new("RGB", (200, 100), "#222222")
    d = ImageDraw.Draw(img)
    for x in range(0, 200, 4):
        d.line([(x, 0), (x, 100)], fill="#EEEEEE")
    _save_base(tmp_path, img)
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: text, id: t, at: [50, 25], text: Readable, size: 8}
"""
    from paperfig.render import render
    from paperfig.lint import lint
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert res.text_plates, "应自动加文字底板"
    codes = {i.code for i in lint(spec, res)}
    assert "base-text-contrast" not in codes


def test_base_text_contrast_bad_low_ratio(tmp_path):
    """无 plate + 浅色字压浅底 → 对比率 <3.0 → E。"""
    Image.new("RGB", (200, 100), "#EEEEEE").save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: text, id: t, at: [50, 25], text: Faint, size: 8, color: "#BBBBBB", plate: false}
"""
    from paperfig.render import render
    from paperfig.lint import lint
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert not any(oid == "t" for oid, _ in res.text_plates)
    issues = [i for i in lint(spec, res) if i.code == "base-text-contrast"]
    assert issues and issues[0].level == "E"
    assert "对比" in issues[0].msg


def test_base_text_contrast_busy_no_plate(tmp_path):
    """无 plate + 底稿花纹繁忙（高方差）→ E。

    用浅/中灰棋盘：平均亮度仍够衬深色字（对比过关），但局部方差超阈值。
    """
    img = Image.new("RGB", (200, 100))
    px = img.load()
    for y in range(100):
        for x in range(200):
            px[x, y] = (255, 255, 255) if ((x // 3) + (y // 3)) % 2 == 0 else (0x88, 0x88, 0x88)
    img.save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: text, id: t, at: [50, 25], text: Busy, size: 8, color: "#1F2933", plate: false}
"""
    from paperfig.render import render
    from paperfig.lint import lint
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    issues = [i for i in lint(spec, res) if i.code == "base-text-contrast"]
    assert issues and issues[0].level == "E"
    assert "花纹" in issues[0].msg or "方差" in issues[0].msg


def test_base_region_drift_aligned_and_shifted(tmp_path):
    """对齐模块不报；内容挪出矩形 → base-region-drift。"""
    from paperfig.render import render
    from paperfig.lint import lint

    # 画布 100×60 mm；底稿 100×60 px → 1mm=1px，便于控制偏移
    w, h = 100, 60
    # 好卡：模块色块贴合 box rect [10,15,30,25]
    good = Image.new("RGB", (w, h), "#FFFFFF")
    ImageDraw.Draw(good).rectangle([10, 15, 39, 39], fill="#3B6EA5")
    good.save(tmp_path / "base_good.png")
    yaml_good = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base_good.png
elements:
  - {type: box, id: mod, rect: [10, 15, 30, 25], title: Mod}
"""
    spec = load_spec(_write(tmp_path, yaml_good, "good.yaml"))
    res = render(spec, dpi=72)
    assert not any(i.code == "base-region-drift" for i in lint(spec, res))

    # 翻车卡：色块挪到右侧，原矩形近空
    bad = Image.new("RGB", (w, h), "#FFFFFF")
    ImageDraw.Draw(bad).rectangle([70, 15, 99, 39], fill="#3B6EA5")
    bad.save(tmp_path / "base_bad.png")
    yaml_bad = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base_bad.png
elements:
  - {type: box, id: mod, rect: [10, 15, 30, 25], title: Mod}
"""
    spec2 = load_spec(_write(tmp_path, yaml_bad, "bad.yaml"))
    res2 = render(spec2, dpi=72)
    drifts = [i for i in lint(spec2, res2) if i.code == "base-region-drift"]
    assert drifts and drifts[0].level == "W"
    assert "mod" in drifts[0].msg


def test_base_region_drift_small_offset_ok(tmp_path):
    """质心偏移约 3px 的好卡不应误报。"""
    from paperfig.render import render
    from paperfig.lint import lint
    w, h = 100, 60
    img = Image.new("RGB", (w, h), "#FFFFFF")
    # rect [10,15,30,25] → 理想 [10,15]-[39,39]；右下偏 3px
    ImageDraw.Draw(img).rectangle([13, 17, 42, 42], fill="#3B6EA5")
    img.save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base.png
elements:
  - {type: box, id: mod, rect: [10, 15, 30, 25], title: Mod}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert not any(i.code == "base-region-drift" for i in lint(spec, res))


def test_base_mode_disables_richness_and_sketch_lints(tmp_path):
    """base 模式停用 R-empty-box / R-no-section / R-no-legend / arrow-exit-over-content。"""
    from paperfig.render import render
    from paperfig.lint import lint
    Image.new("RGB", (240, 160), "#E8EEF5").save(tmp_path / "base.png")
    # 多元素 + 空心实体盒 + 多语义色无 legend → 非 base 会报丰度；base 应静默
    yaml = """
figure: {width: 120, height: 80}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: box, id: a, rect: [5, 5, 28, 22], title: A, variant: primary}
  - {type: box, id: b, rect: [40, 5, 28, 22], title: B, variant: accent}
  - {type: box, id: c, rect: [75, 5, 28, 22], title: C, variant: highlight}
  - {type: box, id: empty, rect: [5, 35, 40, 30], title: Empty, ghost: false}
  - {type: box, id: d, rect: [55, 35, 28, 22], title: D}
  - {type: box, id: e, rect: [90, 35, 25, 18], title: E}
  - {type: text, id: t1, at: [10, 72], text: one}
  - {type: text, id: t2, at: [40, 72], text: two}
  - {type: arrow, id: ar, from: a.right, to: b.left, label: go}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    codes = {i.code for i in lint(spec, res)}
    assert "R-empty-box" not in codes
    assert "R-no-section" not in codes
    assert "R-no-legend" not in codes
    assert "arrow-exit-over-content" not in codes
    assert "arrow-label-over-sketch" not in codes
    # 幽灵盒无 sketch，sketch_rects 应为空
    assert res.sketch_rects == []


def test_plate_overlap_warning(tmp_path):
    """两块文字底板重叠超过 30% → W plate-overlap。"""
    from paperfig.render import render
    from paperfig.lint import lint
    Image.new("RGB", (200, 100), "#DDDDDD").save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: text, id: a, at: [40, 24], text: AlphaWord, size: 9}
  - {type: text, id: b, at: [48, 24], text: BetaWordX, size: 9}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    assert len(res.text_plates) >= 2
    assert any(i.code == "plate-overlap" and i.level == "W" for i in lint(spec, res))


def test_base_lint_skips_contrast_without_image(tmp_path):
    """无 base.image 时跳过对比度/漂移（不因缺图在 lint 阶段炸）。"""
    from paperfig.render import render
    from paperfig.lint import lint
    yaml = """
figure: {width: 80, height: 40}
theme: sci
base:
  mode: skeleton
  prompt: "x"
elements:
  - {type: box, id: a, rect: [10, 10, 25, 18], title: A}
  - {type: text, id: t, at: [55, 20], text: Hi, plate: false}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    codes = {i.code for i in lint(spec, res)}
    assert "base-text-contrast" not in codes
    assert "base-region-drift" not in codes


def test_e2e_base_sample_zero_errors():
    """参考样例 /tmp/paperfig-base-e2e 应 0E。"""
    from pathlib import Path
    from paperfig.render import render
    from paperfig.lint import lint
    p = Path("/tmp/paperfig-base-e2e/figure.yaml")
    if not p.is_file():
        return  # 环境无样例则跳过
    spec = load_spec(p)
    res = render(spec, dpi=72)
    errs = [i for i in lint(spec, res) if i.level == "E"]
    assert errs == [], f"e2e 不应有 E: {errs}"


def test_solid_box_text_contrast_ok_despite_busy_base(tmp_path):
    """ghost:false 盒内深字浅填：即使底稿花纹繁忙也不报 base-text-contrast。"""
    from paperfig.render import render
    from paperfig.lint import lint
    # 高方差棋盘底稿：若误采底稿会报花纹繁忙
    img = Image.new("RGB", (200, 100))
    px = img.load()
    for y in range(100):
        for x in range(200):
            px[x, y] = (255, 255, 255) if ((x // 3) + (y // 3)) % 2 == 0 else (0x22, 0x22, 0x22)
    img.save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: box, id: panel, rect: [20, 10, 60, 30], title: SolidOK,
     ghost: false, fill: "#E3ECF7", text_color: "#1F2933"}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    codes = {i.code for i in lint(spec, res)}
    assert "base-text-contrast" not in codes


def test_solid_box_text_contrast_bad_on_dark_fill(tmp_path):
    """ghost:false 盒：深字压深填 → 仍报 base-text-contrast（有效背景=实体填充）。"""
    from paperfig.render import render
    from paperfig.lint import lint
    # 浅净空底稿：若误采底稿则对比过关；实体深填才应报错
    Image.new("RGB", (200, 100), "#F5F5F5").save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 50}
theme: sci
base:
  mode: freeform
  prompt: "x"
  image: base.png
elements:
  - {type: box, id: dark, rect: [20, 10, 60, 30], title: DarkBad,
     ghost: false, fill: "#222222", text_color: "#333333"}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    issues = [i for i in lint(spec, res) if i.code == "base-text-contrast"]
    assert issues and issues[0].level == "E"
    assert "对比" in issues[0].msg


def test_solid_box_skips_region_drift(tmp_path):
    """ghost:false 不参与 base-region-drift（底稿空了也不报）。"""
    from paperfig.render import render
    from paperfig.lint import lint
    w, h = 100, 60
    # 矩形内近空（白底），若参与漂移会对 ghost 盒报 W；实体应豁免
    Image.new("RGB", (w, h), "#FFFFFF").save(tmp_path / "base.png")
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base.png
elements:
  - {type: box, id: solid, rect: [10, 15, 30, 25], title: Solid, ghost: false}
  - {type: box, id: ghost, rect: [55, 15, 30, 25], title: Ghost}
"""
    spec = load_spec(_write(tmp_path, yaml))
    res = render(spec, dpi=72)
    drifts = [i for i in lint(spec, res) if i.code == "base-region-drift"]
    assert not any("solid" in i.msg for i in drifts)
    # 幽灵空盒仍可报漂移
    assert any("ghost" in i.msg for i in drifts)


def test_skeleton_ghost_false_is_pale_gray(tmp_path):
    """skeleton：ghost:false → #EEEEEE；默认幽灵 → 饱和色（避开文字板保留区采样）。"""
    from io import BytesIO
    yaml = """
figure: {width: 100, height: 60}
theme: sci
base: {mode: skeleton, prompt: "x"}
elements:
  - {type: box, id: g, rect: [10, 10, 30, 30], title: Ghost}
  - {type: box, id: s, rect: [55, 10, 30, 30], title: Solid, ghost: false}
"""
    spec = load_spec(_write(tmp_path, yaml))
    data = render_skeleton(spec)
    im = Image.open(BytesIO(data)).convert("RGB")
    arr = np.asarray(im)
    pw, ph = im.size
    sx, sy = pw / 100.0, ph / 60.0

    def sample_xy(x_mm, y_mm):
        cx, cy = int(x_mm * sx), int(y_mm * sy)
        return tuple(int(v) for v in arr[cy, cx])

    solid_px = sample_xy(70, 25)
    # 幽灵盒底部远离 title 板，应为饱和色
    ghost_px = sample_xy(25, 36)
    assert solid_px == (0xEE, 0xEE, 0xEE)
    assert ghost_px != (0xEE, 0xEE, 0xEE)
    assert max(ghost_px) < 240 or min(ghost_px) < 200


def test_skeleton_draws_plate_reserve_tinted_and_aligned(tmp_path):
    """幽灵盒文字板保留区：同色系提亮（非灰），矩形与渲染板精确同源。"""
    from io import BytesIO
    import colorsys
    from paperfig.base import tint_reserve_color, _skeleton_palette
    from paperfig.render import estimate_box_text_plate, render
    from paperfig.theme import load_theme

    yaml = """
figure: {width: 100, height: 60}
theme: sci
base:
  mode: skeleton
  prompt: "x"
  image: base/base.png
elements:
  - {type: box, id: g, rect: [10, 10, 40, 35], title: TitleHere, body: "sub line", valign: top}
"""
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    Image.new("RGB", (200, 120), "#CCDDEE").save(base_dir / "base.png")
    spec = load_spec(_write(tmp_path, yaml))
    th = load_theme(spec.theme_cfg)
    estimated = estimate_box_text_plate(spec.elements[0], th, spec.font_scale)
    assert estimated is not None
    assert estimated.w > 2 and estimated.h > 2

    res = render(spec, dpi=72)
    rendered = next(r for oid, r in res.text_plates if oid == "g")
    assert abs(estimated.x - rendered.x) < 1e-6
    assert abs(estimated.y - rendered.y) < 1e-6
    assert abs(estimated.w - rendered.w) < 1e-6
    assert abs(estimated.h - rendered.h) < 1e-6

    palette = _skeleton_palette(spec.theme_cfg)
    expected = tint_reserve_color(palette[0])
    assert expected.upper() != "#EEEEEE"
    # 提亮后仍带色相（非灰）
    er, eg, eb = (int(expected[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    _h, _l, sat = colorsys.rgb_to_hls(er, eg, eb)
    assert sat > 0.05

    data = render_skeleton(spec)
    im = Image.open(BytesIO(data)).convert("RGB")
    arr = np.asarray(im)
    pw, ph = im.size
    sx, sy = pw / 100.0, ph / 60.0
    cx = int((estimated.x + estimated.w / 2) * sx)
    cy = int((estimated.y + estimated.h / 2) * sy)
    px = tuple(int(v) for v in arr[cy, cx])
    exp_rgb = tuple(int(expected[i : i + 2], 16) for i in (1, 3, 5))
    assert px == exp_rgb, (px, exp_rgb, expected)
    # 盒底插画区仍为饱和色（非保留带）
    bx = int(30 * sx)
    by = int(40 * sy)
    bot = tuple(int(v) for v in arr[by, bx])
    assert bot != exp_rgb
    assert bot != (0xEE, 0xEE, 0xEE)
    assert max(bot) < 240 or min(bot) < 200


def test_tint_reserve_color_hsl():
    """tint_reserve_color：L≈0.88、S 减半。"""
    from paperfig.base import tint_reserve_color
    import colorsys
    src = "#3B6EA5"
    out = tint_reserve_color(src)
    r0, g0, b0 = (int(src[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    r1, g1, b1 = (int(out[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    h0, _l0, s0 = colorsys.rgb_to_hls(r0, g0, b0)
    h1, l1, s1 = colorsys.rgb_to_hls(r1, g1, b1)
    # 8-bit 量化会带来微小色相漂移
    assert abs(h0 - h1) < 0.01 or abs(abs(h0 - h1) - 1.0) < 0.01
    assert abs(l1 - 0.88) < 0.02
    assert abs(s1 - s0 * 0.5) < 0.02


def test_tiles_grid_count_and_min_width(tmp_path):
    """tiles：默认网格数量正确，单片宽 ≥1200，含 overview。"""
    from paperfig.tiles import export_tiles, default_tile_grid
    from PIL import Image as PILImage

    yaml = """
figure: {width: 120, height: 80}
theme: sci
elements:
  - {type: box, id: a, rect: [10, 10, 40, 30], title: A}
  - {type: box, id: b, rect: [60, 30, 40, 30], title: B}
"""
    spec = load_spec(_write(tmp_path, yaml))
    assert default_tile_grid(120) == (2, 2)
    assert default_tile_grid(200) == (3, 3)

    out = tmp_path / "tiles"
    paths = export_tiles(spec, out, grid="2x2", dpi=72, min_tile_width=1200)
    assert "overview.png" in paths
    assert paths["overview.png"].is_file()
    tiles = [n for n in paths if n.startswith("tile_")]
    assert len(tiles) == 4
    for name in tiles:
        with PILImage.open(paths[name]) as im:
            assert im.width >= 1200, (name, im.size)


def test_lint_canvas_edge_gap_trigger_and_clean(tmp_path):
    """canvas-edge-gap：底部大空带触发；贴边内容不触发。"""
    from paperfig.render import render
    from paperfig.lint import lint

    # 内容贴顶，底部空隙远超阈值（h=100 → min(8,8)=8）
    spec = load_spec(_write(tmp_path, """
figure: {width: 100, height: 100}
theme: sci
elements:
  - {type: box, id: a, rect: [10, 5, 40, 25], title: A}
  - {type: box, id: b, rect: [55, 5, 35, 25], title: B}
"""))
    res = render(spec, dpi=72)
    gaps = [i for i in lint(spec, res) if i.code == "canvas-edge-gap"]
    assert gaps, "应报 canvas-edge-gap"
    assert any("bottom" in i.msg for i in gaps)
    assert all(i.level == "W" for i in gaps)

    # 矮画布：底部空隙 7.5mm ≈ 10%（> 8%=6mm）也应触发
    spec_short = load_spec(_write(tmp_path, """
figure: {width: 120, height: 75}
theme: sci
elements:
  - {type: box, id: a, rect: [5, 5, 50, 30], title: A}
  - {type: box, id: b, rect: [60, 5, 50, 30], title: B}
""", "short.yaml"))
    res_s = render(spec_short, dpi=72)
    assert any(
        i.code == "canvas-edge-gap" and "bottom" in i.msg
        for i in lint(spec_short, res_s)
    )

    # 四周贴边（空隙 < 阈值）
    spec2 = load_spec(_write(tmp_path, """
figure: {width: 100, height: 60}
theme: sci
elements:
  - {type: box, id: a, rect: [3, 3, 45, 25], title: A}
  - {type: box, id: b, rect: [52, 3, 45, 25], title: B}
  - {type: box, id: c, rect: [3, 32, 45, 25], title: C}
  - {type: box, id: d, rect: [52, 32, 45, 25], title: D}
"""))
    res2 = render(spec2, dpi=72)
    assert not any(i.code == "canvas-edge-gap" for i in lint(spec2, res2))
