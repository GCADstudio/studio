"""Source file -> kernel -> GCore -> geometry graph -> back-end -> export: the end-to-end loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gcad.backends import get_backend
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
    return Evaluated(kernel, forms, evaluate(forms, limits, metadata, prelude))


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
    triangles: int | None
    volume: float


def export_file(
    path: Path,
    out: Path,
    fmt_id: str | None = None,
    kernel_id: str | None = None,
    backend_name: str = "manifold",
    tolerance: float | None = None,
    options: dict[str, object] | None = None,
) -> tuple[Evaluated, ExportReport | None]:
    evaluated = evaluate_file(path, kernel_id)
    graph = evaluated.result.graph
    if not evaluated.result.ok or graph is None:
        return evaluated, None
    fmt = format_for(out, fmt_id)
    backend = get_backend(backend_name)
    model = backend.realize(graph, tolerance)
    if not model.is_valid():
        raise GcadError("the back-end produced an invalid model")
    files = export(ExportJob(model, graph, out, dict(options or {})), fmt, backend.capabilities)
    triangles = model.mesh().triangle_count if fmt.needs == "mesh" else None
    return evaluated, ExportReport(files, fmt.name, triangles, model.volume())
