from __future__ import annotations

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


@pytest.fixture
def examples() -> Path:
    return EXAMPLES
