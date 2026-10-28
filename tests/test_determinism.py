"""SPEC AC-7 (EX-2): exporting twice with SOURCE_DATE_EPOCH set gives byte-identical files,
for every format and back-end. Each run is a fresh interpreter with its own hash seed, so
nothing that varies between processes (set and dict order of str keys, ids, caches) can leak
into a file unnoticed, as it could when one process exports twice."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from gcad.exporters import FORMATS

EXAMPLES = Path(__file__).parents[1] / "src" / "gcad" / "kernels" / "minscad" / "examples"
HAS_OCCT = importlib.util.find_spec("OCP") is not None

EXPORT_ALL = """
import sys
from pathlib import Path
from gcad.exporters import FORMATS
from gcad.pipeline import export_file

part, plate, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
occt = sys.argv[4] == "1"
for fmt in FORMATS.values():
    ext = fmt.extensions[0]
    if fmt.needs == "2d":
        export_file(plate, out / f"plate{ext}")
    elif fmt.needs == "brep":
        if occt:
            export_file(part, out / f"part{ext}")
    elif fmt.needs == "graph":  # written from the graph: no back-end; its extension is shared
        export_file(part, out / f"part-{fmt.id}{ext}", fmt.id)
    else:
        export_file(part, out / f"part-manifold{ext}", backend_name="manifold")
        if occt:
            export_file(part, out / f"part-occt{ext}", backend_name="occt")
"""


def export_all(out: Path, hash_seed: str) -> dict[str, bytes]:
    out.mkdir()
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1767225600", "PYTHONHASHSEED": hash_seed}
    subprocess.run(
        [
            sys.executable,
            "-c",
            EXPORT_ALL,
            str(EXAMPLES / "flange.mscad"),
            str(EXAMPLES / "plate2d.mscad"),
            str(out),
            "1" if HAS_OCCT else "0",
        ],
        env=env,
        check=True,
        timeout=600,
    )
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def test_every_format_is_byte_identical_across_processes(tmp_path: Path) -> None:
    first = export_all(tmp_path / "a", "1")
    second = export_all(tmp_path / "b", "2")
    written = {Path(name).suffix for name in first}
    expected = {f.extensions[0] for f in FORMATS.values() if HAS_OCCT or f.needs != "brep"}
    assert expected <= written  # every format was exported (OBJ adds its .mtl)
    assert first.keys() == second.keys()
    differing = [name for name in first if first[name] != second[name]]
    assert not differing, f"not reproducible: {differing}"
