# SPDX-License-Identifier: LGPL-2.1-or-later
"""Check a STEP file against ISO 10303 (no FreeCAD needed).

    python -m nxstyle.iso10303 check part.stp [--exp more.exp] [--json]
    python -m nxstyle.iso10303 entities part.stp          # what is in it, by schema and part
    python -m nxstyle.iso10303 attributes ENTITY_NAME      # the Part 21 parameter list of an entity
    python -m nxstyle.iso10303 bom part.stp               # the product structure (assembly tree, quantities)
    python -m nxstyle.iso10303 schema fetch AP242          # get an application protocol's schema (once)
    python -m nxstyle.iso10303 schema add mim_lf.exp       # or add one from a file
    python -m nxstyle.iso10303 schema list

The dictionary (resources/iso10303/dictionary.json, built by tools/iso10303_dictionary.py) holds the
schemas of ISO 10303-42 (geometry, topology, geometric models), -43 (representation structures), -55
(procedural and hybrid representation), -108 with Technical Corrigenda 1 and 2 (parameters, constraints,
variational representations, sketches), -111 (procedural solid features) and -112 (2D sketch commands),
plus what they use from ISO 10303-41, ISO 10303-50 and ISO 13584-20 (supplement.exp).

Application protocols. identify() reads FILE_SCHEMA: AP203 (CONFIG_CONTROL_DESIGN, edition 1; the
..._MIM_LF schema, edition 2), AP214 (AUTOMOTIVE_DESIGN, with the version from its object identifier) or
AP242 (AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF). An AP's own entities (the product structure, the
B-rep shape representations, PMI, presentation) are checked when its long-form schema is in the schema
cache: `schema fetch` downloads the long form ISO publishes (AP242, standards.iso.org) or the one the
STEPcode project distributes (AP203 editions 1 and 2, AP214 edition 3), compiles it and keeps it in the
user's cache folder; `schema add` takes any other (another AP242 edition, for example). The schemas are
not part of this repository. Without one, those entities are counted and reported as not checked.

What each AP contains (from their long forms): AP242 has every entity of ISO 10303-108, the entities of
Parts 55 and 111 that NXStyle writes, and semantic PMI, but none of ISO 10303-112; AP203 edition 2 has
Part 111 and most of Part 55 but 3 of the 65 Part 108 entities; AP214 edition 3 has none of them.

bom() reads the product structure any of these APs carry (PRODUCT, PRODUCT_DEFINITION and
NEXT_ASSEMBLY_USAGE_OCCURRENCE): the assembly tree with quantities, and the parts list.

On top of express.check() (well-formedness against the schemas), express_eval evaluates every WHERE,
UNIQUE and global rule of the schemas the file is checked against, with their FUNCTIONs; a rule it cannot
decide is reported as not evaluated, with the reason, and the check ends with the count of rules
evaluated, violated and not evaluated. Where the standards contradict themselves, a violation that is the
contradiction and nothing else is a warning that says so (express_eval.KNOWN). rules() adds the informal
propositions of ISO 10303-108 that no WHERE rule states.
"""

import argparse
import json
import os
import sys

from . import express, express_eval, p21

DICTIONARY = os.path.join(os.path.dirname(__file__), "resources", "iso10303", "dictionary.json")
PART = {  # schema -> where it is defined
    "geometry_schema": "ISO 10303-42", "topology_schema": "ISO 10303-42",
    "geometric_model_schema": "ISO 10303-42", "representation_schema": "ISO 10303-43",
    "procedural_model_schema": "ISO 10303-55", "procedural_shape_model_schema": "ISO 10303-55",
    "parameterization_schema": "ISO 10303-108", "explicit_constraint_schema": "ISO 10303-108",
    "variational_representation_schema": "ISO 10303-108",
    "explicit_geometric_constraint_schema": "ISO 10303-108", "sketch_schema": "ISO 10303-108",
    "solid_shape_element_schema": "ISO 10303-111", "procedural_sketch_schema": "ISO 10303-112",
    "support_resource_schema": "ISO 10303-41", "measure_schema": "ISO 10303-41",
    "product_property_representation_schema": "ISO 10303-41",
    "mathematical_functions_schema": "ISO 10303-50",
    "iso13584_generic_expressions_schema": "ISO 13584-20", "iso13584_expressions_schema": "ISO 13584-20",
}
_DICT = None

