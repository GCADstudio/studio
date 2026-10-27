"""Check GCore conventions against OpenSCAD itself. Skipped where OpenSCAD is not installed."""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import trimesh
from conftest import ok

from gcad.backends import get_backend
from gcad.kernels import BUNDLED, discover
from gcad.pipeline import evaluate_source


def find_openscad() -> str | None:
    if os.environ.get("OPENSCAD"):
        return os.environ["OPENSCAD"]
    for name in ("openscad.com", "openscad"):
        if found := shutil.which(name):
            return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "OpenSCAD" / "openscad.com",
        Path("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


OPENSCAD = find_openscad()
pytestmark = pytest.mark.skipif(OPENSCAD is None, reason="OpenSCAD not installed")

CASES = {
    "rotate_x": ("rotate([90,0,0]) cube([1,2,3]);", "(rotate [90 0 0] (box [1 2 3]))"),
    "rotate_xyz": ("rotate([30,45,60]) cube([1,2,3]);", "(rotate [30 45 60] (box [1 2 3]))"),
    "rotate_axis": ("rotate(90,[0,0,1]) cube([1,2,3]);", "(rotate 90 [0 0 1] (box [1 2 3]))"),
    "mirror": ("mirror([1,1,0]) cube([1,2,3]);", "(mirror [1 1 0] (box [1 2 3]))"),
    "twist": (
        "linear_extrude(height=10, twist=90, slices=1)"
        " translate([5,0]) square([1,1], center=true);",
        "(linear_extrude :height 10 :twist 90 :slices 1"
        " (translate [5 0] (rect [1 1] :center true)))",
    ),
    "rotate_extrude": (
        "rotate_extrude($fn=64) translate([1,0]) square(1);",
        "(rotate_extrude (translate [1 0] (rect 1)) :$fn 64)",
    ),
    # Positional cylinder(h, r1, r2): r2 defaults to 1, not to r1.
    "cylinder_positional": (
        "cylinder(10, 5, $fn=64);",
        "(cylinder 10 5 :$fn 64)",
    ),
    "polyhedron": (
        "polyhedron([[0,0,0],[10,0,0],[10,7,0],[0,7,0],[0,0,5],[10,0,5],[10,7,5],[0,7,5]],"
        "[[0,1,2,3],[4,5,1,0],[7,6,5,4],[5,6,2,1],[6,7,3,2],[7,4,0,3]]);",
        "(polyhedron [[0 0 0] [10 0 0] [10 7 0] [0 7 0] [0 0 5] [10 0 5] [10 7 5] [0 7 5]]"
        " [[0 1 2 3] [4 5 1 0] [7 6 5 4] [5 6 2 1] [6 7 3 2] [7 4 0 3]])",
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_same_bounds_as_openscad(name: str, tmp_path: Path) -> None:
    scad, gcsg = CASES[name]
    src, stl = tmp_path / f"{name}.scad", tmp_path / f"{name}.stl"
    src.write_text(scad)
    assert OPENSCAD is not None
    subprocess.run(
        [OPENSCAD, "-o", str(stl), str(src)], check=True, capture_output=True, timeout=120
    )
    reference = trimesh.load(stl, force="mesh")
    graph = ok(gcsg).graph
    assert graph is not None
    model = get_backend("manifold").realize(graph)
    box = model.bounds()
    assert box is not None
    # OpenSCAD 2021 writes STL with float32-rounded text, hence the tolerance.
    assert np.allclose(np.concatenate(box), reference.bounds.ravel(), atol=1e-4)
    if name != "twist":  # one-slice twists are triangulated differently; bounds still agree
        assert model.volume() == pytest.approx(reference.volume, rel=1e-4)


MINSCAD_EXAMPLES = sorted((BUNDLED / "minscad" / "examples").glob("*.mscad"))


@pytest.mark.parametrize("path", MINSCAD_EXAMPLES, ids=lambda p: p.name)
def test_minscad_examples_match_openscad(path: Path, tmp_path: Path) -> None:
    """Every minscad example is also an OpenSCAD file; both must make the same solid.

    $fn is fixed so that both use the same circle tessellation."""
    text = "$fn = 64;\n" + path.read_text(encoding="utf-8")
    evaluated = evaluate_source(text, path.name, discover()[0]["minscad"])
    assert evaluated.result.ok and evaluated.result.graph is not None
    model = get_backend("manifold").realize(evaluated.result.graph)
    box = model.bounds()
    assert box is not None
    two_d = evaluated.result.graph.root.dim == 2
    src = tmp_path / f"{path.stem}.scad"
    out = tmp_path / f"{path.stem}.{'dxf' if two_d else 'stl'}"
    src.write_text(text)
    assert OPENSCAD is not None
    subprocess.run(
        [OPENSCAD, "-o", str(out), str(src)], check=True, capture_output=True, timeout=300
    )
    if two_d:
        # OpenSCAD writes oriented LINE segments; Green's theorem gives the net area.
        ezdxf = pytest.importorskip("ezdxf")
        lines = ezdxf.readfile(out).modelspace().query("LINE")
        seg = np.array(
            [[e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y] for e in lines], dtype=float
        )
        area = 0.5 * float(np.sum(seg[:, 0] * seg[:, 3] - seg[:, 2] * seg[:, 1]))
        pts = seg.reshape(-1, 2)
        assert np.allclose(box[0][:2], pts.min(axis=0), atol=1e-6)
        assert np.allclose(box[1][:2], pts.max(axis=0), atol=1e-6)
        assert len(seg) == sum(len(c) for c in model.contours())
        # OpenSCAD writes DXF coordinates to 6 significant digits (e.g. 16.1162).
        assert model.area() == pytest.approx(area, rel=1e-6)
        return
    reference = trimesh.load(out, force="mesh")
    assert np.allclose(np.concatenate(box), reference.bounds.ravel(), atol=1e-4)
    assert model.volume() == pytest.approx(reference.volume, rel=1e-5)
