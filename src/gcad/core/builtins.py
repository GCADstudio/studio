"""GCore built-in functions: numbers, lists, and the geometry constructors of SPEC §5.3."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from gcad.core.evaluator import (
    Builtin,
    BuiltinFn,
    CallCtx,
    Closure,
    Env,
    flatten_shapes,
    is_shape_list,
    truthy,
    type_name,
)
from gcad.core.graph import ArgValue, Node, empty, rotation_axis
from gcad.core.sexpr import format_number
from gcad.diagnostics import GcadError, Span

HINTS = ("$fn", "$fa", "$fs")


# --- value checks ----------------------------------------------------------------------------


def as_num(value: object, what: str, span: Span | None) -> float:
    if isinstance(value, float) and not isinstance(value, bool):
        return value
    raise GcadError(f"{what} must be a number, got a {type_name(value)}", span)


def as_bool(value: object, what: str, span: Span | None) -> bool:
    if isinstance(value, bool):
        return value
    raise GcadError(f"{what} must be true or false, got a {type_name(value)}", span)


def as_vec(value: object, sizes: tuple[int, ...], what: str, span: Span | None) -> list[float]:
    if isinstance(value, list) and len(value) in sizes:
        return [as_num(v, what, span) for v in value]
    want = " or ".join(str(n) for n in sizes)
    raise GcadError(f"{what} must be a vector of {want} numbers", span)


def as_points(value: object, n: int, what: str, span: Span | None) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list) or not value:
        raise GcadError(f"{what} must be a non-empty list of {n}D points", span)
    return tuple(tuple(as_vec(p, (n,), f"each point of {what}", span)) for p in value)


def as_index_lists(
    value: object, count: int, what: str, span: Span | None
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list) or not value:
        raise GcadError(f"{what} must be a non-empty list of index lists", span)
    out = []
    for item in value:
        if not isinstance(item, list) or len(item) < 3:
            raise GcadError(f"each entry of {what} needs at least 3 indices", span)
        idx = []
        for v in item:
            f = as_num(v, f"an index in {what}", span)
            if not f.is_integer() or not 0 <= f < count:
                raise GcadError(f"index {format_number(f)} in {what} is out of range", span)
            idx.append(f)
        out.append(tuple(idx))
    return tuple(out)


def show(value: object) -> str:
    """GCore-style text of a value, for echo and str."""
    if value is None:
        return "undef"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + " ".join(show(v) for v in value) + "]"
    if isinstance(value, Node):
        return f"<{value.dim}D {value.op} {value.id}>"
    if isinstance(value, Closure):
        return f"<function {value.name}>"
    if isinstance(value, Builtin):
        return f"<builtin {value.name}>"
    return repr(value)


# --- argument binding for geometry built-ins ----------------------------------------------------


def bind(
    name: str,
    params: list[tuple[str, object]],
    args: list[object],
    kwargs: dict[str, object],
    ctx: CallCtx,
    hints: bool = False,
) -> tuple[dict[str, object], list[Node]]:
    """Shapes (and lists of shapes) become children; other values fill parameters in order."""
    values: dict[str, object] = {}
    children: list[Node] = []
    positional: list[object] = []
    for a in args:
        if isinstance(a, Node) or is_shape_list(a):
            children.extend(flatten_shapes(a))
        else:
            positional.append(a)
    names = [p for p, _ in params]
    for i, a in enumerate(positional):
        if i < len(names):
            values[names[i]] = a
        elif isinstance(a, list) and not a:
            continue  # an empty list of children, for example from a loop with no iterations
        else:
            raise GcadError(f"'{name}' takes at most {len(names)} non-shape arguments", ctx.span)
    for key, value in kwargs.items():
        if hints and key in HINTS:
            values[key] = value
            continue
        if key not in names:
            raise GcadError(f"'{name}' has no parameter '{key}'", ctx.span)
        if key in values and values[key] is not None:
            raise GcadError(f"'{name}' got '{key}' twice", ctx.span)
        values[key] = value
    # Passing undef is the same as not passing the argument, as in OpenSCAD.
    values = {k: v for k, v in values.items() if v is not None}
    for pname, default in params:
        values.setdefault(pname, default)
    if hints:
        for h in HINTS:
            if h not in values:
                found, v = ctx.env.lookup(h)
                values[h] = v if found and v is not None else 0.0
    return values, children


def hint_args(values: dict[str, object], span: Span | None) -> dict[str, ArgValue]:
    out: dict[str, ArgValue] = {}
    for h in HINTS:
        v = as_num(values[h], h, span)
        if v < 0:
            raise GcadError(f"{h} must not be negative", span)
        if v > 0:
            out[h[1:]] = v
    return out


def radius(values: dict[str, object], r: str, d: str, span: Span | None) -> float:
    if values.get(d) is not None:
        return as_num(values[d], d, span) / 2
    return as_num(values[r], r, span)


def same_dim(children: list[Node], name: str, span: Span | None) -> int:
    dims = {c.dim for c in children}
    if len(dims) > 1:
        raise GcadError(f"'{name}' mixes 2D and 3D shapes", span)
    return dims.pop() if dims else 3


# --- geometry built-ins ----------------------------------------------------------------------


def g_box(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, _ = bind("box", [("size", 1.0), ("center", False)], args, kwargs, ctx)
    size = v["size"]
    vec = (
        [as_num(size, "size", ctx.span)] * 3
        if isinstance(size, float)
        else as_vec(size, (3,), "size", ctx.span)
    )
    if min(vec) < 0:
        raise GcadError("box size must not be negative", ctx.span)
    return ctx.ev.node(
        "box",
        {"size": tuple(vec), "center": as_bool(v["center"], "center", ctx.span)},
        [],
        ctx.span,
        3,
    )


def g_sphere(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, _ = bind("sphere", [("r", 1.0), ("d", None)], args, kwargs, ctx, hints=True)
    r = radius(v, "r", "d", ctx.span)
    if r <= 0:
        raise GcadError("sphere radius must be positive", ctx.span)
    return ctx.ev.node("sphere", {"r": r, **hint_args(v, ctx.span)}, [], ctx.span, 3)


def g_cylinder(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    # Positional order is OpenSCAD's: cylinder(h, r1, r2, center).
    params: list[tuple[str, object]] = [
        ("h", 1.0),
        ("r1", None),
        ("r2", None),
        ("center", False),
        ("r", 1.0),
        ("d", None),
        ("d1", None),
        ("d2", None),
    ]
    v, _ = bind("cylinder", params, args, kwargs, ctx, hints=True)
    base = radius(v, "r", "d", ctx.span)
    r1 = (
        as_num(v["d1"], "d1", ctx.span) / 2
        if v["d1"] is not None
        else (as_num(v["r1"], "r1", ctx.span) if v["r1"] is not None else base)
    )
    r2 = (
        as_num(v["d2"], "d2", ctx.span) / 2
        if v["d2"] is not None
        else (as_num(v["r2"], "r2", ctx.span) if v["r2"] is not None else base)
    )
    h = as_num(v["h"], "h", ctx.span)
    if h <= 0 or r1 < 0 or r2 < 0 or (r1 == 0 and r2 == 0):
        raise GcadError("cylinder needs h > 0 and a positive radius", ctx.span)
    args_out: dict[str, ArgValue] = {
        "h": h,
        "r1": r1,
        "r2": r2,
        "center": as_bool(v["center"], "center", ctx.span),
    }
    return ctx.ev.node("cylinder", {**args_out, **hint_args(v, ctx.span)}, [], ctx.span, 3)


def g_polyhedron(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    # convexity is an OpenSCAD preview hint; it is accepted and ignored.
    params: list[tuple[str, object]] = [("points", None), ("faces", None), ("convexity", None)]
    v, _ = bind("polyhedron", params, args, kwargs, ctx)
    points = as_points(v["points"], 3, "points", ctx.span)
    faces = as_index_lists(v["faces"], len(points), "faces", ctx.span)
    return ctx.ev.node("polyhedron", {"points": points, "faces": faces}, [], ctx.span, 3)


def g_rect(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, _ = bind("rect", [("size", 1.0), ("center", False)], args, kwargs, ctx)
    size = v["size"]
    vec = (
        [as_num(size, "size", ctx.span)] * 2
        if isinstance(size, float)
        else as_vec(size, (2,), "size", ctx.span)
    )
    if min(vec) < 0:
        raise GcadError("rect size must not be negative", ctx.span)
    return ctx.ev.node(
        "rect",
        {"size": tuple(vec), "center": as_bool(v["center"], "center", ctx.span)},
        [],
        ctx.span,
        2,
    )


def g_circle(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, _ = bind("circle", [("r", 1.0), ("d", None)], args, kwargs, ctx, hints=True)
    r = radius(v, "r", "d", ctx.span)
    if r <= 0:
        raise GcadError("circle radius must be positive", ctx.span)
    return ctx.ev.node("circle", {"r": r, **hint_args(v, ctx.span)}, [], ctx.span, 2)


def g_polygon(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    params: list[tuple[str, object]] = [("points", None), ("paths", None), ("convexity", None)]
    v, _ = bind("polygon", params, args, kwargs, ctx)
    points = as_points(v["points"], 2, "points", ctx.span)
    out: dict[str, ArgValue] = {"points": points}
    if v["paths"] is not None:
        out["paths"] = as_index_lists(v["paths"], len(points), "paths", ctx.span)
    return ctx.ev.node("polygon", out, [], ctx.span, 2)


def _transform(
    op: str,
    params: list[tuple[str, object]],
    build: Callable[[dict[str, object], int, Span | None], tuple[str, dict[str, ArgValue]]],
) -> BuiltinFn:
    def fn(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
        v, children = bind(op, params, args, kwargs, ctx)
        if not children:
            return empty(3, ctx.span)
        dim = same_dim(children, op, ctx.span)
        out_op, out_args = build(v, dim, ctx.span)
        return ctx.ev.node(out_op, out_args, children, ctx.span, dim)

    return fn


def _pad(vec: list[float], fill: float) -> list[float]:
    return vec + [fill] * (3 - len(vec))


def _translate(
    v: dict[str, object], dim: int, span: Span | None
) -> tuple[str, dict[str, ArgValue]]:
    vec = _pad(as_vec(v["v"], (2, 3), "translate vector", span), 0.0)
    if dim == 2 and vec[2] != 0:
        raise GcadError("a 2D shape cannot be translated along z", span)
    return "translate", {"v": tuple(vec)}


def _rotate(v: dict[str, object], dim: int, span: Span | None) -> tuple[str, dict[str, ArgValue]]:
    a = v["a"]
    if v["v"] is not None:
        axis = as_vec(v["v"], (3,), "rotation axis", span)
        if not any(axis):
            raise GcadError("rotation axis must not be zero", span)
        if dim == 2 and (axis[0] or axis[1]):
            raise GcadError("a 2D shape can only rotate about z", span)
        m = rotation_axis(as_num(a, "rotation angle", span), axis)
        return "matrix", {"m": tuple(float(x) for x in m.flatten())}
    angles = (
        [0.0, 0.0, as_num(a, "rotation angle", span)]
        if isinstance(a, float)
        else _pad(as_vec(a, (1, 2, 3), "rotation angles", span), 0.0)
    )
    if dim == 2 and (angles[0] or angles[1]):
        raise GcadError("a 2D shape can only rotate about z", span)
    return "rotate", {"a": tuple(angles)}


def _scale(v: dict[str, object], dim: int, span: Span | None) -> tuple[str, dict[str, ArgValue]]:
    s = v["v"]
    vec = (
        [as_num(s, "scale", span)] * 3
        if isinstance(s, float)
        else _pad(as_vec(s, (2, 3), "scale", span), 1.0)
    )
    return "scale", {"v": tuple(vec)}


def _mirror(v: dict[str, object], dim: int, span: Span | None) -> tuple[str, dict[str, ArgValue]]:
    vec = _pad(as_vec(v["v"], (2, 3), "mirror normal", span), 0.0)
    if not any(vec):
        raise GcadError("mirror normal must not be zero", span)
    return "mirror", {"v": tuple(vec)}


def _matrix(v: dict[str, object], dim: int, span: Span | None) -> tuple[str, dict[str, ArgValue]]:
    rows = v["m"]
    if not isinstance(rows, list) or len(rows) not in (3, 4):
        raise GcadError("matrix must be a 3x4 or 4x4 list of rows", span)
    m = [as_vec(r, (4,), "matrix row", span) for r in rows]
    if len(m) == 3:
        m.append([0.0, 0.0, 0.0, 1.0])
    return "matrix", {"m": tuple(x for row in m for x in row)}


def _boolean(op: str) -> BuiltinFn:
    def fn(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
        _, children = bind(op, [], args, kwargs, ctx)
        if not children:
            return empty(3, ctx.span)
        if len(children) == 1:
            return children[0]
        return ctx.ev.node(op, {}, children, ctx.span)

    return fn


def g_linear_extrude(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    # Positional order is OpenSCAD's; convexity is accepted and ignored.
    params: list[tuple[str, object]] = [
        ("height", 1.0),
        ("center", False),
        ("convexity", None),
        ("twist", 0.0),
        ("slices", 0.0),
        ("scale", 1.0),
    ]
    v, children = bind("linear_extrude", params, args, kwargs, ctx)
    if not children:
        return empty(3, ctx.span)
    if same_dim(children, "linear_extrude", ctx.span) != 2:
        raise GcadError("linear_extrude needs 2D shapes", ctx.span)
    h = as_num(v["height"], "height", ctx.span)
    if h <= 0:
        raise GcadError("extrusion height must be positive", ctx.span)
    s = v["scale"]
    scale = (
        [as_num(s, "scale", ctx.span)] * 2
        if isinstance(s, float)
        else as_vec(s, (2,), "scale", ctx.span)
    )
    slices = as_num(v["slices"], "slices", ctx.span)
    if slices < 0 or not slices.is_integer():
        raise GcadError("slices must be a whole number >= 0", ctx.span)
    out: dict[str, ArgValue] = {
        "height": h,
        "center": as_bool(v["center"], "center", ctx.span),
        "twist": as_num(v["twist"], "twist", ctx.span),
        "scale": tuple(scale),
        "slices": slices,
    }
    return ctx.ev.node("linear_extrude", out, children, ctx.span, 3)


def _to_2d(op: str, params: list[tuple[str, object]]) -> BuiltinFn:
    """3D -> 2D: `section` cuts at a height, `project` takes the outline seen from +z."""

    def fn(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
        v, children = bind(op, params, args, kwargs, ctx)
        if not children:
            return empty(2, ctx.span)
        if same_dim(children, op, ctx.span) != 3:
            raise GcadError(f"'{op}' needs 3D shapes", ctx.span)
        out: dict[str, ArgValue] = {}
        if "z" in v:
            out["z"] = as_num(v["z"], "z", ctx.span)
        return ctx.ev.node(op, out, children, ctx.span, 2)

    return fn


def g_rotate_extrude(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    params: list[tuple[str, object]] = [("angle", 360.0), ("convexity", None)]
    v, children = bind("rotate_extrude", params, args, kwargs, ctx, hints=True)
    if not children:
        return empty(3, ctx.span)
    if same_dim(children, "rotate_extrude", ctx.span) != 2:
        raise GcadError("rotate_extrude needs 2D shapes", ctx.span)
    angle = as_num(v["angle"], "angle", ctx.span)
    if not 0 < angle <= 360:
        raise GcadError("rotate_extrude angle must be in (0, 360]", ctx.span)
    return ctx.ev.node(
        "rotate_extrude", {"angle": angle, **hint_args(v, ctx.span)}, children, ctx.span, 3
    )


COLOR_NAMES = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "orange": "#ffa500",
    "purple": "#800080",
    "brown": "#a52a2a",
    "pink": "#ffc0cb",
    "lime": "#00ff00",
    "navy": "#000080",
    "teal": "#008080",
    "olive": "#808000",
    "maroon": "#800000",
}


def g_color(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, children = bind("color", [("c", None), ("alpha", None)], args, kwargs, ctx)
    c = v["c"]
    if isinstance(c, str):
        text = COLOR_NAMES.get(c.lower(), c)
        if not (text.startswith("#") and len(text) in (7, 9)):
            raise GcadError(f"unknown colour '{c}' (use a name, #rrggbb or [r g b a])", ctx.span)
        try:
            rgba = [int(text[i : i + 2], 16) / 255 for i in range(1, len(text), 2)]
        except ValueError:
            raise GcadError(f"invalid colour '{c}'", ctx.span) from None
    else:
        rgba = as_vec(c, (3, 4), "colour", ctx.span)
    if len(rgba) == 3:
        rgba.append(1.0)
    if v["alpha"] is not None:
        rgba[3] = as_num(v["alpha"], "alpha", ctx.span)
    if not all(0 <= x <= 1 for x in rgba):
        raise GcadError("colour components must be between 0 and 1", ctx.span)
    if not children:
        return empty(3, ctx.span)
    rounded = tuple(round(x, 6) for x in rgba)
    return ctx.ev.node("color", {"rgba": rounded}, children, ctx.span)


def g_name(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> Node:
    v, children = bind("name", [("n", None)], args, kwargs, ctx)
    if not isinstance(v["n"], str) or not v["n"]:
        raise GcadError("name needs a non-empty string", ctx.span)
    if not children:
        return empty(3, ctx.span)
    return ctx.ev.node("name", {"n": v["n"]}, children, ctx.span)


# --- numeric and list built-ins ----------------------------------------------------------------


def _numeric(
    name: str, fn: Callable[[float], float], domain: Callable[[float], bool] | None = None
) -> BuiltinFn:
    def call(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
        if len(args) != 1 or kwargs:
            raise GcadError(f"'{name}' takes one number", ctx.span)
        x = as_num(args[0], f"argument of {name}", ctx.span)
        if domain is not None and not domain(x):
            raise GcadError(f"'{name}' is undefined for {format_number(x)}", ctx.span)
        result = float(fn(x))
        if not math.isfinite(result):
            raise GcadError(f"'{name}' of {format_number(x)} is not finite", ctx.span)
        return result

    return call


def _round(x: float) -> float:
    return float(math.floor(x + 0.5)) if x >= 0 else -float(math.floor(-x + 0.5))


def _add2(a: object, b: object, sign: float, span: Span | None) -> object:
    if (
        isinstance(a, float)
        and isinstance(b, float)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return a + sign * b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise GcadError("cannot add vectors of different lengths", span)
        return [_add2(x, y, sign, span) for x, y in zip(a, b, strict=True)]
    raise GcadError(f"cannot add a {type_name(a)} and a {type_name(b)}", span)


def _mul2(a: object, b: object, span: Span | None) -> object:
    a_num = isinstance(a, float) and not isinstance(a, bool)
    b_num = isinstance(b, float) and not isinstance(b, bool)
    if a_num and b_num:
        return a * b  # type: ignore[operator]
    if a_num and isinstance(b, list):
        return [_mul2(a, x, span) for x in b]
    if b_num and isinstance(a, list):
        return [_mul2(x, b, span) for x in a]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise GcadError("dot product of vectors with different lengths", span)
        total: object = 0.0
        for x, y in zip(a, b, strict=True):
            total = _add2(total, _mul2(x, y, span), 1.0, span)
        return total
    raise GcadError(f"cannot multiply a {type_name(a)} and a {type_name(b)}", span)


def b_add(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> object:
    if not args:
        return 0.0
    total = args[0]
    for x in args[1:]:
        total = _add2(total, x, 1.0, ctx.span)
    return total


def b_sub(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> object:
    if not args:
        raise GcadError("'-' needs at least one argument", ctx.span)
    if len(args) == 1:
        return _mul2(-1.0, args[0], ctx.span)
    total = args[0]
    for x in args[1:]:
        total = _add2(total, x, -1.0, ctx.span)
    return total


def b_mul(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> object:
    total: object = 1.0
    for x in args:
        total = _mul2(total, x, ctx.span)
    return total


def b_div(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> object:
    if len(args) != 2:
        raise GcadError("'/' takes two arguments", ctx.span)
    d = as_num(args[1], "divisor", ctx.span)
    if d == 0:
        raise GcadError("division by zero", ctx.span)
    return _mul2(args[0], 1.0 / d, ctx.span)


def b_mod(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
    if len(args) != 2:
        raise GcadError("'%' takes two arguments", ctx.span)
    a, b = as_num(args[0], "dividend", ctx.span), as_num(args[1], "divisor", ctx.span)
    if b == 0:
        raise GcadError("modulo by zero", ctx.span)
    return math.fmod(a, b)


def _compare(name: str, op: Callable[[float, float], bool]) -> BuiltinFn:
    def call(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> bool:
        if len(args) < 2:
            raise GcadError(f"'{name}' needs at least two arguments", ctx.span)
        nums = [as_num(a, f"argument of {name}", ctx.span) for a in args]
        return all(op(x, y) for x, y in zip(nums, nums[1:], strict=False))

    return call


def b_eq(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> bool:
    return all(_equal(args[0], x) for x in args[1:]) if args else True


def _equal(a: object, b: object) -> bool:
    if isinstance(a, Node) and isinstance(b, Node):
        return a.id == b.id
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def b_ne(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> bool:
    if len(args) != 2:
        raise GcadError("'!=' takes two arguments", ctx.span)
    return not _equal(args[0], args[1])


def b_not(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> bool:
    if len(args) != 1:
        raise GcadError("'not' takes one argument", ctx.span)
    return not truthy(args[0])


def b_pow(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
    if len(args) != 2:
        raise GcadError("'pow' takes two numbers", ctx.span)
    try:
        result = math.pow(as_num(args[0], "base", ctx.span), as_num(args[1], "exponent", ctx.span))
    except (ValueError, OverflowError):
        raise GcadError("pow result is undefined or too large", ctx.span) from None
    return result


def _min_max(name: str, pick: Callable[[list[float]], float]) -> BuiltinFn:
    def call(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
        values = args[0] if len(args) == 1 and isinstance(args[0], list) else args
        if not values:
            raise GcadError(f"'{name}' needs at least one number", ctx.span)
        return pick([as_num(v, f"argument of {name}", ctx.span) for v in values])

    return call


def b_atan2(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
    if len(args) != 2:
        raise GcadError("'atan2' takes two numbers (y x)", ctx.span)
    return math.degrees(math.atan2(as_num(args[0], "y", ctx.span), as_num(args[1], "x", ctx.span)))


def b_len(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
    if len(args) != 1 or not isinstance(args[0], list | str):
        raise GcadError("'len' takes one list or string", ctx.span)
    return float(len(args[0]))


def b_concat(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> list[object]:
    out: list[object] = []
    for a in args:
        out.extend(a) if isinstance(a, list) else out.append(a)
    return out


def b_range(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> list[object]:
    """(range start end [step]): inclusive of end, like OpenSCAD's [start:step:end]."""
    if len(args) not in (2, 3):
        raise GcadError("'range' takes start, end and an optional step", ctx.span)
    start = as_num(args[0], "range start", ctx.span)
    end = as_num(args[1], "range end", ctx.span)
    step = as_num(args[2], "range step", ctx.span) if len(args) == 3 else 1.0
    if step == 0:
        raise GcadError("range step must not be zero", ctx.span)
    count = math.floor((end - start) / step + 1e-9) + 1
    if count <= 0:
        return []
    ctx.ev.count_iterations(count, ctx.span)
    return [start + i * step for i in range(count)]


