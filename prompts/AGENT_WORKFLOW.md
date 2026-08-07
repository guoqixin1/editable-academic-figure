# paperfig · Agent 作图工作流

给 AI agent 的操作指南：当用户要一张科研图（方法框架图、流程图、系统总览、pipeline 图等），按下面的分阶段流程驱动 paperfig。核心原则：**布局用代码精确控制，AI 只生成"物件"素材，默认产出顶会级信息密度，每步渲染后自检。**

---

## Step 0：风格决策（写 spec 之前）

**默认主题**：`theme: {preset: topconf}`（白填充 + Okabe-Ito 色边框，顶会克制风）。  
现代 ML / RL 示意、柔彩浮动面板 → 用 `theme: {preset: airy}`（默认 soft shadow + pastel token）。

从用户的论文/描述里提取：
- **画布**：目标是单栏（~85mm）还是双栏（~180mm）图？据此定 `figure.width`。高度按内容估。
- **分区**：图是否分多个阶段/子图（Stage 1/2、预训练/微调/下游、A/B/C 区）？是 → 每块用一个 `panel`（带色条标题的容器）或彩色虚线 `group`（`color` + `label_pos: inside-bottom`，IEEE 风格分区），画在最底层，内部元素坐标落在各分区范围内。
- **节点**：有哪些模块/步骤/数据？每个是一个 `box`（带文字）还是 `asset`（插画物件）。按语义选 `shape`：流程图用 `stadium`(起止)/`parallelogram`(输入输出)/`diamond`(判定)；数据库/存储/显存用 `cylinder`；预处理用 `hexagon`；编码/解码/采样块用 `trapezoid`；其余 `rect`。跨模态/融合模块可用 `gradient: [c1,c2]` 渐变；渐变色系列（浅→深）逐盒 `fill` 指定。
- **专用示意元素**：神经网络内部 → `network`；数据聚类/嵌入空间 → `scatter`；token 序列/特征金字塔 → `tokens`（`colors` 掩码、`sizes` 递变）；步骤编号 → `badge`；⊕/⊗ 算子 → `marker: oplus/otimes`；无线链路 → `route: arc` + `marker: wifi`。
- **数学记号**：模块名/损失里的下标上标一律用 `_{...}`/`^{...}`（`E_{s}`、`L_{InfoNCE}`、`ℝ^{(B V) H W C}`），别写伪记号。含 `_`/`^`/`{` 的 YAML 值要加引号。
- **可训练/冻结标注**：🔥/❄ → `marker`（`fire`/`snow`），贴在模块角上。
- **连接**：数据/控制流向 → `arrow`，端点直接写**裸节点 id**（`from: a, to: b`）即自动选朝向对方的边、不会"没对上"；只在需要精确指定某条边时才写 `a.right`。残差/skip/需绕开盒子的线 → 用 `arrow.via` 途经点。强调主流向的粗箭头 → `style: block`。多语义流（前向/反馈）用双色区分并配图例（短 arrow + text 拼）。
- **素材需求**：哪些概念用插画更直观（设备、器官、文档、机器人……）？这些进 `assets` 抽卡。**抽象概念、带文字的东西不要用 AI 素材**——用 box 或代码画。
- **真实实验图**：频谱/波形/生成结果/热图/定量曲线等**来自真实实验的图，绝不能让 AI 生成（学术不端）**。用 `asset` + `placeholder: true` 占位，投稿前由用户手动放真实文件进去。

### 可选色系（均通过 palette 覆盖实现）

