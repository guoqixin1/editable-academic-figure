"""AI 整图底稿：骨架导出、底稿抽卡、mm 网格叠加。

阶段 1 管线（渲染主链阶段 2 再接 base.image）：
  skeleton 模式 → render_skeleton → urls 参考图图生图 → base_gacha
  freeform 模式 → base_gacha → base_grid 辅助标注 regions
"""

from __future__ import annotations

import colorsys
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .assets import (
    DEFAULT_MODEL,
    Candidate,
    GachaResult,
    _generate_one,
    build_style_pack,
    resolve_style_for_assets,
)
from .spec import AssetEl, ArrowEl, BoxEl, FigureSpec, PanelEl, load_spec
from .theme import load_theme

# 底稿版硬约束（与素材版不同：整幅构图、不抠图、模块落点）
_BASE_HARD_CONSTRAINTS = """\
HARD CONSTRAINTS (full-bleed base illustration, NOT a cutout asset):
- No text, letters, numbers, captions, watermarks, logos, or UI chrome anywhere in the image.
- 画面禁止任何文字、字母、数字、水印或标识。
- Full-bleed composition filling the entire canvas; this is a complete scene/layout, \
NOT a single centered object with large empty margins.
- 整幅构图铺满画布，不是单物件居中素材。
- Place visual modules according to the colored block regions in the reference layout \
image when a reference is provided (skeleton mode); keep module positions aligned.
- 若有参考图（skeleton 模式），模块区域按参考图色块位置摆放，不得大幅漂移。
- Leave modest clear space inside each module and near intended label landing zones \
(light/plain areas for later text overlay).
- 每个模块内部及标签落点区留适度净空（浅色/平整区域，供后续叠字）。
- White or extremely light background only; no heavy textures or dark full-bleed washes.
- 白色或极浅背景。
- Do not draw outer frames, borders, page margins, or decorative chrome around the canvas.
- 不要画外框/边框装饰。
- Flat vector illustration language consistent with the theme style pack above; \
no photorealism, no 3D render, no neon glow.
- 扁平矢量插画风格与主题一致。"""

# skeleton 强编辑指令：弱 prompt 会原样吐色块，必须明确 replace fill + 禁止几何变换
_SKELETON_EDIT_INSTRUCTIONS = """\
- Image edit / restyle (CRITICAL): Replace each colored block's flat fill with \
illustrated content (detailed flat-vector scientific modules inside each panel). \
Keep every module's position and footprint exactly. Do not translate, scale, merge \
or split modules. Do not reorder or resize panels.
- Light tinted header bands inside modules (same hue, much lighter) and pale-gray \
blocks are reserved for later text plates — keep them plain and unillustrated.
- 强编辑：把每个色块的平面填充替换成插画内容；保持每个模块的位置与 footprint \
完全不变；禁止平移、缩放、合并或拆分模块。
- 模块内同色系浅色头带与浅灰占位块为文字板保留区：保持平面净空，不要插画或装饰。"""

# nano-banana 常见宽高比；按 figure mm 比例就近匹配
_ASPECT_CHOICES: list[tuple[float, str]] = [
    (1 / 1, "1:1"),
    (16 / 9, "16:9"),
    (9 / 16, "9:16"),
    (4 / 3, "4:3"),
    (3 / 4, "3:4"),
    (3 / 2, "3:2"),
    (2 / 3, "2:3"),
    (5 / 4, "5:4"),
    (4 / 5, "4:5"),
    (21 / 9, "21:9"),
]

_SKELETON_LONG_SIDE = 1024
_PANEL_FILL = "#F0F3F7"
# ghost:false 实体区 / 箭头胶囊在骨架上的占位色：告知模型勿插画（最终被矢量盖住）
_SKELETON_RESERVED_FILL = "#EEEEEE"


def _skeleton_is_ghost(el) -> bool:
    """骨架导出时的幽灵判定：与 base 渲染一致（None → 幽灵）。"""
    g = getattr(el, "ghost", None)
    return True if g is None else bool(g)


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb01_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def tint_reserve_color(fill: str, *, lightness: float = 0.88) -> str:
    """同色系提亮：HSL 上 L→lightness、S 减半，作幽灵盒文字板保留带色。"""
    r, g, b = _hex_to_rgb01(fill)
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    nr, ng, nb = colorsys.hls_to_rgb(h, lightness, s * 0.5)
    return _rgb01_to_hex(nr, ng, nb)


