"""CGM, ISO/IEC 8632 version 1, binary encoding (ISO/IEC 8632-3).

VDCs are reals in the default 16.16 fixed-point precision (resolution 1/65536 mm, range
±32767 mm); SCALING MODE is metric at 1 mm per VDC unit, so the picture is 1:1. Each outline
is a POLYGON with the default hollow interior, i.e. drawn as a closed line.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob
from gcad.exporters.drawing import drawing

MAX_PARTITION = 32766  # even, below the 15-bit length limit


def _element(cls: int, eid: int, params: bytes = b"") -> bytes:
    """One element: short form for fewer than 31 parameter bytes, otherwise long form split
    into partitions. Parameter data is padded to an even length."""
    if len(params) < 31:
        short = struct.pack(">H", (cls << 12) | (eid << 5) | len(params)) + params
        return short + (b"\x00" if len(params) % 2 else b"")
    out = bytearray(struct.pack(">H", (cls << 12) | (eid << 5) | 31))
    for start in range(0, len(params), MAX_PARTITION):
        chunk = params[start : start + MAX_PARTITION]
        more = start + MAX_PARTITION < len(params)
        out += struct.pack(">H", (0x8000 if more else 0) | len(chunk)) + chunk
    if len(params) % 2:
        out += b"\x00"
    return bytes(out)


def _string(text: str) -> bytes:
    data = text.encode("latin-1", errors="replace")
    if len(data) < 255:
        return bytes([len(data)]) + data
    return b"\xff" + struct.pack(">H", len(data)) + data


def _int(value: int) -> bytes:
    return struct.pack(">h", value)


def _fixed(value: float) -> bytes:
    """16.16 fixed point: signed whole part, unsigned fraction."""
    whole = math.floor(value)
    if not -32768 <= whole <= 32767:
        raise GcadError("CGM coordinates must lie within ±32767 mm")
    fraction = round((value - whole) * 65536)
    if fraction == 65536:
        whole, fraction = whole + 1, 0
    return struct.pack(">hH", whole, fraction)


def _point(x: float, y: float) -> bytes:
    return _fixed(x) + _fixed(y)


ELEMENTS_USED = [
    (0, 1), (0, 2), (0, 3), (0, 4), (0, 5),
    (1, 1), (1, 2), (1, 3), (1, 5), (1, 11),
    (2, 1), (2, 6),
    (4, 7),
]  # fmt: skip


def write(job: ExportJob) -> list[Path]:
    d = drawing(job)
    description = f"{job.generator}; 1:1, millimetres" + (
        f"; source {Path(job.source).name}" if job.source else ""
    )
    element_list = _int(len(ELEMENTS_USED)) + b"".join(_int(c) + _int(e) for c, e in ELEMENTS_USED)
    out = bytearray()
    out += _element(0, 1, _string(job.title))  # BEGIN METAFILE
    out += _element(1, 1, _int(1))  # METAFILE VERSION 1
    out += _element(1, 2, _string(description))  # METAFILE DESCRIPTION
    out += _element(1, 3, _int(1))  # VDC TYPE real
    out += _element(1, 5, _int(0) + _int(9) + _int(23))  # REAL PRECISION: IEEE single
    out += _element(1, 11, element_list)  # METAFILE ELEMENT LIST
    out += _element(0, 3, _string(job.title))  # BEGIN PICTURE
    out += _element(2, 1, _int(1) + struct.pack(">f", 1.0))  # SCALING MODE metric, 1 mm
    out += _element(2, 6, _point(*d.lo) + _point(*d.hi))  # VDC EXTENT
    out += _element(0, 4)  # BEGIN PICTURE BODY
    for contour in d.contours:
        out += _element(4, 7, b"".join(_point(float(x), float(y)) for x, y in contour))
    out += _element(0, 5)  # END PICTURE
    out += _element(0, 2)  # END METAFILE
    job.path.write_bytes(bytes(out))
    return [job.path]
