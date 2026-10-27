"""STL, binary (default) and ASCII. Binary is little-endian whatever the host byte order."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    normals = mesh.face_normals()
    if job.options.get("ascii"):
        name = job.title.replace(" ", "_") or "gcad"
        lines = [f"solid {name}"]
        for tri, n in zip(mesh.triangles, normals, strict=True):
            lines.append("  facet normal " + " ".join(format_number(float(x)) for x in n))
            lines.append("    outer loop")
            for idx in tri:
                lines.append(
                    "      vertex " + " ".join(format_number(float(x)) for x in mesh.vertices[idx])
                )
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {name}")
        job.path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
        return [job.path]

    # The header must not start with "solid", or readers mistake the file for ASCII STL.
    header = f"{job.generator}; units=mm; source={Path(job.source).name}".encode("ascii", "replace")
    header = header[:80].ljust(80, b" ")
    record = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    data = np.zeros(mesh.triangle_count, dtype=record)
    data["normal"] = normals
    data["v"] = mesh.vertices[mesh.triangles]
    with job.path.open("wb") as fh:
        fh.write(header)
        fh.write(np.uint32(mesh.triangle_count).astype("<u4").tobytes())
        fh.write(data.tobytes())
    return [job.path]
