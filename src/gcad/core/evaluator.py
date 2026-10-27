"""GCore evaluator: a small, pure functional language that produces a geometry graph (SPEC §5.1).

Special forms:
    (define name expr)              bind in the enclosing block
    (define (name params...) body)  named function; hoisted, so it may be used before definition
    (module (name params...) body)  named shape function; shape arguments become `children`
    (fn (params...) body...)        lambda; a parameter is `name` or `(name default)`
    (let ((name expr)...) body...)  sequential local bindings
    (if cond then [else])
    (for ((name list)...) body...)  list comprehension; several bindings nest
    (and a b...) (or a b...)        short-circuit logic

A body is a block: its value is its single expression, or, when it has several, the list of
shapes they produce (an implicit union, as in OpenSCAD).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from gcad.core.graph import ArgValue, Graph, Node, empty, make_node
from gcad.core.sexpr import Expr, Kw, Num, SList, Str, Sym
from gcad.diagnostics import Diagnostic, GcadError, Severity, Span


class Shapes(list[object]):
    """The value of a block with several shape expressions: an implicit group of children."""


@dataclass
class Limits:
    """Guards against runaway or hostile sources (FR-C6)."""

    max_depth: int = 1000
    max_iterations: int = 1_000_000
    max_nodes: int = 100_000
    max_seconds: float = 10.0


class Env:
    def __init__(self, parent: Env | None = None) -> None:
        self.vars: dict[str, object] = {}
        self.parent = parent

    def lookup(self, name: str) -> tuple[bool, object]:
        env: Env | None = self
        while env is not None:
            if name in env.vars:
                return True, env.vars[name]
            env = env.parent
        return False, None


@dataclass
class Closure:
    params: list[tuple[str, Expr | None]]
    body: list[Expr]
    env: Env
    name: str = "<fn>"
    is_module: bool = False
    span: Span | None = None


@dataclass
class CallCtx:
    span: Span | None
    env: Env
    ev: Evaluator


BuiltinFn = Callable[[list[object], dict[str, object], CallCtx], object]


@dataclass
class Builtin:
    name: str
    fn: BuiltinFn


@dataclass
class EvalResult:
    graph: Graph | None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.graph is not None and not any(
            d.severity is Severity.ERROR for d in self.diagnostics
        )


SPECIAL_FORMS = frozenset({"define", "module", "fn", "let", "if", "for", "and", "or"})


def is_shape_list(value: object) -> bool:
    if isinstance(value, Shapes):
        return True
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(v, Node) or is_shape_list(v) for v in value)
    )


def flatten_shapes(value: object) -> list[Node]:
    if isinstance(value, Node):
        return [value]
    out: list[Node] = []
    for item in value:  # type: ignore[attr-defined]
        out.extend(flatten_shapes(item))
    return out


def type_name(value: object) -> str:
    if value is None:
        return "undef"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Node):
        return f"{value.dim}D shape"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Closure | Builtin):
        return "function"
    return type(value).__name__


class Evaluator:
    def __init__(self, limits: Limits | None = None) -> None:
        from gcad.core.builtins import global_env  # local import: builtins needs this module

        self.limits = limits or Limits()
        self.globals = global_env()
        self.diagnostics: list[Diagnostic] = []
        self.depth = 0
        self.iterations = 0
        self.node_count = 0
        self.deadline = 0.0

    # -- entry point ----------------------------------------------------------------------

    def run(self, forms: list[Expr], metadata: dict[str, str] | None = None) -> EvalResult:
        self.deadline = time.monotonic() + self.limits.max_seconds
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 40 * self.limits.max_depth + 1000))
        try:
            value = self.block(forms, Env(self.globals), top_level=True)
            shapes = self.collect_top_level(value, forms)
            if not shapes:
                root = empty()
            elif len(shapes) == 1:
                root = shapes[0]
            else:
                root = self.node("union", {}, shapes, None)
            return EvalResult(Graph(root, metadata=dict(metadata or {})), self.diagnostics)
        except GcadError as exc:
            self.diagnostics.append(exc.diagnostic)
        except RecursionError:
            self.diagnostics.append(
                Diagnostic(Severity.ERROR, "recursion too deep for the evaluator stack")
            )
        finally:
            sys.setrecursionlimit(old_limit)
        return EvalResult(None, self.diagnostics)

    def collect_top_level(self, value: object, forms: list[Expr]) -> list[Node]:
        if value is None:
            return []
        if isinstance(value, Node) or is_shape_list(value):
            return flatten_shapes(value)
        if isinstance(value, list) and not value:
            return []
        span = forms[-1].span if forms else None
        self.warn(f"top-level value of type {type_name(value)} is ignored", span)
        return []

    # -- helpers --------------------------------------------------------------------------

    def warn(self, message: str, span: Span | None) -> None:
        self.diagnostics.append(Diagnostic(Severity.WARNING, message, span))

    def info(self, message: str, span: Span | None) -> None:
        self.diagnostics.append(Diagnostic(Severity.INFO, message, span))

    def tick(self, span: Span | None) -> None:
        if time.monotonic() > self.deadline:
            raise GcadError(
                f"evaluation exceeded the time limit of {self.limits.max_seconds:g} s", span
            )

    def count_iterations(self, n: int, span: Span | None) -> None:
        self.iterations += n
        if self.iterations > self.limits.max_iterations:
            raise GcadError(
                f"more than {self.limits.max_iterations} loop iterations in total", span
            )

    def node(
        self,
        op: str,
        args: Mapping[str, ArgValue],
        children: list[Node],
        span: Span | None,
        dim: int | None = None,
    ) -> Node:
        self.node_count += 1
        if self.node_count > self.limits.max_nodes:
            raise GcadError(f"model has more than {self.limits.max_nodes} geometry nodes", span)
        if dim is None:
            dims = {c.dim for c in children}
            if len(dims) > 1:
                raise GcadError(f"'{op}' mixes 2D and 3D shapes", span)
            dim = dims.pop() if dims else 3
        return make_node(op, args, children, dim, span)

    # -- evaluation -----------------------------------------------------------------------

    def eval(self, expr: Expr, env: Env) -> object:
        if isinstance(expr, Num):
            return expr.value
        if isinstance(expr, Str):
            return expr.value
        if isinstance(expr, Sym):
            found, value = env.lookup(expr.name)
            if not found:
                raise GcadError(f"'{expr.name}' is not defined", expr.span)
            return value
        if isinstance(expr, Kw):
            raise GcadError(f"keyword ':{expr.name}' outside a call", expr.span)
        if expr.bracket == "[":
            return [self.eval(item, env) for item in expr.items]
        if not expr.items:
            raise GcadError("empty call '()'", expr.span)
        head = expr.items[0]
        if isinstance(head, Sym) and head.name in SPECIAL_FORMS:
            if head.name in ("define", "module"):
                raise GcadError(
                    f"'{head.name}' is only allowed in a body or at top level", expr.span
                )
            return getattr(self, "form_" + head.name)(expr, env)
        fn = self.eval(head, env)
        args: list[object] = []
        kwargs: dict[str, object] = {}
        items = expr.items[1:]
        i = 0
        while i < len(items):
            item = items[i]
            if isinstance(item, Kw):
                if i + 1 >= len(items):
                    raise GcadError(f"keyword ':{item.name}' has no value", item.span)
                if item.name in kwargs:
                    raise GcadError(f"argument '{item.name}' given twice", item.span)
                kwargs[item.name] = self.eval(items[i + 1], env)
                i += 2
            else:
                args.append(self.eval(item, env))
                i += 1
        return self.apply(fn, args, kwargs, expr.span, env)

    def apply(
        self,
        fn: object,
        args: list[object],
        kwargs: dict[str, object],
        span: Span | None,
        env: Env,
    ) -> object:
        self.tick(span)
        if isinstance(fn, Builtin):
            return fn.fn(args, kwargs, CallCtx(span, env, self))
        if not isinstance(fn, Closure):
            raise GcadError(f"a {type_name(fn)} is not callable", span)
        self.depth += 1
        if self.depth > self.limits.max_depth:
            raise GcadError(
                f"call depth exceeded {self.limits.max_depth} (unbounded recursion?)", span
            )
        try:
            local = Env(fn.env)
            children: list[Node] = []
            if fn.is_module:
                plain = []
                for a in args:
                    if isinstance(a, Node) or is_shape_list(a):
                        children.extend(flatten_shapes(a))
                    else:
                        plain.append(a)
                args = plain
                local.vars["children"] = Shapes(children)
            if len(args) > len(fn.params):
                raise GcadError(
                    f"'{fn.name}' takes {len(fn.params)} arguments but {len(args)} were given",
                    span,
                )
            names = {p for p, _ in fn.params}
            for key, value in kwargs.items():
                if key not in names and not key.startswith("$"):
                    raise GcadError(f"'{fn.name}' has no parameter '{key}'", span)
                if key.startswith("$"):
                    local.vars[key] = value
            for idx, (pname, default) in enumerate(fn.params):
                if idx < len(args):
                    local.vars[pname] = args[idx]
                elif pname in kwargs:
                    local.vars[pname] = kwargs[pname]
                elif default is not None:
                    local.vars[pname] = self.eval(default, local)
                else:
                    local.vars[pname] = None
            return self.block(fn.body, local)
        finally:
            self.depth -= 1

    def block(
        self, forms: list[Expr] | tuple[Expr, ...], env: Env, top_level: bool = False
    ) -> object:
        # Hoist named functions and modules so they can be used before their definition.
        for form in forms:
            if _is_form(form, "module") or (_is_form(form, "define") and _defines_function(form)):
                name, closure = self.make_named_closure(form, env)  # type: ignore[arg-type]
                env.vars[name] = closure
        results: list[tuple[object, Expr]] = []
        for form in forms:
            if _is_form(form, "module") or (_is_form(form, "define") and _defines_function(form)):
                continue
            if _is_form(form, "define"):
                self.define_value(form, env)  # type: ignore[arg-type]
                continue
            results.append((self.eval(form, env), form))
        if not results:
            return None
        if len(results) == 1:
            return results[0][0]
        shapes = Shapes()
        for value, form in results:
            if isinstance(value, Node) or is_shape_list(value):
                shapes.extend(flatten_shapes(value))
            elif value is None or (isinstance(value, list) and not value):
                continue
            elif top_level:
                self.warn(f"top-level value of type {type_name(value)} is ignored", form.span)
            else:
                raise GcadError(
                    "a body with several expressions must produce shapes, "
                    f"not a {type_name(value)}",
                    form.span,
                )
        return shapes

    # -- special forms --------------------------------------------------------------------

    def make_named_closure(self, form: SList, env: Env) -> tuple[str, Closure]:
        keyword = form.items[0].name  # type: ignore[union-attr]
        if len(form.items) < 3 or not isinstance(form.items[1], SList) or not form.items[1].items:
            raise GcadError(f"expected ({keyword} (name params...) body...)", form.span)
        signature = form.items[1]
        name_sym = signature.items[0]
        if not isinstance(name_sym, Sym):
            raise GcadError(f"{keyword} name must be a symbol", signature.span)
        params = self.parse_params(signature.items[1:], form.span)
        closure = Closure(
            params, list(form.items[2:]), env, name_sym.name, keyword == "module", form.span
        )
        return name_sym.name, closure

    def define_value(self, form: SList, env: Env) -> None:
        if len(form.items) != 3 or not isinstance(form.items[1], Sym):
            raise GcadError("expected (define name expr)", form.span)
        env.vars[form.items[1].name] = self.eval(form.items[2], env)

    def parse_params(
        self, items: tuple[Expr, ...], span: Span | None
    ) -> list[tuple[str, Expr | None]]:
        params: list[tuple[str, Expr | None]] = []
        for item in items:
            if isinstance(item, Sym):
                params.append((item.name, None))
            elif (
                isinstance(item, SList)
                and item.bracket == "("
                and len(item.items) == 2
                and isinstance(item.items[0], Sym)
            ):
                params.append((item.items[0].name, item.items[1]))
            else:
                raise GcadError("a parameter is a name or (name default)", item.span or span)
        return params

    def form_fn(self, form: SList, env: Env) -> Closure:
        if len(form.items) < 3 or not isinstance(form.items[1], SList):
            raise GcadError("expected (fn (params...) body...)", form.span)
        params = self.parse_params(form.items[1].items, form.span)
        return Closure(params, list(form.items[2:]), env, "<fn>", False, form.span)

    def bindings(self, form: SList, what: str) -> list[tuple[str, Expr]]:
        if len(form.items) < 3 or not isinstance(form.items[1], SList):
            raise GcadError(f"expected ({what} ((name expr)...) body...)", form.span)
        out = []
        for b in form.items[1].items:
            if not (isinstance(b, SList) and len(b.items) == 2 and isinstance(b.items[0], Sym)):
                raise GcadError(f"a {what} binding is (name expr)", b.span or form.span)
            out.append((b.items[0].name, b.items[1]))
        return out

    def form_let(self, form: SList, env: Env) -> object:
        local = Env(env)
        for name, expr in self.bindings(form, "let"):
            local.vars[name] = self.eval(expr, local)
        return self.block(form.items[2:], local)

    def form_if(self, form: SList, env: Env) -> object:
        if len(form.items) not in (3, 4):
            raise GcadError("expected (if cond then [else])", form.span)
        if truthy(self.eval(form.items[1], env)):
            return self.eval(form.items[2], env)
        return self.eval(form.items[3], env) if len(form.items) == 4 else None

    def form_for(self, form: SList, env: Env) -> list[object]:
        binds = self.bindings(form, "for")
        out: list[object] = []

        def loop(level: int, local: Env) -> None:
            if level == len(binds):
                self.count_iterations(1, form.span)
                self.tick(form.span)
                value = self.block(form.items[2:], Env(local))
                if isinstance(value, Shapes):
                    out.extend(value)
                else:
                    out.append(value)
                return
            name, expr = binds[level]
            seq = self.eval(expr, local)
            if not isinstance(seq, list):
                raise GcadError(f"'for' needs a list, got a {type_name(seq)}", expr.span)
            for item in seq:
                inner = Env(local)
                inner.vars[name] = item
                loop(level + 1, inner)

        loop(0, env)
        return out

    def form_and(self, form: SList, env: Env) -> bool:
        return all(truthy(self.eval(e, env)) for e in form.items[1:])

    def form_or(self, form: SList, env: Env) -> bool:
        return any(truthy(self.eval(e, env)) for e in form.items[1:])


def truthy(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, float):
        return value != 0
    if isinstance(value, str | list):
        return len(value) > 0
    return True


def _is_form(expr: Expr, keyword: str) -> bool:
    return (
        isinstance(expr, SList)
        and expr.bracket == "("
        and bool(expr.items)
        and isinstance(expr.items[0], Sym)
        and expr.items[0].name == keyword
    )


def _defines_function(form: Expr) -> bool:
    return isinstance(form, SList) and len(form.items) > 1 and isinstance(form.items[1], SList)


def evaluate(
    forms: list[Expr], limits: Limits | None = None, metadata: dict[str, str] | None = None
) -> EvalResult:
    return Evaluator(limits).run(forms, metadata)
