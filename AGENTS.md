# AGENTS.md — 用 scifig 帮用户改图 / 作图

本仓库是 **scifig**：代码定布局、AI 只画物件素材、文字全走矢量渲染的受控科研作图工具。
本文件是给 **AI 助手**看的操作手册，重点解决**用户拿到草稿后的二次修改**。人类看 [`USAGE.md`](USAGE.md)。

> 作为主流 coding agent 的 skill 使用时（Claude Skills / Cursor Skills / `npx skills`），发现入口是同目录的 [`SKILL.md`](SKILL.md)（含 YAML frontmatter 和触发描述）。本文件是它指向的详细手册——上下文有余量或用户提到"改图/配色/坐标/字段"等具体动作时，直接来这里查。

你最常见的任务：用户已有 `figure.yaml` + 渲染出的 `figure.png`，用自然语言让你「挪一下」「加条线」「换个色」「把占位实验图换成真图」。
一张图 = 一个 YAML `spec`，所有坐标 **mm**、字号 **pt**、原点在左上、`rect: [x, y, w, h]`。

---

## 0. 铁律（先读，别违反）

1. **确定性优先**：布局是显式坐标，改哪个值就重渲对比。不要凭空大改，做**最小改动**满足需求。
2. **真实实验图绝不生成**：频谱/波形/热图/生成样本/定量曲线等来自真实实验的图，**一律 `asset` + `placeholder: true` 占位**，让用户手动放文件。替用户 AI 生成这些 = 学术不端，禁止。
3. **文字/公式/数字走代码**（`box`/`text`/`tokens`），**AI 素材只画"物件"**（设备/器官/文档/机器人），且 prompt 禁止文字。
4. **改完必须验证**：每次改 `spec` 后跑 `render`、**读输出的体检（lint）**、**读渲染出的 PNG**（多模态目检）。E 级必须清零。
5. **小步快跑**：一次改一处、渲染、看图，再改下一处。迭代预览用低 DPI（150–220，<1s）。

---

## 1. 心智模型

- **坐标系**：mm，左上原点，x 向右、y 向下。`rect: [x, y, w, h]`。`at: [x, y]` 是点（text/marker）。
- **绘制顺序（z 轴，后画的盖前面的）**：`panel` → `group` → `box`/`asset`/`tokens` → `arrow` → `marker` → `text`/`panel_label`。
  所以：分区容器 `panel` 放最前面写；标注 `marker`/`text` 放最后写才在最上层。
- **锚点**：箭头端点最简写 `节点id`（**自动选朝向对方的那条边**，最省心，消除"箭头没对上"）；要精确控制某条边再写 `节点id.side`，side ∈ `left|right|top|bottom|center`，可加 `@t`(0~1) 指定边上位置，如 `enc.right@0.3`；也可直接写坐标 `[x, y]`。
- **对齐靠共享坐标**：同一行的盒子写相同 `y`+`h`，同一列写相同 `x`+`w`，天然对齐。这是改图时保持整齐的关键手法。
- **画布**：双栏 `width≈180`，单栏 `width≈85`（mm）；高度贴合内容（体检 `canvas-sparse` 提示留白过多）。

---

## 2. 字段速查（完整）

### figure / theme / assets

```yaml
figure: {width, height, dpi: 600, background: "#FFFFFF", font_scale: 1.0, assets_dir: assets}
theme: sci            # 简写；或 {preset: sci|warm|mono, ink, muted, arrow, ...任意字段覆盖}
assets:               # 声明要 AI 生成的物件（抽卡对象）
  - {id, prompt, aspect: "1:1", candidates: 3, shadow: keep|remove}
```

- `theme.preset`：`sci`(蓝绿橙红，AI/ML) | `mono`(灰阶，体系结构/黑白投稿) | `warm`(橙棕，工程系统)。
- `variant`（用在 box/panel/tokens 上区分模块）：`primary secondary accent highlight plain dark`。

### elements（`type` 区分）

