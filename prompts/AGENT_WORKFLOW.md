# paperfig · Agent 作图工作流

给 AI agent 的操作指南：当用户要一张科研图（方法框架图、流程图、系统总览、pipeline 图等），按下面的分阶段流程驱动 paperfig。核心原则：**布局用代码精确控制**；默认路径下 AI 只生成"物件"素材；需要更高观感上限时走 **混合模式（base）**——AI 画整图底稿、文字/箭头仍矢量精确可编辑。默认产出顶会级信息密度，每步渲染后自检。

---

## Step 0：风格决策（写 spec 之前）

### 制图模式：纯矢量 vs 混合（base）

| 选 | 适合 | 不适合 |
| --- | --- | --- |
| **纯矢量**（默认） | 严肃排版投稿、方法/架构主文图、需毫米级对齐与可复现 | 观感天花板偏「图框+物件」 |
| **混合 base** | 形象化场景、英雄图、演示/封面/博客附图；模块可具象插画 | 纯符号堆叠、极克制线稿投稿（用纯矢量更稳） |

混合模式要点：底稿拉观感上限，**文字/箭头/图例永远走矢量层**——改文案秒级重渲、不必重抽底稿。完整分支见下方「混合模式（base）」。

---

**默认主题（论文方法图）**：`theme: {preset: neurips}`  
—— Soft Pastel 浅填 + Okabe 描边、印刷字号、无阴影、inline 图例。  
需要「白底彩框」旧顶会克制风时仍可用 `topconf`。

| 场合 | 推荐 preset | 说明 |
| --- | --- | --- |
| **主文方法 / 架构图** | `neurips` | Soft Tech Pastels；印刷线宽与字号 |
| **顶刊/色盲稳妥白底边框** | `topconf` | 白填 + 彩边；无浅色模块面 |
| **演讲 / 博客 / 柔彩卡** | `airy` | 大圆角 + 默认阴影；**勿当论文默认** |
| **博客隐喻 / 编辑型概念图** | `editorial` | 暖纸画布 + 单一 clay accent |
| **系统 / 硬件 / 具身架构** | `isosystem` | 浅晒图；可选 `figure.grid_bg: true` |

从用户的论文/描述里提取：
- **画布**：目标是单栏（~85mm）还是双栏（~180mm）图？据此定 `figure.width`。高度按内容估。
- **分区**：图是否分多个阶段/子图（Stage 1/2、预训练/微调/下游、A/B/C 区）？是 → 每块用一个 `panel`（带色条标题的容器）或彩色虚线 `group`（`color` + `label_pos: inside-bottom`，IEEE 风格分区），画在最底层，内部元素坐标落在各分区范围内。
- **节点**：有哪些模块/步骤/数据？每个是一个 `box`（带文字）还是 `asset`（插画物件）。按语义选 `shape`：流程图用 `stadium`(起止)/`parallelogram`(输入输出)/`diamond`(判定)；数据库/存储/显存用 `cylinder`；预处理用 `hexagon`；编码/解码/采样块用 `trapezoid`；其余 `rect`。跨模态/融合模块可用 `gradient: [c1,c2]` 渐变；渐变色系列（浅→深）逐盒 `fill` 指定。
- **专用示意元素**：神经网络内部 → `network`；数据聚类/嵌入空间 → `scatter`；token 序列/特征金字塔 → `tokens`（`colors` 掩码、`sizes` 递变）；步骤编号 → `badge`；⊕/⊗ 算子 → `marker: oplus/otimes`；无线链路 → `route: arc` + `marker: wifi`。
- **数学记号**：模块名/损失里的下标上标一律用 `_{...}`/`^{...}`（`E_{s}`、`L_{InfoNCE}`、`ℝ^{(B V) H W C}`），别写伪记号。含 `_`/`^`/`{` 的 YAML 值要加引号。
- **可训练/冻结标注**：🔥/❄ → `marker`（`fire`/`snow`），贴在模块角上。
- **连接**：数据/控制流向 → `arrow`，端点直接写**裸节点 id**（`from: a, to: b`）即自动选朝向对方的边、不会"没对上"；只在需要精确指定某条边时才写 `a.right`。**箭头一律先写 `route: avoid`**（走廊 A* 正交避障，自动绕盒、垂直进出、平行错开；标签默认 `label_pos: auto` 碰撞打分，硬拒端点盒 inner 与 box 内 `sketch`/`accent`）。只有对路径不满意时才删掉 `avoid`、改用手写 `via` 微调。显式 `label_offset` 仍按坐标渲染，但 lint（`arrow-label-over-sketch` / `arrow-label-in-node`）不豁免。出口落在本盒 sketch 带会报 `arrow-exit-over-content` → 改 `@t` 或换边。强调主流向的粗箭头 → `style: block`。多语义流（前向/反馈）用双色区分并配图例（短 arrow + text 拼）。
- **素材需求**：哪些概念用插画更直观（设备、器官、文档、机器人……）？这些进 `assets` 抽卡。**抽象概念、带文字的东西不要用 AI 素材**——用 box 或代码画。
- **真实实验图**：频谱/波形/生成结果/热图/定量曲线等**来自真实实验的图，绝不能让 AI 生成（学术不端）**。用 `asset` + `placeholder: true` 占位，投稿前由用户手动放真实文件进去。

