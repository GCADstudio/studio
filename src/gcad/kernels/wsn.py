"""Wirth Syntax Notation to Lark (SPEC FR-K8): read a grammar written in WSN, the notation
ISO 10303-11 and ISO 10303-21 specify their syntax in, and write it as a Lark grammar.

    gcad grammar import --notation wsn productions.txt --start EXCHANGE_FILE > grammar.lark

WSN, as Wirth published it (Communications of the ACM 20:11, 1977): a production is an
identifier, "=", an expression and a full stop; an expression is terms separated by "|"; a term
is a sequence of factors; a factor is an identifier, a literal, or an expression in ( ), [ ]
(optional) or { } (repeated). A literal is written in double quotes, a quote inside it twice.

Every identifier becomes a Lark rule (lowercased), every literal a string terminal, so the result
is a character-level grammar for Lark's Earley parser with its dynamic lexer: no token level, no
separators, exactly the language the productions define. That is what a hand-written grammar is
checked against (docs/design/iso10303-kernel.md §1.4); it is not meant to be a kernel's grammar.

A production whose closing full stop is missing (ISO 10303-21:2002 has two) is closed at the
next `identifier =`, and reported.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from gcad.diagnostics import GcadError

_TOKEN = re.compile(
    r'\s*(?:(?P<lit>"(?:[^"]|"")*")|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[=|.()\[\]{}]))'
)

Node = tuple  # ("alt", [...]) ("seq", [...]) ("opt", x) ("rep", x) ("id", name) ("lit", text)


@dataclass
class Grammar:
    productions: dict[str, Node] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_lark(self, start: str) -> str:
        if start not in self.productions:
            raise GcadError(f"no production {start}")
        lines = [f"start: {rule(start)}", ""]
        for name, body in self.productions.items():
            lines.append(f"{rule(name)}: {lark(body)}")
        missing = sorted(self.undefined())
        if missing:
            raise GcadError(f"identifiers used but not defined: {', '.join(missing)}")
        return "\n".join(lines) + "\n"

    def undefined(self) -> set[str]:
        used: set[str] = set()

        def walk(n: Node) -> None:
            if n[0] == "id":
                used.add(n[1])
            elif n[0] in ("alt", "seq"):
                for x in n[1]:
                    walk(x)
            elif n[0] in ("opt", "rep"):
                walk(n[1])

        for body in self.productions.values():
            walk(body)
        return used - set(self.productions)

    def sample(self, start: str, rng: random.Random, depth: int = 10) -> str:
        """A random sentence of the language, short: repetitions and options are cut back as
        the depth grows, and past it the shortest alternative is taken."""
        short = self._shortest()

        def gen(n: Node, d: int) -> str:
            kind = n[0]
            if kind == "lit":
                return str(n[1])
            if kind == "id":
                return gen(self.productions[n[1]], d + 1)
            if kind == "seq":
                return "".join(gen(x, d) for x in n[1])
            if kind == "alt":
                if d > depth:
                    return gen(min(n[1], key=lambda x: self._cost(x, short)), d)
                return gen(rng.choice(n[1]), d)
            if kind == "opt":
                return gen(n[1], d) if d <= depth and rng.random() < 0.5 else ""
            count = 0 if d > depth else rng.choice([0, 1, 1, 2, 3])
            return "".join(gen(n[1], d) for _ in range(count))

        return gen(("id", start), 0)

    def _shortest(self) -> dict[str, float]:
        best = {name: float("inf") for name in self.productions}
        for _ in range(len(self.productions) + 1):
            for name, body in self.productions.items():
                best[name] = min(best[name], self._cost(body, best))
        return best

    def _cost(self, n: Node, best: dict[str, float]) -> float:
        kind = n[0]
        if kind == "lit":
            return float(len(n[1]))
        if kind == "id":
            return best.get(n[1], float("inf"))
        if kind == "seq":
            return sum(self._cost(x, best) for x in n[1])
        if kind == "alt":
            return min(self._cost(x, best) for x in n[1])
        return 0.0


def rule(name: str) -> str:
    return "w_" + name.lower()


def lark(n: Node) -> str:
    kind = n[0]
    if kind == "lit":
        return '"' + str(n[1]).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if kind == "id":
        return rule(n[1])
    if kind == "seq":
        return " ".join(lark(x) for x in n[1]) if n[1] else '""'
    if kind == "alt":
        return "(" + " | ".join(lark(x) for x in n[1]) + ")"
    if kind == "opt":
        return "[" + lark(n[1]) + "]"
    return "(" + lark(n[1]) + ")*"


def parse(text: str) -> Grammar:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise GcadError(f"WSN: unexpected {text[pos : pos + 20]!r}")
        kind = m.lastgroup or ""
        value = m.group(kind)
        if kind == "lit":
            value = value[1:-1].replace('""', '"')
        tokens.append((kind, value))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    g = Grammar()
    i = 0

    def peek(k: int = 0) -> tuple[str, str]:
        return tokens[i + k] if i + k < len(tokens) else ("eof", "")

    def take(kind: str, value: str | None = None) -> str:
        nonlocal i
        t = peek()
        if t[0] != kind or (value is not None and t[1] != value):
            raise GcadError(f"WSN: expected {value or kind}, found {t[1] or 'the end'}")
        i += 1
        return t[1]

    def at_production() -> bool:
        return peek()[0] == "id" and peek(1) == ("op", "=")

    def expression() -> Node:
        terms = [term()]
        while peek() == ("op", "|"):
            take("op", "|")
            terms.append(term())
        return terms[0] if len(terms) == 1 else ("alt", terms)

    def term() -> Node:
        factors = []
        while True:
            t = peek()
            if t[0] == "lit" or (t[0] == "id" and not at_production()):
                take(t[0])
                factors.append(("lit", t[1]) if t[0] == "lit" else ("id", t[1]))
            elif t in (("op", "("), ("op", "["), ("op", "{")):
                close = {"(": ")", "[": "]", "{": "}"}[t[1]]
                take("op", t[1])
                inner = expression()
                take("op", close)
                factors.append(inner if t[1] == "(" else ("opt" if t[1] == "[" else "rep", inner))
            else:
                break
        return factors[0] if len(factors) == 1 else ("seq", factors)

    while peek()[0] != "eof":
        name = take("id")
        take("op", "=")
        body = expression()
        if peek() == ("op", "."):
            take("op", ".")
        elif at_production() or peek()[0] == "eof":
            g.notes.append(f"production {name} has no closing full stop")
        else:
            raise GcadError(f"WSN: production {name} ends at {peek()[1]!r}")
        if name in g.productions:
            raise GcadError(f"WSN: {name} is defined twice")
        g.productions[name] = body
    return g
