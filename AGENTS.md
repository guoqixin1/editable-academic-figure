# AGENTS.md — 用 paperfig 帮用户改图 / 作图

本仓库是 **paperfig**：代码定布局、AI 只画物件素材（或混合模式下画整图底稿）、文字全走矢量渲染的受控科研作图工具。
本文件是给 **AI 助手**看的操作手册，重点解决**用户拿到草稿后的二次修改**。人类看 [`USAGE.md`](USAGE.md)。

> 作为主流 coding agent 的 skill 使用时（Claude Skills / Cursor Skills / `npx skills`），发现入口是同目录的 [`SKILL.md`](SKILL.md)（含 YAML frontmatter 和触发描述）。本文件是它指向的详细手册——上下文有余量或用户提到"改图/配色/坐标/字段"等具体动作时，直接来这里查。

你最常见的任务：用户已有 `figure.yaml` + 渲染出的 `figure.png`，用自然语言让你「挪一下」「加条线」「换个色」「把占位实验图换成真图」。
一张图 = 一个 YAML `spec`，所有坐标 **mm**、字号 **pt**、原点在左上、`rect: [x, y, w, h]`。
混合模式（`base:`）下另有整图底稿：改文字通常只改 YAML 重渲；换场景观感才重抽底稿。

---

## 0. 铁律（先读，别违反）

