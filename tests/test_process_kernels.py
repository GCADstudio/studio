"""Process kernels (SPEC §7.2, FR-K7, AC-13, AC-14).

The protocol, limits and trust are checked everywhere with a small test kernel (tests/kernels/
echo). The bundled `openscad` and `cadquery` kernels are checked where their programs are
installed: OpenSCAD on PATH or in its usual folder, CadQuery in the environment named by
GCAD_CADQUERY_PYTHON.
"""

from __future__ import annotations

import importlib.util
import math
import os
import shutil
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from gcad.backends import available_backends, get_backend
from gcad.cli import main
from gcad.core import blobs
from gcad.core.evaluator import Limits
from gcad.core.graph import graph_from_json
from gcad.diagnostics import GcadError
from gcad.importers import read_mesh
from gcad.kernels import BUNDLED, Kernel, discover, load_kernel, process, trust
from gcad.pipeline import evaluate_source, export_file
from gcad.server import Studio

TEST_KERNELS = Path(__file__).parent / "kernels"
HAS_OCCT = available_backends().get("occt") == "ok"


@pytest.fixture(autouse=True)
def fresh_programs() -> Iterator[None]:
    yield
    process.end_all()


@pytest.fixture
def echo(monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("GCAD_KERNEL_PATH", str(TEST_KERNELS))
    kernel = discover()[0]["echo"]
    trust(kernel)
    return kernel


def errors(result: object) -> list[str]:
    return [d.message for d in result.diagnostics if d.severity.value == "error"]  # type: ignore[attr-defined]


# ---------- the protocol ----------


@pytest.mark.parametrize("word", ["graph", "mesh"])
def test_geometry_comes_back_as_a_graph_or_a_mesh(echo: Kernel, word: str) -> None:
    result = evaluate_source(word, "box.echo", echo).result
    assert result.ok and result.graph is not None, errors(result)
    model = get_backend("manifold").realize(result.graph)
    assert model.volume() == pytest.approx(6000)
    lo, hi = model.bounds()
    assert np.allclose(lo, 0) and np.allclose(hi, [10, 20, 30])
    assert result.graph.metadata["kernel"] == "echo"
    if word == "mesh":
        assert result.graph.root.op == "import"


def test_a_mesh_is_never_exported_as_exact_geometry(echo: Kernel) -> None:
    if not HAS_OCCT:
        pytest.skip("OpenCASCADE is not installed here")
    graph = evaluate_source("mesh", "box.echo", echo).result.graph
    assert graph is not None
    with pytest.raises(GcadError, match="not exact geometry"):
        get_backend("occt").realize(graph)


def test_diagnostics_keep_their_place_in_the_source(echo: Kernel) -> None:
    result = evaluate_source("error\n  here\n", "bad.echo", echo).result
    assert not result.ok
    (d,) = result.diagnostics
    assert d.message == "bad thing" and d.span is not None
    assert (d.span.line, d.span.col, d.span.start) == (2, 3, 8)


def test_log_notifications_become_info(echo: Kernel) -> None:
    result = evaluate_source("log", "a.echo", echo).result
    assert result.ok
    assert [d.message for d in result.diagnostics] == ["hello from echo"]


def test_the_program_keeps_running_between_evaluations(echo: Kernel) -> None:
    evaluate_source("graph", "a.echo", echo)
    first = {id(p) for p in process._programs.values()}
    evaluate_source("mesh", "a.echo", echo)
    assert {id(p) for p in process._programs.values()} == first and len(first) == 1


def test_a_crash_is_reported_and_the_next_evaluation_starts_afresh(echo: Kernel) -> None:
    result = evaluate_source("crash", "a.echo", echo).result
    (message,) = errors(result)
    assert "exit code 3" in message and "crashed on purpose" in message
    assert evaluate_source("graph", "a.echo", echo).result.ok


def test_a_runaway_program_is_ended_at_the_time_limit(echo: Kernel) -> None:
    evaluate_source("graph", "a.echo", echo)  # started, so only the evaluation is timed
    started = time.monotonic()
    result = evaluate_source("hang", "a.echo", echo, Limits(max_seconds=1)).result
    assert time.monotonic() - started < 5
    assert errors(result) == ["evaluation exceeded the time limit of 1 s"]
    assert evaluate_source("graph", "a.echo", echo).result.ok


def test_a_newer_evaluation_stops_a_running_one(echo: Kernel) -> None:
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()
    result = evaluate_source("hang", "a.echo", echo, Limits(stop=stop)).result
    assert time.monotonic() - started < 5
    assert errors(result) == ["evaluation stopped: a newer one replaced it"]


def test_output_files_must_stay_in_the_working_folder(echo: Kernel) -> None:
    (message,) = errors(evaluate_source("outside", "a.echo", echo).result)
    assert "not a file in its working folder" in message


# ---------- trust (FR-K6, FR-K7) ----------


def test_an_untrusted_kernel_does_not_run_until_trusted(tmp_path: Path) -> None:
    folder = tmp_path / "echo"
    shutil.copytree(TEST_KERNELS / "echo", folder)
    kernel = load_kernel(folder)
    (message,) = errors(evaluate_source("graph", "a.echo", kernel).result)
    assert "not trusted" in message
    assert main(["kernel", "trust", str(folder)]) == 0
    assert evaluate_source("graph", "a.echo", kernel).result.ok
    # Changing the program withdraws the trust.
    program = folder / "program.py"
    program.write_text(program.read_text() + "\n# edited\n")
    assert "not trusted" in errors(evaluate_source("graph", "a.echo", kernel).result)[0]


def test_bundled_kernels_are_trusted() -> None:
    kernels = discover()[0]
    assert kernels["openscad"].is_process and kernels["openscad"].bundled
    assert process.is_trusted(kernels["openscad"]) and process.is_trusted(kernels["cadquery"])


def test_the_studio_asks_before_running_an_untrusted_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GCAD_KERNEL_PATH", str(TEST_KERNELS))
    studio = Studio(tmp_path)
    asked = studio.rpc_document_evaluate(kernel="echo", source="graph")
    assert not asked["ok"] and asked["trust"]["kernel"] == "echo"
    studio.rpc_kernel_trust(kernel="echo")
    answer = studio.rpc_document_evaluate(kernel="echo", source="graph")
    assert answer["ok"] and answer["volume"] == pytest.approx(6000)
    kernels = {k["id"]: k for k in studio.rpc_kernel_list()}
    assert kernels["echo"]["process"] and kernels["echo"]["available"]
    assert kernels["openscad"]["highlight"]["keywords"]  # declared in its manifest


def test_a_missing_program_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GCAD_CADQUERY_PYTHON", raising=False)
    kernel = discover()[0]["cadquery"]
    reason = process.unavailable_reason(kernel)
    assert reason is not None and "GCAD_CADQUERY_PYTHON" in reason
    (message,) = errors(evaluate_source("x = 1", "a.cq.py", kernel).result)
    assert "GCAD_CADQUERY_PYTHON" in message


def test_kernel_for_prefers_the_longest_extension() -> None:
    from gcad.kernels import kernel_for

    assert kernel_for(Path("part.cq.py")).id == "cadquery"
    assert kernel_for(Path("part.scad")).id == "openscad"


# ---------- reading what programs hand back ----------


def test_graph_json_round_trips_and_is_checked() -> None:
    graph = evaluate_source(
        (BUNDLED / "csg" / "examples" / "bracket.gcsg").read_text(),
        "bracket.gcsg",
        discover()[0]["csg"],
    ).result.graph
    assert graph is not None
    assert graph_from_json(graph.to_json()).hash() == graph.hash()
    doc = graph.to_json(spans=False)
    doc["nodes"][doc["root"]]["op"] = "explode"  # type: ignore[index]
    with pytest.raises(ValueError, match="unknown op"):
        graph_from_json(doc)


def test_mesh_readers_agree(tmp_path: Path) -> None:
    trimesh = pytest.importorskip("trimesh")
    export_file(BUNDLED / "csg" / "examples" / "test_part.gcsg", tmp_path / "p.stl")
    reference = trimesh.load(tmp_path / "p.stl", force="mesh")
    v, f = reference.vertices.tolist(), reference.faces.tolist()  # plain floats print exactly
    (tmp_path / "p.off").write_text(
        f"OFF\n{len(v)} {len(f)} 0\n"
        + "".join(f"{x!r} {y!r} {z!r}\n" for x, y, z in v)
        + "".join(f"3 {a} {b} {c}\n" for a, b, c in f)
    )
    model = (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
        ' unit="centimeter"><resources><object id="1" type="model"><mesh><vertices>'
        + "".join(f'<vertex x="{x / 10!r}" y="{y / 10!r}" z="{z / 10!r}"/>' for x, y, z in v)
        + "</vertices><triangles>"
        + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
        + "</triangles></mesh></object></resources></model>"
    )
    with zipfile.ZipFile(tmp_path / "p.3mf", "w") as zf:
        zf.writestr("3D/3dmodel.model", model)
    for fmt in ("stl", "off", "3mf"):
        vertices, tris = read_mesh((tmp_path / f"p.{fmt}").read_bytes(), fmt)
        mesh = trimesh.Trimesh(vertices, tris, process=False)
        assert mesh.is_watertight, fmt
        assert mesh.volume == pytest.approx(reference.volume, rel=1e-6), fmt


def test_blobs_are_content_addressed() -> None:
    digest = blobs.put(b"solid")
    assert blobs.get(digest) == b"solid" and len(digest) == 64
    with pytest.raises(GcadError, match="no longer loaded"):
        blobs.get("0" * 64)


# ---------- AC-13: OpenSCAD, where it is installed ----------


def _openscad() -> str | None:
    spec = importlib.util.spec_from_file_location(
        "openscad_program", BUNDLED / "openscad" / "program.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.find_openscad()  # type: ignore[no-any-return]


OPENSCAD = _openscad()
needs_openscad = pytest.mark.skipif(OPENSCAD is None, reason="OpenSCAD not installed")
MINSCAD_EXAMPLES = sorted((BUNDLED / "minscad" / "examples").glob("*.mscad"))


@needs_openscad
@pytest.mark.parametrize("path", MINSCAD_EXAMPLES, ids=lambda p: p.name)
def test_openscad_kernel_matches_minscad(path: Path) -> None:
    """Two kernels, one model: OpenSCAD's mesh of every minscad example (each is a valid
    OpenSCAD file) has the solid minscad builds, within the 3MF's micrometre precision."""
    text = "$fn = 64;\n" + path.read_text(encoding="utf-8")
    kernels = discover()[0]
    reference = evaluate_source(text, path.name, kernels["minscad"]).result
    assert reference.ok and reference.graph is not None
    result = evaluate_source(
        text, path.stem + ".scad", kernels["openscad"], Limits(max_seconds=120)
    ).result
    if reference.graph.root.dim == 2:
        assert errors(result) == ["this is a 2D model; the openscad kernel returns 3D meshes only"]
        return
    assert result.ok and result.graph is not None, errors(result)
    ours = get_backend("manifold").realize(reference.graph)
    theirs = get_backend("manifold").realize(result.graph)
    assert np.allclose(np.concatenate(theirs.bounds()), np.concatenate(ours.bounds()), atol=1e-4)
    assert theirs.volume() == pytest.approx(ours.volume(), rel=1e-5)


@needs_openscad
def test_openscad_text_renders_and_refuses_exact_export(tmp_path: Path) -> None:
    example = BUNDLED / "openscad" / "examples" / "nameplate.scad"
    evaluated, report = export_file(example, tmp_path / "plate.stl")
    assert report is not None, errors(evaluated.result)
    assert report.triangles and report.triangles > 1000  # the letters are there
    if HAS_OCCT:
        with pytest.raises(GcadError, match="not exact geometry"):
            export_file(example, tmp_path / "plate.step")


@needs_openscad
def test_openscad_errors_point_at_the_line_and_includes_resolve(tmp_path: Path) -> None:
    kernel = discover()[0]["openscad"]
    bad = evaluate_source("cube(1);\ncube(2)\n", "bad.scad", kernel).result
    assert not bad.ok
    # The missing semicolon is found at the end of line 2 or the start of line 3.
    assert any(d.span is not None and d.span.line in (2, 3) for d in bad.diagnostics)
    (tmp_path / "lib.scad").write_text("module peg() { cylinder(h = 5, r = 1, $fn = 16); }\n")
    main_file = tmp_path / "main.scad"
    main_file.write_text("include <lib.scad>\npeg();\n")
    good = evaluate_source(main_file.read_text(), str(main_file), kernel).result
    assert good.ok, errors(good)


@pytest.mark.skipif(
    not os.environ.get("GCAD_REQUIRE_PROCESS_KERNELS"), reason="only where CI installs them"
)
def test_the_programs_are_installed_where_required() -> None:
    """The CI job that installs OpenSCAD and CadQuery must not skip AC-13 and AC-14."""
    assert OPENSCAD is not None
    assert os.environ.get("GCAD_CADQUERY_PYTHON")
    assert HAS_OCCT


# ---------- AC-14: CadQuery, where an environment is named ----------

needs_cadquery = pytest.mark.skipif(
    not os.environ.get("GCAD_CADQUERY_PYTHON"), reason="GCAD_CADQUERY_PYTHON not set"
)


@needs_cadquery
@pytest.mark.skipif(not HAS_OCCT, reason="OpenCASCADE is not installed here")
@pytest.mark.parametrize("fmt", ["step", "iges"])
def test_cadquery_part_exports_exactly(fmt: str, tmp_path: Path) -> None:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader
    from OCP.STEPControl import STEPControl_Reader

    example = BUNDLED / "cadquery" / "examples" / "test_part.cq.py"
    out = tmp_path / f"part.{fmt}"
    evaluated, report = export_file(example, out)
    assert report is not None, errors(evaluated.result)
    reader = STEPControl_Reader() if fmt == "step" else IGESControl_Reader()
    assert reader.ReadFile(str(out)) == IFSelect_RetDone
    reader.TransferRoots()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(reader.OneShape(), props)
    assert props.Mass() == pytest.approx(12000 - 160 * math.pi, rel=1e-6)


@needs_cadquery
def test_cadquery_runaway_and_errors() -> None:
    kernel = discover()[0]["cadquery"]
    syntax = evaluate_source("x = (\n", "bad.cq.py", kernel).result
    assert not syntax.ok and syntax.diagnostics[-1].span is not None
    raised = evaluate_source(
        "import cadquery as cq\n\nraise ValueError('no')\n", "r.cq.py", kernel
    ).result
    (message,) = errors(raised)
    assert message == "ValueError: no" and raised.diagnostics[-1].span.line == 3  # type: ignore[union-attr]
    started = time.monotonic()
    loop = evaluate_source(
        "while True:\n    pass\n", "loop.cq.py", kernel, Limits(max_seconds=2)
    ).result
    assert errors(loop) == ["evaluation exceeded the time limit of 2 s"]
    assert time.monotonic() - started < 10
    shown = evaluate_source(
        "import cadquery as cq\nresult = cq.Workplane().box(1, 2, 3)\n", "b.cq.py", kernel
    ).result
    assert shown.ok, errors(shown)