STEPCODE = "https://raw.githubusercontent.com/stepcode/stepcode/develop/data/"
APS = {   # FILE_SCHEMA name -> (AP, title, edition when the name tells it)
    "CONFIG_CONTROL_DESIGN": ("AP203", "Configuration controlled 3D design of mechanical parts and assemblies",
                              "edition 1"),
    "AP203_CONFIGURATION_CONTROLLED_3D_DESIGN_OF_MECHANICAL_PARTS_AND_ASSEMBLIES_MIM_LF":
        ("AP203", "Configuration controlled 3D design of mechanical parts and assemblies", "edition 2"),
    "AUTOMOTIVE_DESIGN": ("AP214", "Core data for automotive mechanical design processes", None),
    "AUTOMOTIVE_DESIGN_CC2": ("AP214", "Core data for automotive mechanical design processes",
                              "conformance class 2 schema of edition 1"),
    "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF": ("AP242", "Managed model-based 3D engineering", None),
}
FETCH = {   # what `schema fetch` gets, and from where
    "AP203": STEPCODE + "ap203/ap203.exp",
    "AP203E2": STEPCODE + "ap203e2/ap203e2_mim_lf.exp",
    "AP214": STEPCODE + "ap214e3/AP214E3_2010.exp",
    "AP242": "https://standards.iso.org/iso/ts/10303/-442/ed-7/tech/express/mim_lf.exp",
}


def identify(f):
    """The schemas a file names: [{schema, oid, ap, title, edition}]."""
    out = []
    for raw in f.schemas:
        name, _, rest = raw.partition("{")
        name = name.strip().rstrip(".").upper()          # OpenCASCADE writes "..._MIM_LF. {...}"
        oid = [int(x) for x in rest.replace("}", " ").split() if x.isdigit()]
        ap, title, edition = APS.get(name, (None, None, None))
        if ap and edition is None and len(oid) >= 5 and oid[:3] == [1, 0, 10303]:
            edition = f"object identifier version {oid[4]}"
        out.append({"schema": name, "oid": oid, "ap": ap, "title": title, "edition": edition})
    return out


def schema_dir():
    """Where compiled application-protocol schemas are kept (NXSTYLE_ISO10303_SCHEMAS overrides)."""
    if os.environ.get("NXSTYLE_ISO10303_SCHEMAS"):
        return os.environ["NXSTYLE_ISO10303_SCHEMAS"]
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FreeCAD-NXStyle")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches/FreeCAD-NXStyle")
    else:
        base = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "freecad-nxstyle")
    return os.path.join(base, "iso10303")


def add_schema(text, source=""):
    """Compile long-form EXPRESS into the schema cache; returns [(schema name, entities, replaced source)].
    The cache holds one schema per name: the editions of AP242 share theirs, so the last one added is the
    one files are checked against."""
    made = []
    os.makedirs(schema_dir(), exist_ok=True)
    for s in express.parse(text):
        d = express.Dictionary.from_schemas([s])
        d.sources = [f"{s['name'].upper()} from {source}" if source else s["name"].upper()]
        path = os.path.join(schema_dir(), s["name"].upper() + ".json")
        old = express.Dictionary.load(path).sources if os.path.isfile(path) else None
        d.dump(path)
        _AP_DICTS.pop(path, None)
        made.append((s["name"].upper(), len(d.entities), old))
    return made


def fetch_schema(ap):
    """Download an AP's long form (see FETCH) and add it to the cache."""
    import urllib.request
    url = FETCH[ap.upper()]
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8", errors="replace")
    return add_schema(text, url)


def cached_schemas():
    d = schema_dir()
    return sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []


_AP_DICTS = {}


