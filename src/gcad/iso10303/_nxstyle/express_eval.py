# SPDX-License-Identifier: LGPL-2.1-or-later
"""An EXPRESS (ISO 10303-11) interpreter: evaluate the rules of the loaded schemas on a STEP file (no FreeCAD).

rules(file, dictionary) evaluates, on every instance of a parsed Part 21 file:

* the domain rules (WHERE) of each entity in the instance's type set, supertypes included (11, 9.2.2.2);
* the WHERE rules of every defined type an attribute value has, along its chain of underlying types and
  inside aggregates and typed parameters (8.3.2);
* aggregate bounds given by an expression, as in LIST [1:segments] (8.2);
* the uniqueness rules (UNIQUE) of each entity over the population (9.2.2.1);
* the cardinalities of INVERSE attributes: exactly one user, or the bounds of the SET or BAG (9.2.1.3);
* the global rules (RULE ... FOR) over the extents of their entities (9.6).

Expressions are evaluated as clause 12 says, with the schemas' FUNCTIONs and CONSTANTs and the built-in
functions and procedures of clauses 15 and 16 (TYPEOF, USEDIN, ROLESOF, SIZEOF, QUERY, the aggregate,
string and arithmetic operators, entity constructors and the complex entity constructor ||). Derived and
inverse attributes are computed from their declarations. A value that is not in the file is indeterminate
(?): arithmetic on it gives ?, comparisons give UNKNOWN, and a rule that evaluates to UNKNOWN or ? is not
violated (9.2.2.2). Only FALSE is reported.

A rule the interpreter cannot finish (it calls a FUNCTION the loaded schemas do not declare, or runs past
the step budget) is reported once per rule as "not evaluated", with the reason, so a clean result says
what was and was not decided.

TYPEOF returns, for an entity instance, its entity types qualified by the schema that declares each; for
other values, the defined types the value was declared with, the SELECT types it was reached through, and
its simple type names, a REAL also being a NUMBER and an INTEGER a REAL and a NUMBER (8.1).
"""

import math
import re

from . import express, p21

STEP_BUDGET = 2_000_000                              # operations per rule before it is given up
GLOBAL_BUDGET = 5_000_000                            # for a global rule, which ranges over the population


class EvalError(Exception):
    """A rule could not be evaluated (unsupported construct, unknown name, budget)."""


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Escape(Exception):
    pass


class _Skip(Exception):
    pass


# ------------------------------------------------------------------ values
class _Unknown:
    """The LOGICAL value UNKNOWN."""
    __slots__ = ()

    def __repr__(self):
        return "UNKNOWN"

    def __bool__(self):
        raise EvalError("UNKNOWN used as a BOOLEAN")


UNKNOWN = _Unknown()


class TReal(float):
    types = ()


class TInt(int):
    types = ()


class TStr(str):
    types = ()


class EnumV(str):
    """An enumeration item (upper case), with the enumeration type it belongs to when known."""
    types = ()


class Agg(list):
    """An aggregate value: kind LIST, SET, BAG or ARRAY, the index of its first element, declared bounds."""

    def __init__(self, items=(), kind="LIST", lo=1, bounds=(None, None), types=()):
        super().__init__(items)
        self.kind, self.lo, self.bounds, self.types = kind, lo, bounds, types

    def like(self, items):
        return Agg(items, self.kind, self.lo if self.kind == "ARRAY" else 1, self.bounds)


def _tag(v, names):
    """v with the type names (qualified, upper case) added for TYPEOF."""
    if not names or v is None or isinstance(v, (bool, _Unknown, Inst)):
        return v
    if isinstance(v, Agg):
        v.types = tuple(v.types) + tuple(names)
        return v
    if isinstance(v, EnumV):
        out = EnumV(v)
    elif isinstance(v, float):
        out = TReal(v)
    elif isinstance(v, int):
        out = TInt(v)
    elif isinstance(v, p21.Binary):
        return v
    elif isinstance(v, str):
        out = TStr(v)
    else:
        return v
    out.types = tuple(getattr(v, "types", ())) + tuple(names)
    return out


class Inst:
    """An entity instance: from the file (pid is its instance id) or made by an entity constructor."""
    __slots__ = ("names", "vals", "pid", "cache", "ev")

    def __init__(self, ev, names, vals, pid=None):
        self.ev, self.names, self.vals, self.pid, self.cache = ev, names, vals, pid, {}

    def __repr__(self):
        return f"#{self.pid}" if self.pid is not None else f"<{'|'.join(sorted(self.names))}>"


def _is_true(v):
    return v is True


def _logical(v):
    """A value used as a LOGICAL: True, False or UNKNOWN (? counts as UNKNOWN)."""
    if v is True or v is False or v is UNKNOWN:
        return v
    if v is None:
        return UNKNOWN
    raise EvalError(f"expected a LOGICAL, found {v!r}")


def _not(a):
    a = _logical(a)
    return UNKNOWN if a is UNKNOWN else (not a)


def _and(a, b):
    a, b = _logical(a), _logical(b)
    if a is False or b is False:
        return False
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return True


def _or(a, b):
    a, b = _logical(a), _logical(b)
    if a is True or b is True:
        return True
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return False


def _xor(a, b):
    a, b = _logical(a), _logical(b)
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return a != b


# ------------------------------------------------------------------ parsing expressions and algorithms
_REL = {"=", "<>", "<", ">", "<=", ">=", ":=:", ":<>:", "IN", "LIKE"}
_ADD = {"+", "-", "OR", "XOR"}
_MUL = {"*", "/", "DIV", "MOD", "AND", "||"}
_STOP = {";", ")", "]", ",", ":", "}", "|", "THEN", "OF", "ELSE", "END_IF", "TO", "BY", "WHILE", "UNTIL",
         ":=", "<*"}


def _decode_estr(s):
    h = s[1:-1]
    return "".join(chr(int(h[i:i + 8], 16)) for i in range(0, len(h) - 7, 8))