| 方案 | 风格定位 | YAML |
| --- | --- | --- |
| **Okabe-Ito（默认）** | Nature / CVPR，色盲友好 | `theme: {preset: topconf}` |
| **Teal + Amber** | ICLR / NeurIPS 现代感 | `theme: {preset: topconf, palette: {primary: "#00897B", secondary: "#FFB300", section_bg: "#ECEFF1"}}` |
| **Navy + Coral** | IEEE 期刊沉稳 | `theme: {preset: topconf, palette: {primary: "#1A3A5C", secondary: "#E05A47", section_bg: "#F9F6EE"}}` |
| **Slate + Violet** | 医学 / 生物信息 | `theme: {preset: topconf, palette: {primary: "#3F51B5", secondary: "#7E57C2", section_bg: "#EDE7F6"}}` |
| **Forest + Gold** | 自然科学厚重 | `theme: {preset: topconf, palette: {primary: "#2E7D32", secondary: "#C49A00", section_bg: "#F9F6EE"}}` |
| **Minimal Grey** | arXiv 极简 + 单强调 | `theme: {preset: topconf, palette: {primary: "#263238", secondary: "#546E7A", tertiary: "#0072B2", section_bg: "#ECEFF1"}}` |
| **Airy 柔彩** | 现代 ML/RL 示意 | `theme: {preset: airy}` |

用户已指定配色或给了参考图 → 跳过选色，直接按参考提取 hex 写入 `palette`。  
旧主题 `sci` / `warm` / `mono` 仍可用，但**新图不要默认它们**。

---

## 四层分解法（写每一个 figure.yaml 时强制执行）

把需求拆成四层再落 YAML，缺一不可：

### Layer 1 · 全局

- 画布：单栏 `width≈85` / 双栏 `width≈180`；高度贴内容（覆盖率目标见下方 checklist）。
- 面板划分：几列几行？主阅读方向（左→右 / 上→下）。
- 图类型：总体框架 / 网络架构 / 模块详解 / 对比消融 / 数据行为（见下方版式卡）。

### Layer 2 · 逐分区

每个分区用 `panel`（推荐 `header_style: smallcaps` + 浅 `fill`）或带 `fill` 的 `group`。  
**每个 box 必须有子内容**，至少满足其一：

| 子内容 | 字段 |
| --- | --- |
| 短说明 | `body: "..."` |
| 单色缩略图 | `sketch: waveform` 等（见词汇表） |
| AI/位图图标 | `icon: asset_id` |
| 嵌套子元素 | `valign: top` 容器卡，内部再放 sketch/tokens/asset |

**禁止**只有标题的空心圆角框。

### Layer 3 · 全局标注

- 主数据流箭头：`weight: heavy`，`label` 标维度/含义（如 `"R^{B×D}"`、`"features"`）。
- skip / 残差 / 反馈：`style: dashed` 或 `dotted`，必要时 `via` / `route: arc`。
- **设计建议**：≥2 种非 muted 语义色就加 `type: legend`（不要用手拼短箭头冒充）。机检 `R-no-legend` 在 ≥3 色时才报（阈值宽松，不报不代表不需要）。

### Layer 4 · 风格规格

| 角色 | variant / 字段 |
| --- | --- |
| 核心贡献模块 | `variant: primary`，可加 `accent: left`、`shadow: true` |
| 次要 / 辅助 | `variant: secondary` |
| 输出 / 点缀 | `variant: tertiary` 或 `accent` |
| 常规结构 | `variant: muted` 或 `plain` |
| 主箭头 | `weight: heavy` |
| 注释箭头 | `weight: thin`，`style: dashed` |

topconf 下卡片默认无投影；需要浮起时显式 `shadow: true`。airy 下默认已开投影。

---

## 信息密度 checklist（交稿前自检）

写完 / 改完 spec 后对照：

- [ ] **无空盒子**：每个模块有 body / sketch / icon，或确有子元素落在内部
- [ ] **缩略图密度**：≥50% 的内容模块内嵌 `sketch` 或 `icon`
- [ ] **箭头有标签**：主要数据流箭头都有 `label`（维度或语义）
- [ ] **有分区底色**：存在 `panel` 或带 `fill` 的 `group`
- [ ] **画布覆盖**：内容不呈「四周大片死白」（lint `canvas-sparse`）；也不挤爆（`canvas-crowded`）
- [ ] **数学记号**：一律 `_{...}` / `^{...}`，YAML 值加引号
- [ ] **色彩克制**：同图最多 3 个主色 + 灰系；见配色禁忌表
- [ ] **图例完备**：设计建议 ≥2 种非 muted 语义色加 `legend`（机检 ≥3 色才报，不报≠不需要）
- [ ] **素材风格统一**：有 AI 素材时写顶层 `assets_style`，与 `theme` 色板一致

