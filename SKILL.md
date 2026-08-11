---
name: paperfig
description: >-
  Create or modify editable academic figures / paper figures with controllable
  layout (architecture diagrams, method frameworks, flowcharts, pipelines,
  feature pyramids, mini network / embedding diagrams) using the paperfig
  toolkit. Layout is an explicit YAML spec in mm coordinates, rendered to
  reproducible editable SVG+PNG; AI generates only object assets (device /
  organ / document icons) or, in hybrid base mode, a full-bleed illustrated
  figure base while text/arrows stay vector-editable; all text, formulas, and
  numbers use vector rendering; every render emits a geometry lint report.
  From-scratch figures first expand the rough request into a Figure Brief via
  prompts/FIGURE_BRIEF.md, then write the YAML. Use when the user asks to draw
  or edit a scientific / academic figure, reproduce a paper figure, replace a
  placeholder experiment image, tweak figure.yaml, or mentions "paperfig",
  "figure.yaml", "学术图", "学术作图", "论文配图", "论文架构图", "可编辑",
  "可复现", "顶会风格图", "科研作图", "复现论文图", "hybrid", "AI base",
  "illustrated figure", "混合模式", or "底稿". Never generate real experimental
  plots (spectra, waveforms, heatmaps, quantitative curves) — those must always
  use placeholder assets that the user fills in.
---

# paperfig — Editable, controllable academic paper figures

一张图 = 一份 YAML `spec`。坐标 **mm**，字号 **pt**，左上原点，`rect: [x, y, w, h]`。
改数值就是改图，可精确复现。

## 硬规则（先读，不要违反）

1. **真实实验图绝不 AI 生成**：频谱 / 波形 / 热图 / 曲线 / 采样样本 → `asset` + `placeholder: true`，由用户手动放文件。
2. **文字 / 公式 / 数字走代码**（`box` / `text` / `tokens`），AI 只画"物件"或整图底稿插画；生图 prompt **禁止**包含文字。底稿另禁止画箭头（矢量层负责）。
3. **最小改动，每次改完必须验证**：改一处 → 渲染 → 读 lint 输出 → 目检 PNG → 再改下一处。E 级 lint 必须清零。
4. **默认顶会观感**：新图默认 `theme: {preset: topconf}`，按四层分解法（全局→分区→标注→风格）写 spec，遵守信息密度 checklist（无空盒、≥50% 模块含 sketch/icon、主箭头有标签、有分区底色、多语义色必有 legend）。详见 [`prompts/AGENT_WORKFLOW.md`](prompts/AGENT_WORKFLOW.md)。
5. **从零作图先优化需求**：尚无精确 spec 时，先用 [`prompts/FIGURE_BRIEF.md`](prompts/FIGURE_BRIEF.md) 把粗糙需求 / 论文片段扩写成 Figure Brief，再写 YAML（工作流 Phase 0.5 → Phase 1）。用户已给精确改图指令时可跳过。
6. **混合模式何时用**：形象化场景 / 英雄图 / 演示材料 → `base:`（skeleton 优先）；严肃排版投稿主文图 → 纯矢量。base 抽卡后**必须**目检 contact sheet（查烤字/**烤箭头/烤连接线**/漂移；核对骨架浅灰保留区是否被插画侵占）。
7. **交付前必须切片循环复核**：`render`（≥300dpi）→ `paperfig tiles` → **逐片目检**按 checklist 记缺陷 → 修 spec/重抽 → 重渲复检 → **循环直到连续一整轮零新发现**（至少两轮）。未完成不得交付。

## 前置条件

先确认 CLI 可用（`python -m paperfig.cli --help` 能跑）。若尚未安装：

```bash
# 在本仓库内
pip install -e .
# 或仅装依赖：pip install -r requirements.txt

# 或在其他项目里使用 paperfig
pip install -e /path/to/editable-academic-figure
```

系统依赖：`libcairo2` + `fonts-noto-cjk`（缺失时 `sudo apt install libcairo2 fonts-noto-cjk fonts-liberation2`）。

