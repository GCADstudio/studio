"""Back-end M: mesh booleans on Manifold (https://github.com/elalish/manifold).

Every primitive instance is tagged with Manifold's original-id mechanism, so each output
triangle traces back to the graph node that created it and the colour it was given (FR-B1).
"""

from __future__ import annotations

import math
import sys
from functools import reduce
from typing import Any, TypeAlias

import numpy as np

if sys.platform == "emscripten":
    # In the browser (SPEC §4.1): Manifold's own WebAssembly build behind the same API.
    from gcad.backends import manifold_js as mf
else:
    import manifold3d as mf

from gcad.core.graph import Graph, Node, circle_segments, default_tolerance, node_matrix
from gcad.diagnostics import GcadError
from gcad.mesh import RGBA, Mesh, MeshGroup

# manifold3d ships no type stubs; a shape is a Manifold (3D) or a CrossSection (2D).
Shape: TypeAlias = Any


class ManifoldModel:
    def __init__(
        self,
        shape: Shape,
        dim: int,
        origins: dict[int, MeshGroup],
        root_id: str,
        faces: dict[int, list[str | None]] | None = None,
    ) -> None:
        self.shape = shape
        self.dim = dim
        self.origins = origins
        self.root_id = root_id
        #: for an imported mesh, by its original id: the source entity of each face id
        self.faces = faces or {}
        #: imports left out of a preview, with the reason (`realize(skip_imports=True)`)
        self.skipped: list[GcadError] = []

    def mesh(self) -> Mesh:
        if self.dim != 3:
            raise GcadError("a 2D model has no 3D mesh; use a 2D export format")
        m = self.shape.to_mesh64()
        props = np.asarray(m.vert_properties, dtype=np.float64)
        vertices = props[:, :3].copy() if props.size else np.zeros((0, 3))
        triangles = np.asarray(m.tri_verts, dtype=np.int64).reshape(-1, 3)
        run_index = np.asarray(m.run_index, dtype=np.int64)
        run_ids = np.asarray(m.run_original_id, dtype=np.int64)
        # (the browser build of Manifold returns no face ids; it imports no B-rep either)
        face_id = np.asarray(getattr(m, "face_id", []), dtype=np.int64)
        groups: list[MeshGroup] = []
        tri_group = np.zeros(len(triangles), dtype=np.int64)
        for r, original in enumerate(run_ids):
            group = self.origins.get(int(original), MeshGroup(self.root_id, None))
            lo, hi = run_index[r] // 3, run_index[r + 1] // 3
            entities = self.faces.get(int(original))
            if entities is None or len(face_id) != len(triangles):
                if group not in groups:
                    groups.append(group)
                tri_group[lo:hi] = groups.index(group)
                continue
            # An imported B-rep keeps a group per face, labelled with the face's entity.
            for f in np.unique(face_id[lo:hi]):
                entity = entities[f] if 0 <= f < len(entities) else None
                g = MeshGroup(group.node_id, group.color, entity)
                if g not in groups:
                    groups.append(g)
                tri_group[lo + np.flatnonzero(face_id[lo:hi] == f)] = groups.index(g)
        return Mesh(vertices, triangles, groups, tri_group)

    def contours(self) -> list[np.ndarray]:
        """Closed 2D outlines in mm: outer boundaries counter-clockwise, holes clockwise."""
        if self.dim != 2:
            raise GcadError("a 3D model has no 2D outline; wrap it in section or project")
        # Slicing leaves collinear points on straight edges; drop only those (1 nm).
        clean = self.shape.simplify(1e-6)
        return [np.asarray(c, dtype=np.float64) for c in clean.to_polygons() if len(c) >= 3]

    def volume(self) -> float:
        return float(self.shape.volume()) if self.dim == 3 else 0.0

    def area(self) -> float:
        return float(self.shape.surface_area() if self.dim == 3 else self.shape.area())

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.shape.is_empty():
            return None
        if self.dim == 3:
            b = self.shape.bounding_box()
            return np.array(b[:3]), np.array(b[3:])
        b = self.shape.bounds()
        return np.array([b[0], b[1], 0.0]), np.array([b[2], b[3], 0.0])

    def is_valid(self) -> bool:
        if self.dim == 3:
            # (Manifold's JS build marks the empty solid it makes from a zero cube as invalid)
            return self.shape.is_empty() or bool(self.shape.status() == mf.Error.NoError)
        return True


