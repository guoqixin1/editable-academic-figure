# paperfig 视觉评审 rubric

渲染后先看机检（`render` 输出的体检），再做目检。机检覆盖客观项（溢出、重叠、越界、字号、穿线、疏密、视觉丰度），目检覆盖审美与语义。

## 一、机检（lint 自动）

`render` 会打印 E/W 两级问题，覆盖客观项：溢出 / 重叠 / 越界 / 字号 / 穿线 / 疏密，以及**对齐与等距近失**（`row-misaligned` / `col-misaligned` / `uneven-gap`——同排/列几乎对齐或等距却差 1–2mm 时提示 snap，明显有意的错落不报）。

> **完整体检码 → 修法表见 [`../AGENTS.md`](../AGENTS.md) §4（唯一权威，勿在本文件重复维护）。**  
> 视觉丰度相关速查：`R-empty-box` / `R-no-section` / `R-no-legend`（W）；穿线：`arrow-through-node`（E）。

判读口径：**E 级必须清零；新图应尽量清零 `R-*`；其他 W 尽量清零**（`asset-placeholder` 例外）。

## 二、目检（多模态看图）

渲染 PNG 后逐项打分：

### A. 几何与语义

1. **对齐与网格**：同一行/列边缘是否对齐、间距是否均匀？用 `--grid` 叠加 10mm 网格核对（机检的 `row/col-misaligned`、`uneven-gap` 会先抓 1–2mm 级近失）。
2. **留白与呼吸感**：有无大块死白或挤成一团？紧凑但不拥挤。
3. **箭头语义**：方向、锚点、主流向 vs skip/反馈是否正确？双向流用 `bidir: true`。
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
10. **图例完备性**
    - 设计建议 ≥2 种非 muted 语义色加 `legend`（机检 ≥3 色才报，不报≠不需要）；swatch 与图中颜色是否一致？
11. **素材风格统一**
    - AI 物件是否像同一画师（描边、色板、视角）？
    - 是否写了顶层 `assets_style`，且与 `theme`/`palette` 协调？
    - 抠图边缘干净、无白边 halo？

## 三、自动修改循环（开发/调优阶段）

1. 发现问题 → 改 `spec.yaml`（坐标、字号、route、variant、sketch、legend、panel）或换素材候选；
2. 重新 `render`；
3. 重新机检 + 目检；
4. **最多 3 轮**，仍不达标则记录遗留问题交人工。

> 抠图/素材类问题不在 spec 层修，而是 `select` 换卡或 `assets --force` 重抽。
> 「图太朴素」优先按 AGENTS.md 升级配方：换 `topconf` → 补 sketch → 加 legend → panel smallcaps → 开 shadow。
