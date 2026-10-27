"""The geometry graph: the evaluated, language-neutral model (SPEC §5.3).

Nodes are immutable. A node's id is the SHA-256 of its canonical content (op, arguments,
dimension and child ids) without source spans, so identical sub-trees share one id and the
same model written in two languages yields the same graph hash (FR-G1).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import cast

import numpy as np

from gcad.core.sexpr import format_number
from gcad.diagnostics import Span

ArgValue = float | bool | str | None | tuple["ArgValue", ...]

# Ops that only carry attributes; back-ends treat them as a union of their children.
ATTRIBUTE_OPS = frozenset({"color", "name"})


def canonical(value: ArgValue) -> object:
    """JSON-ready canonical form: integral numbers become ints, -0 becomes 0."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float | int):
        f = float(value)
        if not math.isfinite(f):
            raise ValueError(f"not a finite number: {f}")
        text = format_number(f)
        return int(text) if "." not in text and "e" not in text else f
    return [canonical(v) for v in value]


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, eq=False)
class Node:
    op: str
    args: tuple[tuple[str, ArgValue], ...]
    children: tuple[Node, ...]
    dim: int
    span: Span | None = None
    id: str = field(init=False)

    def __post_init__(self) -> None:
        content = {
            "op": self.op,
            "dim": self.dim,
            "args": {k: canonical(v) for k, v in self.args},
            "children": [c.id for c in self.children],
        }
        digest = hashlib.sha256(canonical_json(content).encode()).hexdigest()
        object.__setattr__(self, "id", digest[:16])

    def arg(self, name: str, default: ArgValue = None) -> ArgValue:
        for key, value in self.args:
            if key == name:
                return value
        return default

    def num(self, name: str, default: float = 0.0) -> float:
        value = self.arg(name, default)
        assert isinstance(value, float | int) and not isinstance(value, bool), (name, value)
        return float(value)

    def vec(self, name: str) -> list[float]:
        value = self.arg(name)
        assert isinstance(value, tuple), (name, value)
        return [float(cast(float, v)) for v in value]

    def rows(self, name: str) -> list[list[float]]:
        """A list-of-lists argument such as polyhedron points or faces."""
        value = self.arg(name)
        assert isinstance(value, tuple), (name, value)
        return [[float(cast(float, x)) for x in cast(tuple, row)] for row in value]

    def content_json(self) -> dict[str, object]:
        return {
            "op": self.op,
            "dim": self.dim,
            "args": {k: canonical(v) for k, v in self.args},
            "children": [c.id for c in self.children],
        }


def make_node(
    op: str,
    args: Mapping[str, ArgValue],
    children: list[Node] | tuple[Node, ...] = (),
    dim: int = 3,
    span: Span | None = None,
) -> Node:
    return Node(op, tuple(sorted(args.items())), tuple(children), dim, span)


def empty(dim: int = 3, span: Span | None = None) -> Node:
    return make_node("union", {}, (), dim, span)


@dataclass
class Graph:
    root: Node
    units: str = "mm"
    metadata: dict[str, str] = field(default_factory=dict)

    def walk(self) -> Iterator[Node]:
        """Every node object once, children before parents."""
        seen: set[int] = set()

        def visit(node: Node) -> Iterator[Node]:
            if id(node) in seen:
                return
            seen.add(id(node))
            for child in node.children:
                yield from visit(child)
            yield node

        yield from visit(self.root)

    def nodes(self) -> dict[str, Node]:
        """Distinct nodes by id, children before parents."""
        out: dict[str, Node] = {}
        for node in self.walk():
            out.setdefault(node.id, node)
        return out

    def to_json(self, spans: bool = True) -> dict[str, object]:
        nodes = self.nodes()
        doc: dict[str, object] = {
            "format": "gcad-graph",
            "version": 1,
            "units": self.units,
            "root": self.root.id,
            "nodes": {nid: n.content_json() for nid, n in nodes.items()},
        }
        if spans:
            where: dict[str, list[dict[str, object]]] = {}
            for node in self.walk():
                if node.span is not None:
                    entry = node.span.to_json()
                    bucket = where.setdefault(node.id, [])
                    if entry not in bucket:
                        bucket.append(entry)
            doc["spans"] = where
        return doc

    def hash(self) -> str:
        """SHA-256 of the canonical graph without spans or metadata (FR-G1)."""
        return hashlib.sha256(canonical_json(self.to_json(spans=False)).encode()).hexdigest()


#: Every op a back-end understands. A graph read from outside (a process kernel) may use no other.
OPS = frozenset(
    {
        *("box", "sphere", "cylinder", "polyhedron", "rect", "circle", "polygon"),
        *("translate", "rotate", "scale", "mirror", "matrix"),
        *("union", "difference", "intersection"),
        *("linear_extrude", "rotate_extrude", "section", "project"),
        *("color", "name", "import"),
    }
)


