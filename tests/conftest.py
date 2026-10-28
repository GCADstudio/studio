from __future__ import annotations

import os
from pathlib import Path

import pytest

from gcad.core.evaluator import EvalResult, Limits, evaluate
from gcad.core.sexpr import read

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "gcad" / "kernels" / "csg" / "examples"


def run(source: str, limits: Limits | None = None) -> EvalResult:
    return evaluate(read(source, "test.gcsg"), limits, {"title": "test", "source": "test.gcsg"})


def ok(source: str) -> EvalResult:
    result = run(source)
    assert result.ok, [str(d) for d in result.diagnostics]
    return result


def error_of(source: str, limits: Limits | None = None) -> str:
    result = run(source, limits)
    assert not result.ok
    return next(str(d) for d in result.diagnostics if d.severity.value == "error")


@pytest.fixture(autouse=True)
def isolated_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Tests never read or write the user's own GCAD Studio folder (kernels, trust)."""
    folder = tmp_path_factory.mktemp("config")
    monkeypatch.setenv("GCAD_CONFIG_DIR", str(folder))
    return folder


@pytest.fixture
def examples() -> Path:
    return EXAMPLES


def reader_unusable(reason: str) -> None:
    """An independent reader is installed but cannot run here (for example it lacks a system
    library). Skip on a developer machine; fail in CI, where GCAD_REQUIRE_READERS is set,
    so coverage cannot silently disappear."""
    if os.environ.get("GCAD_REQUIRE_READERS"):
        pytest.fail(reason)
    pytest.skip(reason)