class _Realizer:
    def __init__(
        self, tolerance: float, blobs: dict[str, bytes] | None = None, skip_imports: bool = False
    ) -> None:
        self.tolerance = tolerance
        #: a preview without OpenCASCADE leaves imported B-rep out (and says so) rather than
        #: failing, so what else the source carries (its annotations) can still be shown
        self.skip_imports = skip_imports
        self.skipped: list[GcadError] = []
        self.origins: dict[int, MeshGroup] = {}
        self.faces: dict[int, list[str | None]] = {}
        self.blobs = blobs or {}

    def tag(self, m: mf.Manifold, node: Node, color: RGBA | None) -> mf.Manifold:
        m = m.as_original()
        self.origins[int(m.original_id())] = MeshGroup(node.id, color)
        return m

    def segments(self, node: Node, r: float) -> int:
        return circle_segments(r, node.num("fn"), node.num("fa"), node.num("fs"), self.tolerance)

    def union(self, parts: list[Shape], dim: int) -> Shape:
        cls = mf.Manifold if dim == 3 else mf.CrossSection
        if not parts:
            return cls()
        if len(parts) == 1:
            return parts[0]
        return cls.batch_boolean(parts, mf.OpType.Add)

    def build(self, node: Node, color: RGBA | None) -> Shape:
        op = node.op
        if node.dim == 3 and op in ("box", "sphere", "cylinder", "polyhedron"):
            return self.tag(self.primitive3(node), node, color)
        if op == "import":
            return self.imported(node, color)
        if op == "rect":
            return mf.CrossSection.square(node.vec("size"), bool(node.arg("center")))
        if op == "circle":
            r = node.num("r")
            return mf.CrossSection.circle(r, self.segments(node, r))
        if op == "polygon":
            return self.polygon(node)
        if op == "color":
            rgba = tuple(node.vec("rgba"))
            color = (rgba[0], rgba[1], rgba[2], rgba[3])
        parts = [self.build(c, color) for c in node.children]
        if op == "section":
            return self.union(parts, 3).slice(node.num("z"))
        if op == "project":
            return self.union(parts, 3).project()
        if op in ("linear_extrude", "rotate_extrude"):
            section = self.union(parts, 2)
            return self.tag(self.extrude(node, section), node, color)
        if op == "difference":
            head = parts[0]
            rest = self.union(parts[1:], node.dim)
            return head - rest
        if op == "intersection":
            return reduce(lambda a, b: a ^ b, parts)
        merged = self.union(parts, node.dim)
        m = node_matrix(node)
        if m is None:  # union, color, name
            return merged
        if node.dim == 3:
            return merged.transform(m[:3, :4])
        return merged.transform(m[np.ix_([0, 1], [0, 1, 3])])

    def primitive3(self, node: Node) -> mf.Manifold:
        op = node.op
        if op == "box":
            size = node.vec("size")
            if min(size) <= 0:
                return mf.Manifold()
            return mf.Manifold.cube(size, bool(node.arg("center")))
        if op == "sphere":
            r = node.num("r")
            return mf.Manifold.sphere(r, self.segments(node, r))
        if op == "cylinder":
            r1, r2 = node.num("r1"), node.num("r2")
            return mf.Manifold.cylinder(
                node.num("h"), r1, r2, self.segments(node, max(r1, r2)), bool(node.arg("center"))
            )
        return self.polyhedron(node)

    def imported(self, node: Node, color: RGBA | None) -> mf.Manifold:
        """Explicit B-rep carried by the source (an `import` node): OpenCASCADE reads and
        tessellates it, as for any mesh export from back-end B. Without OpenCASCADE (the web
        studio, Windows on ARM) there is nothing that can read it. Each face keeps its own id
        through booleans, labelled with the file entity it came from."""
        try:
            from gcad.backends.occt_backend import OcctModel, read_step, step_face_names
        except ImportError:
            error = GcadError(
                "explicit B-rep geometry is read by OpenCASCADE (back-end occt), which is not "
                "available here; the browser studio shows CSG and procedural models only",
                node.span,
            )
            if self.skip_imports:
                self.skipped.append(error)
                return mf.Manifold()
            raise error from None
        digest = str(node.arg("data"))
        if digest not in self.blobs:
            raise GcadError("the data this import reads is not in the graph", node.span)
        entity = int(node.num("entity"))
        shape = read_step(self.blobs[digest], entity, digest, node.span)
        names = step_face_names(digest, entity)
        mesh = OcctModel(shape, 3, self.tolerance, node.id, None, None, names).mesh()
        original = int(mf.Manifold.reserve_ids(1))
        try:
            m = mf.Manifold(
                mf.Mesh(
                    vert_properties=np.array(mesh.vertices, dtype=np.float32),
                    tri_verts=np.array(mesh.triangles, dtype=np.uint32).reshape(-1, 3),
                    run_index=np.array([0, 3 * len(mesh.triangles)], dtype=np.uint32),
                    run_original_id=np.array([original], dtype=np.uint32),
                    face_id=np.array(mesh.tri_group, dtype=np.uint32),
                )
            )
        except Exception as exc:  # manifold3d raises its own error types
            raise GcadError(f"the imported B-rep is not a closed solid: {exc}", node.span) from None
        if m.status() != mf.Error.NoError:
            raise GcadError(
                "the imported B-rep is not a closed solid, so the mesh back-end cannot use it in "
                "booleans; use --backend occt",
                node.span,
            )
        self.origins[original] = MeshGroup(node.id, color)
        self.faces[original] = [g.entity for g in mesh.groups]
        return m

    def polyhedron(self, node: Node) -> mf.Manifold:
        points = np.asarray(node.rows("points"), dtype=np.float32)
        tris: list[list[int]] = []
        for face in node.rows("faces"):
            idx = [int(i) for i in face]
            # OpenSCAD lists face points clockwise seen from outside; Manifold wants CCW.
            idx.reverse()
            tris.extend([idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1))
        mesh = mf.Mesh(vert_properties=points, tri_verts=np.asarray(tris, dtype=np.uint32))
        result = mf.Manifold(mesh)
        if result.status() != mf.Error.NoError:
            raise GcadError(
                f"polyhedron is not a closed, consistently oriented solid ({result.status().name})",
                node.span,
            )
        return result

    def polygon(self, node: Node) -> mf.CrossSection:
        points = [tuple(p) for p in node.rows("points")]
        if node.arg("paths") is None:
            contours = [points]
        else:
            contours = [[points[int(i)] for i in path] for path in node.rows("paths")]
        return mf.CrossSection(contours, mf.FillRule.EvenOdd)

    def extrude(self, node: Node, section: mf.CrossSection) -> mf.Manifold:
        if node.op == "linear_extrude":
            h = node.num("height")
            twist = node.num("twist")
            slices = int(node.num("slices"))
            if slices == 0:
                slices = max(1, math.ceil(abs(twist) / 5)) if twist else 1
            sx, sy = node.vec("scale")
            # GCore follows OpenSCAD: a positive twist turns clockwise seen from +z.
            m = mf.Manifold.extrude(section, h, slices - 1, -twist, (sx, sy))
            return m.translate([0, 0, -h / 2]) if node.arg("center") else m
        bounds = section.bounds()
        reach = max(abs(bounds[0]), abs(bounds[2])) if not section.is_empty() else 0.0
        segments = self.segments(node, reach) if reach > 0 else 3
        return mf.Manifold.revolve(section, segments, node.num("angle"))


class ManifoldBackend:
    name = "manifold"
    capabilities = frozenset({"mesh", "2d"})

    def realize(
        self, graph: Graph, tolerance: float | None = None, skip_imports: bool = False
    ) -> ManifoldModel:
        tol = tolerance if tolerance is not None else default_tolerance(graph)
        if tol <= 0:
            raise GcadError("tessellation tolerance must be positive")
        realizer = _Realizer(tol, graph.blobs, skip_imports)
        shape = realizer.build(graph.root, None)
        model = ManifoldModel(
            shape, graph.root.dim, realizer.origins, graph.root.id, realizer.faces
        )
        model.skipped = realizer.skipped
        return model
