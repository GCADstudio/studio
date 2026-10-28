"""Triangle meshes handed from back-ends to mesh exporters and the viewer."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RGBA = tuple[float, float, float, float]


@dataclass(frozen=True)
class MeshGroup:
    """Triangles that came from one primitive instance: its graph node and its colour."""

    node_id: str
    color: RGBA | None
    #: the source entity the triangles came from, when a back-end knows it: the `#n` of an
    #: imported STEP face. Lets the studio point a face back at its line (FR-U7).
    entity: str | None = None


@dataclass
class Mesh:
    vertices: np.ndarray  # (N, 3) float64, document units
    triangles: np.ndarray  # (M, 3) int64, counter-clockwise seen from outside
    groups: list[MeshGroup]
    tri_group: np.ndarray  # (M,) int64 index into groups

    @property
    def triangle_count(self) -> int:
        return int(len(self.triangles))

    def face_normals(self) -> np.ndarray:
        v = self.vertices[self.triangles]
        n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        return np.divide(n, length, out=np.zeros_like(n), where=length > 0)

    def volume(self) -> float:
        v = self.vertices[self.triangles]
        return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def color_runs(self) -> list[tuple[RGBA | None, np.ndarray]]:
        """Triangle indices per distinct colour, in order of first appearance."""
        colors: list[RGBA | None] = []
        for g in self.groups:
            if g.color not in colors:
                colors.append(g.color)
        group_color = np.array([colors.index(g.color) for g in self.groups], dtype=np.int64)
        tri_color = group_color[self.tri_group] if len(self.groups) else np.zeros(0, np.int64)
        return [(c, np.flatnonzero(tri_color == i)) for i, c in enumerate(colors)]
