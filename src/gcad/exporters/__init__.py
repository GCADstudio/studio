"""Exporter registry (SPEC §8). Each exporter states what it consumes: mesh, brep or 2d."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from gcad import GENERATOR
from gcad.backends.base import RealizedModel
from gcad.core.graph import Graph
from gcad.diagnostics import GcadError


@dataclass
class ExportJob:
    model: RealizedModel
    graph: Graph
    path: Path
    options: dict[str, object] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.graph.metadata.get("title") or self.path.stem

    @property
    def source(self) -> str:
        return self.graph.metadata.get("source", "")

    @property
    def generator(self) -> str:
        return GENERATOR

    @property
    def time(self) -> datetime:
        """The time stamped into formats that carry one (EX-2): the `epoch` option, else
        SOURCE_DATE_EPOCH, else now. Fixing it makes STEP, IGES and COLLADA reproducible."""
        epoch = self.options.get("epoch") or os.environ.get("SOURCE_DATE_EPOCH")
        if epoch not in (None, ""):
            try:
                return datetime.fromtimestamp(int(str(epoch)), tz=UTC)
            except ValueError:
                raise GcadError(f"invalid epoch timestamp {epoch!r}") from None
        return datetime.now(tz=UTC).replace(microsecond=0)


def _lazy(module: str) -> Callable[[ExportJob], list[Path]]:
    """Import a writer only when used, so formats needing OpenCASCADE do not stop the
    others from loading where it is not installed."""

    def write(job: ExportJob) -> list[Path]:
        writer: Callable[[ExportJob], list[Path]] = importlib.import_module(module).write
        return writer(job)

    return write


@dataclass(frozen=True)
class ExportFormat:
    id: str
    name: str
    extensions: tuple[str, ...]
    needs: str  # "mesh", "brep", "2d", or "graph" (written from the graph; no back-end)
    writer: Callable[[ExportJob], list[Path]] | None
    milestone: str = ""  # set for formats that are specified but not built yet
    #: evaluate in trace mode, so numbers keep the parameters they came from (FR-C10)
    traced: bool = False

    @property
    def available(self) -> bool:
        return self.writer is not None


def _formats() -> dict[str, ExportFormat]:
    m = "gcad.exporters."
    built = [
        ExportFormat(
            "step",
            "STEP (AP214, or AP242 with --schema)",
            (".step", ".stp"),
            "brep",
            _lazy(m + "step"),
        ),
        ExportFormat(
            "step-parametric",
            "STEP parametric (ISO 10303-42 CSG, -55 history, -108 parameters; AP242)",
            (".stp", ".step"),
            "graph",
            _lazy(m + "step_parametric"),
            traced=True,
        ),
        ExportFormat("iges", "IGES 5.3 B-rep solid", (".iges", ".igs"), "brep", _lazy(m + "iges")),
        ExportFormat("stl", "STL (binary; --ascii for ASCII)", (".stl",), "mesh", _lazy(m + "stl")),
        ExportFormat("obj", "Wavefront OBJ + MTL", (".obj",), "mesh", _lazy(m + "obj")),
        ExportFormat("glb", "glTF 2.0 binary", (".glb",), "mesh", _lazy(m + "gltf")),
        ExportFormat("x3d", "X3D 3.3 XML", (".x3d",), "mesh", _lazy(m + "x3d")),
        ExportFormat("vrml", "VRML97", (".wrl",), "mesh", _lazy(m + "vrml")),
        ExportFormat("dae", "COLLADA 1.4.1", (".dae",), "mesh", _lazy(m + "collada")),
        ExportFormat("unv", "I-DEAS Universal surface mesh", (".unv",), "mesh", _lazy(m + "unv")),
        ExportFormat(
            "dxf", "DXF (R2000, or R12 with --dxf-version)", (".dxf",), "2d", _lazy(m + "dxf")
        ),
        ExportFormat("svg", "SVG 1.1", (".svg",), "2d", _lazy(m + "svg")),
        ExportFormat("pdf", "PDF 1.7 vector", (".pdf",), "2d", _lazy(m + "pdf")),
        ExportFormat("eps", "EPS 3.0 (PostScript Level 2)", (".eps",), "2d", _lazy(m + "eps")),
        ExportFormat("cgm", "CGM version 1, binary", (".cgm",), "2d", _lazy(m + "cgm")),
    ]
    # Formats that are specified but not built yet: (id, name, extensions, needs, milestone).
    planned: list[tuple[str, str, tuple[str, ...], str, str]] = []
    out = {f.id: f for f in built}
    for fid, name, ext, needs, milestone in planned:
        out[fid] = ExportFormat(fid, name, ext, needs, None, milestone)
    return out


FORMATS = _formats()


class GraphOnly:
    """The model handed to a format written from the graph alone: nothing is realized."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _none(self) -> object:
        raise GcadError("this format is written from the graph; nothing was realized")

    mesh = contours = volume = area = bounds = _none  # type: ignore[assignment]

    def is_valid(self) -> bool:
        return True


def format_for(path: Path, explicit: str | None = None) -> ExportFormat:
    if explicit:
        fmt = FORMATS.get(explicit.lower().lstrip("."))
        if fmt is None:
            raise GcadError(f"unknown export format '{explicit}' (see 'gcad formats')")
        return fmt
    suffix = path.suffix.lower()
    for fmt in FORMATS.values():
        if suffix in fmt.extensions:
            return fmt
    raise GcadError(f"cannot tell the export format from '{path.name}'; pass -f")


def export(job: ExportJob, fmt: ExportFormat, backend_capabilities: frozenset[str]) -> list[Path]:
    if fmt.writer is None:
        raise GcadError(f"{fmt.name} export is specified but arrives in milestone {fmt.milestone}")
    if fmt.needs == "brep" and "brep" not in backend_capabilities:
        raise GcadError(f"{fmt.name} needs exact geometry; the active back-end only makes meshes")
    if fmt.needs in ("mesh", "brep") and job.model.dim != 3:
        raise GcadError(f"{fmt.name} needs a 3D model; this model is 2D")
    if fmt.needs == "2d" and job.model.dim != 2:
        raise GcadError(
            f"{fmt.name} needs a 2D model; wrap a 3D model in (section ...) or (project ...)"
        )
    if fmt.needs == "2d" and "2d" not in backend_capabilities:
        raise GcadError(f"{fmt.name} is written from the manifold back-end; use --backend auto")
    job.path.parent.mkdir(parents=True, exist_ok=True)
    return fmt.writer(job)
