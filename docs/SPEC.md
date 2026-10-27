# GCAD Studio — Requirements and Specification (MVE v0.1)

Status: approved 2026-10-27 with the recommended §11 decisions (Python core + local browser UI, Lark EBNF, the §8.3 treatment, `minscad` as lead language). Milestones M0, M1 and M2 are implemented; see the README for what works today. Repository: `GCADstudio/studio` (public, AGPL-3.0).

## 1. Product in one paragraph

GCAD Studio is a language-agnostic, code-first CAD environment: what Jupyter did for IPython, GCAD Studio does for OpenSCAD. A **kernel** is a modelling language defined by a formal grammar plus declarative rewrite rules. The studio parses the source with that grammar, rewrites it into one shared functional core language, evaluates it to a neutral geometry graph, realizes the graph on an open-source geometry engine, shows it in a 3D viewer next to the editor, and exports it to neutral and vendor formats. Adding a language means adding a folder of text files, not writing a CAD program.

The MVE (minimum viable example) proves that loop end-to-end with two languages, two geometry engines, a CLI, a local browser UI and the export formats listed in §8.

## 2. What issue #2299 and the BNF question teach

Each design driver below comes from a specific comment in [openscad/openscad#2299](https://github.com/openscad/openscad/issues/2299) or from the [cs.stackexchange answers on BNF alternatives](https://cs.stackexchange.com/questions/127499).

| # | Source | Lesson | Requirement it drives |
|---|---|---|---|
| D1 | Foadsf (opening post) | Many CAD/mesh/2D/CAM languages share one GUI style; wrap existing libraries instead of writing from scratch. | Reuse open geometry engines (OCCT, Manifold); write no geometry kernel. |
| D2 | MichaelPFrey | Do not build on OpenSCAD's code; write a UI that "tabs into" other projects as compilers and renderers. | New code base; languages and engines are plug-ins behind interfaces. |
| D3 | kintel | The core/GUI split needs an adapter API, not direct coupling. | Explicit JSON-RPC protocol between UI, server and external kernels (§7). |
| D4 | mkeeter | Language Server Protocol turns editors × languages into editors + languages; "a proof-of-concept is worth a thousand ideas". | Protocol-first design; ship a working MVE before broadening. |
| D5 | mkeeter (why Guile) | Sandboxing unknown scripts; homoiconic AST enables rewriting (drag a shape, the variable changes); embeddability. | Grammar kernels are data, never executable code; the core language is an S-expression with source spans preserved for source↔geometry linking. |
| D6 | doug-moen | OpenSCAD's strength is its functional/declarative style. | The core language is pure functional (no mutation). |
| D7 | doug-moen | OpenSCAD and Curv cannot run fully inside a browser. | Computation runs in a native local process; the browser only edits and displays. |
| D8 | doug-moen (2020) | A plain 3-pane GUI is not enough "juice" for language authors to pay the integration cost. | The value is: language defined by grammar alone, universal export, live parameter sliders, later notebooks. |
| D9 | doug-moen / Book of Shaders, Bret Victor | Click a numeric literal, get a slider, watch the model update. | Live parameter sliders (Should, §6). |
| D10 | Foadsf (motivations) | Version control, robust parametrics, open hardware, object-oriented part libraries. | Plain-text sources, deterministic byte-stable exports, user-defined modules/functions in the core language. |
| D11 | Foadsf (Antimony) | Visual programming, then export to a chosen language. | Out of MVE; the core graph is designed so a node view can be added later. |
| D12 | CS.SE answer 1 | BNF/ABNF are notations; ANTLR is a tool with its own grammar dialect; notation and implementation are different things. | Pick one notation **tied to its parser implementation**, not an abstract notation. |
| D13 | CS.SE answer 2 | For a parser, the grammar's form is dictated by the parser technology. | Grammar notation = Lark EBNF, loaded at run time without code generation (§5.2). |

## 3. Scope

**In the MVE**

- Core language (GCore), evaluator and geometry graph (§5).
- Grammar-kernel engine: grammar + rewrite rules → GCore (§5.2).
- Two bundled kernels: `minscad` (an OpenSCAD subset) and `csg` (an S-expression language).
- Two geometry back-ends: exact B-rep (OpenCASCADE via OCP) and mesh (Manifold) (§5.4).
- Exporters for the formats marked MVE in §8.
- Headless CLI and a local browser studio with editor, viewer and console (§6).
- CI on Windows, macOS and Linux, x86_64 and arm64 (§9).

**Not in the MVE** (designed for, built later): notebook documents and a Jupyter kernel; process kernels for OpenSCAD, CadQuery, Curv and others; importing CAD files; CAM (G-code, HPGL, OpenSBP) and CAE solver decks; assemblies and PMI; fillets/chamfers/hull/Minkowski; the visual node editor; 3D PDF; DWG; ACIS; PHIGS (see §8.3).

## 4. Architecture

```
 Browser UI (editor | viewer | console)          gcad CLI (headless)
          │  JSON-RPC 2.0 over WebSocket              │ in-process
          ▼                                           ▼
 ┌───────────────────────── gcad server / library (Python) ─────────────────────────┐
 │ Kernel registry ─► Grammar kernel: Lark parse ─► rewrite rules ─┐                │
 │                └─► Process kernel (later): external exe, stdio ─┤                │
 │                                                                 ▼                │
 │                       GCore (S-expression) ─► Evaluator ─► Geometry graph (JSON) │
 │                                                                 │                │
 │            Back-end B: OCCT B-rep (OCP)  |  Back-end M: Manifold mesh            │
 │                       │ tessellate                     │ exact or mesh          │
 │            glTF preview to the viewer            Exporters (§8)                   │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

Stack decision (recommended, open for confirmation in §11): Python core with a local browser front-end.

- Every heavy dependency ships as a binary wheel for the five desktop targets, verified on PyPI on 2026-10-27: `cadquery-ocp` 8.0.1.1.0 (win_amd64, macOS x86_64/arm64, manylinux_2_28 x86_64/aarch64), `manifold3d` 3.5.4 (also win_arm64), `ezdxf` 1.4.5, `lark` 1.3.1 (pure Python). No compiler is needed to install.
- GPU and driver differences are absorbed by the browser's WebGL, which falls back to software rendering (SwiftShader, llvmpipe) on machines without a usable GPU.
- The 2018 thread leaned towards C++/Qt; that is still a valid route but costs a native build and packaging per platform before anything else exists. Doug Moen's objection to Electron (D7) does not apply here because no geometry runs in the browser.

## 5. Functional requirements: language and geometry

Priority uses MoSCoW: **M** = Must for the MVE, **S** = Should, **C** = Could.

### 5.1 GCore, the shared core language

| ID | Pri | Requirement |
|---|---|---|
| FR-C1 | M | GCore is an S-expression language with a canonical text form and a JSON form. Every node carries a source span `(kernel, file, start, end)` pointing back to the user's source. |
| FR-C2 | M | Values: number (IEEE-754 double), boolean, string, vector, list, `undef`, function, shape2d, shape3d. |
| FR-C3 | M | Special forms: `let`, `fn` (lambda with default arguments), `if`, `for` (list comprehension), `call` (positional and named arguments), `module` (named shape-producing function). No mutation. |
| FR-C4 | M | Built-ins: arithmetic, comparison, logic, `sin cos tan asin acos atan atan2` in degrees (OpenSCAD convention), `sqrt pow abs min max floor ceil round`, `len concat range`, `PI`. |
| FR-C5 | M | Geometry built-ins (§5.3) produce graph nodes; they never compute geometry during evaluation. |
| FR-C6 | M | Limits against runaway or hostile sources: recursion depth (default 1,000), loop iterations (10⁶), graph nodes (10⁵), wall time (10 s). Exceeding one returns a diagnostic, never a hang. |
| FR-C7 | M | Diagnostics (parse, rewrite, evaluation, back-end) carry severity, message and source span. |

### 5.2 Kernels: a language defined by a formal grammar

A kernel is a folder; no Python is required:

```
kernels/minscad/
  kernel.toml     id, display name, version, file extensions, comment syntax, start rule, parser (gcore|earley|lalr)
  grammar.lark    Lark EBNF grammar of the surface language
  rewrite.toml    [rules]: one GCore template per tree name; [tokens]: how terminals become GCore atoms
  prelude.gcore   optional GCore definitions evaluated in their own scope (e.g. OpenSCAD's cube -> box)
  examples/       sample sources, used as tests
```

Rewrite rules map each tree the parser can build (a rule name or an `-> alias`) to a GCore template. Placeholders refer to the tree's children, 1-based, after Lark drops anonymous punctuation: `$n` is child n rewritten, `$n*` splices child n's children, `$*` splices all children, `$n:str` / `$n:sym` / `$n:num` take child n's source text, `:$n` makes a keyword named by child n. An optional part that did not match is an absent child and produces nothing. A template may hold several forms (spliced into the parent) or none (the node disappears). As built in M2, from `minscad`:

```toml
# grammar.lark:  NAME "(" [arguments] ")" statement   -> instantiation
instantiation = "($1 $2* $3)"
# grammar.lark:  NAME "=" expr                         -> named_arg
named_arg = ":$1 $2"
# grammar.lark:  "[" expr ":" expr ":" expr "]"        -> range_step
range_step = "(range $1 $3 $2)"
```

| ID | Pri | Requirement |
|---|---|---|
| FR-K1 | M | Grammar notation is Lark EBNF, loaded at run time (Earley for any context-free grammar, LALR(1) when declared for speed). No parser generation step. |
| FR-K2 | M | Rewrite rules are declarative templates; a kernel that is only `kernel.toml`, `grammar.lark` and `rewrite.toml` works. |
| FR-K3 | M | Dropping a kernel folder into the kernel path (`./kernels`, user config dir, or `GCAD_KERNEL_PATH`) makes it available to the next CLI command and to the UI after a kernel reload. |
| FR-K4 | M | `gcad kernel check <dir>` validates the manifest, compiles the grammar, checks that every tree the grammar can produce has a rewrite rule (and warns about rules that match nothing), reads the prelude, and runs `examples/`. |
| FR-K5 | M | Syntax highlighting is derived from the grammar: word literals become keywords, other literals operators, ignored terminals comments, and named terminals take the classes in `rewrite.toml [tokens]`. `gcad kernel describe <id>` prints them for an editor. |
| FR-K6 | S | Optional `transform.py` escape hatch for semantics templates cannot express. Such kernels are marked **trusted-only** and the UI asks before loading one from an untrusted folder. |
| FR-K7 | S | Process kernels: an external program speaking the stdio protocol (§7.2) returns a geometry graph, a STEP file or a mesh. First target: OpenSCAD, using its `.csg` export parsed by the `minscad` grammar. |
| FR-K8 | C | Import of ISO 14977 EBNF and W3C EBNF grammars by conversion to Lark. |

Bundled kernels:

- **`minscad`** (M): OpenSCAD subset — the geometry built-ins of §5.3 with OpenSCAD names and argument conventions, variables, arithmetic, `for`, `if`, `module` and `function` definitions, `$fn/$fa/$fs` as tessellation hints. As built in M2 (`.mscad`, LALR grammar, 55 rewrite rules, a 5-form prelude): also `let`, ternaries, function literals, `children()`/`$children`, `v.x`, ranges, list comprehensions with one `for`, and the `* % # !` modifiers (`*` and `%` drop the object; `#` and `!` keep it). Not yet: `use`/`include` (reported as errors), `if` and `each` inside list comprehensions, `intersection_for`, `import`, `surface`, `text`, and OpenSCAD's scope rule that the last assignment to a name wins in the whole scope (minscad assignments are sequential). Every bundled example is a valid OpenSCAD file and is checked against OpenSCAD output.
- **`csg`** (M): an S-expression language close to GCore, showing that a homoiconic language needs almost no rewrite rules: three, as built in M2. File extension `.gcsg`, because OpenSCAD already uses `.csg` for its CSG-tree export (FR-K7).
- **`parts`** (added in M2 for AC-5): a declarative parts list, one solid per line, written only as `kernel.toml`, `grammar.lark` and `rewrite.toml`.

### 5.3 Geometry graph

The evaluator emits a JSON directed acyclic graph; identical sub-trees share one node. Each node has `id`, `op`, `args`, `children`, `span`, optional `name` and `color`.

| Group | Ops (MVE = M unless marked) |
|---|---|
| 3D primitives | `box(size, center)`, `sphere(r)`, `cylinder(h, r1, r2, center)`, `polyhedron(points, faces)` |
| 2D primitives | `rect(size, center)`, `circle(r)`, `polygon(points, paths)` |
| Transforms | `translate`, `rotate` (Euler x→y→z in degrees, OpenSCAD order), `scale`, `mirror`, `matrix` (4×4) |
| Booleans | `union`, `difference`, `intersection` (n-ary, 2D and 3D) |
| 2D→3D | `linear_extrude(height, center)`, `rotate_extrude(angle)`; `twist`/`scale` on extrude is S |
| 3D→2D | `section(plane)` M; `projection(view, hidden_lines)` S |
| Attributes | `color(rgba)`, `name`; `material` S |
| Document | `units` (default mm; also cm, m, in), metadata (title, author, revision) |

FR-G1 (M): the canonical graph JSON (sorted keys, normalized numbers) has a stable SHA-256; the same model written in two kernels must yield the same hash once spans are stripped.

### 5.4 Geometry back-ends

| ID | Pri | Requirement |
|---|---|---|
| FR-B1 | M | Back-end interface: `realize(graph) → shape`, `tessellate(shape, tolerance) → mesh with per-triangle node id`, `bounds`, `volume`, `area`, `is_valid`, `section(plane) → 2D curves`. |
| FR-B2 | M | Back-end **B** (OpenCASCADE via OCP): exact B-rep; required for STEP, IGES and analytic 2D sections. |
| FR-B3 | M | Back-end **M** (Manifold): guaranteed-manifold mesh booleans; used where OCP has no wheel (Windows on ARM today) and as a fast preview option. |
| FR-B4 | M | A format that needs exact geometry is refused on back-end M with a clear diagnostic. A STEP file of triangles is never produced silently. |
| FR-B5 | M | Tessellation tolerance: linear deflection default 0.1 % of the bounding-box diagonal, angular 0.5 rad, both overridable per export and by `$fn/$fa/$fs` hints. |
| FR-B6 | S | `projection` with hidden-line removal on back-end B for 2D drawing views. |

## 6. Functional requirements: user interface and CLI

| ID | Pri | Requirement |
|---|---|---|
| FR-U1 | M | `gcad serve` starts a local server on 127.0.0.1 with a random port and session token and opens the default browser. Works offline: all JavaScript is vendored, no CDN at run time. |
| FR-U2 | M | Three panes: code editor (CodeMirror 6), 3D viewer (three.js), console (diagnostics and log). Kernel picker; file open/save on the local disk. |
| FR-U3 | M | Re-evaluation on save and on F5; optional live mode with 300 ms debounce. A failed evaluation keeps the last good model on screen. |
| FR-U4 | M | Viewer: orbit, pan, zoom, fit, standard views, axes, grid, wireframe toggle, bounding-box dimensions in document units. |
| FR-U5 | M | Diagnostics appear inline in the editor at their source span and in the console. |
| FR-U6 | M | Export dialog listing every format of §8, greyed with a reason when the active back-end cannot produce it. |
| FR-U7 | S | Click geometry in the viewer → highlight the source span that created it, and the reverse. |
| FR-U8 | S | Sliders on numeric literals (D9): scrubbing rewrites the literal in the source and re-evaluates. |
| FR-U9 | S | 2D view for 2D models and sections, with true-scale rulers. |
| FR-U10 | C | Notebook documents (`.gcadnb`, JSON like `.ipynb`): cells, each bound to a kernel, sharing one scene; later a Jupyter kernel wrapper. |
| FR-CLI1 | M | `gcad kernels`, `gcad eval <file> [--kernel k] [--dump gcore|graph]`, `gcad export <file> -f <fmt> -o <out> [--backend B|M] [--tol …]`, `gcad kernel check <dir>`, `gcad serve`. |
| FR-CLI2 | M | The CLI needs no display, GPU or browser; it is the reference path for tests and automation. Exit code 0 on success, non-zero on any error diagnostic. |

## 7. Protocols

### 7.1 UI ↔ server (M)

JSON-RPC 2.0 over WebSocket. Methods: `kernel/list`, `kernel/describe` (extensions, highlight tokens), `document/evaluate {kernel, source, params} → {diagnostics, graph, preview}` where `preview` is a URL to a binary glTF with per-node ids, `export/formats`, `export/run {format, options} → file`. Notifications: `log`, `progress`. Method names follow LSP conventions so a later LSP bridge for VS Code and other editors is a thin layer (D4).

### 7.2 Server ↔ process kernel (S)

JSON-RPC 2.0 over the child's stdin/stdout, newline-delimited. `initialize → {name, version, extensions, outputs}`, `evaluate {source, params, workdir} → {diagnostics, graph | step_path | mesh_path}`, `shutdown`. The child runs in its own working directory with the same time and memory limits as FR-C6.

## 8. Export formats

### 8.1 Matrix

"Source" is what the exporter consumes: B-rep (exact), mesh (tessellation), or 2D (2D shape, section or projection). Verification uses an independent reader wherever one exists.

| Format | Standard / version | Ext | Source | Implementation | Pri | Verified by |
|---|---|---|---|---|---|---|
| STEP | ISO 10303-21, AP214 default, AP242 option | `.step .stp` | B-rep | OCCT `STEPControl_Writer` | M | Re-read with OCCT; one valid solid per body; volume equals analytic value within 1e-6 relative; units read back as mm |
| IGES | IGES 5.3 | `.iges .igs` | B-rep | OCCT `IGESControl_Writer` (B-rep mode, entity 186) | M | Re-read with OCCT; volume as STEP |
| STL | 3D Systems STL, binary and ASCII | `.stl` | mesh | Own writer, explicit little-endian | M | `trimesh` re-read; watertight; triangle count matches header; volume within 0.5 % of B-rep volume |
| OBJ | Wavefront OBJ + MTL for colour | `.obj .mtl` | mesh | Own writer | M | `trimesh` re-read; face and vertex counts |
| X3D | ISO/IEC 19775-1 / 19776-1, X3D 3.3 XML encoding | `.x3d` | mesh | Own writer, `IndexedFaceSet` | M | Validate against the X3D 3.3 XML schema; triangle count |
| VRML | ISO/IEC 14772-1 (VRML97) | `.wrl` | mesh | Own writer, `IndexedFaceSet` | M | Re-read with OCCT VRML reader; triangle count |
| COLLADA | COLLADA 1.4.1 (ISO/PAS 17506) | `.dae` | mesh | Own writer | M | Validate against the COLLADA 1.4.1 schema; re-read with `pycollada` |
| UNV | I-DEAS Universal: datasets 164 (units), 2411 (nodes), 2412 (elements) | `.unv` | mesh | Own writer; surface triangles (FE type 91) | M | Re-read with Gmsh where available; node and element counts |
| glTF | glTF 2.0 binary | `.glb` | mesh | Own writer (also the viewer feed) | M | glTF validator; re-read with `trimesh` |
| DXF | AutoCAD DXF, R2000 (AC1015) default, R12 option | `.dxf` | 2D | `ezdxf` | M | `ezdxf` audit with zero errors; every vertex read back equals the source in mm |
| SVG | SVG 1.1 Second Edition | `.svg` | 2D | Own writer, 1 user unit = 1 mm, `width/height` in mm | M | XML well-formed; rendered and inspected; dimensions in mm |
| PDF (2D) | ISO 32000-1 (PDF 1.7), vector, 1:1 scale | `.pdf` | 2D | Own minimal writer (paths only) | M | `pypdf` re-read; `qpdf --check` where available; page size equals drawing extents |
| EPS | Adobe EPSF 3.0, PostScript Level 2 | `.eps` | 2D | Own writer | M | Ghostscript render where available; BoundingBox in points matches mm extents |
| CGM | ISO/IEC 8632 Version 1, binary encoding | `.cgm` | 2D | Own writer | S | LibreOffice Draw import where available; element count |
| Tetra UNV | UNV dataset 2412, FE type 111 | `.unv` | B-rep → volume mesh | Gmsh (GPLv2+, optional dependency) | S | Gmsh re-read; element quality report |
| PLY, OFF, 3MF | de facto / 3MF Core 1.3 | | mesh | Own writers or `trimesh` | C | `trimesh` re-read |
| DWG | Autodesk DWG | `.dwg` | 2D | Optional plug-in calling a user-installed converter on our DXF | C | Converter round-trip to DXF, vertex comparison |
| 3D PDF | PRC (ISO 14739-1) or U3D (ECMA-363) in PDF | `.pdf` | mesh | No maintained open writer identified; research item | C | Opens with 3D content in Adobe Acrobat |

### 8.2 Rules for every exporter

- **EX-1 (M) Units:** every format that can declare units does so; 2D formats are written at 1:1 in document units.
- **EX-2 (M) Determinism:** same source, kernel version and back-end version give byte-identical files. Timestamps come from `SOURCE_DATE_EPOCH` when set, else are fixed by an option, so exports diff cleanly in git (D10).
- **EX-3 (M) Metadata:** title, author, generator `GCAD Studio <version>` and source file name in each format's header field where one exists.
- **EX-4 (M) Byte order and encoding:** binary writers state their byte order explicitly; text formats are UTF-8 where the standard allows, ASCII otherwise.
- **EX-5 (M) Names and colours** from the graph are carried where the format supports them (STEP product names, OBJ groups and MTL, X3D/VRML/COLLADA materials, DXF layers).
- **EX-6 (M) Round-trip test** per format in CI using the "verified by" column; an exporter without a passing round-trip test is not listed as supported.

### 8.3 Formats from the request that the MVE cannot honestly meet

- **ACIS SAT** (and Parasolid `.x_t` from the issue): proprietary kernel formats. OpenCASCADE's open-source edition has no writer and no maintained open-source writer was found. Proposal: not supported; STEP AP242 is the route into ACIS- and Parasolid-based systems.
- **DWG:** writing needs either the Open Design Alliance SDK (commercial) or LibreDWG, whose write support is limited. Proposal: optional plug-in (Could) that drives a converter the user installs; never bundled.
- **PHIGS** (ISO/IEC 9592): a 3D graphics programming interface, not an interchange file format. Proposal: not an export target; CGM (ISO/IEC 8632), the ISO picture-exchange metafile standard, covers 2D picture exchange.
- **3D PDF:** needs a PRC or U3D writer; none suitable was found as a maintained library. Proposal: research item after the MVE.

## 9. Non-functional requirements

| ID | Pri | Requirement |
|---|---|---|
| NFR-1 | M | **Platforms:** Windows 10/11, macOS 12+, Linux with glibc ≥ 2.28. Architectures x86_64 and arm64. Back-end B wherever `cadquery-ocp` ships a wheel; everywhere else (today Windows on ARM) the studio runs with back-end M and says which formats are unavailable. |
| NFR-2 | M | **Hardware-agnostic:** no GPU, vendor API (CUDA, Metal compute, DirectX) or CPU-specific instruction set is required for parsing, evaluation, geometry or export. The viewer uses WebGL 2 with WebGL 1 fallback; software renderers count as supported. |
| NFR-3 | M | **Python:** the version range is pinned to the intersection of the `cadquery-ocp` and `manifold3d` wheel tags at implementation time (expected 3.11–3.13). |
| NFR-4 | M | **Install:** `pip install gcad-studio` or `uv tool install gcad-studio`; no compiler, no admin rights, no network after install. |
| NFR-5 | M | **Security:** server binds to 127.0.0.1 only and requires the session token; grammar kernels are data; trusted-only kernels need explicit consent; evaluation limits per FR-C6. |
| NFR-6 | M | **Licence:** AGPL-3.0 for GCAD Studio. Dependencies must be AGPL-compatible: Lark (MIT), OCP (Apache-2.0) with OCCT (LGPL-2.1 with exception), Manifold (Apache-2.0), ezdxf (MIT), three.js (MIT), CodeMirror (MIT), Gmsh (GPLv2+, optional). No proprietary SDK is bundled. |
| NFR-7 | S | **Performance targets** on a 4-core laptop CPU without GPU: parse + evaluate the bundled examples in < 200 ms; preview update < 1 s for a model with ≤ 100 boolean operations on back-end M and < 3 s on back-end B. |
| NFR-8 | M | **Quality gates:** `ruff` and type checks on Python; unit tests for GCore, rewrite engine and each exporter; golden-file tests stored under `tests/golden/` (the repo's `.gitignore` ignores `*.obj`, so OBJ goldens need a negated rule or a different extension). |
| NFR-9 | M | **CI matrix** (GitHub Actions): `ubuntu-latest`, `ubuntu-24.04-arm`, `windows-latest`, `windows-11-arm` (back-end M), `macos-latest` (arm64), and `macos-15-intel` (x86_64). Every job runs the CLI export suite; one job also opens the UI headless and checks the viewer loads a model. |
| NFR-10 | M | **Docs:** README with install and a 5-minute tutorial; `docs/kernel-authoring.md` that takes a reader from an empty folder to a working kernel. |

## 10. Acceptance criteria for the MVE

1. **AC-1 Same model, two languages:** `examples/bracket.mscad` (minscad) and `examples/bracket.gcsg` (csg) give the same canonical graph hash (FR-G1).
2. **AC-2 Exact export:** a test part (box 40×30×10 mm minus a through-hole Ø8 mm) exported to STEP and IGES re-reads as one valid solid with volume 12,000 − 160π mm³ within 1e-6 relative.
3. **AC-3 Mesh export:** the same part in STL, OBJ, X3D, VRML, COLLADA, UNV and glTF re-reads with the expected triangle count, is watertight, and has volume within 0.5 % of AC-2.
4. **AC-4 2D export:** a 2D plate (rectangle with holes) and a section of the test part exported to DXF, SVG, PDF and EPS read back with every vertex within 1e-6 mm of the source and at 1:1 scale.
5. **AC-5 New language without code:** a third kernel written only as `kernel.toml`, `grammar.lark` and `rewrite.toml` (for example a YAML-like declarative parts list) passes `gcad kernel check` and renders in the UI with highlighting.
6. **AC-6 Portability:** the CI matrix of NFR-9 is green; on `windows-11-arm` the B-rep formats are reported as unavailable rather than failing.
7. **AC-7 Determinism:** exporting twice with `SOURCE_DATE_EPOCH` set gives byte-identical files for every format.
8. **AC-8 Safety:** a source with infinite recursion stops with a diagnostic within the time limit; the UI stays responsive.

## 11. Decisions to confirm before development

1. **Stack:** Python core + local browser UI (recommended, §4) versus C++/Qt as discussed in 2018.
2. **Grammar notation:** Lark EBNF (recommended: run-time loading, any context-free grammar, pure Python) versus tree-sitter (incremental, editor-grade, but each grammar needs a generate-and-compile step) versus ANTLR 4 (mature, but needs Java for generation).
3. **Unmet formats:** accept the treatment of ACIS, DWG, PHIGS and 3D PDF in §8.3.
4. **First surface language:** `minscad` (OpenSCAD subset) as the lead example, so OpenSCAD users can try it without learning anything new.

## 12. Delivery plan

| Milestone | Content | Exit check |
|---|---|---|
| M0 | Repo scaffold, packaging, CI matrix, README | CI green on all six runners with a smoke test |
| M1 | GCore, evaluator, `csg` kernel, back-end M, STL/OBJ/glTF, CLI | AC-3 for STL/OBJ/glTF on back-end M |
| M2 | Grammar-kernel engine, `minscad`, `gcad kernel check` | AC-1, AC-5 |
| M3 | Back-end B, STEP, IGES, X3D, VRML, COLLADA, UNV | AC-2, AC-3 |
| M4 | 2D: sections, DXF, SVG, PDF, EPS (CGM if time) | AC-4 |
| M5 | Browser studio (FR-U1–U6) | Manual walk-through on Windows, macOS, Linux; headless UI check in CI |
| M6 | Determinism, limits, docs, first release `v0.1.0` | AC-6, AC-7, AC-8 |

## 13. Glossary

- **Kernel:** a language plug-in. A grammar kernel is grammar plus rewrite rules; a process kernel is an external program. (Not a geometry kernel; those are called back-ends here.)
- **Back-end:** the geometry engine that turns the graph into shapes (OCCT or Manifold).
- **GCore:** the shared functional core language every kernel rewrites into.
- **Geometry graph:** the evaluated, language-neutral model that back-ends realize and exporters consume.
- **MVE:** minimum viable example, the first end-to-end slice of the product.
