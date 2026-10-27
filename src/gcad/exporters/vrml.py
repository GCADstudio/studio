"""VRML97 (ISO/IEC 14772-1), UTF-8 encoding.

VRML has no unit field; its length unit is the metre and +Y is up. As for glTF, vertex
data stays in millimetres and one Transform scales by 0.001 and turns +Z up.
"""

from __future__ import annotations

import json
from pathlib import Path

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob

DEFAULT_RGBA = (0.8, 0.8, 0.8, 1.0)


def _f(x: float) -> str:
    return format_number(round(float(x), 6))


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    info = [f"generator: {job.generator}", "units: millimetres scaled to metres by the Transform"]
    if job.source:
        info.append(f"source: {Path(job.source).name}")
    out = [
        "#VRML V2.0 utf8",
        f"# {job.generator}",
        f"WorldInfo {{ title {json.dumps(job.title)} info [ "
        + ", ".join(json.dumps(i) for i in info)
        + " ] }",
        "Transform {",
        "  scale 0.001 0.001 0.001",
        "  rotation 1 0 0 -1.5707963267948966",
        "  children [",
    ]
    points = ", ".join(" ".join(format_number(float(c)) for c in v) for v in mesh.vertices)
    for i, (color, tris) in enumerate(mesh.color_runs()):
        r, g, b, a = color or DEFAULT_RGBA
        coord = f"DEF points Coordinate {{ point [ {points} ] }}" if i == 0 else "USE points"
        index = ", ".join(f"{p} {q} {s} -1" for p, q, s in mesh.triangles[tris])
        out += [
            "    Shape {",
            f"      appearance Appearance {{ material Material {{ diffuseColor {_f(r)} {_f(g)} "
            f"{_f(b)} transparency {_f(1 - a)} }} }}",
            "      geometry IndexedFaceSet {",
            "        solid TRUE",
            "        ccw TRUE",
            f"        coord {coord}",
            f"        coordIndex [ {index} ]",
            "      }",
            "    }",
        ]
    out += ["  ]", "}"]
    job.path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return [job.path]
