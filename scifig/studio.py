"""scifig studio — 本地交互式调图界面（零额外依赖，浏览器打开即用）。

用法：
    python -m scifig.cli studio examples/rep_evdispatch/figure.yaml
    # 等价：python -m scifig.studio examples/rep_evdispatch/figure.yaml --port 8323

设计：
- spec 文本的源头始终在浏览器编辑器里；每次改动自动 POST /api/render
  重渲（不落盘），解决"改完还得手动跑一遍 CLI"。
- 预览 SVG 中每个元素带 data-el 分组（见 render.py 的 _wrap_el），
  前端据此实现点选高亮、定位 YAML 行、拖拽/方向键微调坐标。
- 拖拽/微调直接改写 YAML 里 rect/at 的前两个数值——元素与源码行的
  对应关系由 element_ranges()（yaml.compose 的行号标记）给出。
- 服务器只监听 127.0.0.1、只操作启动时指定的一个 spec 文件。
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from .lint import lint
from .render import render
from .spec import (AssetEl, BadgeEl, BoxEl, FigureSpec, GroupEl, MarkerEl,
                   NetworkEl, PanelEl, PanelLabelEl, ScatterEl, SpecError,
                   TextEl, TokensEl, load_spec)

_DRAG_RECT = (BoxEl, AssetEl, PanelEl, TokensEl, NetworkEl, ScatterEl)
_DRAG_AT = (TextEl, MarkerEl, BadgeEl, PanelLabelEl)


def element_ranges(text: str, spec: FigureSpec) -> list[dict]:
    """elements 序列逐项的 YAML 行号范围（0-based 闭区间），与 spec.elements 按序对齐。

    load_spec 对每个 YAML 条目恰好追加一个元素，因此可以按下标一一对应。
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    seq = None
    for k, v in root.value:
        if getattr(k, "value", None) == "elements" and isinstance(v, yaml.SequenceNode):
            seq = v
            break
    if seq is None:
        return []

    items = seq.value
    n_lines = text.count("\n") + 1
    out = []
    for i, node in enumerate(items):
        if i >= len(spec.elements):
            break
        el = spec.elements[i]
        line0 = node.start_mark.line
        if i + 1 < len(items):
            line1 = items[i + 1].start_mark.line - 1
        else:
            line1 = min(node.end_mark.line, n_lines - 1)
        if isinstance(el, _DRAG_RECT):
            drag = "rect"
        elif isinstance(el, _DRAG_AT):
            drag = "at"
        elif isinstance(el, GroupEl) and el.rect is not None:
            drag = "rect"
        else:
            drag = None  # arrow / members 推导的 group：位置由锚点决定，不可拖
        out.append({
            "id": el.id,
            "type": type(el).__name__.removesuffix("El").lower(),
            "line0": line0, "line1": max(line0, line1),
            "drag": drag,
        })
    return out


class StudioServer:
    """无状态 API：load / render(text) / save(text) / export(text)。"""

    def __init__(self, spec_path: Path):
        self.spec_path = spec_path.resolve()

    def api_load(self) -> dict:
        return {"path": str(self.spec_path),
                "text": self.spec_path.read_text(encoding="utf-8")}

    def api_render(self, payload: dict) -> dict:
        text = payload.get("text", "")
        try:
            spec = load_spec(self.spec_path, text=text)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            return {"error": {"msg": f"YAML 解析错误：{e}",
                              "line": mark.line if mark else None}}
        except SpecError as e:
            return {"error": {"msg": str(e), "line": None}}
        try:
            res = render(spec, out_png=None, grid=bool(payload.get("grid")))
        except Exception as e:  # noqa: BLE001 — 渲染期错误如实回传给前端
            return {"error": {"msg": f"渲染失败 {type(e).__name__}: {e}", "line": None}}
        issues = lint(spec, res)
        return {
            "svg": res.svg,
            "width": spec.width, "height": spec.height,
            "issues": [{"level": i.level, "code": i.code, "msg": i.msg} for i in issues],
            "elements": element_ranges(text, spec),
        }

    def api_save(self, payload: dict) -> dict:
        self.spec_path.write_text(payload.get("text", ""), encoding="utf-8")
        return {"ok": True}

    def api_export(self, payload: dict) -> dict:
        """保存 YAML 并导出 PNG + SVG（与 CLI render 等价的产物）。"""
        text = payload.get("text", "")
        self.spec_path.write_text(text, encoding="utf-8")
        spec = load_spec(self.spec_path)
        dpi = payload.get("dpi")
        png = self.spec_path.with_suffix(".png")
        svg = self.spec_path.with_suffix(".svg")
        res = render(spec, out_png=png, dpi=int(dpi) if dpi else None)
        svg.write_text(res.svg, encoding="utf-8")
        issues = lint(spec, res)
        return {"png": str(png), "svg": str(svg),
                "errors": sum(1 for i in issues if i.level == "E"),
                "warnings": sum(1 for i in issues if i.level == "W")}


class _Handler(BaseHTTPRequestHandler):
    server_version = "scifig-studio"
    studio: StudioServer  # serve() 注入

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def _json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (Path(__file__).parent / "studio.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/api/load":
            self._json(self.studio.api_load())
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": {"msg": "请求体不是合法 JSON"}}, 400)
            return
        handler = {
            "/api/render": self.studio.api_render,
            "/api/save": self.studio.api_save,
            "/api/export": self.studio.api_export,
        }.get(self.path)
        if handler is None:
            self.send_error(404)
            return
        try:
            self._json(handler(payload))
        except Exception as e:  # noqa: BLE001
            self._json({"error": {"msg": f"{type(e).__name__}: {e}"}}, 500)


def serve(spec_path: str | Path, port: int = 8323, open_browser: bool = True) -> None:
    sp = Path(spec_path)
    if not sp.exists():
        raise SystemExit(f"spec 不存在: {sp}")
    load_spec(sp)  # 启动前先验证一遍，spec 坏了立刻报错而非白屏
    _Handler.studio = StudioServer(sp)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"scifig studio → {url}   spec: {sp}（Ctrl+C 退出）")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


def main() -> None:
    ap = argparse.ArgumentParser(description="scifig 交互式调图界面")
    ap.add_argument("spec")
    ap.add_argument("--port", type=int, default=8323)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args()
    serve(a.spec, a.port, open_browser=not a.no_open)


if __name__ == "__main__":
    main()
