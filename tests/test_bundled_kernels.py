"""The bundled kernels: csg as a grammar kernel, minscad (AC-1) and parts (AC-5)."""

import json
import math
from pathlib import Path

import pytest

from gcad.backends import get_backend
from gcad.cli import main
from gcad.core.sexpr import read, to_text
from gcad.kernels import BUNDLED, discover
from gcad.pipeline import evaluate_file, evaluate_source


def kernel(kid: str):  # type: ignore[no-untyped-def]
    return discover()[0][kid]


def graph_of(kid: str, source: str):  # type: ignore[no-untyped-def]
    evaluated = evaluate_source(source, f"t.{kid}", kernel(kid))
    assert evaluated.result.ok, [str(d) for d in evaluated.result.diagnostics]
    assert evaluated.result.graph is not None
    return evaluated.result.graph


def same_model(scad: str, gcsg: str) -> None:
    assert graph_of("minscad", scad).hash() == graph_of("csg", gcsg).hash()


# --- csg ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["bracket", "flange", "test_part"])
def test_csg_grammar_kernel_reads_like_the_gcore_reader(name: str) -> None:
    path = BUNDLED / "csg" / "examples" / f"{name}.gcsg"
    text = path.read_text(encoding="utf-8")
    via_grammar = [to_text(f, 10**6) for f in kernel("csg").to_gcore(text, path.name)]
    via_reader = [to_text(f, 10**6) for f in read(text, path.name)]
    assert via_grammar == via_reader


def test_csg_token_edge_cases() -> None:
    forms = kernel("csg").to_gcore('(1+ +3 -x .5 -2e1 :k "a\\"b") ; tail', "t.gcsg")
    assert to_text(forms[0]) == '(1+ 3 -x 0.5 -20 :k "a\\"b")'


# --- AC-1 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["bracket", "flange", "test_part"])
def test_ac1_same_model_in_two_languages_has_one_hash(name: str) -> None:
    a = evaluate_file(BUNDLED / "csg" / "examples" / f"{name}.gcsg").result.graph
    b = evaluate_file(BUNDLED / "minscad" / "examples" / f"{name}.mscad").result.graph
    assert a is not None and b is not None
    assert a.hash() == b.hash()


# --- minscad language -----------------------------------------------------------------------


def test_minscad_names_and_argument_orders_follow_openscad() -> None:
    same_model("cube(2);", "(box 2)")
    same_model("square([1, 2]);", "(rect [1 2])")
    same_model("cylinder(10, 5);", "(cylinder :h 10 :r1 5 :r2 1)")
    same_model("cylinder(10, 5, 3, true);", "(cylinder :h 10 :r1 5 :r2 3 :center true)")
    same_model(
        "linear_extrude(5, true, 10, 30) circle(2);",
        "(linear_extrude :height 5 :center true :twist 30 (circle 2))",
    )
    same_model(
        "multmatrix([[1,0,0,2],[0,1,0,0],[0,0,1,0]]) cube(1);",
        "(matrix [[1 0 0 2] [0 1 0 0] [0 0 1 0]] (box 1))",
    )


def test_minscad_expressions() -> None:
    same_model(
        "x = 1 + 2 * 3 - 8 / 4 % 3; cube([x, 2 ^ 3, -x + 10]);",
        "(box [5 8 5])",
    )
    same_model("v = [4, 5, 6]; cube([v.x, v[1], v.z]);", "(box [4 5 6])")
    same_model(
        "b = 3 > 2 && !(1 == 2) || false; cube(b ? 7 : 1);",
        "(box 7)",
    )
    same_model(
        "s = [for (i = [0 : 2 : 6]) i * i]; cube([len(s), s[3], s[2]]);",
        "(box [4 36 16])",
    )
    same_model(
        "f = function(x) x + 1; g = let (a = 2, b = a * 3) b; cube([f(1), g, 1]);",
        "(box [2 6 1])",
    )
    same_model(
        "function fact(n) = n <= 1 ? 1 : n * fact(n - 1); cube(fact(4));",
        "(box 24)",
    )


