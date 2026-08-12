"""白底素材抠图（防护加固版）。

在"边缘连通洪泛 + 去光晕 + 去污染"的基础上，针对真实抽卡数据中确认的
四类"抠坏"模式增加检测与自动补救：

1. 软阴影残边 —— AI 烤进图里的贴地阴影亮度常落在 190~237，躲过纯白阈值，
   在彩色/深色版面上合成为撕裂状白灰残边。
   → 检测：alpha 边界中「平坦梯度 × 高亮度」像素占比（fringe_ratio）；
   → 补救：shadow="auto"（默认）时自动并入低饱和浅灰重抠一次。
2. 洪泛泄漏 —— 白色物件（纸张/屏幕）描边有 1~2px 缺口时，洪泛从缺口
   灌入内部，把主体抠穿。
   → 检测：对比「收紧洪泛」（先腐蚀候选背景再洪泛）与普通洪泛的差集，
     只能经窄通道到达的大面积口袋 = 泄漏区（leak_ratio）；
   → 补救：面积超过阈值的口袋自动回填为前景（fixes 记 leak-sealed）。
3. 底色不纯 —— 偏白但非纯白的背景（暖白/淡渐变）导致背景残留。
   → 检测：最外圈 2% 边框带亮度 p5（bg_p5）；
   → 补救：245>p5≥225 时自适应下调阈值重抠；p5<225 直接拒绝。
4. 薄线损失 —— 腐蚀 + 羽化会吃掉 ~1px 细线（正是素材风格要求的 hairline）。
   → 检测：alpha>0.5 面积相对硬 mask 的损失率（thin_loss）；
   → 补救：损失 >5% 时改用无腐蚀窄羽化（fixes 记 thin-preserved）。

另有连通块分类：低饱和浅灰、边界平坦的小块视为阴影碎屑并自动丢弃
（debris_dropped），n_solid 只统计实体块，避免把阴影碎片误判为多物件。

依赖仅 numpy + PIL + scipy（腐蚀/羽化/连通域用 scipy.ndimage）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# 判定参数（在 18 张真实抽卡素材 + 合成样例上标定）
_FLAT_GRAD = 6.0          # 亮度梯度低于此视为平坦（无描边）
_FRINGE_LUMA = 200.0      # 边界像素亮于此才算"白灰残边"候选
_FRINGE_TRIGGER = 0.06    # 边界中残边占比超过此 → 自动去阴影重抠
_LEAK_GUARD_PX = 2        # 洪泛收紧腐蚀半径（能封住 ≤2*2px 的描边缺口）
_LEAK_MIN_POCKET = 0.002  # 口袋面积占全图比例超过此才算泄漏（过滤描边旁白缝）
_THIN_LOSS_TRIGGER = 0.05 # 薄线损失超过 5% → 无腐蚀羽化回退
_BG_CLEAN_P5 = 245.0      # 边框带 p5 亮度 ≥ 此为干净白底
_BG_REJECT_P5 = 225.0     # 低于此判定背景非白，拒绝


@dataclass
class CutoutReport:
    ok: bool
    reason: str = ""
    fg_ratio: float = 0.0        # 前景占原图面积比
    n_components: int = 0        # 显著前景连通块数（含阴影碎屑，向后兼容）
    touches_border: bool = False # 前景是否贴着原图边缘（主体被裁断的信号）
    out_size: tuple[int, int] = (0, 0)
    # ── 防护加固新增 ──
    bg_p5: float = 255.0         # 边框带亮度 p5（<245 说明底色不纯）
    leak_ratio: float = 0.0      # 洪泛泄漏口袋面积占比（已回填）
    fringe_ratio: float = 0.0    # 补救后仍残留的白灰软边占 alpha 边界比例
    thin_loss: float = 0.0       # 最终 alpha 相对硬 mask 的面积损失率
    n_solid: int = 0             # 实体连通块数（不含阴影碎屑）
    debris_dropped: int = 0      # 被自动丢弃的阴影碎屑块数
    fixes: list[str] = field(default_factory=list)  # 自动补救记录


def _edge_connected(mask: np.ndarray) -> np.ndarray:
    """mask 中与图像边缘连通的部分。"""
    labels, _ = ndimage.label(mask)
    border = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    border = border[border != 0]
    return np.isin(labels, border)


def _flood_bg_with_leak_guard(
    candidate_bg: np.ndarray, fixes: list[str]
) -> tuple[np.ndarray, float]:
    """洪泛出背景，并把只能经窄通道到达的大口袋（泄漏区）回填为前景。

    返回 (bg_mask, leak_ratio)。
    """
    bg_loose = _edge_connected(candidate_bg)

    # 收紧版洪泛：先腐蚀候选背景（窄通道被掐断），洪泛后再膨胀回原候选内
    # border_value=1：图像外视作背景，避免腐蚀把 mask 从画布边缘剥离
    tight = ndimage.binary_erosion(
        candidate_bg, iterations=_LEAK_GUARD_PX, border_value=1)
    bg_tight = _edge_connected(tight)
    bg_tight = ndimage.binary_dilation(bg_tight, iterations=_LEAK_GUARD_PX) & candidate_bg

    pockets = bg_loose & ~bg_tight
    if not pockets.any():
        return bg_loose, 0.0

    # 只把大面积口袋当泄漏回填；描边旁的细小白缝仍归背景
    labels, n = ndimage.label(pockets)
    if n == 0:
        return bg_loose, 0.0
    sizes = ndimage.sum_labels(np.ones_like(labels), labels, range(1, n + 1))
    min_area = _LEAK_MIN_POCKET * candidate_bg.size
    big = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= min_area])
    if not big.any():
        return bg_loose, 0.0

    leak_ratio = float(big.sum() / candidate_bg.size)
    fixes.append(f"leak-sealed({leak_ratio:.1%})")
    return bg_loose & ~big, leak_ratio


def _fringe_ratio(fg: np.ndarray, luma: np.ndarray, grad: np.ndarray) -> float:
    """alpha 边界中「平坦 × 高亮」（软阴影/白灰残边特征）像素占比。

    有描边的主体边界落在深色高梯度描边上，不会命中；
    软阴影边界既平坦又亮，命中率高。
    """
    edge = fg ^ ndimage.binary_erosion(fg)
    n_edge = int(edge.sum())
    if n_edge == 0:
        return 0.0
    hits = edge & (grad < _FLAT_GRAD) & (luma > _FRINGE_LUMA)
    return float(hits.sum() / n_edge)


def _classify_components(
    fg: np.ndarray, luma: np.ndarray, sat: np.ndarray, grad: np.ndarray
) -> tuple[np.ndarray, int, int, int]:
    """连通块分类：丢弃阴影碎屑。返回 (清理后 fg, n_significant, n_solid, n_dropped)。

    碎屑判据：面积 <3% 且平均亮度 >200 且平均饱和度 <25 且边界过半平坦
    ——即"浅灰、无描边的小块"，与阴影残片特征一致。
    """
    labels, n = ndimage.label(fg)
    if n == 0:
        return fg, 0, 0, 0
    sizes = ndimage.sum_labels(np.ones_like(labels), labels, range(1, n + 1))
    min_sig = 0.0005 * fg.size
    keep = fg.copy()
    n_sig = n_solid = n_drop = 0
    for i, size in enumerate(sizes, start=1):
        if size <= min_sig:
            continue  # 微小碎屑不计数（后续腐蚀羽化自然处理）
        n_sig += 1
        comp = labels == i
        if size < 0.03 * fg.size:
            mean_luma = float(luma[comp].mean())
            mean_sat = float(sat[comp].mean())
            edge = comp ^ ndimage.binary_erosion(comp)
            flat = float((grad[edge] < _FLAT_GRAD).mean()) if edge.any() else 0.0
            if mean_luma > 200 and mean_sat < 25 and flat > 0.5:
                keep &= ~comp
                n_drop += 1
                continue
        n_solid += 1
    return keep, n_sig, n_solid, n_drop


def cutout_white_bg(
    input_path: str | Path,
    output_path: str | Path,
    threshold: int = 238,
    shadow: str = "auto",
    padding: int = 8,
    feather: float = 1.2,
    min_fg_ratio: float = 0.02,
    max_fg_ratio: float = 0.985,
) -> CutoutReport:
    """白底图 → 透明底 PNG，返回质量报告。

    shadow="auto"   先按 keep 抠，检测到白灰残边超标时自动去阴影重抠（默认）；
    shadow="keep"   仅移除与边缘连通的近白像素；
    shadow="remove" 直接把低饱和度的浅灰（软阴影）也并入背景。
    """
    fixes: list[str] = []
    img = Image.open(input_path).convert("RGBA")
    px = np.asarray(img).astype(np.float32)
    h, w = px.shape[:2]
    rgb = px[:, :, :3]
    luma = rgb @ _LUMA
    gy, gx = np.gradient(luma)
    grad = np.hypot(gx, gy)
    sat = rgb.max(axis=2) - rgb.min(axis=2)

    # ── 防护 3：底色纯度检查与自适应阈值 ──
    band = max(2, int(0.02 * min(h, w)))
    border_luma = np.concatenate([
        luma[:band].ravel(), luma[-band:].ravel(),
        luma[:, :band].ravel(), luma[:, -band:].ravel()])
    bg_p5 = float(np.percentile(border_luma, 5))
    if bg_p5 < _BG_REJECT_P5:
        return CutoutReport(
            False, f"背景不是纯白（边框带亮度 p5={bg_p5:.0f} < {_BG_REJECT_P5:.0f}）",
            bg_p5=bg_p5)
    eff_threshold = float(threshold)
    if bg_p5 < _BG_CLEAN_P5:
        eff_threshold = min(eff_threshold, bg_p5 - 8.0)
        fixes.append(f"adaptive-threshold({eff_threshold:.0f})")

    def build_fg(remove_shadow: bool) -> tuple[np.ndarray, float]:
        near_white = (rgb >= eff_threshold).all(axis=2)
        if remove_shadow:
            mn = rgb.min(axis=2)
            soft = (mn >= 185) & (sat <= 22) & (luma >= 195)
            candidate = near_white | soft
        else:
            candidate = near_white
        bg, leak = _flood_bg_with_leak_guard(candidate, fixes)
        return ~bg, leak

    # ── 防护 1：软阴影残边检测 + auto 重抠；防护 2：泄漏回填在 build_fg 内 ──
    fg, leak_ratio = build_fg(remove_shadow=(shadow == "remove"))
    fringe = _fringe_ratio(fg, luma, grad)
    if shadow == "auto" and fringe > _FRINGE_TRIGGER:
        fixes.append(f"shadow-removed(fringe={fringe:.0%})")
        fg, leak_ratio = build_fg(remove_shadow=True)
        fringe = _fringe_ratio(fg, luma, grad)

    fg_ratio = float(fg.mean())
    if fg_ratio < min_fg_ratio:
        return CutoutReport(False, f"前景过小 ({fg_ratio:.1%})，可能整图近白",
                            fg_ratio=fg_ratio, bg_p5=bg_p5, fixes=fixes)
    if fg_ratio > max_fg_ratio:
        return CutoutReport(False, f"前景占比 {fg_ratio:.1%}，背景不是纯白（无法抠图）",
                            fg_ratio=fg_ratio, bg_p5=bg_p5, fixes=fixes)

    # ── 连通块分类：丢阴影碎屑，实体块单独计数 ──
    fg, n_sig, n_solid, n_drop = _classify_components(fg, luma, sat, grad)
    if n_drop:
        fixes.append(f"debris-dropped(×{n_drop})")

    # ── 去 halo：腐蚀 + 距离羽化；防护 4：薄线保护回退 ──
    hard = fg.astype(np.float32)
    hard_area = float(fg.sum())

    def alpha_pass(erode: bool) -> np.ndarray:
        if erode:
            base = ndimage.binary_erosion(fg, iterations=1).astype(np.float32)
            a = ndimage.gaussian_filter(base, sigma=feather)
            a = np.clip((a - 0.25) / 0.5, 0.0, 1.0)
        else:
            a = ndimage.gaussian_filter(hard, sigma=min(feather, 0.7))
            a = np.clip((a - 0.30) / 0.40, 0.0, 1.0)
        return a * hard  # 背景必为 0

    alpha = alpha_pass(erode=True)
    thin_loss = 1.0 - float((alpha > 0.5).sum()) / max(hard_area, 1.0)
    if thin_loss > _THIN_LOSS_TRIGGER:
        fixes.append(f"thin-preserved(loss={thin_loss:.0%})")
        alpha = alpha_pass(erode=False)
        thin_loss = 1.0 - float((alpha > 0.5).sum()) / max(hard_area, 1.0)
    alpha_u8 = (alpha * 255).astype(np.uint8)

    # 颜色去污染：把混入白底的边缘像素往主体色拉回
    # I_obs = a*I_fg + (1-a)*255  →  I_fg = (I_obs - (1-a)*255)/a
    a = np.clip(alpha, 1e-3, 1.0)[..., None]
    defringed = np.clip((rgb - (1.0 - a) * 255.0) / a, 0, 255)
    mix = (alpha[..., None] < 0.999) & (alpha[..., None] > 0)
    rgb_out = np.where(mix, defringed, rgb).astype(np.uint8)

    out = np.dstack([rgb_out, alpha_u8])

    ys, xs = np.where(alpha_u8 > 8)
    if ys.size == 0:
        return CutoutReport(False, "腐蚀/羽化后无有效前景（主体过细）",
                            fg_ratio=fg_ratio, bg_p5=bg_p5, fixes=fixes)
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    touches = bool(y0 <= 1 or x0 <= 1 or y1 >= h - 2 or x1 >= w - 2)

    y0p, y1p = max(0, y0 - padding), min(h, y1 + 1 + padding)
    x0p, x1p = max(0, x0 - padding), min(w, x1 + 1 + padding)
    cropped = out[y0p:y1p, x0p:x1p]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(cropped, "RGBA").save(output_path, "PNG")

    return CutoutReport(
        ok=True, fg_ratio=fg_ratio, n_components=n_sig,
        touches_border=touches, out_size=(cropped.shape[1], cropped.shape[0]),
        bg_p5=bg_p5, leak_ratio=leak_ratio, fringe_ratio=fringe,
        thin_loss=thin_loss, n_solid=n_solid, debris_dropped=n_drop, fixes=fixes,
    )


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="白底素材抠图（防护加固版）")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--threshold", type=int, default=238)
    p.add_argument("--shadow", choices=["auto", "keep", "remove"], default="auto")
    p.add_argument("--padding", type=int, default=8)
    args = p.parse_args()
    rep = cutout_white_bg(args.input, args.output, threshold=args.threshold,
                          shadow=args.shadow, padding=args.padding)
    print(rep)
    raise SystemExit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