机检会以 W 级提示部分项：`R-empty-box` / `R-no-section` / `R-no-legend`（不阻断，但新图应清零）。

---

## sketch 词汇表（语义 → kind）

优先用程序化 `sketch` 提高密度，真实实验图仍用 `placeholder: true`。

| 语义 | `kind` |
| --- | --- |
| 时序 / 信号 | `waveform` |
| 频谱 | `spectrum` |
| 注意力 / 空间热力 | `heatmap` |
| 混淆矩阵 / 相关阵 | `matrix` |
| 嵌入 / 聚类 | `scatter` |
| 训练收敛（上升） | `curve` |
| 损失下降 | `curve_desc` |
| 特征图 / patch | `grid` |
| 分布 / 直方图 | `bars` / `distribution` |
| 多层堆叠 / MLP 条 | `layers` |
| 层次嵌套 | `nested` |
| 决策树 | `tree` |
| 压缩 / 瓶颈 | `dots_flow` |

```yaml
- {type: box, id: enc, rect: [10, 16, 36, 32], title: Encoder,
   variant: primary, accent: left, sketch: heatmap, valign: top, shadow: true}
# 或独立放置：
- {type: sketch, id: sk1, rect: [50, 20, 28, 18], kind: curve_desc, label: "loss"}
```

---

## 5 类图版式卡

### 1) 总体框架图（Overall Framework）

- **比例**：约 16:9（双栏 `180×96`～`200×100`）
- **必选元素**：`panel`/`group`(fill) 分区 · 输入/阶段/输出 `box` · 主箭头+label · ≥50% 模块 `sketch`/`icon` · `legend`（多色时）
- **骨架**：

```yaml
figure: {width: 180, height: 96, dpi: 600, assets_dir: assets}
theme: {preset: topconf}
elements:
  - {type: panel, id: p, rect: [4, 4, 172, 88], title: Overall Pipeline,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: box, id: inp, rect: [12, 28, 32, 36], title: Input, body: "raw x",
     variant: primary, accent: left, sketch: grid, valign: top}
  - {type: box, id: mid, rect: [62, 28, 40, 36], title: Core Module,
     variant: primary, sketch: layers, valign: top, shadow: true}
  - {type: box, id: out, rect: [122, 28, 32, 36], title: Output, body: "y",
     variant: secondary, sketch: bars, valign: top}
  - {type: arrow, from: inp.right, to: mid.left, label: "R^{B×D}", weight: heavy}
  - {type: arrow, from: mid.right, to: out.left, label: logits, weight: heavy}
  - {type: arrow, from: out.top, to: mid.top, route: arc, style: dashed,
     label: feedback, weight: thin}
  - {type: legend, id: lg, at: [128, 72], items: [
      {swatch: box, color: "#0072B2", label: "core"},
      {swatch: box, color: "#E69F00", label: "aux"},
      {swatch: dashed, color: "#4D4D4D", label: "skip"},
    ]}
```

### 2) 网络架构图（Network Architecture）

- **比例**：16:9 或 3:2
- **必选元素**：层 `box`（Conv/Attn/FFN）· 维度箭头 · `style: dashed` 残差 · 重复块用 `group`+`×N` 文本 · 注意力/特征用 `sketch: heatmap|grid`
- **骨架**：

```yaml
figure: {width: 180, height: 72, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: box, id: x, rect: [8, 24, 28, 28], title: Input, sketch: grid, valign: top}
  - {type: box, id: enc, rect: [50, 16, 50, 44], title: Encoder Stack,
     variant: primary, valign: top, accent: left}
  - {type: sketch, id: attn, rect: [56, 28, 38, 24], kind: heatmap, label: "Attn"}
  - {type: box, id: head, rect: [120, 24, 40, 28], title: Head, body: "softmax",
     variant: secondary, sketch: bars, valign: top}
  - {type: arrow, from: x.right, to: enc.left, label: "B×L×D", weight: heavy}
  - {type: arrow, from: enc.right, to: head.left, label: "B×D"}
  - {type: arrow, from: x.top, to: head.top, via: [[20, 8], [140, 8]],
     style: dashed, label: skip, weight: thin}
  - {type: group, id: blk, members: [enc], label: "×N", fill: "#F7F7F7",
     style: dashed, label_pos: inside-top}
```

