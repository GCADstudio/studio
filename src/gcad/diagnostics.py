"""Diagnostics shared by every stage: parse, rewrite, evaluation, back-end, export (FR-C7)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Span:
    """A region of a source file. Lines and columns are 1-based, offsets 0-based."""

    file: str
    line: int
    col: int
    start: int
    end: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"

    def to_json(self) -> dict[str, object]:
        return {
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    span: Span | None = None

    def __str__(self) -> str:
        where = f"{self.span}: " if self.span else ""
        return f"{where}{self.severity.value}: {self.message}"

    def to_json(self) -> dict[str, object]:
        return {
            "severity": self.severity.value,
            "message": self.message,
            "span": self.span.to_json() if self.span else None,
        }


class GcadError(Exception):
    """An error that carries a diagnostic with a source span."""

    def __init__(self, message: str, span: Span | None = None) -> None:
        super().__init__(message)
        self.diagnostic = Diagnostic(Severity.ERROR, message, span)

    def __str__(self) -> str:
        return str(self.diagnostic)