class _Parser:
    def __init__(self, text):
        self.t = express.tokens(text)
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j][1] if j < len(self.t) else None

    def kind(self, k=0):
        j = self.i + k
        return self.t[j][0] if j < len(self.t) else None

    def next(self):
        if self.i >= len(self.t):
            raise EvalError("unexpected end of EXPRESS text")
        v = self.t[self.i][1]
        self.i += 1
        return v

    def accept(self, v):
        if self.peek() == v:
            self.i += 1
            return True
        return False

    def expect(self, v):
        got = self.next()
        if got != v:
            ctx = " ".join(x[1] for x in self.t[max(0, self.i - 8):self.i + 4])
            raise EvalError(f"expected {v!r}, found {got!r} near: {ctx}")

    def ident(self):
        if self.kind() != "id":
            raise EvalError(f"expected an identifier, found {self.peek()!r}")
        return self.next()

    def skip_type(self):
        """A type specification, skipped (formal parameters, locals): tokens to ';', ')' or ':=' at depth 0."""
        d = 0
        while self.i < len(self.t):
            v = self.peek()
            if d == 0 and v in (";", ")", ":="):
                return
            if v in ("(", "["):
                d += 1
            elif v in (")", "]"):
                d -= 1
            self.next()

    # expressions
    def expr(self):
        a = self.simple()
        v = self.peek()
        if v in _REL:
            self.next()
            return ("bin", v, a, self.simple())
        return a

    def simple(self):
        a = self.term()
        while self.peek() in _ADD:
            a = ("bin", self.next(), a, self.term())
        return a

    def term(self):
        a = self.factor()
        while self.peek() in _MUL:
            a = ("bin", self.next(), a, self.factor())
        return a

    def factor(self):
        a = self.unary()
        if self.accept("**"):
            return ("bin", "**", a, self.unary())
        return a

    def unary(self):
        v = self.peek()
        if v in ("+", "-", "NOT"):
            self.next()
            return ("un", v, self.unary())
        return self.qualified(self.primary())

    def qualified(self, e):
        while True:
            v = self.peek()
            if v == "." and self.kind(1) == "id":
                self.next()
                e = ("attr", e, self.ident())
            elif v == "\\":
                self.next()
                e = ("group", e, self.ident())
            elif v == "[":
                self.next()
                i = self.expr()
                j = self.expr() if self.accept(":") else None
                self.expect("]")
                e = ("index", e, i, j)
            else:
                return e

    def primary(self):
        k, v = self.kind(), self.peek()
        if k == "num":
            self.next()
            return ("lit", float(v) if any(c in v for c in ".eE") else int(v))
        if k == "str":
            self.next()
            return ("lit", v[1:-1].replace("''", "'"))
        if k == "estr":
            self.next()
            return ("lit", _decode_estr(v))
        if v == "(":
            self.next()
            e = self.expr()
            self.expect(")")
            return e
        if v == "[":
            self.next()
            items = []
            if not self.accept("]"):
                while True:
                    e = self.expr()
                    rep = self.expr() if self.accept(":") else None
                    items.append((e, rep))
                    if not self.accept(","):
                        break
                self.expect("]")
            return ("aggr", items)
        if v == "{":
            self.next()
            lo = self.simple()
            op1 = self.next()
            mid = self.simple()
            op2 = self.next()
            hi = self.simple()
            self.expect("}")
            return ("interval", lo, op1, mid, op2, hi)
        if v == "?":
            self.next()
            return ("lit", None)
        if k == "op" and v == "%":
            raise EvalError("binary literals are not supported")
        if k != "id":
            raise EvalError(f"unexpected {v!r} in an expression")
        self.next()
        if v == "SELF":
            return ("self",)
        if v == "TRUE":
            return ("lit", True)
        if v == "FALSE":
            return ("lit", False)
        if v == "UNKNOWN":
            return ("lit", UNKNOWN)
        if v == "PI":
            return ("lit", math.pi)
        if v == "CONST_E":
            return ("lit", math.e)
        if v == "QUERY":
            self.expect("(")
            var = self.ident()
            self.expect("<*")
            agg = self.expr()
            self.expect("|")
            cond = self.expr()
            self.expect(")")
            return ("query", var, agg, cond)
        if self.peek() == "(":
            self.next()
            args = []
            if not self.accept(")"):
                while True:
                    args.append(self.expr())
                    if not self.accept(","):
                        break
                self.expect(")")
            return ("call", v, args)
        return ("name", v)

    # statements
    def statements(self, stops):
        out = []
        while self.peek() not in stops:
            out.append(self.statement())
        return out

    def statement(self):
        v = self.peek()
        if v == ";":
            self.next()
            return ("null",)
        if v == "BEGIN":
            self.next()
            body = self.statements({"END"})
            self.expect("END")
            self.expect(";")
            return ("block", body)
        if v == "IF":
            self.next()
            cond = self.expr()
            self.expect("THEN")
            then = self.statements({"ELSE", "END_IF"})
            other = self.statements({"END_IF"}) if self.accept("ELSE") else []
            self.expect("END_IF")
            self.expect(";")
            return ("if", cond, then, other)
        if v == "CASE":
            self.next()
            sel = self.expr()
            self.expect("OF")
            cases, other = [], None
            while not self.accept("END_CASE"):
                if self.accept("OTHERWISE"):
                    self.expect(":")
                    other = self.statement()
                    continue
                labels = [self.expr()]
                while self.accept(","):
                    labels.append(self.expr())
                self.expect(":")
                cases.append((labels, self.statement()))
            self.expect(";")
            return ("case", sel, cases, other)
        if v == "REPEAT":
            self.next()
            var = lo = hi = by = whl = until = None
            if self.kind() == "id" and self.peek(1) == ":=":
                var = self.ident()
                self.next()
                lo = self.expr()
                self.expect("TO")
                hi = self.expr()
                if self.accept("BY"):
                    by = self.expr()
            if self.accept("WHILE"):
                whl = self.expr()
            if self.accept("UNTIL"):
                until = self.expr()
            self.expect(";")
            body = self.statements({"END_REPEAT"})
            self.expect("END_REPEAT")
            self.expect(";")
            return ("repeat", var, lo, hi, by, whl, until, body)
        if v == "RETURN":
            self.next()
            e = None
            if self.accept("("):
                e = self.expr()
                self.expect(")")
            self.expect(";")
            return ("return", e)
        if v == "ESCAPE":
            self.next()
            self.expect(";")
            return ("escape",)
        if v == "SKIP":
            self.next()
            self.expect(";")
            return ("skip",)
        if v == "ALIAS":
            self.next()
            name = self.ident()
            self.expect("FOR")
            target = self.qualified(("name", self.ident()) if self.peek() != "SELF" else (self.next(), ("self",))[1])
            self.expect(";")
            body = self.statements({"END_ALIAS"})
            self.expect("END_ALIAS")
            self.expect(";")
            return ("alias", name, target, body)
        # assignment or procedure call
        target = self.qualified(self.primary())
        if self.accept(":="):
            e = self.expr()
            self.expect(";")
            return ("assign", target, e)
        self.expect(";")
        if target[0] == "call":
            return ("pcall", target[1], target[2])
        if target[0] == "name":
            return ("pcall", target[1], [])
        raise EvalError(f"statement not understood: {target!r}")

    # declarations inside an algorithm
    def head(self):
        """Nested declarations, CONSTANT and LOCAL: returns (locals [(name, expr)], nested algorithms)."""
        locs, nested = [], {}
        while True:
            v = self.peek()
            if v == "LOCAL":
                self.next()
                while not self.accept("END_LOCAL"):
                    names = [self.ident()]
                    while self.accept(","):
                        names.append(self.ident())
                    self.expect(":")
                    agg = self.peek() in ("LIST", "SET", "BAG", "ARRAY", "AGGREGATE")
                    kind = self.peek()
                    self.skip_type()
                    init = self.expr() if self.accept(":=") else None
                    self.expect(";")
                    for n in names:
                        locs.append((n, init, kind if agg else None))
                self.expect(";")
            elif v == "CONSTANT":
                self.next()
                while not self.accept("END_CONSTANT"):
                    n = self.ident()
                    self.expect(":")
                    self.skip_type()
                    self.expect(":=")
                    locs.append((n, self.expr(), None))
                    self.expect(";")
                self.expect(";")
            elif v in ("FUNCTION", "PROCEDURE"):
                start = self.i
                f = self.algorithm()
                f["text_range"] = (start, self.i)
                nested[f["name"]] = f
            elif v in ("TYPE", "ENTITY"):
                raise EvalError(f"{v} declared inside an algorithm is not supported")
            else:
                return locs, nested

    def algorithm(self):
        kind = self.next()                           # FUNCTION, PROCEDURE or RULE
        name = self.ident()
        params, ents, kinds, returns = [], [], {}, None
        if kind == "RULE":
            self.expect("FOR")
            self.expect("(")
            ents = [self.ident()]
            while self.accept(","):
                ents.append(self.ident())
            self.expect(")")
            self.expect(";")
        else:
            if self.accept("("):
                while True:
                    self.accept("VAR")
                    names = [self.ident()]
                    while self.accept(","):
                        names.append(self.ident())
                    self.expect(":")
                    pkind = self.peek()
                    self.skip_type()
                    params += names
                    kinds.update({n: pkind for n in names if pkind in _AGG_KINDS})
                    if not self.accept(";"):
                        break
                self.expect(")")
            if kind == "FUNCTION":
                self.expect(":")
                returns = self.peek() if self.peek() in _AGG_KINDS else None
                self.skip_type()
            self.expect(";")
        locs, nested = self.head()
        end = "END_" + kind
        body = self.statements({end, "WHERE"} if kind == "RULE" else {end})
        where = []
        if kind == "RULE" and self.accept("WHERE"):
            while self.peek() != end:
                label = None
                if self.kind() == "id" and self.peek(1) == ":":
                    label = self.ident().lower()
                    self.next()
                where.append((label or f"wr{len(where) + 1}", self.expr()))
                self.expect(";")
        self.expect(end)
        self.expect(";")
        return {"kind": kind, "name": name, "params": params, "locals": locs, "nested": nested, "body": body,
                "entities": ents, "where": where, "kinds": kinds, "returns": returns}


_AGG_KINDS = ("SET", "BAG", "LIST", "ARRAY")
_EXPR_CACHE = {}


def parse_expr(text):
    if text not in _EXPR_CACHE:
        p = _Parser(text)
        e = p.expr()
        if p.i != len(p.t):
            raise EvalError(f"unexpected {p.peek()!r} after the expression")
        _EXPR_CACHE[text] = e
    return _EXPR_CACHE[text]


def parse_algorithm(text):
    p = _Parser(text)
    return p.algorithm()


