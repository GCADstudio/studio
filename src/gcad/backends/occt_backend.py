"""Back-end B: exact B-rep on OpenCASCADE (https://dev.opencascade.org) through OCP.

Curved primitives are exact (true cylinders, spheres, revolutions), so STEP and IGES carry
analytic geometry. One exception follows OpenSCAD intent: a cylinder or circle with an
explicit $fn of 3 to 16 is the polygon OpenSCAD would make (a hex nut stays a hexagon).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_GTransform,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Sewing,
    BRepBuilderAPI_Transform,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepLib import BRepLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeRevol,
    BRepPrimAPI_MakeSphere,
)
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_GTrsf, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.Message import Message, Message_Gravity
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

try:  # OCP 8 (OCCT 8) exposes NCollection lists as generic containers
    from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
except ImportError:  # pragma: no cover - OCP 7.x
    from OCP.TopTools import TopTools_ListOfShape

from gcad.core.graph import ATTRIBUTE_OPS, Graph, Node, default_tolerance, node_matrix
from gcad.diagnostics import GcadError, Span
from gcad.mesh import RGBA, Mesh, MeshGroup

POLYGONAL_FN_MAX = 16  # an explicit $fn up to this makes a polygon, as in OpenSCAD

# OCCT reports transfer statistics (with absolute paths) on stdout; keep only failures.
_printers = Message.DefaultMessenger_s().Printers()
for _i in range(1, _printers.Size() + 1):
    _printers.Value(_i).SetTraceLevel(Message_Gravity.Message_Fail)


def _cast(kind: str, shape: TopoDS_Shape) -> Any:
    """TopoDS casts are functions in OCCT 8 and static methods (Name_s) before."""
    fn = getattr(TopoDS, kind, None) or getattr(TopoDS, kind + "_s")
    return fn(shape)


def _shapes(shape: TopoDS_Shape, kind: Any) -> list[Any]:
    out = []
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def _list(shapes: list[TopoDS_Shape]) -> TopTools_ListOfShape:
    lst = TopTools_ListOfShape()
    for s in shapes:
        lst.Append(s)
    return lst


def _unify(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Merge coplanar and co-cylindrical faces left behind by booleans."""
    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    return unify.Shape()


def _boolean(op: str, head: TopoDS_Shape, rest: list[TopoDS_Shape], node: Node) -> TopoDS_Shape:
    algo: Any = {"union": BRepAlgoAPI_Fuse, "difference": BRepAlgoAPI_Cut}[op]()
    algo.SetArguments(_list([head]))
    algo.SetTools(_list(rest))
    algo.SetRunParallel(False)  # deterministic output
    algo.Build()
    if not algo.IsDone():
        raise GcadError(f"OpenCASCADE could not compute the {op}", node.span)
    return _unify(algo.Shape())


def _transform(shape: TopoDS_Shape, m: np.ndarray) -> TopoDS_Shape:
    m3 = m[:3, :3]
    scale = abs(np.linalg.det(m3)) ** (1 / 3)
    if scale > 0 and np.allclose(m3 @ m3.T, np.eye(3) * scale**2, atol=1e-12 * max(1, scale**2)):
        t = gp_Trsf()
        t.SetValues(*(float(x) for x in m[:3, :4].ravel()))
        return BRepBuilderAPI_Transform(shape, t, True).Shape()
    gt = gp_GTrsf()
    for i in range(3):
        for j in range(4):
            gt.SetValue(i + 1, j + 1, float(m[i, j]))
    return BRepBuilderAPI_GTransform(shape, gt, True).Shape()


def _polygon_wire(points: list[tuple[float, float, float]]) -> Any:
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    return poly.Wire()


def _regular_polygon(r: float, n: int, z: float = 0.0) -> list[tuple[float, float, float]]:
    """Vertices at the same angles OpenSCAD and Manifold use, starting on +x."""
    return [
        (r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z) for i in range(n)
    ]


