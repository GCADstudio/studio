# SPDX-License-Identifier: LGPL-2.1-or-later
"""EXPRESS (ISO 10303-11): read schemas, and check STEP files (ISO 10303-21) against them (no FreeCAD).

parse(text) reads the declarations a STEP file depends on: SCHEMA with its USE FROM / REFERENCE FROM
interface, TYPE (defined types, ENUMERATION and SELECT, EXTENSIBLE and BASED_ON included, aggregates
with bounds, OPTIONAL and UNIQUE elements), ENTITY (ABSTRACT, SUPERTYPE OF with ONEOF / AND / ANDOR,
SUBTYPE OF, explicit attributes, OPTIONAL, redeclarations SELF\\entity.attribute in the attribute list,
DERIVE and INVERSE, UNIQUE and WHERE rules by label). FUNCTION, PROCEDURE, RULE, CONSTANT and
SUBTYPE_CONSTRAINT bodies are kept as text, as are WHERE and UNIQUE rules, DERIVE expressions and
INVERSE declarations: express_eval evaluates them.

A Dictionary is what the schemas say about instances: the attribute list of each entity in the order
Part 21 writes it (internal mapping, 10.2.5.2: supertypes first, in SUBTYPE OF order, each once), the
partial records of complex instances (external mapping, 10.2.5.3), which attributes are written "*"
because a subtype re-derives them (10.2.6), and the types of every attribute.

check(file, dictionary) then reports, for every instance: unknown or abstract entities, missing or extra
attributes, "$" for an attribute that is not OPTIONAL, "*" where nothing is derived, values of the wrong
type (REAL, INTEGER, STRING width, BOOLEAN, LOGICAL, enumerations, SELECT alternatives and typed
parameters, aggregates with their bounds, SET and UNIQUE duplicates), references to missing instances or
to instances of the wrong entity, complex instances that are not closed under their supertypes, that
break a ONEOF or leave an abstract supertype without a subtype; and the header rules of Part 21 clause 8.

check() is about form: whether every instance is well-formed against the schemas. Whether the rules
hold is express_eval.rules().
"""

import json
import re

SIMPLE = {"INTEGER", "REAL", "NUMBER", "BOOLEAN", "LOGICAL", "BINARY", "STRING", "GENERIC",
          "GENERIC_ENTITY"}
AGGREGATES = {"LIST", "SET", "BAG", "ARRAY"}


class ExpressError(ValueError):
    pass


# ------------------------------------------------------------------ tokens
_TOK = re.compile(r"""
    (?P<sp>\s+)
  | (?P<lc>--[^\n]*)
  | (?P<str>'(?:[^']|'')*')
  | (?P<estr>"[0-9A-Fa-f]*")
  | (?P<num>[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)
  | (?P<id>[A-Za-z][A-Za-z0-9_]*)
  | (?P<op><\*|:=:|:<>:|:=|<=|>=|<>|\|\||\*\*|[\\.,;:()\[\]{}?|*+\-/<>=])
""", re.X)


def _strip_comments(text):
    out, i, n, depth = [], 0, len(text), 0
    while i < n:
        if text.startswith("(*", i):
            depth += 1
            i += 2
        elif depth and text.startswith("*)", i):
            depth -= 1
            i += 2
        elif depth:
            i += 1
        elif text.startswith("--", i):             # a line comment may hold an apostrophe or "(*"
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text[i] == "'":                       # keep strings intact (they may hold "(*")
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def tokens(text):
    text = _strip_comments(text.replace("’", "'").replace("‘", "'"))
    out, pos = [], 0
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m:
            raise ExpressError(f"unexpected character {text[pos]!r} at {pos}: {text[pos:pos + 40]!r}")
        k = m.lastgroup
        if k not in ("sp", "lc"):
            v = m.group(k)
            out.append((k, v.upper() if k == "id" else v))
        pos = m.end()
    return out


# ------------------------------------------------------------------ parsing
class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j][1] if j < len(self.t) else None

    def kind(self, k=0):
        j = self.i + k
        return self.t[j][0] if j < len(self.t) else None

    def next(self):
        if self.i >= len(self.t):
            raise ExpressError("unexpected end of schema text")
        v = self.t[self.i][1]
        self.i += 1
        return v

    def expect(self, v):
        got = self.next()
        if got != v:
            ctx = " ".join(x[1] for x in self.t[max(0, self.i - 8):self.i + 4])
            raise ExpressError(f"expected {v!r}, found {got!r} near: {ctx}")
        return got

    def accept(self, v):
        if self.peek() == v:
            self.i += 1
            return True
        return False

    def ctx(self):
        return " ".join(x[1] for x in self.t[max(0, self.i - 12):self.i + 6])

    def ident(self):
        if self.kind() != "id":
            raise ExpressError(f"expected an identifier, found {self.peek()!r} near: {self.ctx()}")
        return self.next().lower()

    def until(self, stops, depth0=True):
        """Tokens up to (not including) the first of stops at bracket depth 0; returns their text."""
        out, d = [], 0
        while self.i < len(self.t):
            v = self.peek()
            if d == 0 and v in stops:
                break
            if v in ("(", "["):
                d += 1
            elif v in (")", "]"):
                if d == 0:
                    break
                d -= 1
            out.append(self.next())
        return " ".join(out)