## 核心命令

```bash
# 渲染 + 体检；调试用低 DPI 快渲，定稿用 600 出矢量
python -m paperfig.cli render  spec.yaml [--grid] [--dpi 180|600] [-o png] [--svg svg] [--strict]

# 交互式微调界面（给用户手工调，不是给 AI 用）
python -m paperfig.cli studio  spec.yaml [--port 8323]

# 抽卡生成 AI 物件素材（每素材默认抽 3 张候选、自动抠图评分）
python -m paperfig.cli assets  spec.yaml --api-key KEY [--only ids] [--force]

# 换卡（把候选 #INDEX 提为正式素材，零成本）
python -m paperfig.cli select  spec.yaml ASSET_ID INDEX

# 混合模式：整图底稿抽卡 / 选卡 / mm 网格（freeform 标区）
python -m paperfig.cli base gen   spec.yaml [-k KEY] [--model nano-banana-fast|nano-banana-2|nano-banana-pro] [--force]
python -m paperfig.cli base pick  spec.yaml INDEX
python -m paperfig.cli base grid  spec.yaml

# 成图切片放大（循环目检用；默认宽≤180→2x2，更宽→3x3；单片宽≥1200px）
python -m paperfig.cli tiles  spec.yaml [-o outdir] [--grid 2x2|3x3] [--dpi 300]
```

`PAPERFIG_API_KEY` 环境变量可替代 `--api-key` / `-k`（仍兼容旧名 `SCIFIG_API_KEY`）。

## 迭代循环

```
改 spec → render --dpi 180 → 读 lint (E/W) → tiles 逐片目检 → 再改
定稿前：render ≥300dpi → tiles → 连续一整轮零新发现（至少两轮）
```

体检码到修法的映射、arrow.route 全集、改图配方（含换底稿/文字压花纹）、陷阱——**都在 [AGENTS.md](AGENTS.md)**。混合模式分支流程见 [prompts/AGENT_WORKFLOW.md](prompts/AGENT_WORKFLOW.md)。

## 三种典型任务

- **二次改图**（最常见）：用户给 `figure.yaml` + `figure.png`，说"挪一下 / 加连线 / 换色 / 换占位图"。查 AGENTS.md §3 配方表定位改哪个字段。base 图改文案直接重渲；换场景观感才 `base gen --force`。
- **从零作图**：走 [prompts/AGENT_WORKFLOW.md](prompts/AGENT_WORKFLOW.md) 的分阶段流程——先用 [prompts/FIGURE_BRIEF.md](prompts/FIGURE_BRIEF.md) 生成 Figure Brief（Phase 0.5），再需求拆解写 spec → 占位布局 → 抽卡（物件或底稿）→ 评审 → 交付。
- **复现论文图**：同样先走 FIGURE_BRIEF 需求优化；YAML 写法参照 `examples/rep_*/`（`rep_evdispatch`, `rep_uavedge`, `rep_codriving`, `rep_d3pgmodel`, `rep_ensemble`）。
- **混合底稿图**：形象化 / illustrated figure 需求 → Brief 填「底稿场景描述」→ `base gen` → 目检 contact sheet（烤字+烤箭头）→ `pick` → `render`/`lint` → **tiles 切片循环复核**。

## 视觉评审

机检（lint）过关后按 [prompts/visual_rubric.md](prompts/visual_rubric.md) 做目检：对齐、留白、箭头语义、**视觉丰度**（密度/层次/图例/素材统一）、配色、上下标、marker/tokens。用户说「太素」时走 AGENTS.md 升级配方。base 另查烤字残留与 `base-text-contrast`。**交付前强制 tiles 切片循环**（见硬规则 7）。

## 完整参考

- [AGENTS.md](AGENTS.md) — 全部字段速查、坐标系、绘制顺序、锚点、改图配方、体检码→修法、陷阱
- [USAGE.md](USAGE.md) — 面向人的教程和经验（可选阅读）
- [examples/](examples/) — 16 个可运行范例
