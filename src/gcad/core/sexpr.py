"""GCore surface syntax: an S-expression reader and printer that keep source spans (FR-C1).

Syntax:
    ; comment to end of line
    42  -1.5  2e-3          numbers
    "text"                  strings (JSON escapes)
    name  $fn  +  <=        symbols
    :name                   keywords, used for named arguments
    ( ... )                 call or special form
    [ ... ]                 vector literal
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from gcad.diagnostics import GcadError, Span


@dataclass(frozen=True)
class Num:
    value: float
    span: Span | None = None


@dataclass(frozen=True)
class Str:
    value: str
    span: Span | None = None


@dataclass(frozen=True)
class Sym:
    name: str
    span: Span | None = None


@dataclass(frozen=True)
class Kw:
    name: str
    span: Span | None = None


@dataclass(frozen=True)
class SList:
    items: tuple[Expr, ...]
    bracket: str = "("  # "(" for calls and forms, "[" for vector literals
    span: Span | None = None


Expr = Num | Str | Sym | Kw | SList

_DELIMS = set('()[];"') | set(" \t\r\n\f\v")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_CLOSE = {"(": ")", "[": "]"}


class _Reader:
    def __init__(self, text: str, file: str) -> None:
        self.text = text
        self.file = file
        self.pos = 0
        self.line = 1
        self.col = 1

    def span_from(self, start: int, line: int, col: int) -> Span:
        return Span(self.file, line, col, start, self.pos)

    def here(self) -> Span:
        return Span(self.file, self.line, self.col, self.pos, self.pos)

    def advance(self, n: int = 1) -> None:
        for _ in range(n):
            if self.text[self.pos] == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.pos += 1

    def skip_blank(self) -> None:
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == ";":
                while self.pos < len(self.text) and self.text[self.pos] != "\n":
                    self.advance()
            elif ch.isspace():
                self.advance()
            else:
                return

    def read_all(self) -> list[Expr]:
        forms: list[Expr] = []
        while True:
            self.skip_blank()
            if self.pos >= len(self.text):
                return forms
            forms.append(self.read())

    def read(self) -> Expr:
        self.skip_blank()
        if self.pos >= len(self.text):
            raise GcadError("unexpected end of input", self.here())
        ch = self.text[self.pos]
        start, line, col = self.pos, self.line, self.col
        if ch in _CLOSE:
            self.advance()
            items: list[Expr] = []
            while True:
                self.skip_blank()
                if self.pos >= len(self.text):
                    raise GcadError(
                        f"'{ch}' is never closed", Span(self.file, line, col, start, start + 1)
                    )
                if self.text[self.pos] == _CLOSE[ch]:
                    self.advance()
                    return SList(tuple(items), ch, self.span_from(start, line, col))
                if self.text[self.pos] in ")]":
                    raise GcadError(
                        f"expected '{_CLOSE[ch]}' but found '{self.text[self.pos]}'", self.here()
                    )
                items.append(self.read())
        if ch in ")]":
            raise GcadError(f"unexpected '{ch}'", self.here())
        if ch == '"':
            return self.read_string(start, line, col)
        while self.pos < len(self.text) and self.text[self.pos] not in _DELIMS:
            self.advance()
        token = self.text[start : self.pos]
        span = self.span_from(start, line, col)
        if _NUMBER.fullmatch(token):
            return Num(float(token), span)
        if token.startswith(":") and len(token) > 1:
            return Kw(token[1:], span)
        return Sym(token, span)

    def read_string(self, start: int, line: int, col: int) -> Str:
        self.advance()
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\":
                self.advance(2)
                continue
            if ch == '"':
                self.advance()
                raw = self.text[start : self.pos]
                span = self.span_from(start, line, col)
                try:
                    return Str(json.loads(raw), span)
                except json.JSONDecodeError as exc:
                    raise GcadError(f"invalid string literal: {exc.msg}", span) from None
            if ch == "\n":
                break
            self.advance()
        raise GcadError("string is never closed", Span(self.file, line, col, start, start + 1))


def read(text: str, file: str = "<input>") -> list[Expr]:
    """Parse GCore source text into a list of top-level forms."""
    return _Reader(text, file).read_all()


def format_number(value: float) -> str:
    """Shortest round-trip text for a number; integral values print without a decimal point."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"not a finite number: {value}")
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def to_text(expr: Expr, width: int = 88, indent: int = 0) -> str:
    """Canonical GCore text. Breaks long lists one item per line."""
    flat = _flat(expr)
    if len(flat) + indent <= width or not isinstance(expr, SList) or len(expr.items) < 2:
        return flat
    close = _CLOSE[expr.bracket]
    head = to_text(expr.items[0], width, indent + 1)
    pad = " " * (indent + 2)
    rest = [pad + to_text(item, width, indent + 2) for item in expr.items[1:]]
    return expr.bracket + head + "\n" + "\n".join(rest) + close


def _flat(expr: Expr) -> str:
    if isinstance(expr, Num):
        return format_number(expr.value)
    if isinstance(expr, Str):
        return json.dumps(expr.value, ensure_ascii=False)
    if isinstance(expr, Sym):
        return expr.name
    if isinstance(expr, Kw):
        return ":" + expr.name
    return expr.bracket + " ".join(_flat(i) for i in expr.items) + _CLOSE[expr.bracket]


def to_json(expr: Expr, spans: bool = False) -> object:
    """GCore JSON form: numbers stay numbers, everything else is a one-key object."""
    out: dict[str, object]
    if isinstance(expr, Num):
        if not spans:
            return expr.value
        out = {"num": expr.value}
    elif isinstance(expr, Str):
        out = {"str": expr.value}
    elif isinstance(expr, Sym):
        out = {"sym": expr.name}
    elif isinstance(expr, Kw):
        out = {"kw": expr.name}
    else:
        key = "vec" if expr.bracket == "[" else "list"
        out = {key: [to_json(i, spans) for i in expr.items]}
    if spans and expr.span is not None:
        out["span"] = expr.span.to_json()
    return out
