"""Generate the ISO 10303 coverage matrix of the iso10303 kernel (SPEC FR-I8, design note §5).

    python tools/iso10303_coverage.py            # writes docs/design/iso10303-coverage.tsv

Every entity of the AP242 long form and of the resource parts (ISO 10303-41, 42, 43, 50, 55,
108 with both corrigenda, 111, 112, ISO 13584-20) gets one status:

    supported     the kernel builds it into the model, or uses it to (a placement, a unit, a
                  parameter); a fixture in the repository holds it (tests/test_iso10303_coverage.py)
    delegated     explicit B-rep geometry and topology: back-end B (OpenCASCADE) reads it
    diagnostic    met in a model, it stops evaluation with a message saying why
    out-of-scope  carries no shape GCAD shows; checked against its schema, and the reason says why

The entities come from the compiled schemas in the cache (gcad iso schema fetch AP242, gcad iso
schema build): only names are written, no text of the standards. What the kernel supports comes
from its own files: prelude.gcore's solid/ and expr/ handlers and UNSUPPORTED list, plus the
supporting entities named below.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gcad.iso10303 import schemas  # noqa: E402
from gcad.iso10303._nxstyle import express  # noqa: E402
from gcad.iso10303._nxstyle import iso10303 as nx  # noqa: E402

PRELUDE = ROOT / "src/gcad/kernels/iso10303/prelude.gcore"
OUT = ROOT / "docs/design/iso10303-coverage.tsv"
AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"

# Entities the kernel reads to build what the handlers make: (names, what they are for).
SUPPORTING = [
    (
        "cartesian_point direction vector axis1_placement axis2_placement_2d "
        "axis2_placement_3d plane "
        "cartesian_transformation_operator_3d box_domain",
        "placements and transforms",
    ),
    (
        "length_unit plane_angle_unit solid_angle_unit named_unit si_unit conversion_based_unit "
        "measure_with_unit length_measure_with_unit plane_angle_measure_with_unit "
        "dimensional_exponents "
        "uncertainty_measure_with_unit",
        "units (lengths to mm, angles to degrees)",
    ),
    (
        "representation_context geometric_representation_context global_unit_assigned_context "
        "global_uncertainty_assigned_context parametric_representation_context",
        "contexts",
    ),
    (
        "representation shape_representation csg_shape_representation csg_2d_shape_representation "
        "definitional_representation",
        "representations",
    ),
    (
        "curve_bounded_surface outer_boundary_curve boundary_curve composite_curve_segment pcurve "
        "bounded_pcurve trimmed_curve line circle polyline",
        "profiles of swept solids",
    ),
    (
        "procedural_shape_representation procedural_representation_sequence "
        "procedural_shape_representation_sequence procedural_solid_representation_sequence "
        "user_selected_shape_elements user_selected_elements",
        "ISO 10303-55 histories",
    ),
    (
        "variational_parameter unbound_variational_parameter bound_variational_parameter "
        "unbound_parameter_environment bound_parameter_environment "
        "unbound_variational_parameter_semantics "
        "instance_attribute_reference free_form_assignment equal_parameter_constraint "
        "variational_representation variational_current_representation_relationship "
        "variational_representation_item elementary_space maths_variable maths_real_variable",
        "ISO 10303-108 parameters and assignments",
    ),
    (
        "generic_expression expression numeric_expression simple_numeric_expression "
        "generic_variable "
        "variable numeric_variable real_numeric_variable simple_generic_expression",
        "ISO 13584-20 operands",
    ),
    (
        "styled_item presentation_style_assignment surface_style_usage surface_side_style "
        "surface_style_fill_area fill_area_style fill_area_style_colour colour_rgb "
        "mechanical_design_geometric_presentation_representation",
        "colours of whole solids",
    ),
    ("application_context application_protocol_definition product_context", "file context"),
    (
        "draughting_callout annotation_occurrence annotation_curve_occurrence "
        "tessellated_annotation_occurrence annotation_plane draughting_model camera_model_d3 "
        "view_volume planar_box geometric_set geometric_curve_set tessellated_geometric_set "
        "repositioned_tessellated_item tessellated_curve_set triangulated_surface_set "
        "complex_triangulated_surface_set coordinates_list curve_style "
        "draughting_pre_defined_curve_font draughting_model_item_association",
        "graphical PMI and saved views: drawn in the viewer as annotations (M15)",
    ),
    (
        "geometric_tolerance geometric_tolerance_with_datum_reference "
        "geometric_tolerance_with_modifiers angularity_tolerance circular_runout_tolerance "
        "coaxiality_tolerance concentricity_tolerance cylindricity_tolerance flatness_tolerance "
        "line_profile_tolerance parallelism_tolerance perpendicularity_tolerance "
        "position_tolerance roundness_tolerance straightness_tolerance surface_profile_tolerance "
        "symmetry_tolerance total_runout_tolerance tolerance_zone tolerance_zone_form datum "
        "datum_feature datum_system datum_reference_compartment datum_target "
        "placed_datum_target_feature shape_aspect composite_shape_aspect "
        "shape_aspect_relationship geometric_item_specific_usage "
        "item_identified_representation_usage dimensional_size angular_size "
        "dimensional_location angular_location dimensional_characteristic_representation "
        "shape_dimension_representation plus_minus_tolerance tolerance_value "
        "measure_representation_item",
        "semantic PMI: listed with the faces it applies to and the callouts presenting it (M15)",
    ),
]

BREP_SCHEMAS = {"topology_schema", "geometry_schema"}
OUT_OF_SCOPE = [  # (pattern on the entity name, reason)
    (
        r"tolerance|datum|dimension|dimensional|annotation|draughting|callout|pmi|geometric_item_specific|"
        r"shape_aspect|feature_definition|placed_datum|tolerance_zone|limits_and_fits",
        "PMI, GD&T and MBD: validated against the schema; the PMI reader (M15) does not "
        "show this kind yet",
    ),
    (
        r"kinematic|pair|joint|mechanism|link|motion",
        "kinematics: no motion in GCAD's graph; validated against the schema only",
    ),
    (
        r"tessellated|triangulated|tessellation",
        "tessellated geometry: GCAD's graph imports no meshes yet (M9)",
    ),
    (
        r"product|approval|person|organization|organisation|date|time|document|contract|security|"
        r"certification|action|effectivity|identification|classification|assignment|role|group|"
        r"address|version|requirement|external|library|class|characterized|property|context_dependent|"
        r"assembly|usage|occurrence|configuration|make_from|supplied|alternate|event|state",
        "product data management and structure: carries no shape of its own; "
        "validated against the schema",
    ),
    (
        r"presentation|style|colour|color|font|text|camera|view|light|symbol|drawing|curve_style|fill|"
        r"hatch|tile|planar|area|occlusion|invisibility|render|texture|background|pre_defined|externally",
        "presentation: only the colour of a whole solid is shown; validated against the schema",
    ),
]


def handlers() -> tuple[set[str], dict[str, str]]:
    text = PRELUDE.read_text(encoding="utf-8")
    supported = {m.lower() for m in re.findall(r"\(define \((?:solid|expr)/([A-Z0-9_]+)", text)}
    block = text[text.index("(define UNSUPPORTED") : text.index("(define (unsupported-why")]
    pairs = re.findall(r'"([A-Z0-9_]+)" "([^"]+)"', block)
    unsupported = {
        k.lower(): f"refused: {v} has no operation in GCAD's geometry graph" for k, v in pairs
    }
    return supported, unsupported


BREP_SOLIDS = {
    "manifold_solid_brep",
    "brep_with_voids",
    "faceted_brep",
    "shell_based_surface_model",
    "face_based_surface_model",
    "advanced_brep_shape_representation",
    "faceted_brep_shape_representation",
    "manifold_surface_shape_representation",
}


def status(
    name: str, schema: str, d: express.Dictionary, supported: set[str], diag: dict[str, str]
) -> tuple[str, str]:
    if name in BREP_SOLIDS:
        return "delegated", "explicit B-rep: read by back-end B (OpenCASCADE), a file at a time"
    if name in supported:
        return "supported", "built by the kernel (prelude.gcore)"
    for names, why in SUPPORTING:
        if name in names.split():
            return "supported", f"read by the kernel: {why}"
    if name in diag:
        return "diagnostic", diag[name]
    sup = d.supertypes(name) if name in d.entities else {name}
    if schema in ("solid_shape_element_schema",) or "solid_model" in sup or "modified_solid" in sup:
        return (
            "diagnostic",
            "refused: a solid the kernel does not build stops evaluation with its name",
        )
    if schema == "procedural_sketch_schema":
        return (
            "diagnostic",
            "refused: a sketch command the kernel does not evaluate stops evaluation",
        )
    if schema in ("iso13584_expressions_schema", "iso13584_generic_expressions_schema"):
        return "diagnostic", "refused in an assignment: only numeric expressions drive geometry"
    if schema in (
        "explicit_geometric_constraint_schema",
        "explicit_constraint_schema",
        "sketch_schema",
    ):
        return "out-of-scope", (
            "constraints and sketches are kept in the file and checked; GCAD evaluates "
            "assignments, it does not solve constraint systems; the current geometry is shown"
        )
    if schema == "mathematical_functions_schema":
        return (
            "out-of-scope",
            "ISO 10303-50 maths other than parameter value spaces: validated only",
        )
    if (
        schema in BREP_SCHEMAS
        or "topological_representation_item" in sup
        or "geometric_representation_item" in sup
    ):
        return "delegated", "explicit geometry and topology: read by back-end B (OpenCASCADE)"
    for pattern, why in OUT_OF_SCOPE:
        if re.search(pattern, name):
            return "out-of-scope", why
    return "out-of-scope", "outside parametric shape exchange: validated against the schema only"


def main() -> int:
    ap_path = schemas.schema_dir() / f"{AP242}.json"
    if not ap_path.is_file() or not schemas.has_resources():
        print(
            "needs the AP242 long form and the resource parts in the schema cache "
            "(gcad iso schema fetch AP242; gcad iso schema build ...)",
            file=sys.stderr,
        )
        return 1
    ap = express.Dictionary.load(str(ap_path))
    res = schemas.resources()
    supported, diag = handlers()
    rows = []
    names = sorted(set(ap.entities) | set(res.entities))
    for name in names:
        in_res = name in res.entities
        schema = res.entities[name]["schema"] if in_res else "ap242 long form"
        part = nx.PART.get(schema, "ISO 10303-242 (AP242)") if in_res else "ISO 10303-242 (AP242)"
        d = res if in_res else ap
        st, why = status(name, schema, d, supported, diag)
        rows.append((name, part, "yes" if name in ap.entities else "no", st, why))
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
    counts = {
        s: sum(1 for r in rows if r[3] == s)
        for s in ("supported", "delegated", "diagnostic", "out-of-scope")
    }
    lines = [
        "# ISO 10303 coverage of the iso10303 kernel; "
        "generated by tools/iso10303_coverage.py, do not edit.",
        f"# entities: {len(rows)} (AP242 long form {len(ap.entities)}, "
        f"resource parts {len(res.entities)}); "
        f"sha256 of the sorted names: {digest}",
        "# " + ", ".join(f"{k} {v}" for k, v in counts.items()),
        "entity\tpart\tin_ap242\tstatus\treason",
        *("\t".join(r) for r in rows),
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(rows)} entities; "
        + ", ".join(f"{k} {v}" for k, v in counts.items())
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
