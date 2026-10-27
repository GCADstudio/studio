"""DXF through ezdxf: R2000 (AC1015) by default, R12 (AC1009) for older readers.

Each outline is a closed LWPOLYLINE (POLYLINE in R12) on layer GCAD, in millimetres
($INSUNITS 4). ezdxf writes random GUIDs and its own timestamps; they are fixed here, and
the creation dates set to the export time, so the file is reproducible (EX-2).
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import ezdxf
from ezdxf import units, zoom

from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob
from gcad.exporters.drawing import Drawing, drawing

VERSIONS = ("R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018")
UNIX_EPOCH_JULIAN_DAY = 2440587.5


def write(job: ExportJob) -> list[Path]:
    version = str(job.options.get("dxf_version") or "R2000").upper()
    if version not in VERSIONS:
        raise GcadError(f"DXF version must be one of {', '.join(VERSIONS)}")
    d = drawing(job)
    # ezdxf stamps its metadata when the document is created and again when it is written;
    # its fixed-metadata option must cover both.
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        text = _build(job, d, version)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    job.path.write_bytes(text)
    return [job.path]


def _build(job: ExportJob, d: Drawing, version: str) -> bytes:
    doc = ezdxf.new(version, setup=False)
    if version != "R12":  # R12 has no units header variable
        doc.units = units.MM
        doc.header["$MEASUREMENT"] = 1  # metric
    doc.layers.add("GCAD", color=7)
    msp = doc.modelspace()
    attribs = {"layer": "GCAD"}
    for contour in d.contours:
        points = [(float(x), float(y)) for x, y in contour]
        if version == "R12":
            msp.add_polyline2d(points, close=True, dxfattribs=attribs)
        else:
            msp.add_lwpolyline(points, close=True, dxfattribs=attribs)
    extents = [c for contour in d.contours for c in contour]
    xs, ys = [p[0] for p in extents], [p[1] for p in extents]
    lo = (float(min(xs)), float(min(ys)), 0.0)
    hi = (float(max(xs)), float(max(ys)), 0.0)
    # Readers such as LibreOffice scale the drawing to $EXTMIN/$EXTMAX. On save ezdxf copies
    # the modelspace extents into the header, but only when both are non-zero vectors; a
    # drawing that starts at the origin would keep its "unset" +-1e20 defaults. Set both.
    msp.dxf.extmin, msp.dxf.extmax = lo, hi
    doc.header["$EXTMIN"], doc.header["$EXTMAX"] = lo, hi
    zoom.extents(msp, factor=1.05)  # viewers open on the drawing

    stream = io.StringIO()
    doc.write(stream)
    lines = stream.getvalue().split("\n")

    julian = f"{job.time.timestamp() / 86400 + UNIX_EPOCH_JULIAN_DAY:.10f}"
    guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"gcad:{job.path.name}:{julian}")).upper() + "}"
    for i, line in enumerate(lines[:-2]):
        if line.strip() in ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"):
            lines[i + 2] = julian
        elif line.strip() in ("$FINGERPRINTGUID", "$VERSIONGUID"):
            lines[i + 2] = guid
    comment = f"{job.generator}; units mm" + (
        f"; source {Path(job.source).name}" if job.source else ""
    )
    text = "999\n" + comment + "\n" + "\n".join(lines)
    return text.encode(doc.output_encoding, errors="replace")
