"""The blockmesh kernel: OpenFOAM's blockMeshDict as a grammar kernel.

Geometry is checked three ways: against closed-form volumes; against golden numbers that
OpenFOAM's blockMesh produced in CI (the `blockmesh` job; provenance below); and, where
GCAD_BLOCKMESH names a blockMesh command, against blockMesh live, for the examples and for a
tutorial corpus when GCAD_FOAM_TUTORIALS names a folder of tutorials. GCAD_REQUIRE_BLOCKMESH
turns those skips into failures.

Tolerance policy. Volumes come from the graph's polyhedra in double precision (the Manifold
back-end stores vertices as float32, about 1e-7 relative, so the back-end is checked looser).
- Straight edges and plane faces: the kernel's solid and blockMesh's cells bound the same
  polyhedron, so volume and bounding box agree to 1e-9 relative.
- Curved edges: both are polygons inscribed in the curves. A circular arc cut into pieces of
  angle d loses the fraction 1 - sin(d)/d, about d^2/6, of the area between arc and chord,
  so each side is below the true volume by at most d^2/6 of it: the kernel uses d = 7.5
  degrees (0.29 %), blockMesh d = arc / cells. For the examples both are predicted exactly;
  for the tutorials, whose cell counts give d up to about 15 degrees (1.1 %), the bound is 2 %.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

import blockmesh_oracle as oracle
import numpy as np
import pytest

from gcad.backends import get_backend
from gcad.cli import main
from gcad.core.sexpr import read
from gcad.diagnostics import Severity
from gcad.kernels import BUNDLED, discover, kernel_for
from gcad.pipeline import evaluate_source

EXAMPLES = BUNDLED / "blockmesh" / "examples"
HEADER = "FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }\n"


def kernel():  # type: ignore[no-untyped-def]
    return discover()[0]["blockmesh"]


def evaluate(text: str, name: str = "system/blockMeshDict", extra_prelude: str = ""):  # type: ignore[no-untyped-def]
    k = kernel()
    if not extra_prelude:
        return evaluate_source(text, name, k).result
    from gcad.core.evaluator import evaluate as run

    return run(k.to_gcore(text, name), prelude=k.prelude() + read(extra_prelude))


def graph(text: str, **kw):  # type: ignore[no-untyped-def]
    result = evaluate(text, **kw)
    assert result.ok, [str(d) for d in result.diagnostics]
    return result.graph


def error(text: str):  # type: ignore[no-untyped-def]
    """The one error a dictionary gives, which must point at a line of it."""
    result = evaluate(text)
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    assert errors, "expected an error"
    assert errors[0].span is not None and errors[0].span.file == "system/blockMeshDict"
    return errors[0]


def line_of(text: str, fragment: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if fragment in line)


def solids(g):  # type: ignore[no-untyped-def]
    """[name, rgba, points (mm), faces, span] for each block, from the graph."""
    out = []
    for node in g.walk():
        if node.op == "name":
            color = node.children[0]
            poly = color.children[0]
            assert color.op == "color" and poly.op == "polyhedron"
            faces = [[int(i) for i in f] for f in poly.rows("faces")]
            out.append(
                (
                    node.arg("n"),
                    tuple(color.vec("rgba")),
                    np.array(poly.rows("points")),
                    faces,
                    poly.span,
                )
            )
    return out


def polyhedron_volume(points: np.ndarray, faces: list[list[int]]) -> float:
    """GCore lists faces clockwise seen from outside."""
    total = 0.0
    for f in faces:
        p = points[f[::-1]]
        for k in range(1, len(f) - 1):
            total += float(np.dot(p[0], np.cross(p[k], p[k + 1])))
    return total / 6.0


def measure(g):  # type: ignore[no-untyped-def]
    """Total volume and volume by block name in m^3, bounding box in m."""
    zones: dict[str, float] = {}
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for name, _, points, faces, _ in solids(g):
        zones[name] = zones.get(name, 0.0) + polyhedron_volume(points, faces) * 1e-9
        lo, hi = (
            np.minimum(lo, points.min(axis=0) / 1000),
            np.maximum(hi, points.max(axis=0) / 1000),
        )
    return sum(zones.values()), zones, lo, hi


def example(name: str) -> str:
    return (EXAMPLES / f"{name}.blockMeshDict").read_text(encoding="utf-8")


# ---------- closed-form geometry of the examples ----------

SIN = math.sin
ANNULUS = (0.05, 0.075, 0.1, 0.02)  # radii and height in metres


def annulus(pieces: int, r_in: float, r_out: float) -> float:
    """A quarter annulus with each arc cut into `pieces` chords: its exact volume."""
    h = ANNULUS[3]
    return h / 2 * (r_out**2 - r_in**2) * pieces * SIN(math.pi / 2 / pieces)


def wedge_volume(length: float, r0: float, r1: float) -> float:
    angle = math.radians(5)
    return 0.5 * SIN(angle) * length * (r0 * r0 + r0 * r1 + r1 * r1) / 3


EXPECTED = {
    "box": {"volume": 1e-4, "lo": [0, 0, 0], "hi": [0.1, 0.1, 0.01], "zones": {"block 0": 1e-4}},
    "lshape": {
        "volume": 2e-5,
        "lo": [0, 0, 0],
        "hi": [0.06, 0.06, 0.01],
        "zones": {"corner": 4e-6, "arms": 1.6e-5},
    },
    "wedge": {
        "volume": wedge_volume(0.1, 0.02, 0.02) + wedge_volume(0.05, 0.02, 0.01),
        "zones": {"pipe": wedge_volume(0.1, 0.02, 0.02), "nozzle": wedge_volume(0.05, 0.02, 0.01)},
    },
    # 45 degree arcs at 7.5 degrees a piece: 12 chords over the quarter.
    "annulus": {
        "volume": annulus(12, 0.05, 0.1),
        "zones": {"inner": annulus(12, 0.05, 0.075), "outer": annulus(12, 0.075, 0.1)},
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_examples_have_their_closed_form_volumes(name: str) -> None:
    volume, zones, lo, hi = measure(graph(example(name)))
    want = EXPECTED[name]
    assert volume == pytest.approx(want["volume"], rel=1e-9)
    assert zones == pytest.approx(want["zones"], rel=1e-9)
    if "lo" in want:
        assert lo == pytest.approx(want["lo"], abs=1e-12)
        assert hi == pytest.approx(want["hi"], abs=1e-12)


def test_the_mesh_back_end_agrees_and_unions_shared_curved_faces() -> None:
    """The annulus' two layers share a curved face; their union is one valid solid."""
    g = graph(example("annulus"))
    model = get_backend("manifold").realize(g, None)
    assert model.is_valid()
    assert model.volume() == pytest.approx(EXPECTED["annulus"]["volume"] * 1e9, rel=1e-6)