def _type(p):
    """A type specification -> dict."""
    v = p.peek()
    if v in ("LIST", "SET", "BAG", "ARRAY"):
        p.next()
        lo = hi = None
        if p.accept("["):
            lo = _bound(p.until({":"}))
            p.expect(":")
            hi = _bound(p.until({"]"}))
            p.expect("]")
        p.expect("OF")
        opt = p.accept("OPTIONAL")
        uniq = p.accept("UNIQUE")
        return {"agg": v, "lo": lo, "hi": hi, "unique": uniq, "optional": opt, "of": _type(p)}
    if v in ("STRING", "BINARY"):
        p.next()
        width, fixed = None, False
        if p.accept("("):
            width = _bound(p.until({")"}))
            p.expect(")")
            fixed = p.accept("FIXED")
        return {"simple": v, "width": width, "fixed": fixed}
    if v == "REAL":
        p.next()
        if p.accept("("):
            p.until({")"})
            p.expect(")")
        return {"simple": "REAL"}
    if v in ("INTEGER", "NUMBER", "BOOLEAN", "LOGICAL", "GENERIC_ENTITY"):
        p.next()
        return {"simple": v}
    if v == "GENERIC":
        p.next()
        if p.accept(":"):
            p.ident()
        return {"simple": "GENERIC"}
    return {"named": p.ident()}


def _bound(text):
    text = text.strip()
    if text == "?" or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return text                                  # an expression (e.g. 'segments'): not checked


def _supertype_expr(p):
    def term():
        if p.accept("ONEOF"):
            p.expect("(")
            items = [expr()]
            while p.accept(","):
                items.append(expr())
            p.expect(")")
            return ["ONEOF", items]
        if p.accept("("):
            e = expr()
            p.expect(")")
            return e
        return p.ident()

    def factor():
        e = term()
        while p.peek() == "AND":
            p.next()
            e = ["AND", [e, term()]]
        return e

    def expr():
        e = factor()
        while p.peek() == "ANDOR":
            p.next()
            e = ["ANDOR", [e, factor()]]
        return e

    return expr()


def _attr_names(p):
    """id | SELF\\entity.attr [RENAMED id], comma separated -> [(redeclared_of, name)]."""
    out = []
    while True:
        if p.peek() == "SELF" and p.peek(1) == "\\":
            p.next()
            p.next()
            ent = p.ident()
            p.expect(".")
            attr = p.ident()
            new = p.ident() if p.accept("RENAMED") else attr
            out.append(((ent, attr), new))
        else:
            out.append((None, p.ident()))
        if not p.accept(","):
            return out


SECTIONS = ("DERIVE", "INVERSE", "UNIQUE", "WHERE", "END_ENTITY")


