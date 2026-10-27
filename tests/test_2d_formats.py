"""SPEC AC-4: 2D models and sections in DXF, SVG, PDF and EPS read back at 1:1, every vertex
within 1e-6 mm of the model; CGM structurally, and through LibreOffice where installed.

Readers: ezdxf (DXF), svgelements (SVG), pdfminer.six (PDF), Ghostscript + pdfminer (EPS),
LibreOffice (CGM, DXF). None of them is GCAD's writer code.
"""

import math
import os
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from gcad.backends import get_backend
from gcad.exporters.drawing import MARGIN_MM, MM_TO_PT
from gcad.kernels import BUNDLED
from gcad.pipeline import evaluate_file, export_file

EXAMPLES = BUNDLED / "minscad" / "examples"
SOURCES = {"plate": EXAMPLES / "plate2d.mscad", "section": EXAMPLES / "section.mscad"}


def model_contours(src: Path) -> list[np.ndarray]:
    graph = evaluate_file(src).result.graph
    assert graph is not None and graph.root.dim == 2
    return get_backend("manifold").realize(graph).contours()


def assert_same_outlines(read: list[np.ndarray], expected: list[np.ndarray], tol: float) -> None:
    """Every expected outline appears in what was read, vertex for vertex."""
    assert len(read) == len(expected)
    remaining = list(read)
    for contour in expected:
        for i, candidate in enumerate(remaining):
            if candidate.shape == contour.shape and np.abs(candidate - contour).max() <= tol:
                del remaining[i]
                break
        else:
            raise AssertionError(f"outline with {len(contour)} vertices not found")


def export(src: Path, out: Path, **options: object) -> None:
    _, report = export_file(src, out, options=dict(options))
    assert report is not None


@pytest.fixture(params=sorted(SOURCES))
def source(request: pytest.FixtureRequest) -> Path:
    return SOURCES[request.param]


def test_section_of_the_test_part() -> None:
    contours = model_contours(SOURCES["section"])
    assert len(contours) == 2
    outer, hole = sorted(contours, key=lambda c: -np.ptp(c[:, 0]))
    assert np.allclose(sorted(map(tuple, outer)), [(0, 0), (0, 30), (40, 0), (40, 30)])
    assert np.allclose(np.linalg.norm(hole - [20, 15], axis=1), 4)


@pytest.mark.parametrize("version", ["R2000", "R12"])
def test_dxf(source: Path, tmp_path: Path, version: str) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    out = tmp_path / "drawing.dxf"
    export(source, out, dxf_version=version)
    doc = ezdxf.readfile(out)
    auditor = doc.audit()
    assert not auditor.has_errors
    if version != "R12":  # R12 has no units header variable
        assert doc.header["$INSUNITS"] == 4  # millimetres
    kind = "POLYLINE" if version == "R12" else "LWPOLYLINE"
    entities = doc.modelspace().query(kind)
    assert all(e.is_closed for e in entities)
    if kind == "POLYLINE":
        read = [np.array([tuple(p)[:2] for p in e.points()], dtype=float) for e in entities]
    else:
        read = [np.array(e.get_points("xy"), dtype=float) for e in entities]
    assert_same_outlines(read, model_contours(source), 1e-6)


