"""PDF 1.7 (ISO 32000-1), one vector page at 1:1.

A minimal writer: catalog, page, one Flate-compressed content stream, document info. The
content stream scales millimetres to points with a single `cm` operator, so path data holds
the model's own coordinates; the MediaBox is the drawing's extents plus margin, in points.
"""

from __future__ import annotations

import zlib
from pathlib import Path

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob
from gcad.exporters.drawing import LINE_WIDTH_MM, MM_TO_PT, drawing


def _n(x: float) -> str:
    return format_number(float(x))


def _text(value: str) -> bytes:
    """A PDF text string: literal when ASCII, otherwise UTF-16BE hex."""
    if value.isascii():
        escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return f"({escaped})".encode("ascii")
    return b"<FEFF" + value.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def write(job: ExportJob) -> list[Path]:
    d = drawing(job)
    ops = [
        "q",
        f"{_n(MM_TO_PT)} 0 0 {_n(MM_TO_PT)} 0 0 cm",
        f"{_n(LINE_WIDTH_MM)} w 1 J 1 j 0 G",
    ]
    for contour in d.contours:
        (x0, y0), rest = contour[0], contour[1:]
        ops.append(f"{_n(x0)} {_n(y0)} m")
        ops.extend(f"{_n(x)} {_n(y)} l" for x, y in rest)
        ops.append("h")
    ops += ["S", "Q"]
    content = zlib.compress("\n".join(ops).encode("ascii"), 9)
    lo, hi = d.lo * MM_TO_PT, d.hi * MM_TO_PT
    media = f"[{_n(lo[0])} {_n(lo[1])} {_n(hi[0])} {_n(hi[1])}]"
    stamp = job.time.strftime("D:%Y%m%d%H%M%SZ")
    info = (
        b"<< /Title "
        + _text(job.title)
        + b" /Creator "
        + _text(job.generator)
        + b" /Producer "
        + _text(job.generator)
        + b" /Subject "
        + _text(f"1:1, millimetres; source {Path(job.source).name}" if job.source else "1:1, mm")
        + f" /CreationDate ({stamp}) /ModDate ({stamp}) >>".encode("ascii")
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox {media} /Resources << >> /Contents 4 0 R >>"
        ).encode("ascii"),
        f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
        + content
        + b"\nendstream",
        info,
    ]
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    job.path.write_bytes(bytes(out))
    return [job.path]
