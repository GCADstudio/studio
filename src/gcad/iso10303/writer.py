"""Parametric STEP (SPEC FR-I7): a geometry graph as ISO 10303-42 CSG, kept as an ISO 10303-55
procedural history, with its parameters as ISO 10303-108 and their expressions as ISO 13584-20,
under the AP242 long form.

Each graph node becomes one item, shared nodes once:

    box, sphere, cylinder          BLOCK, SPHERE, RIGHT_CIRCULAR_CYLINDER or _CONE
    rect, circle, polygon          RECTANGULAR_AREA, CIRCULAR_AREA, POLYGONAL_AREA (2D CSG); inside
                                   an extrusion, boundaries of a CURVE_BOUNDED_SURFACE
    union, difference, intersection  BOOLEAN_RESULTs chained to the left
    translate, rotate, mirror, uniform scale, rigid matrix, color, name
                                   SOLID_REPLICA of a CARTESIAN_TRANSFORMATION_OPERATOR_3D
                                   (2D: folded into the placement of the primitive they move)
    linear_extrude, rotate_extrude EXTRUDED_AREA_SOLID, REVOLVED_AREA_SOLID

Part 42 records placements, not the operation that made them, and its booleans are binary, so the
graph could not be recovered exactly from geometry alone. Each item is therefore named after its
graph node ("gcad:" and the node as a GCore literal); the iso10303 kernel uses that name only when
the geometry agrees with it (prelude.gcore, "authoring hints"), so a file edited elsewhere still
reads by its geometry, and ours reads back to the same graph hash (FR-G1).

What has no Part 42 form is refused with a diagnostic naming the node: polyhedra, twisted or
scaled extrusions, non-uniform scaling, sections and projections, polygonal cylinders ($fn 3 to
16, a prism in GCAD), and explicit B-rep read from another file.

Numbers that came from a top-level numeric definition (Traced, from the evaluator's trace mode)
make that definition a Part 108 unbound parameter, and every attribute computed from it a bound
parameter whose free-form assignment is the computation. The instance layout is
freecad-nxstyle's (parametric.py), which passes the AP242 long form with every rule evaluated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from gcad import GENERATOR
from gcad.core.graph import Graph, Node, canonical
from gcad.core.sexpr import format_number
from gcad.diagnostics import GcadError
from gcad.iso10303._nxstyle import p21

R, E, T = p21.Ref, p21.Enum, p21.Typed
U, D = p21.UNSET, p21.DERIVED
SCHEMA = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
BOOLEAN = {"union": "UNION", "difference": "DIFFERENCE", "intersection": "INTERSECTION"}
TRANSFORMS = ("translate", "rotate", "scale", "mirror", "matrix", "color", "name")
POLYGONAL_FN_MAX = 16  # back-end B makes a polygon of a cylinder or circle with $fn up to this
#: ISO 13584-20 entities for the operations a Traced expression may hold
OPS = {"+": "PLUS_EXPRESSION", "-": "MINUS_EXPRESSION", "*": "MULT_EXPRESSION",
       "/": "SLASH_EXPRESSION", "pow": "POWER_EXPRESSION"}  # fmt: skip
FUNCS = {"sqrt": "SQUARE_ROOT_FUNCTION", "abs": "ABS_FUNCTION", "neg": "MINUS_FUNCTION",
         "exp": "EXP_FUNCTION", "ln": "LOG_FUNCTION", "log": "LOG10_FUNCTION"}  # fmt: skip


def literal(value: object) -> str:
    """A value as GCore literal text, as prelude.gcore's parse_value reads it back."""
    if value is None:
        return "undef"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float | int):
        return format_number(float(value))
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "[" + " ".join(literal(v) for v in value) + "]"  # type: ignore[union-attr]


def hint(op: str, args: dict[str, object]) -> str:
    pairs = [[k, args[k]] for k in sorted(args)]
    return "gcad:" + literal([op, pairs])


@dataclass(frozen=True)
class Placement:
    """The PLANE a profile lies on; profile points are its (u, v) parameters."""

    plane: R


