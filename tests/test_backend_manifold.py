import math

import numpy as np
import pytest
from conftest import ok

from gcad.backends import get_backend
from gcad.core.graph import circle_segments
from gcad.diagnostics import GcadError


def realize(source: str, tolerance: float | None = None):  # type: ignore[no-untyped-def]
    graph = ok(source).graph
    assert graph is not None
    return get_backend("manifold").realize(graph, tolerance)


def bounds(source: str) -> list[float]:
    b = realize(source).bounds()
    assert b is not None
    return [round(float(x), 9) for x in np.concatenate(b)]


def test_test_part_volume_is_within_tessellation_error() -> None:
    model = realize("(difference (box [40 30 10]) (translate [20 15 -1] (cylinder :h 12 :r 4)))")
    exact = 12000 - 160 * math.pi
    assert model.is_valid()
    assert abs(model.volume() - exact) / exact < 0.005


def test_tolerance_controls_segment_count() -> None:
    coarse = realize("(cylinder :h 1 :r 10)", tolerance=1.0).mesh().triangle_count
    fine = realize("(cylinder :h 1 :r 10)", tolerance=0.001).mesh().triangle_count
    assert fine > coarse
    assert circle_segments(10, fn=7, fa=0, fs=0, tolerance=1) == 7
    assert circle_segments(10, fn=0, fa=12, fs=2, tolerance=1) == 30  # OpenSCAD rule


def test_rotation_order_matches_openscad() -> None:
    # Rotate about X first: the 2 mm side ends up along +z and the 3 mm side along -y.
    assert bounds("(rotate [90 0 0] (box [1 2 3]))") == [0, -3, 0, 1, 0, 2]
    assert bounds("(rotate 90 [0 0 1] (box [1 2 3]))") == [-2, 0, 0, 0, 1, 3]


def test_transforms() -> None:
    assert bounds("(translate [1 2 3] (scale [2 1 1] (box 1)))") == [1, 2, 3, 3, 3, 4]
    assert bounds("(mirror [1 0 0] (box [1 2 3]))") == [-1, 0, 0, 0, 2, 3]
    m = "(matrix [[1 0 0 5] [0 1 0 0] [0 0 1 0] [0 0 0 1]] (box 1))"
    assert bounds(m) == [5, 0, 0, 6, 1, 1]


def test_openscad_style_polyhedron() -> None:
    # The cube from the OpenSCAD manual: faces listed clockwise seen from outside.
    source = """
    (polyhedron
      [[0 0 0] [10 0 0] [10 7 0] [0 7 0] [0 0 5] [10 0 5] [10 7 5] [0 7 5]]
      [[0 1 2 3] [4 5 1 0] [7 6 5 4] [5 6 2 1] [6 7 3 2] [7 4 0 3]])
    """
    model = realize(source)
    assert model.is_valid()
    assert model.volume() == pytest.approx(350)


def test_open_polyhedron_is_an_error() -> None:
    with pytest.raises(GcadError, match="not a closed"):
        realize("(polyhedron [[0 0 0] [1 0 0] [0 1 0] [0 0 1]] [[0 1 2] [0 1 3]])")


def test_rotate_extrude_follows_openscad_profile_plane() -> None:
    model = realize("(rotate_extrude (translate [1 0] (rect 1)) :$fn 256)")
    assert model.volume() == pytest.approx(3 * math.pi, rel=1e-3)
    assert bounds("(rotate_extrude (translate [1 0] (rect 1)) :$fn 4)")[5] == 1


def test_linear_extrude_twist_is_clockwise_like_openscad() -> None:
    model = realize(
        "(linear_extrude :height 10 :twist 90 (translate [5 0] (rect [1 1] :center true)))"
    )
    mesh = model.mesh()
    top = mesh.vertices[mesh.vertices[:, 2] > 9.999]
    # Clockwise seen from +z: the profile at +x ends up at -y.
    assert top[:, 1].max() < -4 and abs(top[:, 0]).max() < 1


def test_2d_booleans_and_polygon_paths() -> None:
    source = """
    (linear_extrude 2
      (polygon [[0 0] [10 0] [10 10] [0 10] [3 3] [7 3] [7 7] [3 7]]
               :paths [[0 1 2 3] [4 5 6 7]]))
    """
    assert realize(source).volume() == pytest.approx((100 - 16) * 2)
    plate = "(linear_extrude 1 (difference (rect 10) (translate [5 5] (circle 2 :$fn 64))))"
    assert realize(plate).volume() == pytest.approx(100 - math.pi * 4, rel=2e-3)


def test_triangles_trace_back_to_nodes_and_colours() -> None:
    source = (
        '(difference (color "red" (box 10)) (color [0 0 1] (translate [5 5 -1] (cylinder 12 2))))'
    )
    graph = ok(source).graph
    assert graph is not None
    mesh = get_backend("manifold").realize(graph).mesh()
    ops = {n.id: n.op for n in graph.nodes().values()}
    by_op = {ops[g.node_id]: g.color for g in mesh.groups}
    assert by_op == {"box": (1.0, 0.0, 0.0, 1.0), "cylinder": (0.0, 0.0, 1.0, 1.0)}
    assert len(mesh.tri_group) == mesh.triangle_count


def test_occt_backend_is_reported_as_planned() -> None:
    with pytest.raises(GcadError, match="M3"):
        get_backend("occt")
