"""Generic engine features added for the iso10303 kernel and documented for every kernel
(docs/kernel-authoring.md): letrec, at, records, resolve, apply, contains, string helpers,
parse_value, import_source, trace mode, named string decoders and kernel checkers."""

from __future__ import annotations

from gcad.core.evaluator import evaluate
from gcad.core.sexpr import read
from gcad.core.trace import Traced


def run(text: str, **kw):  # type: ignore[no-untyped-def]
    return evaluate(read(text, "t.gcore"), **kw)


def value(text: str):  # type: ignore[no-untyped-def]
    result = run(f"(echo {text})")
    assert result.ok, result.diagnostics
    return result.diagnostics[0].message.removeprefix("ECHO: ")


def test_letrec_is_order_free_and_evaluates_each_binding_once() -> None:
    assert value("(letrec ((b (+ a 1)) (a 2)) (* b 10))") == "30"
    assert value("(letrec ((b (+ a 1)) (a 2)))") == "[3 2]"  # no body: every value, in order


def test_letrec_names_a_duplicate_and_a_cycle() -> None:
    dup = run("(letrec ((a 1)\n (a 2)) a)")
    assert dup.diagnostics[0].message == "'a' is defined twice (first on line 1)"
    cycle = run("(letrec ((a (+ b 1)) (b (+ c 1)) (c a)) a)")
    assert cycle.diagnostics[0].message == "cyclic reference: a -> b -> c -> a"


def test_at_attributes_geometry_and_errors_to_a_record() -> None:
    prelude = read(
        '(define (make r) (at r (box 1)))\n(define (bad r) (at r (fail "no")))', "p.gcore"
    )
    ok = evaluate(read('(make (record "x"))', "user.src"), prelude=prelude)
    assert ok.graph.root.span.file == "user.src"
    bad = evaluate(read('(bad (record "x"))', "user.src"), prelude=prelude)
    assert bad.diagnostics[0].span.file == "user.src" and bad.diagnostics[0].message == "no"


def test_data_helpers() -> None:
    assert value('((resolve "max") 1 5)') == "5"
    assert value('(resolve "nothing" 7)') == "7"
    assert value("(apply + [1 2 3])") == "6"
    assert value("(contains [1 2 3] 2)") == "true"
    assert value('(split "a.b" ".")') == "[a b]"
    assert value('(parse_number "2.5")') == "2.5"
    assert value('(parse_value "[1 \\"x\\" [true]]")') == "[1 x [true]]"
    assert value('(parse_value "(not a literal)")') == "undef"


def test_import_source_keeps_the_source_by_its_digest() -> None:
    result = evaluate(read('(import_source "step")', "a.stp"), source="ISO-10303-21; ...")
    node = result.graph.root
    assert node.op == "import" and node.arg("data") in result.graph.blobs


def test_trace_mode_keeps_parameters_without_changing_the_graph() -> None:
    text = "(define d 8) (cylinder :h 12 :d d)"
    plain, traced = run(text), run(text, trace=True)
    assert plain.graph.hash() == traced.graph.hash()
    r = traced.graph.root.arg("r1")
    assert isinstance(r, Traced) and r.expr == ("/", ("param", "d", 8.0), ("num", 2.0))