class OcctModel:
    def __init__(
        self,
        shape: TopoDS_Shape | None,
        dim: int,
        tolerance: float,
        root_id: str,
        color: RGBA | None,
        name: str | None,
        face_names: dict[int, str] | None = None,
    ) -> None:
        self.shape = shape
        #: the source entity of each face, by `_face_key`: triangles are grouped per face
        self.face_names = face_names
        self.dim = dim
        self.tolerance = tolerance
        self.root_id = root_id
        self.color = color
        self.name = name
        self._mesh: Mesh | None = None

    def mesh(self) -> Mesh:
        if self.dim != 3:
            raise GcadError("a 2D model has no 3D mesh; use a 2D export format")
        if self._mesh is None:
            self._mesh = self._triangulate()
        return self._mesh

    def _triangulate(self) -> Mesh:
        group = MeshGroup(self.root_id, self.color)
        if self.shape is None:
            return Mesh(
                np.zeros((0, 3)), np.zeros((0, 3), np.int64), [group], np.zeros(0, np.int64)
            )
        BRepMesh_IncrementalMesh(self.shape, self.tolerance, False, 0.5, False)
        points: list[tuple[float, float, float]] = []
        triangles: list[tuple[int, int, int]] = []
        groups = [group]
        tri_groups: list[int] = []
        for face_shape in _shapes(self.shape, TopAbs_FACE):
            g = 0
            if self.face_names is not None:
                entity = self.face_names.get(_face_key(face_shape))
                if entity is not None:
                    groups.append(MeshGroup(self.root_id, self.color, entity))
                    g = len(groups) - 1
            face = _cast("Face", face_shape)
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None:
                continue
            trsf = loc.Transformation()
            base = len(points)
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                points.append((p.X(), p.Y(), p.Z()))
            reverse = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if reverse:
                    b, c = c, b
                triangles.append((base + a - 1, base + b - 1, base + c - 1))
                tri_groups.append(g)
        # Faces carry their own copies of shared edge nodes: weld them.
        raw = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        key = np.round(raw / max(self.tolerance * 1e-4, 1e-9)).astype(np.int64)
        _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        order = np.argsort(first)
        rank = np.empty_like(order)
        rank[order] = np.arange(len(order))
        vertices = raw[first[order]]
        tris = rank[inverse.ravel()][np.asarray(triangles, dtype=np.int64).reshape(-1, 3)]
        keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
        tris = tris[keep]
        tri_group = np.asarray(tri_groups, dtype=np.int64).reshape(-1)[keep]
        used = sorted(set(tri_group.tolist()))
        if used != list(range(len(groups))):  # drop groups left without triangles
            remap = np.zeros(len(groups), dtype=np.int64)
            remap[used] = np.arange(len(used))
            groups = [groups[i] for i in used]
            tri_group = remap[tri_group]
        return Mesh(vertices, tris, groups, tri_group)

    def contours(self) -> list[np.ndarray]:
        raise GcadError("2D formats are written from the manifold back-end; use --backend auto")

    def volume(self) -> float:
        if self.shape is None or self.dim != 3:
            return 0.0
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(self.shape, props)
        return float(props.Mass())

    def area(self) -> float:
        if self.shape is None:
            return 0.0
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(self.shape, props)
        return float(props.Mass())

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.shape is None:
            return None
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(self.shape, box, False, False)
        lo, hi = box.CornerMin(), box.CornerMax()
        return np.array([lo.X(), lo.Y(), lo.Z()]), np.array([hi.X(), hi.Y(), hi.Z()])

    def is_valid(self) -> bool:
        return self.shape is None or bool(BRepCheck_Analyzer(self.shape).IsValid())


_STEP_CACHE: dict[tuple[str, int], TopoDS_Shape] = {}
#: the `#n` of each face OpenCASCADE made from a STEP file, by `_face_key`, per file and entity
_STEP_FACES: dict[tuple[str, int], dict[int, str]] = {}


def _face_key(face: TopoDS_Shape) -> int:
    """The same for every reference to one face: OCCT hashes its TShape and location."""
    return hash(face)


def instance_names(text: bytes) -> list[str]:
    """The instance names (`#n`) of a Part 21 file in the order they are written, which is
    the order OpenCASCADE numbers its entities in. Strings and comments are skipped."""
    import re

    names: list[str] = []
    # A statement starts the file, or follows a ';'. Strings may hold ';' and '#'.
    token = re.compile(rb"'(?:[^']|'')*'|/\*.*?\*/|;\s*#(\d+)\s*=", re.S)
    for m in token.finditer(text):
        if m.group(1) is not None:
            names.append("#" + m.group(1).decode("ascii"))
    return names