### 可选色系（均通过 palette 覆盖实现）

| 方案 | 风格定位 | YAML |
| --- | --- | --- |
| **NeurIPS Soft Pastel（默认）** | 顶会方法图 | `theme: {preset: neurips}` |
| **Okabe 白底彩框** | Nature / CVPR 克制 | `theme: {preset: topconf}` |
| **Teal + Amber** | ICLR 现代感 | `theme: {preset: neurips, palette: {primary: "#00897B", secondary: "#FFB300"}}` |
| **Navy + Coral** | IEEE 期刊沉稳 | `theme: {preset: topconf, palette: {primary: "#1A3A5C", secondary: "#E05A47", section_bg: "#F9F6EE"}}` |
| **Slate + Violet** | 医学 / 生物信息 | `theme: {preset: topconf, palette: {primary: "#3F51B5", secondary: "#7E57C2", section_bg: "#EDE7F6"}}` |
| **Forest + Gold** | 自然科学厚重 | `theme: {preset: topconf, palette: {primary: "#2E7D32", secondary: "#C49A00", section_bg: "#F9F6EE"}}` |
| **Minimal Grey** | arXiv 极简 + 单强调 | `theme: {preset: topconf, palette: {primary: "#263238", secondary: "#546E7A", tertiary: "#0072B2", section_bg: "#ECEFF1"}}` |
| **Airy 柔彩** | talk / blog，非论文默认 | `theme: {preset: airy}` |
| **Editorial 暖纸** | 隐喻封面 / 解释附图 | `theme: {preset: editorial}` |
| **IsoSystem 晒图** | 系统 / 硬件 / 机器人工作站 | `theme: {preset: isosystem}` + 可选 `figure.grid_bg: true` |

用户已指定配色或给了参考图 → 跳过选色，直接按参考提取 hex 写入 `palette`。  
旧主题 `sci` / `warm` / `mono` 仍可用，但**新图不要默认它们**。

### 箭头语义与状态 variant（neurips）

```yaml
# 箭头一键语义（显式 style/color/width 仍可覆盖）
- {type: arrow, from: enc, to: dec, semantic: data, label: "h_t", route: avoid}
- {type: arrow, from: ctrl, to: enc, semantic: control, route: avoid}
- {type: arrow, from: out, to: fuse, semantic: feedback, label: "grad", route: avoid}
- {type: arrow, from: a, to: b, semantic: optional}
- {type: arrow, from: bad, to: sink, semantic: error, label: "fail"}

# 状态 / 对比
- {type: box, id: train, title: "LoRA", variant: trainable, body: "updated"}
- {type: box, id: base, title: "Frozen Enc", variant: frozen, body: "no grad"}
- {type: box, id: ours, title: "Ours", variant: ours}
- {type: box, id: base2, title: "Baseline", variant: baseline}
```

图例：学术主题默认 `style: inline`（无框色键）；需要卡片时写 `style: card`。

### 踩雷清单（精选）