def test_svg(source: Path, tmp_path: Path) -> None:
    svgelements = pytest.importorskip("svgelements")
    out = tmp_path / "drawing.svg"
    export(source, out)
    svg = svgelements.SVG.parse(str(out), ppi=25.4)  # 1 px = 1 mm
    expected = model_contours(source)
    points = np.vstack(expected)
    w, h = points.max(axis=0) - points.min(axis=0) + 2 * MARGIN_MM
    assert svg.width == pytest.approx(w) and svg.height == pytest.approx(h)
    path = next(e for e in svg.elements() if isinstance(e, svgelements.Path))
    vb = svg.viewbox
    model = []
    for sub in list(path.as_subpaths()):
        segments = [s for s in list(sub) if isinstance(s, (svgelements.Move, svgelements.Line))]
        px = np.array([(s.end.x, s.end.y) for s in segments], dtype=float)
        # ppi 25.4 makes one pixel one millimetre; undo the viewBox origin and the y flip.
        model.append(np.column_stack([px[:, 0] + vb.x, -(px[:, 1] + vb.y)]))
    # Through the full viewport chain svgelements converts mm via 96 dpi; good to ~1e-5.
    assert_same_outlines(model, expected, 1e-4)

    # Exact check: the raw path data holds the model coordinates; the page is 1:1 because the
    # viewBox size equals the width and height in millimetres.
    import xml.etree.ElementTree as ET

    root = ET.parse(out).getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    vx, vy, vw, vh = map(float, root.attrib["viewBox"].split())
    assert root.attrib["width"] == f"{vw:g}mm" or float(root.attrib["width"][:-2]) == vw
    assert float(root.attrib["height"][:-2]) == vh and root.attrib["height"].endswith("mm")
    group = root.find("svg:g", ns)
    assert group is not None and group.attrib["transform"] == "scale(1,-1)"
    d = group.find("svg:path", ns).attrib["d"]  # type: ignore[union-attr]
    raw = []
    for sub in list(svgelements.Path(d).as_subpaths()):
        segments = [s for s in list(sub) if isinstance(s, (svgelements.Move, svgelements.Line))]
        raw.append(np.array([(s.end.x, s.end.y) for s in segments], dtype=float))
    assert_same_outlines(raw, expected, 1e-6)


def _pdf_outlines(path: Path) -> tuple[list[np.ndarray], tuple[float, float, float, float]]:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTCurve

    page = next(iter(extract_pages(str(path))))
    curves = [o for o in page if isinstance(o, LTCurve)]
    read = []
    for curve in curves:
        pts = np.array(curve.pts, dtype=float) / MM_TO_PT
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]  # closed paths may repeat the first point
        read.append(pts)
    return read, page.bbox


def test_pdf(source: Path, tmp_path: Path) -> None:
    pytest.importorskip("pdfminer")
    out = tmp_path / "drawing.pdf"
    export(source, out)
    read, bbox = _pdf_outlines(out)
    expected = model_contours(source)
    points = np.vstack(expected)
    lo, hi = points.min(axis=0) - MARGIN_MM, points.max(axis=0) + MARGIN_MM
    # pdfminer reports the page from (0, 0) and coordinates relative to the MediaBox corner.
    assert np.allclose(np.array(bbox) / MM_TO_PT, [0, 0, *(hi - lo)], atol=1e-9)
    assert_same_outlines([r + lo for r in read], expected, 1e-6)


def ghostscript() -> str | None:
    for name in ("gswin64c", "gswin32c", "gs"):
        if found := shutil.which(name):
            return found
    candidates = sorted(
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"), "gs").glob("gs*/bin/gswin64c.exe")
    )
    return str(candidates[-1]) if candidates else None