def test_curved_blocks_converge_to_the_true_solid() -> None:
    """Halving the angle per piece quarters the error, towards pi/4 (r1^2 - r0^2) h."""
    r0, _, r1, h = ANNULUS
    true = math.pi / 4 * (r1 * r1 - r0 * r0) * h
    errors = []
    for step in (15, 7.5, 3.75):
        volume = measure(graph(example("annulus"), extra_prelude=f"(define /arc-step {step})"))[0]
        assert volume == pytest.approx(annulus(round(90 / step), r0, r1), rel=1e-9)
        errors.append(true - volume)
    assert errors[0] > errors[1] > errors[2] > 0
    assert errors[0] / errors[1] == pytest.approx(4, rel=0.02)
    assert errors[1] / errors[2] == pytest.approx(4, rel=0.02)


# ---------- golden numbers from blockMesh ----------

# Produced by OpenFOAM's blockMesh in the CI job `blockmesh` (tests/blockmesh_oracle.py on the
# examples; volumes from the written polyMesh, in m^3 and m). Provenance: see GOLDEN_SOURCE.
GOLDEN_SOURCE = (
    "GitHub Actions run 40607401714, job blockmesh (ubuntu-24.04): Ubuntu package openfoam "
    "1912.200626-2build3, OpenFOAM v1912 patch 200626 (OpenFOAM.com)"
)
GOLDEN: dict[str, dict[str, object]] = {
    "box": {
        "volume": 0.00010000000000000005,
        "lo": [0.0, 0.0, 0.0],
        "hi": [0.1, 0.1, 0.010000000000000004],
    },
    "lshape": {
        "volume": 1.9999999999999998e-05,
        "lo": [0.0, 0.0, 0.0],
        "hi": [0.06, 0.06, 0.010000000000000002],
        "zones": {"corner": 4e-06, "arms": 1.6e-05},
    },
    "wedge": {
        "volume": 2.2515233543145078e-06,
        "lo": [0.0, 0.0, -0.0008723877473067202],
        "hi": [0.15000000000000002, 0.019980964431637202, 0.0008723877473067202],
        "zones": {"pipe": 1.7431148549531676e-06, "nozzle": 5.084084993613401e-07},
    },
    "annulus": {
        "volume": 0.00011768864359176749,
        "lo": [0.0, 0.0, 0.0],
        "hi": [0.1, 0.1, 0.020000000000000004],
        "zones": {"inner": 4.9036934829903215e-05, "outer": 6.865170876186425e-05},
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_examples_match_blockmesh_golden_numbers(name: str) -> None:
    if name not in GOLDEN:
        pytest.skip("no golden numbers recorded yet")
    gold = GOLDEN[name]
    volume, zones, lo, hi = measure(graph(example(name)))
    if name == "annulus":
        # blockMesh cuts each 45 degree arc into 10 cells: 20 chords over the quarter.
        assert gold["volume"] == pytest.approx(annulus(20, 0.05, 0.1), rel=1e-9)
        assert gold["zones"]["inner"] == pytest.approx(annulus(20, 0.05, 0.075), rel=1e-9)
        tolerance = (math.radians(7.5) ** 2 + math.radians(4.5) ** 2) / 6
        assert abs(volume - gold["volume"]) / volume <= tolerance
        assert zones == pytest.approx(gold["zones"], rel=tolerance)
        return
    assert volume == pytest.approx(gold["volume"], rel=1e-9)
    assert zones == pytest.approx(gold.get("zones") or {"block 0": gold["volume"]}, rel=1e-9)
    assert lo == pytest.approx(gold["lo"], rel=1e-9, abs=1e-12)
    assert hi == pytest.approx(gold["hi"], rel=1e-9, abs=1e-12)


# ---------- blockMesh live ----------


def blockmesh() -> list[str]:
    cmd = oracle.command()
    if not cmd:
        if os.environ.get("GCAD_REQUIRE_BLOCKMESH"):
            pytest.fail("this job needs GCAD_BLOCKMESH")
        pytest.skip("GCAD_BLOCKMESH does not name a blockMesh command")
    return cmd


def compare(text: str, mesh: oracle.Mesh, rel: float) -> None:
    volume, zones, lo, hi = measure(graph(text))
    size = float(np.linalg.norm(mesh.hi - mesh.lo))
    assert volume == pytest.approx(mesh.volume, rel=rel)
    assert lo == pytest.approx(mesh.lo, abs=rel * size)
    assert hi == pytest.approx(mesh.hi, abs=rel * size)
    if mesh.zones:
        assert {k: v for k, v in zones.items() if k in mesh.zones} == pytest.approx(
            mesh.zones, rel=rel
        )


@pytest.mark.parametrize("name", ["box", "lshape", "wedge"])
def test_examples_match_blockmesh_live(name: str, tmp_path: Path) -> None:
    cmd = blockmesh()
    compare(example(name), oracle.run(example(name), tmp_path, cmd), 1e-9)


def test_curved_example_matches_blockmesh_live(tmp_path: Path) -> None:
    mesh = oracle.run(example("annulus"), tmp_path, blockmesh())
    assert mesh.volume == pytest.approx(annulus(20, 0.05, 0.1), rel=1e-9)
    assert mesh.zones["inner"] == pytest.approx(annulus(20, 0.05, 0.075), rel=1e-9)
    compare(example("annulus"), mesh, (math.radians(7.5) ** 2 + math.radians(4.5) ** 2) / 6)


# Small dictionaries in the dialect of the OpenFOAM that CI installs (OpenFOAM.com).
# Each with the oldest OpenFOAM.com release seen to read it: v1912, the Ubuntu 24.04 and Debian
# package, does not know `arc ... origin` or `-$name` (it stops at "origin" and at "-").
LIVE_CASES = {
    "arc-origin": ("arc_origin", 2000),
    "negated-macros": ("negated", 2000),
    "graded-non-plane": ("twisted", 0),
}


@pytest.mark.parametrize("name", sorted(LIVE_CASES))
def test_features_match_blockmesh_live(name: str, tmp_path: Path) -> None:
    variable, newer_than = LIVE_CASES[name]
    text = globals()[variable.upper()]
    cmd = blockmesh()
    try:
        mesh = oracle.run(text, tmp_path, cmd)
    except oracle.Failed as exc:
        if 0 < exc.version < newer_than:
            pytest.skip(f"OpenFOAM v{exc.version} predates this syntax")
        raise
    curved = "arc" in text
    compare(text, mesh, (math.radians(7.5) ** 2 + math.radians(9) ** 2) / 6 if curved else 1e-9)


def test_blockmesh_rejects_what_the_kernel_rejects(tmp_path: Path) -> None:
    """An inside-out block: both refuse it."""
    cmd = blockmesh()
    with pytest.raises(RuntimeError, match="inside-out|inward"):
        oracle.run(INSIDE_OUT, tmp_path, cmd)


# ---------- the tutorial corpus ----------


def corpus_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("blockMeshDict*") if p.is_file() and not p.name.endswith(".m4")
    )


def plain_blocks(g) -> bool:  # type: ignore[no-untyped-def]
    """Every block straight and plane-faced: at most 8 corners and 6 faces each."""
    return all(len(points) <= 8 and len(faces) <= 6 for _, _, points, faces, _ in solids(g))


def check_one(path: Path, cmd: list[str]) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    row: dict[str, object] = {"file": str(path)}
    result = evaluate_source(text, str(path), kernel()).result
    errors = [d for d in result.diagnostics if d.severity is Severity.ERROR]
    if errors:
        row["kernel_error"] = errors[0].message
        row["kernel_error_has_span"] = errors[0].span is not None and errors[0].span.file == str(
            path
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            mesh = oracle.run(text, Path(tmp), cmd, zones=False)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            row["blockmesh_error"] = (
                str(exc).strip().splitlines()[-1][:200] if str(exc).strip() else "failed"
            )
            return row
    row["blockmesh_volume"] = mesh.volume
    if errors or result.graph is None:
        return row
    volume, _, lo, hi = measure(result.graph)
    size = float(np.linalg.norm(mesh.hi - mesh.lo))
    exact = plain_blocks(result.graph)
    rel = 1e-9 if exact else 2e-2
    row.update(
        kernel_volume=volume,
        exact=exact,
        volume_error=abs(volume - mesh.volume) / abs(mesh.volume),
        box_error=float(max(np.abs(lo - mesh.lo).max(), np.abs(hi - mesh.hi).max()) / size),
    )
    row["match"] = row["volume_error"] <= rel and row["box_error"] <= rel
    return row


def test_tutorial_corpus() -> None:
    folder = os.environ.get("GCAD_FOAM_TUTORIALS")
    if not folder:
        if os.environ.get("GCAD_REQUIRE_BLOCKMESH"):
            pytest.fail("this job needs GCAD_FOAM_TUTORIALS")
        pytest.skip("GCAD_FOAM_TUTORIALS does not name a folder of OpenFOAM tutorials")
    cmd = blockmesh()
    files = corpus_files(Path(folder))
    assert files, f"no blockMeshDict under {folder}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda p: check_one(p, cmd), files))
    summary = {
        "files": len(rows),
        "kernel_ok": sum("kernel_error" not in r for r in rows),
        "blockmesh_ok": sum("blockmesh_error" not in r for r in rows),
        "compared": sum("match" in r for r in rows),
        "matched": sum(r.get("match") is True for r in rows),
        "matched_exact": sum(r.get("match") is True and r.get("exact") is True for r in rows),
        "rejected_by_kernel_only": sum(
            "kernel_error" in r and "blockmesh_error" not in r for r in rows
        ),
        "accepted_by_kernel_only": sum(
            "kernel_error" not in r and "blockmesh_error" in r for r in rows
        ),
        "worst_curved_volume_error": max(
            (r["volume_error"] for r in rows if r.get("exact") is False), default=0.0
        ),
    }
    if report := os.environ.get("GCAD_BLOCKMESH_REPORT"):
        Path(report).write_text(json.dumps({"summary": summary, "files": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    mismatched = [r for r in rows if r.get("match") is False]
    spanless = [r for r in rows if "kernel_error" in r and not r["kernel_error_has_span"]]
    assert not mismatched, json.dumps(mismatched[:10], indent=1)
    assert not spanless, json.dumps(spanless[:10], indent=1)


# ---------- the language ----------


def box(header: str = "", v: str = "", edges: str = "", blocks: str = "") -> str:
    """A unit cube (OpenFOAM units) with optional extra entries."""
    vertices = v or "(0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"
    blocks = blocks or "hex (0 1 2 3 4 5 6 7) (1 1 1) simpleGrading (1 1 1)"
    return (
        f"{HEADER}{header}\nvertices ({vertices});\nblocks\n(\n    {blocks}\n);\n"
        f"edges ({edges});\nboundary ();\n"
    )


@pytest.mark.parametrize(
    ("entry", "mm"),
    [
        ("", 1000),
        ("scale 0.001;", 1),
        ("convertToMeters 0.01;", 10),
        ("units [mm];", 1),
        ("units [cm];", 10),
        ("s 0.5; scale $s;", 500),
    ],
)
def test_units_are_millimetres(entry: str, mm: float) -> None:
    volume, _, lo, hi = measure(graph(box(entry)))
    assert hi * 1000 == pytest.approx([mm] * 3, rel=1e-12)
    assert volume * 1e9 == pytest.approx(mm**3, rel=1e-12)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("scale 1; convertToMeters 1;", "only one of scale, convertToMeters and units"),
        ("scale -1;", "positive"),
        ("units [furlong];", "units [furlong] are not supported"),
        ('scale #calc "1.0/1000";', "computed by #calc"),
    ],
)
def test_bad_units_are_errors(entry: str, message: str) -> None:
    assert message in error(box(entry)).message


def test_macros_are_defines_and_references() -> None:
    text = box(
        "x 1; neg -1; d { a 2; b $a; inner { c 3; } }",
        "(0 0 0) ($x 0 0) ($x $x 0) (0 $x 0) "
        "(0 0 $d/a) ($x 0 $d/b) ($x $x $!d/inner/c) (0 $x #neg $neg)",
    )
    forms = " ".join(str(f) for f in kernel().to_gcore(text, "system/blockMeshDict"))
    assert "define" in forms
    corners = solids(graph(text))[0][2][:8] / 1000
    assert corners.tolist() == [
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
        [0, 0, 2],
        [1, 0, 2],
        [1, 1, 3],
        [0, 1, 1],
    ]


def test_a_sub_dictionary_entry_sees_its_siblings_first() -> None:
    """$a inside d finds d's own a, as OpenFOAM does, not the top-level one."""
    text = box(
        "a 5; d { a 1; b $a; }",
        "(0 0 0) ($d/b 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)",
    )
    assert solids(graph(text))[0][2][1][0] == 1000


def test_negated_macros_of_both_dialects() -> None:
    text = box(
        "w 1;", "(-$w 0 0) (0 0 0) (0 1 0) (#neg $w 1 0) (-$w 0 1) (0 0 1) (0 1 1) (-$w 1 1)"
    )
    volume, _, lo, _ = measure(graph(text))
    assert volume == pytest.approx(1.0, rel=1e-12) and lo[0] == pytest.approx(-1)


def test_named_vertices_and_blocks() -> None:
    text = box(
        v="name a (0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) name b (0 1 1)",
        blocks="name core hex (a 1 2 3 4 5 6 b) (2 2 2) simpleGrading (1 1 1)",
    )
    ((name, _, points, _, _),) = solids(graph(text))
    assert name == "core" and len(points) == 8


def test_keyword_lists_may_leave_out_their_semicolon() -> None:
    """blockMesh reads `boundary ( ... )` without the ";", as two v1912 tutorials write it."""
    text = box().replace(
        "boundary ();", "boundary\n(\n    walls { type wall; faces ((0 4 7 3)); }\n)\n"
    )
    text += "mergePatchPairs\n(\n)\n"
    assert graph(text).hash() == graph(box()).hash()
    assert graph(box().replace("));\nblocks", "))\nblocks")).hash() == graph(box()).hash()


def test_mesh_only_entries_change_nothing() -> None:
    plain = graph(box()).hash()
    meshy = box(
        "mergeType points; verbose false; #remove (x)",
        blocks="hex (0 1 2 3 4 5 6 7) 30 20 10 "
        "edgeGrading (1 2 3 4 1 1 1 1 ((0.5 0.5 2) (0.5 0.5 0.5)) 1 1 1)",
    )
    assert graph(meshy).hash() == plain
    assert graph(box("defaultPatch { type wall; } mergePatchPairs ();")).hash() == plain
    zoned = box(blocks="hex (0 1 2 3 4 5 6 7) fluid (5 5 5) simpleGrading (1 1 1)")
    assert graph(zoned).hash() != plain and measure(graph(zoned))[1] == {
        "fluid": pytest.approx(1.0)
    }


def test_zones_name_and_colour_the_parts() -> None:
    parts = solids(graph(example("lshape")))
    names = [p[0] for p in parts]
    colours = {p[0]: p[1] for p in parts}
    assert sorted(names) == ["arms", "arms", "corner"]
    assert len({p[1] for p in parts}) == 2  # one colour per zone
    assert colours == {p[0]: p[1] for p in solids(graph(example("lshape")))}  # stable
    text = example("lshape")
    hex_line = line_of(text, "hex (0 1 4 3")
    corner = next(p for p in parts if p[0] == "corner")
    assert corner[4].line == hex_line  # click-to-source lands on the block's line


# A quarter of a unit disc, 1 high: the origin, (1 0), the 45 degree point and (0 1), with
# two 45 degree arcs, each cut into 6 chords.
DISC = "(0 0 0) (1 0 0) (0.707106781186548 0.707106781186548 0) (0 1 0) " + (
    "(0 0 1) (1 0 1) (0.707106781186548 0.707106781186548 1) (0 1 1)"
)
DISC_ARCS = {
    "mid": "arc 1 2 (0.923879532511287 0.382683432365090 {z}) "
    "arc 2 3 (0.382683432365090 0.923879532511287 {z})",
    "origin": "arc 1 2 origin (0 0 {z}) arc 2 3 origin (0 0 {z})",
    "angle": "arc 1 2 45 (0 0 1) arc 2 3 45 (0 0 1)",
}


def disc(arcs: str) -> str:
    top = arcs.format(z=1).replace("arc 1 2", "arc 5 6").replace("arc 2 3", "arc 6 7")
    return box(v=DISC, edges=arcs.format(z=0) + " " + top)


@pytest.mark.parametrize("form", sorted(DISC_ARCS))
def test_three_arc_forms_give_one_arc(form: str) -> None:
    volume = measure(graph(disc(DISC_ARCS[form])))[0]
    assert volume == pytest.approx(0.5 * 12 * SIN(math.pi / 2 / 12), rel=1e-9)


def esi_arc(p1: np.ndarray, p3: np.ndarray, c: np.ndarray, factor: float, t: float) -> np.ndarray:
    """OpenFOAM.com arcEdge::calcFromCentre and position(t), restated in numpy."""
    r1, r3, chord = p1 - c, p3 - c, p3 - p1
    m1, m3 = np.linalg.norm(r1), np.linalg.norm(r3)
    radius = 0.5 * (m1 + m3)
    if m1 != m3 or factor != 1:
        if factor != 1:
            radius = max(radius * factor, 1.001 * 0.5 * np.linalg.norm(chord))
        n = np.cross(np.cross(r1, r3), chord)
        c = 0.5 * (p1 + p3) + math.sqrt(radius**2 - 0.25 * chord @ chord) * n / np.linalg.norm(n)
        r1, r3 = p1 - c, p3 - c
        radius = 0.5 * (np.linalg.norm(r1) + np.linalg.norm(r3))
    angle = math.acos(r1 @ r3 / (np.linalg.norm(r1) * np.linalg.norm(r3)))
    e1 = r1 / np.linalg.norm(r1)
    axis = np.cross(r1, r3)
    e2 = np.cross(axis / np.linalg.norm(axis), e1)
    return c + radius * (math.cos(t * angle) * e1 + math.sin(t * angle) * e2)


@pytest.mark.parametrize(("centre", "factor"), [("0.05 0", 1), ("0 0", 1.5), ("0.05 -0.02", 2)])
def test_arc_origin_moves_an_uneven_centre_as_openfoam_com_does(centre: str, factor: float) -> None:
    f = "" if factor == 1 else f"{factor} "
    text = box(v=DISC, edges=f"arc 1 2 origin {f}({centre} 0)")
    points = solids(graph(text))[0][2] / 1000
    # 8 + 12 (n - 1) + 6 (n - 1)^2 + 6 n^2 points: corners, edges, faces, quad centres
    n = next(
        k for k in range(2, 40) if 8 + 12 * (k - 1) + 6 * (k - 1) ** 2 + 6 * k * k == len(points)
    )
    c = np.array([float(x) for x in centre.split()] + [0.0])
    p1, p3 = np.array([1.0, 0, 0]), np.array([0.707106781186548] * 2 + [0.0])
    first = 8 + 5 * (n - 1)  # inner points of block edge 5, vertex 1 -> 2
    for k in range(1, n):
        assert points[first + k - 1] == pytest.approx(esi_arc(p1, p3, c, factor, k / n), abs=1e-12)


def test_polyline_edges() -> None:
    v = "(0 0 0) (2 0 0) (2 1 0) (0 1 0) (0 0 1) (2 0 1) (2 1 1) (0 1 1)"
    # a roof over the top face: the y-max edges kink up by 0.5 in the middle
    text = box(v=v, edges="polyLine 3 2 ((1 1.5 0)) polyLine 7 6 ((1 1.5 1))")
    assert measure(graph(text))[0] == pytest.approx(2.5, rel=1e-9)


def test_line_edges_are_straight() -> None:
    assert measure(graph(box(edges="line 0 1 line 2 3")))[0] == pytest.approx(1.0, rel=1e-12)


def test_a_face_that_is_not_plane_bounds_the_bilinear_volume() -> None:
    """One corner raised by t: the bilinear top surface encloses 1 + t/4."""
    text = TWISTED
    volume = measure(graph(text))[0]
    assert volume == pytest.approx(1.25, rel=1e-12)


def test_collapsed_vertices_make_prisms() -> None:
    v = "(0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1)"
    text = box(v=v, blocks="hex (0 1 2 3 4 5 5 4) (1 1 1) simpleGrading (1 1 1)")
    ((_, _, points, faces, _),) = solids(graph(text))
    assert len(points) == 6 and sorted(len(f) for f in faces) == [3, 3, 4, 4, 4]
    assert measure(graph(text))[0] == pytest.approx(0.5, rel=1e-12)


# ---------- what is reported, and where ----------

INSIDE_OUT = box(blocks="hex (4 5 6 7 0 1 2 3) (1 1 1) simpleGrading (1 1 1)")

UNSUPPORTED = {
    "inside-out": (INSIDE_OUT, "hex (4 5 6 7", "inside-out"),
    "undefined macro": (
        box(v="(0 0 0) ($x 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"),
        "($x 0 0)",
        "'x' is not defined",
    ),
    "missing vertex": (
        box(blocks="hex (0 1 2 3 4 5 6 9) (1 1 1) simpleGrading (1 1 1)"),
        "hex (0",
        "vertex 9 does not exist",
    ),
    "unknown name": (
        box(blocks="hex (0 1 2 3 4 5 6 v9) (1 1 1) simpleGrading (1 1 1)"),
        "hex (0",
        "no vertex is named v9",
    ),
    "#calc coordinate": (
        box('x #calc "1+1";', "(0 0 0) ($x 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"),
        "($x 0 0)",
        "computed by #calc",
    ),
    "#eval coordinate": (
        box("x #eval{ 2*3 };", "(0 0 0) ($x 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"),
        "($x 0 0)",
        "computed by #eval",
    ),
    "#codeStream": (
        box("#codeStream { code #{ os << 1; #}; }"),
        "#codeStream",
        "#codeStream is not supported",
    ),
    "#include": (box('#include "other"'), "#include", "#include is not supported"),
    "#ifeq": (box("#ifeq $a b\n#endif"), "#ifeq", "#if, #ifeq"),
    "spline": (
        box(edges="spline 0 1 ((0.5 0.1 0))"),
        "spline 0 1",
        "spline edges are not supported",
    ),
    "BSpline": (
        box(edges="BSpline 0 1 ((0.5 0.1 0))"),
        "BSpline 0 1",
        "BSpline edges are not supported",
    ),
    "projected vertex": (
        box(v="project (0 0 0) (sphere) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"),
        "project",
        "projected vertices",
    ),
    "projected edge": (box(edges="project 0 1 (sphere)"), "project 0 1", "projected edges"),
    "projected faces": (box("faces ( project (0 4 7 3) sphere );"), "project (0 4", "curved faces"),
    "transform": (box("transform { origin (0 0 0); }"), "transform", "transform is not supported"),
    "collinear arc": (box(edges="arc 0 1 (0.5 0 0)"), "arc 0 1", "lie on a line"),
    "wrong vertex": (
        box(v="(0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1)"),
        "(0 0) (1",
        "three numbers",
    ),
    "curved and collapsed": (
        box(
            v="(0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1)",
            blocks="hex (0 1 2 3 4 5 5 4) (1 1 1) simpleGrading (1 1 1)",
            edges="arc 0 1 (0.5 -0.2 0)",
        ),
        "hex (0 1 2 3 4 5 5 4)",
        "collapsed vertices",
    ),
}


@pytest.mark.parametrize("case", sorted(UNSUPPORTED))
def test_unsupported_features_are_errors_at_their_line(case: str) -> None:
    text, fragment, message = UNSUPPORTED[case]
    diagnostic = error(text)
    assert message in diagnostic.message
    assert diagnostic.span.line == line_of(text, fragment)


# ---------- files, the studio and the editor ----------


def test_the_case_file_name_picks_the_kernel(tmp_path: Path) -> None:
    case = tmp_path / "cavity" / "system"
    case.mkdir(parents=True)
    (case / "blockMeshDict").write_text(example("box"), encoding="utf-8")
    (case / "blockMeshDict.orig").write_text(example("box"), encoding="utf-8")
    assert kernel_for(case / "blockMeshDict").id == "blockmesh"
    assert kernel_for(Path("pipe.blockMeshDict")).id == "blockmesh"
    with pytest.raises(Exception, match="no kernel handles"):
        kernel_for(case / "blockMeshDict.orig")
    assert main(["export", str(case / "blockMeshDict"), "-o", str(tmp_path / "box.stl")]) == 0

    from gcad.server import Studio

    studio = Studio(tmp_path)
    files = {f["path"]: f["kernel"] for f in studio.rpc_file_list()["files"]}
    assert files == {"cavity/system/blockMeshDict": "blockmesh"}
    opened = studio.rpc_file_open("cavity/system/blockMeshDict")
    answer = studio.rpc_document_evaluate(kernel=opened["kernel"], source=opened["text"])
    assert answer["ok"] and answer["volume"] == pytest.approx(1e5)


def test_describe_gives_the_editor_its_highlighting(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kernel", "describe", "blockmesh"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["filenames"] == ["blockMeshDict"] and info["extensions"] == [".blockmeshdict"]
    assert info["comment"] == "//"
    for word in (
        "vertices",
        "blocks",
        "edges",
        "hex",
        "arc",
        "polyLine",
        "scale",
        "convertToMeters",
    ):
        assert word in info["highlight"]["keywords"]
    assert len(info["highlight"]["patterns"]["comment"]) == 2  # // and /* */


def test_kernel_check_runs_the_examples() -> None:
    assert main(["kernel", "check", str(BUNDLED / "blockmesh")]) == 0


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("OCP"), reason="OpenCASCADE is not installed here"
)
def test_straight_blocks_export_as_exact_step(tmp_path: Path) -> None:
    out = tmp_path / "lshape.step"
    assert main(["export", str(EXAMPLES / "lshape.blockMeshDict"), "-o", str(out)]) == 0
    assert out.stat().st_size > 1000


# Dictionaries used above and in the live comparisons; they are in OpenFOAM.com's dialect
# because CI's blockMesh comes from it.
ARC_ORIGIN = box(
    "scale 0.1;",
    DISC,
    "arc 1 2 origin 1.2 (0.05 0 0) arc 2 3 origin (0 0 0) "
    "arc 5 6 origin 1.2 (0.05 0 1) arc 6 7 origin (0 0 1)",
    "hex (0 1 2 3 4 5 6 7) (10 10 2) simpleGrading (1 1 1)",
)
NEGATED = box(
    "w 0.5;",
    "(-$w -$w 0) ($w -$w 0) ($w $w 0) (-$w $w 0) (-$w -$w 1) ($w -$w 1) ($w $w 1) (-$w $w 1)",
)
TWISTED = box(
    "",
    "(0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 2) (0 1 1)",
    "",
    "hex (0 1 2 3 4 5 6 7) (7 5 3) simpleGrading (2 0.5 3)",
)
