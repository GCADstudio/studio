"""PMI viewing (SPEC M15, AC-27 to AC-30): annotations beside the graph, NIST's CTC files.

The NIST MBE PMI test files are bundled examples (public, "can be used without any
restrictions"; docs/design/iso10303-kernel.md §5.1). They are evaluated here without the
semantic checker, which is tested elsewhere and takes seconds on files this size.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

from gcad.core.evaluator import EvalResult, Limits, evaluate
from gcad.core.sexpr import read
from gcad.kernels import BUNDLED, load_kernel

KERNEL = load_kernel(BUNDLED / "iso10303")
EXAMPLES = BUNDLED / "iso10303" / "examples"
CTC1 = EXAMPLES / "nist_ctc_01_asme1_ap242-e1.stp"
CTC5 = EXAMPLES / "nist_ctc_05_asme1_ap242-e1.stp"


def occt() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True


needs_occt = pytest.mark.skipif(not occt(), reason="back-end B (OpenCASCADE) is not installed")


def run(path: Path) -> EvalResult:
    source = path.read_text(encoding="utf-8")
    forms = KERNEL.to_gcore(source, path.name)
    # CI machines are slower than a desk; the studio's own limit is 10 s
    return evaluate(forms, Limits(max_seconds=120), {}, KERNEL.prelude(), source)


@pytest.fixture(scope="module")
def ctc1() -> EvalResult:
    return run(CTC1)


@pytest.fixture(scope="module")
def ctc5() -> EvalResult:
    return run(CTC5)


def by_id(result: EvalResult) -> dict:
    return {a.id: a for a in result.annotations}


# --- the engine feature (FR-C11) -----------------------------------------------------------


def test_annotations_stay_out_of_the_graph() -> None:
    plain = evaluate(read("(box [1 2 3])", "t.gcore"))
    noted = evaluate(
        read(
            '(define f (record "#7" "FACE")) '
            '(annotate "n" "note" :kind "dimension" :lines [[[0 0 0] [1 0 0]]] '
            ':triangles [[0 0 0] [1 0 0] [0 1 0]] :targets [f] :details [["value" 5]]) '
            "(box [1 2 3])",
            "t.gcore",
        )
    )
    assert plain.graph is not None and noted.graph is not None
    assert noted.graph.hash() == plain.graph.hash()
    (a,) = noted.annotations
    assert a.kind == "dimension" and a.details == [("value", "5")]
    assert a.lines == [[0, 0, 0, 1, 0, 0]] and len(a.triangles) == 9
    assert a.targets[0].name == "#7" and a.targets[0].span is not None
    assert noted.records["#7"] == a.targets[0].span
    assert a.to_json("t.gcore")["targets"][0]["span"]["inFile"] is True


@pytest.mark.parametrize(
    "call, message",
    [
        ('(annotate "" "x")', "needs an id"),
        ('(annotate "a" "x" :triangles [[0 0 0] [1 0 0]])', "three points per triangle"),
        ('(annotate "a" "x" :targets [5])', "named record"),
        ('(annotate "a" "x" :camera [[0 0 0]])', "position direction up"),
    ],
)
def test_annotate_refuses_bad_input(call: str, message: str) -> None:
    result = evaluate(read(call, "t.gcore"))
    assert result.graph is None
    assert message in result.diagnostics[0].message


def test_position() -> None:
    result = evaluate(read('(echo (position ["a" "b"] "b") (position [1 2] 3))', "t.gcore"))
    assert "ECHO: 1, -1" in result.diagnostics[0].message


# --- NIST CTC-01: tessellated graphical PMI, semantic PMI -----------------------------------


def test_ctc1_counts(ctc1: EvalResult) -> None:
    assert ctc1.graph is not None, [d.message for d in ctc1.diagnostics]
    kinds = Counter(a.kind for a in ctc1.annotations)
    assert kinds == {
        "callout": 23,
        "dimension": 12,
        "tolerance": 6,
        "datum": 3,
        "datum feature": 3,
        "datum system": 2,
        "view": 1,
    }
    # every callout is drawn: tessellated curves, and glyphs as triangles
    for a in ctc1.annotations:
        if a.kind == "callout":
            assert a.lines, a.id
    assert sum(len(a.triangles) for a in ctc1.annotations) > 9 * 1000


def test_ctc1_pmi_leaves_the_geometry_alone(ctc1: EvalResult) -> None:
    # the hash this file had before PMI was read (one import of the whole file)
    assert ctc1.graph is not None
    assert ctc1.graph.root.op == "import"
    assert ctc1.graph.hash().startswith("70b99bdb24640d68")


def test_ctc1_tolerances(ctc1: EvalResult) -> None:
    a = by_id(ctc1)
    assert a["#21"].label == "⌖ 0.75 mm | A | B | C"
    assert [t.name for t in a["#21"].targets] == ["#805", "#801"]
    assert a["#21"].links == ["#628", "#52"]  # its callout, its datum system
    assert a["#57"].label == "⏥ 0.2 mm"
    assert [t.name for t in a["#57"].targets] == ["#861"]
    assert a["#27"].label == "⌮ ⌓ 0.5 mm | A"  # all around
    assert len(a["#27"].targets) == 6
    # a simple subtype carries its datum system as its fifth attribute
    assert a["#56"].label == "⟂ 1.5 mm | A"
    assert dict(a["#56"].details)["type"] == "perpendicularity"


def test_ctc1_dimensions_and_datums(ctc1: EvalResult) -> None:
    a = by_id(ctc1)
    assert a["#120"].label == "⌀35 mm +0 mm / -0.2 mm"
    assert dict(a["#124"].details)["upper limit"] == "35.2 mm"
    assert a["#33"].label == "60° ± 0.5°"
    assert a["#124"].label == "⌀34.8 mm-35.2 mm"  # limits
    assert a["#122"].label == "⌀20 mm +0.05 mm / -0.1 mm"
    assert a["#37"].label == "Datum A" and [t.name for t in a["#37"].targets] == ["#861"]
    assert [t.name for t in a["#38"].targets] == ["#854", "#853"]
    assert a["#52"].label == "Datum system A | B | C"


def test_ctc1_callouts_link_to_semantics_and_views(ctc1: EvalResult) -> None:
    a = by_id(ctc1)
    assert "#21" in a["#628"].links and a["#628"].label == "Position.1"
    assert a["#628"].views == ["#13"]
    view = a["#13"]
    assert view.label == "MBD_0" and view.camera is not None
    assert "#628" in view.links
    # every target is a face of the part, written in the file
    faces = {t.name for x in ctc1.annotations for t in x.targets}
    assert faces and all(f in ctc1.records for f in faces)


# --- NIST CTC-05: polyline graphical PMI in inches, two saved views -------------------------


def test_ctc5_polylines_inches_views(ctc5: EvalResult) -> None:
    assert ctc5.graph is not None, [d.message for d in ctc5.diagnostics]
    kinds = Counter(a.kind for a in ctc5.annotations)
    assert kinds["callout"] == 22 and kinds["view"] == 2
    views = [a for a in ctc5.annotations if a.kind == "view"]
    assert sorted(v.label for v in views) == ["MBD_A", "MBD_B"]
    # polylines and circles in model coordinates, converted from inches
    xs = [v for a in ctc5.annotations for line in a.lines for v in line[0::3]]
    assert xs and max(abs(x) for x in xs) > 25.4


# NIST's expected semantic PMI for CTC-05, as its STEP File Analyzer lists it, in GCAD's notation
# (docs/design/iso10303-kernel.md §5.1 says how the two compare)
CTC5_EXPECTED = {
    "#946": "↗ 0.035 in | A-B",
    "#947": "↗ 0.025 in | A-B",
    "#948": "↗ 0.025 in | A-B",
    "#955": "⟂ 0.01 in | C",
    "#956": "⟂ 0.01 in | D",
    "#957": "⏤ 0.005 in",
    "#960": "◎ ⌀0.03 in | A",
    "#961": "○ 0.002 in",
    "#962": "⌰ 0.002 in | A",
    "#963": "⌰ 0.015 in | B",
    "#888": "⌀10 in ± 0.001 in",
    "#941": "5 in ± 0.008 in <ST>",
    "#942": "[1.25 in]",
    "#944": "[2 in]",
    "#1103": "Datum target C1",
    "#1104": "Datum target D1",
    "#969": "Datum system A-B",
}


def test_ctc5_matches_nist_expected(ctc5: EvalResult) -> None:
    a = by_id(ctc5)
    assert {k: a[k].label for k in CTC5_EXPECTED} == CTC5_EXPECTED


def test_every_tolerance_kind() -> None:
    """A handwritten fixture with one of each kind the reader names (tests/iso10303)."""
    result = run(Path(__file__).parent / "iso10303" / "pmi_kinds.stp")
    assert result.graph is not None, [d.message for d in result.diagnostics]
    tolerances = [a for a in result.annotations if a.kind == "tolerance"]
    assert len(tolerances) == 15
    for t in tolerances:
        assert dict(t.details)["type"] == dict(t.details)["name"], t.label
    a = by_id(result)
    assert a["#30"].label == "∠ 0.1 mm | A"  # simple subtype with a datum system
    assert a["#44"].label == "⌖ ⌀0.1 mm | A"  # complex, diametral zone
    assert dict(a["#44"].details)["modifiers"] == "maximum_material_requirement"
    assert a["#21"].label == "Datum target A2"
    assert Counter(x.kind for x in result.annotations)["callout"] == 4  # one loose occurrence


# --- faces: the B-rep keeps the #n of each face, so PMI can point at it ----------------------


@needs_occt
def test_face_groups_carry_entities(ctc1: EvalResult) -> None:
    from gcad.backends import get_backend

    assert ctc1.graph is not None
    mesh = get_backend("manifold").realize(ctc1.graph).mesh()
    entities = {g.entity for g in mesh.groups}
    assert len(entities) == 117 and None not in entities
    targets = {t.name for a in ctc1.annotations for t in a.targets}
    assert targets <= entities


@pytest.fixture
def quick_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The studio's checker evaluates form only, as for a file above its rules limit."""
    from gcad.iso10303 import check

    monkeypatch.setattr(check, "RULES_LIMIT", 0)