def _entity(p, schema):
    name = p.ident()
    e = {"schema": schema, "abstract": False, "supertype_of": None, "subtype_of": [], "attributes": [],
         "redeclared": [], "derived": [], "derive": {}, "inverse": [], "unique": [], "where": []}
    while not p.accept(";"):
        if p.accept("ABSTRACT"):
            e["abstract"] = True
            p.accept("SUPERTYPE")
            if p.peek() == "OF":
                p.next()
                p.expect("(")
                e["supertype_of"] = _supertype_expr(p)
                p.expect(")")
        elif p.accept("SUPERTYPE"):
            p.expect("OF")
            p.expect("(")
            e["supertype_of"] = _supertype_expr(p)
            p.expect(")")
        elif p.accept("SUBTYPE"):
            p.expect("OF")
            p.expect("(")
            e["subtype_of"].append(p.ident())
            while p.accept(","):
                e["subtype_of"].append(p.ident())
            p.expect(")")
        else:
            raise ExpressError(f"entity {name}: unexpected {p.peek()!r} in header")
    while p.peek() not in SECTIONS:
        names = _attr_names(p)
        p.expect(":")
        opt = p.accept("OPTIONAL")
        t = _type(p)
        p.expect(";")
        for red, n in names:
            if red:
                e["redeclared"].append({"of": f"{red[0]}.{red[1]}", "name": n, "type": t, "optional": opt})
            else:
                e["attributes"].append({"name": n, "type": t, "optional": opt})
    while not p.accept("END_ENTITY"):
        sec = p.next()
        if sec == "DERIVE":
            while p.peek() not in SECTIONS:
                names = _attr_names(p)
                p.expect(":")
                t = _type(p)
                p.expect(":=")
                expr = p.until({";"})
                p.expect(";")
                for red, n in names:
                    key = f"{red[0]}.{red[1]}" if red else n
                    e["derived"].append(key)
                    e["derive"][key] = {"name": n, "type": t, "expr": expr}
        elif sec == "INVERSE":
            while p.peek() not in SECTIONS:
                names = _attr_names(p)
                p.expect(":")
                inv = {"agg": None, "lo": None, "hi": None}
                if p.peek() in ("SET", "BAG"):
                    inv["agg"] = p.next()
                    if p.accept("["):
                        inv["lo"] = _bound(p.until({":"}))
                        p.expect(":")
                        inv["hi"] = _bound(p.until({"]"}))
                        p.expect("]")
                    p.expect("OF")
                inv["entity"] = p.ident()
                p.expect("FOR")
                inv["for"] = p.ident()
                if p.accept("."):                    # FOR entity.attribute (EXPRESS edition 2)
                    inv["for"] = p.ident()
                p.expect(";")
                for _, n in names:
                    e["inverse"].append(dict(inv, name=n))
        elif sec in ("UNIQUE", "WHERE"):
            while p.peek() not in SECTIONS:
                label = None
                if p.kind() == "id" and p.peek(1) == ":" and p.peek(2) != "=":
                    label = p.ident()
                    p.next()
                text = p.until({";"})
                p.expect(";")
                if sec == "WHERE":
                    e["where"].append({"label": label or f"wr{len(e['where']) + 1}", "expr": text})
                else:
                    attrs = [a.replace(" ", "").lower() for a in text.split(",")]
                    e["unique"].append({"label": label or f"ur{len(e['unique']) + 1}", "attrs": attrs})
        else:
            raise ExpressError(f"entity {name}: unexpected {sec!r}")
    p.expect(";")
    return name, e


def _typedecl(p, schema):
    name = p.ident()
    p.expect("=")
    t = {"schema": schema}
    ext = p.accept("EXTENSIBLE")
    gen = p.accept("GENERIC_ENTITY")
    if p.accept("ENUMERATION"):
        t.update(kind="enumeration", extensible=ext, items=[], based_on=None)
        if p.accept("OF"):
            p.expect("(")
            t["items"] = [p.ident()]
            while p.accept(","):
                t["items"].append(p.ident())
            p.expect(")")
        if p.accept("BASED_ON"):
            t["based_on"] = p.ident()
            if p.accept("WITH"):
                p.expect("(")
                t["items"].append(p.ident())
                while p.accept(","):
                    t["items"].append(p.ident())
                p.expect(")")
    elif p.accept("SELECT"):
        t.update(kind="select", extensible=ext, generic_entity=gen, items=[], based_on=None)
        if p.accept("("):
            t["items"] = [p.ident()]
            while p.accept(","):
                t["items"].append(p.ident())
            p.expect(")")
        if p.accept("BASED_ON"):
            t["based_on"] = p.ident()
            if p.accept("WITH"):
                p.expect("(")
                t["items"].append(p.ident())
                while p.accept(","):
                    t["items"].append(p.ident())
                p.expect(")")
    else:
        t.update(kind="defined", underlying=_type(p))
    p.expect(";")
    t["where"] = []
    if p.accept("WHERE"):
        while p.peek() != "END_TYPE":
            label = None
            if p.kind() == "id" and p.peek(1) == ":":
                label = p.ident()
                p.next()
            text = p.until({";"})
            p.expect(";")
            t["where"].append({"label": label or f"wr{len(t['where']) + 1}", "expr": text})
    p.expect("END_TYPE")
    p.expect(";")
    return name, t


_ALGORITHMS = {"FUNCTION": "END_FUNCTION", "PROCEDURE": "END_PROCEDURE", "RULE": "END_RULE",
               "SUBTYPE_CONSTRAINT": "END_SUBTYPE_CONSTRAINT"}


def _algorithm(p, start):
    """FUNCTION, PROCEDURE, RULE or SUBTYPE_CONSTRAINT -> (name, its whole text, for express_eval)."""
    end, depth, first = _ALGORITHMS[start], 1, p.i - 1
    name = p.ident() if p.kind() == "id" else None
    while depth:
        v = p.next()
        if v == start:
            depth += 1                              # nested FUNCTION / PROCEDURE
        elif v == end:
            depth -= 1
    p.expect(";")
    return name, " ".join(x[1] for x in p.t[first:p.i])


