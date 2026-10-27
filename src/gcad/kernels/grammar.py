"""Grammar kernels (SPEC §5.2): a Lark EBNF grammar plus declarative rewrite rules -> GCore.

rewrite.toml maps every tree name (a rule name or an `-> alias`) to a template written in
GCore syntax. Placeholders refer to the node's children, 1-based, after Lark has dropped
anonymous punctuation; an optional part that did not match (`[...]`) is an absent child.

    $1          child 1 rewritten (absent children produce nothing)
    $1*         the children of child 1, rewritten and spliced
    $*          all children of this node, rewritten and spliced
    $1:str      the source text of child 1 as a string; also :sym and :num
    :$1         a keyword named after the source text of child 1

A template may hold several forms (they are spliced into the parent) or none (the node
disappears). [tokens] maps terminal names to number, string, symbol, keyword or text;
unlisted terminals are guessed from their names. A string is read with JSON escapes, or with a
named decoder: `string:iso10303-21` reads ISO 10303-21 strings ('it''s', \\X2\\...\\X0\\).
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from lark import Lark, Token, Tree
from lark.exceptions import (
    GrammarError,
    LarkError,
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
)
from lark.lexer import PatternStr

from gcad.core.sexpr import Expr, Kw, Num, SList, Str, Sym, read
from gcad.diagnostics import GcadError, Span

PLACEHOLDER = re.compile(r"^\$(?:(\d+)(\*|:str|:sym|:num)?|\*)$")
KEYWORD_PLACEHOLDER = re.compile(r"^\$(\d+)$")
TOKEN_KINDS = ("number", "string", "symbol", "keyword", "text")


def _iso10303_21(text: str) -> str:
    from gcad.iso10303._nxstyle.p21 import P21Error, decode_string

    try:
        # Line breaks are not part of the exchange structure, even inside a string (annex A).
        return str(decode_string(text[1:-1].replace("\r", "").replace("\n", "")))
    except (P21Error, IndexError, ValueError) as exc:
        raise ValueError(str(exc)) from None


#: decoders for `string:<name>` tokens: quoted source text -> the string's value
STRING_DECODERS = {"iso10303-21": _iso10303_21}

Child = Tree | Token | None


def guess_token_kind(name: str) -> str:
    upper = name.upper()
    if any(k in upper for k in ("NUMBER", "INT", "FLOAT", "DECIMAL")):
        return "number"
    if "STRING" in upper:
        return "string"
    if "KEYWORD" in upper:
        return "keyword"
    return "symbol"


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class GrammarKernel:
    """A compiled grammar with its rewrite templates."""

    def __init__(self, kernel_id: str, folder: Path, algorithm: str, start: str) -> None:
        self.kernel_id = kernel_id
        self.folder = folder
        grammar_path = folder / "grammar.lark"
        rewrite_path = folder / "rewrite.toml"
        try:
            grammar = grammar_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise GcadError(f"kernel '{kernel_id}': no grammar.lark") from None
        try:
            self.lark = Lark(
                grammar,
                parser=algorithm,
                start=start,
                propagate_positions=True,
                maybe_placeholders=True,
            )
        except (GrammarError, LarkError) as exc:
            raise GcadError(f"kernel '{kernel_id}': grammar.lark: {exc}") from None
        try:
            spec = tomllib.loads(rewrite_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise GcadError(f"kernel '{kernel_id}': no rewrite.toml") from None
        except tomllib.TOMLDecodeError as exc:
            raise GcadError(f"kernel '{kernel_id}': rewrite.toml: {exc}") from None
        rules = spec.get("rules", {})
        tokens = spec.get("tokens", {})
        if not isinstance(rules, dict) or not isinstance(tokens, dict):
            raise GcadError(f"kernel '{kernel_id}': rewrite.toml needs [rules] and [tokens] tables")
        self.templates: dict[str, list[Expr]] = {}
        for name, text in rules.items():
            if not isinstance(text, str):
                raise GcadError(f"kernel '{kernel_id}': rule '{name}' must be a string")
            self.templates[name] = self._compile_template(name, text)
        self.token_kinds: dict[str, str] = {}
        for name, kind in tokens.items():
            base, _, decoder = str(kind).partition(":")
            if (
                base not in TOKEN_KINDS
                or (decoder and (base, decoder) != ("string", decoder))
                or (decoder and decoder not in STRING_DECODERS)
            ):
                kinds = (
                    ", ".join(TOKEN_KINDS)
                    + ", "
                    + ", ".join(f"string:{d}" for d in STRING_DECODERS)
                )
                raise GcadError(f"kernel '{kernel_id}': token {name} kind must be one of {kinds}")
            self.token_kinds[name] = kind
        self.file = ""
        self.source = ""

    def _compile_template(self, name: str, text: str) -> list[Expr]:
        where = f"{self.kernel_id}/rewrite.toml [{name}]"
        try:
            forms = read(text, where)
        except GcadError as exc:
            raise GcadError(f"{where}: {exc.diagnostic.message}") from None

        def validate(expr: Expr) -> None:
            if isinstance(expr, Sym) and expr.name.startswith("$") and expr.name[1:2].isdigit():
                if not PLACEHOLDER.match(expr.name):
                    raise GcadError(f"{where}: bad placeholder '{expr.name}'")
            if isinstance(expr, SList):
                for item in expr.items:
                    validate(item)

        for form in forms:
            validate(form)
        return forms

    # -- parsing ---------------------------------------------------------------------------

    def to_gcore(self, source: str, file: str) -> list[Expr]:
        # A shallow copy carries the per-file state, so one compiled kernel can serve
        # several files at once.
        job = copy.copy(self)
        job.file, job.source = file, source
        text = source if source.endswith("\n") else source + "\n"
        try:
            tree = job.lark.parse(text)
        except UnexpectedInput as exc:
            raise job._syntax_error(exc, text) from None
        return job.rewrite(tree, Span(file, 1, 1, 0, len(source)))

    def _syntax_error(self, exc: UnexpectedInput, text: str) -> GcadError:
        line, col = getattr(exc, "line", 1) or 1, getattr(exc, "column", 1) or 1
        pos = getattr(exc, "pos_in_stream", None) or 0
        expected = sorted(self._describe_terminal(t) for t in getattr(exc, "expected", ()) or ())
        hint = f"; expected {', '.join(expected[:8])}" if expected else ""
        if isinstance(exc, UnexpectedCharacters):
            message = f"unexpected character {text[pos]!r}"
            hint = ""
        elif isinstance(exc, UnexpectedEOF) or (
            isinstance(exc, UnexpectedToken) and exc.token.type == "$END"
        ):
            message = "unexpected end of input"
            lines = text.rstrip("\n").split("\n")
            line, col, pos = len(lines), len(lines[-1]) + 1, len(text.rstrip("\n"))
        elif isinstance(exc, UnexpectedToken):
            message = f"unexpected {str(exc.token)!r}"
        else:
            message = "syntax error"
        return GcadError(message + hint, Span(self.file, line, col, pos, pos + 1))

    def _describe_terminal(self, name: str) -> str:
        try:
            term = self.lark.get_terminal(name)
        except KeyError:
            return name
        if isinstance(term.pattern, PatternStr):
            return repr(term.pattern.value)
        return name

    # -- rewriting -------------------------------------------------------------------------

    def _span(self, child: Child, fallback: Span) -> Span:
        if isinstance(child, Token) and child.line is not None:
            return Span(
                self.file, child.line, child.column or 1, child.start_pos or 0, child.end_pos or 0
            )
        if isinstance(child, Tree) and not child.meta.empty:
            m = child.meta
            return Span(self.file, m.line, m.column, m.start_pos, m.end_pos)
        return fallback

    def _text(self, child: Child) -> str:
        if isinstance(child, Token):
            return str(child)
        if isinstance(child, Tree) and not child.meta.empty:
            return self.source[child.meta.start_pos : child.meta.end_pos]
        return ""

    def rewrite(self, child: Child, parent_span: Span) -> list[Expr]:
        if child is None:
            return []
        span = self._span(child, parent_span)
        if isinstance(child, Token):
            return [self.convert_token(child, span)]
        name = str(child.data)
        template = self.templates.get(name)
        if template is None:
            raise GcadError(f"kernel '{self.kernel_id}' has no rewrite rule for '{name}'", span)
        out: list[Expr] = []
        for form in template:
            out.extend(self.instantiate(form, child, span))
        return out

    def convert_token(self, token: Token, span: Span) -> Expr:
        kind = self.token_kinds.get(token.type) or guess_token_kind(token.type)
        text = str(token)
        if kind == "number":
            try:
                return Num(float(text), span)
            except ValueError:
                raise GcadError(f"invalid number {text!r}", span) from None
        if kind.startswith("string:"):
            try:
                return Str(STRING_DECODERS[kind[7:]](text), span)
            except ValueError as exc:
                raise GcadError(f"invalid string literal: {exc}", span) from None
        if kind == "string":
            try:
                value = json.loads(text) if text.startswith('"') else text
            except json.JSONDecodeError as exc:
                raise GcadError(f"invalid string literal: {exc.msg}", span) from None
            return Str(value, span)
        if kind == "keyword":
            return Kw(text.lstrip(":"), span)
        if kind == "text":
            return Str(text, span)
        return Sym(text, span)

    def instantiate(self, form: Expr, node: Tree, span: Span) -> list[Expr]:
        if isinstance(form, Sym):
            m = PLACEHOLDER.match(form.name)
            if m:
                return self.placeholder(m, node, span)
            return [replace(form, span=span)]
        if isinstance(form, Kw):
            km = KEYWORD_PLACEHOLDER.match(form.name)
            if km:
                child = self._child(node, int(km.group(1)))
                return [Kw(self._text(child), self._span(child, span))] if child is not None else []
            return [replace(form, span=span)]
        if isinstance(form, SList):
            items: list[Expr] = []
            for item in form.items:
                items.extend(self.instantiate(item, node, span))
            return [SList(tuple(items), form.bracket, span)]
        return [replace(form, span=span)]

    @staticmethod
    def _child(node: Tree, index: int) -> Child:
        return node.children[index - 1] if 0 < index <= len(node.children) else None

    def placeholder(self, m: re.Match[str], node: Tree, span: Span) -> list[Expr]:
        if m.group(1) is None:  # $*
            out: list[Expr] = []
            for c in node.children:
                out.extend(self.rewrite(c, span))
            return out
        child = self._child(node, int(m.group(1)))
        mode = m.group(2)
        if child is None:
            return []
        cspan = self._span(child, span)
        if mode == "*":
            if isinstance(child, Token):
                return self.rewrite(child, span)
            out = []
            for c in child.children:
                out.extend(self.rewrite(c, cspan))
            return out
        if mode == ":str":
            return [Str(self._text(child), cspan)]
        if mode == ":sym":
            return [Sym(self._text(child), cspan)]
        if mode == ":num":
            try:
                return [Num(float(self._text(child)), cspan)]
            except ValueError:
                raise GcadError(f"{self._text(child)!r} is not a number", cspan) from None
        return self.rewrite(child, span)

    # -- authoring support -----------------------------------------------------------------

    def tree_names(self) -> set[str]:
        """Names of the trees the parser can produce, which therefore need rewrite rules."""
        names: set[str] = set()
        for rule in self.lark.rules:
            if rule.alias:
                names.add(str(rule.alias))
                continue
            origin = rule.origin.name
            if isinstance(origin, str) and origin.startswith("_"):
                continue
            if rule.options.expand1:
                kept = sum(
                    2 if (s.is_term is False and s.name.startswith("_")) else 1
                    for s in rule.expansion
                    if not getattr(s, "filter_out", False)
                )
                kept += sum(1 for e in (rule.options.empty_indices or ()) if e)
                if kept <= 1:
                    continue
            names.add(str(origin))
        return names

    def check(self) -> CheckReport:
        report = CheckReport()
        names = self.tree_names()
        for name in sorted(names - set(self.templates)):
            report.errors.append(f"no rewrite rule for '{name}'")
        for name in sorted(set(self.templates) - names):
            report.warnings.append(f"rewrite rule '{name}' matches no grammar rule")
        terminals = {t.name for t in self.lark.terminals}
        for name in sorted(set(self.token_kinds) - terminals):
            report.warnings.append(f"[tokens] {name} is not a terminal of the grammar")
        return report

    def describe(self) -> dict[str, object]:
        """Highlighting classes derived from the grammar (FR-K5). `patterns` gives each
        named terminal's regular expression by class, so an editor can tokenize the language
        without knowing it."""
        keywords: set[str] = set()
        operators: set[str] = set()
        tokens: dict[str, str] = {}
        patterns: dict[str, list[str]] = {}
        ignored = set(self.lark.ignore_tokens)
        for term in self.lark.terminals:
            if isinstance(term.pattern, PatternStr) and term.name not in ignored:
                # Lark names some literals __ANON_n; they are still keywords or operators.
                value = term.pattern.value
                (keywords if re.fullmatch(r"[A-Za-z_]\w*", value) else operators).add(value)
                continue
            if term.name.startswith("__") and term.name not in ignored:
                continue
            kind = (
                "comment"
                if term.name in ignored
                else self.token_kinds.get(term.name) or guess_token_kind(term.name)
            ).partition(":")[0]
            if not term.name.startswith("__"):
                tokens[term.name] = kind
            if kind != "comment" or not re.fullmatch(
                r"\\s\+|\(\?:\\s\)\+|\[[^\]]*\]\+", term.pattern.value
            ):
                patterns.setdefault(kind, []).append(term.pattern.value)
        by_kind: dict[str, list[str]] = {}
        for name, kind in tokens.items():
            by_kind.setdefault(kind, []).append(name)
        return {
            "keywords": sorted(keywords),
            "operators": sorted(operators),
            **{f"{kind}_terminals": sorted(v) for kind, v in sorted(by_kind.items())},
            "patterns": {kind: sorted(p) for kind, p in sorted(patterns.items())},
        }


_CACHE: dict[tuple[str, float, float], GrammarKernel] = {}


def load_grammar_kernel(kernel_id: str, folder: Path, algorithm: str, start: str) -> GrammarKernel:
    """Compile once per grammar and rewrite file version."""
    stamps = []
    for name in ("grammar.lark", "rewrite.toml"):
        p = folder / name
        stamps.append(p.stat().st_mtime if p.exists() else 0.0)
    key = (str(folder.resolve()) + "|" + algorithm + "|" + start, stamps[0], stamps[1])
    if key not in _CACHE:
        _CACHE[key] = GrammarKernel(kernel_id, folder, algorithm, start)
    return _CACHE[key]