# ------------------------------------------------------------------ the interpreter
class _Scope:
    __slots__ = ("vars", "parent", "self_", "entity", "kinds")

    def __init__(self, parent=None, self_=None, entity=None):
        self.vars, self.parent, self.self_, self.entity = {}, parent, self_, entity
        self.kinds = {}                              # variable -> declared aggregate kind (SET, BAG, ...)

    def find(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s
            s = s.parent
        return None


class Evaluator:
    """Evaluates expressions and algorithms against one file and one dictionary."""

    def __init__(self, f, d):
        self.f, self.d = f, d
        self.insts = {}                              # instance id -> Inst
        self._rev = None                             # instance id -> [(referrer id, owner, attribute)]
        self.unknown_refs = {}                       # instance id -> an instance of an unknown entity using it
        self._algs = {}
        self._consts = {}
        self._enum_items = None
        self._selects = None
        self._memo = {}                              # (function, arguments) -> result, for pure calls
        self.steps = 0
        self.budget = STEP_BUDGET
        self.qual = {}                               # entity/type name -> SCHEMA.NAME

    # names
    def qname(self, n):
        q = self.qual.get(n)
        if q is None:
            decl = self.d.entities.get(n) or self.d.types.get(n)
            q = f"{decl['schema'].upper()}.{n.upper()}" if decl else n.upper()
            self.qual[n] = q
        return q

    def enum_items(self):
        if self._enum_items is None:
            self._enum_items = {}
            for n, t in self.d.types.items():
                if t.get("kind") == "enumeration":
                    for it in t["items"]:
                        self._enum_items.setdefault(it.upper(), n)
        return self._enum_items

    def tick(self, n=1):
        self.steps += n
        if self.steps > self.budget:
            raise EvalError(f"more than {self.budget} steps")

    # instances of the file
    def inst(self, ref):
        iid = int(ref)
        got = self.insts.get(iid)
        if got is not None:
            return got
        raw = self.f.get(ref)
        if raw is None:
            return None
        names, vals = set(), {}
        recs = []
        for r in raw.records:
            n = self.d.entity_name(r.name)
            if n is None:
                return None                          # an entity the schemas do not define
            recs.append((n, r))
        if len(recs) == 1:
            n, r = recs[0]
            names = set(self.d.mapping_order(n))
            for a, v in zip(self.d.attributes(n), r.params):
                vals[f"{a['owner']}.{a['name']}"] = (v, a["type"])
        else:
            closure = self.d.complex_closure({n for n, _ in recs})
            names = set(closure)
            for n, r in recs:
                for a, v in zip(self.d.attributes(n, within=closure), r.params):
                    vals[f"{a['owner']}.{a['name']}"] = (v, a["type"])
        i = Inst(self, frozenset(names), vals, iid)
        self.insts[iid] = i
        return i

    def reverse(self):
        if self._rev is None:
            rev = {}
            for raw in self.f:
                i = self.inst(raw.id)
                if i is None:
                    for r in raw.records:
                        for ref in _refs(r.params):
                            self.unknown_refs.setdefault(int(ref), raw)
                    continue
                for key, (v, _t) in i.vals.items():
                    owner, attr = key.split(".")
                    for r in _refs(v):
                        rev.setdefault(int(r), []).append((raw.id, owner, attr))
            self._rev = rev
        return self._rev

    def extent(self, entity):
        out = []
        for raw in self.f:
            i = self.inst(raw.id)
            if i is not None and entity in i.names:
                out.append(i)
        return out

    # converting Part 21 values
    def conv(self, v, t, through=()):
        if v is p21.UNSET or v is p21.DERIVED or v is None:
            return None
        if "agg" in t:
            if not isinstance(v, list):
                return self.generic(v)
            lo = t["lo"] if isinstance(t["lo"], int) else (1 if t["agg"] != "ARRAY" else 1)
            items = [self.conv(x, t["of"]) for x in v]
            return Agg(items, t["agg"], lo if t["agg"] == "ARRAY" else 1,
                       (t["lo"] if isinstance(t["lo"], int) else None, t["hi"] if isinstance(t["hi"], int) else None),
                       through)
        if "simple" in t:
            return _tag(self.simple(v, t["simple"]), through)
        n = t["named"]
        if n in self.d.entities:
            return self.generic(v)
        td = self.d.types.get(n)
        if td is None:
            return _tag(self.generic(v), through)
        kind = td["kind"]
        if kind == "defined":
            return self.conv(v, td["underlying"], through + (self.qname(n),))
        if kind == "enumeration":
            if isinstance(v, p21.Enum):
                e = EnumV(str(v).upper())
                e.types = through + (self.qname(n),)
                return e
            return self.generic(v)
        if kind == "select":
            if isinstance(v, p21.Typed):
                k = v.keyword.lower()
                if k in self.d.types:
                    return self.conv(v.value, {"named": k}, through + (self.qname(n),))
                return self.generic(v)
            if isinstance(v, p21.Ref):
                return self.generic(v)
            return _tag(self.generic(v), through + (self.qname(n),))
        return self.generic(v)

    def simple(self, v, s):
        if s in ("BOOLEAN", "LOGICAL") and isinstance(v, p21.Enum):
            return {"T": True, "F": False, "U": UNKNOWN}.get(str(v).upper(), EnumV(str(v).upper()))
        if s == "REAL" and isinstance(v, int) and not isinstance(v, (bool, p21.Ref)):
            return float(v)
        return self.generic(v)

    def generic(self, v):
        if v is p21.UNSET or v is p21.DERIVED or v is None:
            return None
        if isinstance(v, p21.Ref):
            return self.inst(v)
        if isinstance(v, p21.Typed):
            k = v.keyword.lower()
            if k in self.d.types:
                return self.conv(v.value, {"named": k})
            return self.generic(v.value)
        if isinstance(v, list):
            return Agg([self.generic(x) for x in v])
        if isinstance(v, p21.Enum):
            s = str(v).upper()
            return {"T": True, "F": False, "U": UNKNOWN}.get(s, EnumV(s)) if s in ("T", "F", "U") else EnumV(s)
        return v

    # attributes
    def _depth(self, n):
        return len(self.d.mapping_order(n))

    def attribute(self, i, name, group=None):
        """Value of attribute name (lower case) of instance i, as seen from entity group if given."""
        key = (name, group)
        if key in i.cache:
            return i.cache[key]
        val = self._attribute(i, name, group)
        i.cache[key] = val
        return val

    def _attribute(self, i, name, group):
        d = self.d
        if group is not None:
            if group not in i.names:
                return None                          # Part 11 12.7.4: ? when the group is absent
            scope = sorted(d.mapping_order(group), key=self._depth, reverse=True)
        else:
            scope = sorted(i.names, key=self._depth, reverse=True)
        derived_here = {}                            # owner.attr re-derived by a subtype in the instance
        for n in i.names:
            for k, dv in d.entities[n].get("derive", {}).items():
                if "." in k:
                    derived_here[d._resolve_owner(k, i.names)] = (n, dv)
        for n in scope:
            e = d.entities[n]
            for k, dv in e.get("derive", {}).items():
                if dv["name"] == name:
                    return self.derive(i, n, dv)
            for r in e["redeclared"]:
                if r["name"] == name:
                    return self._explicit(i, d._resolve_owner(r["of"], i.names), derived_here)
            for a in e["attributes"]:
                if a["name"] == name:
                    return self._explicit(i, f"{n}.{name}", derived_here)
            for inv in e["inverse"]:
                if inv["name"] == name:
                    return self.inverse(i, inv)
        raise EvalError(f"{'|'.join(sorted(i.names))} has no attribute {name}")

    def _explicit(self, i, key, derived_here):
        if key in derived_here:
            n, dv = derived_here[key]
            return self.derive(i, n, dv)
        got = i.vals.get(key)
        if i.pid is None or got is None:
            return got                               # made by a constructor: already a value
        return self.conv(*got)

    def derive(self, i, owner, dv):
        val = self.eval(parse_expr(dv["expr"]), _Scope(self_=i, entity=owner))
        return self.declared(val, dv["type"])

    def declared(self, val, t):
        """Tag a computed value with the defined types of its declaration."""
        names = []
        while "named" in t and t["named"] in self.d.types and self.d.types[t["named"]]["kind"] == "defined":
            names.append(self.qname(t["named"]))
            t = self.d.types[t["named"]]["underlying"]
        if isinstance(val, Agg) and "agg" in t and val.kind == "LIST":
            val.kind = t["agg"]
        return _tag(val, names)

    def _complete(self, i):
        """USEDIN, ROLESOF and inverses cannot be decided for an instance an unknown entity refers to."""
        self.reverse()
        u = self.unknown_refs.get(i.pid)
        if u is not None:
            raise EvalError(f"#{i.pid} is used by #{u.id} ({u.name}), an entity the loaded schemas do not define")

    def inverse(self, i, inv):
        out = []
        if i.pid is not None:
            self._complete(i)
            for rid, owner, attr in self.reverse().get(i.pid, ()):
                if attr != inv["for"]:
                    continue
                r = self.inst(rid)
                if r is not None and inv["entity"] in r.names:
                    out.append(r)
        if inv["agg"] is None:
            return out[0] if out else None
        return Agg(out, inv["agg"])

    # built-in functions
    def selects_of(self, n):
        """SELECT types n (an entity or defined type name) is a value of, directly or through other SELECTs."""
        if self._selects is None:
            self._selects = {}
            for sn, t in self.d.types.items():
                if t.get("kind") == "select":
                    ents, types = self.d.select_items(sn)
                    for x in ents | types:
                        self._selects.setdefault(x, set()).add(self.qname(sn))
        return self._selects.get(n, set())

    def typeof(self, v):
        """TYPEOF (15.25): every type v is a value of, SELECTs included."""
        if v is None:
            return None
        if isinstance(v, Inst):
            got = v.cache.get("\0typeof")
            if got is None:
                got = set()
                for n in v.names:
                    got.add(self.qname(n))
                    got |= self.selects_of(n)
                v.cache["\0typeof"] = got
            return Agg(got, "SET")
        out = set(getattr(v, "types", ()))
        for q in list(out):
            out |= self.selects_of(q.split(".")[-1].lower())
        if v is True or v is False:
            out |= {"BOOLEAN", "LOGICAL"}
        elif v is UNKNOWN:
            out.add("LOGICAL")
        elif isinstance(v, Agg):
            out.add(v.kind)
        elif isinstance(v, EnumV):
            pass
        elif isinstance(v, p21.Binary):
            out.add("BINARY")
        elif isinstance(v, str):
            out.add("STRING")
        elif isinstance(v, int):
            out |= {"INTEGER", "REAL", "NUMBER"}
        elif isinstance(v, float):
            out |= {"REAL", "NUMBER"}
        return Agg(out, "SET")

    def usedin(self, t, role):
        if not isinstance(t, Inst) or t.pid is None:
            return Agg([], "BAG")
        self._complete(t)
        role = (role or "").upper()
        out = []
        for rid, owner, attr in self.reverse().get(t.pid, ()):
            if role and f"{self.qname(owner)}.{attr.upper()}" != role:
                continue
            r = self.inst(rid)
            if r is not None:
                out.append(r)
        return Agg(out, "BAG")

    def rolesof(self, t):
        if not isinstance(t, Inst) or t.pid is None:
            return Agg([], "SET")
        self._complete(t)
        return Agg({f"{self.qname(o)}.{a.upper()}" for _, o, a in self.reverse().get(t.pid, ())}, "SET")

    def builtin(self, name, args):
        a = args
        if name == "SIZEOF":
            return None if a[0] is None else len(a[0])
        if name == "EXISTS":
            return a[0] is not None
        if name == "TYPEOF":
            return self.typeof(a[0])
        if name == "USEDIN":
            return self.usedin(a[0], a[1])
        if name == "ROLESOF":
            return self.rolesof(a[0])
        if name == "NVL":
            return a[1] if a[0] is None else a[0]
        if name in ("HIINDEX", "LOINDEX", "HIBOUND", "LOBOUND"):
            v = a[0]
            if v is None:
                return None
            if name == "LOINDEX":
                return v.lo if isinstance(v, Agg) else 1
            if name == "HIINDEX":
                return (v.lo + len(v) - 1) if isinstance(v, Agg) else len(v)
            if isinstance(v, Agg):
                lo, hi = v.bounds
                if name == "LOBOUND":
                    return lo if lo is not None else (v.lo if v.kind == "ARRAY" else 0)
                return hi
            return None
        if name == "LENGTH":
            return None if a[0] is None else len(a[0])
        if name == "BLENGTH":
            return None if a[0] is None else max(0, (len(a[0]) - 1) * 4 - int(a[0][0]))
        if name == "VALUE":
            if a[0] is None:
                return None
            try:
                s = str(a[0]).strip()
                return int(s) if re.fullmatch(r"[+-]?\d+", s) else float(s)
            except ValueError:
                return None
        if name == "VALUE_IN":
            if a[0] is None or a[1] is None:
                return UNKNOWN
            return any(self.equal(x, a[1]) is True for x in a[0])
        if name == "VALUE_UNIQUE":
            if a[0] is None:
                return UNKNOWN
            xs = list(a[0])
            for j in range(len(xs)):
                for k in range(j + 1, len(xs)):
                    if self.equal(xs[j], xs[k]) is True:
                        return False
            return True
        if name == "ODD":
            return None if a[0] is None else bool(int(a[0]) % 2)
        if name == "FORMAT":
            return None if a[0] is None else str(a[0])
        num = {"ABS": abs, "COS": math.cos, "SIN": math.sin, "TAN": math.tan, "EXP": math.exp,
               "SQRT": math.sqrt, "LOG": math.log, "LOG2": math.log2, "LOG10": math.log10, "ACOS": math.acos,
               "ASIN": math.asin}
        if name in num:
            if a[0] is None:
                return None
            try:
                return num[name](a[0])
            except (ValueError, OverflowError):
                return None
        if name == "ATAN":
            if a[0] is None or a[1] is None:
                return None
            if a[1] == 0:
                return math.copysign(math.pi / 2, a[0]) if a[0] else 0.0
            return math.atan(a[0] / a[1])
        raise KeyError(name)

    BUILTINS = {"SIZEOF", "EXISTS", "TYPEOF", "USEDIN", "ROLESOF", "NVL", "HIINDEX", "LOINDEX", "HIBOUND",
                "LOBOUND", "LENGTH", "BLENGTH", "VALUE", "VALUE_IN", "VALUE_UNIQUE", "ODD", "FORMAT", "ABS", "COS",
                "SIN", "TAN", "EXP", "SQRT", "LOG", "LOG2", "LOG10", "ACOS", "ASIN", "ATAN"}

    # comparisons
    def equal(self, a, b, depth=0):
        """Value equality (12.2.1): True, False or UNKNOWN."""
        if a is None or b is None:
            return UNKNOWN
        if a is UNKNOWN or b is UNKNOWN:
            return UNKNOWN if not (a is UNKNOWN and b is UNKNOWN) else True
        if isinstance(a, Inst) or isinstance(b, Inst):
            if not (isinstance(a, Inst) and isinstance(b, Inst)):
                return False
            if a is b or (a.pid is not None and a.pid == b.pid):
                return True
            if a.names != b.names or depth > 6:
                return False
            res = True
            for n in a.names:
                for at in self.d.entities[n]["attributes"]:
                    r = self.equal(self.attribute(a, at["name"], n), self.attribute(b, at["name"], n), depth + 1)
                    if r is False:
                        return False
                    if r is UNKNOWN:
                        res = UNKNOWN
            return res
        if isinstance(a, Agg) or isinstance(b, Agg):
            if not (isinstance(a, Agg) and isinstance(b, Agg)):
                return False
            if a.kind in ("SET", "BAG") or b.kind in ("SET", "BAG"):
                if len(a) != len(b):
                    return False
                rest = list(b)
                for x in a:
                    for k, y in enumerate(rest):
                        if self.equal(x, y, depth + 1) is True:
                            del rest[k]
                            break
                    else:
                        return False
                return True
            if len(a) != len(b):
                return False
            res = True
            for x, y in zip(a, b):
                r = self.equal(x, y, depth + 1)
                if r is False:
                    return False
                if r is UNKNOWN:
                    res = UNKNOWN
            return res
        if isinstance(a, bool) or isinstance(b, bool):
            return a is b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a == b
        if isinstance(a, str) and isinstance(b, str):
            return str(a) == str(b) and isinstance(a, EnumV) == isinstance(b, EnumV)
        return False

    def same(self, a, b):
        """Instance equality :=: (12.2.2)."""
        if a is None or b is None:
            return UNKNOWN
        if isinstance(a, Inst) and isinstance(b, Inst):
            return a is b or (a.pid is not None and a.pid == b.pid)
        if isinstance(a, Agg) and isinstance(b, Agg):
            if len(a) != len(b):
                return False
            if a.kind in ("SET", "BAG"):
                return self.equal(a, b)
            return all(self.same(x, y) is True for x, y in zip(a, b))
        return self.equal(a, b)

    def compare(self, op, a, b):
        if a is None or b is None:
            return UNKNOWN
        if op == "=":
            return self.equal(a, b)
        if op == "<>":
            return _not(self.equal(a, b))
        if op == ":=:":
            return self.same(a, b)
        if op == ":<>:":
            return _not(self.same(a, b))
        if isinstance(a, Agg) and isinstance(b, Agg):    # subset and superset (12.6.4)
            if op == "<=":
                return all(any(self.equal(x, y) is True for y in b) for x in a)
            if op == ">=":
                return all(any(self.equal(x, y) is True for y in a) for x in b)
        if isinstance(a, (bool, _Unknown)) or isinstance(b, (bool, _Unknown)):
            order = {False: 0, UNKNOWN: 1, True: 2}
            a, b = order[a], order[b]
        try:
            if op == "<":
                return a < b
            if op == ">":
                return a > b
            if op == "<=":
                return a <= b
            if op == ">=":
                return a >= b
        except TypeError:
            raise EvalError(f"cannot compare {a!r} {op} {b!r}")
        raise EvalError(f"operator {op}")

    def member(self, e, agg):
        if e is None or agg is None:
            return UNKNOWN
        if not isinstance(agg, (list, set)):
            raise EvalError(f"IN needs an aggregate, found {agg!r}")
        if isinstance(e, Inst):
            return any(isinstance(x, Inst) and self.same(e, x) is True for x in agg)
        res = False
        for x in agg:
            r = self.equal(e, x)
            if r is True:
                return True
            if r is UNKNOWN:
                res = UNKNOWN
        return res

    # the evaluator
    def eval(self, e, s):
        self.tick()
        k = e[0]
        if k == "lit":
            return e[1]
        if k == "name":
            return self.name(e[1], s)
        if k == "self":
            sc = s
            while sc is not None and sc.self_ is None and "SELF" not in sc.vars:
                sc = sc.parent
            if sc is None:
                raise EvalError("SELF outside an entity or type")
            return sc.vars["SELF"] if "SELF" in sc.vars else sc.self_
        if k == "attr":
            if e[1][0] == "group":                   # inst\\entity.attr: the attribute as entity sees it
                g = self.eval(e[1][1], s)
                if g is None:
                    return None
                if not isinstance(g, Inst):
                    raise EvalError(f"group reference on {g!r}")
                return self.attribute(g, e[2].lower(), e[1][2].lower())
            base = self.eval(e[1], s)
            if base is None:
                return None
            name = e[2]
            if isinstance(base, Inst):
                return self.attribute(base, name.lower())
            if isinstance(base, (str, Agg)) or base is True or base is False:
                # an enumeration reference type.item written as a qualified name
                if e[1][0] == "name" and e[1][1].lower() in self.d.types:
                    return EnumV(name)
            if e[1][0] == "name" and e[1][1].lower() in self.d.types:
                return EnumV(name)
            raise EvalError(f"attribute {name} of a non-entity value {base!r}")
        if k == "group":
            base = self.eval(e[1], s)
            if base is None:
                return None
            if not isinstance(base, Inst):
                raise EvalError(f"group reference on {base!r}")
            return base if e[2].lower() in base.names else None
        if k == "index":
            base = self.eval(e[1], s)
            i = self.eval(e[2], s)
            if base is None or i is None:
                return None
            if e[3] is not None:
                j = self.eval(e[3], s)
                if j is None:
                    return None
                if isinstance(base, str):
                    return base[int(i) - 1:int(j)]
                raise EvalError("index range on an aggregate")
            if isinstance(base, Agg):
                pos = int(i) - base.lo
                return base[pos] if 0 <= pos < len(base) else None
            if isinstance(base, str):
                pos = int(i) - 1
                return base[pos] if 0 <= pos < len(base) else None
            raise EvalError(f"index on {base!r}")
        if k == "un":
            v = self.eval(e[2], s)
            if e[1] == "NOT":
                return _not(v)
            if v is None:
                return None
            return -v if e[1] == "-" else v
        if k == "bin":
            return self.binop(e, s)
        if k == "call":
            return self.call(e[1], e[2], s)
        if k == "aggr":
            out = Agg([], "LIST")
            for x, rep in e[1]:
                v = self.eval(x, s)
                n = 1 if rep is None else self.eval(rep, s)
                if n is None:
                    return None
                out.extend([v] * int(n))
            return out
        if k == "interval":
            lo, mid, hi = self.eval(e[1], s), self.eval(e[3], s), self.eval(e[5], s)
            return _and(self.compare(e[2], lo, mid), self.compare(e[4], mid, hi))
        if k == "query":
            agg = self.eval(e[2], s)
            if agg is None:
                return None
            out = []
            sc = _Scope(s)
            for x in agg:
                sc.vars[e[1]] = x
                if self.eval(e[3], sc) is True:
                    out.append(x)
            return agg.like(out) if isinstance(agg, Agg) else Agg(out, "BAG")
        raise EvalError(f"expression {k}")

    def binop(self, e, s):
        op = e[1]
        if op == "AND":
            a = self.eval(e[2], s)
            if a is False:
                return False
            return _and(a, self.eval(e[3], s))
        if op == "OR":
            a = self.eval(e[2], s)
            if a is True:
                return True
            return _or(a, self.eval(e[3], s))
        a, b = self.eval(e[2], s), self.eval(e[3], s)
        if op == "XOR":
            return _xor(a, b)
        if op in ("=", "<>", "<", ">", "<=", ">=", ":=:", ":<>:"):
            return self.compare(op, a, b)
        if op == "IN":
            return self.member(a, b)
        if op == "LIKE":
            if a is None or b is None:
                return UNKNOWN
            return _like(str(a), str(b))
        if op == "||":
            return self.combine(a, b)
        if a is None or b is None:
            return None
        if isinstance(a, Agg) or isinstance(b, Agg):
            return self.aggop(op, a, b)
        if op == "+":
            if isinstance(a, str) and isinstance(b, str):
                return str(a) + str(b)
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                return None
            return float(a) / float(b)
        if op == "DIV":
            if b == 0:
                return None
            return int(a / b)
        if op == "MOD":
            if b == 0:
                return None
            return int(a) - int(a / b) * int(b)
        if op == "**":
            try:
                return a ** b
            except (OverflowError, ZeroDivisionError, ValueError):
                return None
        raise EvalError(f"operator {op}")

    def aggop(self, op, a, b):
        if isinstance(a, Agg):
            if op == "+":
                if isinstance(b, Agg):
                    if a.kind == "SET":
                        out = a.like(a)
                        for x in b:
                            if self.member(x, out) is not True:
                                out.append(x)
                        return out
                    return a.like(list(a) + list(b))
                if a.kind == "SET" and self.member(b, a) is True:
                    return a.like(a)
                return a.like(list(a) + [b])
            if op == "-":
                out = list(a)
                for x in (b if isinstance(b, Agg) else [b]):
                    for k, y in enumerate(out):
                        if self.equal(x, y) is True:
                            del out[k]
                            if a.kind != "BAG":
                                continue
                            break
                    if a.kind == "SET":
                        out = [y for y in out if self.equal(x, y) is not True]
                return a.like(out)
            if op == "*" and isinstance(b, Agg):
                return a.like([x for x in a if self.member(x, b) is True])
        elif op == "+" and isinstance(b, Agg):
            if b.kind == "SET" and self.member(a, b) is True:
                return b.like(b)
            return b.like([a] + list(b))
        raise EvalError(f"aggregate operator {op}")

    def combine(self, a, b):
        """The complex entity constructor || (12.10)."""
        if a is None or b is None:
            return None
        if not (isinstance(a, Inst) and isinstance(b, Inst)):
            raise EvalError("|| needs entity values")
        vals = dict(a.vals)
        for k, v in b.vals.items():
            if k not in vals or vals[k] is None:
                vals[k] = v
        return Inst(self, a.names | b.names, vals)

    def name(self, n, s):
        sc = s.find(n)
        if sc is not None:
            return sc.vars[n]
        # an attribute of SELF referred to by its name alone (in an entity's rules and derivations)
        sc = s
        while sc is not None and sc.self_ is None:
            sc = sc.parent
        if sc is not None and sc.entity is not None:
            low = n.lower()
            for m in self.d.mapping_order(sc.entity):
                e = self.d.entities[m]
                if any(a["name"] == low for a in e["attributes"]) or \
                        any(dv["name"] == low for dv in e.get("derive", {}).values()) or \
                        any(r["name"] == low for r in e["redeclared"]) or \
                        any(iv["name"] == low for iv in e["inverse"]):
                    return self.attribute(sc.self_, low)
        low = n.lower()
        if low in self.d.algorithms["constants"]:
            return self.constant(low)
        if n in self.enum_items():
            e = EnumV(n)
            e.types = (self.qname(self.enum_items()[n]),)
            return e
        if low in self.d.entities or low in self.d.types:
            return n                                  # an entity or type name used as a value (rare)
        raise EvalError(f"unknown name {n}")

    def constant(self, n):
        if n not in self._consts:
            self._consts[n] = _PENDING
            c = self.d.algorithms["constants"][n]
            self._consts[n] = self.declared(self.eval(parse_expr(c["expr"]), _Scope()), c["type"])
        v = self._consts[n]
        if v is _PENDING:
            raise EvalError(f"constant {n} refers to itself")
        return v

    def algorithm(self, kind, n):
        key = (kind, n)
        if key not in self._algs:
            src = self.d.algorithms[kind].get(n)
            if src is None:
                self._algs[key] = None
            else:
                try:
                    self._algs[key] = parse_algorithm(src["text"])
                except (EvalError, express.ExpressError) as err:
                    raise EvalError(f"{kind[:-1]} {n} could not be read: {err}")
        return self._algs[key]

    def call(self, n, argx, s):
        # nested algorithms of the enclosing function
        sc = s
        while sc is not None:
            nested = sc.vars.get("\0nested")
            if nested and n in nested:
                return self.run(nested[n], [self.eval(x, s) for x in argx], s)
            sc = sc.parent
        if n in self.BUILTINS:
            return self.builtin(n, [self.eval(x, s) for x in argx])
        low = n.lower()
        f = self.algorithm("functions", low)
        if f is not None:
            args = [self.eval(x, s) for x in argx]
            key = _memo_key(low, args)
            if key is None:
                return self.run(f, args)
            if key not in self._memo:
                self._memo[key] = self.run(f, args)
            v = self._memo[key]
            return v.like(v) if isinstance(v, Agg) else v
        if low in self.d.entities:
            return self.construct(low, [self.eval(x, s) for x in argx])
        if low in self.d.types:                      # a defined type's name used as a conversion
            v = self.eval(argx[0], s) if argx else None
            return _tag(v, (self.qname(low),))
        raise EvalError(f"function {low} is not declared in the loaded schemas")

    def construct(self, n, args):
        own = [a["name"] for a in self.d.entities[n]["attributes"]]
        allattrs = [(a["owner"], a["name"]) for a in self.d.attributes(n) if not a["derived"]]
        vals = {}
        if len(args) == len(own):
            for a, v in zip(own, args):
                vals[f"{n}.{a}"] = v
        elif len(args) == len(allattrs):
            for (o, a), v in zip(allattrs, args):
                vals[f"{o}.{a}"] = v
        else:
            raise EvalError(f"entity constructor {n} with {len(args)} argument(s)")
        return Inst(self, frozenset(self.d.mapping_order(n)), vals)

    def run(self, f, args, parent=None):
        s = _Scope(parent if parent is not None else None)
        if len(args) != len(f["params"]):
            raise EvalError(f"{f['name'].lower()} called with {len(args)} argument(s), takes {len(f['params'])}")
        for p_, v in zip(f["params"], args):
            s.vars[p_] = v.like(v) if isinstance(v, Agg) and f["kind"] == "FUNCTION" else v
        s.kinds.update(f.get("kinds", {}))
        if f["nested"]:
            s.vars["\0nested"] = f["nested"]
        for n, init, aggkind in f["locals"]:
            if aggkind in _AGG_KINDS:
                s.kinds[n] = aggkind
            s.vars[n] = self.coerce(self.eval(init, s) if init is not None else None, s.kinds.get(n))
        try:
            self.block(f["body"], s)
        except _Return as r:
            return self.coerce(r.value, f.get("returns"))
        return None

    def coerce(self, v, kind):
        """An aggregate assigned to a variable declared SET, BAG, LIST or ARRAY takes that kind (a SET
        drops duplicates); other values are copied so that aggregates are assigned by value."""
        if not isinstance(v, Agg):
            return v
        out = v.like(v)
        if kind in _AGG_KINDS and kind != out.kind:
            out.kind = kind
            if kind == "SET":
                uniq = Agg([], "SET")
                for x in out:
                    if self.member(x, uniq) is not True:
                        uniq.append(x)
                out[:] = uniq
        return out

    def block(self, stmts, s):
        for st in stmts:
            self.statement(st, s)

    def statement(self, st, s):
        self.tick()
        k = st[0]
        if k == "null":
            return
        if k == "block":
            return self.block(st[1], s)
        if k == "assign":
            v = self.eval(st[2], s)
            if st[1][0] == "name":
                sc = s.find(st[1][1])
                v = self.coerce(v, sc.kinds.get(st[1][1]) if sc is not None else None)
            return self.assign(st[1], v, s)
        if k == "if":
            c = self.eval(st[1], s)
            if c is True:
                return self.block(st[2], s)
            return self.block(st[3], s)
        if k == "case":
            sel = self.eval(st[1], s)
            for labels, body in st[2]:
                for lab in labels:
                    if self.equal(sel, self.eval(lab, s)) is True:
                        return self.statement(body, s)
            if st[3] is not None:
                return self.statement(st[3], s)
            return
        if k == "repeat":
            return self.repeat(st, s)
        if k == "return":
            raise _Return(self.eval(st[1], s) if st[1] is not None else None)
        if k == "escape":
            raise _Escape()
        if k == "skip":
            raise _Skip()
        if k == "alias":
            sc = _Scope(s)
            sc.vars[st[1]] = self.eval(st[2], s)
            return self.block(st[3], sc)
        if k == "pcall":
            return self.pcall(st[1], st[2], s)
        raise EvalError(f"statement {k}")

    def repeat(self, st, s):
        _, var, lo, hi, by, whl, until, body = st
        sc = s
        if var is not None:
            a, b = self.eval(lo, s), self.eval(hi, s)
            step = self.eval(by, s) if by is not None else 1
            if a is None or b is None or step is None:
                return
            if step == 0:
                raise EvalError("REPEAT with increment 0")
            sc = _Scope(s)
            i = a
            seq = []
            while (step > 0 and i <= b) or (step < 0 and i >= b):
                seq.append(i)
                i += step
        else:
            seq = None
        n = 0
        while True:
            if seq is not None:
                if n >= len(seq):
                    return
                sc.vars[var] = seq[n]
            n += 1
            if whl is not None and self.eval(whl, sc) is not True:
                return
            try:
                self.block(body, sc)
            except _Escape:
                return
            except _Skip:
                pass
            if until is not None and self.eval(until, sc) is True:
                return
            self.tick()

    def assign(self, target, v, s):
        if target[0] == "name":
            sc = s.find(target[1])
            if sc is None:
                raise EvalError(f"assignment to undeclared {target[1]}")
            sc.vars[target[1]] = v
            return
        if target[0] == "index":
            base = self.eval(target[1], s)
            i = self.eval(target[2], s)
            if isinstance(base, Agg) and i is not None:
                pos = int(i) - base.lo
                if 0 <= pos < len(base):
                    base[pos] = v
                    return
                if pos == len(base):
                    base.append(v)
                    return
            raise EvalError("assignment to an element outside the aggregate")
        if target[0] == "attr":
            base = self.eval(target[1], s)
            if isinstance(base, Inst) and base.pid is None:
                for key in base.vals:
                    if key.split(".")[1] == target[2].lower():
                        base.vals[key] = v
                        base.cache.clear()
                        return
            raise EvalError("assignment to an attribute")
        raise EvalError(f"assignment to {target[0]}")

    def pcall(self, n, argx, s):
        if n in ("INSERT", "REMOVE"):
            agg = self.eval(argx[0], s)
            if not isinstance(agg, Agg):
                raise EvalError(f"{n} on a non-aggregate")
            if n == "INSERT":
                e, p_ = self.eval(argx[1], s), self.eval(argx[2], s)
                agg.insert(int(p_), e)               # 15.? INSERT(L, E, P): E after position P
            else:
                p_ = self.eval(argx[1], s)
                del agg[int(p_) - 1]
            return
        f = self.algorithm("procedures", n.lower())
        if f is None:
            raise EvalError(f"procedure {n.lower()} is not declared in the loaded schemas")
        self.run(f, [self.eval(x, s) for x in argx])


_PENDING = object()


def _memo_key(name, args):
    """A key for a call whose arguments are all persistent instances or simple values, else None."""
    out = [name]
    for a in args:
        if isinstance(a, Inst):
            if a.pid is None:
                return None
            out.append(("#", a.pid))
        elif a is None or isinstance(a, (bool, _Unknown)):
            out.append(("v", a))
        elif isinstance(a, (int, float, str)) and not isinstance(a, Agg):
            out.append((type(a).__name__, a, tuple(getattr(a, "types", ()))))
        else:
            return None
    return tuple(out)


def _refs(v):
    if isinstance(v, p21.Ref):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _refs(x)
    elif isinstance(v, p21.Typed):
        yield from _refs(v.value)


def _like(s, pat):
    """The LIKE operator (12.2.5): @ a letter, ^ an upper-case letter, ? any character, & the rest,
    # a digit, $ a substring (word), * any characters, \\ escapes, ! a lower-case letter."""
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
            continue
        out.append({"@": "[A-Za-z]", "^": "[A-Z]", "!": "[a-z]", "?": ".", "&": ".*", "#": "[0-9]",
                    "$": r"\S+", "*": ".*"}.get(c, re.escape(c)))
        i += 1
    return re.fullmatch("".join(out), s, re.S) is not None


# ------------------------------------------------------------------ checking a file
class Result:
    """What rules() decided: issues, and counts of rules evaluated, violated and not evaluated."""

    def __init__(self):
        self.issues = []
        self.evaluated = 0
        self.violated = 0
        self.unevaluated = {}                         # rule -> (count, first reason)

    def not_evaluated(self, rule, reason):
        c, r = self.unevaluated.get(rule, (0, reason))
        self.unevaluated[rule] = (c + 1, r)


def rules(f, d, part=None):
    """Evaluate every WHERE, UNIQUE and global rule of dictionary d on file f -> Result.

    part(schema) names the standard a schema comes from, for the messages (optional)."""
    import sys
    res = Result()
    ev = Evaluator(f, d)
    where = (lambda n: f" ({part(d.schema_of(n) or (d.types.get(n) or {}).get('schema'))})") if part else \
        (lambda n: "")
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 20000))
    try:
        type_cache = {}
        for raw in f:
            i = ev.inst(raw.id)
            if i is None:
                continue
            for n in sorted(i.names, key=lambda x: (len(d.mapping_order(x)), x)):
                for w in d.entities[n]["where"]:
                    if not isinstance(w, dict):
                        continue
                    rule = f"{n}.{w['label']}"
                    out = _decide(ev, res, rule, w["expr"], _Scope(self_=i, entity=n))
                    if out is False:
                        level, why = "error", ""
                        explain = KNOWN.get((n, w["label"]))
                        if explain is not None:
                            try:
                                level, why = explain(ev, i, w["expr"]) or (level, why)
                            except (EvalError, RecursionError, TypeError, AttributeError) as err:
                                why = f" (not explained: {err})"
                        res.issues.append(express.Issue(level, raw.id, f"{n} {w['label'].upper()} is violated"
                                                                       f"{where(n)}{why}"))
            _inverse_bounds(ev, res, i, raw.id, where)
            _type_rules(ev, res, i, raw.id, type_cache, where)
        _unique_rules(ev, res, where)
        _global_rules(ev, res, where)
    finally:
        sys.setrecursionlimit(old_limit)
    for rule, (c, reason) in sorted(res.unevaluated.items()):
        res.issues.append(express.Issue("info", None, f"{rule}: not evaluated on {c} instance(s): {reason}"))
    return res