def dictionary_for(f, extra=()):
    """The dictionary to check a file with: its AP's long form when that is cached (with any extra
    EXPRESS), else the bundled one. Returns (dictionary, note or None)."""
    for ident in identify(f):
        path = os.path.join(schema_dir(), ident["schema"] + ".json")
        if os.path.isfile(path):
            if path not in _AP_DICTS:
                _AP_DICTS[path] = express.Dictionary.load(path)
            d = _AP_DICTS[path]
            if extra:
                d = d.copy()
                for p in extra:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        d.merge(express.Dictionary.from_text(fh.read()))
            return d, f"checked against the {ident['ap'] or ident['schema']} long form in the schema cache"
    aps = [i["ap"] for i in identify(f) if i["ap"]]
    note = None
    if aps:
        key = aps[0] + ("E2" if any(i["edition"] == "edition 2" for i in identify(f)) else "")
        note = (f"{aps[0]}'s own entities are not checked: its schema is not in the cache "
                f"(python -m nxstyle.iso10303 schema fetch {key if key in FETCH else aps[0]})")
    return dictionary(extra), note


def dictionary(extra=()):
    """The bundled dictionary, plus any EXPRESS files given."""
    global _DICT
    if _DICT is None:
        _DICT = express.Dictionary.load(DICTIONARY)
    d = _DICT
    if extra:
        d = d.copy()
        for path in extra:
            with open(path, encoding="utf-8", errors="replace") as f:
                d.merge(express.Dictionary.from_text(f.read()))
    return d


# ------------------------------------------------------------------ rules of Parts 55, 108, 111
class _Model:
    """Typed access to a parsed file: entity names (with supertypes) of each instance, attribute values
    by name, and the inverse references the rules need."""

    def __init__(self, f, d):
        self.f, self.d = f, d
        self._types, self._users = {}, None

    def types(self, i):
        inst = i if isinstance(i, p21.Instance) else self.f.get(i)
        if inst is None:
            return set()
        if inst.id not in self._types:
            out = set()
            for r in inst.records:
                n = self.d.entity_name(r.name)
                if n:
                    out |= self.d.supertypes(n)
            self._types[inst.id] = out
        return self._types[inst.id]

    def isa(self, i, name):
        return name in self.types(i)

    def get(self, i, attr):
        """Value of an explicit attribute (by name, or owner.name) of an instance, simple or complex."""
        inst = i if isinstance(i, p21.Instance) else self.f.get(i)
        if inst is None:
            return None
        owner, _, name = attr.rpartition(".")
        if not inst.complex:
            n = self.d.entity_name(inst.records[0].name)
            if not n:
                return None
            for a, v in zip(self.d.attributes(n), inst.records[0].params):
                if a["name"] == name and (not owner or a["owner"] == owner):
                    return v
            return None
        within = self.d.complex_closure({self.d.entity_name(r.name) for r in inst.records} - {None})
        for r in inst.records:
            n = self.d.entity_name(r.name)
            if not n:
                continue
            for a, v in zip(self.d.attributes(n, within=within), r.params):
                if a["name"] == name and (not owner or a["owner"] == owner):
                    return v
        return None

    def users(self, ref):
        """Instances that reference ref directly."""
        if self._users is None:
            self._users = {}
            for inst in self.f:
                for r in self.f.references(inst):
                    self._users.setdefault(int(r), []).append(inst)
        return self._users.get(int(ref), [])

    def all_of(self, name):
        return [i for i in self.f if self.isa(i, name)]