1. PowerPoint 默认蓝橙 + 粗黑描边  
2. 模块名用 Serif、变量用 Sans（应相反）  
3. 无理由混用扁平 2D 与等距 3D  
4. 分区用高饱和黄/蓝大底  
5. 数据流与反馈用同一线型（应用 `semantic:`）  
6. 整图 AI 生成还带乱码文字  
7. 塑料 3D / 霓虹 / 玻璃拟态 / 紫粉发光  
8. 每个盒子塞卡通角色（具象 ≤3，放 I/O 侧）

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
- skip / 残差 / 反馈：`route: avoid` + `style: dashed`/`dotted`；自动路径不满意再手写 `via`，或无线链路用 `route: arc`。
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
- [ ] **画布覆盖**：叶内容不呈「四周大片死白」（lint `canvas-sparse`，不计 panel 底）；也不挤爆（`canvas-crowded`）；九宫格无空洞（`region-empty` / `layout-imbalance`）
- [ ] **标签不穿模**：箭头标签不压 `sketch`/`accent`（`arrow-label-over-sketch`），不深入节点 inner（`arrow-label-in-node`）
- [ ] **数学记号**：一律 `_{...}` / `^{...}`，YAML 值加引号
- [ ] **色彩克制**：同图最多 3 个主色 + 灰系；见配色禁忌表
- [ ] **图例完备**：设计建议 ≥2 种非 muted 语义色加 `legend`（机检 ≥3 色才报，不报≠不需要）
- [ ] **素材风格统一**：有 AI 素材时写顶层 `assets_style`，与 `theme` 色板一致

机检会以 W 级提示部分项：`R-empty-box` / `R-no-section` / `R-no-legend` / `region-empty` / `arrow-exit-over-content` / `arrow-route-awkward`（不阻断，但新图应清零相关项）。

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

## 混合模式（base）：AI 底稿 + 矢量标注

顶层 `base:` 打开混合管线。底稿全画布打底；`box`/`asset`/`panel` 默认**幽灵**（不画壳，几何仍供锚点/路由/lint；`ghost: false` 恢复实体）；文字默认半透明白底板，落在干净浅色净空时**自动免贴片**（`plate: true` 强制保留，`plate: false` 强制关闭；主题 `plate_fill` / `plate_opacity` / `plate_pad` / `plate_radius` 调样式）。

**三原则**：① **标题裸文字优先**——落在底图题字带；对比度不足才显式 `plate: true`，贴片总数宜 ≤3–4；② **贴片限量**——实心矢量卡只放底图预留空槽，严禁盖插画；③ **禁漂白**——禁止对底稿做板下漂白等破坏性后处理（会洗掉仪表盘/齿轮等插画）。对比度不足优先提高 `plate_opacity` / 加大保留带；仍不够再改 prompt 后 `--force` 重抽。

```yaml
base:
  mode: skeleton          # 或 freeform
  prompt: "…"             # 底稿场景描述（构图/模块物件/净空；风格走 style 字段）
  style: sci-flat-pro     # 可选；journal-schematic|technical-lineart|sci-flat-pro
  image: base/base.png    # 选中底稿（相对 spec 目录；pick 可回写）
  candidates: 3
  regions:                # freeform 必填；skeleton 由 layout 几何对齐，通常可不写
    enc: [12, 20, 40, 36]
```

元素可用 `region: <id>` 锚定 `base.regions`（代替手写 `rect`/`at`）。CLI key 走 `PAPERFIG_API_KEY`（`-k` 亦可）。

### 风格包选择（`base.style`）

| 包名 | 适用 | 一句话 |
| --- | --- | --- |
| `journal-schematic` | 医学/生物管线、临床流程、测序/成像 CAD | Nature/Cell methods 风：技术性简化器物 + 低饱和点缀色 |
| `technical-lineart` | 系统/RL/架构、训练管线、模型结构 | OSDI/SOSP + ResNet 工程制图：细线、灰阶、纯模块块图 |
| `sci-flat-pro` | 通用底稿（默认兜底） | 去卡通化专业扁平：可读色块，告别贴纸先验 |

缺省按 theme 映射：`neurips`/`topconf`/`sci`→`sci-flat-pro`，`editorial`→`journal-schematic`，`isosystem`→`technical-lineart`，其余→`sci-flat-pro`。

**禁用污染词**（写进 `base.prompt` / `assets_style` 也会把模型拉向童书贴纸）：`flat vector illustration`、`friendly rounded`、`soft pastel`、`uniform 2px outline`、裸 `three-quarter view`（应写 orthographic / equipment-catalog）。

### skeleton（首选）：布局对齐图生图

