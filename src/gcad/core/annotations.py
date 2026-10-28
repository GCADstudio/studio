"""Annotations: what a model says about its geometry, beside the geometry graph (SPEC FR-C11).

A kernel's prelude calls `(annotate id label ...)` to report a note, a dimension, a tolerance,
a saved view: text, optional line and triangle geometry in model coordinates, the records it
applies to and the other annotations it relates to. Annotations travel with the evaluation
result, not in the graph, so they never change a graph's hash and back-ends never see them;
the studio draws them over the model and lists them in its annotation panel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from gcad.diagnostics import Span


@dataclass(frozen=True)
class Target:
    """What an annotation applies to: a record's name (a STEP face's `#n`) and where it is."""

    name: str
    span: Span | None


@dataclass
class Annotation:
    id: str
    label: str
    kind: str = "note"
    span: Span | None = None
    #: polylines, each a flat [x0 y0 z0 x1 y1 z1 ...] in document units
    lines: list[list[float]] = field(default_factory=list)
    #: filled triangles, flat [x y z] * 3 per triangle
    triangles: list[float] = field(default_factory=list)
    color: tuple[float, float, float, float] | None = None
    #: ids of the views (annotations of kind "view") that show this one
    views: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    #: ids of related annotations: a tolerance and the callout that presents it
    links: list[str] = field(default_factory=list)
    details: list[tuple[str, str]] = field(default_factory=list)
    #: for a view: [position, direction of view, up], each [x y z]
    camera: list[list[float]] | None = None

    def to_json(self, file: str | None = None) -> dict[str, object]:
        out: dict[str, object] = {"id": self.id, "label": self.label, "kind": self.kind}
        if self.span is not None:
            out["span"] = span_json(self.span, file)
        if self.lines:
            out["lines"] = [[_round(v) for v in line] for line in self.lines]
        if self.triangles:
            out["triangles"] = [_round(v) for v in self.triangles]
        if self.color is not None:
            out["color"] = list(self.color)
        for key in ("views", "links"):
            if getattr(self, key):
                out[key] = list(getattr(self, key))
        if self.targets:
            out["targets"] = [
                {"name": t.name, **({"span": span_json(t.span, file)} if t.span else {})}
                for t in self.targets
            ]
        if self.details:
            out["details"] = [list(d) for d in self.details]
        if self.camera is not None:
            out["camera"] = self.camera
        return out

    def summary(self) -> str:
        """One line for the console: id, kind, label and the details."""
        parts = [f"{self.id} {self.kind}: {self.label}"]
        parts += [f"{k} {v}" for k, v in self.details]
        if self.targets:
            parts.append("on " + " ".join(t.name for t in self.targets))
        return "; ".join(parts)


def span_json(span: Span, file: str | None = None) -> dict[str, object]:
    out = dict(span.to_json())
    if file is not None:
        out["inFile"] = span.file == file
    return out


def _round(v: float) -> float:
    """Six decimals: well below a drawing's resolution, and a third of the JSON."""
    r = round(v, 6)
    return 0.0 if r == 0 or not math.isfinite(r) else r
