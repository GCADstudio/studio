"""Source file -> kernel -> GCore -> geometry graph -> back-end -> export: the end-to-end loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gcad.backends import select_backend
from gcad.core.evaluator import EvalResult, Limits, evaluate
from gcad.core.sexpr import Expr
from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob, export, format_for
from gcad.kernels import Kernel, kernel_for


@dataclass
class Evaluated:
    kernel: Kernel
    forms: list[Expr]
    result: EvalResult


def evaluate_source(
    source: str, file: str, kernel: Kernel, limits: Limits | None = None
) -> Evaluated:
    try:
        forms = kernel.to_gcore(source, file)
        prelude = kernel.prelude()
    except GcadError as exc:
        return Evaluated(kernel, [], EvalResult(None, [exc.diagnostic]))
    metadata = {"title": Path(file).stem, "source": Path(file).name, "kernel": kernel.id}
    result = evaluate(forms, limits, metadata, prelude, source)
    try:
        result.diagnostics.extend(kernel.check(source, file))
    except GcadError as exc:
        result.diagnostics.append(exc.diagnostic)
    return Evaluated(kernel, forms, result)


def evaluate_file(
    path: Path, kernel_id: str | None = None, limits: Limits | None = None
) -> Evaluated:
    kernel = kernel_for(path, kernel_id)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GcadError(f"cannot read {path}: {exc.strerror}") from None
    return evaluate_source(source, str(path), kernel, limits)


@dataclass
class ExportReport:
    files: list[Path]
    format_name: str
    backend: str
    summary: str  # what was written: triangles and volume, or outlines and area
    triangles: int | None = None


def export_file(
    path: Path,
    out: Path,
    fmt_id: str | None = None,
    kernel_id: str | None = None,
    backend_name: str = "auto",
    tolerance: float | None = None,
    options: dict[str, object] | None = None,
) -> tuple[Evaluated, ExportReport | None]:
    """Evaluate a source file and export it. `auto` uses the exact B-rep back-end for
    formats that need exact geometry and the mesh back-end otherwise."""
    fmt = format_for(out, fmt_id)
    if not fmt.available:
        raise GcadError(f"{fmt.name} export is specified but arrives in milestone {fmt.milestone}")
    evaluated = evaluate_file(path, kernel_id)
    graph = evaluated.result.graph
    if not evaluated.result.ok or graph is None:
        return evaluated, None
    backend = select_backend(backend_name, fmt.needs)
    model = backend.realize(graph, tolerance)
    if not model.is_valid():
        raise GcadError(f"back-end '{backend.name}' produced an invalid model")
    files = export(ExportJob(model, graph, out, dict(options or {})), fmt, backend.capabilities)
    triangles = model.mesh().triangle_count if fmt.needs == "mesh" else None
    if fmt.needs == "2d":
        n = len(model.contours())
        summary = f"{n} outline{'s' if n != 1 else ''}, area {model.area():.6g} mm^2"
    elif triangles is not None:
        summary = f"{triangles} triangles, volume {model.volume():.6g} mm^3"
    else:
        summary = f"volume {model.volume():.6g} mm^3"
    return evaluated, ExportReport(files, fmt.name, backend.name, summary, triangles)
