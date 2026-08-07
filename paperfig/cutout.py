"""白底素材抠图（升级版）。

相比朴素的"全局白色阈值"抠法，这里解决三个实际问题：
1. 主体内部的白色（高光、眼白、留白图案）不能被抠穿
   → 只移除与图像边缘连通的近白区域（BFS 洪泛填充）；
2. 边缘 1–2px 的白色光晕（halo）在深色版面上很扎眼
   → 对 alpha 边缘做腐蚀 + 高斯羽化，并做颜色去污染（unpremultiply）；
3. AI 时常给主体加淡淡的投影，有时要保留有时要去掉
   → shadow="remove" 时用更低的阈值把近白偏灰的软阴影也视作背景。

依赖仅 numpy + PIL + scipy（腐蚀/羽化用 scipy.ndimage）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


@dataclass
class CutoutReport:
    ok: bool
    reason: str = ""
    fg_ratio: float = 0.0        # 前景占原图面积比
    n_components: int = 0        # 前景连通块数量（>3 常意味着碎片/杂物）
    touches_border: bool = False # 前景是否贴着原图边缘（主体被裁断的信号）
    out_size: tuple[int, int] = (0, 0)


def cutout_white_bg(
    input_path: str | Path,
    output_path: str | Path,
    threshold: int = 238,
    shadow: str = "keep",
    padding: int = 8,
    feather: float = 1.2,
    min_fg_ratio: float = 0.02,
    max_fg_ratio: float = 0.985,
) -> CutoutReport:
    """白底图 → 透明底 PNG，返回质量报告。

    shadow="keep"  仅移除与边缘连通的近白像素；
    shadow="remove" 额外把低饱和度的浅灰（软阴影）也并入背景。
    """
    img = Image.open(input_path).convert("RGBA")
    px = np.asarray(img).astype(np.float32)
    h, w = px.shape[:2]
    rgb = px[:, :, :3]

    near_white = (rgb >= threshold).all(axis=2)
    if shadow == "remove":
        mx = rgb.max(axis=2)
        mn = rgb.min(axis=2)
        soft_shadow = (mn >= 190) & ((mx - mn) <= 18)
        candidate_bg = near_white | soft_shadow
    else:
        candidate_bg = near_white

    # 只把与边缘连通的候选背景当作真背景（保住主体内部白色）
    labels, _n = ndimage.label(candidate_bg)
    border_labels = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    border_labels = border_labels[border_labels != 0]
    bg = np.isin(labels, border_labels)

    fg = ~bg
    fg_ratio = float(fg.mean())
    if fg_ratio < min_fg_ratio:
        return CutoutReport(False, f"前景过小 ({fg_ratio:.1%})，可能整图近白", fg_ratio)
    if fg_ratio > max_fg_ratio:
        return CutoutReport(False, f"前景占比 {fg_ratio:.1%}，背景不是纯白（无法抠图）", fg_ratio)

    # 前景连通块统计（忽略 <0.05% 面积的碎屑）
    fg_labels, n_comp = ndimage.label(fg)
    sizes = ndimage.sum_labels(np.ones_like(fg_labels), fg_labels, range(1, n_comp + 1))
    significant = int((sizes > 0.0005 * h * w).sum())

    # 去 halo：轻微腐蚀硬 mask 后做距离羽化
    hard = fg.astype(np.float32)
    eroded = ndimage.binary_erosion(fg, iterations=1)
    alpha = ndimage.gaussian_filter(eroded.astype(np.float32), sigma=feather)
    alpha = np.clip((alpha - 0.25) / 0.5, 0.0, 1.0)  # 收紧过渡带
    alpha = np.maximum(alpha, 0.0) * hard            # 背景必为 0
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
        return CutoutReport(False, "腐蚀/羽化后无有效前景（主体过细）", fg_ratio)
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    touches = bool(y0 <= 1 or x0 <= 1 or y1 >= h - 2 or x1 >= w - 2)

    y0p, y1p = max(0, y0 - padding), min(h, y1 + 1 + padding)
    x0p, x1p = max(0, x0 - padding), min(w, x1 + 1 + padding)
    cropped = out[y0p:y1p, x0p:x1p]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(cropped, "RGBA").save(output_path, "PNG")

    return CutoutReport(
        ok=True, fg_ratio=fg_ratio, n_components=significant,
        touches_border=touches, out_size=(cropped.shape[1], cropped.shape[0]),
    )


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="白底素材抠图（洪泛填充版）")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--threshold", type=int, default=238)
    p.add_argument("--shadow", choices=["keep", "remove"], default="keep")
    p.add_argument("--padding", type=int, default=8)
    args = p.parse_args()
    rep = cutout_white_bg(args.input, args.output, threshold=args.threshold,
                          shadow=args.shadow, padding=args.padding)
    print(rep)
    raise SystemExit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
