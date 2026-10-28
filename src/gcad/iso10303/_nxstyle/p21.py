# SPDX-License-Identifier: LGPL-2.1-or-later
"""STEP files (ISO 10303-21:2002, clear text encoding of the exchange structure): read and write
(no FreeCAD needed).

What the second edition defines, and this module reads and writes:

  tokens (6)          keywords (user-defined "!KEYWORD" too), integers, reals (a full stop is required),
                      strings with '' and \\\\, the \\S\\ \\P?\\ \\X\\hh \\X2\\..\\X0\\ \\X4\\..\\X0\\ control
                      directives (decoded to Unicode, written back for anything outside the basic alphabet),
                      instance names, enumerations, binaries, "$" (no value) and "*" (derived)
  token separators    spaces, /* comments */, the print control directives \\N\\ and \\F\\ (5.6, 11); line
                      breaks are ignored wherever they are, even inside a token (annex A)
  header (8)          FILE_DESCRIPTION, FILE_NAME, FILE_SCHEMA in that order, then FILE_POPULATION,
                      SECTION_LANGUAGE, SECTION_CONTEXT and user-defined header entities
  data sections (9)   one or more; several need DATA('name',('SCHEMA')); instance names unique across them;
                      forward references allowed
  instances (10.2)    simple records (internal mapping) and complex ones, ( A(..) B(..) ) (external mapping)

The third edition's ANCHOR, REFERENCE and SIGNATURE sections are skipped with a note in File.notes; their
instance values (<uri>, @name) are not read.

Values: int, float, str, list, Ref (an instance name), Enum ('.T.' is Enum('T')), Typed (a typed
parameter, the value of a SELECT), Binary, and the singletons UNSET ("$") and DERIVED ("*").
What a value means is the EXPRESS schema's business: see express.py, which checks instances against it.
"""

import re

MAGIC, END = "ISO-10303-21;", "END-ISO-10303-21;"
HEADER_ORDER = ("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA")
HEADER_OPTIONAL = ("FILE_POPULATION", "SECTION_LANGUAGE", "SECTION_CONTEXT")
MAX_STRING = 32769                                # bytes as stored, with the two apostrophes (6.3.3.4)


class P21Error(ValueError):
    def __init__(self, msg, pos=None, text=None):
        if pos is not None and text is not None:
            msg = f"{msg} (at character {pos}: {text[pos:pos + 40]!r})"
        super().__init__(msg)


class Ref(int):
    """An entity instance name, #n."""
    def __repr__(self):
        return f"#{int(self)}"


class Enum(str):
    """An enumeration value without its full stops: .T. is Enum('T')."""
    def __repr__(self):
        return f".{str(self)}."


class Binary(str):
    """A binary as its bit string ('111011'); written as "23B"."""
    def __repr__(self):
        return f'Binary({str(self)!r})'


class Typed:
    """A typed parameter: KEYWORD(value), how a SELECT carries a defined type's value (10.1.8)."""
    __slots__ = ("keyword", "value")

    def __init__(self, keyword, value):
        self.keyword, self.value = keyword.upper(), value

    def __eq__(self, other):
        return isinstance(other, Typed) and (self.keyword, self.value) == (other.keyword, other.value)

    def __hash__(self):
        return hash((self.keyword, repr(self.value)))

    def __repr__(self):
        return f"{self.keyword}({self.value!r})"


class _Token:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return self.text


UNSET, DERIVED = _Token("$"), _Token("*")


class Record:
    """KEYWORD(parameters): a whole simple instance, or one partial value of a complex one."""
    __slots__ = ("name", "params")

    def __init__(self, name, params=()):
        self.name, self.params = name.upper(), list(params)

    def __eq__(self, other):
        return isinstance(other, Record) and self.name == other.name and self.params == other.params

    def __repr__(self):
        return f"{self.name}({', '.join(map(repr, self.params))})"


