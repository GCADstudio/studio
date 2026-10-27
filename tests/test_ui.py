"""The studio in a real, headless browser (SPEC NFR-9): it loads, renders the model with the
software rasterizer (no GPU), and its main interactions work.

Runs where Playwright is installed and a Chrome or Edge is present; screenshots go to
GCAD_UI_ARTIFACTS when that is set.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gcad.server import StudioServer

sync_api = pytest.importorskip("playwright.sync_api")

# SwiftShader: Chrome's CPU WebGL, the path a machine without a usable GPU takes.
ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


@pytest.fixture
def page(tmp_path: Path) -> Iterator[object]:
    server = StudioServer(tmp_path)
    server.start()
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
            server.stop()
            if os.environ.get("GCAD_REQUIRE_UI"):
                pytest.fail("the UI job needs Chrome, Edge or Playwright's Chromium")
            pytest.skip("no Chrome, Edge or Playwright Chromium found")
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        pg = context.new_page()
        pg.on("pageerror", lambda e: errors.append(f"page error: {e}"))
        pg.on(
            "console",
            lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None,
        )
        pg.goto(server.url)
        pg.wait_for_selector('#status[data-state="ok"]', timeout=60_000)
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()
    server.stop()


def shot(page: object, name: str) -> None:
    folder = os.environ.get("GCAD_UI_ARTIFACTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        # Finish the dialogs' fade-in first, or the capture catches them half transparent.
        page.screenshot(path=str(Path(folder) / f"{name}.png"), animations="disabled")  # type: ignore[attr-defined]


def canvas_colors(page: object) -> int:
    """Distinct colours in the middle of the viewer: a blank canvas has one or two."""
    return page.evaluate(  # type: ignore[attr-defined]
        """() => {
            const src = document.querySelector('#viewer canvas');
            const c = document.createElement('canvas');
            c.width = 200; c.height = 200;
            const g = c.getContext('2d');
            g.drawImage(src, src.width / 2 - 100, src.height / 2 - 100, 200, 200, 0, 0, 200, 200);
            const d = g.getImageData(0, 0, 200, 200).data;
            const seen = new Set();
            for (let i = 0; i < d.length; i += 4) {
                seen.add((d[i] >> 3) << 10 | (d[i + 1] >> 3) << 5 | (d[i + 2] >> 3));
            }
            return seen.size;
        }"""
    )


def test_studio_loads_and_renders_without_a_gpu(page: object) -> None:
    assert page.text_content("#file-name") == "bracket.mscad"  # type: ignore[attr-defined]
    assert "40 × 30 × 25" in page.text_content("#model-card")  # type: ignore[attr-defined]
    assert canvas_colors(page) > 40, "the viewer drew nothing"
    shot(page, "studio-dark")
    assert page.errors == []  # type: ignore[attr-defined]


def test_click_on_the_model_selects_its_source(page: object) -> None:
    # A point on the top face of the bracket's base plate, projected to the screen.
    x, y = page.evaluate(  # type: ignore[attr-defined]
        """() => {
            const v = gcad.viewer;
            const p = v.bounds.center.clone().set(30, 25, 5).project(v.camera);
            const r = v.renderer.domElement.getBoundingClientRect();
            return [r.left + (p.x + 1) / 2 * r.width, r.top + (1 - p.y) / 2 * r.height];
        }"""
    )
    page.mouse.click(x, y)  # type: ignore[attr-defined]
    page.wait_for_selector("#pick-card:not([hidden])")  # type: ignore[attr-defined]
    selected = page.evaluate(  # type: ignore[attr-defined]
        "() => { const s = gcad.editor.view.state, r = s.selection.main;"
        " return s.sliceDoc(r.from, r.to); }"
    )
    assert selected.startswith("cube(")


def test_palette_export_dialog_and_light_theme(page: object) -> None:
    page.keyboard.press("Control+o")  # type: ignore[attr-defined]
    page.wait_for_selector("#palette:not([hidden]) .item")  # type: ignore[attr-defined]
    assert page.locator("#palette-list .item").count() >= 10  # type: ignore[attr-defined]
    shot(page, "palette")
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.click("#export-button")  # type: ignore[attr-defined]
    page.wait_for_selector("#export-dialog:not([hidden]) .format")  # type: ignore[attr-defined]
    assert page.is_disabled("button.format:has-text('.dxf')")  # type: ignore[attr-defined]
    assert page.is_enabled("button.format:has-text('.stl')")  # type: ignore[attr-defined]
    shot(page, "export")
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.click("#theme-button")  # type: ignore[attr-defined]
    assert page.get_attribute("html", "data-theme") == "light"  # type: ignore[attr-defined]
    assert canvas_colors(page) > 40
    shot(page, "studio-light")
    assert page.errors == []  # type: ignore[attr-defined]
