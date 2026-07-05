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

import requests
from PIL import Image, ImageDraw, ImageFont

from .cutout import CutoutReport, cutout_white_bg
from .spec import AssetRequest

API_HOST = "https://grsai.dakka.com.cn"
DRAW_ENDPOINT = "/v1/draw/nano-banana"
RESULT_ENDPOINT = "/v1/draw/result"
DEFAULT_MODEL = "nano-banana-fast"

PROMPT_SUFFIX = "，扁平插画风格，简洁的科研图示素材，背景纯白色，我会根据像素值做裁剪背景一定要纯白不能有其他色块，画面中不要出现任何文字字母或数字"


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
) -> GachaResult:
    """为单个素材请求抽 req.candidates 张候选并评分。已有正式素材且非 force 时跳过。"""
    result = GachaResult(asset_id=req.id, prompt=req.prompt)
    final_path = assets_dir / f"{req.id}.png"
    if final_path.exists() and not force:
        print(f"[跳过] {req.id} 已存在")
        return result

    cand_dir = assets_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    full_prompt = req.prompt.rstrip("。，,.") + PROMPT_SUFFIX

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
            "asset_id": r.asset_id, "prompt": r.prompt, "selected": r.selected,
            "contact_sheet": r.contact_sheet,
            "candidates": [asdict(c) for c in r.candidates],
        }
        for r in results
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