1. **写 layout + 矢量标注**：与纯矢量相同写 `layout:` / `elements`（箭头 `route: avoid`、文字/legend 照常）。加 `base: {mode: skeleton, prompt: …}`；技术线稿底稿配 `theme: lineart`。
2. **抽底稿**：`python -m paperfig.cli base gen fig.yaml [-k KEY] [--model nano-banana-fast|nano-banana-2|nano-banana-pro] [--candidates N] [--force]`  
   → 自动渲无文字色块骨架 `base/skeleton.png` → 作参考图（API `urls`）喂 nano-banana 强编辑图生图 → 候选在 `base/candidates/`，contact sheet 在 `base/`。几何与 spec 对齐（实测质心偏移常 <3px）。
3. **目检 contact sheet 筛卡（必经）**：淘汰烤字（字母/数字）、**烤箭头/烤连接线**、模块大漂移、深色花纹占满标签区、擅自画了箭头/连线的卡。骨架同色系浅色头带与浅灰块为文字板保留区——选卡时核对模型是否保持净空（未在保留区插画）。
4. **选卡**：`python -m paperfig.cli base pick fig.yaml <n>`（回写 `base.image`）。
5. **先量后画（像素→mm）**：用 PIL 实测底图各色块 / 题字带 / 空槽的像素矩形，按画布 mm 换算写入 `rect`/`at`/`regions`；矢量 ghost 盒与底图像素对齐（误差 ≤0.8mm）。箭头端点落色块边缘外 0.5–1mm、全正交；标签放走廊净空。
6. **合成渲染**：`python -m paperfig.cli render fig.yaml -o fig.png --svg fig.svg`（矢量层按实测坐标叠字/箭）。
7. **lint**：清零 `base-text-contrast` / `glyph-missing`（E）；留意 `plate-over-art` / `base-region-drift` / `plate-overlap` / `canvas-edge-gap`（W）。base 模式停用 `R-empty-box` / `R-no-section` / `R-no-legend` / `arrow-exit-over-content` / sketch 碰撞等不适用项，其余照常。
8. **只改文字**：改 YAML 文案后直接 `render`——**秒级重渲，不必重抽底稿**。
9. **切片循环复核（交付前强制）**：见 Phase 4。

### freeform：纯文生图 + 人工标区

1. 写 `base: {mode: freeform, prompt: …}`（可先无 `regions`）。
2. `base gen`（无骨架参考，纯文生图）→ 目检 contact sheet（烤字/**烤箭头**/漂移）→ `base pick`。
3. `python -m paperfig.cli base grid fig.yaml` → 出 `base/base_grid.png`（叠 mm 网格），目测标注 `base.regions: {id: [x,y,w,h]}`。
4. 元素用 `region: <id>` 锚定 → `render` → lint（同上；无 skeleton 对拍则无 `base-region-drift`）。

### 底稿 prompt 写法（必须遵守）

**要写**：技术性示意 / 浅色模块填充（配合文字板可读性）；每模块具象物件与构图；模块间留净空；标签落点留浅色平整区。风格用 `base.style` 包，**不要**在 prompt 里堆 `flat vector illustration` / `friendly rounded`。

**禁止（写进 prompt，仍须目检）**：
- 画面内**任何文字、字母、数字**（模型仍偶发烤字 → contact sheet 筛卡是必经步骤）
- **不要画箭头 / 连接线**（矢量层负责）
- 深色满铺、重纹理占满模块、外框装饰、照片级写实 / 霓虹
- 污染词：`flat vector illustration`、`friendly rounded`、吉祥物/笑脸/贴纸隐喻

**抽卡不满意**：先改 `base.prompt` 或 `base.style` 再 `--force`；仍差再换 model（`nano-banana-fast` → `nano-banana-2` / `nano-banana-pro`）。

**反例**：
- ❌ `"图上标注 Encoder / Decoder 和箭头"` → 必烤字 + 抢矢量层
- ❌ `"赛博朋克霓虹，深色背景写满公式"` → 对比度崩、文字板救不回来
- ❌ `"flat vector illustration, friendly rounded pastel icons"` → 童书贴纸先验
- ✅ `"biomedical methods pipeline; left CT scanner module (catalog-style), center U-Net block, right report panel; light fills; no text no arrows; clear gaps"` + `style: journal-schematic`

---

## Phase 0.5：需求优化（生成 Figure Brief）