def _constants(p):
    """CONSTANT id : type := expression; ... END_CONSTANT; -> {name: {type, expr}}."""
    out = {}
    while not p.accept("END_CONSTANT"):
        n = p.ident()
        p.expect(":")
        t = _type(p)
        p.expect(":=")
        out[n] = {"type": t, "expr": p.until({";"})}
        p.expect(";")
    p.expect(";")
    return out


def parse(text):
    """EXPRESS text -> list of schema dicts {name, interface, types, entities, algorithms}."""
    p = _P(tokens(text))
    schemas = []
    while p.i < len(p.t):
        if not p.accept("SCHEMA"):
            raise ExpressError(f"expected SCHEMA, found {p.peek()!r}")
        s = {"name": p.ident(), "interface": [], "types": {}, "entities": {}, "algorithms": [],
             "functions": {}, "procedures": {}, "rules": {}, "constants": {}}
        if p.kind() == "str":
            p.next()                                 # schema version id
        p.expect(";")
        while not p.accept("END_SCHEMA"):
            v = p.next()
            if v in ("USE", "REFERENCE"):
                p.expect("FROM")
                src = p.ident()
                items = None
                if p.accept("("):
                    items = []
                    while True:
                        items.append(p.ident())
                        if p.accept("AS"):
                            items[-1] = (items[-1], p.ident())
                        if not p.accept(","):
                            break
                    p.expect(")")
                p.expect(";")
                s["interface"].append({"kind": v, "from": src, "items": items})
            elif v == "TYPE":
                n, t = _typedecl(p, s["name"])
                s["types"][n] = t
            elif v == "ENTITY":
                n, e = _entity(p, s["name"])
                s["entities"][n] = e
            elif v == "CONSTANT":
                c = _constants(p)
                for n, body in c.items():
                    body["schema"] = s["name"]
                s["constants"].update(c)
                s["algorithms"] += [("CONSTANT", n) for n in c]
            elif v in _ALGORITHMS:
                n, text = _algorithm(p, v)
                s["algorithms"].append((v, n))
                if v != "SUBTYPE_CONSTRAINT":
                    s[v.lower() + "s"][n] = {"schema": s["name"], "text": text}
            else:
                ctx = " ".join(x[1] for x in p.t[max(0, p.i - 12):p.i + 6])
                raise ExpressError(f"schema {s['name']}: unexpected {v!r} near: {ctx}")
        p.expect(";")
        schemas.append(s)
    return schemas