def rules(f, d=None):
    """What express_eval cannot evaluate: the informal propositions of ISO 10303-108 that decide whether
    parametric data means anything (the attribute an instance_attribute_reference names exists, holds a
    number and belongs to the owning instance; every variational item is in a variational representation)."""
    d = d or dictionary()
    bd = dictionary()                               # the resource schemas, for schema names
    m = _Model(f, d)
    out = []

    def bad(inst, msg):
        out.append(express.Issue("error", inst.id, msg))

    # ISO 10303-108 4.4.6 informal proposition: the attribute an instance_attribute_reference names
    for r in m.all_of("instance_attribute_reference"):
        name, owner = m.get(r, "attribute_name"), m.get(r, "owning_instance")
        parts = name.split(".") if isinstance(name, str) else []
        if len(parts) < 3:
            bad(r, f"instance_attribute_reference: attribute_name {name!r} is not SCHEMA.ENTITY.ATTRIBUTE "
                   "(ISO 10303-108 4.4.6 informal proposition, annex F.1)")
            continue
        schema, ent, attr = parts[0].lower(), parts[1].lower(), parts[2].lower()
        home = bd.entities.get(ent, d.entities.get(ent, {})).get("schema")
        if ent not in d.entities:
            bad(r, f"instance_attribute_reference: {parts[1]} is not an entity of the loaded schemas")
        elif home != schema and not (home or "").endswith("_mim_lf"):
            bad(r, f"instance_attribute_reference: {parts[1]} is in {(home or '?').upper()}, not {parts[0]}")
        elif not any(a["name"] == attr for a in d.attributes(ent)):
            bad(r, f"instance_attribute_reference: {parts[1]} has no attribute {parts[2]}")
        elif owner is not None and m.types(owner) and not m.isa(owner, ent):
            bad(r, f"instance_attribute_reference: owning_instance #{int(owner)} is not a {parts[1]}")
        elif len(parts) == 3 and owner is not None and m.types(owner):
            v = m.get(owner, f"{ent}.{attr}") if any(a["owner"] == ent for a in d.attributes(ent)
                                                     if a["name"] == attr) else m.get(owner, attr)
            v = v.value if isinstance(v, p21.Typed) else v
            if v is not None and not isinstance(v, (int, float)) and v is not p21.DERIVED:
                out.append(express.Issue("warning", r.id, f"{name} of #{int(owner)} is not a number"))

    # ISO 10303-108 6.3.4 WR4 as its prose states it: "the type list of the variational representation shall
    # be identical to that of the related current result except that it shall also include variational
    # representation". The EXPRESS is a one-way difference, which a sketch's wrapper meets; the prose it
    # cannot, since a NEUTRAL_SKETCH_REPRESENTATION wrapper could hold no constraints (8.4.9).
    for rel in m.all_of("variational_current_representation_relationship"):
        r1, r2 = m.get(rel, "rep_1"), m.get(rel, "rep_2")
        if m.types(r1) and m.types(r2) - m.types(r1):
            out.append(express.Issue("warning", rel.id, "variational_current_representation_relationship WR4 as "
                       f"stated in prose not met: rep_1 lacks {', '.join(sorted(m.types(r2) - m.types(r1)))}"
                       + ("; a NEUTRAL_SKETCH_REPRESENTATION wrapper could hold no constraints (8.4.9), so the "
                          "prose and 8.4.9 conflict" if m.isa(r2, "neutral_sketch_representation") else "")))
    # ISO 10303-108 6.3.1: a variational item belongs to a variational representation (a note, not a rule)
    holders = set()
    for rep in m.all_of("variational_representation"):
        holders |= {int(x) for x in m.get(rep, "items") or []}
    for it in m.all_of("variational_representation_item"):
        if it.id not in holders:
            out.append(express.Issue("warning", it.id, "variational item not listed in the items of any "
                                                       "variational_representation (6.3.1)"))
    return out


def check(path_or_file, extra=()):
    """All issues: Part 21 header, schema conformance (against the file's AP when its schema is cached),
    and the rules above."""
    f = p21.read(path_or_file) if isinstance(path_or_file, str) else path_or_file
    d, note = dictionary_for(f, extra)
    issues = [express.Issue("info", None, n) for n in f.notes]
    if note:
        issues.append(express.Issue("info", None, note))
    issues += express.check(f, d)
    rd = d
    if not d.has_rules:
        rd = dictionary()
        issues.append(express.Issue("info", None, "the cached schema was compiled before rules were kept; the "
                                                  "rules of the resource parts are evaluated (fetch or add the "
                                                  "schema again for the AP's own rules)"))
    res = express_eval.rules(f, rd, part=lambda sc: PART.get(sc, (sc or "?").upper()))
    issues += res.issues
    issues += rules(f, d)
    issues.append(express.Issue("info", None, f"rules: {res.evaluated} evaluated, {res.violated} violated, "
                                              f"{sum(c for c, _ in res.unevaluated.values())} not evaluated"))
    return issues


