"""COLLADA 1.4.1 (ISO/PAS 17506). Declares millimetres and +Z up, so no conversion is needed.

COLLADA requires created/modified dates; they come from the export time (EX-2).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from gcad.core.sexpr import format_number
from gcad.exporters import ExportJob

DEFAULT_RGBA = (0.8, 0.8, 0.8, 1.0)


def write(job: ExportJob) -> list[Path]:
    mesh = job.model.mesh()
    stamp = job.time.strftime("%Y-%m-%dT%H:%M:%SZ")
    title = escape(job.title)
    runs = mesh.color_runs()
    effects, materials, bindings, triangles = [], [], [], []
    for i, (color, tris) in enumerate(runs):
        rgba = " ".join(format_number(round(float(c), 6)) for c in (color or DEFAULT_RGBA))
        effects.append(
            f'    <effect id="color_{i}-fx"><profile_COMMON><technique sid="common"><lambert>'
            f"<diffuse><color>{rgba}</color></diffuse></lambert></technique></profile_COMMON>"
            "</effect>"
        )
        materials.append(
            f'    <material id="color_{i}" name="color_{i}">'
            f'<instance_effect url="#color_{i}-fx"/></material>'
        )
        bindings.append(f'            <instance_material symbol="color_{i}" target="#color_{i}"/>')
        indices = " ".join(str(int(k)) for k in mesh.triangles[tris].ravel())
        triangles.append(
            f'        <triangles material="color_{i}" count="{len(tris)}">'
            '<input semantic="VERTEX" source="#mesh-vertices" offset="0"/>'
            f"<p>{indices}</p></triangles>"
        )
    positions = " ".join(format_number(float(c)) for c in mesh.vertices.ravel())
    n = len(mesh.vertices)
    source = escape(Path(job.source).name) if job.source else ""
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">',
        "  <asset>",
        "    <contributor>",
        f"      <authoring_tool>{escape(job.generator)}</authoring_tool>",
        *([f"      <source_data>{source}</source_data>"] if source else []),
        "    </contributor>",
        f"    <created>{stamp}</created>",
        f"    <modified>{stamp}</modified>",
        f"    <title>{title}</title>",
        '    <unit name="millimeter" meter="0.001"/>',
        "    <up_axis>Z_UP</up_axis>",
        "  </asset>",
        "  <library_effects>",
        *effects,
        "  </library_effects>",
        "  <library_materials>",
        *materials,
        "  </library_materials>",
        "  <library_geometries>",
        f'    <geometry id="mesh" name="{title}">',
        "      <mesh>",
        '        <source id="mesh-positions">',
        f'          <float_array id="mesh-positions-array" count="{3 * n}">'
        f"{positions}</float_array>",
        "          <technique_common>",
        f'            <accessor source="#mesh-positions-array" count="{n}" stride="3">',
        '              <param name="X" type="float"/>',
        '              <param name="Y" type="float"/>',
        '              <param name="Z" type="float"/>',
        "            </accessor>",
        "          </technique_common>",
        "        </source>",
        '        <vertices id="mesh-vertices">'
        '<input semantic="POSITION" source="#mesh-positions"/></vertices>',
        *triangles,
        "      </mesh>",
        "    </geometry>",
        "  </library_geometries>",
        "  <library_visual_scenes>",
        '    <visual_scene id="scene" name="scene">',
        f'      <node id="model" name="{title}">',
        '        <instance_geometry url="#mesh">',
        "          <bind_material>",
        "            <technique_common>",
        *bindings,
        "            </technique_common>",
        "          </bind_material>",
        "        </instance_geometry>",
        "      </node>",
        "    </visual_scene>",
        "  </library_visual_scenes>",
        '  <scene><instance_visual_scene url="#scene"/></scene>',
        "</COLLADA>",
    ]
    job.path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return [job.path]