# ------------------------------------------------------------------ the dictionary
class Dictionary:
    """Entities and types of one or more schemas, with Part 21 mapping helpers."""

    ALGORITHMS = ("functions", "procedures", "rules", "constants")

    def __init__(self, entities=None, types=None, short_names=None, sources=None, algorithms=None):
        self.entities = entities or {}
        self.types = types or {}
        self.short_names = short_names or {}          # SHORT -> long entity name
        self.sources = sources or []
        # FUNCTION, PROCEDURE and RULE text and CONSTANTs by name, for express_eval
        self.algorithms = {k: dict((algorithms or {}).get(k, {})) for k in self.ALGORITHMS}
        self._order = {}
        self._subtypes = None

    @classmethod
    def from_schemas(cls, schemas, short_names=None):
        d = cls()
        for s in schemas:
            d.types.update(s["types"])
            d.entities.update(s["entities"])
            for k in cls.ALGORITHMS:
                d.algorithms[k].update(s.get(k, {}))
            d.sources.append(s["name"])
        d.short_names.update({k.upper(): v.lower() for k, v in (short_names or {}).items()})
        return d

    @classmethod
    def from_text(cls, text):
        return cls.from_schemas(parse(text))

    def copy(self):
        return Dictionary(dict(self.entities), dict(self.types), dict(self.short_names), list(self.sources),
                          self.algorithms)

    def merge(self, other):
        self.types.update(other.types)
        self.entities.update(other.entities)
        self.short_names.update(other.short_names)
        for k in self.ALGORITHMS:
            self.algorithms[k].update(other.algorithms[k])
        self.sources += other.sources
        self._order, self._subtypes = {}, None
        return self

    @property
    def has_rules(self):
        """False for a dictionary compiled before rule text was kept (format 1)."""
        return any(isinstance(w, dict) for e in self.entities.values() for w in e["where"]) or \
            any(self.algorithms.values())

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(d["entities"], d["types"], d.get("short_names"), d.get("sources"), d.get("algorithms"))

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": "nxstyle express dictionary 2", "sources": self.sources,
                       "entities": self.entities, "types": self.types, "short_names": self.short_names,
                       "algorithms": self.algorithms},
                      f, indent=None, sort_keys=True, separators=(",", ":"))
            f.write("\n")

    # names
    def entity_name(self, keyword):
        """Long entity name (lower case) for a Part 21 keyword, short names included; None if unknown."""
        k = keyword.lower()
        if k in self.entities:
            return k
        return self.short_names.get(keyword.upper())

    def schema_of(self, name):
        e = self.entities.get(name)
        return e["schema"] if e else None

    # inheritance
    def mapping_order(self, name):
        """Entity and its supertypes in Part 21 internal-mapping order (10.2.5.2)."""
        if name in self._order:
            return self._order[name]
        out = []

        def walk(n):
            e = self.entities.get(n)
            if e is None:
                raise ExpressError(f"unknown entity {n!r}")
            for s in e["subtype_of"]:
                walk(s)
            if n not in out:
                out.append(n)

        walk(name)
        self._order[name] = out
        return out

    def supertypes(self, name):
        return set(self.mapping_order(name))

    def subtypes(self, name):
        if self._subtypes is None:
            self._subtypes = {}
            for n, e in self.entities.items():
                for s in e["subtype_of"]:
                    self._subtypes.setdefault(s, set()).add(n)
        return self._subtypes.get(name, set())

    def is_a(self, name, of):
        return of in self.supertypes(name)

    def _derived_in(self, names):
        out = set()
        for n in names:
            for d in self.entities[n]["derived"]:
                if "." in d:
                    out.add(d)
        return out

    def _redeclared_types(self, names):
        """owner.attr -> (type, optional) of the most specific explicit redeclaration among names."""
        out = {}
        for n in sorted(names, key=lambda x: len(self.mapping_order(x))):
            for r in self.entities[n]["redeclared"]:
                out[r["of"]] = (r["type"], r["optional"])
        return out

    def _resolve_owner(self, of, names):
        """SELF\\e.a may name a supertype that inherits a; find the entity that declares a."""
        ent, attr = of.split(".")
        for n in self.mapping_order(ent) if ent in self.entities else []:
            if any(a["name"] == attr for a in self.entities[n]["attributes"]):
                return f"{n}.{attr}"
        return of

    def attributes(self, name, within=None):
        """Part 21 parameter list of a simple instance (internal mapping), or of one partial record when
        within (the set of entity names of a complex instance) is given (external mapping).

        Each item: {owner, name, type, optional, derived}.
        """
        names = within if within is not None else self.mapping_order(name)
        derived = {self._resolve_owner(d, names) for d in self._derived_in(names)}
        redecl = {self._resolve_owner(k, names): v for k, v in self._redeclared_types(names).items()}
        owners = [name] if within is not None else self.mapping_order(name)
        out = []
        for o in owners:
            for a in self.entities[o]["attributes"]:
                key = f"{o}.{a['name']}"
                t, opt = redecl.get(key, (a["type"], a["optional"]))
                out.append({"owner": o, "name": a["name"], "type": t, "optional": opt,
                            "derived": key in derived})
        return out

    def complex_closure(self, names):
        """All entity names a complex instance of these leaves must carry."""
        out = set()
        for n in names:
            out |= self.supertypes(n)
        return out

    def leaves(self, names):
        names = set(names)
        return {n for n in names if not (self.subtypes(n) & names)}

    def oneof_conflicts(self, names):
        """Groups of mutually exclusive subtypes (ONEOF) that occur together in names."""
        names = set(names)
        bad = []

        def walk(expr):
            if isinstance(expr, str):
                return
            op, items = expr
            if op == "ONEOF":
                present = []
                for it in items:
                    present.append(bool(self._expr_names(it) & names))
                if sum(present) > 1:
                    bad.append(sorted(self._expr_names(["ONEOF", items]) & names))
            for it in items:
                walk(it)

        for n in names:
            e = self.entities.get(n)
            if e and e["supertype_of"]:
                walk(e["supertype_of"])
        return bad

    def _expr_names(self, expr):
        if isinstance(expr, str):
            return {expr} | self._all_subtypes(expr)
        out = set()
        for it in expr[1]:
            out |= self._expr_names(it)
        return out

    def _all_subtypes(self, name):
        out, todo = set(), [name]
        while todo:
            for s in self.subtypes(todo.pop()):
                if s not in out:
                    out.add(s)
                    todo.append(s)
        return out

    # types
    def underlying(self, t):
        """Follow named defined types to a simple, aggregate, enumeration, select or entity."""
        seen = 0
        while "named" in t and seen < 50:
            n = t["named"]
            if n in self.entities:
                return {"entity": n}
            td = self.types.get(n)
            if td is None:
                return {"unknown": n}
            if td["kind"] == "defined":
                t = td["underlying"]
            else:
                return {td["kind"]: n}
            seen += 1
        return t

    def enum_items(self, name):
        td = self.types[name]
        items = list(td["items"])
        if td.get("based_on"):
            items += self.enum_items(td["based_on"])
        return items

    def select_items(self, name, _seen=None):
        """Flattened alternatives of a SELECT: (entities, defined/enumeration type names)."""
        _seen = _seen or set()
        if name in _seen:
            return set(), set()
        _seen.add(name)
        td = self.types[name]
        items = list(td["items"])
        if td.get("based_on"):
            items.append(td["based_on"])
        ents, types = set(), set()
        for it in items:
            if it in self.entities:
                ents.add(it)
            elif it in self.types:
                if self.types[it]["kind"] == "select":
                    e2, t2 = self.select_items(it, _seen)
                    ents |= e2
                    types |= t2
                else:
                    types.add(it)
        if td.get("extensible"):
            for n, t in self.types.items():          # SELECTs BASED_ON this one extend it
                if t.get("kind") == "select" and t.get("based_on") == name:
                    e2, t2 = self.select_items(n, _seen)
                    ents |= e2
                    types |= t2
        return ents, types

    def type_text(self, t):
        if "agg" in t:
            b = f"[{t['lo'] if t['lo'] is not None else '?'}:{t['hi'] if t['hi'] is not None else '?'}]"
            return f"{t['agg']}{b} OF {self.type_text(t['of'])}"
        return t.get("simple") or t.get("named") or str(t)