| type | 关键字段（默认值） | 用途 |
| --- | --- | --- |
| `box` | `id, rect, title, body, variant(primary), shape(rect), icon, icon_h(10), title_size, body_size, align(center), valign(middle), gradient, gradient_dir(h), fill, stroke, text_color, stack(0)` | 带文字的节点 / 容器卡 |
| `asset` | `id, rect, src, caption, halign(center), valign(middle), frame(false), placeholder(false)` | 独立素材图 + 图注 |
| `panel` | `id, rect, title, variant(primary), header_fill, fill, title_size, header_h(7)` | 带色条标题的分区容器 |
| `tokens` | `id, rect, n(8), direction(h), variant(secondary), colors, gap(0.7), sizes, label` | token 序列 / 特征图条组 |
| `marker` | `id, at, icon(fire), size(5), color` | 矢量角标（图标见下） |
| `network` | `id, rect, layers([3,4,3]), variant, node_fill, color, direction(v)` | 迷你 MLP（DDPG/Actor 网络示意） |
| `scatter` | `id, rect, clusters[{at,rx,ry,rot,n,color}], seed(42), dot_r(0.5), outline(dashed)` | 聚类散点（嵌入空间示意，seed 可复现） |
| `badge` | `id, at, text("1"), size(5), color, text_color(#FFF)` | 编号圆点（步骤 ❶❷❸） |
| `arrow` | `from, to, route(auto), style(solid), label, color, head(arrow), bidir(false), label_offset(1.4), via([]), width, fill, bend(0.25)` | 连线 |
| `group` | `id, members[] 或 rect, label, pad(2.5), style(dashed), fill, color, label_pos(top), label_size, lw` | 分组框 / 彩色虚线分区 |
| `text` | `id, at, text, size(7), bold(false), italic(false), color, anchor(middle), max_w, rotate(0)` | 自由文字（可斜体/旋转） |
| `panel_label` | `id, at, text` | a/b/c 面板号（自动加粗） |

- **box.shape**：`rect stadium diamond cylinder parallelogram hexagon ellipse trapezoid`。
- **box.gradient**：`[c1, c2]` 两色线性渐变，覆盖 variant 底色；`gradient_dir: h|v`。
- **box.fill/stroke/text_color**：直接指定颜色（画"渐变色系列"逐盒指定）；**box.stack**：背后叠影层数（层叠卡片）；**box.valign: top**：标题贴顶（box 当容器/子卡，内部再放元素）。
- **marker.icon**：`fire snow lock check cross oplus(⊕拼接) otimes(⊗) wifi(无线)`。
- **arrow.route**：`auto`(按锚点边智能选，多条同向自动并总线) `straight hv vh z zv arc(弧线，配 bend)`。
- **arrow.via**：`[[x,y],...]` 途经点，走「起→途经→终」，用于绕开盒子（残差/skip）。
- **arrow.width**：线宽 mm；**arrow.style: block**：多边形粗箭头（`fill` 白=空心，同 `color`=实心彩色）。
- **tokens.colors**：逐格颜色循环（画掩码：`["#111","#DDD",...]`）。**tokens.sizes**：逐格交叉轴 mm（画特征金字塔，居中）。
- **text.rotate: -90**：竖排文字（窄条轴标注）；**text.italic**：斜体（Obs/Action 流程标注惯例）。
- **group 可作箭头锚点**（框对框连线，如分区→分区的主流程）。
- **文字记号**：`title/body/text/label/caption` 均支持 `_{...}` 下标、`^{...}` 上标（`E_{s}`、`ℝ^{(B V) H W C}`）。

---

## 3. 改图配方（核心）

下面是「用户会怎么说 → 你改哪里」。改完都要重渲 + 看 lint + 看 PNG。

| 用户需求 | 操作 |
| --- | --- |
| **挪某个盒子** | 改该元素 `rect` 的 `x`/`y`。若要整行/整列跟着动，把同 `y`（或同 `x`）的元素一起改，保持对齐。 |
| **改大小** | 改 `rect` 的 `w`/`h`。文字放不下会报 `text-overflow` → 同时加高或调小 `body_size`。 |
| **对齐一排盒子** | 统一它们的 `y` 和 `h`（横排）或 `x` 和 `w`（竖排）；等间距则让相邻 `x` 差值一致。 |
| **加一个节点** | 新增一条 `box`，`id` 唯一，`rect` 放到空位；需要连线再加 `arrow`。 |
| **删节点** | 删该元素，并删掉所有 `from/to/members` 引用它的 `arrow`/`group`（否则报错）。 |
| **加连线** | 最简 `- {type: arrow, from: a, to: b, label: ...}`——裸 id **自动选朝向对方的边**，多数情况最整齐、不会"没对上"；要精确控制某条边再写 `from: a.right, to: b.left`。 |
| **箭头穿过了别的盒子**（`arrow-through-node`） | 换 `route`(hv/vh/z/zv)；仍穿则加 `via` 途经点把线引到盒子外侧再回来。 |
| **加残差 / skip 连接** | `arrow` 用 `via` 走侧边：`from: mha.left, to: an.left, via: [[侧边x, y1], [侧边x, y2]], style: dashed, label: 残差`。 |
| **换形状** | 改 `box.shape`（数据库→`cylinder`、采样块→`trapezoid`、判定→`diamond` 并给足尺寸）。 |
| **融合/跨模态模块要渐变** | 给该 `box` 加 `gradient: [c1, c2]`（两端模态色）。 |
| **换配色 / 主题** | 局部：改元素 `variant`；全局：改 `theme.preset` 或覆盖 `theme.ink/arrow/...`。 |
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

---

## 4. 校验循环

