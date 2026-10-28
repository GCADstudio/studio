# Changelog

## Unreleased

**PMI viewing (M15, proposed; SPEC FR-I9).** A STEP AP242 file's graphical PMI (tessellated and polyline callouts) is drawn on the model and its semantic PMI listed in a new annotation panel: geometric tolerances of every kind with their datum systems and modifiers, dimensions with values, ± tolerances, limits, basic and statistical notes, datums, datum features and targets, each linked to its callout and to the faces it applies to; saved views filter the annotations and turn the camera. Faces of an imported B-rep keep the `#n` of the `ADVANCED_FACE` they came from, so clicking one opens its line. The reading is the kernel's prelude (data); checked against NIST's expected PMI for the two bundled NIST test files, CTC-01 and CTC-05. In the browser the B-rep is left out and the PMI shown. New for every kernel: `annotate` (annotations beside the graph, outside its hash), `position`, `positions` and a native `filter`, and `time_limit` in `kernel.toml`; `gcad eval --dump annotations`. Fixed in the M14 kernel: representations whose first partial record is `CHARACTERIZED_OBJECT` (common in AP242 PMI files) lost their items; styles are read only for CSG models.

**ISO 10303 parametric exchange (M14, proposed; SPEC §5.5).** A kernel, `iso10303`, opens STEP files (`.stp`, `.step`, `.p21`): ISO 10303-42 CSG and 2D CSG, ISO 10303-55 histories, ISO 10303-108 parameters with ISO 13584-20 expressions (a slider on a parameter drives the model), ISO 10303-111 holes, pockets and bosses, ISO 10303-112 sketches and colours; explicit B-rep is read by OpenCASCADE. Each file is checked against the EXPRESS schema its header names, every WHERE, UNIQUE and global rule evaluated, with diagnostics at the instance. The export format `step-parametric` writes any model back as parametric STEP under the AP242 long form, which reads back to the same graph. Its grammar is traced to the standard's own productions and checked against them; a coverage matrix gives every AP242 entity a status. The ISO 10303 stack is freecad-nxstyle's, vendored. New for every kernel: `letrec`, records and `at`, data built-ins, `import_source`, named string decoders, semantic checkers, trace mode, graph export formats, and `gcad grammar import` for Wirth Syntax Notation. New commands: `gcad iso schema` and `gcad iso check`.

**Web studio (M7, SPEC §4.1).** The studio as a static site that needs nothing installed: the same UI talks to `gcad.server.Studio` running in Pyodide in a Web Worker instead of to `gcad serve`. Back-end M runs on Manifold's own WebAssembly build through a small binding with the manifold3d API, so the geometry matches the native back-end (checked for every bundled example). Every mesh and 2D format exports in the browser; STEP and IGES say they need the local install. Files are kept in the browser, with Upload and Download. A runaway model is replaced by the next edit within about a second, by a standby worker. `tools/build_site.py` builds the site reproducibly from pinned, SHA-256-checked Pyodide, Manifold and wheels; CI tests it in headless Chrome and publishes it to GitHub Pages from `master`.

## 0.1.0

The first release: the minimum viable example of [the specification](docs/SPEC.md), milestones M0 to M6.

**Languages.** GCore, a small functional core language, with an evaluator that keeps source spans and stops runaway models at set limits. Grammar kernels: a Lark EBNF grammar plus GCore rewrite templates, with no Python. Three bundled kernels: `minscad` (`.mscad`, an OpenSCAD subset checked against OpenSCAD 2021.01), `csg` (`.gcsg`, S-expressions) and `parts` (`.parts`, a declarative parts list). `gcad kernel check` and `gcad kernel describe` for kernel authors, with a [guide](docs/kernel-authoring.md).

**Geometry.** An exact B-rep back-end on OpenCASCADE and a mesh back-end on Manifold, both CPU only. Planar sections and projections of 3D models, and 2D models.

**Export.** 3D: STEP (AP214, AP242), IGES, STL, OBJ + MTL, glTF, X3D, VRML, COLLADA, UNV. 2D: DXF, SVG, PDF, EPS, CGM. Every format is written at 1:1 in millimetres and is byte-reproducible with `SOURCE_DATE_EPOCH` or `--epoch`. Each one is checked by reading it back with an independent reader.

**Studio.** `gcad serve` opens a local browser studio. It has an editor with grammar-derived highlighting and a 3D and 2D viewer that links faces to code. Number sliders, Live mode, an export dialog, and dark and light themes. It runs offline on 127.0.0.1 with a session token.

**Platforms.** Windows, macOS and Linux on x86_64 and arm64, Python 3.11 or newer, tested in CI on all six. On Windows on ARM there is no OpenCASCADE build, so STEP and IGES are reported as unavailable.

Not in this release: ACIS, DWG, PHIGS and 3D PDF (see SPEC §8.3), process kernels, and a PyPI package.
