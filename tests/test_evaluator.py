import time

from conftest import error_of, ok, run

from gcad.core.evaluator import Limits


def root(source: str):  # type: ignore[no-untyped-def]
    graph = ok(source).graph
    assert graph is not None
    return graph.root


def test_arithmetic_and_vectors() -> None:
    node = root("(translate (+ [1 2 3] (* 2 [1 1 1]) (- [0 0 1])) (box (/ 10 4)))")
    assert node.op == "translate" and node.vec("v") == [3.0, 4.0, 4.0]
    assert node.children[0].vec("size") == [2.5, 2.5, 2.5]


def test_math_uses_degrees() -> None:
    node = root("(translate [(sin 90) (cos 180) (atan2 1 1)] (sphere 1))")
    assert node.vec("v") == [1.0, -1.0, 45.0]


def test_define_hoisting_recursion_and_defaults() -> None:
    node = root(
        """
        (box [(fact 4) (twice 3) (twice)])
        (define (fact n) (if (<= n 1) 1 (* n (fact (- n 1)))))
        (define (twice (x 5)) (* 2 x))
        """
    )
    assert node.vec("size") == [24.0, 6.0, 10.0]


def test_let_is_sequential_and_lambdas_close_over() -> None:
    node = root("(let ((a 2) (b (* a 3)) (f (fn (x) (+ x b)))) (box (f a)))")
    assert node.vec("size") == [8.0, 8.0, 8.0]


def test_for_comprehension_nests_and_feeds_booleans() -> None:
    node = root("(union (for ((i (range 0 2)) (j [10 20])) (translate [i j 0] (box 1))))")
    assert node.op == "union" and len(node.children) == 6
    assert [c.vec("v")[:2] for c in node.children][:2] == [[0.0, 10.0], [0.0, 20.0]]


def test_empty_loop_gives_no_children() -> None:
    node = root("(translate [1 0 0] (box 2) (for ((i (range 1 0))) (sphere i)))")
    assert node.op == "translate" and len(node.children) == 1


def test_module_children_and_special_variables() -> None:
    node = root(
        """
        (module (lift (dz 1)) (translate [0 0 dz] children))
        (lift :dz 5 (sphere 2 :$fn 12) (cylinder :h 1 :r 1))
        """
    )
    assert node.op == "translate" and node.vec("v") == [0.0, 0.0, 5.0]
    assert [c.op for c in node.children] == ["sphere", "cylinder"]
    assert node.children[0].num("fn") == 12.0


def test_dollar_fn_is_read_from_the_calling_scope() -> None:
    node = root("(define $fn 40) (circle 3)")
    assert node.num("fn") == 40.0


def test_several_top_level_shapes_are_a_union() -> None:
    node = root("(box 1) (sphere 1)")
    assert node.op == "union" and [c.op for c in node.children] == ["box", "sphere"]


def test_echo_reports_info() -> None:
    result = ok('(echo "w =" (* 2 3) :k [1 2]) (box 1)')
    assert any("ECHO: w =, 6, k = [1 2]" in str(d) for d in result.diagnostics)


def test_errors_carry_spans() -> None:
    assert "test.gcsg:2:9: error: 'wdth' is not defined" == error_of("(box 1)\n(box [1 wdth 3])")
    assert "mixes 2D and 3D" in error_of("(union (box 1) (rect 1))")
    assert "linear_extrude needs 2D" in error_of("(linear_extrude 5 (box 1))")
    assert "has no parameter 'radius'" in error_of("(sphere :radius 2)")
    assert "division by zero" in error_of("(box (/ 1 0))")
    assert "only rotate about z" in error_of("(rotate [90 0 0] (rect 1))")


def test_unbounded_recursion_stops_with_a_diagnostic() -> None:
    start = time.monotonic()
    message = error_of("(define (f x) (f x)) (f 1)")
    assert "call depth exceeded 1000" in message
    assert time.monotonic() - start < 5


def test_iteration_and_node_limits() -> None:
    assert "loop iterations" in error_of("(for ((i (range 0 1e9))) i)")
    limits = Limits(max_nodes=50)
    assert "more than 50 geometry nodes" in error_of(
        "(union (for ((i (range 0 100))) (translate [i 0 0] (box 1))))", limits
    )


def test_time_limit() -> None:
    limits = Limits(max_seconds=0.2, max_iterations=10**10)
    source = "(for ((i (range 1 1000)) (j (range 1 1000)) (k (range 1 1000))) 0)"
    message = error_of(source, limits)
    assert "time limit" in message


def test_top_level_non_shape_is_a_warning() -> None:
    result = run("(box 1) 42")
    assert result.ok
    assert any("ignored" in str(d) for d in result.diagnostics)
