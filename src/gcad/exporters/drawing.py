"""What the 2D exporters share: the model's closed outlines in millimetres, their extents,
and how they are drawn.

Every 2D format is written at 1:1. Outlines are stroked with a 0.1 mm hairline and not
filled, as cutters and plotters expect; the page is the drawing's extents plus a 1 mm
margin so the line is not clipped. Coordinates are written unchanged (only scaled to points
where the format counts in points), so a reader gets the model's own numbers back.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gcad.diagnostics import GcadError
from gcad.exporters import ExportJob

MM_TO_PT = 72 / 25.4
LINE_WIDTH_MM = 0.1
MARGIN_MM = 1.0


@dataclass
class Drawing:
    contours: list[np.ndarray]  # each (N, 2), closed implicitly, mm
    lo: np.ndarray  # page corner, mm, extents minus margin
    hi: np.ndarray

    @property
    def size(self) -> np.ndarray:
        return self.hi - self.lo


def drawing(job: ExportJob) -> Drawing:
    contours = job.model.contours()
    if not contours:
        raise GcadError("the 2D model is empty; there is nothing to draw")
    points = np.vstack(contours)
    lo = points.min(axis=0) - MARGIN_MM
    hi = points.max(axis=0) + MARGIN_MM
    return Drawing(contours, lo, hi)