def b_get(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> object:
    if len(args) != 2 or not isinstance(args[0], list | str):
        raise GcadError("'get' takes a list (or string) and an index", ctx.span)
    i = as_num(args[1], "index", ctx.span)
    seq = args[0]
    if not i.is_integer() or not 0 <= i < len(seq):
        return None
    return seq[int(i)]


def b_norm(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> float:
    if len(args) != 1:
        raise GcadError("'norm' takes one vector", ctx.span)
    return float(np.linalg.norm(as_vec(args[0], tuple(range(1, 17)), "norm argument", ctx.span)))


def b_cross(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> list[object]:
    if len(args) != 2:
        raise GcadError("'cross' takes two 3D vectors", ctx.span)
    a = as_vec(args[0], (3,), "cross argument", ctx.span)
    b = as_vec(args[1], (3,), "cross argument", ctx.span)
    return [float(x) for x in np.cross(a, b)]


def b_str(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> str:
    return "".join(show(a) for a in args)


def b_echo(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> None:
    parts = [show(a) for a in args] + [f"{k} = {show(v)}" for k, v in kwargs.items()]
    ctx.ev.info("ECHO: " + ", ".join(parts), ctx.span)
    return None


def b_assert(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> None:
    """(assert cond [message]): stop with an error when cond is false."""
    if not args or len(args) > 2:
        raise GcadError("'assert' takes a condition and an optional message", ctx.span)
    if not truthy(args[0]):
        detail = f": {show(args[1])}" if len(args) == 2 else ""
        raise GcadError(f"assertion failed{detail}", ctx.span)
    return None


def _is(name: str, test: Callable[[object], bool]) -> BuiltinFn:
    def call(args: list[object], kwargs: dict[str, object], ctx: CallCtx) -> bool:
        if len(args) != 1:
            raise GcadError(f"'{name}' takes one argument", ctx.span)
        return test(args[0])

    return call


def global_env() -> Env:
    env = Env()
    fns: dict[str, BuiltinFn] = {
        # geometry (SPEC §5.3)
        "box": g_box,
        "sphere": g_sphere,
        "cylinder": g_cylinder,
        "polyhedron": g_polyhedron,
        "rect": g_rect,
        "circle": g_circle,
        "polygon": g_polygon,
        "translate": _transform("translate", [("v", None)], _translate),
        "rotate": _transform("rotate", [("a", 0.0), ("v", None)], _rotate),
        "scale": _transform("scale", [("v", None)], _scale),
        "mirror": _transform("mirror", [("v", None)], _mirror),
        "matrix": _transform("matrix", [("m", None)], _matrix),
        "union": _boolean("union"),
        "difference": _boolean("difference"),
        "intersection": _boolean("intersection"),
        "linear_extrude": g_linear_extrude,
        "rotate_extrude": g_rotate_extrude,
        "section": _to_2d("section", [("z", 0.0)]),
        "project": _to_2d("project", []),
        "color": g_color,
        "name": g_name,
        # arithmetic and logic
        "+": b_add,
        "-": b_sub,
        "*": b_mul,
        "/": b_div,
        "%": b_mod,
        "<": _compare("<", lambda a, b: a < b),
        "<=": _compare("<=", lambda a, b: a <= b),
        ">": _compare(">", lambda a, b: a > b),
        ">=": _compare(">=", lambda a, b: a >= b),
        "==": b_eq,
        "!=": b_ne,
        "not": b_not,
        # math, angles in degrees
        "sin": _numeric("sin", lambda x: math.sin(math.radians(x))),
        "cos": _numeric("cos", lambda x: math.cos(math.radians(x))),
        "tan": _numeric("tan", lambda x: math.tan(math.radians(x))),
        "asin": _numeric("asin", lambda x: math.degrees(math.asin(x)), lambda x: -1 <= x <= 1),
        "acos": _numeric("acos", lambda x: math.degrees(math.acos(x)), lambda x: -1 <= x <= 1),
        "atan": _numeric("atan", lambda x: math.degrees(math.atan(x))),
        "atan2": b_atan2,
        "sqrt": _numeric("sqrt", math.sqrt, lambda x: x >= 0),
        "exp": _numeric("exp", math.exp, lambda x: x < 700),
        "ln": _numeric("ln", math.log, lambda x: x > 0),
        "log": _numeric("log", math.log10, lambda x: x > 0),
        "abs": _numeric("abs", abs),
        "floor": _numeric("floor", math.floor),
        "ceil": _numeric("ceil", math.ceil),
        "round": _numeric("round", _round),
        "pow": b_pow,
        "min": _min_max("min", min),
        "max": _min_max("max", max),
        # lists and strings
        "len": b_len,
        "concat": b_concat,
        "range": b_range,
        "get": b_get,
        "norm": b_norm,
        "cross": b_cross,
        "str": b_str,
        "echo": b_echo,
        "assert": b_assert,
        "is_undef": _is("is_undef", lambda v: v is None),
        "is_num": _is("is_num", lambda v: isinstance(v, float) and not isinstance(v, bool)),
        "is_bool": _is("is_bool", lambda v: isinstance(v, bool)),
        "is_string": _is("is_string", lambda v: isinstance(v, str)),
        "is_list": _is("is_list", lambda v: isinstance(v, list)),
        "is_function": _is("is_function", lambda v: isinstance(v, Closure | Builtin)),
    }
    for name, fn in fns.items():
        env.vars[name] = Builtin(name, fn)
    env.vars.update({"PI": math.pi, "true": True, "false": False, "undef": None})
    return env