def _arg_value(value: object, where: str) -> ArgValue:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, list):
        return tuple(_arg_value(v, where) for v in value)
    raise ValueError(f"{where}: unsupported argument value {value!r}")


def graph_from_json(doc: Mapping[str, object], span: Span | None = None) -> Graph:
    """Read the canonical graph JSON (the `gcad eval --dump graph` form). Node ids are
    recomputed, so a graph cannot claim content it does not have. `span`, when given, is put
    on every node: the geometry then links back to the whole source it came from."""
    if doc.get("format") != "gcad-graph" or doc.get("version") != 1:
        raise ValueError("not a gcad-graph version 1 document")
    nodes = doc.get("nodes")
    root_id = doc.get("root")
    if not isinstance(nodes, dict) or not isinstance(root_id, str) or root_id not in nodes:
        raise ValueError("graph has no nodes or no root")
    built: dict[str, Node] = {}
    visiting: set[str] = set()

    def build(nid: str) -> Node:
        if nid in built:
            return built[nid]
        if nid in visiting or nid not in nodes:
            raise ValueError(f"node {nid}: missing or part of a cycle")
        visiting.add(nid)
        raw = nodes[nid]
        if not isinstance(raw, dict):
            raise ValueError(f"node {nid}: not an object")
        op, dim = raw.get("op"), raw.get("dim")
        if op not in OPS or dim not in (2, 3):
            raise ValueError(f"node {nid}: unknown op {op!r} or dim {dim!r}")
        args = raw.get("args") or {}
        children = raw.get("children") or []
        if not isinstance(args, dict) or not isinstance(children, list):
            raise ValueError(f"node {nid}: args must be an object and children a list")
        node = make_node(
            str(op),
            {str(k): _arg_value(v, f"node {nid}") for k, v in args.items()},
            [build(str(c)) for c in children],
            int(cast(int, dim)),
            span,
        )
        visiting.discard(nid)
        built[nid] = node
        return node

    units = doc.get("units", "mm")
    return Graph(build(root_id), units=str(units))


# --- transforms -----------------------------------------------------------------------------


def rotation_xyz(angles: list[float]) -> np.ndarray:
    """4x4 matrix rotating about X, then Y, then Z, in degrees (OpenSCAD order)."""
    ax, ay, az = (math.radians(a) for a in angles)
    cx, sx, cy, sy, cz, sz = (
        math.cos(ax),
        math.sin(ax),
        math.cos(ay),
        math.sin(ay),
        math.cos(az),
        math.sin(az),
    )
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = rz @ ry @ rx
    return m


def rotation_axis(angle: float, axis: list[float]) -> np.ndarray:
    """4x4 matrix rotating by angle degrees about an axis through the origin."""
    v = np.asarray(axis, dtype=float)
    v = v / np.linalg.norm(v)
    a = math.radians(angle)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    m = np.eye(4)
    m[:3, :3] = np.eye(3) + math.sin(a) * k + (1 - math.cos(a)) * (k @ k)
    return m


def mirror_matrix(normal: list[float]) -> np.ndarray:
    n = np.asarray(normal, dtype=float)
    n = n / np.linalg.norm(n)
    m = np.eye(4)
    m[:3, :3] = np.eye(3) - 2 * np.outer(n, n)
    return m


def node_matrix(node: Node) -> np.ndarray | None:
    """The 4x4 matrix of a transform node, or None for other ops."""
    m = np.eye(4)
    if node.op == "translate":
        m[:3, 3] = node.vec("v")
    elif node.op == "rotate":
        return rotation_xyz(node.vec("a"))
    elif node.op == "scale":
        m[:3, :3] = np.diag(node.vec("v"))
    elif node.op == "mirror":
        return mirror_matrix(node.vec("v"))
    elif node.op == "matrix":
        return np.asarray(node.vec("m"), dtype=float).reshape(4, 4)
    else:
        return None
    return m


# --- conservative bounds, used to pick a tessellation tolerance before realizing -------------

Box = tuple[np.ndarray, np.ndarray]


def _box_union(boxes: list[Box | None]) -> Box | None:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    return np.min([b[0] for b in real], axis=0), np.max([b[1] for b in real], axis=0)


