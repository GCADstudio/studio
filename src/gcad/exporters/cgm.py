"""CGM, ISO/IEC 8632 version 1, binary encoding (ISO/IEC 8632-3).

VDCs are reals in IEEE double precision, set by a VDC REAL PRECISION in the METAFILE DEFAULTS
REPLACEMENT so that it covers the VDC EXTENT too. (The default 16.16 fixed point would do for
millimetres, but LibreOffice decodes its whole part as unsigned, so every negative coordinate
lands near +65536.) SCALING MODE is metric at 1 mm per VDC unit, so the picture is 1:1. Each
outline is a POLYGON with a hollow interior, i.e. its boundary drawn in the fill colour.

Colours are given explicitly, in direct (RGB) mode: black on white. The standard leaves the
defaults to the device, and LibreOffice's are white lines on a black background.
"""

from __future__ import annotations

import struct
from pathlib import Path

from gcad.exporters import ExportJob
from gcad.exporters.drawing import drawing

MAX_PARTITION = 32766  # even, below the 15-bit length limit
WHITE, BLACK = b"\xff\xff\xff", b"\x00\x00\x00"  # direct colour at the default 8-bit precision


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


def _point(x: float, y: float) -> bytes:
    return struct.pack(">dd", x, y)


ELEMENTS_USED = [
    (0, 1), (0, 2), (0, 3), (0, 4), (0, 5),
    (1, 1), (1, 2), (1, 3), (1, 5), (1, 11), (1, 12),
    (2, 1), (2, 2), (2, 6), (2, 7),
    (3, 2),
    (4, 7),
    (5, 22), (5, 23),
]  # fmt: skip


def write(job: ExportJob) -> list[Path]:
    d = drawing(job)
    description = f"{job.generator}; 1:1, millimetres" + (
        f"; source {Path(job.source).name}" if job.source else ""
    )
    element_list = _int(len(ELEMENTS_USED)) + b"".join(_int(c) + _int(e) for c, e in ELEMENTS_USED)
    vdc_real_precision = _element(3, 2, _int(0) + _int(12) + _int(52))  # IEEE double
    out = bytearray()
    out += _element(0, 1, _string(job.title))  # BEGIN METAFILE
    out += _element(1, 1, _int(1))  # METAFILE VERSION 1
    out += _element(1, 2, _string(description))  # METAFILE DESCRIPTION
    out += _element(1, 3, _int(1))  # VDC TYPE real
    out += _element(1, 5, _int(0) + _int(9) + _int(23))  # REAL PRECISION: IEEE single
    out += _element(1, 11, element_list)  # METAFILE ELEMENT LIST
    out += _element(1, 12, vdc_real_precision)  # METAFILE DEFAULTS REPLACEMENT
    out += _element(0, 3, _string(job.title))  # BEGIN PICTURE
    out += _element(2, 1, _int(1) + struct.pack(">f", 1.0))  # SCALING MODE metric, 1 mm
    out += _element(2, 2, _int(1))  # COLOUR SELECTION MODE direct
    out += _element(2, 6, _point(*d.lo) + _point(*d.hi))  # VDC EXTENT
    out += _element(2, 7, WHITE)  # BACKGROUND COLOUR
    out += _element(0, 4)  # BEGIN PICTURE BODY
    out += _element(5, 22, _int(0))  # INTERIOR STYLE hollow
    out += _element(5, 23, BLACK)  # FILL COLOUR, which a hollow boundary is drawn in
    for contour in d.contours:
        out += _element(4, 7, b"".join(_point(float(x), float(y)) for x, y in contour))
    out += _element(0, 5)  # END PICTURE
    out += _element(0, 2)  # END METAFILE
    job.path.write_bytes(bytes(out))
    return [job.path]