```bash
# 迭代：低 DPI + 网格核对坐标
python -m scifig.cli render {proj}/figure.yaml --grid -o {proj}/draft.png --dpi 180
# 定稿：高 DPI + 出矢量图
python -m scifig.cli render {proj}/figure.yaml -o {proj}/figure.png --svg {proj}/figure.svg --dpi 600
```

每次渲染都会打印体检（E/W 两级）。流程：**清零 E → 尽量清零 W（`asset-placeholder` 除外）→ 读 PNG 目检**（对齐、留白、箭头语义、配色、素材融入、文字可读、上下标是否正确、marker/tokens 是否表意）。

### 体检码 → 修法

| 码 | 级 | 修法 |
| --- | --- | --- |
| `asset-missing` | E | 跑 `assets` 生成，或修 `src`/`icon`；若是实验图应改成 `placeholder: true` |
| `text-overflow` | E | 加高 `rect` / 缩短文字 / 调小 `body_size`（diamond 文字区仅 ~60%） |
| `text-overlap` | E | 挪 `at` 或错开元素 |
| `out-of-canvas` | E | 调坐标或加大 `figure.width/height`（注意 `tokens.sizes` 居中可能越界） |
| `arrow-through-node` | E | 换 `route`，或用 `via` 绕行 |
| `asset-placeholder` | W | **正常**，实验图待用户手动插入；不用管 |
| `font-too-small`/`font-small` | W | 调大字号或 `font_scale` |
| `node-overlap` | W | 调 `rect` |
| `row-misaligned`/`col-misaligned` | W | 同排/列节点**几乎对齐却差 0.5–2mm**（多半是手滑）→ 统一它们的 `y`+`h`（横排）或 `x`+`w`（竖排）snap 齐；明显有意的错落（>2mm）不会报 |
| `uneven-gap` | W | 同排/列节点**中心间距几乎相等却差一点** → 微调相邻坐标成等距（按中心距算，宽度不一也不误报） |
| `asset-tiny` | W | 加大槽位或裁素材空白 |
| `canvas-sparse`/`canvas-crowded` | W | 调 `figure` 尺寸贴合内容 |

`render --strict`：有 E 级时返回非零（`asset-placeholder` 不触发），可接 CI。

---

## 5. 陷阱（真实踩过）

- **YAML 引号**：inline `{}` 里含 `_ ^ { } ? : # [ ,` 的值必须整体加引号。`title: E_{s}` 会失效或报错，写 `title: "E_{s}"`；`title: "loss 收敛?"`。冒号后必须有空格。
- **diamond 给足尺寸**：文字区只有外接框 ~60%，短标题也建议 ≥55×22mm。
- **tokens.sizes 居中越界**：最大值别超出周围留白，否则 token 会伸出画布。
- **删元素要清引用**：删 box 后，引用它的 arrow/group 会报无效引用。
- **via 是折线不是曲线**：途经点之间直线相连，绕盒子要给到盒子外侧的坐标。
- **auto 总线效应**：多箭头指向同一目标会自动并线（fan-in/out 很整齐）；不想并就用 `via` 拆开。
- **cairosvg 无字形回退**：中西文/符号靠 `fonts.py` 分段发排，别手动塞奇怪字体。

---

## 6. 命令速查

| 命令 | 作用 |
| --- | --- |
| `render spec [-o png] [--svg svg] [--grid] [--dpi N] [--strict]` | 渲染 + 体检 |
| `studio spec [--port 8323] [--no-open]` | 用户的交互式调图界面（本地网页：即时重渲、拖拽/键盘微调） |
| `assets spec --api-key KEY [--only ids] [--force] [--no-auto-select]` | 抽卡生成 AI 素材（`--force` 清旧重抽） |
| `select spec ASSET_ID INDEX` | 把候选 #INDEX 提为正式素材（零成本换卡） |
| `cutout in.png out.png [--threshold 238] [--shadow keep\|remove]` | 单张白底图抠图 |

API key 也可用环境变量 `SCIFIG_API_KEY`。

studio 说明：你（AI）改图仍然直接编辑 YAML + `render` 验证；studio 是给**用户**手工微调用的（它对 YAML 的改写与你的编辑完全等价，可能在你两次会话之间发生——重读文件即可）。用户要求"打开调图界面"时，运行 `studio` 命令即可（默认端口 8323，自动开浏览器）。

---

## 7. 更多

- **从零作图的分阶段流程**（需求拆解→占位布局→抽卡→评审→交付）：[`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md)
- **视觉评审 rubric**（机检 + 目检清单）：[`prompts/visual_rubric.md`](prompts/visual_rubric.md)
- **人类使用手册**（教程 + 配方 + 经验）：[`USAGE.md`](USAGE.md)
- **可运行范例**：`examples/`（`paper_style/`、`unet_lora/` 最接近真实论文图，改图时可对照借鉴写法）
