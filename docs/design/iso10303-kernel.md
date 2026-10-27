# ISO 10303 kernel: design note

Status: proposed with the M14 milestone (SPEC §12.3), on branch `claude/iso10303-kernel-72ab6c`. This note is the design record for the `iso10303` kernel: opening ISO 10303-21 exchange files in the studio, checking them against their EXPRESS schemas, editing their parameters, and writing the studio's own models back as parametric ISO 10303.

No text of any ISO standard is reproduced here or anywhere in the repository. Clauses are cited by number; productions and entities by name only.

## 1. Notation, grammar and tool

SPEC §2 (rows D12 and D13) took two lessons from the [CS.SE question on BNF alternatives](https://cs.stackexchange.com/questions/127499): notations, mathematical grammar classes and parser tools are different things, and a parser's grammar takes the form its parser technology dictates. FR-K1 then fixed Lark EBNF as the studio's grammar notation. ISO 10303 is the first case where a kernel implements a language that someone else has *specified* in a notation of its own, so the lesson has to be applied in its second half too. This section does that.

### 1.1 Three layers, kept apart

| Layer | What it is | For this kernel |
|---|---|---|
| Grammar *class* | A mathematical object: a set of rules generating a language. Context-free grammars are one class; Van Wijngaarden two-level grammars are a strictly larger one, so large that parsing with them is undecidable in general. | The *syntax* of the exchange structure (ISO 10303-21) is context-free, and its token level is regular. The *language of valid files* is not: which `#n` exist, how many attributes an entity has and which rules hold depend on the rest of the file and on an EXPRESS schema. A two-level grammar could in principle state some of that, at the price of a parser nobody can guarantee terminates. We do not go there (§1.3). |
| *Notation* | A way to write a grammar of some class down: BNF, ABNF, ISO 14977 EBNF, W3C EBNF, Wirth Syntax Notation (WSN). | ISO 10303-11 and ISO 10303-21 both write their syntax in WSN (§1.2). WSN is the reference notation for this kernel. |
| *Tool dialect* | A parser generator's own input language, with features only a tool needs (ANTLR's embedded actions, Lark's `->` aliases, `?` inlining, terminal priorities, `%ignore`). | Lark EBNF, as for every grammar kernel (FR-K1, [`grammar.py`](../../src/gcad/kernels/grammar.py)). The `iso10303` grammar is written in it on purpose: it is what the studio loads at run time with no generation step, and the aliases are what [`rewrite.toml`](../../src/gcad/kernels/iso10303/rewrite.toml) keys on. |

So "the Part 21 grammar" means two different things, and this note keeps them apart: the **reference** is the WSN in ISO 10303-21; the **implementation** is [`src/gcad/kernels/iso10303/grammar.lark`](../../src/gcad/kernels/iso10303/grammar.lark).

### 1.2 Specification versus parser

The second CS.SE answer distinguishes a grammar written to drive a parser, whose form the parser technology dictates, from a grammar written in a specification, where the document defines its own notation. ISO 10303-21 and ISO 10303-11 are specifications, and each defines its notation:

| Standard (edition in the reference set) | Notation defined in | Productions in |
|---|---|---|
| ISO 10303-21:2002 (exchange structure, clear text encoding) | 5.1, and annex B (WSN defined in itself, Table B.1) | Table 1 (5.2, subsets of the basic alphabet), Table 2 (5.4, tokens), Table 3 (5.5, exchange structure), Table 4 (6.3.3, string control directives). Token separators are prose (5.6), print control directives are clause 11, and the handling of line breaks is annex A. |
| ISO 10303-11:1994 (EXPRESS) | 6.1 (syntax of the specification) and 6.2 (special character notation) | Annex A: A.1 tokens (A.1.1 keywords, A.1.2 character classes, A.1.3 lexical elements, A.1.4 remarks, A.1.5 interpreted identifiers), A.2 grammar rules, A.3 cross-reference listing. |

The kernel's Lark grammar is therefore an implementation of the Part 21 productions, never a replacement for them. Each Lark rule traces to a WSN production, and each place where the two differ is listed with its reason:

| WSN production (ISO 10303-21:2002) | Lark rule or terminal in `grammar.lark` | Deviation and reason |
|---|---|---|
| `EXCHANGE_FILE` (Table 3) | `exchange_file`, `data` | The repetition of data sections is a rule of its own, `data`, so the rewrite can splice every section's instances into one binding list. Same language. |
| `HEADER_SECTION`, `HEADER_ENTITY_LIST` | `header_section` | The optional list is written as `header_entity*` after the three mandatory entities. Same language. That the three are `FILE_DESCRIPTION`, `FILE_NAME` and `FILE_SCHEMA`, in that order, is clause 8, checked by the semantic layer. |
| `HEADER_ENTITY`, `SIMPLE_RECORD` | `header_entity`, `simple_record` | None. |
| `PARAMETER_LIST`, `PARAMETER` | `parameter_list`, `?parameter` | `?` inlines the choice, so the tree holds the chosen parameter directly. Same language. |
| `TYPED_PARAMETER` | `typed_parameter` | None. |
| `UNTYPED_PARAMETER` | `?untyped_parameter` with aliases `unset` (`$`) | Each alternative becomes its own tree or token, so each gets its own rewrite. Same language. |
| `OMITTED_PARAMETER` | `omitted` | Alias only. |
| `LIST` | `list` | None. |
| `DATA_SECTION` | `data_section`, `section_parameters`, `instances` | The production as printed in Table 3 ends without its full stop, which WSN requires (Table B.1); read as the obvious intent. See §1.6. |
| `ENTITY_INSTANCE_LIST`, `ENTITY_INSTANCE` | `instances`, `?entity_instance` | Same language. |
| `SIMPLE_ENTITY_INSTANCE`, `COMPLEX_ENTITY_INSTANCE` | `simple_instance`, `complex_instance` | Shorter names. |
| `SUBSUPER_RECORD`, `SIMPLE_RECORD_LIST` | `subsuper_record` | `simple_record+`. Same language. |
| `KEYWORD`, `USER_DEFINED_KEYWORD`, `STANDARD_KEYWORD` (Table 2) | terminal `KEYWORD` | Lexical rules become one regular terminal. Case-sensitive, as Table 1 defines `UPPER` (EXPRESS identifiers are case-insensitive; Part 21 writes them in capitals, 10.2). |
| `SIGN`, `INTEGER`, `REAL` | terminals `INTEGER`, `REAL` | `REAL` has a higher terminal priority, so `12.` is never read as `12` followed by a stray full stop. |
| `STRING`, `NON_Q_CHAR`, `CONTROL_DIRECTIVE` and Table 4 | terminal `STRING` | One regular terminal covering the control directives of Table 4 and the print control directives of clause 11; decoded by the `iso10303-21` string decoder (§1.4). Accepts line breaks inside the string (annex A). |
| `ENTITY_INSTANCE_NAME` | terminal `INSTANCE` | Shorter name. |
| `ENUMERATION`, `HEX`, `BINARY` | terminals `ENUMERATION`, `BINARY` | `BINARY` accepts line breaks inside (annex A). |
| Table 1 (`SPACE`, `DIGIT`, `LOWER`, `UPPER`, `SPECIAL`, ...) | character classes inside the terminals | Not separate rules: Lark terminals are regular expressions. Characters outside the basic alphabet (5.2) are accepted inside strings, as most writers emit them, and reported by the semantic layer as a warning. |
| 5.6 token separators, clause 11 print control directives, annex A line breaks | `%ignore` of white space, `/* */` comments, `\N\` and `\F\` | A line break *inside* a token other than a string or a binary is not accepted: annex A allows it, but no writer we know of does it and a Lark terminal cannot skip characters. It is a syntax error with a precise position. |

The same reasoning, one level up, applies to EXPRESS. Its WSN is ambiguous by design where only meaning can decide: annex A.1.5 makes `entity_ref`, `type_ref`, `function_ref` and several others the same `simple_id`, and which one a name is depends on the schema's symbol table. The EXPRESS reader we reuse ([`gcad/iso10303/express.py`](../../src/gcad/iso10303/express.py), from freecad-nxstyle) is a hand-written recursive-descent parser that resolves those names against its dictionary; it does not pretend a context-free grammar can.

### 1.3 What a context-free grammar cannot hold, and where it lives instead

| Constraint | Source | Where it is enforced |
|---|---|---|
| `#n` is defined exactly once across all data sections | 10303-21 clauses 9 and 10 | Engine: `letrec` rejects a second binding of the same name with both spans (§3.1). The semantic layer reports it too, against the instance list. |
| `#12` may be used before `#12=` | clause 9 (forward references allowed) | Engine: `letrec` bindings are order-free. |
| A referenced `#n` exists | clause 10 | Engine: a dangling name fails with its span; semantic layer: `express.check` reports the reference. |
| No reference cycles in what we evaluate | not a Part 21 rule; a limit of evaluating values | Engine: `letrec` detects the cycle and names it (`#3 -> #5 -> #3`). Part 21 does not forbid cyclic instance graphs; GCAD cannot build a value from one, and says so. |
| The header has `FILE_DESCRIPTION`, `FILE_NAME`, `FILE_SCHEMA` in that order | clause 8 | Semantic layer: `express.check_header`. |
| An instance's keyword names an entity of the schema, with the right number and types of attributes, `$` only where OPTIONAL, `*` only where derived | clauses 10.2 and 10.3; ISO 10303-11 | Semantic layer: `express.check` against the schema the header names. |
| WHERE rules, UNIQUE rules, global rules, INVERSE cardinalities | ISO 10303-11 9.2.2, 9.2.1.3, 9.6 | Semantic layer: `express_eval.rules`, which runs the schemas' own functions. |
| A string is at most 32769 bytes as stored | 10303-21 6.3.3 | Semantic layer (the reader). |

None of these is squeezed into the grammar. The grammar decides only whether a file is a well-formed exchange structure; everything that needs another part of the file or a schema runs after parsing, on the parse result, and reports with the span of the instance concerned.

### 1.4 FR-K8 and the WSN: derive or hand-write?

FR-K8 (Could) imports ISO 14977 and W3C EBNF grammars by converting them to Lark. A WSN importer belongs there: WSN is close to ISO 14977 (`=`, `|`, `[ ]`, `{ }`, `( )`, quoted terminals, a full stop ending each production).

Decision: **the Part 21 grammar is hand-written, and the WSN is used to verify it, on the user's own copy of the standard.**

- Deriving the grammar mechanically from the WSN at build time would need the WSN in the repository, which the disclosure rule forbids (the productions are the standard's text). Deriving it at run time from the user's PDF would make the kernel depend on a file most users do not have.
- The mechanical derivation would still need every deviation of §1.2 applied by hand: terminals for lexical rules, the line-break rule, LALR shape, aliases for the rewrite.
- So the committed grammar is written by hand with the traceability table above, and `gcad grammar import --notation wsn` (FR-K8, built in this milestone) converts WSN to Lark. `tests/test_iso10303_wsn.py` extracts Tables 2 to 4 from a copy of ISO 10303-21 when one is pointed to by `GCAD_ISO10303_21`, converts them, and checks both grammars accept and reject the same sentences: sentences generated from each grammar are parsed by the other, together with the kernel's fixtures and negative controls. Without the copy the test is skipped and says why; the copy never enters the repository.

### 1.5 Why this matters to users

A file opened in the studio is checked against a grammar. If that grammar's relation to the standard is not written down, "this file conforms to ISO 10303-21" is only an assertion about some parser. With the traceability table, the deviation list and the differential test against the WSN, a user can see which production every rule implements, what was deliberately done differently and why, and re-run the comparison on their own copy of the standard. The same holds one layer up: a file is "valid against AP242" only for the rules that were evaluated, and the semantic layer reports how many were evaluated, how many were violated and how many could not be decided (§3.3).

### 1.6 Errors found in the notation

Checked against the 14 errors in the standards already recorded with freecad-nxstyle (parametric-step page, "Errors in the standards themselves"); these two are new:

15. **10303-21:2002 Table 3, `DATA_SECTION`.** The production ends without the full stop that WSN requires after every production (annex B). A mechanical WSN reader must special-case it; read literally, `DATA_SECTION` and `ENTITY_INSTANCE_LIST` run together into one malformed production.
16. **10303-21:2002 Table 1, `CHARACTER`.** The same: no closing full stop. The WSN reader of §1.4 found it while reading the tables mechanically; the page confirms it. `gcad/kernels/wsn.py` closes such a production at the next `identifier =` and reports it, and the differential test expects exactly these two reports.

## 2. Architecture

```
 file.stp ─► grammar.lark (Lark LALR)  ─► rewrite.toml ─► GCore: (step-model [header] (letrec ((#n (record ...)) ...)))
     │                                                          │
     │                                            prelude.gcore: records -> geometry graph (CSG, history,
     │                                            parameters, expressions); explicit B-rep as one import node
     │                                                          ▼
     │                                              geometry graph ─► back-end M (mesh) / B (OCCT, exact)
     └─► semantic checker "iso10303-21" (kernel.toml `checker`, FR-K9):
           p21 reader ─► express.check (form) ─► express_eval.rules (WHERE, UNIQUE, global, INVERSE)
           against the AP long form the header names (schema cache), or the resource parts
           diagnostics with the span of each #n; origin "check": reported, never blocking the model

 graph ─► gcad/iso10303/writer.py ─► parametric STEP (Part 42 CSG, Part 55 history, Part 108 parameters,
           ISO 13584-20 expressions; AP242), read back by the kernel to the same graph hash (FR-G1)
```

The kernel is data: `kernel.toml`, `grammar.lark`, `rewrite.toml` (18 rules) and `prelude.gcore` (the interpreter of records, about 870 lines of GCore). It needs no `transform.py` and no trusted-only consent. What the engine lacked was added as generic features any kernel can use, documented in [`kernel-authoring.md`](../kernel-authoring.md):

| Feature | Where | Why this kernel needed it |
|---|---|---|
| `letrec`: order-free, once-evaluated bindings; a duplicate or a cycle is named; without a body, the list of all values | [`evaluator.py`](../../src/gcad/core/evaluator.py) | `#12` used before `#12=`; dangling, duplicate and cyclic references with spans |
| `record` values (lists that remember where they were written) and `(at record body)` | evaluator, [`builtins.py`](../../src/gcad/core/builtins.py) | geometry built by prelude code is attributed to the `#n` instance: click a face, the instance is selected |
| `resolve`, `apply`, `contains`, `split`, `upper`, `lower`, `starts_with`, `parse_number`, `parse_value`, `warn`, `info`, `fail` | builtins | dispatch on entity names, read authoring hints, report diagnostics |
| `import_source` and the `import` graph op; `Graph.blobs` | builtins, [`graph.py`](../../src/gcad/core/graph.py), both back-ends | explicit B-rep read by OpenCASCADE from the file itself; the graph keeps the file by its SHA-256 (M9's planned `import()` can share the op) |
| named string decoders (`string:iso10303-21`) | [`grammar.py`](../../src/gcad/kernels/grammar.py) | `''`, `\\`, `\X2\…\X0\` and the other control directives |
| kernel checkers (`checker = "iso10303-21"`) and diagnostics with `origin = "check"` | [`kernels/__init__.py`](../../src/gcad/kernels/__init__.py), [`pipeline.py`](../../src/gcad/pipeline.py) | the semantic layer (§1.3) without a kernel-specific `transform.py` |
| trace mode: `Traced` numbers | [`trace.py`](../../src/gcad/core/trace.py) | the writer turns top-level numeric definitions into Part 108 parameters and the arithmetic on them into ISO 13584-20 expressions |
| graph formats (`needs = "graph"`) | [`exporters/__init__.py`](../../src/gcad/exporters/__init__.py) | parametric STEP is written from the graph, with no back-end, so also in the browser |
| WSN to Lark (`gcad grammar import`) | [`wsn.py`](../../src/gcad/kernels/wsn.py) | FR-K8; checks the hand-written grammar against the standard's own productions (§1.4) |

Reuse of freecad-nxstyle (decided, not forked silently): `p21.py`, `express.py`, `express_eval.py`, `iso10303.py` and the dictionary tool are vendored unchanged in [`src/gcad/iso10303/_nxstyle`](../../src/gcad/iso10303/_nxstyle) (LGPL-2.1 relicensed to AGPL-3.0 under its section 3, attribution kept), so fixes can flow both ways; [`schemas.py`](../../src/gcad/iso10303/schemas.py) only points their cache at GCAD's. The parametric writer is GCAD's own, because nxstyle's `parametric.py` writes a FreeCAD-shaped model (sketches, pads, holes) rather than a CSG graph, but it copies nxstyle's instance layout for contexts, Part 55 sequences, Part 108 parameters and pcurve boundaries, which is known to pass every AP242 rule. `parametric.py`, `procedural_sketch.py` and the PMI modules are not vendored: the kernel reads their files through its own prelude.

## 3. Decisions

**(a) The instance graph becomes GCore through `letrec` and records.** Every instance is a binding `(#12 (record "#12" "KEYWORD" attribute...))`; the prelude's `step-model` receives all records and builds geometry from the representations. Rejected: one GCore function per entity called directly (natural to read, but references to instances no model uses would still be evaluated, unknown keywords would be fatal, and Part 108 overrides need every assignment before any geometry); a topological pre-pass in `kernel.toml` (less general than `letrec`, and still needs the records); `transform.py` (trusted-only, and the brief rules it out unless unavoidable).

**(b) Grammar and semantics meet at the record.** Lark decides the exchange structure (§1.2); everything that needs another instance or a schema is the semantic layer (§1.3), run by the engine after parsing. Its violations are errors with `origin = "check"`: the console shows them and `gcad eval` exits non-zero, but the model is still built, so an OpenCASCADE file with the wrong AP242 protocol name (error 14 of §6) still opens.

**(c) Explicit B-rep is read by OpenCASCADE, a file at a time.** The `import` op names the file's text by SHA-256; back-end B reads it with `STEPControl_Reader`, back-end M tessellates that and uses the mesh. Rejected: one import per solid entity, because OpenCASCADE 8 cannot transfer a single `MANIFOLD_SOLID_BREP` (`TransferEntity` refuses it, and asking the transient process for a result crashes). Consequence: an explicit solid inside a history (a Part 55 hybrid step) is refused with a diagnostic. In the browser there is no OpenCASCADE: B-rep files give a diagnostic that says so; CSG and procedural files work.

**(d) EXPRESS stays a validation layer.** A kernel's job is to produce geometry; an EXPRESS schema has none. Schema browsing and checking are `gcad iso schema` and `gcad iso check`. Rejected for now: an `express` highlighting kernel (its grammar is Part 11 annex A, about 330 productions, ambiguous by design, §1.2).

**(e) `.stp`, `.step` and `.p21` belong to the `iso10303` kernel.** What a file contains decides what is shown, not its extension: procedural (Part 55) first, then CSG, then explicit B-rep; the console says what was left out. The B-rep STEP export keeps `.step`; the parametric export is the format `step-parametric`, chosen by `-f`.

**Authoring hints.** Part 42 stores placements, not the operation that made them (`rotate [0 0 90]` and `matrix` give the same axes), and its booleans are binary. To read its own files back to the same graph (FR-G1), the writer names each item after its graph node (`gcad:["box" [["center" false] ["size" [40 30 10]]]]`); the reader uses a hint only when the geometry agrees with it within 1e-9, so a file edited elsewhere, or a parameter changed by a slider, reads by its geometry. Rejected: deriving the operation from geometry alone (Euler angles are not unique; `rotate [180 0 0]` and `rotate [0 180 180]` are one matrix), and changing the graph's canonical form (it would change every existing kernel's hashes).

**Interoperability.** A translation of an uncentred box, sphere or cylinder is written into the primitive's own placement, as other systems write it, rather than as a `SOLID_REPLICA`; freecad-nxstyle's FreeCAD importer reads such files (§7).

## 4. Mapping

| ISO 10303 entity | GCore built from it | Graph op | Back-ends |
|---|---|---|---|
| BLOCK, SPHERE, RIGHT_CIRCULAR_CYLINDER | `box`, `sphere`, `cylinder` placed by their axis placements | box, sphere, cylinder, translate/matrix | B exact, M mesh |
| RIGHT_CIRCULAR_CONE | `cylinder :r1 base :r2 top`, base below the location (Part 42 6.4.15) | cylinder | B, M |
| TORUS | `rotate_extrude` of a translated circle | rotate_extrude | B exact, M |
| RIGHT_ANGULAR_WEDGE | `linear_extrude` of the trapezium | linear_extrude | B, M |
| ELLIPSOID, ELLIPTIC_AREA | a sphere or circle of the first semi-axis, scaled | scale | B (spline surface), M |
| RECTANGULAR_PYRAMID, TETRAHEDRON | `polyhedron`, faces wound from the points | polyhedron | B, M |
| BOOLEAN_RESULT(_2D), CSG_SOLID(_2D), CSG_PRIMITIVE_SOLID_2D | `union`, `difference`, `intersection` (n-ary when the writer's hint says so) | union, difference, intersection | B, M |
| HALF_SPACE_SOLID, BOXED_HALF_SPACE | a 100 m block on the material side of the plane, cut by the box domain | box, intersection | B, M |
| SOLID_REPLICA + CARTESIAN_TRANSFORMATION_OPERATOR_3D | the transform (or the hinted `translate`, `rotate`, `scale`, `mirror`, `color`, `name`) | matrix etc. | B, M |
| EXTRUDED_AREA_SOLID, REVOLVED_AREA_SOLID with CURVE_BOUNDED_SURFACE of polylines, trimmed lines and whole circles (pcurves or not) | `linear_extrude`, `rotate_extrude` of a polygon, rect or circle less its holes | linear_extrude, rotate_extrude | B, M |
| CIRCULAR_AREA, RECTANGULAR_AREA, POLYGONAL_AREA | `circle`, `rect`, `polygon` | circle, rect, polygon | M (2D) |
| SOLID_WITH_FLAT_BOTTOM / STEPPED / CONICAL_BOTTOM_ROUND_HOLE, + SOLID_WITH_THROUGH_DEPRESSION, SOLID_WITH_CIRCULAR / RECTANGULAR_POCKET, SOLID_WITH_CIRCULAR_PROTRUSION (no blends or draft) | the base solid less (or plus) cylinders, a cone, a box | difference, union | B, M |
| PROCEDURAL_SHAPE_REPRESENTATION / PROCEDURAL_REPRESENTATION_SEQUENCE (Part 55) | the last element not suppressed; selections skipped | (that element's) | B, M |
| Part 108 parameters, FREE_FORM_ASSIGNMENT, EQUAL_PARAMETER_CONSTRAINT, INSTANCE_ATTRIBUTE_REFERENCE | the bound attribute takes the assignment's value; a slider on the parameter's value drives it | (attribute values) | all |
| ISO 13584-20 numeric expressions (+ − × /, DIV, MOD, power, sqrt, abs, exp, logarithms, trigonometry in radians, min, max) | GCore arithmetic | (values) | all |
| Part 112 CREATE_RECTANGLE, _POLYGON, _POLYLINE (closed), _CIRCLE_CENTRE_POINT, _2_POINTS, _3_POINTS | 2D shapes, combined even-odd; open curves reported, not shown | polygon, circle | M (2D) |
| STYLED_ITEM … COLOUR_RGB on a solid | `color` | color | B, M |
| MANIFOLD_SOLID_BREP and the rest of explicit B-rep | `import_source` (the whole file) | import | B; M through B's tessellation |

The full list, with the status and reason of every entity, is the coverage matrix (§5).

## 5. Coverage

[`iso10303-coverage.tsv`](iso10303-coverage.tsv) is generated by [`tools/iso10303_coverage.py`](../../tools/iso10303_coverage.py) from the compiled AP242 long form and the resource parts: 2480 entities, of which 141 supported, 357 delegated (explicit B-rep read by OpenCASCADE), 150 refused with a diagnostic and 1832 out of scope with a reason (product data, presentation, kinematics, tessellation, Part 50 maths, constraint solving, and PMI, planned). [`tests/test_iso10303_coverage.py`](../../tests/test_iso10303_coverage.py) fails if an entity of either schema has no row, if a row has no status or reason, or if a supported entity is in no fixture the kernel evaluates. The matrix carries only entity names.

723 out-of-scope entities share the generic reason "outside parametric shape exchange"; splitting that group further is follow-up work, not a gap in the check.

**PMI, GD&T and MBD (requested 2026-10-27, after this brief).** Viewing a STEP file's annotations is planned as M15 (SPEC §12.3): semantic PMI (DATUM, GEOMETRIC_TOLERANCE and its subtypes, DIMENSIONAL_SIZE/LOCATION with their tolerances) listed by the console and attached to the faces they apply to, and graphical PMI (ANNOTATION_CURVE_OCCURRENCE, TESSELLATED_ANNOTATION_OCCURRENCE, DRAUGHTING_CALLOUT) drawn in the viewer as polylines in their own presentation planes. freecad-nxstyle's `pmi.py`, `gdt.py` and `ap242.py` (pure Python) are the starting point; the 158 PMI entities carry that status in the matrix.

## 6. Errors found in the standards

Cross-checked against the 14 recorded with freecad-nxstyle. New in this work:

15. **ISO 10303-21:2002 Table 3, DATA_SECTION** lacks the full stop WSN requires (§1.6).
16. **ISO 10303-21:2002 Table 1, CHARACTER** lacks it too. Found by the mechanical WSN reader (§1.4), confirmed on the page.
17. **AP242 long form, VALID_CSG_2D_PRIMITIVES.** It takes a CSG_SOLID_2D and recurses into a boolean's operands, which `boolean_operand_2d` makes primitives or BOOLEAN_RESULT_2Ds, never CSG_SOLID_2Ds, so `csg_solid_2d` WR1 fails for every 2D CSG solid whose root is a boolean. [`check.py`](../../src/gcad/iso10303/check.py) reports that violation as a warning only when every leaf is a primitive the function accepts.

## 7. Verification (as of this branch)

- The 8 bundled examples that have an ISO 10303-42 form write as parametric STEP and read back to the same graph hash; 2 are refused with their reason (a scaled extrusion, a section). Each written file: AP242 long form, every rule evaluated, 0 errors, 0 not evaluated (warnings only for the documented contradictions); freecad-nxstyle's own checker gives the same counts.
- STEPcode `p21read` (built for the AP242 long form): 0 errors, 0 warnings on every written file without parameters and on the fixtures; it crashes on files with Part 108 parameters (complex instances of 15 partial records), and passes once those are written as simple instances in a copy. It does not read Part 112 (not in AP242).
- FreeCAD 1.1.4 with freecad-nxstyle's importer rebuilds the AC-2 file: 11497.345175425633 mm³ (12000 − 160π), and 11214.6018366 mm³ (12000 − 250π) after its spreadsheet's `hole_d` is set to 10. It does not yet read a rotated SOLID_REPLICA (the bracket), and refuses aliases with a hyphen: follow-ups in freecad-nxstyle.
- Exact volumes on back-end B match the analytic values to 1e-6 for the fixtures (`tests/test_iso10303.py`).

## 8. Risks and what remains

- A file of several hundred thousand instances is parsed by Lark and interpreted in GCore; the studio evaluates rules only up to 5000 instances (`gcad iso check` has no limit). Not yet measured on large production files.
- Assemblies: explicit B-rep assemblies are composed by OpenCASCADE; CSG or procedural assemblies (mapped items, representation relationships with transformations) are not composed by the prelude yet.
- Trigonometry is the platform's: Pyodide's and glibc's `sin 240°` differ in the last bit, which changed a sketch's graph hash between the browser and the native studio. The prelude takes sines and cosines in the first quadrant only and reduces exactly; GCore's own `sin` and `cos` are unchanged (that would change existing kernels' hashes).
- Part 21 edition 3 (anchor, reference and signature sections): out of scope, as the reference set has no copy of the 2016 edition to trace a grammar to.
- Hybrid output (parametric history plus explicit B-rep in one file, Part 55 5.4.1) is not written yet; the reader shows such files' history and reports the B-rep.
- The schemas are not in the repository. Committing a compiled dictionary (freecad-nxstyle does, for the resource parts) is Foad's decision; until then CI checks against the AP242 long form only when it fetches it, and the resource-part checks run locally.
