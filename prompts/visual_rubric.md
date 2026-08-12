# paperfig 视觉评审 rubric

渲染后先看机检（`render` 输出的体检），再做**切片放大逐格目检**。机检覆盖客观项（溢出、重叠、越界、字号、穿线、疏密、边缘空带、视觉丰度），目检覆盖审美与语义。

## 一、机检（lint 自动）

`render` 会打印 E/W 两级问题，覆盖客观项：溢出 / 重叠 / 越界 / 字号 / 穿线 / 疏密 / **边缘空带**（`canvas-edge-gap`），以及**对齐与等距近失**（`row-misaligned` / `col-misaligned` / `uneven-gap`——同排/列几乎对齐或等距却差 1–2mm 时提示 snap，明显有意的错落不报）。

> **完整体检码 → 修法表见 [`../AGENTS.md`](../AGENTS.md) §4（唯一权威，勿在本文件重复维护）。**  
> 视觉丰度相关速查：`R-empty-box` / `R-no-section` / `R-no-legend`（W）；穿线：`arrow-through-node`（E）；边缘空带：`canvas-edge-gap`（W）。

判读口径：**E 级必须清零；新图应尽量清零 `R-*`；其他 W 尽量清零**（`asset-placeholder` 例外）。

## 二、目检（切片放大逐格）

```bash
python -m paperfig.cli render fig.yaml -o fig.png --dpi 300
python -m paperfig.cli tiles fig.yaml -o tiles --dpi 300
```

对 `tiles/tile_r*c*.png` **逐格**打分（不要只看 overview）：

### A. 几何与语义

1. **对齐与网格**：同一行/列边缘是否对齐、间距是否均匀？用 `--grid` 叠加 10mm 网格核对（机检的 `row/col-misaligned`、`uneven-gap` 会先抓 1–2mm 级近失）。
2. **留白与呼吸感 / 空白带**：有无大块死白、边缘空带或挤成一团？紧凑但不拥挤（对照 `canvas-edge-gap`）。
3. **箭头语义与端点**：方向、锚点正确？端点悬空 / 插进插画？有无多余台阶折线？
4. **文字可读性**：中西文、上下标 `_{...}`/`^{...}` 是否正确。
5. **论文级元素**：`tokens` / `marker` / `gradient` 是否表意。
6. **审稿人雷点**：无乱码文字、无 AI 编造实验结果、线宽一致、导出 ≥600dpi。

### B. 视觉丰度（新图必查）

7. **信息密度**
   - 空框率：是否存在「只有标题的空心盒」？
   - sketch/icon 占比：内容模块是否约一半以上内嵌缩略图或图标？
   - 主箭头是否有维度/语义标签？
8. **层次**
   - 是否有分区底色（`panel` / `group fill`）？
   - 字号层级是否拉开（title > body > caption）？
   - 需要浮起时是否开了 `shadow`（topconf 需显式；airy 默认开）？
   - panel 是否优先 `header_style: smallcaps`（克制风）而非高饱和 banner？
9. **语义配色一致性**
   - `primary` = 核心贡献，`secondary` = 次要，`muted`/`plain` = 常规？
   - 同图主色是否 ≤3 + 灰系？有无彩虹渐变/霓虹？
10. **图例完备性与语义一致**
    - 设计建议 ≥2 种非 muted 语义色加 `legend`（机检 ≥3 色才报，不报≠不需要）；swatch / 虚线样式与图中是否一致？
11. **素材风格统一**
    - AI 物件是否像同一画师（描边、色板、视角）？
    - 是否写了顶层 `assets_style`，且与 `theme`/`palette` 协调？
    - 抠图边缘干净、无白边 halo？

### C. 混合模式 / 成图硬 checklist（切片必查）

12. **贴片 / 文字盖插画**：矢量字或 plate 是否压住插画主体？（对照 `plate-over-art`）
13. **烤字 / 烤箭头 / 烤连接线**：底稿是否残留字母数字或模型自绘箭头？
14. **标签压折角或压内容**：箭头标签是否压折点 / 压模块 / 压其他字？
15. **plate 互叠**：文字底板是否互相重叠？
16. **保留区遵守**（skeleton）：浅灰标签保留区是否被插画侵占？
17. **悬空箭头尖**：端点是否悬空 / 插进插画 / 未贴色块边（宜外偏 0.5–1mm）？
18. **空壳卡片**：是否出现只有标题的空心盒，或实心矢量卡盖住插画而非落在预留空槽？

## 三、切片循环复核（交付强制）

1. `render`（≥300dpi）→ `paperfig tiles` → **逐格目检**按上表记缺陷清单；**必查三项**：贴片/文字盖插画、悬空箭头尖、空壳卡片；
2. 修 `spec.yaml`（或换底稿/换卡）→ 重渲 → 再 tiles → 再逐格复检；
3. **退出条件**：连续一整轮 **零新发现**；**至少两轮**。未满足不得交付。

> 抠图/素材类问题不在 spec 层修，而是 `select` 换卡或 `assets --force` 重抽。
> 「图太朴素」优先按 AGENTS.md 升级配方：换 `topconf` → 补 sketch → 加 legend → panel smallcaps → 开 shadow。
> base 文字压花纹 → 挪字 / 开 plate / 重抽浅色底稿（骨架已画保留区，选卡时核对）；禁漂白底稿。
