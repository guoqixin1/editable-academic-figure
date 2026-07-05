"""字体度量与混排排版。

cairosvg 使用 cairo toy text API，没有按字形的字体 fallback：
用西文字体渲染中文会直接丢字。因此所有文本先在这里切分为
latin / cjk / symbol 三类连续 run，每个 run 用对应字体单独发排，
x 坐标由 PIL 度量结果显式给出。

单位约定：外部接口一律使用 mm（物理尺寸），字号参数使用 pt。
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from PIL import ImageFont

PT_TO_MM = 25.4 / 72.0

# ── 轻量数学记号 ───────────────────────────────────────────
# 语法：`_{...}` 下标、`^{...}` 上标（不支持嵌套）。
# 例：L_{InfoNCE}、Z_{s}、ℝ^{(B V) H W C}、f_{0}~f_{3}
SCRIPT_SCALE = 0.68     # 下/上标字号比例
SUB_SHIFT = 0.14        # 下标基线下移（×字号 mm）
SUP_SHIFT = -0.36       # 上标基线上移（×字号 mm）

_MARKUP_RE = re.compile(r"([_^])\{([^{}]*)\}")


def parse_markup(text: str) -> list[tuple[str, str]]:
    """文本 → [(片段, 模式)]，模式 ∈ 'n'（正常）/'sub'/'sup'。"""
    segs: list[tuple[str, str]] = []
    pos = 0
    for m in _MARKUP_RE.finditer(text):
        if m.start() > pos:
            segs.append((text[pos:m.start()], "n"))
        segs.append((m.group(2), "sub" if m.group(1) == "_" else "sup"))
        pos = m.end()
    if pos < len(text):
        segs.append((text[pos:], "n"))
    return segs or [("", "n")]


def strip_markup(text: str) -> str:
    return "".join(seg for seg, _ in parse_markup(text))

# 度量时的放大倍数（PIL 字号只支持整数像素，放大后量化误差更小）
_MEASURE_SCALE = 8

# 字体文件路径（Liberation Sans 与 Arial 度量兼容；Noto CJK 提供中文；DejaVu 提供符号）
_FONT_FILES = {
    ("latin", False): ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", 0),
    ("latin", True): ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", 0),
    ("cjk", False): ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("cjk", True): ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("symbol", False): ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("symbol", True): ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
}

# SVG 中使用的 font-family（fontconfig 名称）
FAMILY_SVG = {
    "latin": "Liberation Sans",
    "cjk": "Noto Sans CJK SC",
    "symbol": "DejaVu Sans",
}

# cairo 与 PIL 排版存在细微差异，换行宽度预留 2% 安全余量
_SAFETY = 1.02


def _char_class(ch: str) -> str:
    cp = ord(ch)
    if (
        0x3000 <= cp <= 0x303F  # CJK 标点
        or 0x3040 <= cp <= 0x30FF  # 假名
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFF00 <= cp <= 0xFFEF  # 全角
    ):
        return "cjk"
    if (
        0x2100 <= cp <= 0x214F  # 字母式符号（ℝ ℕ ℒ ℤ …）
        or 0x2190 <= cp <= 0x2BFF  # 箭头、数学运算符、几何图形等
        or 0x1D400 <= cp <= 0x1D7FF  # 数学字母数字符号
    ):
        return "symbol"
    return "latin"


@functools.lru_cache(maxsize=64)
def _load_font(cls: str, bold: bool, size_px: int) -> ImageFont.FreeTypeFont:
    path, index = _FONT_FILES[(cls, bold)]
    return ImageFont.truetype(path, size_px, index=index)


@functools.lru_cache(maxsize=8)
def _ascent_ratio(cls: str, bold: bool) -> float:
    size = 100 * _MEASURE_SCALE
    f = _load_font(cls, bold, size)
    ascent, _descent = f.getmetrics()
    return ascent / size


def split_runs(text: str) -> list[tuple[str, str]]:
    """把一行文本切分为 (run_text, class) 列表，class ∈ latin/cjk/symbol。"""
    runs: list[tuple[str, str]] = []
    cur, cur_cls = "", None
    for ch in text:
        cls = _char_class(ch)
        # 空格归入前一 run，避免 run 数量爆炸
        if ch == " " and cur:
            cur += ch
            continue
        if cls == cur_cls:
            cur += ch
        else:
            if cur:
                runs.append((cur, cur_cls))
            cur, cur_cls = ch, cls
    if cur:
        runs.append((cur, cur_cls))
    return runs


@functools.lru_cache(maxsize=4096)
def _run_width_mm(text: str, cls: str, pt: float, bold: bool) -> float:
    size_px = max(4, round(pt * _MEASURE_SCALE))
    f = _load_font(cls, bold, size_px)
    w_px = f.getlength(text)
    # 度量像素 → pt → mm
    return (w_px / _MEASURE_SCALE) * PT_TO_MM * (pt * _MEASURE_SCALE / size_px)


def measure_mm(text: str, pt: float, bold: bool = False) -> float:
    """一行纯文本（无记号）的宽度（mm）。"""
    return sum(_run_width_mm(t, c, pt, bold) for t, c in split_runs(text))


def measure_markup_mm(text: str, pt: float, bold: bool = False) -> float:
    """一行带 _{}/^{} 记号文本的宽度（mm）。"""
    w = 0.0
    for seg, mode in parse_markup(text):
        w += measure_mm(seg, pt * (SCRIPT_SCALE if mode != "n" else 1.0), bold)
    return w


def line_ascent_mm(text: str, pt: float, bold: bool = False) -> float:
    """行内最大 ascent（mm），用于基线定位。记号语法字符不参与度量。"""
    classes = {c for _, c in split_runs(strip_markup(text))} or {"latin"}
    ratio = max(_ascent_ratio(c, bold) for c in classes)
    return ratio * pt * PT_TO_MM


@dataclass
class Line:
    text: str
    width_mm: float


def wrap_text(text: str, pt: float, max_w_mm: float, bold: bool = False) -> list[Line]:
    """自动换行。显式 \\n 优先；西文按词、CJK 按字断行。"""
    lines: list[Line] = []
    limit = max_w_mm / _SAFETY
    for para in text.split("\n"):
        if not para.strip():
            lines.append(Line("", 0.0))
            continue
        tokens = _tokenize(para)
        cur = ""
        for tok in tokens:
            cand = cur + tok if cur else tok.lstrip()
            if measure_markup_mm(cand, pt, bold) <= limit or not cur:
                cur = cand
            else:
                lines.append(Line(cur.rstrip(), measure_markup_mm(cur.rstrip(), pt, bold)))
                cur = tok.lstrip()
        if cur:
            lines.append(Line(cur.rstrip(), measure_markup_mm(cur.rstrip(), pt, bold)))
    return lines


def _tokenize(para: str) -> list[str]:
    """切分为不可再分的排版单元：西文词（带前导空格）或单个 CJK 字符。
    `_{...}`/`^{...}` 记号块视为原子，附着在前一个单元上，避免换行截断记号。"""
    tokens: list[str] = []
    cur = ""
    i = 0
    n = len(para)
    while i < n:
        ch = para[i]
        if ch in "_^" and i + 1 < n and para[i + 1] == "{":
            j = para.find("}", i + 2)
            j = j if j != -1 else n - 1
            cur += para[i:j + 1]
            i = j + 1
            continue
        if _char_class(ch) == "cjk":
            if cur:
                tokens.append(cur)
                cur = ""
            tokens.append(ch)
        elif ch == " ":
            if cur:
                tokens.append(cur)
            cur = " "
        else:
            cur += ch
        i += 1
    if cur:
        tokens.append(cur)
    return tokens


LINE_HEIGHT = 1.32


def text_block_height_mm(n_lines: int, pt: float) -> float:
    if n_lines <= 0:
        return 0.0
    return n_lines * pt * PT_TO_MM * LINE_HEIGHT