class Instance:
    """#id = one record (simple, internal mapping) or several in name order (complex, external mapping)."""
    __slots__ = ("id", "records", "section")

    def __init__(self, id, records, section=None):
        self.id, self.records, self.section = id, list(records), section

    @property
    def complex(self):
        return len(self.records) > 1

    @property
    def name(self):
        """The keyword of a simple instance; the record names joined by '+' for a complex one."""
        return "+".join(r.name for r in self.records)

    @property
    def names(self):
        return [r.name for r in self.records]

    @property
    def params(self):
        """Parameters of a simple instance (a complex one has them per record)."""
        if self.complex:
            raise P21Error(f"#{self.id} is a complex instance; use records")
        return self.records[0].params

    def record(self, name):
        name = name.upper()
        for r in self.records:
            if r.name == name:
                return r
        return None

    def __repr__(self):
        body = repr(self.records[0]) if not self.complex else "(" + " ".join(map(repr, self.records)) + ")"
        return f"#{self.id}={body}"


class Section:
    __slots__ = ("name", "schemas", "instances")

    def __init__(self, name=None, schemas=None):
        self.name, self.schemas, self.instances = name, list(schemas or []), {}


class File:
    """A whole exchange structure: header records, data sections, and notes on what was skipped."""

    def __init__(self, schemas=(), description=("",), name="", author=("",), organization=("",),
                 preprocessor="", system="", authorization="", time_stamp=None, level=None):
        self.header = []
        self.sections = [Section()]
        self.notes = []
        if schemas:
            self.set_header(schemas, description, name, author, organization, preprocessor, system,
                            authorization, time_stamp, level)

    # header helpers
    def set_header(self, schemas, description=("",), name="", author=("",), organization=("",),
                   preprocessor="", system="", authorization="", time_stamp=None, level=None):
        import datetime
        ts = time_stamp or datetime.datetime.now().replace(microsecond=0).isoformat()
        multi = len(self.sections) > 1
        self.header = [
            Record("FILE_DESCRIPTION", [list(description), level or ("3;1" if multi else "2;1")]),
            Record("FILE_NAME", [name, ts, list(author), list(organization), preprocessor, system,
                                 authorization]),
            Record("FILE_SCHEMA", [[s.upper() if "{" not in s else s for s in schemas]]),
        ]

    def header_record(self, name):
        for r in self.header:
            if r.name == name.upper():
                return r
        return None

    @property
    def schemas(self):
        r = self.header_record("FILE_SCHEMA")
        return list(r.params[0]) if r and r.params and isinstance(r.params[0], list) else []

    # instances across sections
    @property
    def instances(self):
        out = {}
        for s in self.sections:
            out.update(s.instances)
        return out

    def get(self, ref):
        for s in self.sections:
            i = s.instances.get(int(ref))
            if i is not None:
                return i
        return None

    def __getitem__(self, ref):
        i = self.get(ref)
        if i is None:
            raise KeyError(f"#{int(ref)}")
        return i

    def __iter__(self):
        for s in self.sections:
            yield from s.instances.values()

    def __len__(self):
        return sum(len(s.instances) for s in self.sections)

    def by_name(self, name):
        """Instances that have a record of this entity name (simple or as a partial value)."""
        name = name.upper()
        return [i for i in self if name in i.names]

    def next_id(self):
        return max((int(k) for s in self.sections for k in s.instances), default=0) + 1

    def add(self, name, *params, section=None):
        """Append a simple instance; returns its Ref."""
        return self.add_records([Record(name, params)], section)

    def add_complex(self, records, section=None):
        """Append a complex instance from (name, params) pairs; records are put in name order (10.2.5.3)."""
        recs = sorted((Record(n, p) for n, p in records), key=lambda r: r.name)
        return self.add_records(recs, section)

    def add_records(self, records, section=None, id=None):
        s = section or self.sections[-1]
        i = id or self.next_id()
        if self.get(i) is not None:
            raise P21Error(f"#{i} is defined twice")
        s.instances[i] = Instance(i, records, s)
        return Ref(i)

    def references(self, inst):
        """Every Ref in an instance's parameters, in order."""
        out = []
        for r in inst.records:
            _refs(r.params, out)
        return out

    def dangling(self):
        """(instance id, missing ref) for references to instances that are not in the file."""
        have = self.instances
        return [(i.id, r) for i in self for r in self.references(i) if int(r) not in have]


