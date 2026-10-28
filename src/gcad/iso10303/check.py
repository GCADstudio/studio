"""The semantic layer of the `iso10303` kernel (SPEC FR-K9, FR-I4): a Part 21 file against the
EXPRESS schemas its header names.

What the grammar cannot decide is decided here, after it has accepted the file (design note
§1.3): the header's entities and their order (ISO 10303-21 clause 8), every instance against its
schema (unknown or abstract entities, attribute counts and types, `$` and `*`, references), and
the schema's WHERE, UNIQUE and global rules and INVERSE cardinalities, evaluated by the EXPRESS
interpreter. Each finding becomes a diagnostic with the span of the `#n` instance it is about.

Violations are errors (the file does not conform), but they do not stop the model from being
built: `origin = "check"`. A rule the interpreter cannot decide is reported as not evaluated, and
the last diagnostic counts the rules evaluated, violated and not evaluated, so a clean result says
what was and was not decided.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

from gcad.diagnostics import Diagnostic, Severity, Span
from gcad.iso10303 import schemas
from gcad.iso10303._nxstyle import express, express_eval, p21
from gcad.iso10303._nxstyle import iso10303 as nx

# A contradiction in the standards found while making GCAD's writer pass every rule (design note
# §6, number 17): the AP242 long form's VALID_CSG_2D_PRIMITIVES takes a CSG_SOLID_2D but recurses
# into a boolean's operands, which boolean_operand_2d makes primitives or BOOLEAN_RESULT_2Ds, never
# a CSG_SOLID_2D; so csg_solid_2d WR1 fails for every 2D CSG solid whose root is a boolean. The
# violation is downgraded only when that is all it is: the root is a BOOLEAN_RESULT_2D and every
# leaf of its tree is one of the primitives the function accepts.
_CSG_2D_LEAVES = {
    "CIRCULAR_AREA",
    "COMPLEX_AREA",
    "ELLIPTIC_AREA",
    "HALF_SPACE_2D",
    "POLYGONAL_AREA",
    "PRIMITIVE_2D_WITH_INNER_BOUNDARY",
    "RECTANGULAR_AREA",
}


def _csg_solid_2d_wr1(ev: object, i: object, expr: str) -> tuple[str, str] | None:
    f = ev.f  # type: ignore[attr-defined]

    def ok(ref: object, depth: int = 0) -> bool:
        inst = f.get(ref) if isinstance(ref, p21.Ref) else None
        if inst is None or depth > 1000:
            return False
        if "BOOLEAN_RESULT_2D" in inst.names:
            params = inst.record("BOOLEAN_RESULT_2D").params
            return ok(params[2], depth + 1) and ok(params[3], depth + 1)
        return bool(_CSG_2D_LEAVES & set(inst.names))

    root = f.get(i.pid)  # type: ignore[attr-defined]
    tree = root.record("CSG_SOLID_2D").params[1] if root and root.record("CSG_SOLID_2D") else None
    if tree is not None and "BOOLEAN_RESULT_2D" in f.get(tree).names and ok(tree):
        return "warning", (
            ": the AP242 long form's VALID_CSG_2D_PRIMITIVES passes a boolean's operands, which "
            "cannot be CSG_SOLID_2Ds, where it requires one, so no 2D boolean can meet WR1; every "
            "primitive in this tree is one the function accepts"
        )
    return None


express_eval.KNOWN[("csg_solid_2d", "wr1")] = _csg_solid_2d_wr1

#: above this many instances the studio checks form only; `gcad iso check` evaluates every rule
RULES_LIMIT = int(os.environ.get("GCAD_ISO10303_RULES_LIMIT", "5000"))

_SCAN = re.compile(r"'(?:[^']|'')*'|/\*.*?\*/|(#[0-9]+)\s*=|;", re.S)
LEVELS = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def instance_spans(source: str, file: str) -> dict[int, Span]:
    """The span of each `#n = ...;` instance, from its name to its semicolon."""
    spans: dict[int, Span] = {}
    open_at: tuple[int, int] | None = None
    line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
    for m in _SCAN.finditer(source):
        if m.group(1):
            open_at = (int(m.group(1)[1:]), m.start())
        elif m.group(0) == ";" and open_at is not None:
            iid, start = open_at
            line = _line_of(line_starts, start)
            spans.setdefault(
                iid, Span(file, line, start - line_starts[line - 1] + 1, start, m.end())
            )
            open_at = None
    return spans


def _line_of(line_starts: list[int], pos: int) -> int:
    lo, hi = 0, len(line_starts)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if line_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid
    return lo + 1


def _header_span(source: str, file: str) -> Span | None:
    m = re.search(r"FILE_SCHEMA\s*\([^;]*;", source)
    if not m:
        return None
    line = source.count("\n", 0, m.start()) + 1
    col = m.start() - (source.rfind("\n", 0, m.start()) + 1) + 1
    return Span(file, line, col, m.start(), m.end())


@dataclass
class Report:
    diagnostics: list[Diagnostic]
    evaluated: int = 0
    violated: int = 0
    unevaluated: int = 0
    schema: str = ""  # what the file was checked against


def report(source: str, file: str, rules: bool | None = None) -> Report:
    """Check a Part 21 text. rules=None evaluates the rules up to RULES_LIMIT instances."""
    spans = instance_spans(source, file)
    header = _header_span(source, file)

    def diag(level: str, iid: int | None, message: str) -> Diagnostic:
        span = spans.get(iid) if iid is not None else header
        return Diagnostic(LEVELS.get(level, Severity.INFO), message, span, origin="check")

    try:
        f = p21.loads(source)
    except p21.P21Error as exc:
        message = re.sub(r" \(at character \d+: .*\)$", "", str(exc))
        m = re.search(r"#(\d+)", message)
        return Report([diag("error", int(m.group(1)) if m else None, message)])
    out = [diag("info", None, note) for note in f.notes]
    have_ap = any((schemas.schema_dir() / f"{i['schema']}.json").is_file() for i in nx.identify(f))
    if not have_ap and not schemas.has_resources():
        out += [diag(i.level, i.id, i.message) for i in express.check_header(f)]
        out.append(
            diag(
                "info",
                None,
                "not checked against EXPRESS: no schema in the cache "
                "(gcad iso schema fetch AP242, or gcad iso schema build)",
            )
        )
        return Report(out, schema="")
    d, note = nx.dictionary_for(f)
    if note:
        out.append(diag("info", None, note.replace("python -m nxstyle.iso10303", "gcad iso")))
    out += [diag(i.level, i.id, i.message) for i in express.check(f, d)]
    if have_ap:  # the file's own long form is complete: an entity it lacks is outside the schema
        ap = _name(f, have_ap)
        for inst in f:
            for name in inst.names:
                if d.entity_name(name) is None:
                    out.append(
                        diag(
                            "error",
                            inst.id,
                            f"{name} is not an entity of {ap}, the schema FILE_SCHEMA names",
                        )
                    )
    count = sum(len(s.instances) for s in f.sections)
    if rules is None:
        rules = count <= RULES_LIMIT
    if not rules:
        out.append(
            diag(
                "info",
                None,
                f"rules not evaluated in the studio: {count} instances is more than "
                f"{RULES_LIMIT} (gcad iso check evaluates them)",
            )
        )
        return Report(out, schema=_name(f, have_ap))
    rd = d if d.has_rules else schemas.resources()
    res = express_eval.rules(f, rd, part=lambda sc: nx.PART.get(sc, (sc or "?").upper()))
    out += [diag(i.level, i.id, i.message) for i in res.issues]
    out += [diag(i.level, i.id, i.message) for i in nx.rules(f, d)]
    unevaluated = sum(c for c, _ in res.unevaluated.values())
    out.append(
        diag(
            "info",
            None,
            f"rules: {res.evaluated} evaluated, {res.violated} violated, "
            f"{unevaluated} not evaluated",
        )
    )
    return Report(out, res.evaluated, res.violated, unevaluated, _name(f, have_ap))


def _name(f: object, have_ap: bool) -> str:
    if have_ap:
        return ", ".join(i["ap"] or i["schema"] for i in nx.identify(f))
    return "the ISO 10303 resource parts"


#: the last few checks, by file name and SHA-256 of the text: the studio evaluates a file again
#: on every run, and a PMI file of a few thousand instances takes seconds to check
_CACHE: dict[tuple[str, str], list[Diagnostic]] = {}
_CACHE_SIZE = 8


def check(source: str, file: str) -> list[Diagnostic]:
    """The kernel's checker (kernel.toml `checker = "iso10303-21"`)."""
    # a schema added or fetched since (the web studio's upload) must be checked against
    folder = schemas.schema_dir()
    known = sorted(f.name for f in folder.glob("*.json")) if folder.is_dir() else []
    key = (file, hashlib.sha256((source + "\0" + " ".join(known)).encode("utf-8")).hexdigest())
    if key not in _CACHE:
        while len(_CACHE) >= _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = report(source, file).diagnostics
    return list(_CACHE[key])
