"""Round-trip every exporter through an independent reader (SPEC EX-6, AC-3 for M1 formats)."""

import importlib.util
import json
import math
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

from gcad.pipeline import export_file

EXACT = 12000 - 160 * math.pi  # SPEC AC-2 test part


@pytest.fixture
def test_part(examples: Path) -> Path:
    return examples / "test_part.gcsg"


def export(src: Path, out: Path, **options: object) -> int:
    evaluated, report = export_file(src, out, options=options)
    assert report is not None, [str(d) for d in evaluated.result.diagnostics]
    assert report.triangles is not None
    return report.triangles


@pytest.mark.parametrize("name", ["part.stl", "part_ascii.stl", "part.obj"])
def test_mesh_formats_read_back(test_part: Path, tmp_path: Path, name: str) -> None:
    out = tmp_path / name
    triangles = export(test_part, out, ascii=name.endswith("_ascii.stl"))
    mesh = trimesh.load(out, force="mesh", process=True)
    assert len(mesh.faces) == triangles
    assert mesh.is_watertight
    assert abs(mesh.volume - EXACT) / EXACT < 0.005
    assert np.allclose(mesh.bounds, [[0, 0, 0], [40, 30, 10]])


def test_binary_stl_layout(test_part: Path, tmp_path: Path) -> None:
    out = tmp_path / "part.stl"
    triangles = export(test_part, out)
    data = out.read_bytes()
    assert not data.startswith(b"solid")
    assert data[:80].startswith(b"GCAD Studio")
    assert struct.unpack("<I", data[80:84])[0] == triangles
    assert len(data) == 84 + 50 * triangles


def test_obj_carries_colours(examples: Path, tmp_path: Path) -> None:
    out = tmp_path / "bracket.obj"
    export(examples / "bracket.gcsg", out)
    mtl = (tmp_path / "bracket.mtl").read_text()
    assert "Kd 0.25 0.45 0.85" in mtl
    assert "# units: mm" in out.read_text()


def test_glb_reads_back_in_metres_y_up(test_part: Path, tmp_path: Path) -> None:
    out = tmp_path / "part.glb"
    triangles = export(test_part, out)
    scene = trimesh.load(out)
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    assert len(mesh.faces) == triangles
    # x stays x, z (height 10 mm) becomes +y, y (depth 30 mm) becomes -z; all in metres.
    assert np.allclose(mesh.bounds, [[0, 0, -0.03], [0.04, 0.01, 0]], atol=1e-7)
    assert abs(mesh.volume - EXACT * 1e-9) / (EXACT * 1e-9) < 0.005


def test_glb_container_is_well_formed(examples: Path, tmp_path: Path) -> None:
    out = tmp_path / "flange.glb"
    export(examples / "flange.gcsg", out)
    data = out.read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert (magic, version, length) == (b"glTF", 2, len(data))
    json_len, kind = struct.unpack("<I4s", data[12:20])
    assert kind == b"JSON" and json_len % 4 == 0
    doc = json.loads(data[20 : 20 + json_len])
    assert doc["asset"]["version"] == "2.0"
    assert doc["nodes"][0]["extras"]["units"] == "mm"
    bin_len, bin_kind = struct.unpack("<I4s", data[20 + json_len : 28 + json_len])
    assert bin_kind == b"BIN\x00" and bin_len >= doc["buffers"][0]["byteLength"]


def test_2d_model_is_refused_by_mesh_formats(tmp_path: Path) -> None:
    src = tmp_path / "plate.gcsg"
    src.write_text("(rect [10 20])")
    with pytest.raises(Exception, match="needs a 3D model"):
        export_file(src, tmp_path / "plate.stl")


def test_brep_formats_are_refused_by_the_mesh_backend(test_part: Path, tmp_path: Path) -> None:
    with pytest.raises(Exception, match="needs exact geometry"):
        export_file(test_part, tmp_path / "part.step", backend_name="manifold")


def test_exports_are_byte_identical_across_processes(
    examples: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC AC-7: with SOURCE_DATE_EPOCH set, two separate runs give the same bytes."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    src = examples / "flange.gcsg"
    formats = ["stl", "obj", "glb", "x3d", "wrl", "dae", "unv"]
    if importlib.util.find_spec("OCP") is not None:
        formats += ["step", "igs"]
    runs = []
    for i in range(2):
        folder = tmp_path / f"run{i}"
        for fmt in formats:
            subprocess.run(
                [sys.executable, "-m", "gcad", "export", str(src), "-o", str(folder / f"f.{fmt}")],
                check=True,
                capture_output=True,
            )
        runs.append({p.name: p.read_bytes() for p in sorted(folder.iterdir())})
    assert sorted(runs[0]) == sorted([f"f.{f}" for f in formats] + ["f.mtl"])
    for name in runs[0]:
        assert runs[0][name] == runs[1][name], name