def _refs(v, out):
    if isinstance(v, Ref):
        out.append(v)
    elif isinstance(v, list):
        for x in v:
            _refs(x, out)
    elif isinstance(v, Typed):
        _refs(v.value, out)


# ------------------------------------------------------------------ strings (6.3.3)
_HEX = "0123456789ABCDEF"


def decode_string(s):
    """Effective contents of a string as stored (without its apostrophes)."""
    out, i, n, page = [], 0, len(s), "A"
    while i < n:
        c = s[i]
        if c == "'":
            if s[i + 1:i + 2] != "'":
                raise P21Error("lone apostrophe in string")
            out.append("'")
            i += 2
            continue
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if s.startswith("\\\\", i):
            out.append("\\")
            i += 2
        elif s.startswith("\\N\\", i) or s.startswith("\\F\\", i):
            i += 3                                   # print control: not part of the contents (11)
        elif s.startswith("\\S\\", i):
            ch = s[i + 3]
            out.append(bytes([ord(ch) + 128]).decode(f"iso8859-{_PAGES.index(page) + 1}", "replace"))
            i += 4
        elif re.match(r"\\P[A-I]\\", s[i:i + 4]):
            page = s[i + 2]
            i += 4
        elif s.startswith("\\X2\\", i) or s.startswith("\\X4\\", i):
            width = 4 if s[i + 2] == "2" else 8
            j = s.find("\\X0\\", i + 4)
            if j < 0:
                raise P21Error("\\X2\\ or \\X4\\ without \\X0\\")
            hexes = s[i + 4:j]
            if len(hexes) % width:
                raise P21Error("\\X2\\ or \\X4\\ with a partial character")
            for k in range(0, len(hexes), width):
                cp = int(hexes[k:k + width], 16)
                if width == 4 and 0xD800 <= cp <= 0xDBFF and k + 8 <= len(hexes):
                    lo = int(hexes[k + 4:k + 8], 16)          # tolerate UTF-16 surrogate pairs
                    if 0xDC00 <= lo <= 0xDFFF:
                        continue
                if width == 4 and 0xDC00 <= cp <= 0xDFFF and k >= 4:
                    hi = int(hexes[k - 4:k], 16)
                    cp = 0x10000 + ((hi - 0xD800) << 10) + (cp - 0xDC00)
                out.append(chr(cp))
            i = j + 4
        elif s.startswith("\\X\\", i):
            out.append(chr(int(s[i + 3:i + 5], 16)))
            i += 5
        else:
            raise P21Error(f"unknown control directive {s[i:i + 4]!r} in string")
    return "".join(out)


_PAGES = "ABCDEFGHI"


def encode_string(text):
    """A string as stored, with its apostrophes; characters outside the basic alphabet as \\X2\\/\\X4\\."""
    out, wide = ["'"], []

    def flush():
        if wide:
            if any(ord(c) > 0xFFFF for c in wide):
                out.append("\\X4\\" + "".join(f"{ord(c):08X}" for c in wide) + "\\X0\\")
            else:
                out.append("\\X2\\" + "".join(f"{ord(c):04X}" for c in wide) + "\\X0\\")
            wide.clear()

    for c in text:
        o = ord(c)
        if 32 <= o <= 126:
            flush()
            out.append("''" if c == "'" else "\\\\" if c == "\\" else c)
        else:
            if wide and (ord(wide[-1]) > 0xFFFF) != (o > 0xFFFF):
                flush()
            wide.append(c)
    flush()
    out.append("'")
    return "".join(out)


def format_real(x):
    """A REAL as Part 21 wants it: a full stop in the mantissa, capital E (6.3.2)."""
    x = float(x)
    if x != x or x in (float("inf"), float("-inf")):
        raise P21Error(f"{x} cannot be written as a REAL")
    if x == int(x) and abs(x) < 1e15:
        return f"{int(x)}."
    s = repr(x)
    if "e" in s:
        m, e = s.split("e")
        if "." not in m:
            m += "."
        return f"{m}E{int(e):+03d}"
    return s