def _vrep_wr2(ev, i, expr):
    """ISO 10303-108 variational_representation WR2 against the rest of Part 108 and Part 55.

    invalidate_vrep_item wants a constraint's elements, and a bound parameter's owner, used by exactly the
    representations that use the constraint or parameter. But 6.3.4 WR1 makes the current result share
    the wrapper's geometry (and WR3 keeps constraints out of it), and a procedural representation, which
    may hold only sequences (55, 4.3.3), shares the sequence with the current result a history's wrapper
    needs; and an auxiliary line used as a symmetry's mirror is never a reference element, which
    symmetry_geometric_constraint WR1 forbids it to be. Violations of those kinds alone are warnings."""
    cm = ev.attribute(i, "cm_link")
    if cm is None:
        return None
    cur = ev.attribute(cm, "rep_2")
    s = _Scope(self_=i, entity="variational_representation")
    bad = ev.eval(parse_expr("QUERY ( Q <* ( SELF \\ REPRESENTATION . ITEMS - CM_LINK . REP_2 . ITEMS ) | "
                             "INVALIDATE_VREP_ITEM ( Q ) )"), s)
    cur_items = {x.pid for x in ev.attribute(cur, "items")}
    twins = {cur.pid}                                # representations holding exactly the current result's items
    role = f"{ev.qname('representation')}.ITEMS"
    for x in ev.attribute(cur, "items"):
        for r in ev.usedin(x, role):
            if {y.pid for y in ev.attribute(r, "items")} == cur_items:
                twins.add(r.pid)

    def reps_of(x):
        s.vars["E"] = x
        return {r.pid for r in ev.eval(parse_expr("USING_REPRESENTATIONS ( E )"), s)}

    other, why = [], set()
    for q in bad or ():
        reps = reps_of(q)
        if "explicit_constraint" in q.names:
            used = [reps_of(e) for e in list(ev.attribute(q, "reference_elements") or []) +
                    list(ev.attribute(q, "constrained_elements") or [])]
            ok = all(u >= reps and u - reps <= twins for u in used)
            why.add("shared geometry")
        elif "bound_variational_parameter" in q.names:
            sem = ev.attribute(ev.attribute(q, "interpretation", "generic_variable"), "semantics")
            owner = ev.attribute(sem, "owning_instance") if isinstance(sem, Inst) and \
                "instance_attribute_reference" in sem.names else None
            u = reps_of(owner) if owner is not None else set()
            ok = owner is not None and u >= reps and u - reps <= twins
            why.add("a history's owner shared with its procedural representation")
        elif "auxiliary_geometric_representation_item" in q.names:
            role = f"{ev.qname('symmetry_geometric_constraint')}.MIRROR_ELEMENT"
            basis = ev.attribute(q, "basis_curve") if "trimmed_curve" in q.names else None
            ok = len(ev.usedin(q, role)) > 0 or (isinstance(basis, Inst) and len(ev.usedin(basis, role)) > 0)
            why.add("an auxiliary line used as a symmetry mirror")
        else:
            ok = False
        if not ok:
            other.append(q)
    if other:
        return "error", f": {', '.join(repr(q) for q in other)} invalid (invalidate_vrep_item)"
    return "warning", (f": {len(bad)} item(s) invalid only through {', '.join(sorted(why))}; ISO 10303-108 cannot be "
                       "met here (see the notes of express_eval._vrep_wr2)")


