"""Explicit B-rep in the browser: OpenCASCADE compiled to WebAssembly (SPEC §4.1, FR-I5).

OCP has no Pyodide build, so the web studio reads a STEP file's B-rep with occt-import-js, the
same OpenCASCADE behind a small JS API (LGPL-2.1, a separate file the site serves). The web
worker loads it only when a STEP file is evaluated, and registers it as the JS module
`_gcad_occt`. It gives triangles per B-rep face, in millimetres, in the order OpenCASCADE
explores the faces, which for a solid is the order its CLOSED_SHELL lists them: so each face
keeps the `#n` it was written as, as with back-end B natively.
"""

from __future__ import annotations

import re

import numpy as np

from gcad.diagnostics import GcadError, Span
from gcad.mesh import Mesh, MeshGroup, weld

_SHELL = re.compile(rb"(?:CLOSED|OPEN)_SHELL\s*\(\s*'(?:[^']|'')*'\s*,\s*\(([^)]*)\)")


def available() -> bool:
    try:
        import _gcad_occt  # noqa: F401  (registered by js/worker.js)
    except ImportError:
        return False
    return True


def shell_faces(data: bytes) -> list[list[str]]:
    """The faces of each shell of a Part 21 file, as `#n`, in the order the file lists them."""
    return [
        [f.strip().decode("ascii") for f in m.group(1).split(b",")] for m in _SHELL.finditer(data)
    ]


def read_step(data: bytes, tolerance: float, node_id: str, span: Span | None = None) -> Mesh:
    """The B-rep of a STEP file as one welded mesh, a group per face labelled with its `#n`
    when the faces OpenCASCADE made match the file's shells one for one."""
    import _gcad_occt as occt
    import js
    from pyodide.ffi import to_js

    params = to_js(
        {
            "linearUnit": "millimeter",
            "linearDeflectionType": "absolute_value",
            "linearDeflection": float(tolerance),
            "angularDeflection": 0.5,
        },
        dict_converter=js.Object.fromEntries,
    )
    result = occt.ReadStepFile(to_js(data), params)
    if not result.success:
        raise GcadError("OpenCASCADE (WebAssembly) could not read the STEP data", span)
    points: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    face_of_triangle: list[np.ndarray] = []
    faces = 0
    base = 0
    for mesh in result.meshes:
        pos = np.asarray(mesh.attributes.position.array.to_py(), dtype=np.float64).reshape(-1, 3)
        tri = np.asarray(mesh.index.array.to_py(), dtype=np.int64).reshape(-1, 3) + base
        owner = np.zeros(len(tri), dtype=np.int64)
        for face in mesh.brep_faces:
            owner[int(face.first) : int(face.last) + 1] = faces
            faces += 1
        points.append(pos)
        triangles.append(tri)
        face_of_triangle.append(owner)
        base += len(pos)
    if not triangles or sum(len(t) for t in triangles) == 0:
        raise GcadError("OpenCASCADE (WebAssembly) found no faces in the STEP data", span)
    vertices, tris, keep = weld(np.concatenate(points), np.concatenate(triangles), tolerance)
    owner = np.concatenate(face_of_triangle)[keep]
    names: list[str | None] = [n for shell in shell_faces(data) for n in shell]
    if len(names) != faces:  # an assembly or a file this reading does not match: no labels
        names = [None] * faces
    groups = [MeshGroup(node_id, None, name) for name in names]
    used = sorted(set(owner.tolist()))
    remap = np.zeros(max(faces, 1), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return Mesh(vertices, tris, [groups[i] for i in used], remap[owner])