def encode_binary(bits):
    n = (-len(bits)) % 4
    padded = "0" * n + bits
    return '"' + str(n) + "".join(_HEX[int(padded[k:k + 4], 2)] for k in range(0, len(padded), 4)) + '"'


def decode_binary(text):
    n = int(text[0])
    bits = "".join(f"{int(c, 16):04b}" for c in text[1:])
    return Binary(bits[n:])


# ------------------------------------------------------------------ reading
_TOKEN = re.compile(r"""
    (?P<sp>[ \t\f]+|\\[NF]\\)
  | (?P<comment>/\*.*?\*/)
  | (?P<string>'(?:[^'\\]|''|\\.)*?')(?!')
  | (?P<ref>\#[0-9]+)
  | (?P<enum>\.[A-Z_][A-Z0-9_]*\.)
  | (?P<real>[+-]?[0-9]+\.[0-9]*(?:E[+-]?[0-9]+)?)
  | (?P<int>[+-]?[0-9]+)
  | (?P<binary>"[0-3][0-9A-F]*")
  | (?P<kw>!?[A-Z_][A-Z0-9_]*(?:-[A-Z0-9_]+)*)
  | (?P<punct>[()=;,$*])
  | (?P<uri><[^>]*>|@[A-Za-z0-9_]+)
""", re.X | re.S)


def _tokens(text):
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            raise P21Error("unexpected character", pos, text)
        kind = m.lastgroup
        if kind not in ("sp", "comment"):
            yield kind, m.group(kind), pos
        pos = m.end()


def _strip_lines(text):
    """Annex A: line delimiters are not part of the exchange structure; drop them everywhere."""
    return text.replace("\r", "").replace("\n", "")


class _Reader:
    def __init__(self, text):
        self.text = text
        self.toks = list(_tokens(text))
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("eof", "", len(self.text))

    def take(self, kind=None, value=None):
        t = self.peek()
        if (kind and t[0] != kind) or (value is not None and t[1] != value):
            raise P21Error(f"expected {value or kind}, found {t[1] or 'end of file'}", t[2], self.text)
        self.i += 1
        return t

    def at(self, kind, value=None):
        t = self.peek()
        return t[0] == kind and (value is None or t[1] == value)

    def special(self, word):
        """A special token such as HEADER; or END-ISO-10303-21; (keyword followed by a semicolon)."""
        self.take("kw", word)
        self.take("punct", ";")

    def param(self):
        kind, val, pos = self.take()
        if kind == "punct" and val == "$":
            return UNSET
        if kind == "punct" and val == "*":
            return DERIVED
        if kind == "punct" and val == "(":
            out = []
            if self.at("punct", ")"):
                self.take()
                return out
            while True:
                out.append(self.param())
                if self.at("punct", ","):
                    self.take()
                    continue
                self.take("punct", ")")
                return out
        if kind == "int":
            return int(val)
        if kind == "real":
            return float(val)
        if kind == "string":
            if len(val) > MAX_STRING:
                raise P21Error("string longer than 32769 bytes as stored", pos, self.text)
            return decode_string(val[1:-1])
        if kind == "ref":
            return Ref(int(val[1:]))
        if kind == "enum":
            return Enum(val[1:-1])
        if kind == "binary":
            return decode_binary(val[1:-1])
        if kind == "kw":
            self.take("punct", "(")
            v = self.param()
            self.take("punct", ")")
            return Typed(val, v)
        raise P21Error(f"unexpected {val!r}", pos, self.text)

    def params(self):
        self.take("punct", "(")
        out = []
        if self.at("punct", ")"):
            self.take()
            return out
        while True:
            out.append(self.param())
            if self.at("punct", ","):
                self.take()
                continue
            self.take("punct", ")")
            return out

    def record(self):
        name = self.take("kw")[1]
        return Record(name, self.params())


