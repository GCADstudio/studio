# GCAD Studio — Requirements and Specification (MVE v0.1)

Status: approved 2026-10-27 with the recommended §11 decisions (Python core + local browser UI, Lark EBNF, the §8.3 treatment, `minscad` as lead language). Milestones M0 to M7 are implemented; see the README for what works today. M7 is the web deployment mode (§4.1). The v0.2 milestones (M8 to M13, §12.2) and their acceptance criteria (AC-13 to AC-20, §10) are proposed and await approval; so are M14 (ISO 10303 parametric exchange, §5.5) and M15 (PMI viewing), §12.3, with AC-21 to AC-30. Repository: `GCADstudio/studio` (public, AGPL-3.0).

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

**After the MVE, M7:** the web deployment mode (§4.1): the same studio as a static site, with the Python core running in the visitor's browser.

**Not in the MVE** (designed for, built later): notebook documents and a Jupyter kernel; process kernels for OpenSCAD, CadQuery, Curv and others; importing CAD files; CAM (G-code, HPGL, OpenSBP) and CAE solver decks; assemblies and PMI; fillets/chamfers/hull/Minkowski; the visual node editor; 3D PDF; DWG; ACIS; PHIGS (see §8.3).

## 4. Architecture

```
 Browser UI (editor | viewer | console)          gcad CLI (headless)
          │  JSON-RPC 2.0 over HTTP (§7.1)            │ in-process
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

### 4.1 Web deployment mode (M7)

A second way to run the same studio: a static site, published on GitHub Pages, where a visitor edits, views and exports with nothing installed. The UI and the Python core are the ones `gcad serve` uses; only where Python runs changes. D7 still describes `gcad serve`. The web mode is possible because CPython now runs in the browser as WebAssembly ([Pyodide](https://pyodide.org)) and Manifold publishes its own WebAssembly build; neither existed when D7 was written. Exact B-rep geometry (OpenCASCADE) is still native-only, so the web mode is a mesh-and-drawing studio, and the local install remains the full product.

```
 Browser tab (one static origin, no server-side code)
 ├─ main thread: UI (editor | viewer | console), the same files as gcad serve
 │      │ rpc(method, params): one transport switch in js/rpc.js
 │      │   server mode: HTTP POST /rpc          web mode: postMessage to the worker
 │      ▼
 └─ Web Worker: Pyodide (CPython 3.13 in WebAssembly)
        gcad wheel: kernels, evaluator, exporters, gcad.server.Studio (the same RPC methods)
        back-end M: gcad.backends.manifold_backend, on manifold-3d (WebAssembly) via Pyodide's JS FFI
        user files: a folder backed by IndexedDB (Pyodide's IDBFS)
```

| ID | Pri | Requirement |
|---|---|---|
| FR-W1 | M | **What runs where.** The page's main thread only edits and draws, as in server mode. Pyodide runs in a dedicated module Web Worker that answers the §7.1 JSON-RPC methods by calling `gcad.server.Studio` unmodified (a thin subclass only says where files are kept). The UI chooses its transport with one switch; no kernel, evaluation, geometry or export logic is written in JavaScript. |
| FR-W2 | M | **Mesh back-end.** Back-end M uses Manifold's own WebAssembly build (npm `manifold-3d`, the same version as the `manifold3d` wheel), reached through Pyodide's JS FFI behind the existing back-end interface (§5.4): a small Python binding presents the subset of the `manifold3d` API that `manifold_backend.py` uses, so the realizer (tessellation, twist convention, polyhedron orientation, per-triangle provenance) is shared code, not a port. Decision (2026-10-27): `manifold3d` has no Pyodide recipe; building one would mean pinning Pyodide's exact Emscripten toolchain, cross-compiling its nanobind extension (not tested upstream for Emscripten) and rebuilding and hosting a wheel for every Pyodide ABI, while the JS build is maintained and tested by Manifold's authors. Consequence: the JS binding returns meshes in single precision, so mesh exports carry float32 vertex coordinates (binary STL is float32 anyway); volumes, areas and 2D outlines stay double precision. |
| FR-W3 | M | **Formats.** Every mesh and 2D format of §8.1 whose exporter works under Pyodide is offered. STEP and IGES are greyed in the export dialog with the reason, as on Windows on ARM (AC-6): they need back-end B, and OpenCASCADE has no Pyodide build (a multi-million-line C++ kernel behind a pybind11 binding; nobody publishes one). An exporter that fails under Pyodide is listed as unavailable with its reason, never offered and then broken. |
| FR-W4 | M | **Files.** The bundled examples are available read-only, as in server mode. User files are kept in a folder in the browser (IndexedDB, through Pyodide's IDBFS), per browser and per site, and survive reloads; clearing the site's data deletes them, and the UI says where files are kept. *Download* saves the open file to the visitor's disk; *Upload* copies a local file into the browser folder and opens it. |
| FR-W5 | M | **Limits and responsiveness.** Evaluation and export run in the worker, so the page never blocks; the FR-C6 limits apply unchanged. A newer evaluation replaces a running one (as AC-8): older evaluations still waiting are dropped at once; a running one that has not finished within a short grace period (1 s) is stopped by terminating the worker and handing over to another one, because a busy worker cannot be signalled without `SharedArrayBuffer`, which needs cross-origin-isolation headers that GitHub Pages cannot send. User files live in IndexedDB, so restarting the worker loses nothing. |
| FR-W6 | M | **Security.** There is no server-side code, no RPC endpoint and so no session token: the origin only serves static files, and sources, models and exports never leave the browser. Every script, WebAssembly module and wheel is served from the site's own origin; nothing is fetched from a CDN at run time. Grammar kernels are still data, and the FR-C6 limits still apply. |
| FR-W7 | M | **Dependencies pinned and checked.** Pyodide (0.29.5: CPython 3.13, which the CI matrix also tests natively), manifold-3d and every wheel are pinned to exact versions with SHA-256 digests in one manifest in the repository. The build fetches them and fails on any mismatch; Pyodide's own packages (numpy and the pure-Python dependencies it ships) are also checked against the digests in its pinned `pyodide-lock.json`, again when loaded. No third-party binary is committed to git. |
| FR-W8 | M | **Build.** One command assembles the site into a folder: `src/gcad/web`, a `gcad` wheel built from the checkout, and the pinned assets. The same checkout and manifest give byte-identical output. The folder works from any static file server, including a local copy for offline use; offline use after a first visit relies on the browser cache (a service worker is C). |
| FR-W9 | M | **Deployment.** CI builds the site and runs its browser tests on every pull request; a separate job publishes it to GitHub Pages (`actions/upload-pages-artifact` + `actions/deploy-pages`) only on pushes to `master`. Enabling Pages for the repository is the owner's decision. |
| FR-W10 | S | **Load time.** First visit downloads about 15 MB (Pyodide, numpy, the wheels, Manifold) and shows progress; the studio is ready within 20 s on a 50 Mbit/s connection, and within 5 s on later visits. |

As built in M7: `tools/build_site.py` assembles the site from `src/gcad/web` (with `js/mode.js` switched to `"web"`), a `gcad` wheel written directly from the checkout, and the assets pinned in `tools/site-assets.toml` (Pyodide 0.29.5 with numpy, pyparsing, fonttools, typing-extensions and micropip from its lock file; manifold-3d 3.5.4; lark 1.3.1 and ezdxf 1.4.5 from PyPI): about 20 MB uncompressed, byte-identical across builds. The worker installs the wheels with micropip and runs `gcad.browser.BrowserStudio`; `gcad.backends.manifold_js` is the manifold3d-shaped binding. Replacing a runaway (FR-W5): the page keeps a second, booted worker in reserve, which takes over at once and reloads the user's files from IndexedDB, so the next edit's model appears about 1.5 s after it was typed (Live mode's debounce plus the 1 s grace period); booting Python takes about 7 to 17 s on a developer laptop, which a restart without a reserve would add. The browser test reads exports back with trimesh (STL, OBJ, glTF, COLLADA), pyuff (UNV), ezdxf (DXF), pdfminer (PDF) and the CGM structure reader, comparing each with the native export; X3D and VRML are compared with the native file through their point and index lists (the native tests already read them with MeshLab, whose Linux X3D importer failed on the bracket), and SVG and EPS line by line apart from the creation date. FR-W10 has not been measured on a throttled connection.

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
| FR-C8 | S | `letrec`: bindings in any order, each evaluated once when first used; a name bound twice and a cycle of references are errors that name them. Without a body, its value is the list of every binding's value. (M14, for languages with forward references such as ISO 10303-21.) |
| FR-C9 | S | Records: `(record ...)` makes a list that remembers its source span; `(at record body)` attributes the geometry and diagnostics of body to it, so code in a prelude can build geometry that points back to the user's source. (M14.) |
| FR-C11 | S | Annotations: `(annotate id label ...)` reports what the model says about its geometry (a note, a dimension, a tolerance, a saved view) with optional polylines and triangles in model coordinates, the named records it applies to, related annotations and `[name value]` details. Annotations travel beside the graph, never in it, so they change no hash and no back-end sees them. (M15.) |
| FR-C10 | S | Trace mode: the program's top-level numeric definitions are its parameters, and arithmetic keeps how each number was computed from them, without changing the graph (FR-G1). Exporters that carry parameters use it. (M14.) |

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
| FR-K8 | C | Import of ISO 14977 EBNF and W3C EBNF grammars by conversion to Lark. As built in M14 for Wirth Syntax Notation (`gcad grammar import --notation wsn`), the notation of ISO 10303-11 and -21; used to check a hand-written grammar against a standard's own productions. |
| FR-K9 | S | A kernel may name a semantic checker the engine provides (`checker` in `kernel.toml`): it reads the source after the grammar accepted it and adds diagnostics, for what a context-free grammar cannot decide. Its errors mean the source does not conform; they are reported, and `gcad eval` exits non-zero, but the model is still built. (M14: `iso10303-21`.) |

Bundled kernels:

- **`minscad`** (M): OpenSCAD subset — the geometry built-ins of §5.3 with OpenSCAD names and argument conventions, variables, arithmetic, `for`, `if`, `module` and `function` definitions, `$fn/$fa/$fs` as tessellation hints. As built in M2 (`.mscad`, LALR grammar, 55 rewrite rules, a 5-form prelude): also `let`, ternaries, function literals, `children()`/`$children`, `v.x`, ranges, list comprehensions with one `for`, and the `* % # !` modifiers (`*` and `%` drop the object; `#` and `!` keep it). Not yet: `use`/`include` (reported as errors), `if` and `each` inside list comprehensions, `intersection_for`, `import`, `surface`, `text`, and OpenSCAD's scope rule that the last assignment to a name wins in the whole scope (minscad assignments are sequential). Every bundled example is a valid OpenSCAD file and is checked against OpenSCAD output.
- **`csg`** (M): an S-expression language close to GCore, showing that a homoiconic language needs almost no rewrite rules: three, as built in M2. File extension `.gcsg`, because OpenSCAD already uses `.csg` for its CSG-tree export (FR-K7).
- **`parts`** (added in M2 for AC-5): a declarative parts list, one solid per line, written only as `kernel.toml`, `grammar.lark` and `rewrite.toml`.

- **`iso10303`** (M14, §5.5): ISO 10303-21 exchange files (`.stp`, `.step`, `.p21`), a Lark grammar of the exchange structure, 18 rewrite rules and a prelude that builds geometry from the instances, with the `iso10303-21` checker.

### 5.3 Geometry graph

The evaluator emits a JSON directed acyclic graph; identical sub-trees share one node. Each node has `id`, `op`, `args`, `children`, `span`, optional `name` and `color`.

| Group | Ops (MVE = M unless marked) |
|---|---|
| 3D primitives | `box(size, center)`, `sphere(r)`, `cylinder(h, r1, r2, center)`, `polyhedron(points, faces)` |
| 2D primitives | `rect(size, center)`, `circle(r)`, `polygon(points, paths)` |
| Transforms | `translate`, `rotate` (Euler x→y→z in degrees, OpenSCAD order), `scale`, `mirror`, `matrix` (4×4) |
| Booleans | `union`, `difference`, `intersection` (n-ary, 2D and 3D) |
| 2D→3D | `linear_extrude(height, center)`, `rotate_extrude(angle)`; `twist`/`scale` on extrude is S |
| 3D→2D | `section(plane)` M; `projection(view, hidden_lines)` S. As built in M4: `section(z)` cuts at height z (other planes: rotate the model first); `project` gives the outline seen from +z (no hidden lines); minscad's `projection(cut)` maps onto both, as in OpenSCAD. Both run on back-end M; back-end B reports them as not supported yet. |
| Attributes | `color(rgba)`, `name`; `material` S |
| Document | `units` (default mm; also cm, m, in), metadata (title, author, revision) |
| Import | `import(format, data, entity)`: geometry a back-end reads from content the graph keeps by its SHA-256 (`Graph.blobs`), so the hash covers it. As built in M14 for STEP read by back-end B (the whole file); back-end M uses back-end B's tessellation where it is installed. |

FR-G1 (M): the canonical graph JSON (sorted keys, normalized numbers) has a stable SHA-256; the same model written in two kernels must yield the same hash once spans are stripped.

### 5.4 Geometry back-ends

| ID | Pri | Requirement |
|---|---|---|
| FR-B1 | M | Back-end interface: `realize(graph) → shape`, `tessellate(shape, tolerance) → mesh with per-triangle node id`, `bounds`, `volume`, `area`, `is_valid`, `section(plane) → 2D curves`. |
| FR-B2 | M | Back-end **B** (OpenCASCADE via OCP): exact B-rep; required for STEP, IGES and analytic 2D sections. As built in M3 (OCCT 8.0 through `cadquery-ocp`): curved primitives are analytic; a cylinder or circle with an explicit `$fn` of 3 to 16 is the polygon OpenSCAD would make (hex nuts stay hexagons); twisted or scaled extrusions are lofts through the slices; non-uniform scaling turns surfaces into splines. The whole model carries one colour and name, taken from its outermost `color`/`name`; per-face colours need boolean history and are not done yet. |
| FR-B3 | M | Back-end **M** (Manifold): guaranteed-manifold mesh booleans; used where OCP has no wheel (Windows on ARM today) and as a fast preview option. |
| FR-B4 | M | A format that needs exact geometry is refused on back-end M with a clear diagnostic. A STEP file of triangles is never produced silently. |
| FR-B5 | M | Tessellation tolerance: linear deflection default 0.1 % of the bounding-box diagonal, angular 0.5 rad, both overridable per export and by `$fn/$fa/$fs` hints. |
| FR-B7 | M | Back-end `auto` (the default, added in M3) uses B for formats that need exact geometry and M for mesh formats, so mesh exports keep Manifold's per-triangle provenance and speed. `--backend` overrides it. |
| FR-B6 | S | `projection` with hidden-line removal on back-end B for 2D drawing views. |

### 5.5 ISO 10303 parametric exchange (M14, proposed)

The design, its decisions and its verification are in [docs/design/iso10303-kernel.md](design/iso10303-kernel.md); the status of every entity is in [docs/design/iso10303-coverage.tsv](design/iso10303-coverage.tsv).

| ID | Pri | Requirement |
|---|---|---|
| FR-I1 | S | **Part 21 syntax.** The `iso10303` kernel's grammar implements the WSN of ISO 10303-21:2002 Tables 1 to 4, traced production by production, each deviation listed with its reason (design note §1.2); a test compares it with the WSN read from the user's copy of the standard. Edition 3 sections are out of scope. |
| FR-I2 | S | **References.** Instance names may be used before they are defined; dangling, duplicate and cyclic references are errors at the instance. |
| FR-I3 | S | **Reuse.** The Part 21 reader, the EXPRESS reader and interpreter, and the checker come from freecad-nxstyle, vendored unchanged with attribution. |
| FR-I4 | S | **Conformance.** A file is checked against the long form of the application protocol its header names, when it is in the schema cache, else against the resource parts: header, form, and every WHERE, UNIQUE and global rule and INVERSE cardinality, with counts of rules evaluated, violated and not evaluated. A violation that is a documented contradiction in the standards is a warning that says so. No schema is in the repository: `gcad iso schema fetch` downloads the long forms ISO publishes; `gcad iso schema build` compiles the resource parts from the user's copies. |
| FR-I5 | S | **Geometry.** ISO 10303-42 CSG primitives, booleans, half-spaces, replicas, extruded and revolved areas and 2D CSG; ISO 10303-111 round holes, plain pockets and bosses; ISO 10303-55 histories; ISO 10303-112 sketch commands that make regions; colours of whole solids. Explicit B-rep is read by back-end B. Everything else met in a model stops evaluation with a diagnostic naming the instance (FR-B4). |
| FR-I6 | S | **Parameters.** ISO 10303-108 free-form assignments and equal-parameter constraints give bound attributes their values from ISO 13584-20 expressions; a slider on a parameter's value drives the model. |
| FR-I7 | S | **Parametric export.** `step-parametric` writes the graph as ISO 10303-42 CSG in an ISO 10303-55 history, the program's numeric definitions as ISO 10303-108 parameters (FR-C10), under the AP242 long form; the kernel reads it back to the same graph hash. Written from the graph, with no back-end, so also in the web studio. What has no ISO 10303-42 form is refused with a diagnostic. |
| FR-I9 | S | **PMI viewing (M15).** A file's graphical PMI (tessellated annotation occurrences, annotation curve occurrences of polylines and circles, grouped by draughting callout) is drawn in model coordinates; its semantic PMI (geometric tolerances and their subtypes, datums, datum features, datum systems, datum targets, dimensional sizes and locations with their values and plus-minus tolerances) is listed with the faces each applies to, through shape aspects and their item usages, and with the callouts that present it; saved views (draughting models with a camera) show their own annotations and look along the camera. All of it is the prelude's `annotate` calls (FR-C11); the geometry and its graph hash are as without PMI. Explicit B-rep faces keep the `#n` they were read from (FR-U7). |
| FR-I8 | S | **Coverage.** A matrix generated from the schemas gives every entity of the AP242 long form and of the resource parts a status (supported, delegated to back-end B, refused with a diagnostic, out of scope) and a reason; a test fails on any entity without one, or a supported entity without a fixture. |

## 6. Functional requirements: user interface and CLI

| ID | Pri | Requirement |
|---|---|---|
| FR-U1 | M | `gcad serve` starts a local server on 127.0.0.1 with a random port and session token and opens the default browser. Works offline: all JavaScript is vendored, no CDN at run time. |
| FR-U2 | M | Three panes: code editor (CodeMirror 6), 3D viewer (three.js), console (diagnostics and log). Kernel picker; file open/save on the local disk. |
| FR-U3 | M | Re-evaluation on save and on F5; optional live mode with 300 ms debounce. A failed evaluation keeps the last good model on screen. |
| FR-U4 | M | Viewer: orbit, pan, zoom, fit, standard views, axes, grid, wireframe toggle, bounding-box dimensions in document units. |
| FR-U5 | M | Diagnostics appear inline in the editor at their source span and in the console. |
| FR-U6 | M | Export dialog listing every format of §8, greyed with a reason when the active back-end cannot produce it. |
| FR-U7 | S | Click geometry in the viewer → highlight the source span that created it, and the reverse. As built in M5: click → source (the face lights up, the editor selects the call that made it); source → geometry not yet. |
| FR-U8 | S | Sliders on numeric literals (D9): scrubbing rewrites the literal in the source and re-evaluates. As built in M5. |
| FR-U9 | S | 2D view for 2D models and sections, with true-scale rulers. As built in M5: a top view with a millimetre grid and the extents in the stats card; no rulers yet. |
| FR-U11 | S | Annotation panel (M15): the annotations of FR-C11 beside the viewer, by section (annotations, tolerances, dimensions, datums), each drawn one with a check box, saved views as chips that filter and orient; a row selects its source span, lights up the faces it applies to and the annotations it is linked to, and shows its details; a face clicked in the viewer marks the rows that apply to it. |
| FR-U10 | C | Notebook documents (`.gcadnb`, JSON like `.ipynb`): cells, each bound to a kernel, sharing one scene; later a Jupyter kernel wrapper. |
| FR-CLI1 | M | `gcad kernels`, `gcad eval <file> [--kernel k] [--dump gcore|graph]`, `gcad export <file> -f <fmt> -o <out> [--backend B|M] [--tol …]`, `gcad kernel check <dir>`, `gcad serve`. As built in M14, also `gcad iso schema list|fetch|add|build`, `gcad iso check <file>` and `gcad grammar import --notation wsn`. |
| FR-CLI2 | M | The CLI needs no display, GPU or browser; it is the reference path for tests and automation. Exit code 0 on success, non-zero on any error diagnostic. |

## 7. Protocols

### 7.1 UI ↔ server (M)

JSON-RPC 2.0 over WebSocket. Methods: `kernel/list`, `kernel/describe` (extensions, highlight tokens), `document/evaluate {kernel, source, params} → {diagnostics, graph, preview}` where `preview` is a URL to a binary glTF with per-node ids, `export/formats`, `export/run {format, options} → file`. Notifications: `log`, `progress`. Method names follow LSP conventions so a later LSP bridge for VS Code and other editors is a thin layer (D4).

As built in M5: JSON-RPC 2.0 over HTTP POST `/rpc` instead of a WebSocket, because no call needs server-pushed notifications yet and the server then needs only the Python standard library. The session token travels in the URL fragment (never sent to the server) and comes back in an `X-GCAD-Token` header; the `Host` header must be the server's own address (no DNS rebinding). `document/evaluate` returns the mesh as base64 arrays (positions, indices, per-triangle group) with each group's colour and source spans, which is what click-to-source needs; `file/list`, `file/open` and `file/save` work inside the folder `gcad serve` was started in, with bundled examples read-only. A WebSocket can be added when `log`/`progress` notifications are needed.

In web mode (§4.1) the same requests and responses travel by `postMessage` between the page and the worker; `hello` additionally reports `"mode": "web"`, and there is no token.

### 7.2 Server ↔ process kernel (S)

JSON-RPC 2.0 over the child's stdin/stdout, newline-delimited. `initialize → {name, version, extensions, outputs}`, `evaluate {source, params, workdir} → {diagnostics, graph | step_path | mesh_path}`, `shutdown`. The child runs in its own working directory with the same time and memory limits as FR-C6.

## 8. Export formats

### 8.1 Matrix

"Source" is what the exporter consumes: B-rep (exact), mesh (tessellation), or 2D (2D shape, section or projection). Verification uses an independent reader wherever one exists.

| Format | Standard / version | Ext | Source | Implementation | Pri | Verified by |
|---|---|---|---|---|---|---|
| STEP | ISO 10303-21, AP214 default, AP242 option | `.step .stp` | B-rep | OCCT `STEPCAFControl_Writer` (product name and colour via XCAF) | M | Re-read with OCCT; one valid solid per body; volume equals analytic value within 1e-6 relative; units read back as mm. As built: also parsed by `steputils` (an independent Part 21 parser) |
| STEP parametric | ISO 10303-21; ISO 10303-42 CSG, -55 history, -108 parameters, ISO 13584-20 expressions; AP242 long form | `.stp .step` | graph | Own writer (`gcad.iso10303.writer`), layout from freecad-nxstyle; `-f step-parametric` | S | As built (M14): read back by the `iso10303` kernel to the same graph hash; AP242 long form with every rule evaluated; STEPcode `p21read`; FreeCAD with freecad-nxstyle's importer (AC-23) |
| IGES | IGES 5.3 | `.iges .igs` | B-rep | OCCT `IGESControl_Writer` (B-rep mode, entity 186); global-section dates patched to the export time | M | Re-read with OCCT; volume as STEP |
| STL | 3D Systems STL, binary and ASCII | `.stl` | mesh | Own writer, explicit little-endian | M | `trimesh` re-read; watertight; triangle count matches header; volume within 0.5 % of B-rep volume |
| OBJ | Wavefront OBJ + MTL for colour | `.obj .mtl` | mesh | Own writer | M | `trimesh` re-read; face and vertex counts |
| X3D | ISO/IEC 19775-1 / 19776-1, X3D 3.3 XML encoding | `.x3d` | mesh | Own writer, `IndexedTriangleSet`; mm declared by the UNIT statement; +Z turned to X3D's +Y | M | Validate against the X3D 3.3 XML schema; triangle count. As built: schema-valid (checked locally, the schema is fetched online); MeshLab re-read in CI: triangle count, watertight, volume |
| VRML | ISO/IEC 14772-1 (VRML97) | `.wrl` | mesh | Own writer, `IndexedFaceSet`; metres and +Y up by the VRML convention, via one Transform | M | As built: MeshLab re-read (the OCCT VRML reader was not needed): triangle count, watertight, volume in m³ |
| COLLADA | COLLADA 1.4.1 (ISO/PAS 17506) | `.dae` | mesh | Own writer; mm and Z_UP declared in `<asset>` | M | Validate against the COLLADA 1.4.1 schema; re-read with `pycollada`. As built: schema-valid (checked locally); pycollada and MeshLab in CI |
| UNV | I-DEAS Universal: datasets 164 (units), 2411 (nodes), 2412 (elements) | `.unv` | mesh | Own writer; surface triangles (FE type 91); units code 5 (mm) | M | Re-read with Gmsh where available; node and element counts. As built: pyuff (pure Python, every platform) and Gmsh; volume from the re-read mesh |
| glTF | glTF 2.0 binary | `.glb` | mesh | Own writer (also the viewer feed) | M | glTF validator; re-read with `trimesh` |
| DXF | AutoCAD DXF, R2000 (AC1015) default, R12 option | `.dxf` | 2D | `ezdxf`; closed LWPOLYLINE (POLYLINE in R12) per outline, `$INSUNITS` mm | M | `ezdxf` audit with zero errors; every vertex read back equals the source in mm. As built: also imported by Inkscape (rendered and inspected locally) and LibreOffice (CI) |
| SVG | SVG 1.1 Second Edition | `.svg` | 2D | Own writer, 1 user unit = 1 mm, `width/height` in mm | M | XML well-formed; rendered and inspected; dimensions in mm. As built: svgelements re-read (raw path data within 1e-6 mm; through the viewport within 1e-4 mm, its 96 dpi conversion); Inkscape render |
| PDF (2D) | ISO 32000-1 (PDF 1.7), vector, 1:1 scale | `.pdf` | 2D | Own minimal writer (paths only) | M | As built: pdfminer.six re-read within 1e-6 mm; Ghostscript render. Page = drawing extents plus a 1 mm margin, so the 0.1 mm hairline is not clipped |
| EPS | Adobe EPSF 3.0, PostScript Level 2 | `.eps` | 2D | Own writer | M | Ghostscript render where available; BoundingBox in points matches mm extents. As built: Ghostscript interprets the file and pdfminer reads its output (within 2e-3 mm, Ghostscript's PDF precision) |
| CGM | ISO/IEC 8632 Version 1, binary encoding | `.cgm` | 2D | Own writer; real VDCs in IEEE double precision (16.16 fixed point is misread by LibreOffice for negative values), metric scaling 1 mm per unit, POLYGON with hollow interior, black on white in direct colour | S | LibreOffice Draw import where available; element count. As built: a structural parse of the elements in every test run; LibreOffice in one CI job |
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
| NFR-6 | M | **Licence:** AGPL-3.0 for GCAD Studio. Dependencies must be AGPL-compatible: Lark (MIT), OCP (Apache-2.0) with OCCT (LGPL-2.1 with exception), Manifold (Apache-2.0), ezdxf (MIT), three.js (MIT), CodeMirror (MIT), Gmsh (GPLv2+, optional). No proprietary SDK is bundled. Test-only readers, installed with the `dev` extra and never distributed: pymeshlab (GPL-3.0), pycollada (BSD), pyuff (MIT), Gmsh. The web site (§4.1) also distributes Pyodide (MPL-2.0) with CPython (PSF), numpy (BSD-3-Clause), pyparsing (MIT), fonttools (MIT) and typing_extensions (PSF), each with its licence. |
| NFR-7 | S | **Performance targets** on a 4-core laptop CPU without GPU: parse + evaluate the bundled examples in < 200 ms; preview update < 1 s for a model with ≤ 100 boolean operations on back-end M and < 3 s on back-end B. |
| NFR-8 | M | **Quality gates:** `ruff` and type checks on Python; unit tests for GCore, rewrite engine and each exporter; golden-file tests stored under `tests/golden/` (the repo's `.gitignore` ignores `*.obj`, so OBJ goldens need a negated rule or a different extension). |
| NFR-9 | M | **CI matrix** (GitHub Actions): `ubuntu-latest`, `ubuntu-24.04-arm`, `windows-latest`, `windows-11-arm` (back-end M), `macos-latest` (arm64), and `macos-15-intel` (x86_64). Every job runs the CLI export suite; one job also opens the UI headless and checks the viewer loads a model. |
| NFR-10 | M | **Docs:** README with install and a 5-minute tutorial; `docs/kernel-authoring.md` that takes a reader from an empty folder to a working kernel. |

## 10. Acceptance criteria for the MVE

1. **AC-1 Same model, two languages:** `examples/bracket.mscad` (minscad) and `examples/bracket.gcsg` (csg) give the same canonical graph hash (FR-G1).
2. **AC-2 Exact export:** a test part (box 40×30×10 mm minus a through-hole Ø8 mm) exported to STEP and IGES re-reads as one valid solid with volume 12,000 − 160π mm³ within 1e-6 relative.
3. **AC-3 Mesh export:** the same part in STL, OBJ, X3D, VRML, COLLADA, UNV and glTF re-reads with the expected triangle count, is watertight, and has volume within 0.5 % of AC-2.
4. **AC-4 2D export:** a 2D plate (rectangle with holes) and a section of the test part exported to DXF, SVG, PDF and EPS read back with every vertex within 1e-6 mm of the source and at 1:1 scale. (As built: met for DXF, SVG and PDF; EPS is checked through Ghostscript, whose PDF output limits the round trip to 2e-3 mm.)
5. **AC-5 New language without code:** a third kernel written only as `kernel.toml`, `grammar.lark` and `rewrite.toml` (for example a YAML-like declarative parts list) passes `gcad kernel check` and renders in the UI with highlighting.
6. **AC-6 Portability:** the CI matrix of NFR-9 is green; on `windows-11-arm` the B-rep formats are reported as unavailable rather than failing.
7. **AC-7 Determinism:** exporting twice with `SOURCE_DATE_EPOCH` set gives byte-identical files for every format. (As built: checked in two separate processes with different hash seeds, for every format and both back-ends, on every CI runner.)
8. **AC-8 Safety:** a source with infinite recursion stops with a diagnostic within the time limit; the UI stays responsive. (As built: the server answers while an evaluation runs, and a newer evaluation stops the running one instead of queueing behind it.)

Web deployment mode (M7, §4.1), checked in CI against the built site served by a plain static file server in headless Chrome:

9. **AC-9 Web studio:** the site loads and is ready with no server-side code (no request reaches an RPC endpoint), and the minscad bracket example evaluates and renders a mesh, in the dark and the light theme.
10. **AC-10 Web export:** STL and DXF exported in the browser download files that read back with the readers of AC-3 and AC-4 (trimesh, ezdxf) and match the native export; every other format either exports in the worker and reads back, or is reported unavailable with a reason (STEP and IGES).
11. **AC-11 Web safety:** while a runaway source (`(f 60)` with exponential recursion, csg kernel) evaluates, the page stays responsive, and the next edit replaces it and shows its model before the 10 s limit would have stopped the runaway.
12. **AC-12 Web parity:** every bundled example gives the same triangle count, bounds, volume and area in the browser as on the native back-end M (within 1e-6 relative).

v0.2 (proposed, §12.2):

13. **AC-13 OpenSCAD as a process kernel:** every bundled minscad example, opened as a `.scad` file with the `openscad` kernel, gives the same graph hash as minscad once spans are stripped, and so the same exports. A file that uses `text()` renders through OpenSCAD's own mesh, the console says so, and STEP export of it is refused (FR-B4). Checked in CI on a runner with OpenSCAD installed; skipped elsewhere.
14. **AC-14 CadQuery as a process kernel:** a CadQuery script for the AC-2 part exports to STEP and IGES and re-reads with volume 12,000 − 160π mm³ within 1e-6 relative. A script that loops forever is stopped at the wall-time limit with a diagnostic, and the studio stays responsive. Opening a process kernel from an untrusted folder asks for consent first.
15. **AC-15 OpenSCAD parity, second set:** new examples using `hull`, `minkowski`, `offset`, `include`/`use` and `import` (STL and DXF) are valid OpenSCAD files and match OpenSCAD 2021.01 within the tolerances of `tests/test_openscad_parity.py`, in minscad and through the `openscad` kernel's `.csg` route (no mesh fallback).
16. **AC-16 Notebooks:** a `.gcadnb` document with a minscad cell, a csg cell and a Markdown cell runs in server mode and in web mode with the same results (triangle count, bounds, volume). Saving it twice without changes gives identical bytes.
17. **AC-17 Jupyter:** `jupyter nbconvert --execute` runs a notebook that uses the `gcad` Jupyter kernel with no display. Each geometry cell's output contains a binary glTF that reads back with trimesh and matches the CLI export's volume, and the 3D view in JupyterLab renders with no network request (headless Chrome).
18. **AC-18 Exact drawings:** on back-end B, a section of the AC-2 part at z = 5 exports to DXF as one CIRCLE of radius 4 and one closed 40 × 30 outline, exact within 1e-9 mm, and to SVG with an arc, not a polygon. A hidden-line front view of the bracket puts visible and hidden edges on separate DXF layers and SVG classes.
19. **AC-19 More formats:** PLY, OFF and 3MF re-read with trimesh as in AC-3 and are byte-reproducible as in AC-7. A tetrahedral UNV re-reads with Gmsh with a total element volume within 0.5 % of AC-2. The DWG plug-in, where a converter is installed, converts DWG back to DXF with every vertex equal to the source; this check is local only.
20. **AC-20 Release on PyPI:** `pip install gcad-studio==0.2.0` from PyPI into a clean environment works on all six CI runners and `gcad --version` prints 0.2.0 (NFR-4).

ISO 10303 parametric exchange (M14, proposed, §5.5):

21. **AC-21 Grammar and coverage:** `gcad kernel check` passes for `iso10303`; the coverage matrix test passes with no entity of the AP242 long form or the resource parts unstatused and every supported entity in a fixture; locally, with the user's copy of ISO 10303-21, the kernel's grammar and the standard's WSN accept and reject the same sentences.
22. **AC-22 AC-2 in Part 42:** the AC-2 part written as ISO 10303-42 CSG with an ISO 10303-108 parameter for the hole diameter exports to B-rep STEP that re-reads in OpenCASCADE with volume 12,000 − 160π mm³ within 1e-6 relative; with the parameter at 10 mm, 12,000 − 250π.
23. **AC-23 Independent proof:** the parametric files GCAD writes pass the AP242 long form with every rule evaluated (0 errors, 0 not evaluated; warnings only for documented contradictions), and freecad-nxstyle's own checker gives the same counts; STEPcode `p21read` accepts those it does not crash on; FreeCAD with freecad-nxstyle's importer rebuilds the AC-2 file with the same volume and parameter.
24. **AC-24 Foreign files:** B-rep STEP from OpenCASCADE (AP214 and AP242) opens with the same volume, and parametric STEP from freecad-nxstyle opens with its parameters driving the model.
25. **AC-25 Negative controls:** a syntax error, a dangling `#n`, a duplicate `#n`, a reference cycle, a WHERE-rule violation, an entity outside the schema and an unsupported ISO 10303-111 feature each give their exact diagnostic at the instance.
26. **AC-26 Round trip and determinism:** every bundled minscad, csg and parts example exports as parametric STEP and reads back to the same graph hash, or is refused with its stated reason; two processes write byte-identical files.

PMI viewing (M15, proposed, §5.5 FR-I9):

27. **AC-27 NIST CTC-01:** the bundled `nist_ctc_01_asme1_ap242-e1.stp` gives 23 drawn callouts, 1 saved view, 6 geometric tolerances, 12 dimensions, 3 datums, 3 datum features and 2 datum systems; Position.1 reads `⌖ 0.75 mm | A | B | C` on faces #805 and #801 and is linked to its callout #628; the graph hash is the one the file has without PMI.
28. **AC-28 NIST CTC-05:** the bundled `nist_ctc_05_asme1_ap242-e1.stp` (polyline PMI, inches) gives 22 drawn callouts and the saved views MBD_A and MBD_B, in millimetres in model coordinates.
29. **AC-29 Faces:** with OpenCASCADE, every face of CTC-01's B-rep is a mesh group labelled with its `ADVANCED_FACE` `#n`, and every face a PMI item targets is one of them; the studio sends each such face with the span of its instance.
30. **AC-30 Both studios:** the PMI of CTC-01 is seen in the native studio and in the web studio, where OpenCASCADE is absent: the B-rep is left out with a warning and the annotations are still drawn and listed.

## 11. Decisions to confirm before development

1. **Stack:** Python core + local browser UI (recommended, §4) versus C++/Qt as discussed in 2018.
2. **Grammar notation:** Lark EBNF (recommended: run-time loading, any context-free grammar, pure Python) versus tree-sitter (incremental, editor-grade, but each grammar needs a generate-and-compile step) versus ANTLR 4 (mature, but needs Java for generation).
3. **Unmet formats:** accept the treatment of ACIS, DWG, PHIGS and 3D PDF in §8.3.
4. **First surface language:** `minscad` (OpenSCAD subset) as the lead example, so OpenSCAD users can try it without learning anything new.

## 12. Delivery plan

### 12.1 v0.1 (done)

| Milestone | Content | Exit check |
|---|---|---|
| M0 | Repo scaffold, packaging, CI matrix, README | CI green on all six runners with a smoke test |
| M1 | GCore, evaluator, `csg` kernel, back-end M, STL/OBJ/glTF, CLI | AC-3 for STL/OBJ/glTF on back-end M |
| M2 | Grammar-kernel engine, `minscad`, `gcad kernel check` | AC-1, AC-5 |
| M3 | Back-end B, STEP, IGES, X3D, VRML, COLLADA, UNV | AC-2, AC-3 |
| M4 | 2D: sections, DXF, SVG, PDF, EPS (CGM if time) | AC-4 |
| M5 | Browser studio (FR-U1–U6) | Manual walk-through on Windows, macOS, Linux; headless UI check in CI |
| M6 | Determinism, limits, docs, first release `v0.1.0` | AC-6, AC-7, AC-8 |
| M7 | Web deployment mode (§4.1): Pyodide worker, Manifold WebAssembly binding, static-site build, GitHub Pages workflow | AC-9 to AC-12 in CI (headless Chrome); screenshots in both themes |

v0.1.0 was released on 2026-10-27 as a GitHub Release (wheel and sdist). The PyPI job is built but switched off, so NFR-4's `pip install gcad-studio` is not met yet.

### 12.2 v0.2 (proposed 2026-10-27, awaiting approval)

Goal: open OpenSCAD and CadQuery files as they are, make the notebook in "Jupyter for CAD" real, and draw from exact geometry. Each milestone lands as one pull request with its acceptance criteria in CI, and v0.2.0 is published on PyPI.

| Milestone | Content | Exit check |
|---|---|---|
| M8 | **Process kernels** (FR-K7, §7.2), server mode only: a runner that starts the child in its own working folder, speaks the stdio protocol, enforces the FR-C6 wall-time limit by ending the process, and asks for consent as for trusted-only kernels (FR-K6). Two bundled process kernels. **`openscad`** uses the OpenSCAD the user has installed, found on PATH or in the standard install folders. It turns the file into OpenSCAD's `.csg` export and parses that with the minscad grammar and a prelude for `multmatrix`, `group`, `render` and the other `.csg` operations. For operations the graph does not have yet, it uses OpenSCAD's own mesh of the whole model, and the console says so. **`cadquery`** runs a CadQuery script in a separate Python environment that the user names (`GCAD_CADQUERY_PYTHON`) and reads back the STEP file the script writes (`step_path`) on back-end B. | AC-13, AC-14 |
| M9 | **OpenSCAD parity.** New graph operations on back-end M: `hull`, `minkowski` and 2D `offset` (`r`, `delta`, `chamfer`). Back-end B reports them as not supported (FR-B4). minscad gains `include`/`use` (paths relative to the file, inside the served folder), `if` and `each` in list comprehensions, `intersection_for`, `import()` of STL, OFF and DXF (STEP on back-end B), and OpenSCAD's rule that the last assignment wins in the whole scope. `text()` stays with the `openscad` kernel, because fonts are out of scope. | AC-15 |
| M10 | **Notebooks** (FR-U10). In the studio, `.gcadnb` documents hold Markdown cells and code cells, each code cell bound to a kernel. All cells add to one shared scene, and each cell shows its own diagnostics, view and measurements. The file is JSON with sorted keys and stores no outputs by default, so it diffs cleanly in git. A Jupyter kernel, `gcad`, runs the same cells in JupyterLab and outputs a binary glTF plus a self-contained viewer that needs no network. | AC-16, AC-17 |
| M11 | **Drawings from exact geometry.** On back-end B: sections on any plane and projections, keeping analytic curves as DXF CIRCLE and ARC entities and as SVG arcs. Hidden-line views for the standard views (FR-B6, OCCT HLR). Rulers in the 2D view (FR-U9). The second half of FR-U7: selecting source highlights the geometry it made. | AC-18 |
| M12 | **More formats.** PLY, OFF and 3MF with our own writers (from C to S). Tetrahedral UNV through Gmsh, an optional dependency (§8.1). DWG as an optional plug-in that drives a converter the user installs (§8.3): ODA File Converter (free of charge, proprietary, never bundled) or LibreDWG's `dxf2dwg`, whose write support stops at R2004. 3D PDF stays a research item. | AC-19 |
| M13 | **Release v0.2.0 on PyPI.** The owner registers the repository as a trusted publisher for `gcad-studio` on pypi.org and sets `PUBLISH_TO_PYPI`. The README installs from PyPI, and the web studio shows its version. | AC-20, with AC-1 to AC-19 green on the tag |

Alongside these milestones, a separate study, **R1: exact geometry in the browser**, makes a decision only. Today the web mode cannot export STEP or IGES (FR-W3). The candidate follows the FR-W2 pattern: an OCCT WebAssembly build behind a small Python binding that offers the part of OCP that `occt_backend.py` uses. npm on 2026-10-27:

- `opencascade.js`: 1.1.1 stable, last updated March 2023.
- The `@taucad/opencascade.js` fork: 3.0.0 betas on OCCT 8, last published July 2026.
- `occt-import-js`: reads files only.

R1 measures download size and the API coverage the binding needs, and tries to write the AC-2 part as STEP in headless Chrome. It ends with a go or no-go recorded in §4.1. If it is a go, the implementation is planned for v0.3.

Facts checked on 2026-10-27 that shape this plan:

- CadQuery 2.8.0 requires `cadquery-ocp >=7.9.3.1, <8.0`, while gcad requires `>=8.0.1`, so CadQuery cannot share gcad's environment and runs as a process kernel.
- OpenSCAD 2021.01's `.csg` export keeps `text()`, `hull()`, `minkowski()` and `offset()` as operations rather than resolving them into geometry, so M8's mesh fallback covers them until M9 adds hull, Minkowski and offset to the graph.
- The `manifold3d` wheel provides `hull`, `minkowski_sum` and `CrossSection.offset`.

For review:

1. **Order:** M8 comes first because it opens every existing OpenSCAD file at once. M9 then shrinks M8's mesh fallback.
2. **Process kernels are not a sandbox.** They run the user's code with the user's rights. The plan relies on consent, a separate working folder and the time limit.
3. **M13 needs the owner's two steps** on pypi.org and in the repository settings.
4. **Not in v0.2:** assemblies and PMI, fillets and chamfers, CAM, CAE decks, the node editor, an LSP bridge, ACIS, PHIGS and 3D PDF.

### 12.3 ISO 10303 (proposed 2026-10-27, awaiting approval)

Alongside v0.2 and independent of it; each lands as one pull request.

| Milestone | Content | Exit check |
|---|---|---|
| M14 | **ISO 10303 parametric exchange** (§5.5): the `iso10303` kernel, the `iso10303-21` checker, the engine features it needs (FR-C8 to FR-C10, FR-K8, FR-K9, the `import` op), the parametric STEP writer and the coverage matrix. | AC-21 to AC-26 |
| M15 | **PMI viewing** (FR-I9, FR-C11, FR-U11): a STEP file's semantic PMI (datums, geometric tolerances, dimensions with their tolerances) listed in the studio's annotation panel and attached to its faces, and its graphical PMI (annotation curves and tessellated annotations, callouts) drawn in the viewer, with saved views; read by the kernel's prelude (data), with NIST's CTC test files as examples. | AC-27 to AC-30 |

## 13. Glossary

- **Kernel:** a language plug-in. A grammar kernel is grammar plus rewrite rules; a process kernel is an external program. (Not a geometry kernel; those are called back-ends here.)
- **Back-end:** the geometry engine that turns the graph into shapes (OCCT or Manifold).
- **GCore:** the shared functional core language every kernel rewrites into.
- **Geometry graph:** the evaluated, language-neutral model that back-ends realize and exporters consume.
- **MVE:** minimum viable example, the first end-to-end slice of the product.
- **Server mode / web mode:** the studio driven by `gcad serve` on the user's machine, or the static site whose Python core runs in the visitor's browser (§4.1).
- **Pyodide:** CPython compiled to WebAssembly, with a package loader and a JavaScript bridge (FFI).