def _sketch_wr1(ev, i, expr):
    """ISO 10303-108 Cor.2 neutral_sketch_representation WR1 requires three-dimensional items, against
    the clause's own statement of WR1 (8.4.9, "the dimensionality ... shall be 2") and the 2005 rule."""
    if "= 3" not in expr:
        return None
    gri = ev.qname("geometric_representation_item")
    ok = ev.eval(parse_expr(f"SIZEOF ( QUERY ( Q <* SELF \\ REPRESENTATION . ITEMS | ( '{gri}' IN TYPEOF ( Q ) ) "
                            "AND ( Q \\ GEOMETRIC_REPRESENTATION_ITEM . DIM <> 2 ) ) ) = 0"),
                 _Scope(self_=i, entity="neutral_sketch_representation"))
    if ok is True:
        return "warning", (": as Cor.2 prints it, WR1 wants three-dimensional items, which contradicts the clause "
                           "(8.4.9: the dimensionality shall be 2); the items are two-dimensional as the clause says")
    return None


def _hole_wr2(ev, i, expr):
    """ISO 10303-111 solid_with_stepped_round_hole_and_conical_transitions WR2 is written with XOR where its
    statement says "either the hole is a through hole or the number of conical transitions ... shall not
    exceed the number of cylindrical segments": as printed, a through hole may not have that few
    transitions, though WR1 allows up to one more than the segments."""
    s = _Scope(self_=i, entity="solid_with_stepped_round_hole_and_conical_transitions")
    through = "solid_with_through_depression" in i.names
    few = ev.eval(parse_expr("SIZEOF ( CONICAL_TRANSITIONS ) <= SELF \\ SOLID_WITH_STEPPED_ROUND_HOLE . SEGMENTS"), s)
    if through and few is True:
        return "warning", (": WR2 is printed with XOR, but its statement reads 'either a through hole or no more "
                           "transitions than segments'; this through hole meets the statement")
    return None