### 3) 模块详解图（Module Detail）

- **比例**：约 4:3（`140×100`）
- **必选元素**：中心机制大块 · 逐步 `box` · 中间表示 `sketch` · 旁注公式 `text` · skip 虚线
- **骨架**：

```yaml
figure: {width: 140, height: 100, dpi: 600}
theme: {preset: topconf, palette: {primary: "#00897B", secondary: "#FFB300"}}
elements:
  - {type: panel, id: p, rect: [4, 4, 132, 92], title: Module Detail,
     header_style: smallcaps, fill: "#ECEFF1"}
  - {type: box, id: q, rect: [12, 24, 30, 28], title: "Q", sketch: matrix, valign: top,
     variant: primary}
  - {type: box, id: k, rect: [12, 60, 30, 28], title: "K", sketch: matrix, valign: top,
     variant: secondary}
  - {type: box, id: op, rect: [55, 40, 36, 32], title: "Attn", body: "softmax",
     variant: primary, accent: left, sketch: heatmap, valign: top, shadow: true}
  - {type: box, id: v, rect: [104, 40, 26, 32], title: "V→Y", sketch: layers, valign: top}
  - {type: arrow, from: q.right, to: op.left, label: "QK^{T}"}
  - {type: arrow, from: k.right, to: op.left, style: dashed}
  - {type: arrow, from: op.right, to: v.left, weight: heavy}
  - {type: text, at: [70, 88], text: "Attention(Q,K,V)=softmax(QK^{T}/√d)V", size: 6.5}
  - {type: legend, id: lg, at: [100, 22], items: [
      {swatch: box, color: "#00897B", label: "Q / Attn"},
      {swatch: box, color: "#FFB300", label: "K"},
    ]}
```

### 4) 对比 / 消融图（Comparison / Ablation）

- **比例**：约 16:9 宽表
- **必选元素**：N 列 `panel` 或 `group` · 共享结构用 `muted` · 差异用 `primary`/`secondary` + 虚线框 · 列标题 smallcaps · 可选底部指标 `sketch: bars`
- **骨架**：

```yaml
figure: {width: 180, height: 80, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: panel, id: p0, rect: [4, 8, 54, 52], title: Baseline,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: panel, id: p1, rect: [63, 8, 54, 52], title: Ablation, variant: secondary,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: panel, id: p2, rect: [122, 8, 54, 52], title: Ours, variant: primary,
     header_style: smallcaps, fill: "#F0F6FA"}
  - {type: box, id: b0, rect: [12, 22, 38, 28], title: Shared, body: backbone,
     variant: muted, sketch: layers, valign: top}
  - {type: box, id: b1, rect: [71, 22, 38, 28], title: w/o X, body: remove X,
     variant: secondary, sketch: curve_desc, valign: top}
  - {type: box, id: b2, rect: [130, 22, 38, 28], title: Full, body: "+ module",
     variant: primary, accent: left, sketch: curve, valign: top, shadow: true}
  - {type: group, id: diff, members: [b2], style: dashed, color: "#0072B2",
     label: "diff", label_pos: inside-bottom}
  - {type: sketch, id: met, rect: [50, 64, 80, 12], kind: bars, label: "metric"}
  - {type: legend, id: lg, at: [140, 64], items: [
      {swatch: box, color: "#0072B2", label: "ours"},
      {swatch: box, color: "#E69F00", label: "ablate"},
      {swatch: box, color: "#CCCCCC", label: "shared"},
    ]}
```

### 5) 数据 / 行为图（Data / Behavior）

