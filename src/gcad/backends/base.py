"""The back-end contract (FR-B1)."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from gcad.core.graph import Graph
from gcad.mesh import Mesh


class RealizedModel(Protocol):
    dim: int

    def mesh(self) -> Mesh: ...

    def contours(self) -> list[np.ndarray]: ...

    def volume(self) -> float: ...

    def area(self) -> float: ...

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None: ...

    def is_valid(self) -> bool: ...


class Backend(Protocol):
    name: str
    #: "mesh" for tessellated output, "brep" for exact geometry (STEP, IGES), "2d" for
    #: closed outlines of 2D models and sections (DXF, SVG, PDF, EPS, CGM).
    capabilities: frozenset[str]

    def realize(self, graph: Graph, tolerance: float | None = None) -> RealizedModel: ...
