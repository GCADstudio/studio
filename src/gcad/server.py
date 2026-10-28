"""`gcad serve`: the local studio (SPEC §6, §7.1).

A small HTTP server bound to 127.0.0.1. It serves the browser UI from gcad/web and answers
JSON-RPC 2.0 calls on POST /rpc. Every call must carry the session token; the Host header
must be the server's own address, which defeats DNS-rebinding pages. Geometry is computed
here, in Python; the browser only edits and draws (D7).
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import secrets
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

from gcad import GENERATOR, __version__
from gcad.backends import available_backends, get_backend, select_backend
from gcad.core.evaluator import Limits
from gcad.core.sexpr import to_text
from gcad.diagnostics import Diagnostic, GcadError, Severity
from gcad.exporters import FORMATS, ExportJob, GraphOnly, export
from gcad.kernels import BUNDLED, Kernel, discover, kernel_for
from gcad.pipeline import evaluate_source

WEB = Path(__file__).parent / "web"
EXAMPLE_PREFIX = "example:"


class RpcError(Exception):
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


def _b64(array: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode("ascii")


def _diagnostic(d: Diagnostic, file: str) -> dict[str, object]:
    out: dict[str, object] = {"severity": d.severity.value, "message": d.message}
    if d.span is not None:
        out.update(
            {
                "line": d.span.line,
                "col": d.span.col,
                "from": d.span.start,
                "to": max(d.span.end, d.span.start + 1),
                "inFile": d.span.file == file,
                "file": d.span.file,
            }
        )
    return out


class GraphBackend:
    """No geometry engine: formats written from the graph alone."""

    name = "none"
    capabilities = frozenset({"graph"})


class Studio:
    """The RPC methods. `root` is the folder files are opened from and saved to."""

    def __init__(self, root: Path, limits: Limits | None = None) -> None:
        self.root = root.resolve()
        self.limits = limits or Limits()
        # Loading OpenCASCADE takes a while, and far longer while an evaluation holds the GIL;
        # do it before the first page asks.
        self.backends = available_backends()
        self.lock = threading.Lock()  # one evaluation at a time
        # Live mode sends an evaluation per edit. A newer one stops the one running and any
        # still waiting, so a runaway source costs one time limit, not one per keystroke.
        self.newest = threading.Event()
        self.newest_lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------------------

    def kernel(self, kernel_id: str) -> Kernel:
        kernels, _ = discover()
        if kernel_id not in kernels:
            raise RpcError(f"no kernel '{kernel_id}'")
        return kernels[kernel_id]

    def resolve(self, path: str, write: bool = False) -> Path:
        if path.startswith(EXAMPLE_PREFIX):
            if write:
                raise RpcError("examples are read-only; save under a new name")
            target = (BUNDLED / path[len(EXAMPLE_PREFIX) :]).resolve()
            if not target.is_relative_to(BUNDLED.resolve()) or "examples" not in target.parts:
                raise RpcError("not an example file")
            return target
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise RpcError("files must stay inside the studio folder")
        return target

    # -- methods ---------------------------------------------------------------------------

    def rpc_hello(self) -> dict[str, object]:
        return {
            "version": __version__,
            "generator": GENERATOR,
            "root": str(self.root),
            "backends": self.backends,
        }

    def rpc_kernel_list(self) -> list[dict[str, object]]:
        kernels, _ = discover()
        return [k.describe() for k in kernels.values()]

    def rpc_file_list(self) -> dict[str, object]:
        kernels, _ = discover()
        extensions = {ext: k.id for k in kernels.values() for ext in k.extensions}
        examples = [
            {"path": f"{EXAMPLE_PREFIX}{k.id}/examples/{p.name}", "name": p.name, "kernel": k.id}
            for k in kernels.values()
            for p in k.examples()
            if k.path.resolve().is_relative_to(BUNDLED.resolve())
        ]
        # minscad, the lead language (SPEC §11), first; then the others by name.
        examples.sort(key=lambda e: (e["kernel"] != "minscad", e["kernel"], e["name"]))
        files: list[dict[str, str]] = []
        skip = {"node_modules", "__pycache__", "out", "build", "dist"}
        for folder, dirs, names in os.walk(self.root):
            # Prune hidden and generated folders before descending (a .venv has thousands).
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in skip)
            for name in sorted(names):
                ext = os.path.splitext(name)[1].lower()
                if ext in extensions:
                    rel = Path(folder, name).relative_to(self.root).as_posix()
                    files.append({"path": rel, "name": name, "kernel": extensions[ext]})
            if len(files) >= 500:
                break
        return {"root": str(self.root), "files": files, "examples": examples}

    def rpc_file_open(self, path: str) -> dict[str, object]:
        target = self.resolve(path)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise RpcError(f"cannot open {path}: {exc.strerror}") from None
        kernel = kernel_for(target)
        return {
            "path": path,
            "name": target.name,
            "text": text,
            "kernel": kernel.id,
            "readOnly": path.startswith(EXAMPLE_PREFIX),
        }

    def rpc_file_save(self, path: str, text: str) -> dict[str, object]:
        target = self.resolve(path, write=True)
        if not target.parent.is_dir():
            raise RpcError(f"folder {target.parent} does not exist")
        target.write_text(text, encoding="utf-8", newline="")
        return {"path": target.relative_to(self.root).as_posix(), "name": target.name}

    def rpc_document_evaluate(
        self, kernel: str, source: str, file: str = "untitled", gcore: bool = False
    ) -> dict[str, object]:
        k = self.kernel(kernel)
        started = time.perf_counter()
        stop = threading.Event()
        with self.newest_lock:
            self.newest.set()
            self.newest = stop
        with self.lock:
            if stop.is_set():  # replaced while waiting
                return {"ok": False, "superseded": True, "diagnostics": [], "ms": 0}
            evaluated = evaluate_source(source, file, k, replace(self.limits, stop=stop))
            result: dict[str, object] = {"ok": False}
            if stop.is_set():
                result["superseded"] = True
            graph = evaluated.result.graph
            diagnostics = list(evaluated.result.diagnostics)
            if gcore:
                result["gcore"] = "\n".join(to_text(f) for f in evaluated.forms)
            if evaluated.result.ok and graph is not None:
                try:
                    result.update(self._preview(graph, evaluated.result.records, file, diagnostics))
                    result["annotations"] = [a.to_json(file) for a in evaluated.result.annotations]
                    result["ok"] = True
                    result["hash"] = graph.hash()[:16]
                except GcadError as exc:
                    diagnostics.append(exc.diagnostic)
        result["diagnostics"] = [_diagnostic(d, file) for d in diagnostics]
        result["ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    def _preview(
        self,
        graph: Any,
        records: dict[str, Any] | None = None,
        file: str | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> dict[str, object]:
        # Without OpenCASCADE (the browser) an imported B-rep is left out with a warning, so
        # the rest of the model and its annotations still show.
        model = get_backend("manifold").realize(graph, skip_imports=diagnostics is not None)
        for skipped in model.skipped:
            if diagnostics is not None:
                diagnostics.append(replace(skipped.diagnostic, severity=Severity.WARNING))
        if not model.is_valid():
            raise GcadError("the mesh back-end produced an invalid model")
        bounds = model.bounds()
        info: dict[str, object] = {
            "dim": graph.root.dim,
            "bounds": [list(map(float, b)) for b in bounds] if bounds else None,
        }
        if graph.root.dim == 2:
            contours = model.contours()
            info["contours"] = [c.round(9).tolist() for c in contours]
            info["area"] = model.area()
            return info
        mesh = model.mesh()
        spans = graph.to_json(spans=True)["spans"]
        info.update(
            {
                "positions": _b64(mesh.vertices, "<f4"),
                "indices": _b64(mesh.triangles, "<u4"),
                "triGroup": _b64(mesh.tri_group, "<u4"),
                "groups": [self._group(g, spans, records or {}, file) for g in mesh.groups],
                "triangles": mesh.triangle_count,
                "volume": model.volume(),
                "area": model.area(),
            }
        )
        return info

    @staticmethod
    def _group(g: Any, spans: Any, records: dict[str, Any], file: str | None) -> dict[str, object]:
        """A mesh group for the page: its colour, and where it was written. A face a back-end
        labelled with its source entity points at that entity's record (FR-U7)."""
        out: dict[str, object] = {"color": list(g.color) if g.color else None}
        if g.entity is not None:
            out["entity"] = g.entity
            where = records.get(g.entity)
            if where is not None:
                out["spans"] = [where.to_json()]
                return out
        out["spans"] = spans.get(g.node_id, [])
        return out

    def rpc_export_formats(self) -> list[dict[str, object]]:
        status = available_backends()
        out: list[dict[str, object]] = []
        for fmt in FORMATS.values():
            reason = ""
            if not fmt.available:
                reason = f"planned for {fmt.milestone}"
            elif fmt.needs == "brep" and status.get("occt") != "ok":
                reason = (
                    "needs OpenCASCADE, which has no WebAssembly build for the browser; "
                    "install GCAD Studio locally for STEP and IGES"
                    if sys.platform == "emscripten"
                    else "needs OpenCASCADE, which is not installed here"
                )
            out.append(
                {
                    "id": fmt.id,
                    "name": fmt.name,
                    "ext": fmt.extensions[0],
                    "needs": fmt.needs,
                    "reason": reason,
                }
            )
        return out

    def rpc_export_run(
        self,
        kernel: str,
        source: str,
        format: str,
        file: str = "untitled",
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        fmt = FORMATS.get(format)
        if fmt is None or not fmt.available:
            raise RpcError(f"format '{format}' is not available")
        k = self.kernel(kernel)
        with self.lock:
            evaluated = evaluate_source(source, file, k, self.limits, trace=fmt.traced)
            graph = evaluated.result.graph
            if not evaluated.result.ok or graph is None:
                raise RpcError("the model has errors; fix them before exporting")
            if fmt.needs == "graph":
                backend: Any = GraphBackend()
                model: Any = GraphOnly(graph.root.dim)
            else:
                backend = select_backend("auto", fmt.needs)
                model = backend.realize(graph)
            stem = Path(file).stem or "model"
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / f"{stem}{fmt.extensions[0]}"
                try:
                    files = export(
                        ExportJob(model, graph, out, dict(options or {})), fmt, backend.capabilities
                    )
                except GcadError as exc:
                    raise RpcError(exc.diagnostic.message) from None
                if len(files) == 1:
                    data, name = files[0].read_bytes(), files[0].name
                else:
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for f in files:
                            zf.writestr(f.name, f.read_bytes())
                    data, name = buffer.getvalue(), f"{stem}-{fmt.id}.zip"
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return {
            "name": name,
            "mime": mime,
            "data": base64.b64encode(data).decode("ascii"),
            "bytes": len(data),
            "backend": backend.name,
        }

    def call(self, method: str, params: dict[str, object]) -> object:
        handler: Callable[..., object] | None = getattr(
            self, "rpc_" + method.replace("/", "_"), None
        )
        if handler is None:
            raise RpcError(f"unknown method '{method}'", -32601)
        try:
            return handler(**params)
        except TypeError as exc:
            raise RpcError(f"bad parameters for {method}: {exc}", -32602) from None

    def respond(self, request: dict[str, Any]) -> dict[str, object]:
        """One JSON-RPC 2.0 request to its response; shared by HTTP and the web worker."""
        request_id: object = None
        try:
            request_id = request.get("id")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError("params must be an object", -32602)
            result = self.call(str(request.get("method", "")), params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RpcError as exc:
            error = {"code": exc.code, "message": str(exc)}
        except Exception as exc:  # report, keep serving
            traceback.print_exc()
            error = {"code": -32603, "message": f"internal error: {exc}"}
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def make_handler(studio: Studio, token: str, host_names: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"GCADStudio/{__version__}"

        def log_message(self, format: str, *args: object) -> None:  # quiet by default
            pass

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in host_names

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if not self._host_ok():
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB / rel).resolve()
            if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            ctype = {
                ".js": "text/javascript",
                ".mjs": "text/javascript",
                ".css": "text/css",
                ".html": "text/html; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".json": "application/json",
            }.get(target.suffix, "application/octet-stream")
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if not self._host_ok():
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return
            if self.path != "/rpc":
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            if not secrets.compare_digest(self.headers.get("X-GCAD-Token", ""), token):
                self._send(HTTPStatus.UNAUTHORIZED, b"bad token", "text/plain")
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                request = None
            if isinstance(request, dict):
                body = studio.respond(request)
            else:
                error = {"code": -32700, "message": "the request is not a JSON object"}
                body = {"jsonrpc": "2.0", "id": None, "error": error}
            self._send(HTTPStatus.OK, json.dumps(body).encode(), "application/json")

    return Handler


class StudioServer:
    def __init__(
        self, root: Path, port: int = 0, token: str | None = None, limits: Limits | None = None
    ) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.studio = Studio(root, limits)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
        self.port = self.httpd.server_address[1]
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.httpd.RequestHandlerClass = make_handler(self.studio, self.token, hosts)
        self.httpd.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#token={self.token}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