- **比例**：4:3 或 1:1
- **必选元素**：多 `panel` 网格 · 每格 `sketch`（scatter/heatmap/curve）· 类别色区分 · `legend`
- **骨架**：

```yaml
figure: {width: 140, height: 100, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: panel, id: a, rect: [4, 4, 64, 44], title: Embeddings,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: panel, id: b, rect: [72, 4, 64, 44], title: Attention,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: panel, id: c, rect: [4, 52, 64, 44], title: Train curve,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: panel, id: d, rect: [72, 52, 64, 44], title: Features,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: sketch, id: s1, rect: [12, 16, 48, 26], kind: scatter}
  - {type: sketch, id: s2, rect: [80, 16, 48, 26], kind: heatmap}
  - {type: sketch, id: s3, rect: [12, 64, 48, 26], kind: curve}
  - {type: sketch, id: s4, rect: [80, 64, 48, 26], kind: grid}
  - {type: legend, id: lg, at: [108, 86], columns: 1, items: [
      {swatch: dot, color: "#0072B2", label: "class A"},
      {swatch: dot, color: "#E69F00", label: "class B"},
    ]}
```

---

## 配色禁忌表（避免「AI 生图感」）

| 禁止 | 正确做法 |
| --- | --- |
| 4–5 种彩色背景面板 | 白/近白为主 + 极浅 `section_bg` 分区 |
| 高饱和彩色 banner 标题条 | topconf 下用 `header_style: smallcaps` + 灰分割线 |
| 每个模块不同彩色填充 | 白填充 + 彩色/灰**边框**（topconf 默认） |
| 彩虹渐变 / 霓虹描边 | 纯色、扁平；融合例外可用双色 `gradient`（仅 2 色） |
| 同图 5+ 主色 | **最多 3 主色 + 灰系** |
| 彩色花哨缩略图 | `sketch` 保持单色/双色；AI 素材走 `assets_style` 统一色板 |
| 无图例的多语义色 | 加 `legend` |

---

## Phase 0.5：需求优化（生成 Figure Brief）

在 Step 0 / 四层分解落 YAML **之前**，先用 [`FIGURE_BRIEF.md`](FIGURE_BRIEF.md) 把粗糙需求扩写成结构化 **Figure Brief**（图类型、分区与盒子的 title/body/sketch、箭头语义、风格、素材清单、密度自检）。

| 情况 | 是否执行 Phase 0.5 |
| --- | --- |
| **从零作图**（只有自然语言 / 论文片段，尚无可用 spec） | **必做** |
| **复现论文图**（对照论文重画，尚无本仓库 YAML） | **必做** |
| 用户已给出**精确 spec 修改指令**（挪盒子、改色、加箭头、换卡等） | **跳过** → 直接改 YAML（见 [`../AGENTS.md`](../AGENTS.md)） |
| 用户已提供完整可用的 `figure.yaml` 并只要微调 | **跳过** |

做法：将 `FIGURE_BRIEF.md` 当作 system/指令，用户需求（+ 可选论文原文）作输入 → 得到 Brief → Brief 末尾若有「必须向用户确认」（≤3 问）则先问清再进入 Phase 1。

产出：一份 Figure Brief（markdown）。**本阶段不写 YAML。**

## Phase 1：需求拆解（Brief → YAML）

以 Phase 0.5 的 Figure Brief 为权威输入（若已跳过 0.5，则直接从用户描述提取），在 Step 0 色系与四层分解之后落笔：

- **画布 / 分区 / 节点 / 连接**：按 Brief 的 Layout / Annotations；分区优先 `panel`+`smallcaps`，节点按语义选 `shape`。
- **专用示意**：`network` / `scatter` / `tokens` / `badge` / `marker` / `sketch` / `legend`。
- **数学记号**：`_{...}` / `^{...}`，含特殊字符的 YAML 值加引号。
- **可训练/冻结**：`marker: fire|snow`。
- **素材**：具象物件进 `assets`（prompt 只写「是什么+形态」）；抽象概念用 box+sketch，**不要** AI 生成文字/公式。
- **真实实验图**：频谱/波形/热图/定量曲线 → `placeholder: true`，禁止 AI 生成。

