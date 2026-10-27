"""glTF 2.0 binary (.glb). Also the viewer's preview feed.

glTF is metres and +Y up by definition. Vertex data stays in millimetres, Z up; the single
root node carries the matrix that scales by 0.001 and turns Z-up into Y-up, so coordinates
read back exactly and the unit conversion is explicit.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from gcad.exporters import ExportJob

DEFAULT_RGBA = (0.8, 0.8, 0.8, 1.0)
# Column-major: x -> x, y -> -z, z -> y, scaled from mm to m.
ROOT_MATRIX = [0.001, 0, 0, 0, 0, 0, -0.001, 0, 0, 0.001, 0, 0, 0, 0, 0, 1]

FLOAT, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    positions = mesh.vertices.astype("<f4")
    blob = bytearray(positions.tobytes())
    buffer_views: list[dict[str, object]] = [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(blob), "target": ARRAY_BUFFER}
    ]
    accessors: list[dict[str, object]] = [
        {
            "bufferView": 0,
            "componentType": FLOAT,
            "count": len(positions),
            "type": "VEC3",
            "min": [float(x) for x in positions.min(axis=0)] if len(positions) else [0, 0, 0],
            "max": [float(x) for x in positions.max(axis=0)] if len(positions) else [0, 0, 0],
        }
    ]
    materials: list[dict[str, object]] = []
    primitives: list[dict[str, object]] = []
    for i, (color, tris) in enumerate(mesh.color_runs()):
        rgba = [round(float(c), 6) for c in (color or DEFAULT_RGBA)]
        material: dict[str, object] = {
            "name": f"color_{i}",
            "pbrMetallicRoughness": {
                "baseColorFactor": rgba,
                "metallicFactor": 0.0,
                "roughnessFactor": 0.7,
            },
            "doubleSided": False,
        }
        if rgba[3] < 1:
            material["alphaMode"] = "BLEND"
        materials.append(material)
        indices = mesh.triangles[tris].astype("<u4").ravel()
        offset = len(blob)
        blob += indices.tobytes()
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": indices.nbytes,
                "target": ELEMENT_ARRAY_BUFFER,
            }
        )
        accessors.append(
            {
                "bufferView": len(buffer_views) - 1,
                "componentType": UINT32,
                "count": int(indices.size),
                "type": "SCALAR",
            }
        )
        node_ids = sorted({mesh.groups[g].node_id for g in set(mesh.tri_group[tris].tolist())})
        primitives.append(
            {
                "attributes": {"POSITION": 0},
                "indices": len(accessors) - 1,
                "material": i,
                "mode": 4,
                "extras": {"gcad_nodes": node_ids},
            }
        )
    doc = {
        "asset": {"version": "2.0", "generator": job.generator},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "name": job.title,
                "mesh": 0,
                "matrix": ROOT_MATRIX,
                "extras": {"units": "mm", "up": "+Z", "source": Path(job.source).name},
            }
        ],
        "meshes": [{"name": job.title, "primitives": primitives}],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if not primitives:  # an empty model: a node without a mesh is still a valid glTF
        del doc["meshes"], doc["materials"], doc["accessors"], doc["bufferViews"], doc["buffers"]
        del doc["nodes"][0]["mesh"]  # type: ignore[index]
        blob = bytearray()
    json_chunk = _pad(json.dumps(doc, separators=(",", ":")).encode(), b" ")
    bin_chunk = _pad(bytes(blob), b"\x00")
    chunks = struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
    if bin_chunk:
        chunks += struct.pack("<I4s", len(bin_chunk), b"BIN\x00") + bin_chunk
    with job.path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)))
        fh.write(chunks)
    return [job.path]
