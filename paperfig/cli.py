"""paperfig 命令行入口。

  python -m paperfig.cli render  spec.yaml [-o out.png] [--grid] [--dpi 600] [--svg out.svg]
  python -m paperfig.cli resolve spec.yaml -o resolved.yaml
  python -m paperfig.cli studio  spec.yaml [--port 8323] [--no-open]
  python -m paperfig.cli assets  spec.yaml --api-key KEY [--only id1,id2] [--force] [--no-auto-select]
  python -m paperfig.cli select  spec.yaml ASSET_ID INDEX
  python -m paperfig.cli cutout  in.png out.png [--threshold 238] [--shadow keep|remove]
  python -m paperfig.cli base gen  spec.yaml [--api-key|-k] [--model] [--force] [--candidates N]
  python -m paperfig.cli base pick spec.yaml N
  python -m paperfig.cli base grid spec.yaml
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from .assets import auto_select, gacha_generate, save_report, select_candidate
from .base import base_gacha, overlay_mm_grid, pick_base, render_skeleton
from .cutout import cutout_white_bg
from .layout import LayoutError, document_has_layout, materialize_yaml
from .lint import lint
from .render import render
from .spec import load_spec


def _resolve_api_key(args: argparse.Namespace) -> str:
    return (
        getattr(args, "api_key", None)
        or os.environ.get("PAPERFIG_API_KEY")
        or os.environ.get("SCIFIG_API_KEY", "")
    )


def cmd_render(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    out = Path(args.output) if args.output else spec.path.with_suffix(".png")
    res = render(spec, out_png=out, grid=args.grid, dpi=args.dpi)
    if args.svg:
        Path(args.svg).write_text(res.svg, encoding="utf-8")
        print(f"SVG: {args.svg}")
    print(f"PNG: {out}")

    issues = lint(spec, res)
    errors = [i for i in issues if i.level == "E"]
    warns = [i for i in issues if i.level == "W"]
    for i in issues:
        print(f"  {i}")
    print(f"体检: {len(errors)} 错误, {len(warns)} 警告")
    return 1 if errors and args.strict else 0


def cmd_resolve(args: argparse.Namespace) -> int:
    """结构化 layout → 纯绝对坐标 YAML（可供手改 / 直接 render）。"""
    src = Path(args.spec)
    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 2
    if not isinstance(raw, dict):
        print("spec 顶层必须是 mapping", file=sys.stderr)
        return 2

    if not document_has_layout(raw):
        if args.output:
            # 幂等：无 layout 时原样写出（或提示）
            text = src.read_text(encoding="utf-8")
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"无 layout 节可解，已原样写出: {args.output}")
        else:
            print("无 layout 节可解（已是绝对坐标 spec）")
        return 0

    try:
        text = materialize_yaml(raw, force=args.force)
    except LayoutError as e:
        print(f"布局求解失败: {e}", file=sys.stderr)
        return 1

    out = Path(args.output) if args.output else src.with_suffix(".resolved.yaml")
    header = (
        f"# resolved from {src.name} — absolute coordinates; layout tree removed\n"
        f"# edit rect / via freely; re-resolve from structured source to regenerate\n"
    )
    out.write_text(header + text, encoding="utf-8")
    print(f"Resolved: {out}")
    return 0


def cmd_assets(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    api_key = _resolve_api_key(args)
    if not api_key:
        print(
            "需要 --api-key 或环境变量 PAPERFIG_API_KEY（兼容 SCIFIG_API_KEY）",
            file=sys.stderr,
        )
        return 2
    only = set(args.only.split(",")) if args.only else None

    todo = [r for r in spec.asset_requests if only is None or r.id in only]
    if not todo:
        print("spec 中没有匹配的素材请求")
        return 0

    results = []
    for req in todo:
        r = gacha_generate(req, spec.assets_dir, api_key,
                           model=args.model, force=args.force)
        if r.candidates and not args.no_auto_select:
            if not auto_select(r, spec.assets_dir):
                print(f"  [!] {req.id} 无达标候选（全部 <60 分），需重抽或人工挑选")
        results.append(r)

    report = save_report(results, spec.assets_dir)
    print(f"\n抽卡报告: {report}")
    n_missing = sum(1 for r in results if r.candidates and r.selected is None)
    return 1 if n_missing else 0


def cmd_select(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    select_candidate(spec.assets_dir, args.asset_id, args.index)
    return 0


def cmd_cutout(args: argparse.Namespace) -> int:
    rep = cutout_white_bg(args.input, args.output,
                          threshold=args.threshold, shadow=args.shadow)
    print(rep)
    return 0 if rep.ok else 1


def cmd_studio(args: argparse.Namespace) -> int:
    from .studio import serve
    serve(args.spec, port=args.port, open_browser=not args.no_open)
    return 0


def cmd_base_gen(args: argparse.Namespace) -> int:
    """底稿抽卡：skeleton 模式先渲骨架图，经 urls 作图生图参考。"""
    spec = load_spec(args.spec)
    if spec.base is None:
        print("spec 缺少 base: 段（mode/prompt 必填）", file=sys.stderr)
        return 2

    api_key = _resolve_api_key(args)
    if not api_key:
        print(
            "需要 --api-key/-k 或环境变量 PAPERFIG_API_KEY（兼容 SCIFIG_API_KEY）",
            file=sys.stderr,
        )
        return 2

    base_dir = spec.base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    reference = None
    if spec.base.mode == "skeleton":
        sk_path = base_dir / "skeleton.png"
        render_skeleton(spec, sk_path)
        print(f"骨架图: {sk_path}")
        reference = sk_path

    n = args.candidates if args.candidates is not None else None
    result = base_gacha(
        args.spec,
        api_key,
        model=args.model,
        force=args.force,
        candidates=n,
        reference_image=reference,
    )
    if not result.candidates:
        return 0 if (base_dir / "base.png").exists() else 1
    ok_n = sum(1 for c in result.candidates if c.verdict != "reject")
    print(f"完成: {ok_n}/{len(result.candidates)} 候选可用；用 paperfig base pick 选卡")
    return 0 if ok_n else 1


def cmd_base_pick(args: argparse.Namespace) -> int:
    try:
        pick_base(args.spec, args.index)
    except (ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_base_grid(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    try:
        out = overlay_mm_grid(spec)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"网格底稿: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="paperfig", description="Editable, controllable academic paper figures")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("render", help="渲染 spec 为 PNG 并体检")
    pr.add_argument("spec")
    pr.add_argument("-o", "--output")
    pr.add_argument("--svg", help="同时导出 SVG 到该路径")
    pr.add_argument("--grid", action="store_true", help="叠加 10mm 坐标网格（调布局用）")
    pr.add_argument("--dpi", type=int, default=None)
    pr.add_argument("--strict", action="store_true", help="有 E 级问题时返回非零")
    pr.set_defaults(func=cmd_render)

    pres = sub.add_parser("resolve", help="结构化 layout → 绝对坐标 YAML")
    pres.add_argument("spec")
    pres.add_argument("-o", "--output", help="输出路径（默认 *.resolved.yaml）")
    pres.add_argument("--force", action="store_true",
                      help="覆盖元素上已有的 rect（默认尊重手改）")
    pres.set_defaults(func=cmd_resolve)

    pa = sub.add_parser("assets", help="按 spec 抽卡生成素材")
    pa.add_argument("spec")
    pa.add_argument("--api-key", default=None)
    pa.add_argument("--model", default="nano-banana-fast")
    pa.add_argument("--only", help="只生成这些 id（逗号分隔）")
    pa.add_argument("--force", action="store_true", help="已有正式素材也重抽")
    pa.add_argument("--no-auto-select", action="store_true")
    pa.set_defaults(func=cmd_assets)

    ps = sub.add_parser("select", help="把候选提升为正式素材")
    ps.add_argument("spec")
    ps.add_argument("asset_id")
    ps.add_argument("index", type=int)
    ps.set_defaults(func=cmd_select)

    pc = sub.add_parser("cutout", help="单独对一张白底图抠图")
    pc.add_argument("input")
    pc.add_argument("output")
    pc.add_argument("--threshold", type=int, default=238)
    pc.add_argument("--shadow", choices=["keep", "remove"], default="keep")
    pc.set_defaults(func=cmd_cutout)

    pst = sub.add_parser("studio", help="交互式调图界面（本地浏览器，改动即时重渲、可拖拽微调）")
    pst.add_argument("spec")
    pst.add_argument("--port", type=int, default=8323)
    pst.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    pst.set_defaults(func=cmd_studio)

    pb = sub.add_parser("base", help="AI 整图底稿：骨架导出 / 抽卡 / 选卡 / mm 网格")
    pb_sub = pb.add_subparsers(dest="base_cmd", required=True)

    pbg = pb_sub.add_parser("gen", help="底稿抽卡（skeleton 先渲骨架作参考）")
    pbg.add_argument("spec")
    pbg.add_argument("-k", "--api-key", default=None)
    pbg.add_argument(
        "--model",
        default="nano-banana-fast",
        help="生图模型：nano-banana-fast（默认）/ nano-banana-2 / nano-banana-pro",
    )
    pbg.add_argument("--force", action="store_true", help="已有 base/base.png 也重抽")
    pbg.add_argument("--candidates", type=int, default=None, help="候选数（覆盖 base.candidates）")
    pbg.set_defaults(func=cmd_base_gen)

    pbp = pb_sub.add_parser("pick", help="把候选提升为 base/base.png")
    pbp.add_argument("spec")
    pbp.add_argument("index", type=int, help="候选编号（从 1 起）")
    pbp.set_defaults(func=cmd_base_pick)

    pbgrid = pb_sub.add_parser("grid", help="底稿叠 mm 网格 → base/base_grid.png")
    pbgrid.add_argument("spec")
    pbgrid.set_defaults(func=cmd_base_grid)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