@dataclass
class Binding:
    """An attribute whose value came from parameters: owner, its entity and attribute, value."""

    owner: R
    attribute: str  # SCHEMA.ENTITY.ATTRIBUTE
    value: object  # a Traced number


@dataclass
class _Writer:
    graph: Graph
    f: p21.File = field(default_factory=p21.File)
    items: dict[str, R] = field(default_factory=dict)
    order: list[R] = field(default_factory=list)  # the history, operands before results
    bindings: list[Binding] = field(default_factory=list)
    cache: dict[tuple[object, ...], R] = field(default_factory=dict)

    def add(self, name: str, *params: object) -> R:
        ref: R = self.f.add(name, *params)
        return ref

    def cx(self, *records: tuple[str, list[object]]) -> R:
        ref: R = self.f.add_complex(records)
        return ref

    # -- geometry ------------------------------------------------------------------------

    def point(self, p: list[float] | tuple[float, ...]) -> R:
        key = ("pt", *(float(c) for c in p))
        if key not in self.cache:
            self.cache[key] = self.add("CARTESIAN_POINT", "", [float(c) for c in p])
        return self.cache[key]

    def direction(self, d: list[float] | tuple[float, ...]) -> R:
        key = ("dir", *(float(c) for c in d))
        if key not in self.cache:
            self.cache[key] = self.add("DIRECTION", "", [float(c) for c in d])
        return self.cache[key]

    def a2p3(self, origin: list[float], axis: list[float] = (0, 0, 1), x: list[float] = (1, 0, 0)) -> R:  # type: ignore[assignment]
        return self.add("AXIS2_PLACEMENT_3D", "", self.point(origin), self.direction(axis), self.direction(x))

    def a1p(self, origin: list[float], axis: list[float] = (0, 0, 1)) -> R:  # type: ignore[assignment]
        return self.add("AXIS1_PLACEMENT", "", self.point(origin), self.direction(axis))

    def a2p2(self, origin: list[float]) -> R:
        return self.add("AXIS2_PLACEMENT_2D", "", self.point(origin), self.direction([1.0, 0.0]))

    def bind(self, owner: R, attribute: str, value: object) -> object:
        if getattr(value, "expr", None) is not None:
            self.bindings.append(Binding(owner, attribute, value))
        return float(value)  # type: ignore[arg-type]

    # -- nodes ---------------------------------------------------------------------------

    def refuse(self, node: Node, why: str) -> GcadError:
        return GcadError(f"parametric STEP: {node.op} {why}", node.span)

    def item(self, node: Node) -> R:
        """The item a 3D node becomes; written once per distinct node."""
        if node.id in self.items:
            return self.items[node.id]
        ref = self._item(node)
        self.items[node.id] = ref
        self.order.append(ref)
        return ref

    def _item(self, node: Node) -> R:
        op, name = node.op, hint(node.op, {k: canonical(v) for k, v in node.args})
        if node.dim == 2:
            return self.area(node)
        if op == "box":
            size = node.vec("size")
            origin = [-s / 2 for s in size] if node.arg("center") else [0.0, 0.0, 0.0]
            place = self.a2p3(origin)
            ref = R(self.f.next_id())  # the BLOCK, written next; bindings point at it
            x, y, z = (self.bind(ref, f"GEOMETRIC_MODEL_SCHEMA.BLOCK.{a}", v) for a, v in
                       zip("XYZ", (dict(node.args)["size"]), strict=True))  # fmt: skip
            return self.add("BLOCK", name, place, x, y, z)
        if op == "sphere":
            centre = self.point([0.0, 0.0, 0.0])
            ref = R(self.f.next_id())
            rad = self.bind(ref, "GEOMETRIC_MODEL_SCHEMA.SPHERE.RADIUS", dict(node.args)["r"])
            return self.add("SPHERE", name, rad, centre)
        if op == "cylinder":
            return self.cylinder(node, name)
        if op in BOOLEAN:
            return self.boolean(node)
        if op in TRANSFORMS:
            return self.replica(node, name)
        if op == "linear_extrude":
            return self.extrusion(node, name)
        if op == "rotate_extrude":
            return self.revolution(node, name)
        if op == "polyhedron":
            raise self.refuse(node, "has no ISO 10303-42 CSG form; export B-rep STEP for it")
        if op == "import":
            raise self.refuse(node, "is explicit B-rep read from a file; export B-rep STEP for it")
        raise self.refuse(node, "has no ISO 10303-42 CSG form")

    def cylinder(self, node: Node, name: str) -> R:
        fn = node.num("fn", 0.0)
        if 3 <= fn <= POLYGONAL_FN_MAX:
            raise self.refuse(node, f"with $fn = {fn:g} is a prism in GCAD; it has no CSG primitive")
        args = dict(node.args)
        h, r1, r2 = node.num("h"), node.num("r1"), node.num("r2")
        z0 = -h / 2 if node.arg("center") else 0.0
        if r1 == r2:
            place = self.a1p([0.0, 0.0, z0])
            ref = R(self.f.next_id())
            height = self.bind(ref, "GEOMETRIC_MODEL_SCHEMA.RIGHT_CIRCULAR_CYLINDER.HEIGHT", args["h"])
            rad = self.bind(ref, "GEOMETRIC_MODEL_SCHEMA.RIGHT_CIRCULAR_CYLINDER.RADIUS", args["r1"])
            return self.add("RIGHT_CIRCULAR_CYLINDER", name, place, height, rad)
        # Part 42 6.4.15: the location is the centre of the smaller face, the axis points away
        # from the base, and semi_angle is the half-angle; the base lies height below.
        semi = math.atan(abs(r1 - r2) / h)
        if r2 < r1:
            place, top = self.a1p([0.0, 0.0, z0 + h]), r2
        else:
            place, top = self.a1p([0.0, 0.0, z0], [0.0, 0.0, -1.0]), r1
        return self.add("RIGHT_CIRCULAR_CONE", name, place, h, top, semi)

    def operand(self, node: Node) -> R:
        return self.item(node)

    def solid_model(self, node: Node) -> R:
        """SOLID_REPLICA's parent must be a solid_model: wrap a primitive or a boolean result."""
        ref = self.item(node)
        if node.op in ("box", "sphere", "cylinder", *BOOLEAN) or node.dim == 2:
            key = ("csg", node.id)
            if key not in self.cache:
                self.cache[key] = self.add("CSG_SOLID", "", ref)
                self.order.append(self.cache[key])
            return self.cache[key]
        return ref

    def boolean(self, node: Node) -> R:
        return self.chain(list(node.children), node.op, node.op, node.dim)

    def chain(self, kids: list[Node], op: str, tag: str, dim: int) -> R:
        """An n-ary boolean as binary ones chained to the left; the outermost says how many.
        tag "children" marks the shapes of a transform that applies to several of them."""
        entity = "BOOLEAN_RESULT_2D" if dim == 2 else "BOOLEAN_RESULT"
        acc = self.operand(kids[0])
        for i, child in enumerate(kids[1:], start=2):
            n = len(kids) if i == len(kids) else 0
            acc_ref = self.add(entity, hint(tag, {"n": n}), E(BOOLEAN[op]), acc, self.operand(child))
            if i < len(kids):
                self.order.append(acc_ref)
            acc = acc_ref
        return acc

    def replica(self, node: Node, name: str) -> R:
        m = self.matrix(node)
        if node.dim == 2:
            raise self.refuse(node, "of a 2D boolean has no ISO 10303-42 form; move its primitives")
        cols = [[m[r][c] for r in range(3)] for c in range(3)]
        scale = math.sqrt(sum(x * x for x in cols[0]))
        units = [[x / scale for x in c] for c in cols]
        if len(node.children) == 1:
            parent = self.solid_model(node.children[0])
        else:  # a transform of several shapes: their union, marked as the shapes themselves
            key = ("children", node.id)
            if key not in self.cache:
                chain = self.chain(list(node.children), "union", "children", 3)
                self.order.append(chain)
                self.cache[key] = self.add("CSG_SOLID", "", chain)
                self.order.append(self.cache[key])
            parent = self.cache[key]
        op = self.add(
            "CARTESIAN_TRANSFORMATION_OPERATOR_3D", name, name, U,
            self.direction(units[0]), self.direction(units[1]),
            self.point([m[0][3], m[1][3], m[2][3]]), scale, self.direction(units[2]),
        )  # fmt: skip
        return self.add("SOLID_REPLICA", "", parent, op)

    def matrix(self, node: Node) -> list[list[float]]:
        """The 4x4 matrix of a transform node, refusing what a CTO3D cannot express: anything
        but a rotation or reflection with one uniform scale and a translation."""
        from gcad.core.graph import node_matrix

        if node.op in ("color", "name"):
            return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
        m = node_matrix(node)
        assert m is not None
        a = m[:3, :3]
        s = float(abs(a[:, 0] @ a[:, 0])) ** 0.5
        if s == 0 or float(abs((a.T @ a) - s * s * __import__("numpy").eye(3)).max()) > 1e-9 * s * s:
            raise self.refuse(node, "is not a rotation, reflection or uniform scale (non-uniform "
                                    "scaling and shear have no ISO 10303-42 form)")  # fmt: skip
        return [[float(x) for x in row] for row in m]

    # -- 2D: areas, profiles ------------------------------------------------------------

    def area(self, node: Node, offset: tuple[float, float] = (0.0, 0.0)) -> R:
        """A 2D node as Part 42 2D CSG: primitive_2d areas and boolean results."""
        if node.id in self.items and offset == (0.0, 0.0):
            return self.items[node.id]
        op, name = node.op, hint(node.op, {k: canonical(v) for k, v in node.args})
        if op == "translate" and node.children:
            v = node.vec("v")
            return self.area(node.children[0], (offset[0] + v[0], offset[1] + v[1]))
        if op == "circle":
            self.round_enough(node)
            centre = self.point2(offset)
            ref = R(self.f.next_id())
            rad = self.bind(ref, "GEOMETRIC_MODEL_SCHEMA.CIRCULAR_AREA.RADIUS", dict(node.args)["r"])
            return self.add("CIRCULAR_AREA", name, centre, rad)
        if op == "rect":
            size = node.vec("size")
            o = [offset[0] - size[0] / 2, offset[1] - size[1] / 2] if node.arg("center") else list(offset)
            return self.add("RECTANGULAR_AREA", name, self.a2p2(o), size[0], size[1])
        if op == "polygon":
            if node.arg("paths") is not None:
                raise self.refuse(node, "with paths (holes) has no POLYGONAL_AREA form; use difference")
            pts = [[p[0] + offset[0], p[1] + offset[1]] for p in node.rows("points")]
            if offset != (0.0, 0.0):
                name = ""
            return self.add("POLYGONAL_AREA", name, [self.point(p) for p in pts])
        if op in BOOLEAN and offset == (0.0, 0.0):
            return self.boolean(node)
        raise self.refuse(node, "in 2D has no ISO 10303-42 2D CSG form here")

    def round_enough(self, node: Node) -> None:
        fn = node.num("fn", 0.0)
        if 3 <= fn <= POLYGONAL_FN_MAX:
            raise self.refuse(node, f"with $fn = {fn:g} is a polygon in GCAD; write it as polygon()")

    def profile(self, node: Node, to3d: Placement) -> list[R]:
        """A 2D node as the boundaries of a CURVE_BOUNDED_SURFACE: one primitive, or a difference
        of a primitive and primitives inside it. to3d places a profile point on the plane."""
        if node.op == "difference":
            outer = self.boundary(node.children[0], True, to3d)
            return [outer, *(self.boundary(c, False, to3d) for c in node.children[1:])]
        return [self.boundary(node, True, to3d)]

    def boundary(self, node: Node, outer: bool, to3d: Placement, offset: tuple[float, float] = (0.0, 0.0)) -> R:
        """A closed loop on the profile's plane: BOUNDED_PCURVEs on the plane whose 2D curves are
        trimmed lines or two half circles, in the plane's (u, v), which are the profile's own
        (x, y) (Part 42 4.4.41 to 4.4.47; freecad-nxstyle's layout)."""
        kind = "OUTER_BOUNDARY_CURVE" if outer else "BOUNDARY_CURVE"
        if node.op == "translate" and node.children:
            v = node.vec("v")
            return self.boundary(node.children[0], outer, to3d, (offset[0] + v[0], offset[1] + v[1]))
        name = hint(node.op, {k: canonical(v) for k, v in node.args})
        plane = to3d.plane
        if node.op == "circle":
            self.round_enough(node)
            c, r = offset, node.num("r")
            east, west = (c[0] + r, c[1]), (c[0] - r, c[1])
            curve = self.add("CIRCLE", "", self.add("AXIS2_PLACEMENT_2D", "", self.point2(c), self.direction([1.0, 0.0])), r)
            segs = [self.pcurve(plane, self.add("TRIMMED_CURVE", "", curve, [self.point2(a)], [self.point2(b)], True,
                                                E("CARTESIAN"))) for a, b in ((east, west), (west, east))]  # fmt: skip
            return self.add(kind, name, segs, E("F"))
        if node.op == "rect":
            sx, sy = node.vec("size")
            x0, y0 = (-sx / 2, -sy / 2) if node.arg("center") else (0.0, 0.0)
            pts = [[x0, y0], [x0 + sx, y0], [x0 + sx, y0 + sy], [x0, y0 + sy]]
        elif node.op == "polygon" and node.arg("paths") is None:
            pts = node.rows("points")
        else:
            raise self.refuse(node, "cannot be a profile boundary (use circle, square or a polygon without paths)")
        if offset != (0.0, 0.0):
            name = ""
        pts = [(p[0] + offset[0], p[1] + offset[1]) for p in pts]
        segs = []
        for a, b in zip(pts, [*pts[1:], pts[0]], strict=True):
            d = (b[0] - a[0], b[1] - a[1])
            length = math.hypot(*d)
            line = self.add("LINE", "", self.point2(a),
                            self.add("VECTOR", "", self.direction([d[0] / length, d[1] / length]), length))  # fmt: skip
            trimmed = self.add("TRIMMED_CURVE", "", line, [self.point2(a)], [self.point2(b)], True, E("CARTESIAN"))
            segs.append(self.pcurve(plane, trimmed))
        return self.add(kind, name, segs, E("F"))

    def point2(self, p: tuple[float, float] | list[float]) -> R:
        return self.point([float(p[0]), float(p[1])])

    def pcurve(self, plane: R, curve2d: R) -> R:
        if "pctx" not in self.__dict__:
            self.__dict__["pctx"] = self.cx(
                ("GEOMETRIC_REPRESENTATION_CONTEXT", [2]),
                ("PARAMETRIC_REPRESENTATION_CONTEXT", []),
                ("REPRESENTATION_CONTEXT", ["2D", "parameter space"]),
            )
        rep = self.add("DEFINITIONAL_REPRESENTATION", "", [curve2d], self.__dict__["pctx"])
        pc = self.add("BOUNDED_PCURVE", "", plane, rep)
        return self.add("COMPOSITE_CURVE_SEGMENT", E("CONTINUOUS"), True, pc)

    def extrusion(self, node: Node, name: str) -> R:
        if node.num("twist") != 0 or node.vec("scale") != [1.0, 1.0]:
            raise self.refuse(node, "with twist or scale has no ISO 10303-42 form")
        child = self.single(node)
        z = -node.num("height") / 2 if node.arg("center") else 0.0  # the swept area's plane
        plane = self.add("PLANE", "", self.a2p3([0.0, 0.0, z]))
        to3d = Placement(plane)
        surface = self.add("CURVE_BOUNDED_SURFACE", "", plane, self.profile(child, to3d), False)
        direction = self.direction([0.0, 0.0, 1.0])
        ref = R(self.f.next_id())
        depth = self.bind(ref, "GEOMETRIC_MODEL_SCHEMA.EXTRUDED_AREA_SOLID.DEPTH", dict(node.args)["height"])
        return self.add("EXTRUDED_AREA_SOLID", name, surface, direction, depth)

    def revolution(self, node: Node, name: str) -> R:
        child = self.single(node)
        # GCAD's profile is (radius, height) in its xy; in 3D it lies in the xz plane, which
        # rotate_extrude spins about z (OpenSCAD's convention). The plane's normal is -y, so its
        # own x and y are the model's x and z.
        plane = self.add("PLANE", "", self.a2p3([0.0, 0.0, 0.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]))
        to3d = Placement(plane)
        surface = self.add("CURVE_BOUNDED_SURFACE", "", plane, self.profile(child, to3d), False)
        angle = math.radians(node.num("angle"))
        return self.add("REVOLVED_AREA_SOLID", name, surface, self.a1p([0.0, 0.0, 0.0]), angle)

    def single(self, node: Node) -> Node:
        if len(node.children) != 1:
            raise self.refuse(node, "of several shapes: wrap the profile in a union first")
        return node.children[0]

    # -- the file ------------------------------------------------------------------------

    def contexts(self) -> R:
        mm = self.cx(("LENGTH_UNIT", []), ("NAMED_UNIT", [D]), ("SI_UNIT", [E("MILLI"), E("METRE")]))
        rad = self.cx(("NAMED_UNIT", [D]), ("PLANE_ANGLE_UNIT", []), ("SI_UNIT", [U, E("RADIAN")]))
        sr = self.cx(("NAMED_UNIT", [D]), ("SI_UNIT", [U, E("STERADIAN")]), ("SOLID_ANGLE_UNIT", []))
        unc = self.add("UNCERTAINTY_MEASURE_WITH_UNIT", T("LENGTH_MEASURE", 1e-7), mm,
                       "distance_accuracy_value", "confusion accuracy")  # fmt: skip
        dim = 2 if self.graph.root.dim == 2 else 3
        ctx = self.cx(
            ("GEOMETRIC_REPRESENTATION_CONTEXT", [dim]),
            ("GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT", [[unc]]),
            ("GLOBAL_UNIT_ASSIGNED_CONTEXT", [[mm, rad, sr]]),
            ("REPRESENTATION_CONTEXT", [f"{dim}D", "GCAD Studio"]),
        )
        app = self.add("APPLICATION_CONTEXT", "managed model based 3d engineering")
        self.add("APPLICATION_PROTOCOL_DEFINITION", "international standard",
                 SCHEMA.lower(), 2020, app)  # fmt: skip
        self.add("PRODUCT_CONTEXT", "", app, "mechanical")
        return ctx

    def write(self, title: str) -> None:
        ctx = self.contexts()
        root = self.graph.root
        if root.dim == 2:
            top = self.add("CSG_SOLID_2D", "", self.area(root))
            self.add("CSG_2D_SHAPE_REPRESENTATION", title, [top], ctx)
            if self.bindings:
                self.parameters(title, [top], ctx, None)
            return
        top = self.solid_model(root)
        seq = self.cx(
            ("GEOMETRIC_REPRESENTATION_ITEM", []),
            ("PROCEDURAL_REPRESENTATION_SEQUENCE", [list(self.order), [], "construction history"]),
            ("PROCEDURAL_SHAPE_REPRESENTATION_SEQUENCE", []),
            ("PROCEDURAL_SOLID_REPRESENTATION_SEQUENCE", []),
            ("REPRESENTATION_ITEM", [title]),
            ("SOLID_MODEL", []),
        )
        del top
        self.add("PROCEDURAL_SHAPE_REPRESENTATION", title, [seq], ctx)
        if self.bindings:
            history = self.add("SHAPE_REPRESENTATION", f"{title} history", [seq], ctx)
            self.parameters(title, [seq], ctx, history)

    # -- ISO 10303-108 parameters --------------------------------------------------------

    def parameters(self, title: str, owners: list[R], ctx: R, history: R | None) -> None:
        generics = self.add("ELEMENTARY_SPACE", E("ES_GENERICS"))
        reals: list[R] = []
        unbound: dict[str, R] = {}

        def variable(name: str, value: float) -> R:
            if name not in unbound:
                if not reals:
                    reals.append(self.add("ELEMENTARY_SPACE", E("ES_REALS")))
                ref = self.cx(
                    ("EXPRESSION", []), ("GENERIC_EXPRESSION", []), ("GENERIC_VARIABLE", []),
                    ("MATHS_REAL_VARIABLE", []), ("MATHS_VARIABLE", [reals[0], name]),
                    ("NUMERIC_EXPRESSION", []), ("NUMERIC_VARIABLE", []), ("REAL_NUMERIC_VARIABLE", []),
                    ("REPRESENTATION_ITEM", [name]), ("SIMPLE_GENERIC_EXPRESSION", []),
                    ("SIMPLE_NUMERIC_EXPRESSION", []), ("UNBOUND_VARIATIONAL_PARAMETER", []), ("VARIABLE", []),
                    ("VARIATIONAL_PARAMETER", ["[mm] ", T("MATHS_REAL", float(value))]),
                    ("VARIATIONAL_REPRESENTATION_ITEM", []),
                )  # fmt: skip
                sem = self.add("UNBOUND_VARIATIONAL_PARAMETER_SEMANTICS")
                self.add("UNBOUND_PARAMETER_ENVIRONMENT", ref, sem)
                unbound[name] = ref
            return unbound[name]

        used: set[int] = set()

        def expression(e: tuple[object, ...]) -> R:
            kind = e[0]
            if kind == "param":
                ref = variable(str(e[1]), float(e[2]))  # type: ignore[arg-type]
                used.add(int(ref))
                return ref
            if kind == "num":
                return self.add("REAL_LITERAL", float(e[1]))  # type: ignore[arg-type]
            if kind in OPS:
                return self.add(OPS[kind], [expression(a) for a in e[1:]])  # type: ignore[arg-type]
            if kind in FUNCS:
                return self.add(FUNCS[kind], expression(e[1]))  # type: ignore[arg-type]
            raise GcadError(f"parametric STEP: the operation {kind!r} has no ISO 13584-20 form")

        items: list[R] = list(owners)
        for b in self.bindings:
            label = b.attribute.rsplit(".", 2)[1].lower() + "." + b.attribute.rsplit(".", 1)[1].lower()
            label = f"#{int(b.owner)} {label}"
            bound = self.add("BOUND_VARIATIONAL_PARAMETER", label, generics, label, U, D)
            ref = self.add("INSTANCE_ATTRIBUTE_REFERENCE", b.attribute, b.owner)
            self.add("BOUND_PARAMETER_ENVIRONMENT", bound, ref)
            used.clear()
            expr = expression(b.value.expr)  # type: ignore[attr-defined]
            refs = [R(i) for i in sorted(used)]
            rule = self.add("FREE_FORM_ASSIGNMENT", label, U, [bound], refs, expr)
            items += [bound, rule]
        items += sorted(unbound.values(), key=int)
        wrapper = self.cx(
            ("REPRESENTATION", [f"{title} parameters", items, ctx]),
            ("SHAPE_REPRESENTATION", []),
            ("VARIATIONAL_REPRESENTATION", []),
        )
        if history is not None:
            self.add("VARIATIONAL_CURRENT_REPRESENTATION_RELATIONSHIP", title, U, wrapper, history, history)


def to_step(graph: Graph, title: str, time_stamp: str, source: str = "") -> str:
    """The graph as a parametric ISO 10303-21 file (text, deterministic)."""
    w = _Writer(graph)
    w.f.set_header(
        [SCHEMA],
        description=[f"{title}: parametric model" + (f" from {source}" if source else "")],
        name=title,
        preprocessor=GENERATOR,
        system="GCAD Studio",
        time_stamp=time_stamp,
    )
    w.write(title)
    text: str = p21.dumps(w.f)
    return text
