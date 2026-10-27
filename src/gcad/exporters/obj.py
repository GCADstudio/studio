"""Wavefront OBJ with an MTL file for colours. OBJ has no unit field; a comment states mm."""

from __future__ import annotations

from pathlib import Path

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob

DEFAULT_RGBA = (0.8, 0.8, 0.8, 1.0)


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    runs = mesh.color_runs()
    mtl_path = job.path.with_suffix(".mtl")
    lines = [
        f"# {job.generator}",
        "# units: mm",
        f"# source: {Path(job.source).name}" if job.source else "# source: -",
        f"mtllib {mtl_path.name}",
        f"o {job.title}",
    ]
    lines.extend("v " + " ".join(format_number(float(x)) for x in v) for v in mesh.vertices)
    mtl = [f"# {job.generator}"]
    for i, (color, tris) in enumerate(runs):
        rgba = color or DEFAULT_RGBA
        mtl += [
            f"newmtl color_{i}",
            "Kd " + " ".join(format_number(round(c, 6)) for c in rgba[:3]),
            f"d {format_number(round(rgba[3], 6))}",
            "illum 1",
        ]
        lines.append(f"usemtl color_{i}")
        lines.extend(f"f {a + 1} {b + 1} {c + 1}" for a, b, c in mesh.triangles[tris])
    job.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    mtl_path.write_text("\n".join(mtl) + "\n", encoding="utf-8", newline="\n")
    return [job.path, mtl_path]
