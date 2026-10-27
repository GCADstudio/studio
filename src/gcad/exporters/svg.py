"""SVG 1.1 at 1:1: width and height in mm, one user unit = 1 mm.

SVG's y axis points down; a single scale(1,-1) group keeps the model's own coordinates in
the path data, so a reader that applies transforms gets them back exactly.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob
from gcad.exporters.drawing import LINE_WIDTH_MM, drawing


def _n(x: float) -> str:
    return format_number(float(x))


def write(job: ExportJob) -> list[Path]:
    d = drawing(job)
    w, h = d.size
    path = " ".join(
        "M " + " L ".join(f"{_n(x)} {_n(y)}" for x, y in contour) + " Z" for contour in d.contours
    )
    desc = f"{job.generator}; 1 user unit = 1 mm" + (
        f"; source {Path(job.source).name}" if job.source else ""
    )
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{_n(w)}mm" '
        f'height="{_n(h)}mm" viewBox="{_n(d.lo[0])} {_n(-d.hi[1])} {_n(w)} {_n(h)}">',
        f"  <title>{escape(job.title)}</title>",
        f"  <desc>{escape(desc)}</desc>",
        f'  <g id="model" transform="scale(1,-1)" fill="none" stroke="#000000" '
        f'stroke-width="{_n(LINE_WIDTH_MM)}" stroke-linejoin="round" fill-rule="evenodd">',
        f'    <path d="{path}"/>',
        "  </g>",
        "</svg>",
    ]
    job.path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return [job.path]
