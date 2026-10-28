# Changelog

## Unreleased

**Kernel `elmergrid`.** ElmerGrid's `.grd` structured-mesh format from Elmer FEM, MATC expressions included, as a fourth bundled kernel: grammar, rewrite rules, a GCore prelude and five examples, with no change to the core. Each material becomes a named, coloured solid or region, so a `.grd` file opens in the studio and exports like any other model. Keywords that only shape the mesh are ignored; ones that change the geometry and are not supported are errors at their line. Checked against ElmerGrid itself: per-material area or volume and bounds match on 441 of the 531 distinct `.grd` files in Elmer's sources, and every other file is an error by design (SPEC §5.2).

**Web studio (M7, SPEC §4.1).** The studio as a static site that needs nothing installed: the same UI talks to `gcad.server.Studio` running in Pyodide in a Web Worker instead of to `gcad serve`. Back-end M runs on Manifold's own WebAssembly build through a small binding with the manifold3d API, so the geometry matches the native back-end (checked for every bundled example). Every mesh and 2D format exports in the browser; STEP and IGES say they need the local install. Files are kept in the browser, with Upload and Download. A runaway model is replaced by the next edit within about a second, by a standby worker. `tools/build_site.py` builds the site reproducibly from pinned, SHA-256-checked Pyodide, Manifold and wheels; CI tests it in headless Chrome and publishes it to GitHub Pages from `master`.

## 0.1.0

The first release: the minimum viable example of [the specification](docs/SPEC.md), milestones M0 to M6.

**Languages.** GCore, a small functional core language, with an evaluator that keeps source spans and stops runaway models at set limits. Grammar kernels: a Lark EBNF grammar plus GCore rewrite templates, with no Python. Three bundled kernels: `minscad` (`.mscad`, an OpenSCAD subset checked against OpenSCAD 2021.01), `csg` (`.gcsg`, S-expressions) and `parts` (`.parts`, a declarative parts list). `gcad kernel check` and `gcad kernel describe` for kernel authors, with a [guide](docs/kernel-authoring.md).

**Geometry.** An exact B-rep back-end on OpenCASCADE and a mesh back-end on Manifold, both CPU only. Planar sections and projections of 3D models, and 2D models.

**Export.** 3D: STEP (AP214, AP242), IGES, STL, OBJ + MTL, glTF, X3D, VRML, COLLADA, UNV. 2D: DXF, SVG, PDF, EPS, CGM. Every format is written at 1:1 in millimetres and is byte-reproducible with `SOURCE_DATE_EPOCH` or `--epoch`. Each one is checked by reading it back with an independent reader.

**Studio.** `gcad serve` opens a local browser studio. It has an editor with grammar-derived highlighting and a 3D and 2D viewer that links faces to code. Number sliders, Live mode, an export dialog, and dark and light themes. It runs offline on 127.0.0.1 with a session token.

**Platforms.** Windows, macOS and Linux on x86_64 and arm64, Python 3.11 or newer, tested in CI on all six. On Windows on ARM there is no OpenCASCADE build, so STEP and IGES are reported as unavailable.

Not in this release: ACIS, DWG, PHIGS and 3D PDF (see SPEC §8.3), process kernels, and a PyPI package.