@needs_occt
def test_studio_sends_annotations_and_face_spans(tmp_path: Path, quick_check: None) -> None:
    from gcad.server import Studio

    studio = Studio(tmp_path, Limits(max_seconds=120))
    source = CTC1.read_text(encoding="utf-8")
    out = studio.rpc_document_evaluate("iso10303", source, CTC1.name)
    assert out["ok"], out["diagnostics"]
    assert len(out["annotations"]) == 50
    face = next(g for g in out["groups"] if g.get("entity") == "#861")
    assert source[face["spans"][0]["start"] :].startswith("#861=ADVANCED_FACE")
    tol = next(a for a in out["annotations"] if a["id"] == "#57")
    assert tol["targets"][0]["span"]["inFile"] is True


def test_without_opencascade_the_annotations_still_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quick_check: None
) -> None:
    """The browser has no OpenCASCADE: the B-rep is left out with a warning, the PMI shown."""
    from gcad.server import Studio

    studio = Studio(tmp_path, Limits(max_seconds=120))
    monkeypatch.setitem(sys.modules, "gcad.backends.occt_backend", None)
    out = studio.rpc_document_evaluate("iso10303", CTC1.read_text(encoding="utf-8"), CTC1.name)
    assert out["ok"]
    assert out["triangles"] == 0
    assert len(out["annotations"]) == 50
    assert any(
        d["severity"] == "warning" and "OpenCASCADE" in d["message"] for d in out["diagnostics"]
    )
