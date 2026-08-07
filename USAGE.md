# paperfig 使用说明

**Editable Academic Figure** —— 受控学术作图工具：**布局用 YAML 逐毫米定死（可控、可复现、可微调），AI 只生成插画物件并抠成透明图（美观），文字/公式全部矢量渲染（杜绝乱码）**。目标是产出可直接进 Illustrator/Inkscape 的可编辑学术论文配图草稿，把手工调整压到最少。

适合方法框架图、网络架构图、模块详解、对比消融、数据行为示意，以及体系结构 / 分布式 / 流程图。新图默认 **NeurIPS Soft Pastel**（`neurips`）。

> 人用本文档；让 AI 助手二次改图 → [AGENTS.md](AGENTS.md)。从零作图先读 [prompts/FIGURE_BRIEF.md](prompts/FIGURE_BRIEF.md) 与 [prompts/AGENT_WORKFLOW.md](prompts/AGENT_WORKFLOW.md)。

---

## 目录

1. [安装](#1-安装)
2. [五步工作流](#2-五步工作流)
3. [主题与配色](#3-主题与配色)
4. [信息密度](#4-信息密度)
5. [元素与字段](#5-元素与字段)
6. [箭头与路由](#6-箭头与路由)
7. [AI 素材抽卡](#7-ai-素材抽卡)
8. [命令行与 studio](#8-命令行与-studio)
9. [体检码速查](#9-体检码速查)
10. [实践经验与常见坑](#10-实践经验与常见坑)
11. [后期二次修改](#11-后期二次修改)

---

## 1. 安装

```bash
pip install -e .
# 或：pip install -r requirements.txt
sudo apt install libcairo2 fonts-noto-cjk fonts-liberation2
```

自检：`PYTHONPATH=. python -m paperfig.cli render examples/shapes/figure.yaml -o /tmp/shapes.png --dpi 120`。

AI 素材 API key（可选）：[grsai.ai](https://grsai.ai/zh) → `export PAPERFIG_API_KEY=sk-xxxx`（兼容旧名 `SCIFIG_API_KEY`）。无 key 时素材渲成虚线占位，不阻塞布局调试。

---

## 2. 五步工作流

一张图 = 一个 YAML `spec`。坐标单位 **mm**，字号 **pt**，原点左上，`rect: [x, y, w, h]`。

### 2.1 最小可运行示例

```yaml
# hello.yaml
figure: {width: 120, height: 40, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: box, id: a, rect: [8, 10, 40, 22], title: Input, body: data,
     variant: primary, accent: left, sketch: waveform, valign: top}
  - {type: box, id: b, rect: [72, 10, 40, 22], title: Output, body: result,
     variant: secondary, sketch: curve, valign: top}
  - {type: arrow, from: a, to: b, label: process, weight: heavy}
```

```bash
python -m paperfig.cli render hello.yaml -o hello.png
python -m paperfig.cli studio hello.yaml    # 拖拽 / 键盘微调
```

### 2.2 从零作图：五步

| 步 | 做什么 | 命令 / 文档 |
| --- | --- | --- |
| **0.5 Figure Brief** | 把粗糙需求扩成结构化图纸说明（图类型、分区、每盒 title/body/sketch、箭头语义、风格、素材清单） | [`prompts/FIGURE_BRIEF.md`](prompts/FIGURE_BRIEF.md) — **本阶段不写 YAML** |
| **1 写 spec** | Brief → YAML；新图默认 `neurips` + 四层分解 | [`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md) |
| **2 占位渲染** | 调布局；缺素材 = 虚线框 | `python -m paperfig.cli render fig.yaml --grid -o draft.png --dpi 180` |
| **3 抽卡**（可选） | 生成 AI 物件；**必须目检** contact sheet 再 `select` | `python -m paperfig.cli assets fig.yaml` |
| **4 定稿** | 高 DPI + SVG；清零 E，尽量清零 `R-*` | `python -m paperfig.cli render fig.yaml -o fig.png --svg fig.svg` |

**何时跳过 Phase 0.5**：用户已给精确改图指令（挪盒子 / 改色 / 加箭头），或已有完整可用 `figure.yaml` 只需微调 → 直接改 YAML。

**核心心法**：布局先于素材定稿；真实实验图（频谱/热图/定量曲线）一律 `placeholder: true`，禁止 AI 生成。

---

## 3. 主题与配色

### 3.1 主题 preset

| preset | 视觉 | 适用 |
| --- | --- | --- |
| **`neurips`**（新图默认） | Soft Pastel 浅填 + Okabe 描边；印刷字号；无阴影；inline 图例 | 主文方法 / 架构图 |
| **`topconf`** | 白填充 + Okabe 彩/灰细边框；投影默认关 | 顶刊克制 / 白底边框风 |
| **`airy`** | pastel 填充 + **默认 soft shadow** + 大圆角 | talk / blog；**勿当论文默认** |
| **`editorial`** | 暖纸画布 `#FAF9F5` + clay accent；细线无阴影 | 博客隐喻 / 解释性附图 |
| **`isosystem`** | 浅晒图 `#F4F7FA` + 钢蓝；可选 `figure.grid_bg: true` | 系统 / 硬件 / 具身架构 |
| `sci` / `warm` / `mono` | 旧稿兼容 | 非新图默认 |

示例：[`examples/themes/editorial_concept.yaml`](examples/themes/editorial_concept.yaml)、[`examples/themes/isosystem_stack.yaml`](examples/themes/isosystem_stack.yaml)。

```yaml
# airy 示例（默认 soft shadow）
figure: {width: 120, height: 48, dpi: 600}
theme: {preset: airy}
elements:
  - {type: box, id: a, rect: [10, 12, 40, 26], title: Actor, body: policy,
     variant: primary, sketch: curve, valign: top}
  - {type: box, id: b, rect: [70, 12, 40, 26], title: Critic, body: "Q(s,a)",
     variant: secondary, sketch: bars, valign: top}
  - {type: arrow, from: a, to: b, label: state, weight: heavy}
```

### 3.2 `palette` 换色方案

任意 preset 可写 `palette` 覆盖语义色（未写的 role 继承预设默认）：

```yaml
figure: {width: 100, height: 36, dpi: 600}
theme:
  preset: topconf
  palette: {primary: "#00897B", secondary: "#FFB300", section_bg: "#ECEFF1"}
elements:
  - {type: box, id: a, rect: [8, 8, 36, 22], title: Primary, body: core,
     variant: primary, accent: left, sketch: layers, valign: top}
  - {type: box, id: b, rect: [56, 8, 36, 22], title: Secondary, body: aux,
     variant: secondary, sketch: bars, valign: top}
  - {type: arrow, from: a, to: b, weight: heavy}
```

| 方案 | YAML `palette` 要点 |
| --- | --- |
| Soft Pastel（默认） | `theme: {preset: neurips}` |
| Okabe 白底彩框 | `theme: {preset: topconf}` |
| Teal + Amber | `{preset: neurips, palette: {primary: "#00897B", secondary: "#FFB300"}}` |
| Navy + Coral | `{preset: topconf, palette: {primary: "#1A3A5C", secondary: "#E05A47", section_bg: "#F9F6EE"}}` |
| Slate + Violet | `{preset: topconf, palette: {primary: "#3F51B5", secondary: "#7E57C2", section_bg: "#EDE7F6"}}` |
| Forest + Gold | `{preset: topconf, palette: {primary: "#2E7D32", secondary: "#C49A00", section_bg: "#F9F6EE"}}` |
| Minimal Grey | `{preset: topconf, palette: {primary: "#263238", secondary: "#546E7A", tertiary: "#0072B2", section_bg: "#ECEFF1"}}` |
| Airy / Editorial / IsoSystem | `theme: {preset: airy\|editorial\|isosystem}` |

### 3.3 variant 语义分配

| variant | 语义 |
| --- | --- |
| `primary` / `secondary` / `tertiary` | 主 / 次 / 辅模块（neurips 为浅填+同色描边） |
| `sky` / `purple` / `vermillion` | neurips 扩展角色色 |
| `trainable` / `frozen` | 可训（暖橙）/ 冻结（冷灰蓝） |
| `ours` / `baseline` | 对比列强调 / 灰色基线 |
| `muted` / `plain` / `section` | 常规结构、共享骨架 |
| `highlight` / `accent` / `dark` | 强调或深底白字 |

同图主色 ≤3 + 灰系。`≥2` 种非 muted 语义色时建议加 `legend`（机检约在 ≥3 色时报 `R-no-legend`）。

### 3.4 配色禁忌（避免「AI 生图感」）

| 禁止 | 正确做法 |
| --- | --- |
| 4–5 种彩色背景面板 | 白/近白 + 极浅 `section_bg` |
| 高饱和彩色 banner | topconf 下 `header_style: smallcaps` |
| 每个模块高饱和填色 | neurips：浅 pastel 填；topconf：白填 + 彩边 |
| 彩虹渐变 / 霓虹 | 扁平纯色；融合例外可用双色 `gradient` |
| 无图例的多语义色 | 加 `legend` |

---

## 4. 信息密度

「只有标题的空心圆角框」会让图显得像朴素 PPT——审稿人一眼判为信息量不足。对策：四层分解 + sketch/legend。

### 4.1 为什么要填满盒子

每个内容模块至少满足其一：`body` 短说明 · `sketch` 缩略图 · `icon` AI/位图 · `valign: top` 容器卡内再嵌子元素。小标签条（面积 ≤300 mm²）可豁免。

### 4.2 sketch：14 种语义 → kind

程序化单色缩略图，可独立 `type: sketch`，也可 `box.sketch: <kind>` 内嵌（配 `valign: top`）。

| 语义 | `kind` |
| --- | --- |
| 时序 / 信号 | `waveform` |
| 频谱 | `spectrum` |
| 注意力 / 空间热力 | `heatmap` |
| 混淆/相关阵 | `matrix` |
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
figure: {width: 160, height: 70, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: panel, id: p, rect: [4, 4, 152, 62], title: Density Demo,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: box, id: enc, rect: [12, 18, 36, 34], title: Encoder,
     variant: primary, accent: left, sketch: heatmap, valign: top, shadow: true}
  - {type: sketch, id: sk1, rect: [56, 22, 32, 22], kind: curve_desc, label: "loss"}
  - {type: box, id: head, rect: [98, 18, 46, 34], title: Head, body: "softmax",
     variant: secondary, sketch: bars, valign: top}
  - {type: arrow, from: enc, to: head, label: "B×D", weight: heavy, via: [[92, 35]]}
  - {type: legend, id: lg, at: [56, 50], columns: 2, items: [
      {swatch: box, color: "#0072B2", label: "core"},
      {swatch: box, color: "#E69F00", label: "head"},
    ]}
```

目标：≥50% 内容模块带 `sketch` 或 `icon`；主数据流箭头有 `label`；有 `panel` 或带 `fill` 的 `group`。

### 4.3 legend：何时必须加

多语义色（encoder 蓝 / aux 橙 / skip 虚线灰……）时用 `type: legend`，不要用手拼短箭头冒充。`swatch` ∈ `box | line | dashed | arrow | dot`。

---

## 5. 元素与字段

按声明顺序绘制；`panel`/`group` 偏底层，`text`/`panel_label` 偏顶层。

### 5.1 `figure`

| 字段 | 说明 |
| --- | --- |
| `width` / `height` | mm；双栏 ~180，单栏 ~85；高度贴内容 |
| `dpi` | 默认输出 DPI（可被 `--dpi` 覆盖） |
| `background` | 默认 `#FFFFFF` |
| `font_scale` | 全局字号缩放 |
| `assets_dir` | AI 素材目录（相对 spec） |

顶层还可写 `assets_style`（英文插画语言，见 §7）。

### 5.2 `box`

关键字段：`id, rect, title, body, variant, shape, icon, icon_h, title_size, body_size, align, valign, gradient, fill, stroke, text_color, stack`。

**新增视觉字段**：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `shadow` | bool / null | soft shadow（null=跟随主题；airy 默认开） |
| `accent` | `left` \| `top` | 色条（variant 边框色），锚点计入外边界 |
| `header_fill` | bool | 标题区浅底 + 分隔线 |
| `sketch` | kind 字符串 | 内嵌缩略图 |

`shape`：`rect|stadium|diamond|cylinder|parallelogram|hexagon|ellipse|trapezoid`。标题/正文支持 `_{...}` / `^{...}`（值须加引号）。

### 5.3 `panel`

分区容器。`header_style: smallcaps`（顶会克制：大写标签 + 灰线）或 `banner`（色条标题）。另有 `header_h` / `header_fill` / `fill` / `shadow`。

### 5.4 `sketch` / `legend`

见 §4。独立 sketch：`kind, color, stroke_color, label, seed`。legend：`at, items, columns, frame, fill, stroke, size`。

### 5.5 `arrow`

| 字段 | 说明 |
| --- | --- |
| `from` / `to` | 裸 id / `id.side` / `id.side@t` / `[x,y]` |
| `route` | `auto|straight|hv|vh|z|zv|arc` |
| `style` | `solid|dashed|dotted|block` |
| `weight` | `thin|normal|heavy`（线宽倍率） |
| `label_bg` | bool，标签胶囊底（默认 true） |
| `via` | 途经点列表（残差/绕线） |
| `width` / `bend` / `fill` / `bidir` / `head` | 线宽覆盖、弧弯曲、block 填充、双向、箭头头 |

### 5.6 `group` / `text` 及其他

- **group**：`members` 或 `rect`；`fill` / `hatch`（斜线底纹）/ `shadow` / `color` / `label_pos`
- **text**：`smallcaps`（大写+字距）、`rotate: -90` 竖排、`italic`/`bold`
- **asset**：`placeholder: true` = 真实实验图槽（缺文件仅 W 级）
- **tokens / marker / network / scatter / badge / panel_label**：见 [AGENTS.md](AGENTS.md) 字段表；`marker.icon` ∈ `fire|snow|lock|check|cross|oplus|otimes|wifi`

### 5.7 综合字段示例（可渲染）

```yaml
figure: {width: 180, height: 90, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: panel, id: stage1, rect: [4, 4, 100, 82], title: Encoder Stage,
     header_style: smallcaps, fill: "#F7F7F7"}
  - {type: box, id: a, rect: [12, 18, 40, 34], title: Input,
     variant: primary, accent: left, header_fill: true,
     sketch: waveform, shadow: true, valign: top}
  - {type: box, id: b, rect: [60, 22, 36, 28], title: Module, body: "FFN",
     variant: secondary, sketch: layers, valign: top}
  - {type: group, id: g, members: [a, b], fill: "#F7F7F7",
     hatch: true, style: dashed, label: Ablation, label_pos: inside-top, pad: 3}
  - {type: arrow, from: a, to: b, style: dotted, weight: heavy,
     label: "R^{B×D}", label_bg: true}
  - {type: text, at: [54, 72], text: Stage Name, smallcaps: true,
     size: 7.5, color: "#0072B2"}
  - {type: sketch, id: sk1, rect: [112, 16, 56, 28], kind: heatmap, label: "Attn"}
  - {type: legend, id: lg, at: [112, 52], items: [
      {swatch: box, color: "#0072B2", label: "encoder"},
      {swatch: line, color: "#E69F00", label: "aux"},
      {swatch: dashed, color: "#4D4D4D", label: "skip"},
      {swatch: arrow, color: "#0072B2", label: "flow"},
      {swatch: dot, color: "#009E73", label: "output"},
    ]}
```

---

## 6. 箭头与路由

### 6.1 锚点

- **裸 id**（推荐）：`from: a, to: b` → 自动选朝向对方的边、落在边中点。
- **精确边**：`a.right` / `b.left@0.3`（`@t` ∈ 0~1）。
- **视觉外边界**：`accent` 色条与 `stack` 叠影计入锚点，避免箭头压色条或悬在叠影缝。

### 6.2 路由与末段垂直进入

| route | 行为 |
| --- | --- |
| `auto` | 按锚点边智能折线；同向多箭头可并总线 |
| `hv` / `vh` / `z` / `zv` | 水平/垂直正交折线 |
| `straight` | 直线（不强制正交） |
| `arc` | 二次贝塞尔；`bend` 控制弯向 |

正交路由下，**出发边 / 到达边各留法向 stub，全程仅水平/垂直段**，末段垂直进入目标边——消除「贴边斜滑」。显式 `via` 的真斜线不会被强行扳直，但可能触发 `arrow-approach` 警告。

```yaml
figure: {width: 100, height: 70, dpi: 600}
theme: {preset: topconf}
elements:
  - {type: box, id: mha, rect: [30, 10, 50, 18], title: MHA, body: attention,
     variant: primary, sketch: heatmap, valign: top}
  - {type: box, id: an1, rect: [30, 42, 50, 18], title: "Add&Norm", body: residual,
     variant: muted, sketch: layers, valign: top}
  - {type: arrow, from: mha, to: an1, weight: heavy, label: main}
  - {type: arrow, from: mha.left, to: an1.left, via: [[12, 19], [12, 51]],
     style: dashed, label: skip, weight: thin}
```

残差/skip 一定用 `via` 绕开中间盒子，否则报 `arrow-through-node`。

---

## 7. AI 素材抽卡

### 7.1 风格包（同一画师）

同图所有 AI 素材共享：**色板 hex**（来自 `theme`/`palette`）+ **插画语言** + 抽象层级 / ~2px 描边 / 三分之四视角硬约束。顶层可覆盖：

```yaml
figure: {width: 80, height: 40, dpi: 600, assets_dir: assets}
theme: {preset: topconf}
assets_style: >
  clean isometric scientific icons, uniform 2px charcoal outline,
  two-tone flat shading, no gradients
elements:
  - {type: box, id: a, rect: [10, 8, 60, 26], title: Microscope slot,
     body: "icon after assets", variant: primary, accent: left,
     sketch: nested, valign: top}
```

### 7.2 Prompt 写法

- **只写「是什么 + 形态」**（「一块 GPU 加速卡，斜俯视角」）。
- **不要写色调/风格词**（蓝灰、扁平、赛博——交给风格包）。
- **禁止文字/公式/数字**；真实实验图用 `placeholder: true`。

```bash
python -m paperfig.cli assets fig.yaml --api-key sk-xxxx
# 必须目检 assets/contact_sheet_{id}.png —— 自动选卡只看抠图洁净度，不保证跨素材视角一致
python -m paperfig.cli select fig.yaml microscope 2    # 换卡，零成本
python -m paperfig.cli assets fig.yaml --only microscope --force   # 改 prompt 后重抽
```

---

## 8. 命令行与 studio

| 命令 | 说明 |
| --- | --- |
| `python -m paperfig.cli render spec [-o png] [--svg svg] [--grid] [--dpi N] [--strict]` | 渲染 + 体检 |
| `python -m paperfig.cli studio spec [--port 8323] [--no-open]` | 交互调图 |
| `python -m paperfig.cli assets spec [--api-key KEY] [--only ids] [--force]` | 抽卡 |
| `python -m paperfig.cli select spec ASSET_ID INDEX` | 换卡 |
| `python -m paperfig.cli cutout in.png out.png [--shadow keep\|remove]` | 抠图 |

### studio

```bash
python -m paperfig.cli studio path/to/figure.yaml
```

| 操作 | 效果 |
| --- | --- |
| 编辑 YAML | 防抖重渲；体检实时刷新 |
| 点击 / 拖拽预览元素 | 定位 YAML；写回 `rect`/`at`（0.5 mm 吸附，Alt=0.1 mm） |
| 方向键 | 微调；Shift=2 mm，Alt=0.1 mm |
| 编辑器 Alt+↑↓ | 光标处数字步进 |
| Ctrl+S / 导出 | 保存；出 PNG+SVG |

改写本质是 YAML 文本，Ctrl+Z 可撤销，与手改 / AI 改完全等价。

---

## 9. 体检码速查

| 码 | 级 | 含义 | 修法 |
| --- | --- | --- | --- |
| `asset-missing` | E | 素材不存在 | 跑 `assets` 或修引用；实验图改 `placeholder: true` |
| `text-overflow` | E | 文字溢出盒子 | 加高 / 缩短文字 / 调小字号 |
| `text-overlap` | E | 文字重叠 | 挪 `at` |
| `out-of-canvas` | E | 超出画布 | 调坐标或加大画布 |
| `arrow-through-node` | E | 箭头穿无关节点 | 换 `route` 或 `via` |
| `asset-placeholder` | W | 实验图待填（正常） | 投稿前放真图 |
| `font-too-small` / `font-small` | W | 字号偏小 | 调大或 `font_scale` |
| `node-overlap` | W | 节点重叠 | 调 `rect` |
| `row/col-misaligned` / `uneven-gap` | W | 近失对齐/等距 | snap 同排 `y+h` 或同列 `x+w` |
| `asset-tiny` | W | 素材显示过小 | 加大槽位 |
| `canvas-sparse` / `canvas-crowded` | W | 留白过多/过挤 | 调画布尺寸 |
| `R-empty-box` | W | 空心大盒子 | 加 `body`/`sketch`/`icon` |
| `R-no-section` | W | 缺分区底色 | 加 `panel`（smallcaps）或 `group`+`fill` |
| `R-no-legend` | W | 多语义色无图例 | 加 `legend`，或次要色改 `muted` |
| `arrow-approach` | W | 末段不垂直进入锚定边 | 改用正交 `route`；斜线 `via`/`straight` 只警告 |
| `arrow-gap` | W | 端点悬空或压入视觉边 | 检查 accent/stack；调锚点 |
| `arrow-label-tip` | W | 标签胶囊盖住尖端 | 挪 label / 改 `label_offset` / 关 `label_bg` |
| `arrow-label-over-text` | W | 标签压到其他文字 | 挪标签或元素 |

`render --strict`：有 E 级返回非零（`asset-placeholder` 与 `R-*` 不触发）。

---

## 10. 实践经验与常见坑

**布局**
- 先 `--grid` 占位定稿，再抽卡。
- 同行同 `y+h`、同列同 `x+w`；体检会抓 1–2 mm 手滑。
- 新图「太素」升级顺序：`topconf` → 补 sketch → panel smallcaps → legend → `shadow`/`accent` → 主箭头 `weight: heavy`。

**字号 / DPI**
- 双栏正文 ≥6.5 pt；迭代 150–220 dpi，投稿 600 dpi + `--svg`。

**YAML**
- inline `{}` 含 `_ ^ { } ? : # [ ,` 的值必须加引号：`title: "E_{s}"`、`title: "loss 收敛?"`。
- 冒号后必须有空格：`{id: a}` 对，`{id:a}` 错。

**学术诚信**
- 实验产物一律 `placeholder: true`；AI 素材只画通用物件（显微镜/服务器/文档）。

**各领域配方**（完整成品见 `examples/`）
- 方法框架：`panel`+`box.sketch`+`legend`；见 `demo_method/`、`paper_style/`。
- 网络结构：纵向 `box` + `via` 残差；见 `transformer/`、`unet_lora/`。
- 论文复现：`examples/rep_*`（五张真实图逆向复刻）。
- 体系结构：`mono` + `cpu_pipeline/` / `gpu_arch/`。
- 流程图：`stadium`→`parallelogram`→`rect`→`diamond`。

评审标准见 [`prompts/visual_rubric.md`](prompts/visual_rubric.md)。

---

## 11. 后期二次修改

版面全是显式坐标，最省力的微调是**让 AI 助手改 spec**。直接说：

- 「把编码器往右挪 8 mm，整行对齐」
- 「加一条残差，绕开右边」
- 「换成 Teal+Amber palette，核心卡开 shadow」
- 「太空了，按升级配方补 sketch 和 legend」

AI 读 [AGENTS.md](AGENTS.md)（字段速查 + 改图配方）。你也可以用 `studio` 手调——三种方式随时混用。

> **人读本文档学会写图；改图时把需求丢给 AI（AGENTS.md）或打开 studio。**
