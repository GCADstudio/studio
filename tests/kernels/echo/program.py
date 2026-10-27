"""A process kernel for the tests: the first word of the source picks the answer.

graph   a 10 x 20 x 30 box as a geometry graph
mesh    the same box as an ASCII STL file
log     a log notification, then the graph
error   an error diagnostic on line 2
hang    never answers
crash   exits with an error message on stderr
"""

import json
import sys
import time
from pathlib import Path

GRAPH = {
    "format": "gcad-graph",
    "version": 1,
    "units": "mm",
    "root": "b",
    "nodes": {"b": {"op": "box", "dim": 3, "args": {"size": [10, 20, 30], "center": False}}},
}

CORNERS = [(x, y, z) for x in (0, 10) for y in (0, 20) for z in (0, 30)]
QUADS = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]


def stl() -> str:
    lines = ["solid box"]
    for a, b, c, d in QUADS:
        for tri in ((a, b, c), (a, c, d)):
            lines.append("facet normal 0 0 0\nouter loop")
            lines += [f"vertex {x} {y} {z}" for x, y, z in (CORNERS[i] for i in tri)]
            lines.append("endloop\nendfacet")
    return "\n".join(lines + ["endsolid box"]) + "\n"


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def evaluate(params: dict) -> dict:
    words = params["source"].split()
    word = words[0] if words else ""
    if word == "hang":
        while True:
            time.sleep(1)
    if word == "crash":
        print("echo: crashed on purpose", file=sys.stderr)
        sys.exit(3)
    if word == "error":
        return {"diagnostics": [{"severity": "error", "message": "bad thing", "line": 2, "col": 3}]}
    if word == "mesh":
        Path(params["workdir"], "box.stl").write_text(stl())
        return {"diagnostics": [], "mesh_path": "box.stl"}
    if word == "log":
        send({"jsonrpc": "2.0", "method": "log", "params": {"message": "hello from echo"}})
    if word == "outside":
        return {"diagnostics": [], "mesh_path": str(Path(__file__))}
    return {"diagnostics": [], "graph": GRAPH}


def main() -> None:
    for line in sys.stdin:
        request = json.loads(line)
        method, rid = request.get("method"), request.get("id")
        if method == "initialize":
            result: object = {"name": "echo", "version": "1.0.0"}
        elif method == "evaluate":
            result = evaluate(request["params"])
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": rid, "result": None})
            return
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
