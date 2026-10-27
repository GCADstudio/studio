"""I-DEAS Universal (UNV): the surface triangulation as a finite-element mesh.

Datasets: 164 units (code 5, mm / milli-newton; length factor 1000 converts to metres by
division), 2411 nodes, 2412 elements of type 91 (thin shell linear triangle). Field widths
follow the I-DEAS fixed formats, with D exponents for coordinates.
"""

from __future__ import annotations

from pathlib import Path

from gcad.exporters import ExportJob


def _d(value: float, width: int = 25, digits: int = 16) -> str:
    return f"{value:{width}.{digits}E}".replace("E", "D")


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    lines = [
        f"{-1:6d}",
        f"{164:6d}",
        f"{5:10d}{'mm (milli-newton)':>20s}{2:10d}",
        _d(1000.0) + _d(1000.0) + _d(1.0),
        _d(273.15),
        f"{-1:6d}",
        f"{-1:6d}",
        f"{2411:6d}",
    ]
    for i, (x, y, z) in enumerate(mesh.vertices, start=1):
        lines.append(f"{i:10d}{1:10d}{1:10d}{11:10d}")
        lines.append(_d(float(x)) + _d(float(y)) + _d(float(z)))
    lines += [f"{-1:6d}", f"{-1:6d}", f"{2412:6d}"]
    for i, (a, b, c) in enumerate(mesh.triangles, start=1):
        lines.append(f"{i:10d}{91:10d}{1:10d}{1:10d}{7:10d}{3:10d}")
        lines.append(f"{a + 1:10d}{b + 1:10d}{c + 1:10d}")
    lines.append(f"{-1:6d}")
    job.path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return [job.path]