产出：`{project}/figure.yaml` 初稿。复现论文图见 `examples/rep_*`。

## Phase 2：布局草稿（占位渲染）

```bash
python -m paperfig.cli render {project}/figure.yaml --grid -o {project}/draft.png --dpi 150
```

- `--grid` 叠 10mm 网格核对齐。
- 缺失素材 → 虚线占位，不阻塞。
- **读 PNG** + 清零 E 级；尽量消化 `R-*` 与几何 W。
- 布局在**无素材时**定稿。

## Phase 3：素材抽卡

```bash
python -m paperfig.cli assets {project}/figure.yaml --api-key <KEY>
```

- 顶层可写 `assets_style`（英文插画语言），工具会注入与 `theme`/`palette` 一致的 STYLE SPECIFICATIONS（含统一色板、~2px 描边、图标级抽象、三分之四视角硬约束）。
- `assets[].prompt`：**只写「是什么 + 形态」**（如「一块 GPU 加速卡，斜俯视角」）；**不要写色调/风格词**（蓝灰、扁平插画、赛博朋克等——交给风格包，避免与 STYLE SPEC 抢权）；**不要写文字**；避免负面人像/审核敏感词。
- **必须目检** `contact_sheet_{id}.png` 再定稿：自动选卡**只看抠图洁净度**（前景占比/连通块/贴边），**不保证**跨素材视角与细节密度一致。优先选与同图其他素材视角族一致的候选；换卡零成本：
  ```bash
  python -m paperfig.cli select {project}/figure.yaml {asset_id} {index}
  ```
- 全体候选风格都不合 → 改 prompt（仍勿写色调）后 `--force` 重抽。

## Phase 4：正式渲染 + 视觉评审闭环

```bash
python -m paperfig.cli render {project}/figure.yaml -o {project}/figure.png --svg {project}/figure.svg
```

按 `prompts/visual_rubric.md`：

1. 机检：E 清零；`R-empty-box` / `R-no-section` / `R-no-legend` 尽量清零。
2. 目检：对齐、留白、箭头语义、**视觉丰度**、配色、素材风格统一、文字/上下标。
3. 最多 3 轮修改；遗留项交用户。

## Phase 5：交付

展示 `figure.png` + `figure.svg` + 体检结论。说明用户可改任意坐标重渲；素材可换卡/重抽。

---

## 避坑

作图/改图的**硬规则、YAML 引号陷阱、体检码 → 修法**统一见 [`../AGENTS.md`](../AGENTS.md)：§0 铁律 · §4 体检码 · §5 陷阱（唯一权威，不在此重复维护）。

| 审稿人雷点 | paperfig 对策 |
| --- | --- |
| AI 乱码文字 | 文字走代码渲染；素材禁止文字 |
| 模糊/低分 | SVG，导出 ≥600dpi |
| 中文丢字 | fonts.py 中西文分段 + Noto CJK |
| 布局失控 | 坐标全在 spec，可复现 |
| 素材白边 | 抠图 + lint 贴边 |
| 配色花哨 | 默认 topconf + 配色禁忌表 |
| 图太素 / 空心框 | 四层分解 + 密度 checklist + `R-*` lint |
| **编造实验结果** | 真实图一律 `placeholder: true` |

## 常见坑

- 不要用 AI 素材承载文字/公式/精确数字。
- 不要把真实实验结果交给 AI 生成。
- 空标题框交稿前必须补 body/sketch/icon。
- 多语义色忘记 `legend` → `R-no-legend`。
- 箭头穿盒 → `route` / `via`；文字溢出 → 加高或缩短。
- 上下标 YAML 忘加引号。
- 素材风格不统一 → 补 `assets_style`，色板跟 `theme.palette`。
- **真实实验图绝不 AI 生成**（频谱/波形/热图/生成样本/定量曲线）→ 一律 `asset` + `placeholder: true`。
- **文字/公式/数字走代码**，AI 素材只画物件、prompt 禁止文字。
