"""Geometry back-ends (SPEC §5.4): they realize a geometry graph as shapes.

`auto` picks occt (exact B-rep) for formats that need exact geometry and manifold (fast,
robust mesh booleans) for everything else.
"""

from __future__ import annotations

import sys

from gcad.backends.base import Backend, RealizedModel
from gcad.diagnostics import GcadError

ALIASES = {"m": "manifold", "manifold": "manifold", "b": "occt", "occt": "occt"}


def select_backend(name: str, needs: str) -> Backend:
    """Resolve a back-end name, where `auto` depends on what the export format needs."""
    if name.lower() == "auto":
        name = "occt" if needs == "brep" else "manifold"
    return get_backend(name)


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
        try:
            import OCP  # noqa: F401
        except ImportError:
            if sys.platform == "emscripten":  # the web studio (SPEC §4.1)
                raise GcadError(
                    "back-end 'occt' unavailable: OpenCASCADE has no WebAssembly build for "
                    "the browser; install GCAD Studio locally for STEP and IGES"
                ) from None
            raise GcadError(
                "back-end 'occt' unavailable: OpenCASCADE (the cadquery-ocp package) is not "
                "installed; it has no build for this platform (for example Windows on ARM)"
            ) from None
        from gcad.backends.occt_backend import OcctBackend

        return OcctBackend()
    raise GcadError(f"unknown back-end '{name}' (choose auto, manifold or occt)")


__all__ = ["Backend", "RealizedModel", "available_backends", "get_backend", "select_backend"]