# Rules the standards cannot satisfy as printed: (entity, label) -> explain(evaluator, instance, rule text)
# returning (level, note) when the violation is the contradiction and nothing else, or None.
KNOWN = {("variational_representation", "wr2"): _vrep_wr2,
         ("neutral_sketch_representation", "wr1"): _sketch_wr1,
         ("solid_with_stepped_round_hole_and_conical_transitions", "wr2"): _hole_wr2}


def _decide(ev, res, rule, expr, scope):
    ev.steps = 0
    try:
        v = ev.eval(parse_expr(expr), scope)
    except (EvalError, express.ExpressError, RecursionError, KeyError, TypeError, AttributeError,
            ZeroDivisionError, ValueError, IndexError) as err:
        res.not_evaluated(rule, f"{type(err).__name__}: {err}" if not isinstance(err, EvalError) else str(err))
        return None
    res.evaluated += 1
    if v is False:
        res.violated += 1
        return False
    return True


def _inverse_bounds(ev, res, i, iid, where):
    """INVERSE cardinalities (9.2.1.3): exactly one user for a single inverse, the bounds of SET or BAG."""
    for n in i.names:
        for inv in ev.d.entities[n]["inverse"]:
            rule = f"{n}.{inv['name']} (inverse)"
            ev.steps = 0
            try:
                v = ev.inverse(i, inv)
            except EvalError as err:
                res.not_evaluated(rule, str(err))
                continue
            res.evaluated += 1
            k = (0 if v is None else 1) if inv["agg"] is None else len(v)
            lo = 1 if inv["agg"] is None else (inv["lo"] if isinstance(inv["lo"], int) else 0)
            hi = 1 if inv["agg"] is None else (inv["hi"] if isinstance(inv["hi"], int) else None)
            if k < lo or (hi is not None and k > hi):
                res.violated += 1
                want = "exactly 1" if inv["agg"] is None else f"{lo} to {hi if hi is not None else '?'}"
                res.issues.append(express.Issue(
                    "error", iid, f"{n}.{inv['name']}: used by {k} {inv['entity']} through {inv['for']}, the "
                                  f"inverse wants {want}{where(n)}"))


