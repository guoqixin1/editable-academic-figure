# scifig · Agent 作图工作流

给 AI agent 的操作指南：当用户要一张科研图（方法框架图、流程图、系统总览、pipeline 图等），按下面的分阶段流程驱动 scifig。核心原则：**布局用代码精确控制，AI 只生成"物件"素材，每步渲染后自检。**

---

## Phase 1：需求拆解

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

产出：`{project}/figure.yaml` 的初稿（可先只写 panel/box/arrow，AI 素材与实验图都用占位）。复现真实论文图的完整案例见 `examples/rep_*`（拆四层：分区→节点→连线→标注）。

## Phase 2：布局草稿（占位渲染）

素材还没生成，先把版面搭出来：

```bash
python -m scifig.cli render {project}/figure.yaml --grid -o {project}/draft.png --dpi 150
```

- `--grid` 叠加 10mm 网格，用坐标核对对齐。
- 缺失素材会渲成**虚线占位框**，不阻塞——这一步专注布局。
- **读渲染出的 PNG**（多模态目检）+ 看体检输出，把 E 级问题清零：溢出、重叠、越界、穿线。
- 反复调 `rect` 坐标 / `route` / 字号，直到布局骨架满意。

> 关键：布局在**没有素材时**就要定稿。素材只是往槽位里填图，不该反过来牵着布局走。

## Phase 3：素材抽卡

```bash
python -m scifig.cli assets {project}/figure.yaml --api-key <KEY>
```

- 每个素材并发抽 `candidates` 张（默认 3），自动抠图 + 客观评分 + 选最高分。
- **prompt 要点**（写在 spec 的 `assets[].prompt` 里）：
  - 描述具体物件与视角（"侧面视角的光学显微镜"），给定色调让它和 `theme` 协调（sci→蓝灰，warm→橙棕）。
  - 统一风格词：如"扁平插画风格"。工具会自动追加白底/无文字后缀，**不要自己写文字**。
  - 避免负面表情的人/类人角色、"wrong/error/danger"等词，规避内容审核。
- 生成后**读 `contact_sheet_{id}.png`**（多模态目检每张候选）：
  - 造型对不对？色调和版面协调吗？抠图干净吗（无残缺、无多余碎块）？
  - 满意 → 用自动选的卡。
  - 想换 → `python -m scifig.cli select {project}/figure.yaml {id} {index}`。
  - 全不行 → 改 prompt 后 `assets --only {id} --force` 重抽（**抽卡失败是常态，重抽/改词是正常操作**）。

抠图边缘可疑时，把素材合成到深色底目检有无白边 halo（见 `visual_rubric.md`）；`shadow: remove` 可去 AI 加的软阴影。

## Phase 4：正式渲染 + 视觉评审闭环

```bash
python -m scifig.cli render {project}/figure.yaml -o {project}/figure.png --svg {project}/figure.svg
```

按 `prompts/visual_rubric.md` 评审：
1. **机检**：`render` 输出的 E 级清零、W 级尽量清零。
2. **目检**：读 PNG，逐项核对对齐、留白、箭头语义、配色一致、素材融入、文字可读、层级、审稿人雷点。
3. **修改循环**（最多 3 轮）：
   - 布局/文字/箭头问题 → 改 `spec.yaml` 重渲。
   - 素材问题 → `select` 换卡 或 `assets --force` 重抽。
4. 仍不达标的遗留项记录下来交用户定夺。

## Phase 5：交付

向用户展示 `figure.png`（预览）+ `figure.svg`（矢量，可进 AI/Inkscape 再修）+ 体检结论。说明：
- 用户可以**改 spec 里任何坐标/文字/颜色**做逐像素微调，重渲即可（这是本工具相对纯 AI 生图的核心价值）。
- 不满意的素材可以换候选卡或重抽。

---

## 避坑

作图/改图的**硬规则、YAML 引号陷阱、体检码 → 修法**统一见 [`../AGENTS.md`](../AGENTS.md)：§0 铁律 · §4 体检码 · §5 陷阱（唯一权威，不在此重复维护）。本从零流程只重申两条最容易翻车、且在抽卡环节尤其相关的红线：

- **真实实验图绝不 AI 生成**（频谱/波形/热图/生成样本/定量曲线）→ 一律 `asset` + `placeholder: true` 占位，投稿前由用户手动放真图。替用户生成这些 = 学术不端。
- **文字/公式/数字走代码**（`box`/`text`/`tokens`），**AI 素材只画物件、prompt 禁止文字**（否则必出乱码）。
