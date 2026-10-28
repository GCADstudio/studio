"""Parametric STEP (SPEC FR-I7): the model's construction as ISO 10303-42 CSG with its ISO
10303-55 history and ISO 10303-108 parameters, under the AP242 long form. Written from the
geometry graph, with no geometry engine, so it is available everywhere (also in the browser)."""

from __future__ import annotations

from pathlib import Path

from gcad.exporters import ExportJob
from gcad.iso10303.writer import to_step


def write(job: ExportJob) -> list[Path]:
    text = to_step(job.graph, job.title, job.time.strftime("%Y-%m-%dT%H:%M:%S"), job.source)
    job.path.write_bytes(text.encode("ascii"))
    return [job.path]