# ------------------------------------------------------------------ checking a STEP file
class Issue:
    __slots__ = ("level", "id", "message")

    def __init__(self, level, id, message):
        self.level, self.id, self.message = level, id, message

    def __repr__(self):
        where = f"#{self.id}" if self.id is not None else "file"
        return f"{self.level}: {where}: {self.message}"


_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)?$")


def check_header(f):
    from . import p21
    out = []
    names = [r.name for r in f.header]
    if names[:3] != list(p21.HEADER_ORDER):
        out.append(Issue("error", None, f"header must start FILE_DESCRIPTION, FILE_NAME, FILE_SCHEMA (8.1); "
                                        f"it starts {names[:3]}"))
    for r in f.header[3:]:
        if r.name not in p21.HEADER_OPTIONAL and not r.name.startswith("!"):
            out.append(Issue("error", None, f"{r.name} in the header is neither a Part 21 header entity nor "
                                            "user-defined ('!' keyword) (8.3)"))
    fd = f.header_record("FILE_DESCRIPTION")
    if fd and len(fd.params) == 2:
        level = fd.params[1]
        if level not in ("2;1", "2;2", "3;1", "3;2", "4;1", "4;2"):
            out.append(Issue("warning", None, f"implementation_level {level!r}: Part 21:2002 uses '3;1' or '3;2' "
                                              "('2;1'/'2;2' for files kept to edition 1 rules) (8.2.1)"))
        elif level.startswith("2;"):
            extra = [r.name for r in f.header if r.name in p21.HEADER_OPTIONAL]
            if len(f.sections) > 1 or extra or any(s.name for s in f.sections):
                out.append(Issue("error", None, f"implementation_level {level!r} forbids several or named DATA "
                                                f"sections and {', '.join(p21.HEADER_OPTIONAL)} (8.2.1)"))
    elif fd:
        out.append(Issue("error", None, "FILE_DESCRIPTION needs 2 parameters (8.2.1)"))
    fn = f.header_record("FILE_NAME")
    if fn:
        if len(fn.params) != 7:
            out.append(Issue("error", None, f"FILE_NAME needs 7 parameters, has {len(fn.params)} (8.2.2)"))
        elif not (isinstance(fn.params[1], str) and _TIMESTAMP.match(fn.params[1])):
            out.append(Issue("warning", None, f"FILE_NAME time_stamp {fn.params[1]!r} is not an ISO 8601 date and "
                                              "time joined by T (8.2.2)"))
    fs = f.header_record("FILE_SCHEMA")
    if fs:
        if len(fs.params) != 1 or not isinstance(fs.params[0], list) or not fs.params[0]:
            out.append(Issue("error", None, "FILE_SCHEMA needs one non-empty list of schema names (8.2.3)"))
        else:
            for s in fs.params[0]:
                base = s.split("{")[0].strip()
                if base != base.upper():
                    out.append(Issue("error", None, f"schema name {s!r} has small letters (8.2.3)"))
    schemas = {s.split("{")[0].strip().upper() for s in f.schemas}
    for sec in f.sections:
        for s in sec.schemas:
            if s.split("{")[0].strip().upper() not in schemas:
                out.append(Issue("error", None, f"DATA section {sec.name!r} names schema {s!r}, which is not in "
                                                "FILE_SCHEMA (9)"))
    if len(f.sections) == 1 and f.sections[0].name is None and len(schemas) > 1:
        out.append(Issue("warning", None, "one unnamed DATA section but several schemas in FILE_SCHEMA (9)"))
    return out