def figure_aspect_ratio(width_mm: float, height_mm: float) -> str:
    """figure 宽高 → 最近的 API aspectRatio 字符串。"""
    if height_mm <= 0:
        return "16:9"
    r = width_mm / height_mm
    return min(_ASPECT_CHOICES, key=lambda t: abs(t[0] - r))[1]


def base_pixel_size(
    width_mm: float,
    height_mm: float,
    long_side: int = _SKELETON_LONG_SIDE,
) -> tuple[int, int]:
    """导出像素尺寸：长边 = long_side，短边按比例，与 aspectRatio 对应。"""
    if width_mm <= 0 or height_mm <= 0:
        return long_side, long_side
    if width_mm >= height_mm:
        w = long_side
        h = max(1, round(long_side * height_mm / width_mm))
    else:
        h = long_side
        w = max(1, round(long_side * width_mm / height_mm))
    return w, h


def _skeleton_palette(theme_cfg: dict | str | None) -> list[str]:
    """饱和区分色：优先 theme variant 描边色，循环取用。"""
    th = load_theme(theme_cfg)
    colors: list[str] = []
    seen: set[str] = set()
    for name in ("primary", "secondary", "accent", "highlight", "tertiary", "dark"):
        v = th.variants.get(name)
        if v is None:
            continue
        c = v.stroke.upper()
        if c in seen:
            continue
        # 跳过近白，保证色块可辨
        if c in ("#FFFFFF", "#FFF", "#FAFAFA", "#F7F7F7"):
            continue
        colors.append(v.stroke)
        seen.add(c)
    if not colors:
        colors = ["#3B6EA5", "#5B8266", "#C77D2E", "#B5493A"]
    return colors


