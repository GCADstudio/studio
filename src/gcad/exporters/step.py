"""STEP (ISO 10303-21), AP214 by default or AP242, through OpenCASCADE's XCAF writer.

The product is named after the model and carries its colour when the whole model has one.
Header fields that OpenCASCADE would fill from the machine (author, organisation, time)
are set explicitly, so a file reveals nothing about who exported it and is reproducible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_DocumentTool

from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob

SCHEMAS = {"AP214": "AP214IS", "AP242": "AP242DIS", "AP203": "AP203"}
UNITS = {"mm": "MM", "cm": "CM", "m": "M", "in": "INCH"}


def _h(text: str) -> TCollection_HAsciiString:
    return TCollection_HAsciiString(text)


def xcaf_document(job: ExportJob) -> Any:
    """An XCAF document holding the model as one named, optionally coloured, product."""
    shape = getattr(job.model, "shape", None)
    if shape is None:
        raise GcadError("the model is empty; there is nothing to export")
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    label = shapes.AddShape(shape, False)
    name = getattr(job.model, "name", None) or job.title
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    color = getattr(job.model, "color", None)
    if color is not None:
        colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        colors.SetColor(label, Quantity_Color(*color[:3], Quantity_TOC_RGB), XCAFDoc_ColorGen)
    return doc


def write(job: ExportJob) -> list[Path]:
    schema = str(job.options.get("schema") or "AP214").upper()
    if schema not in SCHEMAS:
        raise GcadError(f"STEP schema must be one of {', '.join(SCHEMAS)}")
    unit = UNITS.get(job.graph.units)
    if unit is None:
        raise GcadError(f"STEP export does not know the unit '{job.graph.units}'")
    # The write.step.* parameters exist only once the STEP controller is initialised; before
    # that OpenCASCADE ignores them silently.
    STEPControl_Controller.Init_s()
    for key, value in (("write.step.schema", SCHEMAS[schema]), ("write.step.unit", unit)):
        if not Interface_Static.SetCVal_s(key, value):
            raise GcadError(f"OpenCASCADE rejected {key} = {value}")

    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    if not writer.Transfer(xcaf_document(job)):
        raise GcadError("OpenCASCADE could not translate the model to STEP")
    header = APIHeaderSection_MakeHeader(writer.ChangeWriter().Model())
    header.SetName(_h(job.path.name))
    header.SetTimeStamp(_h(job.time.strftime("%Y-%m-%dT%H:%M:%S")))
    header.SetAuthorValue(1, _h(""))
    header.SetOrganizationValue(1, _h(""))
    header.SetOriginatingSystem(_h(job.generator))
    header.SetAuthorisation(_h(""))
    source = Path(job.source).name
    header.SetDescriptionValue(1, _h(f"{job.title}" + (f" from {source}" if source else "")))
    if writer.Write(str(job.path)) != IFSelect_RetDone:
        raise GcadError(f"OpenCASCADE could not write {job.path.name}")
    return [job.path]
