"""The elmergrid kernel: ElmerGrid's .grd format, MATC included, as grammar and rewrite data only.

The geometry is checked against ElmerGrid itself. Golden numbers below were made with
`ElmerGrid 1 2 case.grd`, which writes case/mesh.{nodes,elements}: a body's area (2D) or
volume (3D) is the sum over its elements, and its bounds are the extent of their nodes.

- Files without MATC: ElmerGrid "26.1-revive-legacy-tools (Rev: b636c4d33)", built from the
  Elmer sources of 2026-02-11 without MATC.
- Files with MATC: ElmerGrid built from Elmer commit 2694bc075 with ELMERGRID_WITH_MATC=ON
  (its version string is "matc-check").

Optional live checks rerun ElmerGrid when GCAD_ELMERGRID (and, for MATC, GCAD_ELMERGRID_MATC)
names the executable, and sweep a folder of .grd files when GCAD_ELMER_CORPUS names one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gcad.backends import get_backend
from gcad.cli import main
from gcad.core.graph import Graph, Node
from gcad.diagnostics import GcadError, Severity
from gcad.kernels import BUNDLED, discover
from gcad.pipeline import Evaluated, evaluate_source

FOLDER = BUNDLED / "elmergrid"
EXAMPLES_DIR = FOLDER / "examples"
EXAMPLES = ["plate", "board", "centred", "stack", "channel"]


def kernel():  # type: ignore[no-untyped-def]
    return discover()[0]["elmergrid"]


def evaluate(text: str, name: str = "t.grd") -> Evaluated:
    return evaluate_source(text, name, kernel())


def errors(evaluated: Evaluated) -> list[str]:
    return [str(d) for d in evaluated.result.diagnostics if d.severity is Severity.ERROR]


def materials(graph: Graph) -> dict[int, Node]:
    out = {}
    for node in graph.walk():
        label = str(node.arg("n", ""))
        if node.op == "name" and label.startswith("material "):
            out[int(float(label.split()[1]))] = node
    return out


def exact(node: Node, offset: tuple[float, float, float] = (0.0, 0.0, 0.0)):  # type: ignore[no-untyped-def]
    """Area or volume and bounds summed over the graph's rectangles and extrusions, in double
    precision: the cells of one material never overlap, so their measures add."""
    if node.op == "rect":
        w, h = node.vec("size")
        return w * h, [offset[0], offset[1], 0.0], [offset[0] + w, offset[1] + h, 0.0]
    parts = []
    if node.op == "translate":
        v = node.vec("v") + [0.0]
        offset = (offset[0] + v[0], offset[1] + v[1], offset[2] + v[2])
    if node.op == "linear_extrude":
        height = node.num("height")
        for child in node.children:
            a, lo, hi = exact(child, (offset[0], offset[1], 0.0))
            parts.append(
                (a * height, [lo[0], lo[1], offset[2]], [hi[0], hi[1], offset[2] + height])
            )
    else:
        parts = [exact(child, offset) for child in node.children]
    return (
        sum(p[0] for p in parts),
        [min(p[1][i] for p in parts) for i in range(3)],
        [max(p[2][i] for p in parts) for i in range(3)],
    )


def assert_matches(graph: Graph, bodies: dict[int, list], rel: float = 1e-12) -> None:  # type: ignore[type-arg]
    found = materials(graph)
    assert sorted(found) == sorted(bodies)
    for m, (measure, lo, hi) in bodies.items():
        got, glo, ghi = exact(found[m])
        assert got == pytest.approx(measure, rel=rel), m
        assert glo == pytest.approx(lo, rel=rel, abs=1e-12), m
        assert ghi == pytest.approx(hi, rel=rel, abs=1e-12), m


def occt():  # type: ignore[no-untyped-def]
    try:
        return get_backend("occt")
    except GcadError:
        return None


# --- the kernel is data -------------------------------------------------------------------


def test_kernel_is_data_only_and_passes_check() -> None:
    files = sorted(p.name for p in FOLDER.iterdir() if p.is_file())
    assert files == ["grammar.lark", "kernel.toml", "prelude.gcore", "rewrite.toml"]
    assert sorted(p.stem for p in EXAMPLES_DIR.iterdir()) == sorted(EXAMPLES)
    assert main(["kernel", "check", str(FOLDER)]) == 0


def test_describe_lists_keywords(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kernel", "describe", "elmergrid"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert {"Subcell", "Divisions", "Material", "Structure", "End", "Extruded"} <= set(
        info["highlight"]["keywords"]
    )
    assert info["comment"] == "!"
    assert info["extensions"] == [".grd"]


def test_keywords_are_case_insensitive() -> None:
    text = (EXAMPLES_DIR / "board.grd").read_text()
    lines = [ln.upper() if not ln.startswith("!") else ln for ln in text.splitlines()]
    shouted = evaluate("\n".join(lines))
    plain = evaluate(text)
    assert not errors(shouted) and not errors(plain)
    assert shouted.result.graph is not None and plain.result.graph is not None
    assert shouted.result.graph.hash() == plain.result.graph.hash()


# --- geometry against ElmerGrid --------------------------------------------------------------

# Per body: area or volume, lower and upper corner. Generated with ElmerGrid (see the top).
GOLDEN: dict[str, dict[int, list]] = {  # type: ignore[type-arg]
    "plate": {
        1: [10000.0, [0.0, 0.0, 0.0], [200.0, 50.0, 0.0]],
    },
    "board": {
        1: [80.0000000000002, [0.0, 0.0, 0.0], [50.0, 1.6, 0.0]],
        2: [48.60000000000001, [8.0, 1.6, 0.0], [41.0, 3.4, 0.0]],
        3: [52.79999999999999, [8.0, 3.4, 0.0], [41.0, 5.0, 0.0]],
    },
    "centred": {
        1: [144.00000000000293, [-15.0, -1.5, 0.0], [15.0, 10.5, 0.0]],
    },
    "stack": {
        1: [162.0, [0.0, 0.0, 0.0], [12.0, 10.0, 1.5]],
        2: [18.0, [3.0, 2.0, 1.0], [9.0, 8.0, 1.5]],
        3: [126.0, [0.0, 0.0, 1.5], [12.0, 10.0, 3.0]],
    },
    "channel": {
        1: [90.0, [0.0, 0.0, 0.0], [7.0, 30.0, 0.0]],
        2: [80.0, [1.5, 10.0, 0.0], [5.5, 30.0, 0.0]],
        3: [40.0, [1.5, 0.0, 0.0], [5.5, 10.0, 0.0]],
    },
}


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_match_elmergrid(name: str) -> None:
    evaluated = evaluate((EXAMPLES_DIR / f"{name}.grd").read_text(), f"{name}.grd")
    assert not errors(evaluated), errors(evaluated)
    assert evaluated.result.graph is not None
    assert_matches(evaluated.result.graph, GOLDEN[name])


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_realize_with_the_same_measure(name: str) -> None:
    """The back-ends' union of the cells has the measure ElmerGrid's mesh has, so no cells
    overlap or leave gaps. OCCT works in double precision; Manifold's binding in float32."""
    graph = evaluate((EXAMPLES_DIR / f"{name}.grd").read_text()).result.graph
    assert graph is not None
    for backend, rel in ((occt(), 1e-9), (get_backend("manifold"), 1e-6)):
        if backend is None:
            continue
        for m, node in materials(graph).items():
            model = backend.realize(Graph(node))
            measure = model.volume() if node.dim == 3 else model.area()
            assert measure == pytest.approx(GOLDEN[name][m][0], rel=rel), (backend.name, m)