在 Step 0 / 四层分解落 YAML **之前**，先用 [`FIGURE_BRIEF.md`](FIGURE_BRIEF.md) 把粗糙需求扩写成结构化 **Figure Brief**（图类型、分区与盒子的 title/body/sketch、箭头语义、风格、素材清单、密度自检）。

| 情况 | 是否执行 Phase 0.5 |
| --- | --- |
| **从零作图**（只有自然语言 / 论文片段，尚无可用 spec） | **必做** |
| **复现论文图**（对照论文重画，尚无本仓库 YAML） | **必做** |
| 用户已给出**精确 spec 修改指令**（挪盒子、改色、加箭头、换卡等） | **跳过** → 直接改 YAML（见 [`../AGENTS.md`](../AGENTS.md)） |
| 用户已提供完整可用的 `figure.yaml` 并只要微调 | **跳过** |

做法：将 `FIGURE_BRIEF.md` 当作 system/指令，用户需求（+ 可选论文原文）作输入 → 得到 Brief → Brief 末尾若有「必须向用户确认」（≤3 问）则先问清再进入 Phase 1。混合模式时 Brief 须填「底稿场景描述」（仅 base 需要）。

产出：一份 Figure Brief（markdown）。**本阶段不写 YAML。**

## Phase 1：需求拆解（Brief → YAML）

**新图建议顺序**：`layout:` 树（row/col/grid）表达结构 + 箭头一律 `route: avoid`（零 via）→ `paperfig resolve` 物化为绝对坐标 → 个别 `rect`/`label_offset` 手调 → 定稿 `render`。不要一上来手写满页绝对坐标。

以 Phase 0.5 的 Figure Brief 为权威输入（若已跳过 0.5，则直接从用户描述提取），在 Step 0 色系与四层分解之后落笔：

- **制图模式**：按 Step 0 选择纯矢量或 `base:`（skeleton 优先；freeform 回退）。base 时写入 `base.prompt`（来自 Brief 底稿场景描述）。
- **画布 / 分区 / 节点 / 连接**：按 Brief 的 Layout / Annotations；分区优先 `panel`+`smallcaps`，节点按语义选 `shape`。
- **专用示意**：`network` / `scatter` / `tokens` / `badge` / `marker` / `sketch` / `legend`。
- **数学记号**：`_{...}` / `^{...}`，含特殊字符的 YAML 值加引号。
- **可训练/冻结**：`marker: fire|snow`。
- **素材**：纯矢量路径下具象物件进 `assets`；**base 混合模式**形象主要在整图底稿里，一般不再为同语义再抽物件素材。
- **真实实验图**：频谱/波形/热图/定量曲线 → `placeholder: true`，禁止 AI 生成。

产出：`{project}/figure.yaml` 初稿。复现论文图见 `examples/rep_*`。

## Phase 2：布局草稿（占位渲染）

1. 用 `layout:` 写行列结构（叶子只写 `{ref, w, h}`，元素节不写 `rect`）。
2. `python -m paperfig.cli render figure.yaml --grid -o draft.png --dpi 180`（内部自动 resolve）。base 未选底稿时仍可调几何。
3. 结构满意后：`python -m paperfig.cli resolve figure.yaml -o figure.resolved.yaml`，后续微调改 resolved 版。

```bash
python -m paperfig.cli render {project}/figure.yaml --grid -o {project}/draft.png --dpi 150
```

- `--grid` 叠 10mm 网格核对齐。
- 缺失素材 → 虚线占位，不阻塞。
- **读 PNG** + 清零 E 级；尽量消化 `R-*` 与几何 W（base 模式部分 `R-*` 已停用）。
- 布局在**无素材 / 未定底稿时**定稿。

## Phase 3：素材 / 底稿抽卡

**纯矢量（物件素材）**：

```bash
python -m paperfig.cli assets {project}/figure.yaml --api-key <KEY>
```

- 顶层可写 `assets_style`（英文插画语言），工具会注入与 `theme`/`palette` 一致的 STYLE SPECIFICATIONS（含统一色板、hairline ~1px 描边、技术性示意抽象、正交/器材目录视角硬约束；**禁用** `flat vector illustration` / `friendly rounded`）。
- `assets[].prompt`：**只写「是什么 + 形态」**（如「一块 GPU 加速卡，斜俯视角」）；**不要写色调/风格词**（蓝灰、扁平插画、赛博朋克等——交给风格包，避免与 STYLE SPEC 抢权）；**不要写文字**；避免负面人像/审核敏感词。
- **必须目检** `contact_sheet_{id}.png` 再定稿：自动选卡**只看抠图洁净度**（前景占比/连通块/贴边），**不保证**跨素材视角与细节密度一致。优先选与同图其他素材视角族一致的候选；换卡零成本：
  ```bash
  python -m paperfig.cli select {project}/figure.yaml {asset_id} {index}
  ```
