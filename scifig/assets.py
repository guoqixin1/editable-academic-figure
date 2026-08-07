"""素材抽卡管线：生成 N 个候选 → 抠图 → 自动评分 → contact sheet → 选卡。

生图模型的产出天然不稳定（"抽卡"），管线的对策是：
- 每个素材一次请求多个候选（默认 3），并发提交；
- 抠图报告（前景占比 / 连通块数 / 是否贴边）转成客观分，先过滤明显废卡；
- 所有候选拼成 contact sheet，供 agent 视觉评审或用户人工选卡；
- 选中的候选复制为正式素材 {id}.png，其余保留在 candidates/ 里可回退换卡。

目录结构（assets_dir 下）：
  {id}.png                 选中的正式素材（透明底）
  candidates/{id}_1.png    候选（已抠图）
  candidates/{id}_1.raw.png 原始白底图（保留以便换参数重抠）
  contact_sheet_{id}.png   候选对比图
  gacha_report.json        全部候选的评分与选择记录
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml
from PIL import Image, ImageDraw, ImageFont

from .cutout import CutoutReport, cutout_white_bg
from .spec import AssetRequest

API_HOST = "https://grsai.dakka.com.cn"
DRAW_ENDPOINT = "/v1/draw/nano-banana"
RESULT_ENDPOINT = "/v1/draw/result"
DEFAULT_MODEL = "nano-banana-fast"

# 旧后缀保留作兼容别名；新管线改用 build_full_prompt + 图级风格包。
PROMPT_SUFFIX = (
    "，扁平插画风格，简洁的科研图示素材，背景纯白色，我会根据像素值做裁剪背景一定要纯白"
    "不能有其他色块，画面中不要出现任何文字字母或数字"
)

# 各 theme preset 的素材配色（stroke/accent hex）。未知 preset 回退 sci。
# topconf / airy 由并行 theme 任务新增；此处按名字映射即可。
_ASSET_PALETTES: dict[str, list[str]] = {
    "sci": ["#3B6EA5", "#5B8266", "#C77D2E", "#B5B5B5"],
    "warm": ["#B8722C", "#8A7A5E", "#4E7D62", "#C49A6C"],
    "mono": ["#3D3D3D", "#6E6E6E", "#2A2A2A", "#B5B5B5"],
    "topconf": ["#0072B2", "#E69F00", "#009E73"],  # Okabe-Ito
    "airy": ["#BBDEFB", "#FFD0D0", "#C8E6C9"],  # pastel
}

_PALETTE_ROLE_ORDER = ("primary", "secondary", "accent", "highlight", "plain", "dark")

_STYLE_BY_PRESET: dict[str, str] = {
    "sci": (
        "clean flat vector-style scientific illustration, icon-level abstraction with "
        "moderate detail, uniform ~2px dark outline (#2A2A2A) on every edge, simple "
        "geometric shapes, subtle flat shading (exactly two tones per color, no "
        "gradients), required three-quarter view, consistent corner radius as if drawn "
        "by one illustrator for a single academic figure"
    ),
    "warm": (
        "clean flat vector-style scientific illustration with warm earthy character, "
        "icon-level abstraction with moderate detail, uniform ~2px dark warm-brown "
        "outline (#3A2E22) on every edge, simple geometric shapes, subtle flat shading "
        "(exactly two tones per color, no gradients), required three-quarter view, "
        "consistent corner radius as if drawn by one illustrator"
    ),
    "mono": (
        "clean flat vector-style scientific illustration in restrained greyscale, "
        "icon-level abstraction with moderate detail, uniform ~2px dark outline "
        "(#1A1A1A) on every edge, simple geometric shapes, subtle flat shading "
        "(exactly two grey tones per surface, no gradients), required three-quarter "
        "view, consistent corner radius as if drawn by one illustrator"
    ),
    "topconf": (
        "clean flat vector-style scientific illustration for top-conference papers, "
        "icon-level abstraction with moderate detail, uniform ~2px dark outline "
        "(#333333) on every edge, simple geometric shapes, subtle flat shading "
        "(exactly two tones per color, no gradients), required three-quarter view, "
        "colorblind-friendly accents, consistent corner radius as if drawn by one "
        "illustrator"
    ),
    "airy": (
        "soft pastel flat vector-style scientific illustration, friendly rounded "
        "shapes, icon-level abstraction with moderate detail, uniform ~2px soft dark "
        "outline (#546E7A) on every edge, subtle flat shading (exactly two soft tones "
        "per color, no gradients), required three-quarter view, airy modern ML-paper "
        "look, consistent corner radius as if drawn by one illustrator"
    ),
}

_HARD_CONSTRAINTS = """\
HARD CONSTRAINTS:
- Pure white background #FFFFFF only. No other background color blocks, gradients, \
textures, or colored floor planes (downstream pixel cutout requires pure white).
- 背景必须纯白 #FFFFFF，不得有任何其他底色色块（下游按像素抠图）。
- No text, letters, numbers, captions, watermarks, logos, or UI chrome anywhere.
- 画面中禁止任何文字、字母、数字、水印或标识。
- Single centered object; keep generous margin from all edges; do not crop or clip the object.
- 单一物件居中，物件不得贴边裁断。
- No drop shadow, or at most an extremely faint ground contact shadow (no cast shadow, no glow).
- 无投影或仅极浅贴地阴影。
- Do not introduce colors outside the restricted palette above (plus neutral greys / white).
- Abstraction level locked: icon-level abstraction, moderate detail only; \
no photorealistic rendering; no PCB-level or blueprint-level micro detail.
- 抽象层级锁定：图标级适度细节，禁止写实渲染与 PCB/蓝图级微观细节。
- Outline lock: uniform outline weight (~2px) across the whole object; \
do not mix thick and thin strokes on the same object.
- 描边锁定：整物件统一约 2px 线宽，禁止粗细混用。
- Viewing angle required: three-quarter view for every object \
(pure front or side elevation only if the object naturally suits a flat silhouette); \
match the same viewing-angle family as sibling assets in this figure.
- 视角必须统一为三分之四视角（仅当物件天然适合正视/侧视时例外），并与同图其他素材同一视角族。"""


@dataclass
class Candidate:
    index: int
    raw_path: str
    cut_path: str | None
    report: dict
    score: float
    verdict: str  # ok | warn | reject


@dataclass
class GachaResult:
    asset_id: str
    prompt: str
    candidates: list[Candidate] = field(default_factory=list)
    selected: int | None = None
    contact_sheet: str | None = None
    full_prompt: str | None = None  # 实际发给 API 的完整 prompt（含风格包）


# ---------------------------------------------------------------- 风格包（图级共享）

def _normalize_theme_cfg(theme: Any) -> dict:
    """theme: sci | {preset, palette, ...} → dict。"""
    if theme is None:
        return {"preset": "sci"}
    if isinstance(theme, str):
        return {"preset": theme.strip() or "sci"}
    if isinstance(theme, dict):
        out = dict(theme)
        out.setdefault("preset", "sci")
        return out
    return {"preset": "sci"}


def resolve_preset(theme_cfg: dict | str | None) -> str:
    """解析 preset 名；未知名回退 sci（含并行任务尚未接入的新 preset 名时仍可映射色板）。"""
    cfg = _normalize_theme_cfg(theme_cfg)
    preset = str(cfg.get("preset") or "sci").strip().lower()
    if preset in _ASSET_PALETTES:
        return preset
    return "sci"


def _norm_hex(val: str) -> str:
    hex_ = val.strip().upper()
    return hex_ if hex_.startswith("#") else f"#{hex_}"


def resolve_asset_palette(theme_cfg: dict | str | None) -> list[str]:
    """素材色板 hex 列表：以 preset 内置为底，theme.palette 的 role→hex 覆盖对应位。"""
    cfg = _normalize_theme_cfg(theme_cfg)
    preset = resolve_preset(cfg)
    base = list(_ASSET_PALETTES.get(preset, _ASSET_PALETTES["sci"]))

    palette = cfg.get("palette")
    if not isinstance(palette, dict) or not palette:
        return base

    # 按角色顺序重建：有覆盖用覆盖，否则保留 preset 对应位（若有）
    merged: list[str] = []
    seen: set[str] = set()
    for i, role in enumerate(_PALETTE_ROLE_ORDER):
        raw = palette.get(role)
        if isinstance(raw, str) and raw.strip():
            hex_ = _norm_hex(raw)
        elif i < len(base):
            hex_ = base[i]
        else:
            continue
        if hex_ not in seen:
            merged.append(hex_)
            seen.add(hex_)
    # 额外自定义 role 追加在后
    for role, val in palette.items():
        if role in _PALETTE_ROLE_ORDER:
            continue
        if isinstance(val, str) and val.strip():
            hex_ = _norm_hex(val)
            if hex_ not in seen:
                merged.append(hex_)
                seen.add(hex_)
    return merged or base


def _style_description(preset: str, assets_style: str | None) -> str:
    if assets_style and str(assets_style).strip():
        return str(assets_style).strip()
    return _STYLE_BY_PRESET.get(preset, _STYLE_BY_PRESET["sci"])


def build_style_pack(
    theme_cfg: dict | str | None = None,
    assets_style: str | None = None,
) -> str:
    """构造图级共享 STYLE SPECIFICATIONS 文本（英文为主）。

    同一张图的所有素材应复用同一段风格包，以形成「同一画师」观感。
    """
    cfg = _normalize_theme_cfg(theme_cfg)
    preset = resolve_preset(cfg)
    colors = resolve_asset_palette(cfg)
    palette_csv = ", ".join(colors)
    style = _style_description(preset, assets_style)

    return (
        "STYLE SPECIFICATIONS:\n"
        f"- Theme preset: {preset}\n"
        f"- Restricted color palette: {palette_csv}, plus neutral greys "
        "(#F5F5F5, #BDBDBD, #757575) and pure white #FFFFFF. "
        "Do not introduce neon, rainbow, or off-palette hues.\n"
        f"- Illustration language (shared across all assets in this figure): {style}\n"
        "- Abstraction level (locked): icon-level abstraction, moderate detail only; "
        "no photorealistic rendering; no PCB-level or blueprint-level micro detail.\n"
        "- Outline lock: uniform outline weight (~2px) across the whole object; "
        "do not mix thick and thin strokes.\n"
        "- Viewing angle (required): three-quarter view for every object "
        "(pure front/side only if the object naturally suits a flat silhouette); "
        "same viewing-angle family for all assets in this figure.\n"
        "- Same illustrator, same outline weight, same shading rules, same viewing angle "
        "family for every object in this figure."
    )


def build_full_prompt(
    object_prompt: str,
    theme_cfg: dict | str | None = None,
    assets_style: str | None = None,
    style_pack: str | None = None,
) -> str:
    """组装完整生图 prompt：物件描述 + 共享风格包 + 硬约束。"""
    subject = (object_prompt or "").rstrip("。，,.\n ")
    pack = style_pack if style_pack is not None else build_style_pack(theme_cfg, assets_style)
    return f"{subject}\n\n{pack}\n\n{_HARD_CONSTRAINTS}"


def load_style_context_from_yaml(path: str | Path) -> tuple[dict, str | None]:
    """从 YAML 原始文件读取 theme 与 assets_style（不经 spec.py，避免未知字段问题）。

    assets_style 读取顺序：
      1. 顶层 `assets_style`（推荐；load_spec 不校验顶层未知字段）
      2. `figure.assets_style`（备选）
    """
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        return {"preset": "sci"}, None
    theme_cfg = _normalize_theme_cfg(raw.get("theme"))
    assets_style = raw.get("assets_style")
    if not assets_style:
        fig = raw.get("figure") or {}
        if isinstance(fig, dict):
            assets_style = fig.get("assets_style")
    if assets_style is not None:
        assets_style = str(assets_style)
    return theme_cfg, assets_style


def discover_spec_yaml(assets_dir: Path) -> Path | None:
    """在 assets_dir 的父目录猜测 figure YAML（CLI 未传 spec_path 时的回退）。"""
    parent = Path(assets_dir).resolve().parent
    for name in ("figure.yaml", "figure.yml", "spec.yaml", "spec.yml"):
        cand = parent / name
        if cand.is_file():
            return cand
    yamls = sorted(parent.glob("*.yaml")) + sorted(parent.glob("*.yml"))
    return yamls[0] if yamls else None


def resolve_style_for_assets(
    assets_dir: Path,
    *,
    theme_cfg: dict | str | None = None,
    assets_style: str | None = None,
    style_pack: str | None = None,
    spec_path: str | Path | None = None,
) -> tuple[str, dict, str | None]:
    """解析最终风格包。返回 (style_pack_text, theme_cfg, assets_style)。"""
    if style_pack is not None:
        cfg = _normalize_theme_cfg(theme_cfg)
        return style_pack, cfg, assets_style

    cfg = theme_cfg
    style = assets_style
    if cfg is None or style is None:
        yaml_path = Path(spec_path) if spec_path else discover_spec_yaml(Path(assets_dir))
        if yaml_path and yaml_path.is_file():
            y_theme, y_style = load_style_context_from_yaml(yaml_path)
            if cfg is None:
                cfg = y_theme
            if style is None:
                style = y_style
    cfg = _normalize_theme_cfg(cfg)
    return build_style_pack(cfg, style), cfg, style


# ---------------------------------------------------------------- API

def _submit(prompt: str, api_key: str, aspect: str, model: str) -> str | None:
    resp = requests.post(
        f"{API_HOST}{DRAW_ENDPOINT}",
        json={"model": model, "prompt": prompt, "aspectRatio": aspect,
              "imageSize": "1K", "webHook": "-1", "shutProgress": True},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        print(f"[ERROR] 提交失败: {data}", file=sys.stderr)
        return None
    return data["data"]["id"]


def _poll(task_id: str, api_key: str, timeout: int = 300) -> str | None:
    start = time.time()
    interval = 2.0
    while time.time() - start < timeout:
        time.sleep(interval)
        resp = requests.post(
            f"{API_HOST}{RESULT_ENDPOINT}", json={"id": task_id},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == -22:
            return None
        result = data.get("data") or {}
        status = result.get("status", "")
        if status == "succeeded":
            results = result.get("results") or []
            return results[0].get("url") if results else None
        if status == "failed":
            print(f"[ERROR] 生成失败: {result.get('failure_reason')} {result.get('error', '')}",
                  file=sys.stderr)
            return None
        interval = min(interval * 1.3, 8.0)
    return None


def _download(url: str, path: Path) -> bool:
    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"[ERROR] 下载失败: {e}", file=sys.stderr)
        return False


def _generate_one(prompt: str, api_key: str, aspect: str, model: str, out_raw: Path) -> bool:
    """单个候选：提交→轮询→下载。任何网络异常都吞掉并返回 False，
    以免一张候选失败拖垮整批抽卡。"""
    try:
        task_id = _submit(prompt, api_key, aspect, model)
        if not task_id:
            return False
        url = _poll(task_id, api_key)
        if not url:
            return False
        return _download(url, out_raw)
    except Exception as e:  # noqa: BLE001 — 单卡容错，记录后继续其余候选
        print(f"[ERROR] 候选生成异常 ({out_raw.name}): {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------- 评分

def _score(report: CutoutReport) -> tuple[float, str]:
    """抠图报告 → (0~100, verdict)。只做客观项，审美判断留给视觉评审。"""
    if not report.ok:
        return 0.0, "reject"
    score = 100.0
    notes_warn = False

    # 前景占比：太小浪费分辨率，太大说明背景不干净
    if report.fg_ratio < 0.08:
        score -= 25
        notes_warn = True
    elif report.fg_ratio > 0.90:
        score -= 30
        notes_warn = True
    elif report.fg_ratio > 0.75:
        score -= 10

    # 连通块：理想 1~2 个（主体 + 也许一个配件）
    if report.n_components > 4:
        score -= 25
        notes_warn = True
    elif report.n_components > 2:
        score -= 8

    # 贴边 = 主体被画布裁断，合成到图里会露出平直切口
    if report.touches_border:
        score -= 30
        notes_warn = True

    if score < 45:
        return max(score, 0.0), "reject"
    return score, ("warn" if notes_warn else "ok")


# ---------------------------------------------------------------- contact sheet

def _contact_sheet(cands: list[Candidate], out_path: Path, cell: int = 360) -> None:
    ok_cands = [c for c in cands if c.cut_path]
    if not ok_cands:
        return
    n = len(ok_cands)
    pad, header = 12, 44
    sheet = Image.new("RGB", (n * cell + (n + 1) * pad, cell + header + 2 * pad), "#DDE3EA")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 22, index=2)
    except OSError:
        font = ImageFont.load_default()

    for i, c in enumerate(ok_cands):
        x0 = pad + i * (cell + pad)
        # 浅灰棋盘底衬出透明区域
        tile = Image.new("RGB", (cell, cell), "#FFFFFF")
        td = ImageDraw.Draw(tile)
        for ty in range(0, cell, 24):
            for tx in range(0, cell, 24):
                if (tx // 24 + ty // 24) % 2:
                    td.rectangle([tx, ty, tx + 24, ty + 24], fill="#F0F2F5")
        with Image.open(c.cut_path) as im:
            im = im.convert("RGBA")
            im.thumbnail((cell - 16, cell - 16), Image.LANCZOS)
            tile.paste(im, ((cell - im.width) // 2, (cell - im.height) // 2), im)
        sheet.paste(tile, (x0, pad + header))
        color = {"ok": "#1F7A3D", "warn": "#B26B00", "reject": "#B3261E"}[c.verdict]
        draw.text((x0 + 6, pad + 8), f"#{c.index}  {c.score:.0f}分 {c.verdict}",
                  fill=color, font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)


# ---------------------------------------------------------------- 主流程

def gacha_generate(
    req: AssetRequest,
    assets_dir: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    threshold: int = 238,
    force: bool = False,
    *,
    theme_cfg: dict | str | None = None,
    assets_style: str | None = None,
    style_pack: str | None = None,
    spec_path: str | Path | None = None,
) -> GachaResult:
    """为单个素材请求抽 req.candidates 张候选并评分。已有正式素材且非 force 时跳过。

    风格包参数（可选，关键字专用，保持旧调用兼容）：
      theme_cfg / assets_style / style_pack — 显式注入；
      spec_path — 从 YAML 读取 theme 与顶层 assets_style；
      若皆未给，则在 assets_dir 父目录自动发现 figure.yaml。
    同一 figure 下请复用同一 style_pack，保证跨素材风格一致。
    """
    result = GachaResult(asset_id=req.id, prompt=req.prompt)
    final_path = assets_dir / f"{req.id}.png"
    if final_path.exists() and not force:
        print(f"[跳过] {req.id} 已存在")
        return result

    cand_dir = assets_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    pack, _, _ = resolve_style_for_assets(
        Path(assets_dir),
        theme_cfg=theme_cfg,
        assets_style=assets_style,
        style_pack=style_pack,
        spec_path=spec_path,
    )
    full_prompt = build_full_prompt(req.prompt, style_pack=pack)
    result.full_prompt = full_prompt

    print(f"[抽卡] {req.id} ×{req.candidates}: {req.prompt[:40]}...")
    raw_paths = [cand_dir / f"{req.id}_{i + 1}.raw.png" for i in range(req.candidates)]
    if force:
        # 重抽：清掉旧的原始白底图，强制重新向 API 请求
        for rp in raw_paths:
            rp.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=min(req.candidates, 4)) as pool:
        futures = [
            pool.submit(_generate_one, full_prompt, api_key, req.aspect, model, rp)
            if not rp.exists() else None
            for rp in raw_paths
        ]
        oks = [f.result() if f else True for f in futures]

    for i, (raw, ok) in enumerate(zip(raw_paths, oks)):
        idx = i + 1
        if not ok or not raw.exists():
            result.candidates.append(Candidate(
                index=idx, raw_path=str(raw), cut_path=None,
                report={"ok": False, "reason": "生成/下载失败"}, score=0.0, verdict="reject"))
            continue
        cut = cand_dir / f"{req.id}_{idx}.png"
        rep = cutout_white_bg(raw, cut, threshold=threshold, shadow=req.shadow)
        score, verdict = _score(rep)
        result.candidates.append(Candidate(
            index=idx, raw_path=str(raw), cut_path=str(cut) if rep.ok else None,
            report=asdict(rep), score=score, verdict=verdict))
        print(f"  #{idx}: {'✓' if rep.ok else '✗'} {score:.0f}分 {verdict}"
              f"  fg={rep.fg_ratio:.0%} comp={rep.n_components}"
              f"{' 贴边!' if rep.touches_border else ''}{' ' + rep.reason if rep.reason else ''}")

    sheet = assets_dir / f"contact_sheet_{req.id}.png"
    _contact_sheet(result.candidates, sheet)
    if sheet.exists():
        result.contact_sheet = str(sheet)
        print(f"  对比图: {sheet}")
    return result


def select_candidate(assets_dir: Path, asset_id: str, index: int) -> Path:
    """把候选 #index 提升为正式素材。"""
    src = assets_dir / "candidates" / f"{asset_id}_{index}.png"
    if not src.exists():
        raise FileNotFoundError(f"候选不存在: {src}")
    dst = assets_dir / f"{asset_id}.png"
    shutil.copyfile(src, dst)
    print(f"[选卡] {asset_id} ← 候选#{index}")
    return dst


def auto_select(result: GachaResult, assets_dir: Path, min_score: float = 60.0) -> bool:
    """自动选客观分最高的候选（分数并列取 index 小的）。低于 min_score 不选。"""
    viable = [c for c in result.candidates if c.cut_path and c.score >= min_score]
    if not viable:
        return False
    best = max(viable, key=lambda c: (c.score, -c.index))
    select_candidate(assets_dir, result.asset_id, best.index)
    result.selected = best.index
    return True


def save_report(results: list[GachaResult], assets_dir: Path) -> Path:
    path = assets_dir / "gacha_report.json"
    payload = [
        {
            "asset_id": r.asset_id, "prompt": r.prompt,
            "full_prompt": r.full_prompt, "selected": r.selected,
            "contact_sheet": r.contact_sheet,
            "candidates": [asdict(c) for c in r.candidates],
        }
        for r in results
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