def test_eps(source: Path, tmp_path: Path) -> None:
    pytest.importorskip("pdfminer")
    gs = ghostscript()
    if gs is None:
        pytest.skip("Ghostscript is not installed")
    out = tmp_path / "drawing.eps"
    export(source, out)
    text = out.read_text()
    assert text.startswith("%!PS-Adobe-3.0 EPSF-3.0")
    expected = model_contours(source)
    points = np.vstack(expected)
    lo, hi = (
        (points.min(axis=0) - MARGIN_MM) * MM_TO_PT,
        (points.max(axis=0) + MARGIN_MM) * MM_TO_PT,
    )
    box = [int(v) for v in text.split("%%BoundingBox:")[1].split("\n")[0].split()]
    assert box == [math.floor(lo[0]), math.floor(lo[1]), math.ceil(hi[0]), math.ceil(hi[1])]
    # Ghostscript interprets the PostScript; its PDF output is read back by pdfminer.
    pdf = tmp_path / "via_gs.pdf"
    subprocess.run(
        [
            gs,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            f"-sOutputFile={pdf}",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    read, _ = _pdf_outlines(pdf)
    # pdfwrite stores coordinates to about 0.001 pt, so the round trip is good to ~1e-3 mm.
    assert_same_outlines(read, expected, 2e-3)


def _cgm_elements(data: bytes) -> list[tuple[int, int, bytes]]:
    """A minimal reader for the binary encoding, used to check the structure."""
    out, i = [], 0
    while i < len(data):
        (word,) = struct.unpack_from(">H", data, i)
        cls, eid, length = word >> 12, (word >> 5) & 0x7F, word & 0x1F
        i += 2
        params = b""
        if length == 31:
            more = True
            while more:
                (lw,) = struct.unpack_from(">H", data, i)
                more, n = bool(lw & 0x8000), lw & 0x7FFF
                params += data[i + 2 : i + 2 + n]
                i += 2 + n
        else:
            params = data[i : i + length]
            i += length
        i += i % 2
        out.append((cls, eid, params))
    return out


def test_cgm_structure_and_coordinates(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "drawing.cgm"
    export(source, out)
    elements = _cgm_elements(out.read_bytes())
    ids = [(c, e) for c, e, _ in elements]
    assert ids[0] == (0, 1) and ids[-1] == (0, 2)  # BEGIN / END METAFILE
    assert (1, 1) in ids and (2, 6) in ids and (0, 4) in ids
    polygons = [p for c, e, p in elements if (c, e) == (4, 7)]
    read = []
    for params in polygons:
        values = [whole + frac / 65536 for whole, frac in struct.iter_unpack(">hH", params)]
        read.append(np.array(values, dtype=float).reshape(-1, 2))
    assert_same_outlines(read, model_contours(source), 1 / 65536)


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


@pytest.mark.parametrize("ext", ["cgm", "dxf"])
def test_libreoffice_reads(tmp_path: Path, ext: str) -> None:
    """LibreOffice imports CGM and DXF with its own filters. Converted to PDF, what it strokes
    must be the plate: its outline sets the extent of everything drawn, 80 x 50 mm.

    PDF rather than SVG: LibreOffice's SVG export adds invisible bounding-box and clip
    rectangles for every shape and for the page, so "the largest shape" there is LibreOffice's
    page structure, not our geometry. A PDF holds only what is painted."""
    exe = soffice()
    if exe is None:
        if os.environ.get("GCAD_REQUIRE_LIBREOFFICE"):
            pytest.fail("LibreOffice is required in this CI job")
        pytest.skip("LibreOffice is not installed")
    pytest.importorskip("pdfminer")
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTCurve

    out = tmp_path / f"plate.{ext}"
    export(SOURCES["plate"], out)
    converted = subprocess.run(
        [exe, "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(out)],
        check=True,
        capture_output=True,
        timeout=180,
    )
    pdf = tmp_path / "plate.pdf"
    keep = os.environ.get("GCAD_READER_ARTIFACTS")
    if keep:  # keep what LibreOffice made of our file, for inspection
        folder = Path(keep) / ext
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, folder / out.name)
        if pdf.exists():
            shutil.copy(pdf, folder / f"plate-from-{ext}.pdf")
        (folder / "soffice.log").write_bytes(converted.stdout + converted.stderr)
    assert pdf.exists(), converted.stdout + converted.stderr
    page = next(iter(extract_pages(str(pdf))))
    # LTRect and LTLine are LTCurve subclasses: everything painted as a path.
    stroked = [o for o in page if isinstance(o, LTCurve) and o.stroke]
    assert stroked, "LibreOffice drew nothing"
    box = np.array([o.bbox for o in stroked], dtype=float)
    width = box[:, 2].max() - box[:, 0].min()
    height = box[:, 3].max() - box[:, 1].min()
    # 1% covers the hairline's width at whatever scale LibreOffice places the drawing.
    assert width / height == pytest.approx(80 / 50, rel=1e-2)


def test_3d_model_needs_a_section(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="section"):
        export_file(EXAMPLES / "test_part.mscad", tmp_path / "part.dxf")


def test_2d_exports_are_reproducible(tmp_path: Path) -> None:
    runs = []
    for i in range(2):
        files = {}
        for ext in ("dxf", "svg", "pdf", "eps", "cgm"):
            out = tmp_path / f"run{i}" / f"plate.{ext}"
            export(SOURCES["plate"], out, epoch=1767225600)
            files[ext] = out.read_bytes()
        runs.append(files)
    for ext in runs[0]:
        assert runs[0][ext] == runs[1][ext], ext
