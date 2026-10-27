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
