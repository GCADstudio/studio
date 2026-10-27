"""X3D 3.3, XML encoding (ISO/IEC 19775-1, 19776-1).

Coordinates stay in millimetres; the X3D 3.3 UNIT statement declares that. X3D is +Y up,
so one Transform turns the CAD +Z axis up. One Shape per colour.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob

DEFAULT_RGBA = (0.8, 0.8, 0.8, 1.0)
Z_UP_TO_Y_UP = "1 0 0 -1.5707963267948966"


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE X3D PUBLIC "ISO//Web3D//DTD X3D 3.3//EN" '
        '"http://www.web3d.org/specifications/x3d-3.3.dtd">',
        '<X3D profile="Interchange" version="3.3" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema-instance" '
        'xsd:noNamespaceSchemaLocation="http://www.web3d.org/specifications/x3d-3.3.xsd">',
        "  <head>",
        '    <unit category="length" name="millimetre" conversionFactor="0.001"/>',
        f'    <meta name="title" content={quoteattr(job.title)}/>',
        f'    <meta name="generator" content={quoteattr(job.generator)}/>',
    ]
    if job.source:
        out.append(f'    <meta name="source" content={quoteattr(Path(job.source).name)}/>')
    out += ["  </head>", "  <Scene>", f'    <Transform rotation="{Z_UP_TO_Y_UP}">']
    points = " ".join(" ".join(format_number(float(c)) for c in v) for v in mesh.vertices)
    for i, (color, tris) in enumerate(mesh.color_runs()):
        r, g, b, a = color or DEFAULT_RGBA
        index = " ".join(str(int(k)) for k in mesh.triangles[tris].ravel())
        coord = (
            f'<Coordinate DEF="points" point="{points}"/>'
            if i == 0
            else '<Coordinate USE="points"/>'
        )
        out += [
            "      <Shape>",
            "        <Appearance>",
            f'          <Material diffuseColor="{format_number(round(r, 6))} '
            f'{format_number(round(g, 6))} {format_number(round(b, 6))}" '
            f'transparency="{format_number(round(1 - a, 6))}"/>',
            "        </Appearance>",
            f'        <IndexedTriangleSet solid="true" ccw="true" index="{index}">',
            f"          {coord}",
            "        </IndexedTriangleSet>",
            "      </Shape>",
        ]
    out += ["    </Transform>", "  </Scene>", "</X3D>"]
    job.path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return [job.path]
