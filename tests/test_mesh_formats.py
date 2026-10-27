"""SPEC AC-3 for X3D, VRML, COLLADA and UNV, read back by independent libraries.

MeshLab (pymeshlab) reads X3D, VRML and COLLADA; pycollada and pyuff are pure Python;
Gmsh reads UNV. Each reader is skipped where it has no build for the platform.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from gcad.pipeline import export_file

EXACT = 12000 - 160 * math.pi  # SPEC AC-2 test part, mm^3


def volume(v: np.ndarray, f: np.ndarray) -> float:
    t = np.asarray(v, dtype=float)[np.asarray(f)]
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6)


@pytest.fixture
def test_part(examples: Path) -> Path:
    return examples / "test_part.gcsg"


def export(src: Path, out: Path) -> int:
    evaluated, report = export_file(src, out)
    assert report is not None and report.triangles is not None, evaluated.result.diagnostics
    return report.triangles


# (extension, factor turning mm^3 into the reader's units, expected bounds in reader units)
MESHLAB_CASES = [
    # X3D: mm declared by the UNIT statement (MeshLab ignores it), +Z turned to +Y
    ("x3d", 1.0, [[0, 0, -30], [40, 10, 0]]),
    # VRML97: metres by convention, +Y up
    ("wrl", 1e-9, [[0, 0, -0.03], [0.04, 0.01, 0]]),
    # COLLADA: mm and +Z up declared in <asset>
    ("dae", 1.0, [[0, 0, 0], [40, 30, 10]]),
]


@pytest.mark.parametrize(("ext", "scale", "bounds"), MESHLAB_CASES)
def test_meshlab_reads_back(
    test_part: Path, tmp_path: Path, ext: str, scale: float, bounds: list[list[float]]
) -> None:
    pymeshlab = pytest.importorskip("pymeshlab")
    out = tmp_path / f"part.{ext}"
    triangles = export(test_part, out)
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(out))
    mesh = ms.current_mesh()
    v, f = mesh.vertex_matrix(), mesh.face_matrix()
    assert len(f) == triangles
    topology = ms.get_topological_measures()
    assert topology["boundary_edges"] == 0 and topology["non_two_manifold_edges"] == 0
    assert abs(volume(v, f) / scale - EXACT) / EXACT < 0.005
    assert np.allclose([v.min(axis=0), v.max(axis=0)], bounds, rtol=0, atol=1e-5)


def test_collada_units_and_colours(examples: Path, tmp_path: Path) -> None:
    collada = pytest.importorskip("collada")
    src = tmp_path / "two.gcsg"
    src.write_text('(color "red" (box 10)) (color "blue" (translate [20 0 0] (sphere 4)))')
    out = tmp_path / "two.dae"
    triangles = export(src, out)
    doc = collada.Collada(str(out))
    assert (doc.assetInfo.unitname, doc.assetInfo.unitmeter) == ("millimeter", 0.001)
    assert str(doc.assetInfo.upaxis).endswith("Z_UP")
    primitives = [p for g in doc.geometries for p in g.primitives]
    assert len(primitives) == 2 and sum(len(p) for p in primitives) == triangles
    colors = sorted(tuple(m.effect.diffuse[:3]) for m in doc.materials)
    assert colors == [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0)]


def test_x3d_with_two_colours_reads_whole(tmp_path: Path) -> None:
    pymeshlab = pytest.importorskip("pymeshlab")
    src = tmp_path / "two.gcsg"
    src.write_text('(color "red" (box 10)) (color "blue" (translate [20 0 0] (box 4)))')
    out = tmp_path / "two.x3d"
    triangles = export(src, out)
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(out))
    assert ms.current_mesh().face_number() == triangles
    assert out.read_text().count("<Shape>") == 2


def test_unv_reads_back_with_pyuff(test_part: Path, tmp_path: Path) -> None:
    pyuff = pytest.importorskip("pyuff")
    out = tmp_path / "part.unv"
    triangles = export(test_part, out)
    sets = {s["type"]: s for s in pyuff.UFF(str(out)).read_sets()}
    assert sets[164]["units_code"] == 5 and sets[164]["length"] == 1000.0
    nodes = sets[2411]
    xyz = np.column_stack([nodes["x"], nodes["y"], nodes["z"]])
    elements = sets[2412][91]  # pyuff groups elements by FE descriptor
    faces = np.asarray([e["nodes_nums"] for e in elements], dtype=int) - 1
    assert len(faces) == triangles
    assert abs(volume(xyz, faces) - EXACT) / EXACT < 0.005


def test_unv_reads_back_with_gmsh(test_part: Path, tmp_path: Path) -> None:
    gmsh = pytest.importorskip("gmsh")
    out = tmp_path / "part.unv"
    triangles = export(test_part, out)
    gmsh.initialize(readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(out))
        tags, coords, _ = gmsh.model.mesh.getNodes()
        types, _, nodes = gmsh.model.mesh.getElements()
    finally:
        gmsh.finalize()
    assert list(types) == [2]  # 3-node triangles
    position = {int(t): i for i, t in enumerate(tags)}
    xyz = np.asarray(coords).reshape(-1, 3)
    faces = np.array([position[int(n)] for n in nodes[0]]).reshape(-1, 3)
    assert len(faces) == triangles
    assert abs(volume(xyz, faces) - EXACT) / EXACT < 0.005
