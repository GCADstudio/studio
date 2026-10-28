"""The `openscad` process kernel: runs the user's installed OpenSCAD (https://openscad.org).

Speaks the GCAD Studio process-kernel protocol (SPEC §7.2) on stdin/stdout. Standard library
only, so it runs under any Python. OpenSCAD renders the whole file to a 3MF mesh (micrometre
precision; OFF when the build has no 3MF), which is handed back as `mesh_path`. Files the
source includes are found next to the source file, through OPENSCADPATH.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MODEL = "model.scad"
LOCATION = re.compile(r'in file "?([^",]*?)"?, line (\d+)')


def find_openscad() -> str | None:
    if found := os.environ.get("OPENSCAD"):
        return found if Path(found).is_file() else shutil.which(found)
    for name in ("openscad", "openscad-nightly"):
        if found := shutil.which(name):
            return found
    candidates = [
        Path("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
        Path("/Applications/OpenSCAD-nightly.app/Contents/MacOS/OpenSCAD"),
    ]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if base := os.environ.get(var):
            # openscad.com is the console build: it writes its messages to the pipe.
            candidates += [
                Path(base, "OpenSCAD", "openscad.com"),
                Path(base, "OpenSCAD", "openscad.exe"),
            ]
    return next((str(p) for p in candidates if p.is_file()), None)


def diagnostics_from(text: str) -> list[dict[str, object]]:
    """OpenSCAD's ERROR/WARNING/ECHO lines as protocol diagnostics."""
    out: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        for prefix, severity in (("ERROR:", "error"), ("WARNING:", "warning"), ("ECHO:", "info")):
            if line.startswith(prefix):
                d: dict[str, object] = {"severity": severity, "message": line}
                where = LOCATION.search(line)
                if where and Path(where.group(1)).name == MODEL:
                    d["line"] = int(where.group(2))
                    d["message"] = LOCATION.sub("", line).rstrip(" ,:") or line
                out.append(d)
    if "not a 3D object" in text:
        out.append(
            {
                "severity": "error",
                "message": "this is a 2D model; the openscad kernel returns 3D meshes only",
            }
        )
    elif "top level object is empty" in text:
        out.append({"severity": "error", "message": "the model is empty"})
    return out


def evaluate(params: dict[str, object], program: str) -> dict[str, object]:
    workdir = Path(str(params["workdir"]))
    (workdir / MODEL).write_text(str(params["source"]), encoding="utf-8")
    env = dict(os.environ)
    if folder := params.get("folder"):
        env["OPENSCADPATH"] = os.pathsep.join(
            p for p in (str(folder), env.get("OPENSCADPATH", "")) if p
        )
    text = ""
    for out in ("model.3mf", "model.off"):
        run = subprocess.run(
            [program, "-o", out, MODEL],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = run.stdout + run.stderr
        if run.returncode == 0 and (workdir / out).is_file():
            return {"diagnostics": diagnostics_from(text), "mesh_path": out}
        if "3mf" not in text.lower():  # failed for another reason than the 3MF writer
            break
    diagnostics = diagnostics_from(text)
    if not any(d["severity"] == "error" for d in diagnostics):
        tail = " ".join(text.strip().splitlines()[-3:])
        diagnostics.append({"severity": "error", "message": f"OpenSCAD failed: {tail}"})
    return {"diagnostics": diagnostics}


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    program = find_openscad()
    for line in sys.stdin:
        request = json.loads(line)
        method, rid = request.get("method"), request.get("id")
        if method == "shutdown":
            send({"jsonrpc": "2.0", "id": rid, "result": None})
            return
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"name": "openscad", "version": "0.1.0"}})
        elif method == "evaluate":
            if program is None:
                message = (
                    "OpenSCAD is not installed. Install it from https://openscad.org, "
                    "or set OPENSCAD to the OpenSCAD program."
                )
                result: dict[str, object] = {
                    "diagnostics": [{"severity": "error", "message": message}]
                }
            else:
                result = evaluate(request["params"], program)
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        else:
            error = {"code": -32601, "message": f"unknown method {method}"}
            send({"jsonrpc": "2.0", "id": rid, "error": error})


if __name__ == "__main__":
    main()
