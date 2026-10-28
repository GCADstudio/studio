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


def weld(
    points: np.ndarray, triangles: np.ndarray, tolerance: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Merge points closer than a ten-thousandth of the tolerance: a B-rep's faces are meshed
    each with its own copy of their shared edges. Returns the vertices in order of first use,
    the triangles on them, and which input triangles were kept (not collapsed to a line)."""
    raw = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    key = np.round(raw / max(tolerance * 1e-4, 1e-9)).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    vertices = raw[first[order]]
    tris = rank[inverse.ravel()][np.asarray(triangles, dtype=np.int64).reshape(-1, 3)]
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return vertices, tris[keep], keep