def render_skeleton(
    spec: FigureSpec,
    out_path: str | Path | None = None,
    *,
    long_side: int = _SKELETON_LONG_SIDE,
) -> Path | bytes:
    """渲无文字色块骨架图：幽灵 box/asset 饱和色块，ghost:false 浅灰占位；白底。

    panel 默认极浅底；ghost:false panel 同样用浅灰保留区。
    幽灵盒的 title/body 文字板矩形与渲染板同源（零外扩），填该盒色块的同色系提亮
    （读作模块浅色头带，而非灰补丁）。
    像素尺寸与底稿目标一致（长边 long_side）。返回写入路径，或未给 out_path 时返回 PNG bytes。
    """
    from .render import (
        estimate_arrow_label_capsule,
        estimate_box_text_plate,
        visual_rect_for,
    )

    pw, ph = base_pixel_size(spec.width, spec.height, long_side=long_side)
    sx = pw / spec.width
    sy = ph / spec.height
    palette = _skeleton_palette(spec.theme_cfg)
    th = load_theme(spec.theme_cfg)

    img = Image.new("RGB", (pw, ph), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    color_i = 0
    ghost_fills: dict[str, str] = {}

    def _fill_rect_mm(r, fill: str) -> None:
        box = [
            round(r.x * sx),
            round(r.y * sy),
            round(r.right * sx) - 1,
            round(r.bottom * sy) - 1,
        ]
        draw.rectangle(box, fill=fill, outline=fill)

    # panel 先画（极浅底 / 实体保留浅灰），再画 box/asset
    for el in spec.elements:
        if isinstance(el, PanelEl):
            r = el.rect
            box = [
                round(r.x * sx),
                round(r.y * sy),
                round(r.right * sx) - 1,
                round(r.bottom * sy) - 1,
            ]
            if _skeleton_is_ghost(el):
                draw.rectangle(box, fill=_PANEL_FILL, outline="#D5DCE5")
            else:
                draw.rectangle(
                    box, fill=_SKELETON_RESERVED_FILL, outline=_SKELETON_RESERVED_FILL,
                )

    node_rects: dict[str, Any] = {}
    node_visual: dict[str, Any] = {}
    for el in spec.elements:
        if not isinstance(el, (BoxEl, AssetEl)):
            continue
        r = el.rect
        node_rects[el.id] = r
        node_visual[el.id] = visual_rect_for(el)
        if _skeleton_is_ghost(el):
            fill = palette[color_i % len(palette)]
            color_i += 1
            ghost_fills[el.id] = fill
        else:
            fill = _SKELETON_RESERVED_FILL
        box = [
            round(r.x * sx),
            round(r.y * sy),
            round(r.right * sx) - 1,
            round(r.bottom * sy) - 1,
        ]
        draw.rectangle(box, fill=fill, outline=fill)

    # 幽灵盒文字板保留区：与渲染板同源矩形 + 模块同色系提亮
    for el in spec.elements:
        if not isinstance(el, BoxEl):
            continue
        if not _skeleton_is_ghost(el):
            continue
        plate = getattr(el, "plate", None)
        if plate is False:
            continue
        pret = estimate_box_text_plate(el, th, spec.font_scale)  # expand_mm=0
        if pret is not None:
            base_fill = ghost_fills.get(el.id, palette[0])
            _fill_rect_mm(pret, tint_reserve_color(base_fill))

    # 箭头标签胶囊：仅静态可算（显式 offset / 非 auto）；auto/avoid 不画
    for el in spec.elements:
        if not isinstance(el, ArrowEl):
            continue
        cap = estimate_arrow_label_capsule(
            el, node_rects, th, spec.font_scale, node_visual_rects=node_visual,
        )
        if cap is not None:
            _fill_rect_mm(cap, _SKELETON_RESERVED_FILL)

    if out_path is None:
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return out


def build_base_prompt(
    scene_prompt: str,
    theme_cfg: dict | str | None = None,
    assets_style: str | None = None,
    style_pack: str | None = None,
    *,
    skeleton: bool = False,
) -> str:
    """组装底稿生图 prompt：场景描述 + 主题风格包 + 底稿版硬约束。"""
    subject = (scene_prompt or "").rstrip("。，,.\n ")
    pack = style_pack if style_pack is not None else build_style_pack(theme_cfg, assets_style)
    extra = f"\n{_SKELETON_EDIT_INSTRUCTIONS}" if skeleton else ""
    return f"{subject}\n\n{pack}\n\n{_BASE_HARD_CONSTRAINTS}{extra}"


def _score_base_image(path: Path) -> tuple[float, str, dict[str, Any]]:
    """底稿客观分：分辨率 + 是否近乎全白/全黑废图。不做抠图相关项。"""
    report: dict[str, Any] = {"ok": False, "path": str(path)}
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            report["width"] = w
            report["height"] = h
            # 采样均值亮度
            small = im.resize((64, 64), Image.BILINEAR)
            pixels = list(small.getdata())
            n = len(pixels)
            mean_l = sum((0.299 * r + 0.587 * g + 0.114 * b) for r, g, b in pixels) / n
            report["mean_luma"] = round(mean_l, 2)
            # 近乎均匀：方差极低
            var = sum(
                (0.299 * r + 0.587 * g + 0.114 * b - mean_l) ** 2 for r, g, b in pixels
            ) / n
            report["luma_var"] = round(var, 2)
    except Exception as e:  # noqa: BLE001
        report["reason"] = f"无法读取: {e}"
        return 0.0, "reject", report

    score = 100.0
    notes_warn = False
    long_side = max(w, h)
    if long_side < 512:
        score -= 40
        notes_warn = True
        report["reason"] = f"分辨率过低 long_side={long_side}"
    elif long_side < 768:
        score -= 15
        notes_warn = True

    # 近乎全白 / 全黑
    if mean_l > 250 and var < 20:
        score -= 50
        notes_warn = True
        report["reason"] = "近乎全白废图"
    elif mean_l < 8 and var < 20:
        score -= 50
        notes_warn = True
        report["reason"] = "近乎全黑废图"
    elif var < 5:
        score -= 25
        notes_warn = True
        report["reason"] = "画面近乎无内容（方差过低）"

    report["ok"] = score >= 45
    if score < 45:
        return max(score, 0.0), "reject", report
    return score, ("warn" if notes_warn else "ok"), report


def _contact_sheet_base(cands: list[Candidate], out_path: Path, cell: int = 360) -> None:
    """底稿 contact sheet：不透明整图，无棋盘透明底。"""
    ok = [c for c in cands if c.cut_path]
    if not ok:
        return
    n = len(ok)
    pad, header = 12, 44
    sheet = Image.new("RGB", (n * cell + (n + 1) * pad, cell + header + 2 * pad), "#DDE3EA")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 22, index=2
        )
    except OSError:
        font = ImageFont.load_default()

    for i, c in enumerate(ok):
        x0 = pad + i * (cell + pad)
        tile = Image.new("RGB", (cell, cell), "#FFFFFF")
        with Image.open(c.cut_path) as im:
            im = im.convert("RGB")
            im.thumbnail((cell - 16, cell - 16), Image.LANCZOS)
            tile.paste(im, ((cell - im.width) // 2, (cell - im.height) // 2))
        sheet.paste(tile, (x0, pad + header))
        color = {"ok": "#1F7A3D", "warn": "#B26B00", "reject": "#B3261E"}[c.verdict]
        draw.text(
            (x0 + 6, pad + 8),
            f"#{c.index}  {c.score:.0f}分 {c.verdict}",
            fill=color,
            font=font,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)


def base_gacha(
    spec_path: str | Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    force: bool = False,
    *,
    candidates: int | None = None,
    reference_image: str | Path | None = None,
) -> GachaResult:
    """底稿抽卡：并发 N 候选、下载、contact sheet、报告；不做抠图。

    产物目录：spec 旁 base/candidates/base_N.png、contact_sheet_base.png、gacha_report.json。
    """
    spec = load_spec(spec_path)
    if spec.base is None:
        raise ValueError("spec 缺少 base: 段")

    base_dir = spec.base_dir()
    cand_dir = base_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    final_path = base_dir / "base.png"

    n = candidates if candidates is not None else spec.base.candidates
    n = max(1, int(n))
    result = GachaResult(asset_id="base", prompt=spec.base.prompt)

    if final_path.exists() and not force:
        print(f"[跳过] 底稿已存在: {final_path}（加 --force 重抽）")
        return result

    pack, theme_cfg, assets_style = resolve_style_for_assets(
        base_dir,
        theme_cfg=spec.theme_cfg,
        spec_path=spec.path,
    )
    full_prompt = build_base_prompt(
        spec.base.prompt,
        theme_cfg=theme_cfg,
        assets_style=assets_style,
        style_pack=pack,
        skeleton=(spec.base.mode == "skeleton"),
    )
    result.full_prompt = full_prompt
    aspect = figure_aspect_ratio(spec.width, spec.height)

    ref_images: list[str] | None = None
    if reference_image is not None:
        ref = Path(reference_image)
        if ref.is_file():
            ref_images = [str(ref)]
            print(f"[参考图] urls ← {ref}")
        else:
            print(f"[WARN] 参考图不存在，按纯文生图提交: {ref}", file=sys.stderr)

    print(f"[底稿抽卡] mode={spec.base.mode} ×{n} aspect={aspect}")
    raw_paths = [cand_dir / f"base_{i + 1}.png" for i in range(n)]
    if force:
        for rp in raw_paths:
            rp.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=min(n, 4)) as pool:
        futures = [
            pool.submit(
                _generate_one,
                full_prompt,
                api_key,
                aspect,
                model,
                rp,
                ref_images,
            )
            if not rp.exists()
            else None
            for rp in raw_paths
        ]
        oks = [f.result() if f else True for f in futures]

    for i, (raw, ok) in enumerate(zip(raw_paths, oks)):
        idx = i + 1
        if not ok or not raw.exists():
            result.candidates.append(
                Candidate(
                    index=idx,
                    raw_path=str(raw),
                    cut_path=None,
                    report={"ok": False, "reason": "生成/下载失败"},
                    score=0.0,
                    verdict="reject",
                )
            )
            continue
        score, verdict, report = _score_base_image(raw)
        result.candidates.append(
            Candidate(
                index=idx,
                raw_path=str(raw),
                cut_path=str(raw),  # 不做抠图，整图即候选
                report=report,
                score=score,
                verdict=verdict,
            )
        )
        print(
            f"  #{idx}: {score:.0f}分 {verdict}"
            f"  {report.get('width', '?')}x{report.get('height', '?')}"
            f"  luma={report.get('mean_luma', '?')}"
            f"{'  ' + report['reason'] if report.get('reason') else ''}"
        )

    sheet = base_dir / "contact_sheet_base.png"
    _contact_sheet_base(result.candidates, sheet)
    if sheet.exists():
        result.contact_sheet = str(sheet)
        print(f"  对比图: {sheet}")

    # 写报告
    report_path = base_dir / "gacha_report.json"
    payload = [
        {
            "asset_id": result.asset_id,
            "prompt": result.prompt,
            "full_prompt": result.full_prompt,
            "selected": result.selected,
            "contact_sheet": result.contact_sheet,
            "mode": spec.base.mode,
            "aspect": aspect,
            "candidates": [asdict(c) for c in result.candidates],
        }
    ]
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  报告: {report_path}")
    return result


def pick_base(spec_path: str | Path, index: int) -> Path:
    """把候选 #index 提升为 base/base.png，并尽量回写 base.image。"""
    spec = load_spec(spec_path)
    if spec.base is None:
        raise ValueError("spec 缺少 base: 段")
    base_dir = spec.base_dir()
    src = base_dir / "candidates" / f"base_{index}.png"
    if not src.exists():
        raise FileNotFoundError(f"候选不存在: {src}")
    dst = base_dir / "base.png"
    shutil.copyfile(src, dst)
    print(f"[选卡] base ← 候选#{index} → {dst}")

    rel = "base/base.png"
    rewritten = _rewrite_base_image(spec.path, rel)
    if rewritten:
        print(f"[回写] {spec.path.name} base.image → {rel}")
    else:
        print(f"[提示] 请在 spec 中设置 base.image: {rel}")
    return dst


def _rewrite_base_image(spec_path: Path, rel: str) -> bool:
    """尽力回写 YAML 的 base.image；保留原格式失败则返回 False。"""
    text = Path(spec_path).read_text(encoding="utf-8")
    # 已有 image: 行（含 null / 空）
    pat = re.compile(
        r"(^base:\s*\n(?:^[ \t]+.*\n)*?)([ \t]+)image:\s*.*$",
        re.MULTILINE,
    )
    m = pat.search(text)
    if m:
        new_text = pat.sub(
            lambda mo: mo.group(1) + mo.group(2) + f"image: {rel}",
            text,
            count=1,
        )
        if new_text != text:
            Path(spec_path).write_text(new_text, encoding="utf-8")
            return True
    # base: 段存在但无 image 行：在 base: 后插入
    m2 = re.search(r"^base:\s*$", text, re.MULTILINE)
    if m2:
        insert_at = m2.end()
        new_text = text[:insert_at] + f"\n  image: {rel}" + text[insert_at:]
        Path(spec_path).write_text(new_text, encoding="utf-8")
        return True
    return False


def overlay_mm_grid(
    spec: FigureSpec,
    src_png: str | Path | None = None,
    out_path: str | Path | None = None,
    *,
    major_mm: float = 10.0,
    minor_mm: float = 5.0,
) -> Path:
    """在底稿上叠 mm 网格（主线 major、细线 minor、坐标标注），尺寸不变。

    默认读 base/base.png，写出 base/base_grid.png。
    """
    if src_png is None:
        src = spec.base_dir() / "base.png"
    else:
        src = Path(src_png)
    if not src.is_file():
        raise FileNotFoundError(f"底稿不存在: {src}")

    with Image.open(src) as im:
        img = im.convert("RGBA")
        w, h = img.size

    sx = w / spec.width
    sy = h / spec.height
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            max(10, round(2.2 * sx)),
        )
    except OSError:
        font = ImageFont.load_default()

    # 细线
    x = 0.0
    while x <= spec.width + 1e-6:
        px = round(x * sx)
        is_major = abs(x % major_mm) < 1e-6 or abs(x % major_mm - major_mm) < 1e-6
        if not is_major and minor_mm > 0:
            draw.line([(px, 0), (px, h)], fill=(255, 0, 170, 70), width=1)
        x += minor_mm if minor_mm > 0 else major_mm

    y = 0.0
    while y <= spec.height + 1e-6:
        py = round(y * sy)
        is_major = abs(y % major_mm) < 1e-6 or abs(y % major_mm - major_mm) < 1e-6
        if not is_major and minor_mm > 0:
            draw.line([(0, py), (w, py)], fill=(255, 0, 170, 70), width=1)
        y += minor_mm if minor_mm > 0 else major_mm

    # 主线 + 标注
    x = 0.0
    while x <= spec.width + 1e-6:
        px = round(x * sx)
        draw.line([(px, 0), (px, h)], fill=(255, 0, 170, 160), width=1)
        label = f"{int(x) if abs(x - round(x)) < 1e-6 else x:g}"
        draw.text((px + 2, 2), label, fill=(255, 0, 170, 200), font=font)
        x += major_mm

    y = 0.0
    while y <= spec.height + 1e-6:
        py = round(y * sy)
        draw.line([(0, py), (w, py)], fill=(255, 0, 170, 160), width=1)
        if y > 0:
            label = f"{int(y) if abs(y - round(y)) < 1e-6 else y:g}"
            draw.text((2, py + 2), label, fill=(255, 0, 170, 200), font=font)
        y += major_mm

    out = Image.alpha_composite(img, overlay).convert("RGB")
    if out_path is None:
        dst = spec.base_dir() / "base_grid.png"
    else:
        dst = Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, format="PNG")
    # 尺寸不变
    assert out.size == (w, h)
    return dst