def inventory(f, d=None):
    """{standard: {entity: count}} for the instances of a file: the part of ISO 10303 that defines each
    entity, or the file's AP for an AP's own entities when its schema is cached."""
    d = d or dictionary()
    ap, _ = dictionary_for(f)
    ap_name = next((i["ap"] for i in identify(f) if i["ap"]), "AP")
    out = {}
    for inst in f:
        for r in inst.records:
            n = d.entity_name(r.name)
            if n:
                std = PART.get(d.schema_of(n), "other")
            elif ap is not d and ap.entity_name(r.name):
                n, std = ap.entity_name(r.name), f"{ap_name} (application protocol)"
            else:
                std = "not in the loaded schemas"
            out.setdefault(std, {})
            key = n or r.name.lower()
            out[std][key] = out[std].get(key, 0) + 1
    return out


def bom(f):
    """The product structure: {"roots": [tree], "parts": [{id, name, quantity}]}. A tree node is
    {id, name, quantity, children}; quantity counts the NEXT_ASSEMBLY_USAGE_OCCURRENCEs (or the
    quantity of a QUANTIFIED_ASSEMBLY_COMPONENT_USAGE) of a component in its parent."""
    def rec(inst, name):
        return inst.record(name) if inst.complex else (inst.records[0] if inst.records[0].name == name else None)

    def first(inst, names):
        for n in names:
            r = rec(inst, n)
            if r is not None:
                return r
        return None

    products, formations, defs = {}, {}, {}
    for inst in f:
        r = rec(inst, "PRODUCT")
        if r is not None:
            products[inst.id] = (r.params[0], r.params[1])
        r = first(inst, ("PRODUCT_DEFINITION_FORMATION", "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE"))
        if r is not None:
            formations[inst.id] = int(r.params[2])
        r = first(inst, ("PRODUCT_DEFINITION", "PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS"))
        if r is not None:
            defs[inst.id] = int(r.params[2])
    links = {}
    for inst in f:
        r = first(inst, ("NEXT_ASSEMBLY_USAGE_OCCURRENCE", "ASSEMBLY_COMPONENT_USAGE",
                         "QUANTIFIED_ASSEMBLY_COMPONENT_USAGE"))
        if r is None or len(r.params) < 5:
            continue
        parent, child = int(r.params[3]), int(r.params[4])
        q = 1
        qr = rec(inst, "QUANTIFIED_ASSEMBLY_COMPONENT_USAGE")
        if qr is not None and qr.params and isinstance(qr.params[-1], p21.Ref):
            m = f.get(qr.params[-1])
            if m is not None and m.records[0].params and isinstance(m.records[0].params[0], p21.Typed):
                q = m.records[0].params[0].value
        links.setdefault(parent, {}).setdefault(child, 0)
        links[parent][child] += q

    def ident(pd):
        prod = products.get(formations.get(defs.get(pd)), ("?", "?"))
        return {"id": prod[0], "name": prod[1]}

    totals = {}

    def node(pd, qty, mult, path):
        n = dict(ident(pd), quantity=qty, children=[])
        kids = links.get(pd, {})
        if not kids:
            key = (n["id"], n["name"])
            totals[key] = totals.get(key, 0) + mult
        for child, q in kids.items():
            if child not in path:
                n["children"].append(node(child, q, mult * q, path | {child}))
        return n

    children = {c for k in links.values() for c in k}
    roots = [node(pd, 1, 1, {pd}) for pd in defs if pd not in children]
    parts = [{"id": k[0], "name": k[1], "quantity": v} for k, v in sorted(totals.items())]
    return {"roots": roots, "parts": parts}