def _type_rules(ev, res, i, iid, cache, where):
    """WHERE rules of the defined types of i's explicit attribute values, and expression bounds."""
    for key, (v, t) in i.vals.items():
        if v is p21.UNSET or v is p21.DERIVED:
            continue
        owner, attr = key.split(".")
        _type_value(ev, res, v, t, iid, f"{owner}.{attr}", cache, where, i, owner)


def _type_value(ev, res, v, t, iid, attr, cache, where, inst, owner, depth=0):
    d = ev.d
    if v is p21.UNSET or v is p21.DERIVED or depth > 8:
        return
    if "agg" in t:
        if not isinstance(v, list):
            return
        for b, which in ((t["lo"], "lower"), (t["hi"], "upper")):
            if isinstance(b, str) and depth == 0:
                rule = f"{owner}.{attr.split('.')[1]} bound"
                ev.steps = 0
                try:
                    n = ev.eval(parse_expr(b), _Scope(self_=inst, entity=owner))
                except (EvalError, express.ExpressError, RecursionError, TypeError) as err:
                    res.not_evaluated(rule, str(err))
                    continue
                res.evaluated += 1
                if isinstance(n, int) and ((which == "lower" and len(v) < n) or (which == "upper" and len(v) > n)):
                    res.violated += 1
                    res.issues.append(express.Issue(
                        "error", iid, f"{attr} has {len(v)} element(s); its {which} bound {b.lower()} is {n}"
                                      f"{where(owner)}"))
        for x in v:
            _type_value(ev, res, x, t["of"], iid, attr, cache, where, inst, owner, depth + 1)
        return
    if "named" not in t:
        return
    n = t["named"]
    td = d.types.get(n)
    if td is None:
        return
    if td["kind"] == "select":
        if isinstance(v, p21.Typed) and v.keyword.lower() in d.types:
            _type_value(ev, res, v.value, {"named": v.keyword.lower()}, iid, attr, cache, where, inst, owner,
                        depth + 1)
        return
    if td["kind"] != "defined":
        return
    rules_ = [w for w in td["where"] if isinstance(w, dict)]
    if rules_:
        try:
            ck = (n, p21.format_value(v))
        except Exception:
            ck = None
        if ck is not None and ck in cache:
            out = cache[ck]
        else:
            val = ev.conv(v, t)
            out = []
            for w in rules_:
                rule = f"{n}.{w['label']}"
                out.append((w["label"], _decide(ev, res, rule, w["expr"], _Scope(self_=val))))
            if ck is not None:
                cache[ck] = out
        for label, r in out:
            if r is False:
                res.issues.append(express.Issue(
                    "error", iid, f"{attr}: the value {p21.format_value(v)} breaks {n} {label.upper()}{where(n)}"))
    _type_value(ev, res, v, td["underlying"], iid, attr, cache, where, inst, owner, depth + 1)


