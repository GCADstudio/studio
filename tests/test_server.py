"""`gcad serve` (SPEC FR-U1, §7.1): security, evaluation payloads, files and exports."""

import base64
import http.client
import io
import json
import struct
import time
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from gcad.core.evaluator import Limits
from gcad.server import StudioServer

BRACKET = Path(__file__).resolve().parents[1] / "src/gcad/kernels/minscad/examples/bracket.mscad"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[StudioServer]:
    s = StudioServer(tmp_path)
    s.start()
    yield s
    s.stop()


def request(
    s: StudioServer,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=60)
    h = {"Host": f"127.0.0.1:{s.port}", **(headers or {})}
    conn.request(method, path, body=body, headers=h)
    response = conn.getresponse()
    return response.status, response.read()


def rpc(s: StudioServer, method: str, **params: object) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    status, data = request(
        s, "POST", "/rpc", body, {"X-GCAD-Token": s.token, "Content-Type": "application/json"}
    )
    assert status == 200
    return json.loads(data)


def ok(s: StudioServer, method: str, **params: object):  # type: ignore[no-untyped-def]
    reply = rpc(s, method, **params)
    assert "error" not in reply, reply
    return reply["result"]


# ---------- security ----------


def test_binds_to_loopback_with_a_token(server: StudioServer) -> None:
    assert server.httpd.server_address[0] == "127.0.0.1"
    assert len(server.token) >= 24 and server.url.startswith("http://127.0.0.1:")
    assert "#token=" in server.url  # a fragment: the browser never sends it in requests


def test_rpc_needs_the_token(server: StudioServer) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "hello"}).encode()
    assert request(server, "POST", "/rpc", body)[0] == 401
    assert request(server, "POST", "/rpc", body, {"X-GCAD-Token": "wrong"})[0] == 401


def test_foreign_host_header_is_refused(server: StudioServer) -> None:
    """A DNS-rebinding page reaches 127.0.0.1 under its own host name."""
    status, _ = request(server, "GET", "/", headers={"Host": "attacker.example:80"})
    assert status == 403
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "hello"}).encode()
    status, _ = request(
        server, "POST", "/rpc", body, {"Host": "attacker.example", "X-GCAD-Token": server.token}
    )
    assert status == 403


def test_static_files_stay_inside_the_web_folder(server: StudioServer) -> None:
    status, page = request(server, "GET", "/")
    assert status == 200 and b"GCAD Studio" in page
    assert request(server, "GET", "/vendor/three.module.min.js")[0] == 200
    assert request(server, "GET", "/../server.py")[0] == 404
    assert request(server, "GET", "/%2e%2e/server.py")[0] == 404


def test_no_external_resources_in_the_page(server: StudioServer) -> None:
    """FR-U1: the studio works offline; nothing is fetched from a CDN."""
    _, page = request(server, "GET", "/")
    assert b"http://" not in page.replace(b"http://www.w3.org", b"") and b"https://" not in page


# ---------- evaluation ----------


def test_evaluate_returns_a_mesh_with_source_spans(server: StudioServer) -> None:
    result = ok(
        server,
        "document/evaluate",
        kernel="minscad",
        source=BRACKET.read_text(encoding="utf-8"),
        file="bracket.mscad",
    )
    assert result["ok"] and result["dim"] == 3
    positions = np.frombuffer(base64.b64decode(result["positions"]), "<f4").reshape(-1, 3)
    indices = np.frombuffer(base64.b64decode(result["indices"]), "<u4").reshape(-1, 3)
    groups = np.frombuffer(base64.b64decode(result["triGroup"]), "<u4")
    assert len(indices) == len(groups) == result["triangles"]
    assert indices.max() < len(positions)
    assert np.allclose(result["bounds"], [[0, 0, 0], [40, 30, 25]])
    assert result["volume"] == pytest.approx(9428.03, rel=1e-4)
    lines = sorted({span["line"] for g in result["groups"] for span in g["spans"]})
    assert 14 in lines  # the cylinder call inside module holes()
    assert result["groups"][0]["color"] == [0.25, 0.45, 0.85, 1.0]


def test_evaluate_reports_errors_in_the_file(server: StudioServer) -> None:
    result = ok(
        server,
        "document/evaluate",
        kernel="minscad",
        source="cube(1);\ncube(wdth);",
        file="t.mscad",
    )
    assert not result["ok"]
    (d,) = result["diagnostics"]
    assert d["severity"] == "error" and d["line"] == 2 and d["inFile"]
    assert d["from"] == len("cube(1);\ncube(")


def test_evaluate_2d_returns_outlines(server: StudioServer) -> None:
    result = ok(
        server, "document/evaluate", kernel="csg", source="(rect [8 5])", file="t.gcsg", gcore=True
    )
    assert result["ok"] and result["dim"] == 2
    assert result["area"] == pytest.approx(40)
    assert len(result["contours"]) == 1
    assert result["gcore"] == "(rect [8 5])"


# ---------- runaway sources (AC-8) ----------

# Exponential recursion: shallow, so neither the depth nor the iteration limit stops it.
RUNAWAY = "(define (f n) (if (> n 0) (+ (f (- n 1)) (f (- n 1))) 0)) (f 60)"