def _transform_box(box: Box, m: np.ndarray) -> Box:
    lo, hi = box
    corners = np.array(
        [[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    pts = (m @ corners.T).T[:, :3]
    return pts.min(axis=0), pts.max(axis=0)


def estimate_bounds(node: Node, memo: dict[str, Box | None] | None = None) -> Box | None:
    """An axis-aligned box guaranteed to contain the node's geometry (2D nodes have z = 0)."""
    memo = {} if memo is None else memo
    if node.id in memo:
        return memo[node.id]
    op = node.op
    result: Box | None
    if op == "box":
        size = np.array(node.vec("size"))
        lo = -size / 2 if node.arg("center") else np.zeros(3)
        result = (lo, lo + size)
    elif op == "sphere":
        r = node.num("r")
        result = (np.full(3, -r), np.full(3, r))
    elif op == "cylinder":
        r, h = max(node.num("r1"), node.num("r2")), node.num("h")
        z0 = -h / 2 if node.arg("center") else 0.0
        result = (np.array([-r, -r, z0]), np.array([r, r, z0 + h]))
    elif op in ("polyhedron", "polygon"):
        pts = np.asarray(node.arg("points"), dtype=float)
        pts = pts.reshape(-1, 3 if op == "polyhedron" else 2)
        if op == "polygon":
            pts = np.column_stack([pts, np.zeros(len(pts))])
        result = (pts.min(axis=0), pts.max(axis=0)) if len(pts) else None
    elif op == "rect":
        size = np.array([*node.vec("size"), 0.0])
        lo = -size / 2 if node.arg("center") else np.zeros(3)
        result = (lo, lo + size)
    elif op == "circle":
        r = node.num("r")
        result = (np.array([-r, -r, 0.0]), np.array([r, r, 0.0]))
    elif op in ("difference", "intersection"):
        result = estimate_bounds(node.children[0], memo) if node.children else None
    elif op == "linear_extrude":
        child = _box_union([estimate_bounds(c, memo) for c in node.children])
        if child is None:
            result = None
        else:
            h = node.num("height")
            sx, sy = node.vec("scale")
            reach = float(np.max(np.abs(np.concatenate(child)))) * max(1.0, sx, sy)
            if node.num("twist") != 0:
                reach *= math.sqrt(2)
            lo = np.array([-reach, -reach, 0.0])
            hi = np.array([reach, reach, h])
            if node.arg("center"):
                lo[2], hi[2] = -h / 2, h / 2
            result = (lo, hi)
    elif op == "rotate_extrude":
        child = _box_union([estimate_bounds(c, memo) for c in node.children])
        if child is None:
            result = None
        else:
            r = float(max(abs(child[0][0]), abs(child[1][0])))
            result = (np.array([-r, -r, child[0][1]]), np.array([r, r, child[1][1]]))
    elif op == "import":
        if node.arg("bounds") is None:
            result = None
        else:
            lo_hi = node.vec("bounds")  # x0 y0 z0 x1 y1 z1
            result = (np.array(lo_hi[:3]), np.array(lo_hi[3:]))
    elif op in ("section", "project"):
        child = _box_union([estimate_bounds(c, memo) for c in node.children])
        if child is None:
            result = None
        else:
            lo, hi = child[0].copy(), child[1].copy()
            lo[2] = hi[2] = 0.0
            result = (lo, hi)
    else:
        result = _box_union([estimate_bounds(c, memo) for c in node.children])
        m = node_matrix(node)
        if result is not None and m is not None:
            result = _transform_box(result, m)
    memo[node.id] = result
    return result


# --- tessellation (FR-B5) --------------------------------------------------------------------

DEFAULT_RELATIVE_TOLERANCE = 1e-3  # of the bounding-box diagonal
DEFAULT_ANGULAR_TOLERANCE = 0.5  # radians
MAX_SEGMENTS = 1024


def default_tolerance(graph: Graph) -> float:
    box = estimate_bounds(graph.root)
    if box is None:
        return 0.01
    diag = float(np.linalg.norm(box[1] - box[0]))
    return max(diag * DEFAULT_RELATIVE_TOLERANCE, 1e-6)


def circle_segments(r: float, fn: float, fa: float, fs: float, tolerance: float) -> int:
    """Segments for a full circle of radius r.

    $fn wins when set; otherwise $fa/$fs use OpenSCAD's rule; otherwise the chord deviation
    stays below the linear tolerance and each segment spans at most the angular tolerance.
    """
    if fn > 0:
        return max(3, min(int(fn), MAX_SEGMENTS))
    if fa > 0 or fs > 0:
        by_angle = 360.0 / fa if fa > 0 else math.inf
        by_size = r * 2 * math.pi / fs if fs > 0 else math.inf
        return max(5, min(math.ceil(min(by_angle, by_size)), MAX_SEGMENTS))
    by_angle = math.ceil(2 * math.pi / DEFAULT_ANGULAR_TOLERANCE)
    by_chord = 3 if tolerance >= r else math.ceil(math.pi / math.acos(1 - tolerance / r))
    return max(3, min(max(by_angle, by_chord), MAX_SEGMENTS))
