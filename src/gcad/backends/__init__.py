"""Geometry back-ends (SPEC §5.4): they realize a geometry graph as shapes."""

from __future__ import annotations

from gcad.backends.base import Backend, RealizedModel
from gcad.diagnostics import GcadError

ALIASES = {"m": "manifold", "manifold": "manifold", "b": "occt", "occt": "occt"}


def available_backends() -> dict[str, str]:
    """Back-end name -> status ("ok" or the reason it cannot load)."""
    status = {}
    for name in ("manifold", "occt"):
        try:
            get_backend(name)
            status[name] = "ok"
        except GcadError as exc:
            status[name] = exc.diagnostic.message
    return status


def get_backend(name: str = "manifold") -> Backend:
    key = ALIASES.get(name.lower())
    if key == "manifold":
        try:
            from gcad.backends.manifold_backend import ManifoldBackend
        except ImportError as exc:
            raise GcadError(f"back-end 'manifold' unavailable: {exc}") from None
        return ManifoldBackend()
    if key == "occt":
        raise GcadError("back-end 'occt' (exact B-rep) arrives in milestone M3")
    raise GcadError(f"unknown back-end '{name}' (choose manifold or occt)")


__all__ = ["Backend", "RealizedModel", "available_backends", "get_backend"]