def test_minscad_statements_and_modules() -> None:
    same_model(
        "module pair(d = 3) { children(0); translate([d, 0, 0]) children(1); }"
        " pair(5) { cube(1); sphere(1); }",
        "(box 1) (translate [5 0 0] (sphere 1))",
    )
    same_model(
        "module count() cube($children); count() { cube(1); cube(2); cube(3); }",
        "(box 3)",
    )
    same_model(
        "for (i = [1 : 3], j = [0, 1]) if (i != 2) translate([i, j, 0]) cube(1); else sphere(i);",
        "(for ((i (range 1 3)) (j [0 1]))  (if (!= i 2) (translate [i j 0] (box 1)) (sphere i)))",
    )
    same_model("* cube(9); % sphere(9); # cube(1); ! sphere(2);", "(box 1) (sphere 2)")
    same_model("{ a = 2; cube(a); } ;;", "(box 2)")


def test_minscad_comments_and_special_variables() -> None:
    same_model(
        "/* block\n comment */ $fn = 12; // line comment\n circle(2);",
        "(circle 2 :$fn 12)",
    )


def test_minscad_echo_and_assert() -> None:
    evaluated = evaluate_source('echo("w", 2 * 3); cube(1);', "t.mscad", kernel("minscad"))
    assert any("ECHO: w, 6" in str(d) for d in evaluated.result.diagnostics)
    failed = evaluate_source('assert(1 > 2, "too small");', "t.mscad", kernel("minscad"))
    assert not failed.result.ok
    assert "assertion failed: too small" in str(failed.result.diagnostics[-1])


def test_minscad_errors_point_at_the_source() -> None:
    bad = evaluate_source("cube(1);\ntranslate([1, 2) cube(1);", "t.mscad", kernel("minscad"))
    assert str(bad.result.diagnostics[0]).startswith("t.mscad:2:16: error: unexpected ')'")
    undefined = evaluate_source("cube(size = wdth);", "t.mscad", kernel("minscad"))
    assert str(undefined.result.diagnostics[0]) == "t.mscad:1:13: error: 'wdth' is not defined"
    use = evaluate_source("use <MCAD/gears.scad>\ncube(1);", "t.mscad", kernel("minscad"))
    assert "use is not supported yet: <MCAD/gears.scad>" in str(use.result.diagnostics[0])


def test_a_program_may_redefine_prelude_names() -> None:
    same_model("module cube(s) sphere(s); cube(3);", "(sphere 3)")


# --- AC-5 -----------------------------------------------------------------------------------


def test_ac5_parts_kernel_is_three_files_and_passes_check() -> None:
    folder = BUNDLED / "parts"
    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    assert files == ["grammar.lark", "kernel.toml", "rewrite.toml"]
    assert main(["kernel", "check", str(folder)]) == 0


def test_ac5_parts_model_geometry() -> None:
    graph = evaluate_file(BUNDLED / "parts" / "examples" / "plate.parts").result.graph
    assert graph is not None
    volume = get_backend("manifold").realize(graph, tolerance=0.005).volume()
    base, boss_extra = 80 * 50 * 4, math.pi * 10**2 * (12 - 4)
    bore = math.pi * 4**2 * 12
    holes = 4 * math.pi * 2.5**2 * 4
    countersink = math.pi * 2 / 3 * (4**2 + 4 * 6 + 6**2) - math.pi * 4**2 * 2
    expected = base + boss_extra - bore - holes - countersink
    assert volume == pytest.approx(expected, rel=2e-3)


def test_parts_describe_lists_keywords(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["kernel", "describe", "parts"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert {"part", "cut", "box", "repeat"} <= set(info["highlight"]["keywords"])
    assert info["comment"] == "#"


def test_parts_syntax_error_has_a_position(tmp_path: Path) -> None:
    src = tmp_path / "bad.parts"
    src.write_text("part a: box 1 2 3\npart b: box 1 2\n")
    evaluated = evaluate_file(src)
    assert str(evaluated.result.diagnostics[0]).startswith(f"{src}:2:")