def loads(text):
    """Parse an exchange structure. Raises P21Error on anything Part 21 does not allow."""
    r = _Reader(_strip_lines(text))
    f = File()
    f.sections = []
    r.special(MAGIC[:-1])
    r.special("HEADER")
    while not r.at("kw", "ENDSEC"):
        rec = r.record()
        r.take("punct", ";")
        f.header.append(rec)
    r.special("ENDSEC")
    names = [h.name for h in f.header]
    if names[:3] != list(HEADER_ORDER):
        f.notes.append(f"header starts {names[:3]}, Part 21 requires {list(HEADER_ORDER)} (8.1)")
    seen = {}
    while not r.at("kw", END[:-1]):
        t = r.peek()
        if t[0] == "eof":
            raise P21Error("no END-ISO-10303-21;", t[2], r.text)
        word = t[1]
        if word in ("ANCHOR", "REFERENCE"):
            r.special(word)
            while not r.at("kw", "ENDSEC"):
                r.take()
            r.special("ENDSEC")
            f.notes.append(f"{word} section (ISO 10303-21 edition 3) skipped")
            continue
        r.take("kw", "DATA")
        sec = Section()
        if r.at("punct", "("):
            p = r.params()
            if len(p) != 2 or not isinstance(p[0], str) or not isinstance(p[1], list) or len(p[1]) != 1:
                raise P21Error("DATA parameters must be ('name', ('SCHEMA')) (9)", t[2], r.text)
            sec.name, sec.schemas = p[0], list(p[1])
        r.take("punct", ";")
        while not r.at("kw", "ENDSEC"):
            kind, val, pos = r.take("ref")
            iid = int(val[1:])
            if iid == 0:
                raise P21Error("#0 is not an instance name (6.3.4)", pos, r.text)
            r.take("punct", "=")
            if r.at("punct", "("):
                r.take()
                recs = []
                while not r.at("punct", ")"):
                    recs.append(r.record())
                r.take()
                if not recs:
                    raise P21Error("empty complex instance", pos, r.text)
            else:
                recs = [r.record()]
            r.take("punct", ";")
            if iid in seen:
                raise P21Error(f"#{iid} is defined twice (9.1)", pos, r.text)
            seen[iid] = True
            sec.instances[iid] = Instance(iid, recs, sec)
        r.special("ENDSEC")
        f.sections.append(sec)
    r.special(END[:-1])
    if r.peek()[0] != "eof":
        f.notes.append("text after END-ISO-10303-21; ignored (a SIGNATURE section, edition 3, is not checked)")
    if not f.sections:
        raise P21Error("no DATA section (9)")
    if len(f.sections) > 1 and any(s.name is None for s in f.sections):
        f.notes.append("several DATA sections, some without a name and schema (9)")
    return f


def read(path):
    with open(path, encoding="latin-1") as fh:            # the basic alphabet is a subset of ISO 8859-1
        return loads(fh.read())


# ------------------------------------------------------------------ writing
def format_value(v):
    if v is UNSET or v is None:
        return "$"
    if v is DERIVED:
        return "*"
    if isinstance(v, Ref):
        return f"#{int(v)}"
    if isinstance(v, Enum):
        return f".{v.upper()}."
    if isinstance(v, bool):
        return ".T." if v else ".F."
    if isinstance(v, Binary):
        return encode_binary(v)
    if isinstance(v, str):
        return encode_string(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return format_real(v)
    if isinstance(v, Typed):
        return f"{v.keyword}({format_value(v.value)})"
    if isinstance(v, (list, tuple)):
        return "(" + ",".join(format_value(x) for x in v) + ")"
    raise P21Error(f"cannot write {v!r} ({type(v).__name__})")


def format_record(r):
    return f"{r.name}({','.join(format_value(p) for p in r.params)})"


def format_instance(i):
    if i.complex:
        return f"#{i.id}=(" + "".join(format_record(r) for r in i.records) + ");"
    return f"#{i.id}={format_record(i.records[0])};"


def dumps(f):
    out = [MAGIC, "HEADER;"]
    out += [format_record(r) + ";" for r in f.header]
    out.append("ENDSEC;")
    for s in f.sections:
        if s.name is not None:
            out.append(f"DATA({encode_string(s.name)},({','.join(encode_string(x) for x in s.schemas)}));")
        else:
            out.append("DATA;")
        out += [format_instance(i) for i in s.instances.values()]
        out.append("ENDSEC;")
    out.append(END)
    return "\n".join(out) + "\n"


def write(f, path):
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(dumps(f))
