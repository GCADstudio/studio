"""Numbers that remember how they were computed from a program's parameters (SPEC FR-C10).

In trace mode the evaluator gives every top-level `(define name number)` of the user's program a
Traced value. Arithmetic on Traced numbers gives Traced numbers whose `expr` is the computation:

    ("param", name, value)   a top-level definition
    ("num", value)           a constant
    ("+", a, b)  ("-", a, b)  ("*", a, b)  ("/", a, b)  ("pow", a, b)
    ("neg", a)  ("sqrt", a)  ("abs", a)  ("exp", a)  ("ln", a)  ("log", a)

A Traced number is a float in every other respect: it hashes, compares and prints as its value,
so a graph built from them is the graph built without trace mode (FR-G1). Anything else (a
comparison, rounding, trigonometry in degrees) gives a plain float, and the trace ends there.
The parametric STEP writer turns traces into ISO 10303-108 parameters and ISO 13584-20
expressions.
"""

from __future__ import annotations

Expr = tuple[object, ...]


def expr_of(x: object) -> Expr:
    return x.expr if isinstance(x, Traced) else ("num", float(x))  # type: ignore[arg-type]


class Traced(float):
    __slots__ = ("expr",)
    expr: Expr

    def __new__(cls, value: float, expr: Expr) -> Traced:
        obj = super().__new__(cls, value)
        obj.expr = expr
        return obj

    @staticmethod
    def wrap(value: float, expr: Expr) -> float:
        return Traced(value, expr)

    def __add__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        b = expr_of(other)
        if b[0] == "neg":
            return Traced(float(self) + float(other), ("-", self.expr, b[1]))
        return Traced(float(self) + float(other), ("+", self.expr, b))

    def __radd__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        if float(other) == 0:
            return Traced(float(self), self.expr)
        return Traced(float(other) + float(self), ("+", expr_of(other), self.expr))

    def __sub__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        return Traced(float(self) - float(other), ("-", self.expr, expr_of(other)))

    def __rsub__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        return Traced(float(other) - float(self), ("-", expr_of(other), self.expr))

    def __mul__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        if float(other) == -1 and not isinstance(other, Traced):
            return Traced(-float(self), ("neg", self.expr))
        if float(other) == 1 and not isinstance(other, Traced):
            return Traced(float(self), self.expr)
        return Traced(float(self) * float(other), ("*", self.expr, expr_of(other)))

    def __rmul__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        if float(other) == -1:
            return Traced(-float(self), ("neg", self.expr))
        if float(other) == 1:
            return Traced(float(self), self.expr)
        return Traced(float(other) * float(self), ("*", expr_of(other), self.expr))

    def __truediv__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        return Traced(float(self) / float(other), ("/", self.expr, expr_of(other)))

    def __rtruediv__(self, other: object) -> float:
        if not isinstance(other, float | int):
            return NotImplemented
        return Traced(float(other) / float(self), ("/", expr_of(other), self.expr))

    def __pow__(self, other: object) -> float:  # type: ignore[override]
        if not isinstance(other, float | int):
            return NotImplemented
        return Traced(float(self) ** float(other), ("pow", self.expr, expr_of(other)))

    def __neg__(self) -> float:
        return Traced(-float(self), ("neg", self.expr))

    def __pos__(self) -> float:
        return self

    def __abs__(self) -> float:
        return Traced(abs(float(self)), ("abs", self.expr))


def traced_call(name: str, x: float, result: float) -> float:
    """The result of a one-argument function, traced when its argument is and the function has
    an ISO 13584-20 counterpart."""
    if isinstance(x, Traced) and name in ("sqrt", "abs", "exp", "ln", "log"):
        return Traced(result, (name, x.expr))
    return result
