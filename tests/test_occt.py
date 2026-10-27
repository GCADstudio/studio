"""Back-end B (OpenCASCADE) and the STEP and IGES exporters: SPEC AC-2."""

import getpass
import math
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ok

from gcad.backends import get_backend, select_backend
from gcad.diagnostics import GcadError
from gcad.pipeline import export_file

EXACT = 12000 - 160 * math.pi

# Skipped as a whole where OpenCASCADE has no build (Windows on ARM); the behaviour there is
# tested in test_backend_manifold.py.
OCP = pytest.importorskip("OCP")

from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.IFSelect import IFSelect_RetDone  # noqa: E402
from OCP.IGESControl import IGESControl_Reader  # noqa: E402
from OCP.STEPControl import STEPControl_Reader  # noqa: E402
from OCP.TopAbs import TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402


def read_back(path: Path):  # type: ignore[no-untyped-def]
    reader = STEPControl_Reader() if path.suffix == ".step" else IGESControl_Reader()
    assert reader.ReadFile(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() >= 1
    return reader.OneShape()


def solids(shape) -> int:  # type: ignore[no-untyped-def]
    exp, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while exp.More():
        n, _ = n + 1, exp.Next()
    return n


def volume(shape) -> float:  # type: ignore[no-untyped-def]
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


@pytest.fixture
def test_part(examples: Path) -> Path:
    return examples / "test_part.gcsg"


@pytest.mark.parametrize("ext", ["step", "igs"])
def test_ac2_exact_export_reads_back_as_one_valid_solid(
    test_part: Path, tmp_path: Path, ext: str
) -> None:
    out = tmp_path / f"part.{ext}"
    evaluated, report = export_file(test_part, out)
    assert report is not None and report.backend == "occt"
    shape = read_back(out)
    assert solids(shape) == 1
    assert BRepCheck_Analyzer(shape).IsValid()
    assert volume(shape) == pytest.approx(EXACT, rel=1e-6)


def test_step_header_units_and_product(examples: Path, tmp_path: Path) -> None:
    out = tmp_path / "bracket.step"
    export_file(examples / "bracket.gcsg", out, options={"epoch": 1767225600})
    text = out.read_text()
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
    assert "FILE_NAME('bracket.step','2026-01-01T00:00:00',(''),('')," in text
    assert "GCAD Studio" in text and "PRODUCT('bracket','bracket'" in text
    assert "COLOUR_RGB" in text  # the whole bracket is blue
    assert getpass.getuser() not in text


def test_step_ap242(test_part: Path, tmp_path: Path) -> None:
    out = tmp_path / "part.step"
    export_file(test_part, out, options={"schema": "AP242"})
    assert "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING" in out.read_text()
    assert volume(read_back(out)) == pytest.approx(EXACT, rel=1e-6)
    with pytest.raises(GcadError, match="STEP schema"):
        export_file(test_part, out, options={"schema": "AP999"})


@pytest.mark.parametrize(
    ("schema", "marker"), [("AP242", "AP242_MANAGED"), ("AP214", "AUTOMOTIVE")]
)
def test_step_options_apply_in_a_fresh_process(
    test_part: Path, tmp_path: Path, schema: str, marker: str
) -> None:
    """OpenCASCADE ignores write.step.* settings until its STEP controller is initialised;
    a fresh process is the only place that shows it."""
    out = tmp_path / "part.step"
    cmd = [sys.executable, "-m", "gcad", "export", str(test_part), "-o", str(out)]
    subprocess.run([*cmd, "--schema", schema], check=True, capture_output=True)
    assert marker in out.read_text()


def test_step_parses_with_an_independent_part21_reader(examples: Path, tmp_path: Path) -> None:
    """steputils implements ISO 10303-21 syntax on its own, without OpenCASCADE."""
    p21 = pytest.importorskip("steputils.p21")
    out = tmp_path / "bracket.step"
    export_file(examples / "bracket.gcsg", out)
    step = p21.readfile(str(out))
    names = [e.entity.name for e in step.data[0].instances.values() if hasattr(e, "entity")]
    assert names.count("MANIFOLD_SOLID_BREP") == 1
    assert names.count("CYLINDRICAL_SURFACE") == 6  # six bolt holes, as true cylinders
    assert names.count("PRODUCT") == 1


def test_iges_global_section_is_clean_and_dated(test_part: Path, tmp_path: Path) -> None:
    out = tmp_path / "part.igs"
    export_file(test_part, out, options={"epoch": 1767225600})
    lines = out.read_text().splitlines()
    global_section = "".join(line[:72] for line in lines if len(line) >= 73 and line[72] == "G")
    assert global_section.count("15H20260101.000000") == 2  # file date and last-change date
    assert "2HMM" in global_section
    assert getpass.getuser() not in global_section
    assert all(len(line) == 80 for line in lines)


def test_auto_backend_choice() -> None:
    assert select_backend("auto", "brep").name == "occt"
    assert select_backend("auto", "mesh").name == "manifold"


CASES = {
    "hex nut (polygonal $fn)": (
        "(difference (cylinder :h 5 :r 6 :$fn 6) (translate [0 0 -1] (cylinder :h 7 :r 3)))",
        5 * 6 * 36 * math.sqrt(3) / 4 - math.pi * 9 * 5,
    ),
    "cone": ("(cylinder :h 10 :r1 4 :r2 0)", math.pi * 16 * 10 / 3),
    "sphere": ("(sphere 5)", 4 / 3 * math.pi * 125),
    "mirror": ("(mirror [1 0 0] (translate [1 0 0] (box 2)))", 8),
    "twist": (
        "(linear_extrude :height 10 :twist 90 :slices 20 (translate [5 0] (rect 1 :center true)))",
        10,
    ),
    "taper": ("(linear_extrude :height 10 :scale 0 (rect 2 :center true))", 4 * 10 / 3),
    "revolve": ("(rotate_extrude (translate [1 0] (rect 1)) :angle 90)", 3 * math.pi / 4),
    "polygon with hole": (
        "(linear_extrude 2 (polygon [[0 0] [10 0] [10 10] [0 10] [3 3] [7 3] [7 7] [3 7]]"
        " :paths [[0 1 2 3] [4 5 6 7]]))",
        168,
    ),
    "polyhedron": (
        "(polyhedron [[0 0 0] [10 0 0] [10 7 0] [0 7 0] [0 0 5] [10 0 5] [10 7 5] [0 7 5]]"
        " [[0 1 2 3] [4 5 1 0] [7 6 5 4] [5 6 2 1] [6 7 3 2] [7 4 0 3]])",
        350,
    ),
    "intersection": ("(intersection (box 10) (translate [5 5 5] (box 10)))", 125),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_occt_geometry_is_exact(name: str) -> None:
    source, exact = CASES[name]
    graph = ok(source).graph
    assert graph is not None
    model = get_backend("occt").realize(graph)
    assert model.is_valid()
    # A twisted extrusion is a loft through the slices: a spline close to the true helicoid.
    assert model.volume() == pytest.approx(exact, rel=1e-5 if name == "twist" else 1e-9)


def test_occt_conventions_match_the_mesh_backend() -> None:
    """Rotation order, mirror and rotate_extrude agree between the two back-ends."""
    for source in (
        "(rotate [30 45 60] (box [1 2 3]))",
        "(mirror [1 1 0] (box [1 2 3]))",
        "(rotate_extrude (translate [1 0] (rect 1)) :$fn 128)",
        "(linear_extrude :height 10 :twist 90 :slices 1 (translate [5 0] (rect 1 :center true)))",
    ):
        graph = ok(source).graph
        assert graph is not None
        exact = get_backend("occt").realize(graph).bounds()
        mesh = get_backend("manifold").realize(graph).bounds()
        assert exact is not None and mesh is not None
        assert abs(exact[0] - mesh[0]).max() < 1e-3 and abs(exact[1] - mesh[1]).max() < 1e-3


def test_occt_mesh_is_watertight_and_exports(test_part: Path, tmp_path: Path) -> None:
    trimesh = pytest.importorskip("trimesh")
    out = tmp_path / "part.stl"
    _, report = export_file(test_part, out, backend_name="occt")
    assert report is not None and report.backend == "occt"
    mesh = trimesh.load(out, force="mesh")
    assert mesh.is_watertight
    assert abs(mesh.volume - EXACT) / EXACT < 0.005


def test_empty_model_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "empty.gcsg"
    src.write_text("(difference (box 1) (box 2))")
    with pytest.raises(GcadError, match="empty"):
        export_file(src, tmp_path / "empty.step")