class _Checker:
    def __init__(self, f, d):
        from . import p21
        self.p21, self.f, self.d = p21, f, d
        self.issues = []
        self.typesets = {}
        self.unknown = {}
        self.cur = None

    def typeset(self, inst):
        if inst.id in self.typesets:
            return self.typesets[inst.id]
        out = set()
        for r in inst.records:
            n = self.d.entity_name(r.name)
            if n:
                out |= self.d.supertypes(n)
        self.typesets[inst.id] = out
        return out

    def add(self, level, iid, msg):
        self.issues.append(Issue(level, iid, msg))

    def run(self):
        self.issues += check_header(self.f)
        for iid, ref in self.f.dangling():
            self.add("error", iid, f"refers to #{int(ref)}, which is not in the file (9.1)")
        for inst in self.f:
            self.instance(inst)
        for n, c in sorted(self.unknown.items()):
            self.add("info", None, f"{n}: {c} instance(s) of an entity the loaded schemas do not define; not checked")
        return self.issues

    def instance(self, inst):
        self.cur = inst.id
        names = []
        for r in inst.records:
            if r.name.startswith("!"):
                return                               # user-defined: a matter of agreement (9.2)
            n = self.d.entity_name(r.name)
            if n is None:
                self.unknown[r.name] = self.unknown.get(r.name, 0) + 1
                return
            names.append(n)
        if not inst.complex:
            n = names[0]
            e = self.d.entities[n]
            if e["abstract"]:
                self.add("error", inst.id, f"{n} is ABSTRACT and cannot be instantiated on its own")
            closure = self.d.mapping_order(n)
            bad = self.d.oneof_conflicts(closure)
            for b in bad:
                self.add("error", inst.id, f"ONEOF violated by {', '.join(b)}")
            self.params(inst, n, inst.records[0].params, self.d.attributes(n))
            return
        # complex instance (external mapping)
        upper = [n.upper() for n in names]            # the basic alphabet's order: "_" after the letters
        if upper != sorted(upper):
            self.add("error", inst.id, "partial records of a complex instance must be in name order (10.2.5.3)")
        nset = set(names)
        need = self.d.complex_closure(nset)
        missing = sorted(need - nset)
        if missing:
            self.add("error", inst.id, f"complex instance lacks supertype record(s) {', '.join(missing)} "
                                       "(every partial value appears, 10.2.5.3)")
        for n in nset:
            if self.d.entities[n]["abstract"] and not (self.d._all_subtypes(n) & nset):
                self.add("error", inst.id, f"ABSTRACT {n} without any of its subtypes")
        for b in self.d.oneof_conflicts(nset):
            self.add("error", inst.id, f"ONEOF violated by {', '.join(b)}")
        if len(self.d.leaves(nset)) == 1:
            self.add("info", inst.id, f"one leaf entity ({next(iter(self.d.leaves(nset)))}): conformance class 1 "
                                      "writes this with the internal mapping (10.2.5.1)")
        for r, n in zip(inst.records, names):
            self.params(inst, n, r.params, self.d.attributes(n, within=need | nset), partial=True)

    def params(self, inst, ent, values, attrs, partial=False):
        label = f"{ent.upper()}" + (" (partial record)" if partial else "")
        if len(values) != len(attrs):
            exp = ", ".join(a["name"] for a in attrs)
            self.add("error", inst.id, f"{label} has {len(values)} parameter(s), the schema has {len(attrs)}"
                                       f" ({exp})")
            return
        for v, a in zip(values, attrs):
            where = f"{a['owner']}.{a['name']}"
            if a["derived"]:
                if v is not self.p21.DERIVED:
                    self.add("error", inst.id, f"{where} is re-derived by a subtype and must be written * (10.2.6)")
                continue
            if v is self.p21.DERIVED:
                self.add("error", inst.id, f"{where} is * but nothing derives it (6.1)")
                continue
            if v is self.p21.UNSET:
                if not a["optional"]:
                    self.add("error", inst.id, f"{where} is $ but not OPTIONAL (10.2.2)")
                continue
            msg = self.value(v, a["type"])
            if msg:
                self.add("error", inst.id, f"{where}: {msg}")

    def value(self, v, t, depth=0):
        """None if v fits type t, else a message."""
        P = self.p21
        if "agg" in t:
            if not isinstance(v, list):
                return f"expected {self.d.type_text(t)}, found {P.format_value(v)}"
            lo, hi = t["lo"], t["hi"]
            if isinstance(lo, int) and len(v) < lo:
                return f"{len(v)} element(s), {self.d.type_text(t)} needs at least {lo}"
            if isinstance(hi, int) and len(v) > hi:
                return f"{len(v)} element(s), {self.d.type_text(t)} allows at most {hi}"
            if t["agg"] in ("SET",) or t["unique"]:
                keys = [P.format_value(x) for x in v]
                if len(set(keys)) != len(keys):
                    return f"duplicate members in {t['agg']}{' OF UNIQUE' if t['unique'] else ''}"
            for x in v:
                if x is P.UNSET:
                    if not (t["agg"] == "ARRAY" and t["optional"]):
                        return "$ inside an aggregate (only ARRAY OF OPTIONAL allows it)"
                    continue
                m = self.value(x, t["of"], depth + 1)
                if m:
                    return m
            return None
        if "simple" in t:
            return self.simple(v, t)
        u = self.d.underlying(t)
        if "unknown" in u:
            return None                              # type from a schema not loaded
        if "entity" in u:
            return self.entity_ref(v, {u["entity"]})
        if "enumeration" in u:
            if not isinstance(v, P.Enum):
                return f"expected an enumeration value of {u['enumeration']}, found {P.format_value(v)}"
            items = self.d.enum_items(u["enumeration"])
            if v.lower() not in items and not self.d.types[u["enumeration"]].get("extensible"):
                return f".{v}. is not one of {u['enumeration']}: {', '.join(items)}"
            return None
        if "select" in u:
            ents, types = self.d.select_items(u["select"])
            if isinstance(v, P.Ref):
                return self.entity_ref(v, ents) if ents else f"{u['select']} has no entity alternative"
            if isinstance(v, P.Typed):
                k = v.keyword.lower()
                if k not in types:
                    return f"{v.keyword} is not a defined type among the alternatives of {u['select']}"
                return self.value(v.value, {"named": k}, depth + 1)
            if types:
                for k in types:                      # untyped: accept if exactly one alternative fits
                    if self.value(v, {"named": k}, depth + 1) is None:
                        self.add("warning", self.cur, f"untyped value {P.format_value(v)} for SELECT {u['select']}; "
                                                  "Part 21 writes it as a typed parameter (10.1.8)")
                        return None
            return f"{P.format_value(v)} is not a value of SELECT {u['select']}"
        return self.value(v, u, depth + 1) if u is not t else None

    def simple(self, v, t):
        P = self.p21
        s = t["simple"]
        if s in ("GENERIC", "GENERIC_ENTITY"):
            return None
        if s in ("REAL", "NUMBER"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or isinstance(v, P.Ref):
                return f"expected {s}, found {P.format_value(v)}"
            return None                              # an integer token for a REAL is tolerated
        if s == "INTEGER":
            if isinstance(v, bool) or not isinstance(v, int) or isinstance(v, P.Ref):
                return f"expected INTEGER, found {P.format_value(v)}"
            return None
        if s == "STRING":
            if not isinstance(v, str) or isinstance(v, (P.Enum, P.Binary)):
                return f"expected STRING, found {P.format_value(v)}"
            w = t.get("width")
            if isinstance(w, int) and (len(v) > w or (t.get("fixed") and len(v) != w)):
                return f"string of {len(v)} characters, STRING({w}{' FIXED' if t.get('fixed') else ''})"
            return None
        if s == "BINARY":
            return None if isinstance(v, P.Binary) else f"expected BINARY, found {P.format_value(v)}"
        if s == "BOOLEAN":
            return None if isinstance(v, P.Enum) and v in ("T", "F") else \
                f"expected BOOLEAN (.T. or .F.), found {P.format_value(v)}"
        if s == "LOGICAL":
            return None if isinstance(v, P.Enum) and v in ("T", "F", "U") else \
                f"expected LOGICAL (.T., .F. or .U.), found {P.format_value(v)}"
        return None

    def entity_ref(self, v, allowed):
        P = self.p21
        if not isinstance(v, P.Ref):
            return f"expected a reference to {' or '.join(sorted(allowed))}, found {P.format_value(v)}"
        target = self.f.get(v)
        if target is None:
            return None                              # reported once as dangling
        ts = self.typeset(target)
        if not ts:
            return None                              # target of an unknown entity: cannot tell
        if not (ts & set(allowed)):
            return f"#{int(v)} is {target.name}, not {' or '.join(sorted(allowed))}"
        return None


def check(f, d):
    """Issues found in a parsed STEP file (p21.File) against a Dictionary."""
    return _Checker(f, d).run()


def summary(issues):
    counts = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        counts[i.level] += 1
    return counts
