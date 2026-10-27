"""The web studio (SPEC §4.1, AC-9 to AC-12): the built static site in headless Chrome, with
the Python core in Pyodide and nothing on the server side but files.

The site is built with tools/build_site.py (or taken from GCAD_SITE) and served by a plain
static file server that cannot run code. Exports are read back with the readers the native
tests use and compared with the native studio's output for the same source.

Runs where Playwright and a Chrome, Edge or Playwright Chromium are present and the pinned
assets can be fetched (or are cached); GCAD_REQUIRE_WEB turns a skip into a failure.
Screenshots go to GCAD_UI_ARTIFACTS when that is set. The tests share one page, in order.
"""

import base64
import functools
import os
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from test_2d_formats import _cgm_elements, _pdf_outlines, assert_same_outlines, model_contours
from test_mesh_formats import meshlab_load, volume
from test_ui import ARGS, canvas_colors, shot

from gcad.kernels import BUNDLED
from gcad.server import Studio

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[1]
BRACKET = BUNDLED / "minscad" / "examples" / "bracket.mscad"
PLATE = BUNDLED / "minscad" / "examples" / "plate2d.mscad"
RUNAWAY = "(define (f n) (if (> n 0) (+ (f (- n 1)) (f (- n 1))) 0)) (f 60)"
READY = '#status[data-state="ok"]'


