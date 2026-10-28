"""Readers for geometry handed over as a file: triangle meshes (STL, OFF, 3MF).

Every reader returns vertices (float64, N x 3, mm) and triangles (int64, M x 3) wound
counter-clockwise seen from outside, with shared vertices merged. Polygonal faces are fanned.
"""

from __future__ import annotations

import io
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from gcad.diagnostics import GcadError

MESH_FORMATS = ("stl", "off", "3mf")
Mesh = tuple[np.ndarray, np.ndarray]


def read_mesh(data: bytes, fmt: str) -> Mesh:
    fmt = fmt.lower()
    if fmt == "stl":
        return _weld(_stl_triangles(data))
    if fmt == "off":
        return _off(data)
    if fmt == "3mf":
        return _3mf(data)
    raise GcadError(f"cannot read '{fmt}' meshes (readable: {', '.join(MESH_FORMATS)})")


_MESH_CACHE: dict[tuple[str, str], Mesh] = {}
_MESH_CACHE_SIZE = 16


def imported_mesh(data: bytes, digest: str, fmt: str) -> Mesh:
    """The mesh in `data` (whose SHA-256 is `digest`), read once per process and kept for the
    next evaluations of the same file."""
    key = (digest, fmt)
    if key not in _MESH_CACHE:
        vertices, tris = read_mesh(data, fmt)
        vertices.flags.writeable = False
        tris.flags.writeable = False
        while len(_MESH_CACHE) >= _MESH_CACHE_SIZE:
            del _MESH_CACHE[next(iter(_MESH_CACHE))]
        _MESH_CACHE[key] = (vertices, tris)
    return _MESH_CACHE[key]


def _weld(corners: np.ndarray) -> Mesh:
    """M x 3 x 3 triangle corners -> shared vertices and index triangles."""
    flat = corners.reshape(-1, 3)
    vertices, inverse = np.unique(flat, axis=0, return_inverse=True)
    tris = inverse.reshape(-1, 3).astype(np.int64)
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return vertices.astype(np.float64), tris[keep]


def _stl_triangles(data: bytes) -> np.ndarray:
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:
            rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
            return np.frombuffer(data, rec, count, 84)["v"].astype(np.float64)
    text = data.decode("ascii", errors="replace")
    values = [line.split()[1:4] for line in text.splitlines() if line.strip().startswith("vertex")]
    if not values or len(values) % 3:
        raise GcadError("not a readable STL file")
    return np.asarray(values, dtype=np.float64).reshape(-1, 3, 3)


def _fan(faces: list[list[int]]) -> np.ndarray:
    tris = [[f[0], f[k], f[k + 1]] for f in faces for k in range(1, len(f) - 1)]
    return np.asarray(tris, dtype=np.int64).reshape(-1, 3)


def _off(data: bytes) -> Mesh:
    lines = [
        words
        for line in data.decode("ascii", errors="replace").splitlines()
        if (words := line.split("#", 1)[0].split())
    ]
    if not lines or lines[0][0] != "OFF":
        raise GcadError("not an OFF file (only plain 'OFF' is read)")
    counts = lines[0][1:] or lines.pop(1)  # counts on the header line or the next one
    try:
        nv, nf = int(counts[0]), int(counts[1])
        body = lines[1 : 1 + nv + nf]
        vertices = np.asarray([row[:3] for row in body[:nv]], dtype=np.float64).reshape(nv, 3)
        # A face line is "n i1 ... in", optionally followed by a colour.
        faces = [[int(w) for w in row[1 : 1 + int(row[0])]] for row in body[nv:]]
    except (IndexError, ValueError):
        raise GcadError("truncated or malformed OFF file") from None
    if len(faces) != nf:
        raise GcadError("truncated OFF file")
    return vertices, _fan(faces)


def _3mf(data: bytes) -> Mesh:
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".model")), None)
            if name is None:
                raise GcadError("3MF package has no .model part")
            root = ET.fromstring(zf.read(name))
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise GcadError(f"not a readable 3MF file: {exc}") from None
    scale = {
        "micron": 1e-3,
        "millimeter": 1.0,
        "centimeter": 10.0,
        "meter": 1000.0,
        "inch": 25.4,
        "foot": 304.8,
    }.get(root.get("unit", "millimeter"), 1.0)
    all_vertices: list[np.ndarray] = []
    all_tris: list[np.ndarray] = []
    base = 0
    for mesh in root.iter(f"{ns}mesh"):
        verts = [
            (float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
            for v in mesh.iter(f"{ns}vertex")
        ]
        tris = [
            (int(t.get("v1", 0)), int(t.get("v2", 0)), int(t.get("v3", 0)))
            for t in mesh.iter(f"{ns}triangle")
        ]
        all_vertices.append(np.asarray(verts, dtype=np.float64).reshape(-1, 3) * scale)
        all_tris.append(np.asarray(tris, dtype=np.int64).reshape(-1, 3) + base)
        base += len(verts)
    if not all_vertices:
        raise GcadError("3MF file holds no mesh")
    # 3MF build items may carry transforms; OpenSCAD and most writers use none.
    return np.concatenate(all_vertices), np.concatenate(all_tris)