ORIENTATION = """Version = 210903
Coordinate System = Cartesian 2D
Subcell Divisions in 2D = 2 3
Subcell Limits 1 = 0 1 3
Subcell Limits 2 = 0 1 2 4
Material Structure in 2D
  1 0
  2 2
  3 0
End
Materials Interval = 1 3
"""
GOLDEN_ORIENTATION: dict[int, list] = {
    1: [2.0, [0.0, 2.0, 0.0], [1.0, 4.0, 0.0]],
    2: [3.0, [0.0, 1.0, 0.0], [3.0, 2.0, 0.0]],
    3: [1.0, [0.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
}  # type: ignore[type-arg]


def test_first_matrix_row_is_the_top_row_of_cells() -> None:
    graph = evaluate(ORIENTATION).result.graph
    assert graph is not None
    assert_matches(graph, GOLDEN_ORIENTATION)
    # ElmerGrid put material 3, the last row, at the bottom (y from 0 to 1).
    assert GOLDEN_ORIENTATION[3][1][1] == 0 and GOLDEN_ORIENTATION[1][2][1] == 4


def test_mesh_only_keywords_leave_the_geometry_alone() -> None:
    text = (EXAMPLES_DIR / "board.grd").read_text()
    extra = (
        "Element Divisions 1 = 5 5 5 5 5\nElement Ratios 2 = 1 2 1\nTriangles = True\n"
        "Element Degree = 2\nReference Density = 0.1\nDecimals = 8\nMerge Nodes = 1e-6\n"
        "Coordinate Ratios = 1\nNumbering = Vertical\nPartition = 2 2\nUnite = True\n"
        "Material Boundary\n  1 2 3\nEnd\n"
    )
    a, b = evaluate(text).result.graph, evaluate(text + extra).result.graph
    assert a is not None and b is not None
    assert a.hash() == b.hash()


@pytest.mark.parametrize(
    "line, message",
    [
        ("Geometry Mappings\n  1 0 1 1 2 0 0.1 1 0.1\nEnd", "Geometry Mappings"),
        ("Revolve Blocks = 2", "Revolve"),
        ("Polar Radius = 2", "Polar Radius moves the mesh coordinates"),
        ("Scale = 2 2", "Scale moves the mesh coordinates"),
        ("Translate = 1 0", "Translate moves"),
        ("Rotate Mesh = 0 0 90", "Rotate Mesh moves"),
        ("Mirror = 1 0", "Mirror moves"),
        ("Clone = 2 1", "Clone moves"),
        ("Start New Mesh", "Start New Mesh"),
        ("Coordinate Mapping = 2 1 3", "Coordinate Mapping"),
        ("Boundary Layer\n  1 2 0.1 1 1\nEnd", "Boundary Layer"),
        ("Extruded Helicity = 90", "Extruded Helicity"),
        ("Subcel Limits 1 = 0 1", "unknown ElmerGrid keyword: Subcel Limits 1"),
    ],
)
def test_unsupported_geometry_keywords_are_errors_at_their_line(line: str, message: str) -> None:
    text = ORIENTATION + line + "\n"
    evaluated = evaluate(text)
    assert evaluated.result.graph is None or errors(evaluated)
    first = errors(evaluated)[0]
    assert message in first, first
    assert first.startswith(f"t.grd:{ORIENTATION.count(chr(10)) + 1}:")


@pytest.mark.parametrize(
    "system, message",
    [("Cartesian 1D", "no 1D shapes"), ("Polar", "Polar is not supported")],
)
def test_unsupported_coordinate_systems_are_errors(system: str, message: str) -> None:
    text = ORIENTATION.replace("Cartesian 2D", system)
    first = errors(evaluate(text))[0]
    assert message in first and first.startswith("t.grd:2:"), first


def test_axisymmetric_and_unrecognised_systems_are_2d_grids_like_elmergrid() -> None:
    plain = evaluate(ORIENTATION).result.graph
    for system in ("Axisymmetric", "2D Cartesian"):
        other = evaluate(ORIENTATION.replace("Cartesian 2D", system)).result.graph
        assert plain is not None and other is not None
        assert other.hash() == plain.hash()


def test_scaling_is_not_an_elmergrid_keyword() -> None:
    evaluated = evaluate(ORIENTATION + "Scaling = 0.001\n")
    assert not errors(evaluated)
    assert any("does not read Scaling" in str(d) for d in evaluated.result.diagnostics)
    assert evaluated.result.graph is not None
    assert_matches(evaluated.result.graph, GOLDEN_ORIENTATION)


# --- MATC ------------------------------------------------------------------------------------


def matc_case(pre: str, limits: str, matc: bool = True, mid: str = "") -> str:
    """A 2D grid whose x cells are materials 1..n, so their bounds show the limits MATC gave."""
    n = int(limits.split("|")[0])
    text = limits.split("|", 1)[1]
    head = "Version = 210903\n" + ("MATC = True\n" if matc else "") + (pre + "\n" if pre else "")
    line = f"Subcell Limits 1{text}" if text.startswith("\n") else f"Subcell Limits 1 = {text}"
    return (
        head + f"Coordinate System = Cartesian 2D\nSubcell Divisions in 2D = {n} 1\n"
        f"{line}\n{mid + chr(10) if mid else ''}Subcell Limits 2 = 0 1\n"
        f"Material Structure in 2D\n  {' '.join(str(i + 1) for i in range(n))}\nEnd\n"
        f"Materials Interval = 1 {n}\n"
    )


# name -> (lines before the grid, "n|limits"); the limits MATC computes for n cells.
MATC_CASES = {
    "row_before_plus": ("", "3|$ 1+1 3 4 6"),
    "row_before_times": ("$ a = 2", "2|$ 1 a 3*a"),
    "minus_before_operand_negates": ("$ a = 2", "2|$ -a 0 a"),
    "spaced_minus_subtracts": ("$ a = 2", "1|$ 0 a - 1"),
    "unspaced_minus_subtracts": ("$ a = 2", "1|$ 0 a-1"),
    "parentheses": ("$ t = 1.5\n$ w = 4", "3|$ 0 t (t+w) (2*t+w)"),
    "functions_in_radians": ("", "3|$ 0 sin(pi/6) sqrt(2) (1+cos(0))"),
    "twelve_digits": ("$ t = 1/3", "2|$ 0 t 1"),
    "statements": ("$ a = 1; b = 2", "2|$ 0 a b"),
    "comments": ("$ a = 1 ! a note", "2|$ 0 a 2 ! a note"),
    "after_plain_numbers": ("$ a = 2", "2|0 $ a 3"),
    "next_line": ("$ a = 4", "2|\n$ 0 1 a"),
    "indexing_from_zero": ("$ v = 0 1.5 3", "2|$ v(0) v(1) v(2)"),
    "row_arithmetic": ("$ v = 0 1 2", "2|$ v*2+1"),
    "range": ("", "3|$ 0:3"),
    "d_exponent": ("", "2|$ 0 1d0 2.5D0"),
    "logarithms": ("", "3|$ 0 log(10) ln(exp(2)) 3"),
    "comparison": ("", "2|$ 0 (2>1) 2"),
    "reductions": ("$ v = 3 1 2", "3|$ 0 min(v) max(v) sum(v)"),
    "atan2_and_mod": ("", "2|$ 0 atan2(1,1) mod(7,3)"),
}
GOLDEN_MATC: dict[str, list[float]] = {
    "row_before_plus": [2.0, 4.0, 5.0, 7.0],
    "row_before_times": [2.0, 4.0, 6.0],
    "minus_before_operand_negates": [-2.0, 0.0, 2.0],
    "spaced_minus_subtracts": [-1.0, 1.0],
    "unspaced_minus_subtracts": [-1.0, 1.0],
    "parentheses": [0.0, 1.5, 5.5, 7.0],
    "functions_in_radians": [0.0, 0.5, 1.41421356237, 2.0],
    "twelve_digits": [0.0, 0.333333333333, 1.0],
    "statements": [0.0, 1.0, 2.0],
    "comments": [0.0, 1.0, 2.0],
    "after_plain_numbers": [0.0, 2.0, 3.0],
    "next_line": [0.0, 1.0, 4.0],
    "indexing_from_zero": [0.0, 1.5, 3.0],
    "row_arithmetic": [1.0, 3.0, 5.0],
    "range": [0.0, 1.0, 2.0, 3.0],
    "d_exponent": [0.0, 1.0, 2.5],
    "logarithms": [0.0, 1.0, 2.0, 3.0],
    "comparison": [0.0, 1.0, 2.0],
    "reductions": [0.0, 1.0, 3.0, 6.0],
    "atan2_and_mod": [0.0, 0.785398163397, 1.0],
}


@pytest.mark.parametrize("name", sorted(MATC_CASES))
def test_matc_values_match_elmergrid(name: str) -> None:
    pre, limits = MATC_CASES[name]
    evaluated = evaluate(matc_case(pre, limits))
    assert not errors(evaluated), errors(evaluated)
    assert evaluated.result.graph is not None
    found = materials(evaluated.result.graph)
    xs = [exact(found[m])[1][0] for m in sorted(found)] + [exact(found[max(found)])[2][0]]
    assert xs == pytest.approx(GOLDEN_MATC[name], rel=1e-12, abs=1e-12)


# Lines where ElmerGrid would not make the intended grid: it stops, or MATC's error text or a
# lost line leaves zeros. The kernel reports an error at the line instead. (ElmerGrid's own
# result for each was checked once; the live test below checks it again.)
MATC_ERRORS = {
    "more_than_four_numbers": (matc_case("", "4|$ 0:4"), 5, "ElmerGrid reads at most 4"),
    "row_power": (matc_case("", "2|$ 0 -2^2 9"), 5, "Matrix ^ Matrix"),
    "two_argument_max": (matc_case("", "2|$ 0 max(1,2) 3"), 5, "max takes one argument"),
    "undeclared": (matc_case("", "2|$ 0 1 zz"), 5, "undeclared identifier zz"),
    "matc_off": (matc_case("", "2|$ 0 1 2", matc=False), 4, "needs MATC = True"),
    "line_after_matc_only_line": (
        matc_case("", "2|$ 0 1 2", mid="$ q = 5"),
        7,
        "ElmerGrid does not read this line",
    ),
    "line_after_star_line": (
        matc_case("", "2|$ 0 1 2", mid="**** a note"),
        7,
        "ElmerGrid does not read this line",
    ),
}


@pytest.mark.parametrize("name", sorted(MATC_ERRORS))
def test_matc_that_elmergrid_misreads_is_an_error(name: str) -> None:
    text, line, message = MATC_ERRORS[name]
    first = errors(evaluate(text))[0]
    assert message in first and first.startswith(f"t.grd:{line}:"), first


def test_matc_assignments_before_the_first_value_are_fine() -> None:
    evaluated = evaluate(matc_case("$ a = 1\n$ b = 2", "2|$ 0 a b"))
    assert not errors(evaluated)


# --- live ElmerGrid -------------------------------------------------------------------------


def run_elmergrid(exe: str, text: str, work: Path) -> dict[int, list] | None:  # type: ignore[type-arg]
    """Runs ElmerGrid on text; returns body -> [measure, lo, hi], or None if it wrote no mesh."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "case.grd").write_text(text)
    subprocess.run([exe, "1", "2", "case.grd"], cwd=work, capture_output=True, timeout=300)
    mesh = work / "case"
    if not (mesh / "mesh.nodes").is_file():
        return None
    nodes = {}
    for line in (mesh / "mesh.nodes").read_text().splitlines():
        f = line.split()
        if f:
            nodes[int(f[0])] = [float(v) for v in f[2:5]]
    bodies: dict[int, list] = {}  # type: ignore[type-arg]
    for line in (mesh / "mesh.elements").read_text().splitlines():
        f = line.split()
        if not f:
            continue
        body, family = int(f[1]), int(f[2]) // 100
        pts = [nodes[int(n)] for n in f[3:]]
        b = bodies.setdefault(body, [0.0, [1e300] * 3, [-1e300] * 3])
        b[0] += element_measure(family, pts)
        for p in pts:
            b[1] = [min(a, c) for a, c in zip(b[1], p, strict=True)]
            b[2] = [max(a, c) for a, c in zip(b[2], p, strict=True)]
    shutil.rmtree(mesh, ignore_errors=True)
    return bodies


def element_measure(family: int, p: list[list[float]]) -> float:
    def tet(a: list[float], b: list[float], c: list[float], d: list[float]) -> float:
        u, v, w = ([q[i] - a[i] for i in range(3)] for q in (b, c, d))
        return (
            u[0] * (v[1] * w[2] - v[2] * w[1])
            - u[1] * (v[0] * w[2] - v[2] * w[0])
            + u[2] * (v[0] * w[1] - v[1] * w[0])
        ) / 6

    if family == 3:
        return (
            abs(
                (p[1][0] - p[0][0]) * (p[2][1] - p[0][1])
                - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])
            )
            / 2
        )
    if family == 4:
        return (
            abs(sum(p[i][0] * p[(i + 1) % 4][1] - p[(i + 1) % 4][0] * p[i][1] for i in range(4)))
            / 2
        )
    if family == 8:  # hexahedron: five tetrahedra, exact for planar faces
        tets = [(0, 1, 3, 4), (1, 2, 3, 6), (1, 3, 4, 6), (3, 4, 6, 7), (1, 4, 5, 6)]
        return abs(sum(tet(p[a], p[b], p[c], p[d]) for a, b, c, d in tets))
    if family == 7:  # prism
        return abs(
            tet(p[0], p[1], p[2], p[3]) + tet(p[1], p[2], p[3], p[4]) + tet(p[2], p[3], p[4], p[5])
        )
    raise ValueError(f"element family {family}")


ELMERGRID = os.environ.get("GCAD_ELMERGRID")
ELMERGRID_MATC = os.environ.get("GCAD_ELMERGRID_MATC")


@pytest.mark.skipif(not ELMERGRID, reason="GCAD_ELMERGRID is not set")
@pytest.mark.parametrize("name", [*EXAMPLES, "orientation"])
def test_live_elmergrid_agrees(name: str, tmp_path: Path) -> None:
    text = ORIENTATION if name == "orientation" else (EXAMPLES_DIR / f"{name}.grd").read_text()
    exe = ELMERGRID_MATC if "MATC = True" in text else ELMERGRID
    if not exe:
        pytest.skip("GCAD_ELMERGRID_MATC is not set")
    bodies = run_elmergrid(exe, text, tmp_path)
    assert bodies is not None
    graph = evaluate(text).result.graph
    assert graph is not None
    assert_matches(graph, bodies, rel=1e-9)


@pytest.mark.skipif(not ELMERGRID_MATC, reason="GCAD_ELMERGRID_MATC is not set")
def test_live_matc(tmp_path: Path) -> None:
    assert ELMERGRID_MATC
    for name, (pre, limits) in MATC_CASES.items():
        text = matc_case(pre, limits)
        bodies = run_elmergrid(ELMERGRID_MATC, text, tmp_path / name)
        assert bodies is not None, name
        graph = evaluate(text).result.graph
        assert graph is not None, name
        assert_matches(graph, bodies, rel=1e-9)
    for name, (text, _, _) in MATC_ERRORS.items():
        bodies = run_elmergrid(ELMERGRID_MATC, text, tmp_path / name)
        assert bodies is None or any(b[0] == 0 for b in bodies.values()), name


# --- a corpus of real files ------------------------------------------------------------------

CORPUS = os.environ.get("GCAD_ELMER_CORPUS")


@pytest.mark.skipif(not CORPUS, reason="GCAD_ELMER_CORPUS is not set")
def test_corpus(tmp_path: Path) -> None:
    """Every file parses; every file the kernel evaluates matches ElmerGrid (when
    GCAD_ELMERGRID is set); every other file is an error with a position, not a model."""
    assert CORPUS
    seen: set[str] = set()
    counts = {"files": 0, "evaluated": 0, "matched": 0, "rejected": 0}
    reasons: dict[str, int] = {}
    failures = []
    for path in sorted(Path(CORPUS).rglob("*.grd")):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        counts["files"] += 1
        text = data.decode("utf-8", errors="replace")
        evaluated = evaluate(text, path.name)
        if errors(evaluated):
            first = next(d for d in evaluated.result.diagnostics if d.severity is Severity.ERROR)
            counts["rejected"] += 1
            # A rejection is one of the kernel's own errors, at a line of the file; a syntax
            # error or an error inside the prelude would be a kernel bug.
            deliberate = first.message.startswith("assertion failed")
            if first.span is None or first.span.file != path.name or not deliberate:
                failures.append(f"{path}: {first}")
            key = first.message.split(":")[0][:60]
            reasons[key] = reasons.get(key, 0) + 1
            continue
        counts["evaluated"] += 1
        if not ELMERGRID:
            continue
        exe = ELMERGRID_MATC if "$" in text else ELMERGRID
        bodies = run_elmergrid(exe, text, tmp_path / digest[:12]) if exe else None
        graph = evaluated.result.graph
        assert graph is not None
        try:
            assert bodies is not None
            assert_matches(graph, bodies, rel=1e-9)
            counts["matched"] += 1
        except AssertionError as exc:
            failures.append(f"{path}: {str(exc)[:200]}")
    print("\nelmergrid corpus:", json.dumps(counts), json.dumps(reasons, indent=1))
    assert not failures, "\n".join(failures[:20])