def unavailable(reason: str) -> None:
    if os.environ.get("GCAD_REQUIRE_WEB"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GCAD_SITE"):
        built = Path(os.environ["GCAD_SITE"])
        if not (built / "site.json").is_file():
            pytest.fail(f"GCAD_SITE={built} is not a built site")
        return built
    out = tmp_path_factory.mktemp("web") / "site"
    build = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_site.py"), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        unavailable(f"the site did not build (offline?): {build.stderr or build.stdout}")
    return out


class StaticFiles(SimpleHTTPRequestHandler):
    """Files only: no handler for POST, so nothing on this side can evaluate anything."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".wasm": "application/wasm",
    }
    requests: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        self.requests.append(self.requestline)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # A reload drops the reserve worker's downloads mid-transfer; that is not an error.
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def server(site: Path) -> Iterator[str]:
    StaticFiles.requests = []
    httpd = QuietServer(("127.0.0.1", 0), functools.partial(StaticFiles, directory=site))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def page(server: str) -> Iterator[object]:
    errors: list[str] = []
    with sync_api.sync_playwright() as p:
        browser = None
        for channel in ("chrome", "msedge", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True, args=ARGS)
                break
            except Exception:  # that browser is not installed here
                continue
        if browser is None:
            unavailable("no Chrome, Edge or Playwright Chromium found")
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, color_scheme="dark", accept_downloads=True
        )
        pg = context.new_page()
        pg.on("dialog", lambda d: d.accept())  # "Discard unsaved changes?" after the edits
        pg.on("pageerror", lambda e: errors.append(f"page error: {e}"))
        pg.on(
            "console",
            lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None,
        )
        started = time.monotonic()
        pg.goto(server)
        pg.wait_for_selector(READY, timeout=180_000)
        pg.boot_seconds = time.monotonic() - started  # type: ignore[attr-defined]
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()


@pytest.fixture(scope="module")
def native(tmp_path_factory: pytest.TempPathFactory) -> Studio:
    return Studio(tmp_path_factory.mktemp("native"))


def rpc(page: object, method: str, **params: object) -> dict:
    return page.evaluate("([m, p]) => gcad.rpc(m, p)", [method, params])  # type: ignore[attr-defined]


def open_in_palette(page: object, name: str) -> None:
    page.keyboard.press("Control+o")  # type: ignore[attr-defined]
    page.wait_for_selector("#palette:not([hidden]) .item")  # type: ignore[attr-defined]
    page.fill("#palette-input", name)  # type: ignore[attr-defined]
    page.keyboard.press("Enter")  # type: ignore[attr-defined]
    page.wait_for_function(  # type: ignore[attr-defined]
        "n => document.querySelector('#file-name').textContent === n"
        " && document.querySelector('#dirty').hidden",
        arg=name,
    )
    page.wait_for_selector(READY)  # type: ignore[attr-defined]


# ---------- AC-9: it loads and renders, with no server-side code ----------


def test_loads_and_renders_with_no_server_side_python(page: object, site: Path) -> None:
    print(f"studio ready {page.boot_seconds:.1f} s after navigation")  # type: ignore[attr-defined]
    assert page.text_content("#file-name") == "bracket.mscad"  # type: ignore[attr-defined]
    assert page.is_visible("#mode-chip")  # type: ignore[attr-defined]
    assert "40 × 30 × 25" in page.text_content("#model-card")  # type: ignore[attr-defined]
    assert canvas_colors(page) > 40, "the viewer drew nothing"
    hello = rpc(page, "hello")
    assert hello["mode"] == "web" and hello["root"] == "this browser"
    # Only GETs of files in the site reached the server: no POST, no RPC endpoint.
    assert StaticFiles.requests and all(r.startswith("GET ") for r in StaticFiles.requests)
    paths = {r.split()[1].split("?")[0] for r in StaticFiles.requests}
    missing = [p for p in paths if p != "/" and not (site / p.lstrip("/")).is_file()]
    assert missing == [], missing
    shot(page, "web-studio-dark")
    assert page.errors == []  # type: ignore[attr-defined]


# ---------- AC-12: the same geometry as the native back-end M ----------


def test_every_example_matches_the_native_mesh_backend(page: object, native: Studio) -> None:
    examples = rpc(page, "file/list")["examples"]
    assert len(examples) >= 10
    for example in examples:
        text = rpc(page, "file/open", path=example["path"])["text"]
        args = {"kernel": example["kernel"], "source": text, "file": example["name"]}
        web = rpc(page, "document/evaluate", **args)
        ref = native.rpc_document_evaluate(**args)  # type: ignore[arg-type]
        assert web["ok"] and ref["ok"], example["name"]
        assert web["hash"] == ref["hash"], example["name"]
        assert np.allclose(web["bounds"], ref["bounds"], rtol=1e-6, atol=1e-6), example["name"]
        assert web["area"] == pytest.approx(ref["area"], rel=1e-6), example["name"]
        if ref["dim"] == 3:
            assert web["triangles"] == ref["triangles"], example["name"]
            assert web["volume"] == pytest.approx(ref["volume"], rel=1e-6), example["name"]
            assert web["groups"] == ref["groups"], example["name"]  # colours and source spans
        else:
            assert len(web["contours"]) == len(ref["contours"]), example["name"]
            got = [np.array(c) for c in web["contours"]]
            assert_same_outlines(got, [np.array(c) for c in ref["contours"]], 1e-9)


# ---------- AC-10: exports ----------


def download(page: object, ext: str, folder: Path) -> Path:
    page.click("#export-button")  # type: ignore[attr-defined]
    page.wait_for_selector("#export-dialog:not([hidden]) .format")  # type: ignore[attr-defined]
    with page.expect_download() as info:  # type: ignore[attr-defined]
        page.click(f"button.format:has-text('.{ext}')")  # type: ignore[attr-defined]
    path = folder / info.value.suggested_filename
    info.value.save_as(path)
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    return path


def test_stl_and_dxf_download_and_read_back(page: object, tmp_path: Path) -> None:
    trimesh = pytest.importorskip("trimesh")
    ezdxf = pytest.importorskip("ezdxf")
    stl = download(page, "stl", tmp_path)
    assert stl.name == "bracket.stl"
    mesh = trimesh.load(stl, force="mesh")
    model = rpc(page, "document/evaluate", kernel="minscad", source=BRACKET.read_text())
    assert len(mesh.faces) == model["triangles"] and mesh.is_watertight
    assert mesh.volume == pytest.approx(model["volume"], rel=1e-5)
    assert np.allclose(mesh.bounds, [[0, 0, 0], [40, 30, 25]], atol=1e-5)

    open_in_palette(page, "plate2d.mscad")
    dxf = download(page, "dxf", tmp_path)
    assert dxf.name == "plate2d.dxf"
    doc = ezdxf.readfile(dxf)
    assert not doc.audit().has_errors
    assert doc.header["$INSUNITS"] == 4  # millimetres
    read = [np.array(e.get_points("xy"), dtype=float) for e in doc.modelspace().query("LWPOLYLINE")]
    assert_same_outlines(read, model_contours(PLATE), 1e-6)
    open_in_palette(page, "bracket.mscad")


def read_mesh(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Vertices and triangles, by the reader the native tests use for that format."""
    ext = path.suffix
    if ext in (".stl", ".glb", ".dae", ".zip"):
        trimesh = pytest.importorskip("trimesh")
        if ext == ".zip":  # OBJ with its MTL
            with zipfile.ZipFile(path) as zf:
                zf.extractall(path.parent / path.stem)
            path = next((path.parent / path.stem).glob("*.obj"))
        mesh = trimesh.load(path, force="mesh")
        return np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if ext == ".unv":
        pyuff = pytest.importorskip("pyuff")
        sets = {s["type"]: s for s in pyuff.UFF(str(path)).read_sets()}
        nodes = sets[2411]
        xyz = np.column_stack([nodes["x"], nodes["y"], nodes["z"]])
        faces = np.asarray([e["nodes_nums"] for e in sets[2412][91]], dtype=int) - 1
        return xyz, faces
    mesh = meshlab_load(path).current_mesh()  # X3D, VRML
    # MeshLab's Linux X3D importer can leave deleted vertices behind; drop them first.
    mesh.compact()
    return mesh.vertex_matrix(), mesh.face_matrix()


def test_every_format_exports_or_says_why(page: object, native: Studio, tmp_path: Path) -> None:
    formats = rpc(page, "export/formats")
    assert len(formats) == 14
    for fmt in formats:
        if fmt["id"] in ("step", "iges"):
            assert "no WebAssembly build" in fmt["reason"], fmt
            continue
        assert fmt["reason"] == "", fmt
        src = PLATE if fmt["needs"] == "2d" else BRACKET
        args = {"kernel": "minscad", "source": src.read_text(), "file": src.name}
        web = rpc(page, "export/run", format=fmt["id"], **args)
        ref = native.rpc_export_run(format=fmt["id"], **args)  # type: ignore[arg-type]
        assert web["name"] == ref["name"] and web["backend"] == "manifold"
        (tmp_path / "web").mkdir(exist_ok=True)
        (tmp_path / "native").mkdir(exist_ok=True)
        web_file = tmp_path / "web" / web["name"]
        ref_file = tmp_path / "native" / ref["name"]
        web_file.write_bytes(base64.b64decode(web["data"]))
        ref_file.write_bytes(base64.b64decode(ref["data"]))
        check_against_native(fmt["id"], web_file, ref_file)


def check_against_native(fmt: str, web: Path, ref: Path) -> None:
    if fmt in ("stl", "obj", "glb", "dae", "x3d", "vrml", "unv"):
        (wv, wf), (rv, rf) = read_mesh(web), read_mesh(ref)
        assert len(wf) == len(rf), fmt
        assert volume(wv, wf) == pytest.approx(volume(rv, rf), rel=1e-5), fmt
        return
    expected = model_contours(PLATE)
    if fmt == "dxf":
        ezdxf = pytest.importorskip("ezdxf")
        entities = ezdxf.readfile(web).modelspace().query("LWPOLYLINE")
        read = [np.array(e.get_points("xy"), dtype=float) for e in entities]
        assert_same_outlines(read, expected, 1e-6)
    elif fmt == "pdf":
        pytest.importorskip("pdfminer")
        (got, got_box), (want, want_box) = _pdf_outlines(web), _pdf_outlines(ref)
        assert got_box == want_box
        assert_same_outlines(got, want, 1e-9)
    elif fmt == "cgm":
        polygons = [p for c, e, p in _cgm_elements(web.read_bytes()) if (c, e) == (4, 7)]
        read = [np.frombuffer(p, dtype=">f8").reshape(-1, 2) for p in polygons]
        assert_same_outlines(read, expected, 1e-9)
    else:  # SVG, EPS: text; identical to the native file but for the creation date

        def body(path: Path) -> list[str]:
            lines = path.read_text(encoding="utf-8").splitlines()
            return [s for s in lines if "CreationDate" not in s and "<dc:date>" not in s]

        assert body(web) == body(ref), fmt


# ---------- AC-11: a runaway source ----------


def set_source(page: object, text: str) -> None:
    page.evaluate(  # type: ignore[attr-defined]
        "t => { const v = gcad.editor.view;"
        " v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: t } }); }",
        text,
    )


def test_runaway_keeps_the_page_responsive_and_is_replaced(page: object) -> None:
    page.select_option("#kernel", "csg")  # type: ignore[attr-defined]
    set_source(page, RUNAWAY)
    page.wait_for_selector('#status[data-state="busy"]')  # type: ignore[attr-defined]
    started = time.monotonic()
    time.sleep(1.0)  # well into the runaway
    # The page itself is not blocked: animation frames keep coming and the editor takes input.
    frames = page.evaluate(  # type: ignore[attr-defined]
        "() => new Promise(r => { let n = 0; const t0 = performance.now();"
        " (function f() { n++; if (performance.now() - t0 < 500) requestAnimationFrame(f);"
        " else r(n); })(); })"
    )
    assert frames >= 10, f"only {frames} frames in 0.5 s"
    page.click("#console-toggle")  # type: ignore[attr-defined]
    page.click("#console-toggle")  # type: ignore[attr-defined]
    assert page.get_attribute("#status", "data-state") == "busy"  # type: ignore[attr-defined]
    # The next edit replaces it, long before the 10 s time limit would have stopped it.
    set_source(page, "(box [12 7 5])")
    page.wait_for_function(  # type: ignore[attr-defined]
        "() => document.querySelector('#model-card').textContent.includes('12 × 7 × 5')",
        timeout=9_000,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 9, f"replaced only after {elapsed:.1f} s"
    print(f"runaway replaced {elapsed:.1f} s after it started")
    assert page.get_attribute("#status", "data-state") == "ok"  # type: ignore[attr-defined]
    open_in_palette(page, "bracket.mscad")


# ---------- the light theme, the dialogs, and files kept in the browser ----------


def test_light_theme_palette_and_export_dialog(page: object) -> None:
    assert "40 × 30 × 25" in page.text_content("#model-card")  # type: ignore[attr-defined]
    page.click("#export-button")  # type: ignore[attr-defined]
    page.wait_for_selector("#export-dialog:not([hidden]) .format")  # type: ignore[attr-defined]
    assert page.is_disabled("button.format:has-text('.step')")  # type: ignore[attr-defined]
    assert "WebAssembly" in page.text_content("#export-groups .group-note")  # type: ignore[attr-defined]
    shot(page, "web-export-dark")
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.click("#theme-button")  # type: ignore[attr-defined]
    assert page.get_attribute("html", "data-theme") == "light"  # type: ignore[attr-defined]
    assert canvas_colors(page) > 40
    shot(page, "web-studio-light")
    page.keyboard.press("Control+o")  # type: ignore[attr-defined]
    page.wait_for_selector("#palette:not([hidden]) .item")  # type: ignore[attr-defined]
    assert page.locator("#palette-list .item", has_text="Upload a file").count() == 1  # type: ignore[attr-defined]
    shot(page, "web-palette-light")
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.click("#export-button")  # type: ignore[attr-defined]
    page.wait_for_selector("#export-dialog:not([hidden]) .format")  # type: ignore[attr-defined]
    shot(page, "web-export-light")
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    assert page.errors == []  # type: ignore[attr-defined]


def test_uploaded_file_is_kept_in_the_browser(page: object, tmp_path: Path) -> None:
    mine = tmp_path / "mine.gcsg"
    mine.write_text("(box [6 5 9])\n", encoding="utf-8")
    page.keyboard.press("Control+o")  # type: ignore[attr-defined]
    page.wait_for_selector("#palette:not([hidden]) .item")  # type: ignore[attr-defined]
    page.fill("#palette-input", "upload")  # type: ignore[attr-defined]
    with page.expect_file_chooser() as chooser:  # type: ignore[attr-defined]
        page.keyboard.press("Enter")  # type: ignore[attr-defined]
    chooser.value.set_files(str(mine))
    page.wait_for_function(  # type: ignore[attr-defined]
        "() => document.querySelector('#file-name').textContent === 'mine.gcsg'"
    )
    page.wait_for_selector(READY)  # type: ignore[attr-defined]
    assert "6 × 5 × 9" in page.text_content("#model-card")  # type: ignore[attr-defined]
    # A new page, a new worker: the file is still there, in IndexedDB.
    page.reload()  # type: ignore[attr-defined]
    page.wait_for_selector(READY, timeout=180_000)  # type: ignore[attr-defined]
    listed = [f["path"] for f in rpc(page, "file/list")["files"]]
    assert listed == ["mine.gcsg"]
    assert rpc(page, "file/open", path="mine.gcsg")["text"] == mine.read_text()
    assert page.errors == []  # type: ignore[attr-defined]