def _unique_rules(ev, res, where):
    d = ev.d
    for n, e in d.entities.items():
        urs = [u for u in e["unique"] if isinstance(u, dict)]
        if not urs:
            continue
        pop = ev.extent(n)
        if len(pop) < 2:
            continue
        for u in urs:
            rule = f"{n}.{u['label']}"
            seen = {}
            try:
                for i in pop:
                    vals = []
                    for a in u["attrs"]:
                        if a.startswith("self\\"):
                            ent, at = a[5:].split(".")
                            vals.append(ev.attribute(i, at, ent))
                        else:
                            vals.append(ev.attribute(i, a))
                    if any(x is None for x in vals):
                        continue
                    k = tuple(_key(x) for x in vals)
                    if k in seen:
                        res.violated += 1
                        res.issues.append(express.Issue(
                            "error", i.pid, f"{n} {u['label'].upper()}: {', '.join(u['attrs'])} same as "
                                            f"#{seen[k]} (UNIQUE){where(n)}"))
                    else:
                        seen[k] = i.pid
                res.evaluated += 1
            except (EvalError, RecursionError, TypeError) as err:
                res.not_evaluated(rule, str(err))


def _key(v):
    if isinstance(v, Inst):
        return ("#", v.pid) if v.pid is not None else ("#", id(v))
    if isinstance(v, Agg):
        k = tuple(_key(x) for x in v)
        return tuple(sorted(k, key=repr)) if v.kind in ("SET", "BAG") else k
    if isinstance(v, EnumV):
        return (".", str(v))
    if isinstance(v, str):
        return str(v)
    return v


def _global_rules(ev, res, where):
    d = ev.d
    unknown = next((raw for raw in ev.f if ev.inst(raw.id) is None), None)
    if unknown is not None and d.algorithms["rules"]:
        for _ in d.algorithms["rules"]:
            res.not_evaluated("global rules", f"the population is incomplete: #{unknown.id} ({unknown.name}) is "
                                              "of an entity the loaded schemas do not define")
        return
    for n in d.algorithms["rules"]:
        rule = f"RULE {n}"
        try:
            r = ev.algorithm("rules", n)
        except EvalError as err:
            res.not_evaluated(rule, str(err))
            continue
        ev.steps = 0
        ev.budget = GLOBAL_BUDGET
        try:
            s = _Scope()
            for ent in r["entities"]:
                s.vars[ent] = Agg(ev.extent(ent.lower()), "SET")
            for ln, init, aggkind in r["locals"]:
                if aggkind in _AGG_KINDS:
                    s.kinds[ln] = aggkind
                s.vars[ln] = ev.coerce(ev.eval(init, s) if init is not None else None, s.kinds.get(ln))
            if r["nested"]:
                s.vars["\0nested"] = r["nested"]
            ev.block(r["body"], s)
            for label, x in r["where"]:
                v = ev.eval(x, s)
                res.evaluated += 1
                if v is False:
                    res.violated += 1
                    res.issues.append(express.Issue("error", None, f"global rule {n.lower()} {label.upper()} is "
                                                                   f"violated{where(r['entities'][0].lower())}"))
        except (EvalError, RecursionError, TypeError, KeyError, AttributeError, ValueError) as err:
            res.not_evaluated(rule, str(err))
        finally:
            ev.budget = STEP_BUDGET
