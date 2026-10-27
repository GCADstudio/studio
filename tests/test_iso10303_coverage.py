"""The ISO 10303 coverage matrix (SPEC FR-I8, AC-21): every entity in scope has a status, and
every supported one is in a fixture that the kernel evaluates."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from gcad.iso10303 import schemas
from gcad.kernels import BUNDLED, load_kernel
from gcad.pipeline import evaluate_source

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/design/iso10303-coverage.tsv"
STATUSES = {"supported", "delegated", "diagnostic", "out-of-scope"}
FIXTURES = sorted((BUNDLED / "iso10303/examples").glob("*.stp")) + sorted(
    (ROOT / "tests/iso10303").glob("*.stp")
)
AP242 = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"


def rows() -> list[list[str]]:
    lines = [ln for ln in MATRIX.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert lines[0].split("\t") == ["entity", "part", "in_ap242", "status", "reason"]
    return [ln.split("\t") for ln in lines[1:]]


def test_every_row_has_a_status_and_a_reason() -> None:
    table = rows()
    assert len(table) > 2400
    for name, _part, in_ap, status, reason in table:
        assert status in STATUSES, name
        assert reason.strip(), name
        assert in_ap in ("yes", "no"), name
    names = [r[0] for r in table]
    assert names == sorted(names) and len(set(names)) == len(names)
    header = MATRIX.read_text(encoding="utf-8").splitlines()[1]
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
    assert digest in header, "the matrix's entity list changed without regenerating its header"


@pytest.mark.skipif(
    not (schemas.schema_dir() / f"{AP242}.json").is_file(),
    reason="the AP242 long form is not in the schema cache (gcad iso schema fetch AP242)",
)
def test_no_ap242_entity_without_a_status() -> None:
    from gcad.iso10303._nxstyle import express

    ap = express.Dictionary.load(str(schemas.schema_dir() / f"{AP242}.json"))
    listed = {r[0] for r in rows()}
    assert set(ap.entities) - listed == set()


@pytest.mark.skipif(
    not schemas.has_resources(),
    reason="the resource parts are not in the schema cache "
    "(gcad iso schema build, from your copies)",
)
def test_no_resource_entity_without_a_status() -> None:
    listed = {r[0] for r in rows()}
    assert set(schemas.resources().entities) - listed == set()


def keywords(text: str) -> set[str]:
    """Entity keywords used by instances: simple records and the partial records of complex ones."""
    body = text[text.index("DATA;") :]
    body = re.sub(r"'(?:[^']|'')*'|/\*.*?\*/", "''", body, flags=re.S)
    return {k.lower() for k in re.findall(r"(?<![A-Z0-9_.!])([A-Z][A-Z0-9_]*)\s*\(", body)}


def test_every_supported_entity_is_in_a_fixture_the_kernel_evaluates() -> None:
    kernel = load_kernel(BUNDLED / "iso10303")
    seen: set[str] = set()
    for path in FIXTURES:
        text = path.read_text(encoding="latin-1")
        if path.name.startswith("bad_"):
            continue  # negative controls
        evaluated = evaluate_source(text, str(path), kernel)
        assert evaluated.result.ok, (path.name, [str(d) for d in evaluated.result.diagnostics])
        seen |= keywords(text)
    supported = {r[0] for r in rows() if r[3] == "supported"}
    assert supported - seen == set(), "supported entities with no fixture"
