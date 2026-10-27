"""Encapsulated PostScript (EPSF 3.0, DSC 3.0, PostScript Level 2) at 1:1.

As in the PDF writer, one `scale` turns millimetres into points and the path data keeps the
model's own coordinates. The bounding box is the drawing's extents plus margin, in points.
"""

from __future__ import annotations

import math
from pathlib import Path

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob
from gcad.exporters.drawing import LINE_WIDTH_MM, MM_TO_PT, drawing


def _n(x: float) -> str:
    return format_number(float(x))


def _dsc(value: str) -> str:
    """DSC comment text: printable ASCII on one line."""
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in value)


def write(job: ExportJob) -> list[Path]:
    d = drawing(job)
    lo, hi = d.lo * MM_TO_PT, d.hi * MM_TO_PT
    out = [
        "%!PS-Adobe-3.0 EPSF-3.0",
        f"%%BoundingBox: {math.floor(lo[0])} {math.floor(lo[1])} "
        f"{math.ceil(hi[0])} {math.ceil(hi[1])}",
        f"%%HiResBoundingBox: {_n(lo[0])} {_n(lo[1])} {_n(hi[0])} {_n(hi[1])}",
        f"%%Title: {_dsc(job.title)}",
        f"%%Creator: {_dsc(job.generator)}",
        f"%%CreationDate: {job.time.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "%%LanguageLevel: 2",
        "%%Pages: 1",
        "%%EndComments",
        f"% 1:1, millimetres{'; source ' + _dsc(Path(job.source).name) if job.source else ''}",
        "%%Page: 1 1",
        "gsave",
        f"{_n(MM_TO_PT)} dup scale",
        f"{_n(LINE_WIDTH_MM)} setlinewidth 1 setlinecap 1 setlinejoin 0 setgray",
        "newpath",
    ]
    for contour in d.contours:
        (x0, y0), rest = contour[0], contour[1:]
        out.append(f"{_n(x0)} {_n(y0)} moveto")
        out.extend(f"{_n(x)} {_n(y)} lineto" for x, y in rest)
        out.append("closepath")
    out += ["stroke", "grestore", "showpage", "%%Trailer", "%%EOF"]
    job.path.write_text("\n".join(out) + "\n", encoding="ascii", newline="\n")
    return [job.path]
