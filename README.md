# GCAD Studio

A language-agnostic, code-first CAD studio: what Jupyter did for IPython, GCAD Studio aims to do for OpenSCAD. A **kernel** is a modelling language defined by a formal grammar and rewrite rules; the studio parses it into one shared functional core language (GCore), evaluates it to a neutral geometry graph, renders it, and exports it to neutral and vendor formats. The idea comes from [openscad/openscad#2299](https://github.com/openscad/openscad/issues/2299); the full requirements are in [docs/SPEC.md](docs/SPEC.md).

## Status

Pre-alpha. Milestones M0, M1 and M2 of [the plan](docs/SPEC.md#12-delivery-plan) are done:

| Works now | Planned |
|---|---|
| GCore language and evaluator with source spans, limits and diagnostics | M3: exact B-rep back-end (OpenCASCADE): STEP, IGES; X3D, VRML, COLLADA, UNV |
| Grammar kernels: a Lark EBNF grammar + GCore rewrite templates, no Python | M4: 2D sections; DXF, SVG, PDF, EPS, CGM |
| Kernels `minscad` (`.mscad`, OpenSCAD subset), `csg` (`.gcsg`, S-expressions), `parts` (`.parts`, a parts list) | M5: browser studio (editor, 3D viewer, console) |
| Mesh back-end on [Manifold](https://github.com/elalish/manifold), CPU only | M6: release |
| Export: STL (binary/ASCII), OBJ + MTL, glTF 2.0 binary | |
| `gcad` command line, headless | |

Run `gcad formats` to see every format and its milestone.

## Install

Python 3.11 or newer on Windows, macOS or Linux, x86_64 or arm64. No compiler or GPU is needed.

```bash
pip install -e ".[dev]"
```

## Five-minute tour

The same part in three languages. In `minscad`, an OpenSCAD subset, `part.mscad`:

```openscad
// 40 x 30 x 10 mm block with an 8 mm through-hole
module hole(d) translate([20, 15, -1]) cylinder(h = 12, d = d);

color("silver")
    difference() {
        cube([40, 30, 10]);
        hole(8);
    }
```

In `parts`, a declarative parts list, `part.parts`:

```
part block: box 40 30 10 color silver
cut  hole:  cylinder d 8 h 12 at 20 15 -1
```

In `csg`, S-expressions that are GCore itself, `part.gcsg`:

```scheme
; 40 x 30 x 10 mm block with an 8 mm through-hole
(define (hole d) (translate [20 15 -1] (cylinder :h 12 :d d)))

(color "silver"
  (difference
    (box [40 30 10])
    (hole 8)))
```

```bash
gcad eval part.mscad                    # check it: diagnostics and a graph summary
gcad eval part.mscad --dump gcore       # what the kernel rewrote it into
gcad eval part.mscad --dump graph       # the language-neutral geometry graph as JSON
gcad export part.mscad -o part.stl      # also .obj or .glb; --ascii for ASCII STL
gcad export part.mscad -o part.glb --tol 0.01  # finer tessellation (mm)
gcad kernels                            # languages on the kernel search path
```

`part.mscad` and `part.gcsg` evaluate to the same geometry graph (`gcad eval --dump hash` prints the same hash). Every `minscad` example is a valid OpenSCAD file, and the test suite checks that OpenSCAD builds the same solid from it.

GCore at a glance: `(define name expr)`, `(define (f x (y 2)) body)`, `(module (name params) body)` where shape arguments arrive as `children`, `(fn (x) body)`, `(let ((a 1) (b (* a 2))) body)`, `(if c a b)`, `(for ((i (range 0 4))) body)` (inclusive, like OpenSCAD), named arguments as `:name value`, vectors as `[x y z]`, angles in degrees. Geometry: `box sphere cylinder polyhedron rect circle polygon translate rotate scale mirror matrix union difference intersection linear_extrude rotate_extrude color name`. Tessellation follows `$fn/$fa/$fs` when given, otherwise a tolerance of 0.1 % of the model's size. See the examples in [src/gcad/kernels/csg/examples](src/gcad/kernels/csg/examples).

Conventions follow OpenSCAD and were checked against OpenSCAD 2021.01 output: rotation order X→Y→Z, `rotate(a, v)`, `mirror`, clockwise faces in `polyhedron`, the profile plane of `rotate_extrude`, the direction of `linear_extrude` twist, and positional argument orders such as `cylinder(h, r1, r2, center)`. Twisted extrusions are triangulated along the other diagonal from OpenSCAD: both approximate the exact volume from opposite sides, and the difference shrinks as `slices` grows.

What `minscad` covers and what it does not yet is listed in [SPEC §5.2](docs/SPEC.md#52-kernels-a-language-defined-by-a-formal-grammar).

## Add a language

A kernel is a folder: `kernel.toml`, a Lark grammar `grammar.lark`, and `rewrite.toml`, which turns each grammar rule into GCore. No Python. The [kernel authoring guide](docs/kernel-authoring.md) builds one from scratch. Drop the folder into `./kernels`, your user kernel folder, or a folder on `GCAD_KERNEL_PATH`, then:

```bash
gcad kernel check path/to/kernel        # grammar, rewrite coverage, examples
gcad kernel describe path/to/kernel     # keywords and token classes for an editor
```

## Exports and units

Documents are in millimetres. STL and OBJ have no unit field (OBJ carries a comment). glTF is metres and Y-up by definition, so the vertex data stays in millimetres and the root node holds the 0.001 scale and Z-up to Y-up turn. Exports are deterministic: the same source gives byte-identical files.

## Development

```bash
pytest -q
ruff check . && ruff format --check . && mypy
```

CI runs the tests and a headless export on Linux, Windows and macOS, each on x86_64 and arm64.

## Licence

AGPL-3.0, see [LICENSE](LICENSE).