def bom_text(b):
    lines = []

    def walk(n, depth):
        lines.append(f"{'  ' * depth}{n['quantity']} x {n['id']}" + (f"  {n['name']}" if n['name'] != n['id'] else ""))
        for c in n["children"]:
            walk(c, depth + 1)

    for r in b["roots"]:
        walk(r, 0)
    if any(r["children"] for r in b["roots"]):
        lines += ["", "Parts list:"] + [f"  {p['quantity']} x {p['id']}" + (f"  {p['name']}" if p["name"] != p["id"]
                                                                        else "") for p in b["parts"]]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m nxstyle.iso10303", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a STEP file")
    c.add_argument("file")
    c.add_argument("--exp", action="append", default=[], help="more EXPRESS (e.g. an AP long form)")
    c.add_argument("--json", action="store_true")
    e = sub.add_parser("entities", help="what a STEP file contains, by standard")
    e.add_argument("file")
    a = sub.add_parser("attributes", help="the Part 21 parameter list of an entity")
    a.add_argument("entity")
    b = sub.add_parser("bom", help="the product structure of a STEP file")
    b.add_argument("file")
    sc = sub.add_parser("schema", help="application protocol schemas in the cache")
    sc.add_argument("action", choices=("list", "fetch", "add"))
    sc.add_argument("what", nargs="?", help="AP203, AP203E2, AP214 or AP242 (fetch); an .exp file (add)")
    args = ap.parse_args(argv)

    if args.cmd == "schema":
        if args.action == "list":
            print(f"{schema_dir()}:")
            for n in cached_schemas():
                src = express.Dictionary.load(os.path.join(schema_dir(), n + ".json")).sources
                print(f"  {n}  ({APS.get(n, ('?',))[0]}; {src[0] if src else ''})")
            print("fetch: " + ", ".join(f"{k} <{v}>" for k, v in FETCH.items()))
            return 0
        if not args.what:
            print("schema fetch|add needs an AP name or a file", file=sys.stderr)
            return 2
        if args.action == "fetch":
            if args.what.upper() not in FETCH:
                print(f"unknown AP {args.what}; known: {', '.join(FETCH)}", file=sys.stderr)
                return 2
            made = fetch_schema(args.what)
        else:
            with open(args.what, encoding="utf-8", errors="replace") as fh:
                made = add_schema(fh.read(), os.path.basename(args.what))
        for n, k, old in made:
            print(f"{n}: {k} entities -> {schema_dir()}" + (f" (replaces {old[0]})" if old else ""))
        return 0

    if args.cmd == "attributes":
        d = dictionary()
        n = d.entity_name(args.entity)
        if not n:
            print(f"{args.entity}: not in the dictionary", file=sys.stderr)
            return 2
        e = d.entities[n]
        print(f"{n.upper()} ({e['schema']}, {PART.get(e['schema'], '?')}){'  ABSTRACT' if e['abstract'] else ''}")
        print("  supertypes: " + " > ".join(reversed(d.mapping_order(n)[:-1])) if e["subtype_of"] else "")
        for i, at in enumerate(d.attributes(n), 1):
            flag = " *derived*" if at["derived"] else (" OPTIONAL" if at["optional"] else "")
            print(f"  {i:2}. {at['owner']}.{at['name']} : {d.type_text(at['type'])}{flag}")
        return 0
    try:
        f = p21.read(args.file)
    except p21.P21Error as err:
        print(f"{args.file}: not a valid ISO 10303-21 file: {err}", file=sys.stderr)
        return 2
    if args.cmd == "bom":
        print(bom_text(bom(f)) or "no product structure in the file")
        return 0
    if args.cmd == "entities":
        for i in identify(f):
            print(f"schema {i['schema']}" + (f": {i['ap']} {i['title']}, {i['edition'] or ''}" if i["ap"] else ""))
        for std, ents in sorted(inventory(f).items()):
            print(f"{std}: {sum(ents.values())}")
            for n, k in sorted(ents.items()):
                print(f"  {n.upper()}: {k}")
        return 0
    issues = check(f, args.exp)
    if args.json:
        print(json.dumps([{"level": i.level, "id": i.id, "message": i.message} for i in issues], indent=1))
    else:
        for i in issues:
            print(i)
        s = express.summary(issues)
        aps = ", ".join(f"{i['ap']} ({i['edition']})" for i in identify(f) if i["ap"]) or ", ".join(f.schemas)
        print(f"{args.file}: {aps}; {len(f)} instances; {s['error']} errors, {s['warning']} warnings, "
              f"{s['info']} notes")
    return 1 if any(i.level == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
