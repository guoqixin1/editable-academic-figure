# scifig 使用说明

受控科研作图工具：**布局用代码逐像素定死（可控、可复现、可微调），AI 只生成插画物件并抠成透明图填进版面（美观），文字全部走矢量渲染（杜绝 AI 乱码）。**

适合 AI/机器学习、计算机体系结构、分布式系统等领域的方法框架图、架构图、流程图、数据流图，也能拼出论文里常见的多阶段面板图、特征图/token 序列、上下标数学记号、可训练/冻结角标——目标是**给你一张可直接进 Illustrator/Inkscape 的可编辑草稿，把手工调整压到最少**。

> 想让 AI 助手帮你**后期二次修改**这张图（挪位、加连线、换占位实验图等）？另有一份更硬核的 [AGENTS.md](AGENTS.md)（放在仓库根目录，Cursor 等工具会自动读取），里面是给 AI 看的完整字段速查 + 改图配方 + 硬规则。人用本文档、AI 用 AGENTS.md。

---

## 目录

1. [安装](#1-安装)
2. [5 分钟上手](#2-5-分钟上手)
3. [spec 完全参考](#3-spec-完全参考)
4. [命令行参考](#4-命令行参考)
5. [各领域配方](#5-各领域配方)
6. [实践经验建议（重点）](#6-实践经验建议重点)
7. [排错：体检码速查](#7-排错体检码速查)
8. [后期二次修改（交给 AI 助手）](#8-后期二次修改交给-ai-助手)

---

## 1. 安装

```bash
pip install -r requirements.txt
# 系统依赖（多数 Linux 已自带）：
sudo apt install libcairo2 fonts-noto-cjk fonts-liberation2
```

自检：`python -m pytest tests/ -q` 应全部通过（当前 53 passed）。

---

## 2. 5 分钟上手

一张图 = 一个 YAML `spec`。所有坐标单位 **mm**，字号单位 **pt**。

```yaml
# hello.yaml
figure: {width: 120, height: 40, dpi: 600}
theme: sci
elements:
  - {type: box, id: a, rect: [8, 12, 40, 18], title: 输入, body: 数据}
  - {type: box, id: b, rect: [72, 12, 40, 18], title: 输出, body: 结果, variant: accent}
  - {type: arrow, from: a.right, to: b.left, label: 处理}
```

```bash
python -m scifig.cli render hello.yaml -o hello.png
```

完整工作流是 4 步：

```bash
# ① 先占位渲染，专心调布局（--grid 叠 10mm 网格核对坐标；缺失素材渲成虚线框）
python -m scifig.cli render fig.yaml --grid -o draft.png --dpi 180

# ② 抽卡生成 spec 里声明的 AI 素材（每个默认抽 3 张，自动抠图+评分+选优）
python -m scifig.cli assets fig.yaml --api-key sk-xxxx

# ③（可选）看 contact_sheet 换卡，或改 prompt 重抽
python -m scifig.cli select fig.yaml report_doc 2
python -m scifig.cli assets fig.yaml --only report_doc --force

# ④ 正式渲染 + 体检（投稿用 600dpi，同时出可再编辑的 SVG）
python -m scifig.cli render fig.yaml -o fig.png --svg fig.svg
```

> **核心心法**：布局先于素材定稿。素材只是往固定槽位里填图，不该反过来牵着布局走。

---

## 3. spec 完全参考

### 3.1 `figure`（画布）

```yaml
figure:
  width: 180        # mm。双栏图 ~180，单栏图 ~85
  height: 96
  dpi: 600          # 默认输出 DPI，可被 --dpi 覆盖
  background: "#FFFFFF"
  font_scale: 1.0   # 全局字号缩放
  assets_dir: assets  # AI 素材目录（相对 spec 路径）
```

### 3.2 `theme`（主题）

```yaml
theme: sci          # 简写
# 或详细覆盖：
theme:
  preset: sci       # sci | warm | mono
  ink: "#1F2933"    # 可覆盖任意字段：muted / arrow / lw_box / corner_radius ...
```

| preset | 配色 | 适合 |
| --- | --- | --- |
| `sci` | 蓝/绿/橙/红 柔和出版色 | AI/ML、通用方法图 |
| `mono` | 灰阶 | 体系结构图、黑白印刷投稿 |
| `warm` | 橙棕大地色 | 偏工程/系统、需要区分于 sci 时 |

每个 preset 有 6 个 `variant`（配色角色）：`primary` `secondary` `accent` `highlight` `plain` `dark`，用在 box 上区分模块类别。

### 3.3 `assets`（AI 素材请求，可选）

```yaml
assets:
  - id: microscope         # 之后用 icon/src 引用
    prompt: 一台简洁的光学显微镜，蓝灰色调扁平插画，侧面视角
    aspect: "1:1"          # 1:1 | 4:3 | 16:9 ...
    candidates: 3          # 抽几张候选
    shadow: keep           # keep 保留软阴影 | remove 连浅灰阴影一起抠掉
```

工具会自动给 prompt 追加「扁平插画风格、白底、无文字」后缀，**你不用自己写**。

### 3.4 `elements`（版面元素）

按声明顺序绘制；`group` 在最底层，`text`/`panel_label` 在最顶层。

#### box — 带文字的节点（支持 8 种形状）

```yaml
- type: box
  id: enc
  rect: [62, 22, 34, 22]   # [x, y, 宽, 高] mm
  title: 编码器             # 加粗标题
  body: 对齐到共享空间       # 正文，支持 \n 换行，自动按框宽折行
  variant: secondary
  shape: rect              # rect(默认) stadium diamond cylinder parallelogram hexagon ellipse trapezoid
  icon: microscope         # 盒内嵌 AI 素材（画在文字上方）
  icon_h: 12               # 图标高度 mm
  align: center            # center | left
  valign: middle           # middle | top（top=标题贴顶，box 当"容器/子卡"用，内部再放其它元素）
  title_size: 8            # 覆盖默认字号(pt)
  body_size: 6
  gradient: ["#B7CDE8", "#F3C89D"]  # 两色线性渐变填充，覆盖 variant 底色（融合/跨模态模块常用）
  gradient_dir: h          # h 水平 | v 垂直
  fill: "#A8C4E5"          # 直接指定填充色（覆盖 variant；画"渐变色系列"逐个指定）
  stroke: "#5E7FA8"        # 直接指定描边色
  text_color: "#FFFFFF"    # 直接指定文字色（深底白字）
  stack: 2                 # 背后叠影层数（层叠卡片/文档效果，VEH#1/#2、Strategy Center）
```

形状语义（见 `examples/shapes/`）：

| shape | 用途 |
| --- | --- |
| `rect` | 通用模块（默认） |
| `stadium` | 流程图起止端点 |
| `parallelogram` | 流程图输入/输出 |
| `diamond` | 流程图判定分支（**给足尺寸，文字区只占 ~60% 宽高**） |
| `cylinder` | 数据库 / 存储 / HDFS / 显存 |
| `hexagon` | 预处理 / 准备步骤 |
| `ellipse` | 状态节点 / 强调 |
| `trapezoid` | 编码器/解码器/降采样块（上下底不等宽，画特征收缩） |

> **标题/正文支持上下标记号**：`_{...}` 下标、`^{...}` 上标，用于数学记号。如 `title: 编码器 E_{s}`、`body: "z ∈ ℝ^{(B V) H W C}"`、`L_{InfoNCE}`。渲染成真正的下标/上标 `<tspan>`，`text` 元素同样支持。含 `^`/`_`/`{` 的值记得整体加引号（YAML 要求）。

#### asset — 独立素材图 + 图注

```yaml
- type: asset
  id: out
  rect: [160, 30, 16, 36]
  src: report_doc          # 素材 id，或相对 spec 的图片路径
  caption: 诊断报告
  halign: center           # left | center | right（素材在框内对齐）
  valign: middle           # top | middle | bottom
  frame: false             # 是否加细边框
  placeholder: false       # true=真实实验图占位槽（见下）
```

**`placeholder: true` —— 真实实验结果的占位槽**：论文图里频谱、波形、生成结果、注意力热图这类**来自真实实验的图**不能让 AI 编造（学术不端），应先占位、投稿前手动把真实文件放进去。声明 `placeholder: true` 后：

- 找不到文件时渲成**带 `[名字]` 标注的虚线槽**，体检只报 `W` 级 `asset-placeholder`（不阻塞、不算错误）；
- 你把真实图片放到 `src` 指向的路径（相对 `assets_dir`），**下次渲染自动嵌入**，警告消失；
- 对比普通 `asset`：普通 asset 缺文件是 `E` 级 `asset-missing`（当成事故）。

#### arrow — 连线（自动路由 / 折线 / 途经点 / 弧线 / 粗箭头）

```yaml
- type: arrow
  from: enc.right          # 锚点 或 [x, y] 坐标
  to: fusion.left
  route: auto              # auto | straight | hv | vh | z(横向Z) | zv(纵向Z) | arc(弧线)
  style: solid             # solid | dashed | block(空心/实心粗箭头)
  label: 证据
  color: "#B5493A"
  head: arrow              # arrow | none
  bidir: true              # 双向箭头
  via: [[12, 63], [12, 85]]  # 手动途经点：残差/skip/绕线专用
  width: 0.8               # 线宽 mm（粗流程线）；block 样式下是箭杆宽
  bend: 0.25               # arc 弯曲度（弦长比例，负值弯向另一侧）
  fill: "#FFFFFF"          # block 样式的填充色（默认白=空心箭头；填彩色=实心粗箭头）
```

- **锚点**：最简写**裸节点 id**（`from: enc, to: dec`）——渲染时**自动选朝向对方的那条边**，多数情况最整齐、省去手算 side，是消除"箭头没对上"的首选；要精确控制才写 `节点id.side`，side ∈ `left/right/top/bottom/center`，可加 `@t`（0~1）指定边上位置，如 `enc.right@0.3`。group 也可作锚点（框对框连线）。
- **route**：`auto` 会根据两端锚点边智能选折线，且多条同向箭头会自动共用一条总线（fan-out/fan-in 很干净）。`arc` 画二次贝塞尔弧线——无线链路、环形流程、绕大弯的场景。
- **via**：给了途经点就走「起点→途经点→终点」直线连接，用于让残差/跳连**绕开**中间的盒子（否则会触发 `arrow-through-node`）。
- **style: block**：画多边形粗箭头（始终直线）。`fill` 白色=流程图空心箭头；`fill` 同 `color`=彩色实心粗箭头（模型图的强调流向）。

#### panel — 带色条标题的分区面板（Stage 1 / Stage 2 容器）

```yaml
- type: panel
  id: stage1
  rect: [4, 4, 112, 88]    # 面板整体（含标题条）
  title: "Stage 1. 表征预训练"
  variant: primary         # 标题条取 variant 主色，面板体取其浅底
  header_h: 7              # 标题条高度 mm
  header_fill: "#B5493A"   # 可选：直接指定标题条颜色
  fill: "#FBEEEC"          # 可选：直接指定面板底色
```

论文多阶段图的骨架：先摆 `panel` 当分区容器（画在最底层），再把 `box`/`tokens`/`marker` 放进去。见 `examples/paper_style/`。

#### tokens — token 序列 / 特征图条组

```yaml
- type: tokens
  id: zs
  rect: [12, 40, 28, 5]    # 条带整体范围
  n: 7                     # 格子数
  direction: h             # h 横排 | v 竖排
  variant: secondary
  gap: 0.7                 # 格间距 mm
  label: "Z_{s}"           # 序列名，画在左/上侧
  colors: ["#111", "#DDD", "#111"]   # 可选：逐格颜色循环（画 masked token）
  sizes: [40, 30, 22, 14]  # 可选：逐格交叉轴尺寸（画 U-Net 特征金字塔，居中对齐）
```

`colors` 画掩码序列（黑/白格），`sizes` 画尺寸递变的特征图堆叠。注意 `sizes` 是**居中**的交叉轴尺寸，最大值别超出周围留白，否则可能越界。

#### marker — 内置矢量角标

```yaml
- type: marker
  at: [46, 24]             # 图标中心 [x, y]
  icon: fire               # fire(🔥) snow(❄) lock check cross oplus(⊕) otimes(⊗) wifi
  size: 4.5                # mm
  color: "#E1922B"         # 可选，覆盖默认色
```

论文里 🔥可训练 / ❄冻结 的标注就用它，贴在对应模块角上。`oplus`/`otimes` 画特征拼接/逐元素乘算子，`wifi` 标注无线链路。全部是矢量绘制，无位图、无 emoji 字体依赖。

#### network — 迷你神经网络（MLP 示意）

```yaml
- type: network
  id: net1
  rect: [44, 66, 32, 20]   # 网络整体范围
  layers: [3, 4, 3]        # 每层节点数（≥2 层）
  direction: v             # v: 层自上而下 | h: 层自左而右
  color: "#4A6FA0"         # 节点描边/连线色（默认 variant.stroke）
  node_fill: "#FFFFFF"
```

DDPG/Actor-Critic/MLP 头部的"三层小网络"画法。相邻层全连接细线 + 圆形节点，节点半径自动适配。常与 `panel`（标题条）组合成"网络模块卡"。

#### scatter — 聚类散点（嵌入空间/数据聚类示意）

```yaml
- type: scatter
  id: clus
  rect: [79, 15, 48, 54]   # 整体区域
  seed: 11                 # 固定随机种子 → 逐像素可复现
  dot_r: 0.42              # 散点半径 mm
  outline: dashed          # dashed | solid | none（椭圆包络样式）
  clusters:                # 每簇一个椭圆 + 高斯散点
    - {at: [0.38, 0.28], rx: 0.30, ry: 0.20, rot: -14, n: 42, color: "#C4622D"}
    - {at: [0.62, 0.52], rx: 0.32, ry: 0.22, rot: 18,  n: 46, color: "#C05A9E"}
```

`at/rx/ry` 都是相对 `rect` 的 0~1 比例，挪动/缩放整个 scatter 只改 `rect`。散点由 seed 决定，同 spec 渲染结果永远一致。

#### badge — 编号圆点（步骤 ❶❷❸）

```yaml
- type: badge
  at: [51, 108]            # 圆心
  text: "1"
  size: 4.2                # 直径 mm
  color: "#C0392B"         # 圆底色（默认主题主色）
  text_color: "#FFFFFF"
```

流程步骤编号、UAV 1/2/3 这类节点序号。文字大小自动随 `size` 缩放，不参与字号体检。

#### group / text / panel_label

```yaml
- type: group                    # 虚线/实线分组框
  members: [enc, retriever]      # 自动包住这些节点；或用 rect 显式指定
  label: Raft 共识组
  style: dashed                  # dashed | solid
  pad: 2.5
  color: "#C0392B"               # 描边+标签颜色（论文常见的红/蓝/橙彩色虚线分区框）
  label_pos: top                 # top(框外上方) | inside-top | inside-bottom
  label_size: 7
  lw: 0.55                       # 线宽覆盖

- type: text                     # 自由文字（标题、注解、公式）
  at: [90, 4]
  text: 图 1. 方法总览
  size: 8
  bold: true
  italic: true                   # 斜体（流程标注 Obs/Action/Forward 的论文惯例）
  anchor: middle                 # start | middle | end
  max_w: 60                      # 给定则自动换行
  rotate: -90                    # 绕 at 点旋转（度）；-90 = 竖排（自下而上读）

- type: panel_label              # a/b/c 分面板号（自动加粗）
  at: [6, 6]
  text: a
```

---

## 4. 命令行参考

| 命令 | 说明 |
| --- | --- |
| `render spec [-o png] [--svg svg] [--grid] [--dpi N] [--strict]` | 渲染 + 体检。`--grid` 叠网格调坐标，`--strict` 有 E 级问题时返回非零 |
| `studio spec [--port 8323] [--no-open]` | **交互式调图界面**（见下） |
| `assets spec --api-key KEY [--only ids] [--force] [--no-auto-select]` | 抽卡生成素材。`--force` 清旧图重抽 |
| `select spec ASSET_ID INDEX` | 把候选 #INDEX 提升为正式素材（换卡，零 API 成本） |
| `cutout in.png out.png [--threshold 238] [--shadow keep\|remove]` | 单独对一张白底图抠图 |

API key 也可用环境变量 `SCIFIG_API_KEY` 代替 `--api-key`。

### studio 交互式调图界面（微调参数的首选方式）

```bash
python -m scifig.cli studio path/to/figure.yaml    # 自动打开 http://127.0.0.1:8323/
```

零额外依赖（Python 标准库起服务 + 单文件原生 JS 页面，离线可用），只操作启动时指定的这一个 spec。左边 YAML 编辑器、右边实时预览，核心解决两件事：**改完不用手动跑 CLI**、**细微参数好调**。

| 操作 | 效果 |
| --- | --- |
| 编辑 YAML | 自动重渲（防抖 ~0.5s），体检结果实时刷新 |
| 点击预览中的元素 | 高亮 + 定位到对应 YAML 行 + 顶部显示元素信息 |
| **拖拽元素** | 移动并自动写回 `rect`/`at`（0.5mm 吸附；按住 Alt = 0.1mm 精调） |
| 选中后按方向键 | 微调位置：默认 0.5mm，Shift=2mm，Alt=0.1mm（步长也可在检查器下拉里选） |
| 编辑器内 Alt+↑↓ | 光标处的**任意数字**步进 ±0.5（+Shift=±2，+Ctrl=±0.1）——宽度/字号/间距都能这么调 |
| 点击体检条目 | 定位到相关元素 |
| 「+ 插入元素…」 | 在光标处插入该类型的模板行 |
| Ctrl+S / 「保存」 | 写回 spec 文件 |
| 「导出」 | 保存 YAML 并渲出 PNG + SVG（DPI 可选） |
| 网格 / 缩放 / Ctrl+滚轮 | 叠加 10mm 网格；预览缩放 |

所有拖拽/微调本质都是改写 YAML 文本，可以 **Ctrl+Z 撤销**，且和手改、AI 改完全等价——三种修改方式随时混用。

> AI 助手辅助改图（自然语言改 spec）见 [AGENTS.md](AGENTS.md)；studio 面向人工手调，两者互补。

---

## 5. 各领域配方

每类给一个最小骨架，完整成品见 `examples/`。

### AI / 机器学习

- **模块框架图**（多模态/RAG/pipeline）：`box` + `arrow`，AI 素材点缀关键模块。见 `examples/demo_method/`。
- **网络结构图**（Transformer/ResNet）：纵向堆叠 `box`，**残差/skip 用 `arrow.via` 绕到侧边**。见 `examples/transformer/`。
- **训练/RL 闭环**：四个 `box` 摆成环，`arrow` 顺时针连。见 `examples/rl_loop/`。
- **论文级多阶段面板图**：`panel` 分区（Stage 1/2/下游）+ `box`（带 `E_{s}` 上下标）+ `tokens`（`Z_{s}` 序列）+ `marker`（🔥/❄）+ 渐变融合模块 + `placeholder` 实验图槽。见 `examples/paper_style/`。
- **扩散/U-Net + LoRA**：`trapezoid` 画降/升采样块，`tokens.sizes` 画特征金字塔，`fire`/`snow` 标可训练/冻结。见 `examples/unet_lora/`。

### 复现真实论文图（`examples/rep_*`，从 5 张已发表图逆向复刻）

| 示例 | 原图类型 | 用到的关键能力 |
| --- | --- | --- |
| `rep_evdispatch/` | 多阶段优化框架（微电网→聚类→RL→鲸鱼优化） | `group.color` 彩色虚线分区、`scatter` 聚类、`text.rotate` 竖排、AI 素材（大脑/鲸鱼/光伏）、回环 `via` |
| `rep_uavedge/` | 系统总览（无人机-基站-服务器） | `route: arc` 弧线链路、`wifi`/`cross` 标记、`badge` 步骤号、图例框、AI 素材（无人机/基站/机房） |
| `rep_codriving/` | 多列模块框架 | `panel` 彩色列、`valign: top` 容器卡、`stack` 叠影、`italic` 流程标注、双色语义箭头 |
| `rep_d3pgmodel/` | 模型结构图 | `network` 迷你 MLP、`style: block` 粗箭头、`oplus` 拼接符、`box.fill` 逐盒配色、图标阵列 |
| `rep_ensemble/` | 集成训练示意 | `box.fill` 渐变色系列、空心 `block` 箭头、`stack` 叠影文档、AI 线稿素材 |

复刻套路：先把原图拆成「分区框 → 节点 → 连线 → 标注」四层，逐层写 spec；语义图标交给 AI 素材抽卡，几何元素全部用内置元素画。

```yaml
# 残差绕线的关键写法
- {type: arrow, from: mha.left, to: an1.left, via: [[12, 63], [12, 85.5]], style: dashed, label: 残差}
```

### 计算机体系结构

- **流水线**：等宽 `box` 横向排列。见 `examples/cpu_pipeline/`（配 `mono` 主题，黑白投稿友好）。
- **存储层次**：递增宽度的 `box` 叠成金字塔，两侧加坐标箭头标注「快/慢、小/大」。见 `examples/memory_hierarchy/`。
- **芯片/SoC/GPU**：`group` 当芯片边界，内部放 `box`，片外存储用 `cylinder`。见 `examples/gpu_arch/`。

### 分布式系统

- **共识/复制拓扑**（Raft/Paxos）：副本用 `box`，`group` 圈出共识组，存储用 `cylinder`。
- **数据流**（MapReduce/Spark）：`auto` 路由天然形成 fan-out/fan-in 总线。见 `examples/mapreduce/`。

### 流程图

用形状表达语义（见 `examples/flowchart/`）：`stadium` 起止 → `parallelogram` 输入 → `rect` 处理 → `diamond` 判定（分支标 `是/否`）。

---

## 6. 实践经验建议（重点）

以下全部来自真实测试（跨 AI/体系结构/分布式/流程图 共 10+ 张图、4 个 AI 素材的抽卡实测）。

### 关于布局

- **先占位、后填素材**。素材没生成时会渲成虚线占位框，不阻塞——先把坐标、字号、连线调到满意，再抽卡。倒过来做会反复返工。
- **用 `--grid` 对坐标**。10mm 网格叠上去，对齐一目了然；改坐标重渲即可精确微调，这是本工具相对纯 AI 生图的核心价值。
- **同类元素等尺寸、共坐标**。同一行的盒子写相同的 `y` 和 `h`，同一列写相同的 `x` 和 `w`，天然对齐。渲染体检会用 `row/col-misaligned`、`uneven-gap` 替你抓"几乎对齐/等距却差 1–2mm"的手滑，按提示 snap 即可（有意的大错落不报）。
- **画布尺寸要贴合内容**。体检报 `canvas-sparse`（内容覆盖 <45%）就是四周留白太多，把 `width/height` 收紧即可。双栏 180mm、单栏 ~85mm 是期刊常用值。

### 关于 AI 素材抽卡（最需要经验的部分）

- **良率其实很高，但要抽对题材**。实测「扁平插画的单个物件」（显微镜、报告、服务器机架、显卡）**12/12 候选全部干净可用、抠图零瑕疵**。当前生图对这类题材已经很稳。
- **变化的是造型/角度/风格，不是质量**。所以「抽卡」主要是在挑**最贴合语义、和主题最协调**的那张，不是在赌能不能用。看 `contact_sheet_{id}.png` 挑，不满意就 `select` 换，零成本。
- **用 prompt 锁定 3 件事来压低方差**：①色调（跟主题一致，如 sci→蓝灰）②风格（统一写「扁平插画风格」）③视角（「正面/侧面视角」）。锁死这三点，4 张候选会高度一致，挑选更省心。
- **素材只画「单个物件」**，绝不要场景、文字、多个物体。多物体会让抠图连通块 >1、评分被扣；文字则是 AI 乱码重灾区（本来就该用 `text`/`box` 画）。
- **耗时预期**：`nano-banana-fast` 模型、4 并发下，一个素材抽 4 张约 45–50s。图里素材多时一次性 `assets` 批量抽，比逐个抽快。
- **重抽策略**：造型不对先**改 prompt 再 `--force`**（清旧图重新请求），别指望同 prompt 重抽能变好；只是想换一张已抽好的用 `select`。
- **抠图去阴影**：AI 常给物件加淡投影。要干净剪影就 `shadow: remove`；要立体感就 `keep`（默认）。抠完可把素材合成到深色底自查有无白边（`examples/` 里的素材实测边缘白边占比 0%）。

### 关于字号 / DPI / 主题

- **字号下限**：180mm 双栏图里，正文 `body` 6.5pt 是舒适下限，再小体检会警告（缩印后审稿人看不清）。整体嫌小就调 `figure.font_scale`。
- **DPI**：迭代预览用 150–220（每张 <1s，快）；投稿正式出图用 600；`--svg` 出的矢量图可无损进 Illustrator/Inkscape 再修。
- **主题选择**：AI/ML 用 `sci`；体系结构、要黑白印刷用 `mono`（纯灰阶，缩印和黑白打印都清晰）；想和别人的 sci 图区分开用 `warm`。

### 关于形状与连线

- **箭头端点优先写裸 id**：`from: a, to: b` 会自动选朝向对方的那条边、落在边中点，省去手算 `.side`，也不会"箭头没对上"；只有要精确指定某条边/某个边上位置时才写 `a.right@0.3`。
- **判定菱形要给足尺寸**：diamond 的文字区只有外接框的 ~60%，短标题（如「loss 收敛?」）也建议给到 55×22mm 以上，否则文字溢出。
- **残差/跳连一定用 `via`**：纵向堆叠的网络图里，直连侧边会穿过中间的宽盒子（体检报 `arrow-through-node`）。用 `via` 把线引到盒子外侧再回来，一步到位。
- **善用 `auto` 路由的总线效应**：多条箭头指向同一目标时，`auto` 会让它们并到同一条竖/横线上，fan-in/fan-out 自动变整齐，不用手算。

### 关于论文级面板图（panel/tokens/marker/上下标）

- **先铺 panel、再放内容**：`panel` 是最底层容器，先用它把图分成 Stage 1 / Stage 2 / 下游几块，内部元素坐标就在各 panel 范围内摆。panel 之间留 4–6mm 间隙。
- **上下标能极大提升"论文感"**：把 `L_InfoNCE` 写成 `L_{InfoNCE}`、`ℝ^{B×H×W}` 写成 `ℝ^{(B V) H W C}`，比全大写的伪记号专业得多。含 `_`/`^`/`{` 的值必须整体加引号。
- **tokens 别当装饰乱塞**：它表达"离散序列/特征图"这个语义。`colors` 画掩码（黑=masked）、`sizes` 画特征金字塔，其余情况给个 `n` 和 `label` 就够。
- **marker 贴在模块右上角**：🔥/❄ 放在 box 角上（`at` 取 box 右上偏移一点），一眼区分可训练/冻结，比写字省地方。

### 关于复现复杂论文图（来自 5 张真实论文图的复刻实测）

- **拆四层再动手**：分区（group/panel）→ 节点（box/asset/tokens/network/scatter）→ 连线（arrow）→ 标注（text/marker/badge）。按层写 spec，每层渲染验证一次，比一次写完再调快得多。
- **彩色虚线分区框是"论文感"的主力**：`group` + `color` + `lw: 0.5` 就是 IEEE 图里的红/蓝/橙分区。分区标签用 `label_pos: inside-bottom` 放框内居中（如 "A: Data Clustering"）。
- **"重复单元 + 省略号"** 是论文表达 ×K 的惯例：画 2 个单元 + `text: "⋮"`/`"•••"` + 第 K 个，别真画 K 个。
- **渐变色系列**（浅→深表示程度/次序）用 `box.fill` 逐个指定色值，比换 variant 精确。
- **竖排文字**（`rotate: -90`）用在窄条区域的轴标注（Historical Data、Final strategy），原图里很常见。
- **弧线链路 + `wifi`/`cross` marker** 是无线/分布式系统图的标配组合；`bend` 正负控制弯向哪侧。
- **box 当容器**：`valign: top` 让标题贴顶，内部再叠 asset/text/tokens——白色子卡（原图 State-Sharing/Negotiation 卡）就是这么画的。lint 对"完全包含"的嵌套不报重叠。
- **AI 素材抽卡良率实测**（本轮 26 个素材、约 60 张候选）：物件类（沙漏/天平/加油机/无人机/服务器楼）≈100% 可用；**俯视道路/地图类最容易贴边被扣分**——prompt 里补"完整构图，四周留白"，或直接人工 `select` 一张贴边不严重的。生成失败（网络重置）是常态，重跑 `assets` 会自动补漏，已成功的不重复花钱。

### 关于占位实验图（真实结果，反学术不端）

- **实验产物一律 `placeholder: true`**：频谱、波形、生成样本、注意力热图、定量结果曲线——这些是真实实验数据，**绝不能让 AI 生成**，只占位。工具会渲成带 `[名字]` 的虚线槽，体检只给 `W` 级提示。
- **投稿前一步到位**：把真实图片按 `src` 路径（相对 `assets_dir`）放好，重渲即自动嵌入、警告自动消失。整张图其余部分（布局/文字/连线）此前已经调好，实验图只是"填空"。
- **和 AI 素材的分工**：AI 素材（`assets`）= 显微镜/服务器/文档这类**通用插画物件**，可生成；占位槽（`placeholder`）= **你自己的实验结果**，只占位。两者别混。

### 关于 YAML 本身（真实踩坑）

- **inline `{}` 里含特殊字符的值必须加引号**：`title: loss 收敛?` 会直接报 YAML 解析错（`?` `:` `#` `[` `,` 都是 YAML 特殊符）。写成 `title: "loss 收敛?"` 即可。这是最容易踩的坑。
- 冒号后**务必有空格**：`{id: a}` 对，`{id:a}` 错。

### 关于评审闭环

- 每次 `render` 都会自动体检（几何 lint）。习惯是：**先清零 E 级，再消 W 级，最后肉眼目检**（对齐、留白、箭头语义、配色协调、素材融入）。
- 评审标准和 agent 分阶段作图流程分别见 `prompts/visual_rubric.md`、`prompts/AGENT_WORKFLOW.md`。

---

## 7. 排错：体检码速查

| 码 | 级别 | 含义 | 修法 |
| --- | --- | --- | --- |
| `asset-missing` | E | 素材文件不存在，渲成占位框 | 跑 `assets` 生成，或修 `src`/`icon` 引用 |
| `text-overflow` | E | box 内容超出盒子（含 diamond 的窄内区） | 加大 `rect` 高度 / 缩短文字 / 调小 `body_size` |
| `text-overlap` | E | 两段文字物理重叠 | 移动 `at` 或错开元素 |
| `out-of-canvas` | E | 文字或节点超出画布 | 调坐标或加大 `figure.width/height` |
| `arrow-through-node` | E | 箭头穿过无关节点 | 改 `route`，或用 `via` 绕行 |
| `asset-placeholder` | W | 真实实验图占位槽待填（**正常状态**） | 投稿前把真实图放到 `src` 路径即自动嵌入 |
| `font-too-small` | W | 字号 <5.5pt | 调大字号或 `font_scale` |
| `font-small` | W | 字号 5.5–6.0pt，缩印后偏小 | 调大字号或 `font_scale`（可接受则忽略） |
| `node-overlap` | W | 两节点矩形重叠 | 调 `rect` |
| `row-misaligned` / `col-misaligned` | W | 同排/列节点几乎对齐却差 0.5–2mm（多半手滑） | 统一它们的 `y`+`h`（横排）或 `x`+`w`（竖排）；有意的大错落不报 |
| `uneven-gap` | W | 同排/列节点中心间距几乎相等却差一点 | 微调相邻坐标成等距（按中心距算，宽度不一不误报） |
| `asset-tiny` | W | 素材显示 <12mm | 加大槽位或裁掉素材空白 |
| `canvas-sparse` | W | 内容包围盒覆盖画布 <45%，四周留白过多 | 缩小画布尺寸 |
| `canvas-crowded` | W | 节点面积占画布 >82% | 加大画布 |

> `render --strict` 会在存在 E 级问题时返回非零退出码，方便接入脚本/CI。`asset-placeholder` 是 W 级，不影响 `--strict`。

---

## 8. 后期二次修改（交给 AI 助手）

一次生成拿到草稿后，最省力的微调方式是**让 AI 助手改 spec**——因为版面全是显式坐标/文字，AI 能精确地挪、改、加，而不像改一张位图那样无从下手。

用法很简单：在装了本工具的项目里，直接对 AI 说自然语言需求，例如：

- 「把`编码器`那个盒子往右挪 8mm，整行跟着对齐」
- 「在 MHA 和 Add&Norm 之间加一条残差连线，绕开右边」
- 「Stage 2 的配色换成 warm 主题」
- 「频谱那个占位槽我已经把真实图放到 `assets/spec.png` 了，重渲一下」

AI 助手会读仓库根目录的 **[AGENTS.md](AGENTS.md)**（Cursor/Claude 等会自动加载），里面有：完整字段速查表、坐标系与不变量、**十几种常见改图操作的配方**（挪位/对齐/加节点/重路由/换占位/改配色…）、体检码→修法映射、以及"实验图只占位不生成"等硬规则。所以你不用把参数一个个讲给它听。

> 简言之：**人读本文档学会怎么写图；要改图时把需求丢给 AI，它读 AGENTS.md 帮你精确改。**
