"""The iso10303 kernel (SPEC §5.5, AC-21 to AC-26): ISO 10303-21 files in, parametric STEP out."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gcad.cli import main
from gcad.diagnostics import Severity
from gcad.iso10303 import schemas
from gcad.iso10303.writer import to_step
from gcad.kernels import BUNDLED, load_kernel
from gcad.pipeline import evaluate_file, evaluate_source

KERNEL = load_kernel(BUNDLED / "iso10303")
EXAMPLES = BUNDLED / "iso10303" / "examples"
HERE = Path(__file__).parent / "iso10303"
AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"
HAVE_AP242 = (schemas.schema_dir() / f"{AP242}.json").is_file()
needs_ap242 = pytest.mark.skipif(
    not HAVE_AP242, reason="no AP242 long form (gcad iso schema fetch AP242)"
)


def occt() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True


needs_occt = pytest.mark.skipif(not occt(), reason="back-end B (OpenCASCADE) is not installed")


def evaluate(text: str, name: str = "t.stp"):  # type: ignore[no-untyped-def]
    return evaluate_source(text, name, KERNEL).result


def errors(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.message for d in result.diagnostics if d.severity is Severity.ERROR]


HEADER = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('t.stp','2026-10-27T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));
ENDSEC;
DATA;
#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
#2=(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.));
#3=(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT());
#4=(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNIT_ASSIGNED_CONTEXT((#1,#2,#3))REPRESENTATION_CONTEXT('3D',''));
#5=CARTESIAN_POINT('',(0.,0.,0.));
#6=DIRECTION('',(0.,0.,1.));
#7=DIRECTION('',(1.,0.,0.));
#8=AXIS2_PLACEMENT_3D('',#5,#6,#7);
"""


def model(*instances: str) -> str:
    return HEADER + "\n".join(instances) + "\nENDSEC;\nEND-ISO-10303-21;\n"


BLOCK = "#10=BLOCK('b',#8,40.,30.,10.);"
REP = "#90=CSG_SHAPE_REPRESENTATION('',(#11),#4);"


# -- the grammar (design note §1) ------------------------------------------------------------


def test_part21_tokens_and_records() -> None:
    text = model(
        "#9=CARTESIAN_POINT('it''s \\X2\\00E9\\X0\\ /* not a comment */',(1.5E1,-2.,+3));",
        "/* a comment */ #10=BLOCK('b',#8,40.,30.,10.);",
        "#11=CSG_SOLID('',#10);",
        REP,
    )
    forms = KERNEL.to_gcore(text, "t.stp")
    gcore = "\n".join(map(str, forms))
    assert "it's é /* not a comment */" in gcore
    assert evaluate(text).ok


def test_strings_may_hold_line_breaks() -> None:
    text = model("#10=BLOCK('a long\nname',#8,40.,30.,10.);", "#11=CSG_SOLID('',#10);", REP)
    assert evaluate(text).ok


# -- negative controls, each at the gate it tests (AC-25) ------------------------------------


def test_syntax_error() -> None:
    result = evaluate(model("#10=BLOCK('b',#8,40.,30.,10.)", "#11=CSG_SOLID('',#10);", REP))
    (message,) = errors(result)
    assert message.startswith("unexpected '#11'")
    assert result.diagnostics[0].span is not None and result.diagnostics[0].span.line == 17


def test_dangling_reference() -> None:
    result = evaluate(model("#10=BLOCK('b',#80,40.,30.,10.);", "#11=CSG_SOLID('',#10);", REP))
    assert "'#80' is not defined" in errors(result)
    assert result.graph is None


def test_duplicate_instance() -> None:
    result = evaluate(model(BLOCK, "#10=CSG_SOLID('',#10);", REP))
    assert "'#10' is defined twice (first on line 16)" in errors(result)
    assert any("#10 is defined twice (9.1)" in m for m in errors(result))  # the semantic layer too


def test_reference_cycle() -> None:
    result = evaluate(
        model(
            "#10=BOOLEAN_RESULT('',.UNION.,#12,#8);",
            "#11=CSG_SOLID('',#10);",
            "#12=BOOLEAN_RESULT('',.UNION.,#10,#8);",
            REP,
        )
    )
    assert "cyclic reference: #10 -> #12 -> #10" in errors(result)


@needs_ap242
def test_where_rule_violation() -> None:
    result = evaluate(
        model(
            "#9=AXIS1_PLACEMENT('',#5,#6);",
            "#10=RIGHT_CIRCULAR_CONE('c',#9,10.,-1.,0.2);",
            "#11=CSG_SOLID('',#10);",
            REP,
        )
    )
    found = [d for d in result.diagnostics if "right_circular_cone WR1 is violated" in d.message]
    assert found and found[0].severity is Severity.ERROR and found[0].origin == "check"
    assert found[0].span is not None and found[0].span.line == 17  # the #10 instance