- 全体候选风格都不合 → 改 prompt（仍勿写色调）后 `--force` 重抽。

**混合模式（整图底稿）**：走上文「混合模式（base）」——`base gen` → **目检 contact sheet（查烤字/烤箭头/漂移/保留区侵占）** → `base pick`（freeform 再 `base grid` 标 `regions`）。抽卡不满意先改 prompt 再换 model。

## Phase 4：正式渲染 + 切片循环复核（强制）

成图评审必须走强制循环，不可口头跳过：

```bash
# 1) 高清渲染（定稿 ≥300dpi；投稿导出常用 600）
python -m paperfig.cli render {project}/figure.yaml -o {project}/figure.png --svg {project}/figure.svg --dpi 300

# 2) 网格切片放大（单片宽 ≥1200px + overview.png）
python -m paperfig.cli tiles {project}/figure.yaml -o {project}/tiles --dpi 300
```

对 `tiles/tile_r*c*.png` **逐格目检**，按 checklist 记缺陷清单（有一条就记一条）：

1. **字压图 / 贴片盖插画**：矢量文字或 plate 压在插画主体或花纹上（对照 `plate-over-art`）
2. **烤字 / 烤箭头 / 烤连接线**：底稿残留字母数字或模型自绘箭头
3. **悬空箭头尖**：端点悬空、插进插画、未贴模块边（宜落色块边缘外 0.5–1mm）
4. **空壳卡片**：只有标题的空心盒 / 幽灵壳无内容且未落在预留空槽
5. **台阶折线**：不必要的直角折、绕行别扭
6. **标签压折角或压内容**：箭头标签压折点 / 压模块 / 压其他字
7. **空白带与密度失衡**：边缘空带、局部过挤或过空（对照 `canvas-edge-gap` 等机检）
8. **图例语义与画面一致**：色键/虚线样式与图中对应
9. **plate 互叠**：文字底板互相重叠遮挡

然后：**修 spec 或重抽底稿 → 重渲 → 再 tiles → 再逐片目检**。

**退出条件**：连续一整轮目检 **零新发现** 才可进入交付；**至少两轮**（第 1 轮发现问题并修好后，第 2 轮必须再扫一遍确认无新问题）。未满足不得交付。

机检同步要求（`prompts/visual_rubric.md`）：

1. E 清零；纯矢量尽量清零 `R-empty-box` / `R-no-section` / `R-no-legend`；base 重点清零 `base-text-contrast` / `glyph-missing`，并处理 `plate-over-art` / `base-region-drift` / `plate-overlap` / `canvas-edge-gap`。
2. 目检以上 checklist + 对齐、留白、箭头语义、视觉丰度、配色、上下标。

## Phase 5：交付

展示 `figure.png` + `figure.svg` + 体检结论 + **切片复核已通过（最后一轮零新发现）**。说明用户可改任意坐标重渲；物件素材可换卡/重抽；**base 模式改文字不必重抽底稿**，换场景观感才 `base gen --force`。
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
- 空标题框交稿前必须补 body/sketch/icon（base 幽灵盒除外，形象在底稿里）。
- 多语义色忘记 `legend` → `R-no-legend`（base 模式该检查已停用，仍建议需要时手加）。
- 箭头穿盒 → 先 `route: avoid`，不满再用 `via`；文字溢出 → 加高或缩短。
- 上下标 YAML 忘加引号。
- 素材风格不统一 → 补 `assets_style`，色板跟 `theme.palette`。
- **真实实验图绝不 AI 生成**（频谱/波形/热图/生成样本/定量曲线）→ 一律 `asset` + `placeholder: true`。
- **文字/公式/数字走代码**，AI 素材只画物件、prompt 禁止文字。
- **base 底稿**禁止烤字/箭头；contact sheet 目检不可省；文字压花纹 → 挪字 / 开 plate / 重抽浅色底稿。
