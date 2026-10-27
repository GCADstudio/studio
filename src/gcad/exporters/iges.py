"""IGES 5.3 in B-rep solid mode (manifold solid B-rep object, entity 186), via OpenCASCADE.

OpenCASCADE fills the global section's author from the login name and stamps the
last-change date with the current time, offering no way to set it. The author is
cleared before writing; the date is patched afterwards, in place and at the same length.
"""

from __future__ import annotations

import re
from pathlib import Path

from OCP.IGESControl import IGESControl_Writer
from OCP.TCollection import TCollection_HAsciiString

from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob

UNITS = {"mm": "MM", "cm": "CM", "m": "M", "in": "IN"}
DATE = re.compile(r"\d{8}\.\d{6}")


def _h(text: str) -> TCollection_HAsciiString:
    return TCollection_HAsciiString(text)


def _patch_global_dates(path: Path, stamp: str) -> None:
    """Replace every date in the global (G) section by `stamp`, keeping all columns."""
    lines = path.read_bytes().decode("ascii").split("\n")
    g_rows = [i for i, line in enumerate(lines) if len(line.rstrip("\r")) >= 73 and line[72] == "G"]
    stream = "".join(lines[i][:72] for i in g_rows)
    patched = DATE.sub(stamp, stream)
    for n, i in enumerate(g_rows):
        lines[i] = patched[n * 72 : (n + 1) * 72] + lines[i][72:]
    path.write_bytes("\n".join(lines).encode("ascii"))


def write(job: ExportJob) -> list[Path]:
    shape = getattr(job.model, "shape", None)
    if shape is None:
        raise GcadError("the model is empty; there is nothing to export")
    unit = UNITS.get(job.graph.units)
    if unit is None:
        raise GcadError(f"IGES export does not know the unit '{job.graph.units}'")
    writer = IGESControl_Writer(unit, 1)  # 1 = B-rep mode
    if not writer.AddShape(shape):
        raise GcadError("OpenCASCADE could not translate the model to IGES")
    writer.ComputeModel()
    model = writer.Model()
    section = model.GlobalSection()
    stamp = job.time.strftime("%Y%m%d.%H%M%S")
    section.SetFileName(_h(job.path.name))
    section.SetSendName(_h(getattr(job.model, "name", None) or job.title))
    section.SetSystemId(_h(job.generator))
    section.SetAuthorName(_h(""))
    section.SetCompanyName(_h(""))
    section.SetDate(_h(stamp))
    model.SetGlobalSection(section)
    if not writer.Write(str(job.path)):
        raise GcadError(f"OpenCASCADE could not write {job.path.name}")
    _patch_global_dates(job.path, stamp)
    return [job.path]