def test_unbounded_recursion_returns_a_diagnostic(server: StudioServer) -> None:
    source = "(define (f x) (f x)) (f 1)"
    result = ok(server, "document/evaluate", kernel="csg", source=source, file="t.gcsg")
    assert not result["ok"] and "call depth exceeded" in result["diagnostics"][0]["message"]
    assert ok(server, "document/evaluate", kernel="csg", source="(box 1)")["ok"]


def test_runaway_source_hits_the_time_limit(tmp_path: Path) -> None:
    s = StudioServer(tmp_path, limits=Limits(max_seconds=1))
    s.start()
    try:
        started = time.monotonic()
        result = ok(s, "document/evaluate", kernel="csg", source=RUNAWAY, file="t.gcsg")
        assert time.monotonic() - started < 5
        assert not result["ok"] and "time limit" in result["diagnostics"][0]["message"]
    finally:
        s.stop()


def test_studio_stays_responsive_during_a_runaway_evaluation(tmp_path: Path) -> None:
    """While one evaluation runs away, the server answers, and the next edit replaces it at
    once rather than waiting out the time limit."""
    s = StudioServer(tmp_path, limits=Limits(max_seconds=30))
    s.start()
    try:
        with ThreadPoolExecutor(1) as pool:
            runaway = pool.submit(
                ok, s, "document/evaluate", kernel="csg", source=RUNAWAY, file="t.gcsg"
            )
            time.sleep(0.5)  # let it start
            started = time.monotonic()
            assert ok(s, "hello")["version"]
            assert request(s, "GET", "/")[0] == 200
            assert ok(s, "document/evaluate", kernel="csg", source="(box 1)")["ok"]
            assert time.monotonic() - started < 5
            assert runaway.result(timeout=5)["superseded"]
    finally:
        s.stop()


def test_kernel_list_describes_highlighting(server: StudioServer) -> None:
    kernels = {k["id"]: k for k in ok(server, "kernel/list")}
    assert {"minscad", "csg", "parts"} <= set(kernels)
    minscad = kernels["minscad"]
    assert "module" in minscad["highlight"]["keywords"]
    assert "cube" in minscad["builtins"] and "translate" in minscad["builtins"]
    assert minscad["highlight"]["patterns"]["number"]


# ---------- files ----------


def test_files_save_open_list(server: StudioServer, tmp_path: Path) -> None:
    saved = ok(server, "file/save", path="part.mscad", text="cube(2);\n")
    assert saved["path"] == "part.mscad"
    assert (tmp_path / "part.mscad").read_text() == "cube(2);\n"
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.mscad").write_text("cube(1);")
    listing = ok(server, "file/list")
    assert [f["path"] for f in listing["files"]] == ["part.mscad"]
    assert any(e["name"] == "bracket.mscad" for e in listing["examples"])
    opened = ok(server, "file/open", path="part.mscad")
    assert (
        opened["text"] == "cube(2);\n" and opened["kernel"] == "minscad" and not opened["readOnly"]
    )
    example = ok(server, "file/open", path="example:minscad/examples/bracket.mscad")
    assert example["readOnly"]


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("../escape.mscad", "inside the studio folder"),
        ("example:minscad/examples/bracket.mscad", "read-only"),
        ("missing/dir/part.mscad", "does not exist"),
    ],
)
def test_file_save_is_confined(server: StudioServer, path: str, message: str) -> None:
    reply = rpc(server, "file/save", path=path, text="x")
    assert message in reply["error"]["message"]


def test_example_paths_cannot_escape(server: StudioServer) -> None:
    reply = rpc(server, "file/open", path="example:../server.py")
    assert "error" in reply


# ---------- export ----------


def test_export_stl_returns_the_file(server: StudioServer) -> None:
    result = ok(
        server,
        "export/run",
        kernel="minscad",
        source="cube([4, 3, 2]);",
        file="box.mscad",
        format="stl",
    )
    data = base64.b64decode(result["data"])
    assert result["name"] == "box.stl" and result["bytes"] == len(data)
    assert struct.unpack("<I", data[80:84])[0] == 12


def test_export_obj_comes_zipped_with_its_mtl(server: StudioServer) -> None:
    result = ok(
        server, "export/run", kernel="minscad", source="cube(2);", file="box.mscad", format="obj"
    )
    names = zipfile.ZipFile(io.BytesIO(base64.b64decode(result["data"]))).namelist()
    assert result["name"].endswith(".zip") and sorted(names) == ["box.mtl", "box.obj"]


def test_export_mismatch_is_explained(server: StudioServer) -> None:
    reply = rpc(
        server, "export/run", kernel="minscad", source="cube(2);", file="box.mscad", format="dxf"
    )
    assert "2D model" in reply["error"]["message"]


def test_export_formats_list_every_format(server: StudioServer) -> None:
    formats = {f["id"]: f for f in ok(server, "export/formats")}
    assert {"step", "stl", "dxf", "cgm"} <= set(formats)
    assert formats["dxf"]["needs"] == "2d"
