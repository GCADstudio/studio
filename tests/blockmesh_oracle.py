"""OpenFOAM's blockMesh as the oracle for the blockmesh kernel.

`run(dictionary_text, folder)` makes a case in `folder`, runs blockMesh there and returns what
it built, read back from constant/polyMesh: the bounding box, the total volume and the volume
of each cell zone, in metres. GCAD_BLOCKMESH names the blockMesh command (shell words, for
example `blockMesh` or `wsl -d Ubuntu -- blockMesh`); it runs in the case folder.

A cell's volume here is exact for the polyhedron whose faces are fanned into triangles about
the mean of their corners. That is the solid blockMesh's cells bound: for a plane face any
triangulation gives it, and for a face that is not plane the fan bounds exactly the volume of
the bilinear surface through its corners. checkMesh's "Total volume" uses face centres and area
vectors instead, which agree to rounding for plane faces.

Run as a script, it prints the numbers for every example of the kernel, with checkMesh's own
lines when GCAD_CHECKMESH names that command; CI uses this for the golden values.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CONTROL_DICT = """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object controlDict;
}
application blockMesh;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 1;
deltaT 1;
writeControl timeStep;
writeInterval 1;
writeFormat ascii;
writePrecision 17;
writeCompression off;
timeFormat general;
timePrecision 6;
"""


@dataclass
class Mesh:
    lo: np.ndarray
    hi: np.ndarray
    volume: float
    zones: dict[str, float] = field(default_factory=dict)
    log: str = ""


def command(env: str = "GCAD_BLOCKMESH") -> list[str] | None:
    value = os.environ.get(env, "").strip()
    return shlex.split(value, posix=os.name != "nt") if value else None


def make_case(text: str, folder: Path) -> Path:
    (folder / "system").mkdir(parents=True, exist_ok=True)
    (folder / "constant").mkdir(exist_ok=True)
    (folder / "system" / "blockMeshDict").write_text(text, encoding="utf-8")
    (folder / "system" / "controlDict").write_text(CONTROL_DICT, encoding="utf-8")
    return folder


def run(text: str, folder: Path, blockmesh: list[str] | None = None, zones: bool = True) -> Mesh:
    cmd = blockmesh or command()
    if not cmd:
        raise RuntimeError("set GCAD_BLOCKMESH to the blockMesh command")
    make_case(text, folder)
    proc = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=600)
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 or "FOAM FATAL" in log:
        raise RuntimeError(f"blockMesh failed:\n{log[-3000:]}")
    mesh = read_mesh(folder / "constant" / "polyMesh", zones)
    mesh.log = log
    return mesh


# ---------- reading an ASCII polyMesh ----------

_HEADER = re.compile(r"FoamFile\s*\{.*?\}", re.S)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _body(path: Path) -> str:
    if not path.exists() and path.with_suffix(".gz").exists():
        raise RuntimeError(f"{path} is compressed; write it uncompressed")
    text = _COMMENTS.sub(" ", path.read_text(encoding="utf-8", errors="replace"))
    return _HEADER.sub(" ", text, count=1)


def _list_start(text: str) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*\(", text)
    if not m:
        raise RuntimeError("no list found")
    return int(m.group(1)), m.end()


def read_points(path: Path) -> np.ndarray:
    text = _body(path)
    n, start = _list_start(text)
    nums = re.findall(r"\(([^()]*)\)", text[start:])[:n]
    return np.array([[float(x) for x in s.split()] for s in nums], dtype=np.float64)


def read_faces(path: Path) -> list[list[int]]:
    text = _body(path)
    n, start = _list_start(text)
    return [
        [int(x) for x in s.split()] for s in re.findall(r"\d+\s*\(([^()]*)\)", text[start:])[:n]
    ]


def read_labels(path: Path) -> np.ndarray:
    text = _body(path)
    n, start = _list_start(text)
    return np.array(text[start:].split(")")[0].split()[:n], dtype=np.int64)


def read_zones(path: Path) -> dict[str, np.ndarray]:
    if not path.exists():
        return {}
    text = _body(path)
    zones = {}
    for m in re.finditer(r"(\S+)\s*\{(.*?)\}", text, re.S):
        body = m.group(2)
        cells = re.search(r"cellLabels\s+List<label>\s*(\d+)\s*\(([^)]*)\)", body)
        if cells:
            zones[m.group(1)] = np.array(cells.group(2).split(), dtype=np.int64)
        else:
            uniform = re.search(r"cellLabels\s+List<label>\s*(\d+)\s*\{(\d+)\}", body)
            if uniform:
                zones[m.group(1)] = np.full(int(uniform.group(1)), int(uniform.group(2)))
    return zones


def face_fluxes(points: np.ndarray, faces: list[list[int]]) -> np.ndarray:
    """Each face's share of the divergence theorem, (1/3) * integral of x . n, with the face
    fanned into triangles about the mean of its corners; faces of one size go together."""
    flux = np.zeros(len(faces))
    by_size: dict[int, list[int]] = {}
    for i, f in enumerate(faces):
        by_size.setdefault(len(f), []).append(i)
    for idx in by_size.values():
        p = points[np.array([faces[i] for i in idx])]  # (F, size, 3)
        c = p.mean(axis=1, keepdims=True)
        q = np.roll(p, -1, axis=1)
        flux[idx] = np.einsum("fki,fki->f", c + p + q, np.cross(p - c, q - c)) / 18.0
    return flux


def read_mesh(folder: Path, zones: bool = True) -> Mesh:
    points = read_points(folder / "points")
    faces = read_faces(folder / "faces")
    owner = read_labels(folder / "owner")
    neighbour = read_labels(folder / "neighbour")
    flux = face_fluxes(points, faces)
    # Faces between cells cancel; the boundary faces alone give the total.
    total = float(flux[len(neighbour) :].sum())
    by_zone: dict[str, float] = {}
    if zones:
        vol = np.zeros(int(owner.max()) + 1)
        np.add.at(vol, owner, flux)
        np.add.at(vol, neighbour, -flux[: len(neighbour)])
        by_zone = {n: float(vol[c].sum()) for n, c in read_zones(folder / "cellZones").items()}
    return Mesh(points.min(axis=0), points.max(axis=0), total, by_zone)


def checkmesh_numbers(log: str) -> dict[str, object]:
    out: dict[str, object] = {}
    box = re.search(r"Overall domain bounding box \(([^)]*)\) \(([^)]*)\)", log)
    if box:
        out["bounding_box"] = [[float(x) for x in box.group(i).split()] for i in (1, 2)]
    total = re.search(r"Total volume = ([-+0-9.eE]+)", log)
    if total:
        out["total_volume"] = float(total.group(1))
    return out


def main(argv: list[str]) -> int:
    """Print the oracle's numbers for every example, as JSON lines."""
    import tempfile

    folder = (
        Path(argv[0]) if argv else Path(__file__).parents[1] / "src/gcad/kernels/blockmesh/examples"
    )
    checkmesh = command("GCAD_CHECKMESH")
    for path in sorted(folder.glob("*.blockMeshDict")):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                mesh = run(path.read_text(encoding="utf-8"), Path(tmp))
            except RuntimeError as exc:
                print(json.dumps({"example": path.name, "error": str(exc)[-500:]}))
                continue
            row: dict[str, object] = {
                "example": path.name,
                "volume": mesh.volume,
                "lo": mesh.lo.tolist(),
                "hi": mesh.hi.tolist(),
                "zones": mesh.zones,
            }
            if checkmesh:
                proc = subprocess.run(checkmesh, cwd=tmp, capture_output=True, text=True)
                row["checkMesh"] = checkmesh_numbers(proc.stdout)
            print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