1. **确定性优先**：布局最终是显式 mm 坐标（可手改、可复现）。新图优先用 `layout:` 树 + `route: avoid` 起步，再 `resolve` 物化后微调；改哪个值就重渲对比。不要凭空大改，做**最小改动**满足需求。
2. **真实实验图绝不生成**：频谱/波形/热图/生成样本/定量曲线等来自真实实验的图，**一律 `asset` + `placeholder: true` 占位**，让用户手动放文件。替用户 AI 生成这些 = 学术不端，禁止。
3. **文字/公式/数字走代码**（`box`/`text`/`tokens`），**AI 素材只画"物件"**（设备/器官/文档/机器人），且 prompt 禁止文字。
4. **改完必须验证**：每次改 `spec` 后跑 `render`、**读输出的体检（lint）**、**读渲染出的 PNG**（多模态目检）。E 级必须清零。
5. **小步快跑**：一次改一处、渲染、看图，再改下一处。迭代预览用低 DPI（150–220，<1s）。
6. **新图默认顶会风**：从零作图用 `topconf` + 四层分解 + 信息密度 checklist（见 [`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md)）；用户说太素时走 §3「图太朴素」升级配方。

---

## 1. 心智模型

- **坐标系**：mm，左上原点，x 向右、y 向下。`rect: [x, y, w, h]`。`at: [x, y]` 是点（text/marker）。
- **绘制顺序（z 轴，后画的盖前面的）**：`panel` → `group` → `box`/`asset`/`tokens` → `arrow` → `marker` → `text`/`panel_label`。
  所以：分区容器 `panel` 放最前面写；标注 `marker`/`text` 放最后写才在最上层。
- **锚点**：箭头端点最简写 `节点id`（**自动选朝向对方的那条边**，最省心，消除"箭头没对上"）；要精确控制某条边再写 `节点id.side`，side ∈ `left|right|top|bottom|center`，可加 `@t`(0~1) 指定边上位置，如 `enc.right@0.3`；也可直接写坐标 `[x, y]`。
- **对齐靠共享坐标或 layout 树**：手调时同一行写相同 `y`+`h`、同一列写相同 `x`+`w`；新图用 `layout:` 的 `row`/`col`/`grid` 天然对齐。
- **画布**：双栏 `width≈180`，单栏 `width≈85`（mm）；高度贴合内容（体检 `canvas-sparse` 提示留白过多）。`figure.width/height` 可省略，由 layout 内容撑开。

### 结构化布局 `layout:`（新图推荐）

```yaml
figure: {width: 180}          # height 可省略，由内容撑开
layout:
  kind: col                   # row | col | grid
  gap: 4                      # 子项间距 mm（默认 4）
  pad: [2.5, 3]               # 标量 | [y,x] | [t,r,b,l]
  align: stretch              # 交叉轴：start|center|end|stretch
  justify: start              # 主轴：start|center|end|space-between
  children:
    - id: p_online            # 可选：容器即 panel
      type: panel
      kind: row
      gap: 3.5
      pad: [8, 4, 4, 4]        # 顶部留给 smallcaps 标题
      title: "Online Pipeline"
      header_style: smallcaps
      fill: "#F7F7F7"
      children:
        - {ref: q, w: 18, h: 36}
        - kind: col
          gap: 3
          children:
            - {ref: dense, w: 22, h: 16.5}
            - {ref: sparse, w: 22, h: 16.5}
        - {ref: fuse, w: 24, h: 36, flex: 1}   # flex 分剩余主轴空间
elements:
  - {type: box, id: q, title: "User Query", ...}   # 无 rect
  - {type: arrow, from: q, to: dense, route: avoid, label: "…"}
```

- 叶子 `{ref: id, w, h}` 或 `{ref: id, flex, h}`（row 主轴）/`{ref: id, flex, w}`（col 主轴）。
- 容器可纯布局（不渲染），或 `type: panel`/`group` 带标题底色（复用视觉字段）。
- **工作流**：`layout` + `route: avoid` 写结构 → `python -m paperfig.cli resolve in.yaml -o in.resolved.yaml` 物化绝对坐标 → 手调单个 `rect`/`via` → `render`。`render` 也可直接吃带 `layout` 的 spec（内部先 resolve）。
- 已有 `rect` 的元素默认**不覆盖**（尊重手改）；`--force` 才重算。无 `layout` 时 resolve 幂等原样输出。

---

## 2. 字段速查（完整）

### figure / theme / assets / assets_style / base

```yaml
figure: {width, height, dpi: 600, background: "#FFFFFF", font_scale: 1.0, assets_dir: assets}
theme: topconf        # 简写；或 {preset: topconf|airy|sci|warm|mono|neurips|editorial|isosystem|lineart, palette: {...}, ink, ...}
assets_style: "clean isometric scientific icons, uniform 2px charcoal outline"  # 可选，图级素材风格包
assets:               # 声明要 AI 生成的物件（抽卡对象）
  - {id, prompt, aspect: "1:1", candidates: 3, shadow: keep|remove}
base:                 # 可选：AI 整图底稿混合模式
  mode: skeleton      # skeleton | freeform
  prompt: "…"         # 底稿场景描述
  style: sci-flat-pro # 可选；journal-schematic|technical-lineart|sci-flat-pro；缺省按 theme 映射
  accent: [policy]    # 可选；skeleton 关键路径 id（lineart 钢蓝强调；缺省 []=全灰阶）
  image: base/base.png
  candidates: 3
  regions: {enc: [12, 20, 40, 36]}   # freeform；skeleton 通常靠 layout 对齐
```

- **新图默认** `theme.preset: topconf`（白底+色边框）；现代 ML/RL 示意用 `airy`；混合模式搭配 technical-lineart / journal-schematic 底稿用 `lineart`（灰阶细边+钢蓝强调）。旧稿可继续 `sci`/`warm`/`mono`。
- `theme.palette`：8-role 覆盖，如 `{primary: "#00897B", secondary: "#FFB300", section_bg: "#ECEFF1"}`。
- `theme.font_family` / `title_weight` / `body_weight` / `label_weight`：主字体与 SVG 字重（默认 Liberation Sans + 700/400/700；`lineart` 为 Lato + 600/400/500）。`smallcaps_letter_spacing` 控制 panel smallcaps 字距。Lato 经 cairo 面名映射：Regular→`LatoPFRegular`，500→`Lato`(Medium)，600→`Lato Semibold`。
- `variant`（box/panel/tokens）：`primary secondary tertiary accent highlight plain dark muted`。语义：primary=核心贡献，secondary=次要，muted/plain=常规。
- `assets_style`：顶层英文插画语言，抽卡时与 theme 色板一并注入（跨素材风格锁）。
- **`base:`**：有则进入混合模式——`base.image` 全画布打底；文字/箭头仍矢量。`base.style` 三选一（医学/生物管线→`journal-schematic`，系统/RL/架构→`technical-lineart`，通用→`sci-flat-pro`）；缺省按 theme 映射（neurips/topconf/sci→sci-flat-pro，editorial→journal-schematic，isosystem→technical-lineart）。`base.accent: [id…]` 指定 skeleton 关键路径（lineart 下钢蓝 `#3D5A80`，其余模块灰阶；缺省空=全灰阶）。骨架幽灵块配色随 style 分派（lineart 灰阶 / journal 浅中性 L≥0.8 / flat 主题饱和色）。主题可调 `plate_fill` / `plate_opacity` / `plate_pad` / `plate_radius`。**禁止**对底稿做板下漂白等破坏性后处理；对比度不足优先提高 `plate_opacity` / 加大保留带，再不行重抽。

### elements（`type` 区分）

| type | 关键字段（默认值） | 用途 |
| --- | --- | --- |
| `box` | `id, rect, title, body, variant(primary), shape(rect), icon, icon_h(10), title_size, body_size, align(center), valign(middle), gradient, gradient_dir(h), fill, stroke, text_color, stack(0), shadow, accent(left\|top), header_fill(false), sketch, region, ghost, plate` | 带文字的节点 / 容器卡 |
| `asset` | `id, rect, src, caption, halign(center), valign(middle), frame(false), placeholder(false), region, ghost` | 独立素材图 + 图注 |
| `panel` | `id, rect, title, variant(primary), header_fill, fill, title_size, header_h(7), header_style(banner\|smallcaps), shadow, ghost, plate` | 分区容器；顶会风用 smallcaps |
| `sketch` | `id, rect, kind(waveform\|…), color, stroke_color, label, seed` | 单色缩略图（信息密度核心） |
| `legend` | `id, at, items[{swatch,color,label}], columns(1), frame(true), fill, stroke, size(6)` | 自动排版图例 |
| `tokens` | `id, rect, n(8), direction(h), variant(secondary), colors, gap(0.7), sizes, label` | token 序列 / 特征图条组 |
| `marker` | `id, at, icon(fire), size(5), color` | 矢量角标（图标见下） |
| `network` | `id, rect, layers([3,4,3]), variant, node_fill, color, direction(v)` | 迷你 MLP |
| `scatter` | `id, rect, clusters[{at,rx,ry,rot,n,color}], seed(42), dot_r(0.5), outline(dashed)` | 聚类散点 |
| `badge` | `id, at, text("1"), size(5), color, text_color(#FFF)` | 编号圆点 |
| `arrow` | `from, to, route(auto), style(solid\|dashed\|dotted\|block), label, color, head(arrow), bidir(false), label_offset(1.4), label_pos, via([]), width, fill, bend(0.25), weight(normal), label_bg(true)` | 连线 |
| `group` | `id, members[] 或 rect, label, pad(2.5), style(dashed), fill, color, label_pos(top), label_size, lw, hatch(false), shadow` | 分组框 / 分区底 |
| `text` | `id, at, text, size(7), bold(false), italic(false), color, anchor(middle), max_w, rotate(0), smallcaps(false), region, plate` | 自由文字 |
| `panel_label` | `id, at, text` | a/b/c 面板号（自动加粗） |

- **box.shape**：`rect stadium diamond cylinder parallelogram hexagon ellipse trapezoid`。
- **box.sketch**：内嵌缩略图 kind（`waveform bars heatmap scatter curve curve_desc grid matrix tree distribution spectrum layers nested dots_flow`）。
- **box.accent**：`left`/`top` 色条；**box.header_fill**：标题区浅底；**box.shadow**：soft shadow（`null` 跟随 theme）。
- **box.gradient**：`[c1, c2]`；**box.stack**：叠影片数；**box.valign: top**：容器卡标题贴顶。
- **panel.header_style**：`banner`（色条）\| `smallcaps`（顶会克制：大写标签+灰线）。
- **legend.swatch**：`box | line | dashed | arrow | dot`。
- **marker.icon**：`fire snow lock check cross oplus otimes wifi`。
- **arrow.route**：`auto straight hv vh z zv arc avoid`；**优先 `route: avoid`**（走廊网格 A* 正交避障，忽略手写 `via`；失败回退 `auto` 并报 `route-avoid-fallback`）。仅当路径不满意时再用 `via` 微调。
- **arrow.label_pos**：`auto` = 碰撞打分落标；`route: avoid` 时默认开启。显式 `label_offset` 或非 avoid 箭头保持旧落标（兼容旧图逐像素不变）。**auto 落标**会硬拒端点盒 inner（边框带≈1mm 可用）与 box 内 `sketch`/`accent`；显式 `label_offset` 仍按用户坐标渲染，但 lint 全套碰撞检查不豁免。
- **arrow.weight**：`thin|normal|heavy`；**arrow.style** 含 `dotted`；**arrow.label_bg**：标签胶囊底。
- **group.fill / hatch / shadow**：分区浅底、斜线底纹、投影。
- **text.smallcaps**：大写+字距；**text.rotate: -90**：竖排。
- **文字记号**：`_{...}` / `^{...}`（值须加引号）。
- **`region`**：锚定 `base.regions[id]`，代替手写 `rect`/`at`（需有 `base:`）。
- **`ghost`**：`box`/`asset`/`panel`；base 下默认幽灵（不画壳）；`ghost: false` 恢复实体。
- **`plate`**：base 下文字默认半透明白底板；落在干净浅色净空（mean≥220 且 std≤10 且 edge≤5）时**自动免贴片**、裸文字直接落底图；`plate: true` 强制保留，`plate: false` 强制关闭。

---

## 3. 改图配方（核心）

下面是「用户会怎么说 → 你改哪里」。改完都要重渲 + 看 lint + 看 PNG。

| 用户需求 | 操作 |
| --- | --- |
| **挪某个盒子** | 改该元素 `rect` 的 `x`/`y`。若要整行/整列跟着动，把同 `y`（或同 `x`）的元素一起改，保持对齐。 |
| **改大小** | 改 `rect` 的 `w`/`h`。文字放不下会报 `text-overflow` → 同时加高或调小 `body_size`。 |
| **对齐一排盒子** | 有 `layout:` → 放进同一个 `kind: row`（或 col）；已物化 → 统一 `y`/`h` 或 `x`/`w`，等间距对齐相邻差值。 |
| **加一个节点** | 结构化：在 `layout` 树加 `{ref: id, w, h}` + `elements` 加视觉字段；已物化：新增 `box`+`rect`，再加 `arrow`。 |
| **删节点** | 删该元素，并删掉所有 `from/to/members` 引用它的 `arrow`/`group`（否则报错）。 |
| **加连线** | 最简 `- {type: arrow, from: a, to: b, label: ...}`——裸 id **自动选朝向对方的边**，多数情况最整齐、不会"没对上"；要精确控制某条边再写 `from: a.right, to: b.left`。 |
| **箭头穿过了别的盒子**（`arrow-through-node`） | 优先改 `route: avoid`；仍不满意再手写 `via` 把线引到盒子外侧。 |
| **加残差 / skip 连接** | 先试 `route: avoid` + `style: dashed`；自动路径不满意再用 `via`：`from: mha.left, to: an.left, via: [[侧边x, y1], [侧边x, y2]], style: dashed, label: 残差`。 |
| **换形状** | 改 `box.shape`（数据库→`cylinder`、采样块→`trapezoid`、判定→`diamond` 并给足尺寸）。 |
| **融合/跨模态模块要渐变** | 给该 `box` 加 `gradient: [c1, c2]`（两端模态色）。 |
| **换配色 / 主题** | 局部：改元素 `variant`；全局：改 `theme.preset` 或 `theme.palette`（如 Teal+Amber）。 |
| **图太朴素 / 太素 / 像 PPT** | **升级配方（按序做，每步重渲）**：① `theme: {preset: topconf}`（或 airy）；② 每个空心 box 补 `body`/`sketch`/`icon`；③ 加 `panel`（`header_style: smallcaps` + 浅 `fill`）或 `group`+`fill`；④ 设计建议 ≥2 语义色加 `legend`（机检 ≥3 色才报）；⑤ 核心卡 `shadow: true` + `accent: left`；⑥ 主箭头 `weight: heavy` 并补 `label`。详见 [`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md)。 |
| **加单色缩略图** | box 内：`sketch: heatmap`（或 waveform/curve/…）+ `valign: top`；独立：`type: sketch`。 |
| **加图例** | `- {type: legend, id: lg, at: [x,y], items: [{swatch: box, color: "#0072B2", label: "encoder"}, …]}`。 |
| **字太小 / 太大** | 改该元素 `title_size/body_size/size`；整体缩放改 `figure.font_scale`。 |
| **加 🔥可训练 / ❄冻结 标注** | `- {type: marker, at: [盒子右上x, y], icon: fire\|snow, size: 4.5}`。 |
| **画 token / query 序列** | `- {type: tokens, id, rect, n: 7, label: "Z_{s}"}`；掩码加 `colors`，特征金字塔加 `sizes`。 |
| **分区（Stage 1/2/下游）** | 加 `panel` 当容器（写在最前/最底层），子元素坐标落在其 `rect` 内，panel 间留 4–6mm。 |
| **模块名要下标上标** | 文字里写 `E_{s}` / `L_{InfoNCE}` / `ℝ^{...}`，**整个值加引号**。 |
| **占位实验图换成真图** | 用户把文件放到 `assets_dir/<src>` 后，直接重渲即自动嵌入；`placeholder` 可留着（有文件时照常显示）。 |
| **新增一个实验图槽** | `- {type: asset, id, rect, src: assets/xxx.png, placeholder: true, caption: ...}`，**不要 AI 生成内容**。 |
| **留白太多 / 太挤**（`canvas-sparse`/`crowded`） | 调 `figure.width/height` 贴合内容，或整体缩放元素坐标。 |
| **加彩色虚线分区（A/B/C 区）** | `group` + `color: "#C0392B"` + `lw: 0.5`，标签 `label_pos: inside-bottom` 居中放框内。 |
| **画数据聚类 / 嵌入空间示意** | `scatter` + 多个 `clusters`（`at/rx/ry` 是相对 rect 的 0~1 比例），`seed` 固定即可复现。 |
| **画迷你神经网络（DDPG/MLP）** | `network` + `layers: [3,4,3]`；要标题条就叠一个 `panel` 在底下。 |
| **加步骤编号 ❶❷❸** | `badge`（`at` 圆心、`text` 数字、`color` 底色），贴在步骤卡左上角。 |
| **无线链路 / 环形流向要弧线** | `route: arc` + `bend`（正负控制弯向），配 `marker: wifi` / `cross`。 |
| **流程图要空心/彩色粗箭头** | `style: block` + `width`（箭杆宽 mm）；`fill` 白=空心，`fill` 同 `color`=实心彩色。 |
| **竖排文字（窄条轴标注）** | `text` + `rotate: -90`（绕 `at` 点转）。 |
| **表达"×K 个重复单元"** | 画 2 个单元 + `text: "⋮"` 或 `"•••"` + 第 K 个，勿真画 K 个。 |
| **渐变色系列（浅→深）** | 逐盒 `fill` 指定色值（如 `#D6E4F5 → #3E6595`），深底配 `text_color: "#FFFFFF"`。 |
| **白色子卡（模块内的次级卡片）** | `box` + `variant: plain` + `valign: top`，内部再放 asset/text；完全包含的嵌套不会报 node-overlap。 |
| **换底稿重抽但文字不动** | 只改 `base.prompt`（或换 `--model`）→ `paperfig base gen … --force` → 目检 contact sheet → `base pick`；**不要动**文字/箭头 YAML，直接 `render`。 |
| **底稿文字压花纹 / `base-text-contrast`** | ① 挪 `at`/`rect`/`label_offset` 到浅色净空；② 确认未关 `plate`（或主题调高 `plate_opacity`）；③ **禁止**板下漂白等破坏性后处理；④ 仍差则改 prompt 要求 pastel 浅填后 `--force` 重抽。 |

---

## 4. 校验循环

```bash
# 迭代：低 DPI + 网格核对坐标
python -m paperfig.cli render {proj}/figure.yaml --grid -o {proj}/draft.png --dpi 180
# 定稿：高 DPI + 出矢量图
python -m paperfig.cli render {proj}/figure.yaml -o {proj}/figure.png --svg {proj}/figure.svg --dpi 600
```

每次渲染都会打印体检（E/W 两级）。流程：**清零 E → 尽量清零 W（`asset-placeholder` 除外）→ 读 PNG 目检**（对齐、留白、箭头语义、配色、素材融入、文字可读、上下标是否正确、marker/tokens 是否表意）。

### 体检码 → 修法

| 码 | 级 | 修法 |
| --- | --- | --- |
| `asset-missing` | E | 跑 `assets` 生成，或修 `src`/`icon`；若是实验图应改成 `placeholder: true` |
| `text-overflow` | E | 加高 `rect` / 缩短文字 / 调小 `body_size`（diamond 文字区仅 ~60%） |
| `text-overlap` | E | 挪 `at` 或错开元素 |
| `out-of-canvas` | E | 调坐标或加大 `figure.width/height`（注意 `tokens.sizes` 居中可能越界） |
| `arrow-through-node` | E | 优先 `route: avoid`；仍穿再用 `via`。障碍含 box/asset/独立 sketch/legend；端点仅豁免法向 stub，回穿 inner 仍报 |
| `arrow-label-over-sketch` | E | 标签胶囊压到 `sketch`/`accent`（交>0.8mm²）→ 挪 `label_offset` / 开 `label_pos: auto` / 加大线缝 |
| `arrow-label-in-node` | E | 标签深入节点 inner（边框带≈1mm 外）→ 同上；**显式 `label_offset` 不豁免** |
| `route-avoid-fallback` | W | A* 无解已回退 `auto`；检查障碍/间隙或改手写 `via` |
| `arrow-exit-over-content` | W | 出口贴边但落在本盒 sketch 带且法向净空<2.5mm → 改锚点 `@t` 或换边 |
| `arrow-route-awkward` | W | `route: avoid` 绕行比>1.3 且长段穿空场 → 换锚点边或直连 |
| `asset-placeholder` | W | **正常**，实验图待用户手动插入；不用管 |
| `font-too-small`/`font-small` | W | 调大字号或 `font_scale` |
| `node-overlap` | W | 调 `rect` |
| `row-misaligned`/`col-misaligned` | W | 同排/列节点**几乎对齐却差 0.5–2mm**（多半是手滑）→ 统一它们的 `y`+`h`（横排）或 `x`+`w`（竖排）snap 齐；明显有意的错落（>2mm）不会报 |
| `uneven-gap` | W | 同排/列节点**中心间距几乎相等却差一点** → 微调相邻坐标成等距（按中心距算，宽度不一也不误报） |
| `asset-tiny` | W | 加大槽位或裁素材空白 |
| `canvas-sparse`/`canvas-crowded` | W | 调 `figure` 尺寸贴合内容（覆盖率按叶元素，不计 panel/背景 group） |
| `canvas-edge-gap` | W | 叶内容联合包围盒到某边空隙 >8mm **或** >该边长×8%（实现阈值 `min(8mm, 8%×边长)`）→ 收画布或外推内容 |
| `region-empty`/`layout-imbalance` | W | 叶元素九宫格空洞（占用<0.05 且邻格>0.3）或极差>0.35 → 填内容/收画布/分散布局；宽<120mm 放宽 |
| `R-empty-box` | W | 给 box 加 `body`/`sketch`/`icon`，或把子元素放进容器卡（**base 模式停用**） |
| `R-no-section` | W | 加 `panel`（smallcaps）或带 `fill` 的 `group`（**base 停用**） |
| `R-no-legend` | W | 加 `legend`，或把次要色改回 `muted`/`plain`（**base 停用**） |
| `base-text-contrast` | E | **无贴片**文字相对有效背景对比 <3.0，或压在繁忙花纹上 → 挪字 / 开 plate / 重抽浅色底稿 |
| `glyph-missing` | E | 文本含默认拉丁字体缺字形（Liberation 黑名单：‖ / 组合抑扬符 / Ẑẑ 等；Lato 对部分字有覆盖但 ∥ 仍靠 DejaVu）→ 换建议字符，否则出豆腐块 |
| `base-region-drift` | W | skeleton：骨架色块与底稿墨迹质心偏移过大 → 重抽或改 prompt 强调对齐 |
| `plate-overlap` | W | 文字底板互叠 >30% → 错开文字或关次要 `plate` |
| `plate-over-art` | W | **有贴片**压住底稿插画（edge≥12 或 luma_std≥28）→ 挪到净空、关 plate、或重抽保留带 |

`render --strict`：有 E 级时返回非零（`asset-placeholder` 与 `R-*` 不触发），可接 CI。
base 模式另停用 `arrow-exit-over-content` 与 sketch 碰撞类检查（幽灵盒无 sketch）。

---

## 5. 陷阱（真实踩过）

- **YAML 引号**：inline `{}` 里含 `_ ^ { } ? : # [ ,` 的值必须整体加引号。`title: E_{s}` 会失效或报错，写 `title: "E_{s}"`；`title: "loss 收敛?"`。冒号后必须有空格。
- **diamond 给足尺寸**：文字区只有外接框 ~60%，短标题也建议 ≥55×22mm。
- **tokens.sizes 居中越界**：最大值别超出周围留白，否则 token 会伸出画布。
- **删元素要清引用**：删 box 后，引用它的 arrow/group 会报无效引用。
- **via 是折线不是曲线**：途经点之间直线相连；新图优先 `route: avoid`，via 只作微调。
- **auto 总线效应**：多箭头指向同一目标会自动并线（fan-in/out 很整齐）；`route: avoid` 会对共享走廊 nudge 错开；仍重叠再用 `via` 拆开。
- **cairosvg 无字形回退**：中西文/符号靠 `fonts.py` 分段发排，别手动塞奇怪字体。

---

## 6. 命令速查

| 命令 | 作用 |
| --- | --- |
| `render spec [-o png] [--svg svg] [--grid] [--dpi N] [--strict]` | 渲染 + 体检（含 `layout:` 的 spec 会先内部 resolve） |
| `resolve spec [-o out.yaml] [--force]` | 结构化 layout → 纯绝对坐标 YAML（无 layout 则原样写出） |
| `studio spec [--port 8323] [--no-open]` | 用户的交互式调图界面（本地网页：即时重渲、拖拽/键盘微调） |
| `assets spec --api-key KEY [--only ids] [--force] [--no-auto-select]` | 抽卡生成 AI 素材（`--force` 清旧重抽） |
| `select spec ASSET_ID INDEX` | 把候选 #INDEX 提为正式素材（零成本换卡） |
| `base gen spec [-k KEY] [--model …] [--candidates N] [--force]` | 底稿抽卡（skeleton 先渲骨架作参考） |
| `base pick spec INDEX` | 候选提升为 `base/base.png`（回写 `base.image`） |
| `base grid spec` | 底稿叠 mm 网格 → `base/base_grid.png`（freeform 标区） |
| `tiles spec [-o dir] [--grid 2x2\|3x3] [--dpi 300]` | 成图网格切片放大（循环目检；单片宽≥1200px + overview） |
| `cutout in.png out.png [--threshold 238] [--shadow auto\|keep\|remove]` | 单张白底图抠图（默认 auto：检测到问题自动补救） |

`cutout`/`assets` 抠图自带防护（`CutoutReport.fixes` 留痕，gacha 评分自动扣分）：

| 防护 | 检测 | 自动补救 |
| --- | --- | --- |
| 软阴影残边 | alpha 边界中「平坦梯度×高亮度」占比 `fringe_ratio` > 6% | 并入低饱和浅灰重抠（`shadow-removed`） |
| 洪泛泄漏（白物件描边缺口被灌入） | 收紧洪泛 vs 普通洪泛差集出现大口袋 `leak_ratio` | 口袋回填为前景（`leak-sealed`） |
| 底色不纯（偏白/渐变底） | 边框带亮度 `bg_p5` < 245 | 自适应下调阈值（`adaptive-threshold`）；p5<225 直接拒绝 |
| 薄线损失（腐蚀吃掉 hairline） | `thin_loss` > 5% | 无腐蚀窄羽化回退（`thin-preserved`） |
| 阴影碎屑误判多物件 | 浅灰低饱和、边界平坦的小连通块 | 自动剔除（`debris-dropped`），`n_solid` 只计实体 |

API key 也可用环境变量 `PAPERFIG_API_KEY`（兼容旧名 `SCIFIG_API_KEY`）。`base gen --model`：`nano-banana-fast`（默认）/ `nano-banana-2` / `nano-banana-pro`。

studio 说明：你（AI）改图仍然直接编辑 YAML + `render` 验证；studio 是给**用户**手工微调用的（它对 YAML 的改写与你的编辑完全等价，可能在你两次会话之间发生——重读文件即可）。用户要求"打开调图界面"时，运行 `studio` 命令即可（默认端口 8323，自动开浏览器）。

---

## 7. 更多

- **从零作图的分阶段流程**（Brief 优化→需求拆解→占位布局→抽卡→评审→交付）：[`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md)
- **需求 → Figure Brief 优化模板**（Phase 0.5，从零作图/复现论文图必做）：[`prompts/FIGURE_BRIEF.md`](prompts/FIGURE_BRIEF.md)
- **视觉评审 rubric**（机检 + 目检清单）：[`prompts/visual_rubric.md`](prompts/visual_rubric.md)
- **人类使用手册**（教程 + 配方 + 经验）：[`USAGE.md`](USAGE.md)
- **可运行范例**：`examples/`（`paper_style/`、`unet_lora/` 最接近真实论文图，改图时可对照借鉴写法）
