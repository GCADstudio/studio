# SPDX-License-Identifier: LGPL-2.1-or-later
"""Build NXStyle's EXPRESS dictionary (resources/iso10303/dictionary.json) from the ISO 10303 parts.

The parts are not in this repository (they are sold by ISO). Point this at your own copies:

    python tools/iso10303_dictionary.py --part 42=part42.pdf --part 43=part43.pdf --part 55=part55.pdf \
        --part 108=part108.pdf --cor 108=cor1.pdf --cor 108=cor2.pdf --part 111=part111.pdf \
        --part 112=part112.pdf [--exp more.exp ...] [--out dictionary.json] [--dump-exp folder]

Each argument may be a PDF (read with pdftotext) or the text pdftotext made of it. The EXPRESS is taken
from between the "*)" and "(*" lines the parts print around their EXPRESS specifications; page headers,
footers and copyright lines are dropped. A corrigendum's EXPRESS replaces the declarations of the same
name in its part (applied in the order given), and ISO 10303-108 Cor.1's renaming of model_parameter to
variational_parameter is applied to the whole of Part 108 first. Short names come from each part's
Annex A table.

ISO 10303-41, ISO 10303-50 and ISO 13584-20 (units, measures, maths spaces and expressions) are read the
same way (--part 41=..., --part 50=..., --part 13584-20=...). resources/iso10303/supplement.exp declares
the one type they leave out, and is always added. --exp adds any other EXPRESS (for example the long form
of an application protocol, from the ISO SC4 site).

Everything an EXPRESS interpreter needs is kept: the WHERE and UNIQUE rules, DERIVE expressions, INVERSE
declarations, FUNCTIONs, RULEs and CONSTANTs (express_eval evaluates them).
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from . import express  # noqa: E402  (GCAD: package import instead of a sys.path entry)

RES = HERE  # GCAD: supplement.exp sits next to this file
NOISE = re.compile(
    r"©|°ISO|\bc\s+ISO|ISO \d{4,5}-\d+(:\s?\d{4})?(/Cor\.\d:\d{4})?\(E\)|All rights reserved|Copyright International|"
    r"^Standardization\b|Organization for$|Provided by IHS|Reproduced by IHS|No reproduction or networking|Not for Resale|Licensee=|"
    r"^\s*(\d+|[ivx]+|c|⃝ISO|ISO)\s*$|^\s*(\d+\s+c|c\s+\d+)\s*$")   # page numbers with a lost ©
WATERMARK = re.compile(r"(?<!\S)[`,\-]*`[`,\-]*(?!\S)")   # IHS watermark runs; EXPRESS never uses "`"
CAPTION = re.compile(r"^(Figure|Table|NOTE|EXAMPLE)\b")      # figures printed inside a specification
# Errors in the printed EXPRESS, corrected as the AP242 long form (ISO/TS 10303-442) prints the same rule:
# (part, printed, corrected, why). Each is reported when it is applied, and fails loudly if not found.
ERRATA = [
    ("108", "NOT('GEOMETRY_SCHEMA.GEOMETRIC_REPRESENTATION_ITEM' IN TYPEOF(q)\nAND",
     "NOT(('GEOMETRY_SCHEMA.GEOMETRIC_REPRESENTATION_ITEM' IN TYPEOF(q))\nAND",
     "neutral_sketch_representation WR1: AND binds tighter than IN (ISO 10303-11 12.1), so the rule as "
     "printed applies IN to a LOGICAL"),
]
RENAMES_108 = [("unbound_model_parameter_semantics", "unbound_variational_parameter_semantics"),
               ("unbound_model_parameter", "unbound_variational_parameter"),
               ("bound_model_parameter", "bound_variational_parameter"),
               ("model_parameter", "variational_parameter")]


def text_of(path):
    if path.lower().endswith(".pdf"):
        return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True,
                              text=True, errors="replace").stdout
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def express_blocks(text):
    """The EXPRESS specifications of a part: the lines between '*)' and the next '(*'."""
    out, cur, inside, mark = [], [], False, 0
    for line in text.replace("\f", "\n").splitlines():
        s = line.strip()
        if s == "*)":
            inside, cur, mark = True, [], 0
            continue
        if s == "(*" and inside:
            inside = False
            out.append("\n".join(_join_split_strings(_drop_figure_labels(cur))))
            continue
        if inside:
            s = WATERMARK.sub("", s).replace("’", "'").replace("‘", "'")
            if s.startswith("Figure "):              # a figure inside the specification: its labels follow
                del cur[mark:]                         # the page break before it
                continue
            if NOISE.search(s):
                mark = len(cur)
            elif s and not CAPTION.match(s) and all(ord(c) < 127 for c in s):
                cur.append(s)
    return out


def _join_split_strings(lines):
    """A string literal broken over two lines by the page layout ('..._DEFINITI' / 'ON'): EXPRESS strings
    never hold a line break, so a line with an odd number of quotes continues on the next."""
    out = []
    for s in lines:
        if out and "--" not in out[-1] and out[-1].count("'") % 2:
            out[-1] += s
        else:
            out.append(s)
    return out


def _drop_figure_labels(lines):
    """A figure printed inside a specification leaves runs of lone words (its labels): drop runs of two
    or more lines that are each a bare word."""
    heads = ("ENTITY", "TYPE", "FUNCTION", "RULE", "PROCEDURE", "SCHEMA")
    keep = heads + ("END_ENTITY", "WHERE", "DERIVE", "INVERSE", "UNIQUE", "END_TYPE", "LOCAL", "END_LOCAL",
                    "BEGIN", "END", "ELSE", "THEN")
    bare = [bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|[A-Za-z]\(?[a-z0-9]*\)?", s))
            and s.upper() not in keep and not (i and lines[i - 1].upper() in heads)
            for i, s in enumerate(lines)]
    out = []
    for i, s in enumerate(lines):
        run = bare[i] and ((i > 0 and bare[i - 1]) or (i + 1 < len(lines) and bare[i + 1]))
        if not run:
            out.append(s)
    return out


def fix_interfaces(text):
    """A watermark can swallow the comma in a REFERENCE FROM list; put commas between bare names."""
    def fix(m):
        return m.group(1) + re.sub(r"(\w)\s+(?=\w)", r"\1, ", m.group(2))
    return re.sub(r"((?:USE|REFERENCE)\s+FROM\s+\w+\s*(?:--[^\n]*\n\s*)?)(\([^)]*\))", fix, text, flags=re.I)


def only_schemas(text):
    """The SCHEMA ... END_SCHEMA spans of a part's EXPRESS: an informative annex may print declarations outside
    any schema (ISO 13584-20 annex examples), which are not part of it."""
    spans = re.findall(r"^SCHEMA\s+\w+\s*;.*?^END_SCHEMA\s*;", text, re.M | re.S | re.I)
    return "\n".join(spans) if spans else text


def short_names(text):
    """Annex A: 'entity name   SHORT' rows -> {SHORT: entity_name}."""
    out = {}
    m = re.search(r"Table A\.1\s*[–-]\s*Short names of entities(.*?)(Annex B|$)", text, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        r = re.match(r"^\s*([a-z][a-z0-9 _]*[a-z0-9])\s{2,}([A-Z][A-Z0-9]{3,5})\s*$", line)
        if r:
            out[r.group(2)] = r.group(1).replace(" ", "_")
    return out


def split_decls(text):
    """Top-level declarations of an EXPRESS fragment, by name: {(kind, name): text}."""
    out = {}
    pat = re.compile(r"^(ENTITY|TYPE|FUNCTION|RULE|PROCEDURE)\s+(\w+)", re.M | re.I)
    starts = [m for m in pat.finditer(text)]
    for m in starts:
        kind = m.group(1).upper()
        end = re.compile(rf"^\s*END_{kind}\s*;", re.M | re.I).search(text, m.end())
        if end:
            out[(kind, m.group(2).lower())] = text[m.start():end.end()]
    return out


def apply_corrigendum(base, cor):
    decls = split_decls(cor)
    for (kind, name), body in decls.items():
        pat = re.compile(rf"^{kind}\s+{name}\b.*?^\s*END_{kind}\s*;", re.M | re.S | re.I)
        if pat.search(base):
            base = pat.sub(lambda _m: body, base, count=1)
        else:
            print(f"  corrigendum declares {kind} {name}, not found in the part: skipped", file=sys.stderr)
    return base


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--part", action="append", default=[], help="NUMBER=path (PDF or pdftotext output); a part of "
                    "ISO 10303 by its number (42), another standard by its full number (13584-20)")
    ap.add_argument("--cor", action="append", default=[], help="NUMBER=path of a technical corrigendum")
    ap.add_argument("--exp", action="append", default=[], help="additional EXPRESS file")
    ap.add_argument("--out", default=os.path.join(RES, "dictionary.json"))
    ap.add_argument("--dump-exp", help="also write the extracted EXPRESS of each part to this folder")
    a = ap.parse_args(argv)

    texts, shorts = {}, {}
    for spec in a.part:
        num, path = spec.split("=", 1)
        t = text_of(path)
        texts[num] = only_schemas(fix_interfaces("\n".join(express_blocks(t))))
        shorts.update(short_names(t))
        if num == "108":
            for old, new in RENAMES_108:
                texts[num] = re.sub(rf"\b{old}\b", new, texts[num])
    for spec in a.cor:
        num, path = spec.split("=", 1)
        cor = "\n".join(express_blocks(text_of(path)))
        texts[num] = apply_corrigendum(texts[num], cor)
    for num, printed, corrected, why in ERRATA:
        if num in texts:
            if printed not in texts[num]:
                raise SystemExit(f"erratum for ISO 10303-{num} not found: {why}")
            texts[num] = texts[num].replace(printed, corrected, 1)
            print(f"  erratum applied to ISO 10303-{num}: {why}", file=sys.stderr)
    if "108" in texts:                               # Cor.1: non_negative_length_measure moved to Part 41
        texts["108"] = re.sub(r"^TYPE non_negative_length_measure\b.*?END_TYPE;", "", texts["108"],
                              flags=re.M | re.S)
    def label(n):                                    # "42" is a part of ISO 10303; "13584-20" names its standard
        return f"ISO {n}" if "-" in n else f"ISO 10303-{n}"
    order = lambda n: (1, n) if "-" in n else (0, f"{int(n):06d}")   # noqa: E731
    sources = [(label(n), t) for n, t in sorted(texts.items(), key=lambda x: order(x[0]))]
    with open(os.path.join(RES, "supplement.exp"), encoding="utf-8") as f:
        sources.append(("supplement.exp", f.read()))
    for p in a.exp:
        with open(p, encoding="utf-8", errors="replace") as f:
            sources.append((os.path.basename(p), f.read()))

    schemas = []
    declared = {}
    for label, t in sources:
        try:
            got = express.parse(t)
        except express.ExpressError as e:
            raise SystemExit(f"{label}: {e}")
        for s in got:                                 # a later source must not shadow a part silently
            for n in list(s["entities"]) + list(s["types"]):
                if n in declared and declared[n] != label:
                    print(f"  {n}: declared in {declared[n]} and again in {label}", file=sys.stderr)
                declared.setdefault(n, label)
        print(f"{label}: {', '.join(s['name'] for s in got)} "
              f"({sum(len(s['entities']) for s in got)} entities, {sum(len(s['types']) for s in got)} types)")
        schemas += got
        if a.dump_exp:
            os.makedirs(a.dump_exp, exist_ok=True)
            with open(os.path.join(a.dump_exp, label.replace(" ", "_") + ".exp"), "w", encoding="utf-8") as f:
                f.write(t)
    d = express.Dictionary.from_schemas(schemas)
    renamed = dict(RENAMES_108)
    shorts = {k: renamed.get(v, v) for k, v in shorts.items()}
    d.short_names = {k: v for k, v in shorts.items() if v in d.entities}
    lost = sorted(set(shorts.values()) - set(d.entities))
    if lost:
        print(f"short names for entities not parsed: {', '.join(lost)}", file=sys.stderr)
    for n in d.entities:                              # every supertype must resolve
        try:
            d.mapping_order(n)
        except express.ExpressError as e:
            print(f"  {n}: {e}", file=sys.stderr)
    d.dump(a.out)
    print(f"{a.out}: {len(d.entities)} entities, {len(d.types)} types, {len(d.short_names)} short names")


if __name__ == "__main__":
    main()
