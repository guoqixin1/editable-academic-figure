"""成图切片放大：供循环目检复核。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .render import render
from .spec import FigureSpec


def default_tile_grid(width_mm: float) -> tuple[int, int]:
    """画布宽 ≤180mm → 2×2，更宽 → 3×3。"""
    return (2, 2) if width_mm <= 180.0 else (3, 3)


def parse_grid(s: str) -> tuple[int, int]:
    """解析 ``2x2`` / ``3x3`` 形式。"""
    text = s.strip().lower().replace("×", "x")
    if "x" not in text:
        raise ValueError(f"grid 须为 RxC 形式（如 2x2），得到 {s!r}")
    a, b = text.split("x", 1)
    rows, cols = int(a), int(b)
    if rows < 1 or cols < 1 or rows > 8 or cols > 8:
        raise ValueError(f"grid 行列须在 1–8：{s!r}")
    return rows, cols


def export_tiles(
    spec: FigureSpec,
    out_dir: str | Path,
    *,
    grid: tuple[int, int] | str | None = None,
    dpi: int = 300,
    min_tile_width: int = 1200,
) -> dict[str, Path]:
    """渲染后按网格切片放大导出。

    产出：
      - ``overview.png``：整图
      - ``tile_r{i}c{j}.png``：每片（单片宽 ≥ min_tile_width）
    返回文件名 → 路径映射。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if grid is None:
        rows, cols = default_tile_grid(spec.width)
    elif isinstance(grid, str):
        rows, cols = parse_grid(grid)
    else:
        rows, cols = grid

    overview = out / "overview.png"
    render(spec, out_png=overview, dpi=dpi)

    with Image.open(overview) as im:
        full = im.convert("RGB")
        w, h = full.size

    paths: dict[str, Path] = {"overview.png": overview}
    tw = w / cols
    th = h / rows
    for r in range(rows):
        for c in range(cols):
            x0 = int(round(c * tw))
            y0 = int(round(r * th))
            x1 = int(round((c + 1) * tw)) if c < cols - 1 else w
            y1 = int(round((r + 1) * th)) if r < rows - 1 else h
            tile = full.crop((x0, y0, x1, y1))
            if tile.width < min_tile_width and tile.width > 0:
                scale = min_tile_width / tile.width
                nh = max(1, int(round(tile.height * scale)))
                tile = tile.resize((min_tile_width, nh), Image.LANCZOS)
            name = f"tile_r{r}c{c}.png"
            dest = out / name
            tile.save(dest, format="PNG")
            paths[name] = dest
    return paths