@needs_ap242
def test_entity_outside_the_schema() -> None:
    result = evaluate(model(BLOCK, "#11=CSG_SOLID('',#10);", "#12=NOT_AN_ENTITY('x');", REP))
    assert "NOT_AN_ENTITY is not an entity of AP242, the schema FILE_SCHEMA names" in errors(result)
    assert result.ok  # not part of the model, so the model is still built


def test_unsupported_feature() -> None:
    result = evaluate(
        model(
            BLOCK,
            "#12=EDGE_CURVE('',#5,#5,#8,.T.);",
            "#13=SOLID_WITH_CONSTANT_RADIUS_EDGE_BLEND('fillet','',#10,(#12),2.);",
            "#11=CSG_SOLID('',#13);",
            REP,
        )
    )
    assert (
        "#13 SOLID_WITH_CONSTANT_RADIUS_EDGE_BLEND: SOLID_WITH_CONSTANT_RADIUS_EDGE_BLEND is an "
        "edge blend (fillet), which GCAD's geometry graph cannot build "
        "(no back-end operation for it)"
    ) in errors(result)


# -- semantics -------------------------------------------------------------------------------


def test_geometry_points_back_to_its_instance() -> None:
    """Clicking a face selects the #n instance that made it (FR-U7)."""
    text = (EXAMPLES / "csg_primitives.stp").read_text()
    graph = evaluate(text, "p.stp").graph
    spans = {n.op: n.span for n in graph.nodes().values()}
    torus = spans["rotate_extrude"]
    assert text[torus.start : torus.end].startswith("#33=TORUS(")


def test_part108_parameters_drive_the_geometry() -> None:
    text = (EXAMPLES / "parameters.stp").read_text()
    changed = text.replace("MATHS_REAL(12.)", "MATHS_REAL(24.)")
    nodes = {n.op: dict(n.args) for n in evaluate(changed).graph.nodes().values()}
    assert nodes["box"]["size"] == (50.0, 30.0, 16.0)  # Z = max(H - 8, 4)
    assert nodes["cylinder"]["h"] == 24.0  # |-H|
    assert nodes["sphere"]["r"] == 8.0  # H / 3


def test_explicit_brep_and_the_rest_reported() -> None:
    result = evaluate((HERE / "expressions.stp").read_text())
    nodes = {n.op: dict(n.args) for n in result.graph.nodes().values()}
    assert nodes["cylinder"]["h"] == 12.0  # the equal-parameter constraint
    assert nodes["linear_extrude"]["height"] == 5.0


@needs_occt
@pytest.mark.parametrize(
    ("name", "volume"),
    [
        ("swept.stp", 6000 - 160 * math.pi + 1500 * math.pi),
        (
            "features.stp",
            48000
            - math.pi * (72 + 100 + 50 + 90 + 80 + 180 - 80)
            - math.pi * 9 * (3 / math.tan(math.pi / 6)) / 3
            - 288,
        ),
        ("units_inch.stp", 25.4**3 + math.pi * 25.4 * (25.4 * math.tan(math.pi / 12)) ** 2 / 3),
    ],
)
def test_exact_volumes(name: str, volume: float) -> None:
    from gcad.backends import get_backend

    graph = evaluate_file(EXAMPLES / name).result.graph
    got = get_backend("occt").realize(graph).volume()
    assert got == pytest.approx(volume, rel=1e-6)


# -- AC-2 in ISO 10303-42 with an ISO 10303-108 parameter (AC-22) ---------------------------


@needs_occt
def test_ac2_part_in_part42_with_a_parameter(tmp_path: Path) -> None:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.STEPControl import STEPControl_Reader

    def exported_volume(source: Path) -> float:
        out = tmp_path / (source.stem + ".step")
        assert main(["export", str(source), "-o", str(out)]) in (0, 1)
        reader = STEPControl_Reader()
        reader.ReadFile(str(out))
        reader.TransferRoots()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(reader.OneShape(), props)
        return props.Mass()

    part = EXAMPLES / "test_part.stp"
    assert exported_volume(part) == pytest.approx(12000 - 160 * math.pi, rel=1e-6)
    bigger = tmp_path / "hole10.stp"
    bigger.write_text(part.read_text().replace("MATHS_REAL(8.)", "MATHS_REAL(10.)"))
    assert exported_volume(bigger) == pytest.approx(12000 - 250 * math.pi, rel=1e-6)


# -- the parametric writer: round trip and determinism (AC-23, AC-26) ------------------------

