"""scifig 命令行入口。

  python -m scifig.cli render  spec.yaml [-o out.png] [--grid] [--dpi 600] [--svg out.svg]
  python -m scifig.cli studio  spec.yaml [--port 8323] [--no-open]
  python -m scifig.cli assets  spec.yaml --api-key KEY [--only id1,id2] [--force] [--no-auto-select]
  python -m scifig.cli select  spec.yaml ASSET_ID INDEX
  python -m scifig.cli cutout  in.png out.png [--threshold 238] [--shadow keep|remove]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .assets import auto_select, gacha_generate, save_report, select_candidate
from .cutout import cutout_white_bg
from .lint import lint
from .render import render
from .spec import load_spec


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


def cmd_assets(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    api_key = args.api_key or os.environ.get("SCIFIG_API_KEY", "")
    if not api_key:
        print("需要 --api-key 或环境变量 SCIFIG_API_KEY", file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scifig", description="受控科研图片生成工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("render", help="渲染 spec 为 PNG 并体检")
    pr.add_argument("spec")
    pr.add_argument("-o", "--output")
    pr.add_argument("--svg", help="同时导出 SVG 到该路径")
    pr.add_argument("--grid", action="store_true", help="叠加 10mm 坐标网格（调布局用）")
    pr.add_argument("--dpi", type=int, default=None)
    pr.add_argument("--strict", action="store_true", help="有 E 级问题时返回非零")
    pr.set_defaults(func=cmd_render)

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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
