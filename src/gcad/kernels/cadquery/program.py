"""The `cadquery` process kernel: runs a CadQuery script (https://cadquery.readthedocs.io).

Speaks the GCAD Studio process-kernel protocol (SPEC §7.2) on stdin/stdout. It runs under the
Python named by GCAD_CADQUERY_PYTHON, an environment with CadQuery installed, and imports
nothing from GCAD Studio. The script shows its result with show_object(...), as in CQ-editor,
or leaves it in a variable named `result`; every shown object goes into one STEP file, which
is handed back as `step_path`, so the geometry stays exact.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any

SOURCE = "<gcad source>"
real_stdout = sys.stdout


def send(message: dict[str, Any]) -> None:
    real_stdout.write(json.dumps(message) + "\n")
    real_stdout.flush()


def error_at(message: str, line: int | None = None, col: int | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"severity": "error", "message": message}
    if line:
        d["line"] = line
        if col:
            d["col"] = col
    return d


def shapes_of(obj: Any, cq: Any) -> list[Any]:
    if isinstance(obj, cq.Workplane):
        return [v for v in obj.vals() if isinstance(v, cq.Shape)]
    if isinstance(obj, cq.Assembly):
        return [obj.toCompound()]
    if isinstance(obj, cq.Shape):
        return [obj]
    return []


def evaluate(params: dict[str, Any]) -> dict[str, Any]:
    import cadquery as cq

    shown: list[Any] = []

    def show_object(obj: Any, name: str | None = None, options: Any = None, **_: Any) -> None:
        shown.append(obj)

    def debug(obj: Any, *args: Any, **kwargs: Any) -> None:
        pass  # CQ-editor's debug view; nothing to draw here

    if params.get("folder"):
        sys.path.insert(0, str(params["folder"]))
    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": str(params.get("file") or SOURCE),
        "show_object": show_object,
        "debug": debug,
        "cq": cq,
    }
    printed = io.StringIO()
    diagnostics: list[dict[str, Any]] = []
    try:
        code = compile(str(params["source"]), SOURCE, "exec")
        with contextlib.redirect_stdout(printed):
            exec(code, namespace)
    except SyntaxError as exc:
        return {"diagnostics": [error_at(f"syntax error: {exc.msg}", exc.lineno, exc.offset)]}
    except Exception as exc:  # the script's own error, reported at its line
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == SOURCE]
        line = frames[-1].lineno if frames else None
        text = f"{type(exc).__name__}: {exc}"
        return {"diagnostics": [*_printed(printed), error_at(text, line)]}
    diagnostics += _printed(printed)
    if not shown and "result" in namespace:
        shown.append(namespace["result"])
    shapes = [s for obj in shown for s in shapes_of(obj, cq)]
    if not shapes:
        message = "the script showed no solid; call show_object(...) or assign it to `result`"
        return {"diagnostics": [*diagnostics, error_at(message)]}
    model = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    out = Path(str(params["workdir"])) / "model.step"
    cq.exporters.export(model, str(out), exportType="STEP")
    return {"diagnostics": diagnostics, "step_path": out.name}


def _printed(buffer: io.StringIO) -> list[dict[str, Any]]:
    return [
        {"severity": "info", "message": line} for line in buffer.getvalue().splitlines() if line
    ]


def main() -> None:
    try:
        import cadquery  # noqa: F401 - fail at start, with a clear message
    except ImportError:
        print(
            f"CadQuery is not installed in {sys.executable}; "
            "install it there or point GCAD_CADQUERY_PYTHON at an environment that has it",
            file=sys.stderr,
        )
        sys.exit(2)
    for line in sys.stdin:
        request = json.loads(line)
        method, rid = request.get("method"), request.get("id")
        if method == "shutdown":
            send({"jsonrpc": "2.0", "id": rid, "result": None})
            return
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"name": "cadquery", "version": "0.1.0"}})
        elif method == "evaluate":
            send({"jsonrpc": "2.0", "id": rid, "result": evaluate(request["params"])})
        else:
            error = {"code": -32601, "message": f"unknown method {method}"}
            send({"jsonrpc": "2.0", "id": rid, "error": error})


if __name__ == "__main__":
    main()
