"""Exporter registry (SPEC §8). Each exporter states what it consumes: mesh, brep or 2d."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ExportFormat:
    id: str
    name: str
    extensions: tuple[str, ...]
    needs: str  # "mesh", "brep" or "2d"
    writer: Callable[[ExportJob], list[Path]] | None
    milestone: str = ""  # set for formats that are specified but not built yet

    @property
    def available(self) -> bool:
        return self.writer is not None


def _formats() -> dict[str, ExportFormat]:
    from gcad.exporters import gltf, obj, stl

    built = [
        ExportFormat("stl", "STL (binary; --ascii for ASCII)", (".stl",), "mesh", stl.write),
        ExportFormat("obj", "Wavefront OBJ + MTL", (".obj",), "mesh", obj.write),
        ExportFormat("glb", "glTF 2.0 binary", (".glb",), "mesh", gltf.write),
    ]
    planned = [
        ("step", "STEP AP214/AP242", (".step", ".stp"), "brep", "M3"),
        ("iges", "IGES 5.3", (".iges", ".igs"), "brep", "M3"),
        ("x3d", "X3D 3.3 XML", (".x3d",), "mesh", "M3"),
        ("vrml", "VRML97", (".wrl",), "mesh", "M3"),
        ("dae", "COLLADA 1.4.1", (".dae",), "mesh", "M3"),
        ("unv", "I-DEAS Universal", (".unv",), "mesh", "M3"),
        ("dxf", "DXF R2000", (".dxf",), "2d", "M4"),
        ("svg", "SVG 1.1", (".svg",), "2d", "M4"),
        ("pdf", "PDF 1.7 vector", (".pdf",), "2d", "M4"),
        ("eps", "EPS 3.0", (".eps",), "2d", "M4"),
        ("cgm", "CGM binary", (".cgm",), "2d", "M4"),
    ]
    out = {f.id: f for f in built}
    for fid, name, ext, needs, milestone in planned:
        out[fid] = ExportFormat(fid, name, ext, needs, None, milestone)
    return out


FORMATS = _formats()


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
    if fmt.needs == "mesh" and job.model.dim != 3:
        raise GcadError(f"{fmt.name} needs a 3D model; this model is 2D")
    if fmt.needs == "2d" and job.model.dim != 2:
        raise GcadError(f"{fmt.name} needs a 2D model or a section")
    job.path.parent.mkdir(parents=True, exist_ok=True)
    return fmt.writer(job)