SOURCES = sorted(
    p for k in ("minscad", "csg", "parts") for p in (BUNDLED / k / "examples").iterdir()
)
REFUSED = {  # examples with no ISO 10303-42 form, and why
    "features.mscad": "linear_extrude with twist or scale has no ISO 10303-42 form",
    "section.mscad": "section in 2D has no ISO 10303-42 2D CSG form here",
}


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_round_trip(source: Path) -> None:
    from gcad.diagnostics import GcadError

    graph = evaluate_file(source, trace=True).result.graph
    try:
        text = to_step(graph, source.stem, "2026-10-27T00:00:00", source.name)
    except GcadError as exc:
        assert exc.diagnostic.message == "parametric STEP: " + REFUSED[source.name]
        return
    assert source.name not in REFUSED
    back = evaluate(text, source.stem + ".stp")
    assert back.ok, [str(d) for d in back.diagnostics]
    assert back.graph.hash() == graph.hash()
    if HAVE_AP242:
        conformance = [
            d.message
            for d in back.diagnostics
            if d.origin == "check" and d.severity is Severity.ERROR
        ]
        assert conformance == []
        counts = [d.message for d in back.diagnostics if d.message.startswith("rules: ")]
        assert re.fullmatch(r"rules: \d+ evaluated, \d+ violated, 0 not evaluated", counts[-1])


def test_writer_is_byte_stable_across_processes(tmp_path: Path) -> None:
    source = HERE / "test_part_param.gcsg"
    outs = []
    for seed in ("1", "2"):
        out = tmp_path / f"p{seed}.stp"
        env = {**os.environ, "PYTHONHASHSEED": seed, "SOURCE_DATE_EPOCH": "1792800000"}
        subprocess.run(
            [
                sys.executable,
                "-m",
                "gcad",
                "export",
                str(source),
                "-f",
                "step-parametric",
                "-o",
                str(out),
            ],
            check=True,
            env=env,
        )
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    assert outs[0] == (EXAMPLES / "test_part.stp").read_bytes()


# -- foreign files (AC-24) -------------------------------------------------------------------


@needs_occt
@pytest.mark.parametrize("schema", ["AP214", "AP242"])
def test_brep_files_from_opencascade(tmp_path: Path, schema: str) -> None:
    from gcad.backends import get_backend

    step = tmp_path / f"bracket-{schema}.step"
    assert (
        main(
            [
                "export",
                str(BUNDLED / "csg/examples/bracket.gcsg"),
                "-o",
                str(step),
                "--schema",
                schema,
            ]
        )
        == 0
    )
    original = get_backend("occt").realize(
        evaluate_file(BUNDLED / "csg/examples/bracket.gcsg").result.graph
    )
    result = evaluate_source(step.read_text(), str(step), KERNEL).result
    assert result.ok, [str(d) for d in result.diagnostics]
    (node,) = [n for n in result.graph.nodes().values() if n.op == "import"]
    assert get_backend("occt").realize(result.graph).volume() == pytest.approx(
        original.volume(), rel=1e-6
    )
    if schema == "AP242" and HAVE_AP242:  # OpenCASCADE writes the AP242 protocol name wrong
        assert any(
            "ap242_application_protocol_definition_required" in d.message
            for d in result.diagnostics
            if d.origin == "check"
        )


def test_freecad_nxstyle_parametric_file() -> None:
    """A file freecad-nxstyle wrote (its parametric.to_step, unchanged): Part 108 drives it."""
    text = (HERE / "nxstyle_csg.stp").read_text()
    nodes = {n.op: dict(n.args) for n in evaluate(text).graph.nodes().values()}
    assert nodes["cylinder"]["r1"] == 4.0
    changed = text.replace("MATHS_REAL(8.)", "MATHS_REAL(6.)")
    assert {n.op: dict(n.args) for n in evaluate(changed).graph.nodes().values()}["cylinder"][
        "r1"
    ] == 3.0


def test_studio_faces_select_their_instances_and_keep_the_file_colour() -> None:
    """In the studio (FR-U7): every face group of the preview points at the #n instance that
    made it, and the colour of a STYLED_ITEM reaches the mesh."""
    from gcad.server import Studio

    text = (EXAMPLES / "csg_primitives.stp").read_text()
    result = Studio(Path(".")).rpc_document_evaluate("iso10303", text, "csg_primitives.stp")
    assert result["ok"]
    picked = [text[s["start"] : s["end"]] for g in result["groups"] for s in g["spans"]]
    assert picked and all(re.match(r"#\d+=[A-Z_]+\(", p) for p in picked)
    assert {tuple(g["color"]) for g in result["groups"]} == {(0.2, 0.5, 0.8, 1.0)}
