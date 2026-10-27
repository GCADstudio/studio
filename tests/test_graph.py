from conftest import ok


def graph_hash(source: str) -> str:
    graph = ok(source).graph
    assert graph is not None
    return graph.hash()


def test_same_model_written_differently_has_the_same_hash() -> None:
    a = "(difference (box [40 30 10]) (translate [20 15 -1] (cylinder :h 12 :r 4)))"
    b = """
        ; same part, different spelling
        (define w 40.0)
        (difference
          (box :size [w 30 10] :center false)
          (translate [(/ w 2) 15 -1] (cylinder 12 :d 8)))
    """
    assert graph_hash(a) == graph_hash(b)


def test_hash_changes_with_geometry_and_with_order() -> None:
    base = graph_hash("(difference (box 10) (sphere 6))")
    assert base != graph_hash("(difference (box 10) (sphere 6.5))")
    assert base != graph_hash("(difference (sphere 6) (box 10))")


def test_single_child_booleans_collapse() -> None:
    assert graph_hash("(union (box 1))") == graph_hash("(box 1)")


def test_identical_subtrees_share_a_node() -> None:
    graph = ok(
        "(define b (box 1)) (union b (translate [2 0 0] b) (translate [4 0 0] (box 1)))"
    ).graph
    assert graph is not None
    ops = [n.op for n in graph.nodes().values()]
    assert ops.count("box") == 1


def test_graph_json_keeps_spans_but_hash_ignores_them() -> None:
    graph = ok("(box 1)\n\n(sphere 2)").graph
    assert graph is not None
    doc = graph.to_json(spans=True)
    assert doc["format"] == "gcad-graph" and doc["units"] == "mm"
    spans = doc["spans"]
    assert isinstance(spans, dict) and any(s[0]["line"] == 3 for s in spans.values())
    moved = ok("\n\n(box 1) (sphere 2)").graph
    assert moved is not None and moved.hash() == graph.hash()
