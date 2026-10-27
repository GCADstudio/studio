"""Independent proof with FreeCAD (SPEC AC-23): freecad-nxstyle's own importer rebuilds a parametric
STEP file GCAD wrote, and its parameters drive FreeCAD's model. Not collected by pytest; run with
FreeCAD's command line, options by environment variable (freecadcmd treats arguments as files):

    GCAD_FC_OUT=out.json NXSTYLE_PATH=<freecad-nxstyle>/NXStyle \
    GCAD_FC_FILES='{"ac2": "src/gcad/kernels/iso10303/examples/test_part.stp"}' \
    freecadcmd tests/iso10303/fc_import.py

out.json then holds, per file, the volumes of the rebuilt solids, the parameters FreeCAD's
spreadsheet received, and for "ac2" the volume after hole_d is set to 10 mm.
"""

import json, os, sys, traceback

out = os.environ["GCAD_FC_OUT"]
res = {}
try:
    sys.path.insert(0, os.environ["NXSTYLE_PATH"])
    import FreeCAD as App
    from nxstyle import parametric_fc

    for key, path in json.loads(os.environ["GCAD_FC_FILES"]).items():
        doc = App.newDocument(key)
        made, report = parametric_fc.import_file(path, doc)
        doc.recompute()
        vols = [o.Shape.Volume for o in made if hasattr(o, "Shape")]
        res[key] = {
            "volumes": vols,
            "report": report,
            "spreadsheet": [(o.Name, o.TypeId) for o in doc.Objects if "Spreadsheet" in o.TypeId],
        }
        sheets = [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"]
        if sheets and key == "ac2":
            s = sheets[0]
            alias = [c for c in s.getUsedCells() if s.getAlias(c)]
            res[key]["aliases"] = {s.getAlias(c): s.get(c) for c in alias}
            for c in alias:
                if s.getAlias(c) in ("hole-d", "hole_d"):
                    s.set(c, "10")
            doc.recompute()
            res[key]["volumes_after_10"] = [o.Shape.Volume for o in made if hasattr(o, "Shape")]
except Exception:
    res["error"] = traceback.format_exc()
with open(out, "w") as fh:
    json.dump(res, fh, indent=1, default=str)