def _face_names(reader: Any, shape: TopoDS_Shape, data: bytes) -> dict[int, str]:
    """Which file instance each face of the transferred shape came from."""
    model = reader.StepModel()
    transfer = reader.WS().TransferReader()
    names = instance_names(data)
    if len(names) != model.NbEntities():  # the file is not as this reader understands it
        return {}
    index = {model.Value(i).This(): i for i in range(1, model.NbEntities() + 1)}
    out: dict[int, str] = {}
    for face in _shapes(shape, TopAbs_FACE):
        entity = transfer.EntityFromShapeResult(face, -1)
        if entity is None:
            continue
        i = index.get(entity.This())
        if i is not None:
            out[_face_key(face)] = names[i - 1]
    return out


def read_step(data: bytes, entity: int, digest: str, span: Span | None = None) -> TopoDS_Shape:
    """The shape a STEP file carries: one entity (#n) of it, or everything (entity 0), as
    OpenCASCADE reads it, in millimetres. Kept per file and entity for the session."""
    import os
    import tempfile

    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    key = (digest, entity)
    if key in _STEP_CACHE:
        return _STEP_CACHE[key]
    fd, path = tempfile.mkstemp(suffix=".stp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        reader = STEPControl_Reader()
        if reader.ReadFile(path) != IFSelect_RetDone:
            raise GcadError("OpenCASCADE could not read the STEP data", span)
        if entity != 0:
            raise GcadError(
                "OpenCASCADE reads a STEP file's shapes all at once, not one entity", span
            )
        reader.TransferRoots()
        shape = reader.OneShape()
        if shape is not None and not shape.IsNull():
            _STEP_FACES[key] = _face_names(reader, shape, data)
    finally:
        os.unlink(path)
    if shape is None or shape.IsNull() or not _shapes(shape, TopAbs_FACE):
        raise GcadError(f"OpenCASCADE found no faces in #{entity}", span)
    _STEP_CACHE[key] = shape
    return shape


def step_face_names(digest: str, entity: int) -> dict[int, str]:
    """The `#n` of each face of a shape `read_step` returned, by `_face_key`."""
    return _STEP_FACES.get((digest, entity), {})


class _Builder:
    def __init__(self, blobs: dict[str, bytes] | None = None) -> None:
        self.blobs = blobs or {}
        self.ops = {
            "box": self.box,
            "sphere": self.sphere,
            "cylinder": self.cylinder,
            "polyhedron": self.polyhedron,
            "rect": self.rect,
            "circle": self.circle,
            "polygon": self.polygon,
            "linear_extrude": self.linear_extrude,
            "rotate_extrude": self.rotate_extrude,
            "import": self.import_,
        }

    def build(self, node: Node) -> TopoDS_Shape | None:
        if node.op in self.ops:
            return self.ops[node.op](node)
        built = [self.build(c) for c in node.children]
        parts = [s for s in built if s is not None]
        if node.op == "difference":
            if not built or built[0] is None:
                return None  # nothing to cut from
            head, rest = built[0], [s for s in built[1:] if s is not None]
            return _boolean("difference", head, rest, node) if rest else head
        if node.op == "intersection":
            if not built or len(parts) < len(built):
                return None  # intersecting with nothing gives nothing
            result = parts[0]
            for other in parts[1:]:
                algo = BRepAlgoAPI_Common(result, other)
                if not algo.IsDone():
                    raise GcadError("OpenCASCADE could not compute the intersection", node.span)
                result = _unify(algo.Shape())
            return result
        if not parts:
            return None
        merged = parts[0] if len(parts) == 1 else _boolean("union", parts[0], parts[1:], node)
        m = node_matrix(node)
        if m is None:
            if node.op not in ATTRIBUTE_OPS and node.op != "union":
                raise GcadError(
                    f"back-end 'occt' does not support '{node.op}' yet; use --backend manifold",
                    node.span,
                )
            return merged
        return _transform(merged, m)

    def import_(self, node: Node) -> TopoDS_Shape:
        fmt = str(node.arg("format"))
        if fmt != "step":
            # FR-B4: a solid made of triangles is never passed off as exact geometry.
            raise GcadError(
                f"this model is a {fmt.upper()} mesh, not exact geometry, so it cannot be "
                "exported to STEP or IGES; choose a mesh format",
                node.span,
            )
        digest = str(node.arg("data"))
        if digest not in self.blobs:
            raise GcadError("the data this import reads is not in the graph", node.span)
        return read_step(self.blobs[digest], int(node.num("entity")), digest, node.span)

    # -- 3D primitives ---------------------------------------------------------------------

    def box(self, node: Node) -> TopoDS_Shape | None:
        size = node.vec("size")
        if min(size) <= 0:
            return None
        lo = [-s / 2 for s in size] if node.arg("center") else [0.0, 0.0, 0.0]
        return BRepPrimAPI_MakeBox(gp_Pnt(*lo), *size).Shape()

    def sphere(self, node: Node) -> TopoDS_Shape:
        return BRepPrimAPI_MakeSphere(node.num("r")).Shape()

    def cylinder(self, node: Node) -> TopoDS_Shape:
        h, r1, r2 = node.num("h"), node.num("r1"), node.num("r2")
        z0 = -h / 2 if node.arg("center") else 0.0
        fn = int(node.num("fn"))
        if 3 <= fn <= POLYGONAL_FN_MAX:
            loft = BRepOffsetAPI_ThruSections(True, True)
            for r, z in ((r1, z0), (r2, z0 + h)):
                if r > 0:
                    loft.AddWire(_polygon_wire(_regular_polygon(r, fn, z)))
                else:
                    loft.AddVertex(BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, z)).Vertex())
            loft.Build()
            return loft.Shape()
        axis = gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1))
        if r1 == r2:
            return BRepPrimAPI_MakeCylinder(axis, r1, h).Shape()
        return BRepPrimAPI_MakeCone(axis, r1, r2, h).Shape()

    def polyhedron(self, node: Node) -> TopoDS_Shape:
        points = [(p[0], p[1], p[2]) for p in node.rows("points")]
        sewing = BRepBuilderAPI_Sewing(1e-6)
        for face in node.rows("faces"):
            # OpenSCAD lists face points clockwise seen from outside.
            idx = [int(i) for i in reversed(face)]
            loops = [idx] if len(idx) == 3 else []
            if not loops:
                maker = BRepBuilderAPI_MakeFace(_polygon_wire([points[i] for i in idx]), True)
                if maker.IsDone():
                    sewing.Add(maker.Face())
                    continue
                loops = [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]
            for loop in loops:
                sewing.Add(
                    BRepBuilderAPI_MakeFace(_polygon_wire([points[i] for i in loop]), True).Face()
                )
        sewing.Perform()
        shells = _shapes(sewing.SewedShape(), TopAbs_SHELL)
        if len(shells) != 1:
            raise GcadError("polyhedron is not one closed surface", node.span)
        solid = BRepBuilderAPI_MakeSolid(_cast("Shell", shells[0])).Solid()
        BRepLib.OrientClosedSolid_s(solid)
        if not BRepCheck_Analyzer(solid).IsValid():
            raise GcadError("polyhedron is not a closed, consistently oriented solid", node.span)
        return solid

    # -- 2D primitives, as planar faces in z = 0 ---------------------------------------------

    def rect(self, node: Node) -> TopoDS_Shape | None:
        w, h = node.vec("size")
        if w <= 0 or h <= 0:
            return None
        x0, y0 = (-w / 2, -h / 2) if node.arg("center") else (0.0, 0.0)
        corners = [(x0, y0, 0.0), (x0 + w, y0, 0.0), (x0 + w, y0 + h, 0.0), (x0, y0 + h, 0.0)]
        return BRepBuilderAPI_MakeFace(_polygon_wire(corners), True).Face()

    def circle(self, node: Node) -> TopoDS_Shape:
        r = node.num("r")
        fn = int(node.num("fn"))
        if 3 <= fn <= POLYGONAL_FN_MAX:
            return BRepBuilderAPI_MakeFace(_polygon_wire(_regular_polygon(r, fn)), True).Face()
        edge = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r)).Edge()
        return BRepBuilderAPI_MakeFace(BRepBuilderAPI_MakeWire(edge).Wire(), True).Face()

    def polygon(self, node: Node) -> TopoDS_Shape | None:
        points = [(p[0], p[1], 0.0) for p in node.rows("points")]
        paths = (
            [[int(i) for i in path] for path in node.rows("paths")]
            if node.arg("paths") is not None
            else [list(range(len(points)))]
        )
        result: TopoDS_Shape | None = None
        for path in paths:
            maker = BRepBuilderAPI_MakeFace(_polygon_wire([points[i] for i in path]), True)
            if not maker.IsDone():
                raise GcadError("polygon path is not a simple planar loop", node.span)
            face = maker.Face()
            if result is None:
                result = face
                continue
            # Even-odd fill: each further path toggles the area it covers.
            union = BRepAlgoAPI_Fuse(result, face).Shape()
            common = BRepAlgoAPI_Common(result, face).Shape()
            result = _unify(BRepAlgoAPI_Cut(union, common).Shape())
        return result

    # -- 2D -> 3D --------------------------------------------------------------------------

    def _section(self, node: Node) -> TopoDS_Shape | None:
        parts = [s for s in (self.build(c) for c in node.children) if s is not None]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else _boolean("union", parts[0], parts[1:], node)

    def linear_extrude(self, node: Node) -> TopoDS_Shape | None:
        section = self._section(node)
        if section is None:
            return None
        h, twist = node.num("height"), node.num("twist")
        sx, sy = node.vec("scale")
        if twist == 0 and sx == 1 and sy == 1:
            solid = BRepPrimAPI_MakePrism(section, gp_Vec(0, 0, h)).Shape()
        else:
            slices = int(node.num("slices")) or (max(1, math.ceil(abs(twist) / 5)) if twist else 1)
            solids = []
            for face_shape in _shapes(section, TopAbs_FACE):
                face = _cast("Face", face_shape)
                outer = BRepTools.OuterWire_s(face)
                lofts = [self._loft(outer, h, twist, sx, sy, slices)]
                for wire in _shapes(face, TopAbs_WIRE):
                    if not wire.IsSame(outer):
                        lofts.append(self._loft(wire, h, twist, sx, sy, slices))
                solid = lofts[0]
                if len(lofts) > 1:
                    solid = _boolean("difference", lofts[0], lofts[1:], node)
                solids.append(solid)
            solid = (
                solids[0] if len(solids) == 1 else _boolean("union", solids[0], solids[1:], node)
            )
        if node.arg("center"):
            m = np.eye(4)
            m[2, 3] = -h / 2
            solid = _transform(solid, m)
        return solid

    def _loft(
        self, wire: Any, h: float, twist: float, sx: float, sy: float, slices: int
    ) -> TopoDS_Shape:
        loft = BRepOffsetAPI_ThruSections(True, twist == 0)
        for k in range(slices + 1):
            t = k / slices
            kx, ky = 1 + (sx - 1) * t, 1 + (sy - 1) * t
            z = h * t
            if kx == 0 or ky == 0:
                loft.AddVertex(BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, z)).Vertex())
                continue
            # OpenSCAD twist turns clockwise seen from +z.
            a = math.radians(-twist * t)
            m = np.eye(4)
            m[:2, :2] = [
                [kx * math.cos(a), -ky * math.sin(a)],
                [kx * math.sin(a), ky * math.cos(a)],
            ]
            m[2, 3] = z
            loft.AddWire(_cast("Wire", _transform(wire, m)))
        loft.Build()
        if not loft.IsDone():
            raise GcadError("OpenCASCADE could not loft the twisted or scaled extrusion")
        return loft.Shape()

    def rotate_extrude(self, node: Node) -> TopoDS_Shape | None:
        section = self._section(node)
        if section is None:
            return None
        # OpenSCAD profile: x is the radius, y becomes z.
        lift = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
        profile = _transform(section, lift)
        axis = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
        return BRepPrimAPI_MakeRevol(profile, axis, math.radians(node.num("angle"))).Shape()


def _root_attributes(graph: Graph) -> tuple[RGBA | None, str | None]:
    """Colour and name of the whole model, when the outermost nodes give them."""
    color: RGBA | None = None
    name: str | None = None
    node = graph.root
    while node.op in ATTRIBUTE_OPS and len(node.children) == 1:
        if node.op == "color" and color is None:
            r, g, b, a = node.vec("rgba")
            color = (r, g, b, a)
        if node.op == "name" and name is None:
            value = node.arg("n")
            name = value if isinstance(value, str) else None
        node = node.children[0]
    return color, name


class OcctBackend:
    name = "occt"
    capabilities = frozenset({"mesh", "brep"})

    def realize(self, graph: Graph, tolerance: float | None = None) -> OcctModel:
        tol = tolerance if tolerance is not None else default_tolerance(graph)
        if tol <= 0:
            raise GcadError("tessellation tolerance must be positive")
        shape = _Builder(graph.blobs).build(graph.root)
        if shape is not None and not _shapes(shape, TopAbs_FACE):
            shape = None  # booleans can leave an empty compound
        color, name = _root_attributes(graph)
        return OcctModel(shape, graph.root.dim, tol, graph.root.id, color, name)
