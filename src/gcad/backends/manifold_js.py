"""Manifold's own WebAssembly build behind the manifold3d Python API (SPEC §4.1, FR-W2).

manifold3d has no Pyodide build, so in the browser `manifold_backend.py` imports this module
in its place. It implements only the subset of the manifold3d API that the back-end uses, on
npm `manifold-3d` (the same Manifold version), through Pyodide's JS FFI; the realizer itself
is shared code. The web worker instantiates the WebAssembly module and registers it as the JS
module `_gcad_manifold` before gcad is imported.

Every wrapper owns one object on the WebAssembly heap, which JavaScript's garbage collector
cannot see, and frees it when Python drops the wrapper.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from typing import Any

import _gcad_manifold as _wasm  # registered by js/worker.js
import js
import numpy as np
from pyodide.ffi import JsException, to_js


class OpType(enum.Enum):
    Add = "Union"
    Subtract = "Difference"
    Intersect = "Intersection"


class FillRule(enum.Enum):
    EvenOdd = "EvenOdd"
    NonZero = "NonZero"
    Positive = "Positive"
    Negative = "Negative"


class Error(enum.Enum):
    NoError = "NoError"
    NonFiniteVertex = "NonFiniteVertex"
    NotManifold = "NotManifold"
    VertexOutOfBounds = "VertexOutOfBounds"
    PropertiesWrongLength = "PropertiesWrongLength"
    MissingPositionProperties = "MissingPositionProperties"
    MergeVectorsDifferentLengths = "MergeVectorsDifferentLengths"
    MergeIndexOutOfBounds = "MergeIndexOutOfBounds"
    TransformWrongLength = "TransformWrongLength"
    RunIndexWrongLength = "RunIndexWrongLength"
    FaceIDWrongLength = "FaceIDWrongLength"
    InvalidConstruction = "InvalidConstruction"
    ResultTooLarge = "ResultTooLarge"
    InvalidTangents = "InvalidTangents"
    Cancelled = "Cancelled"


def _floats(values: Iterable[Any]) -> Any:
    """A JS array of numbers (numpy scalars are not numbers to JavaScript)."""
    return to_js([float(v) for v in values])


def _array(data: np.ndarray, dtype: str, js_type: Any) -> Any:
    raw = to_js(np.ascontiguousarray(data, dtype=dtype).tobytes())  # a Uint8Array copy
    return js_type.new(raw.buffer)


def _numpy(typed: Any, dtype: str) -> np.ndarray:
    return np.frombuffer(typed.to_bytes(), dtype=dtype) if typed is not None else np.zeros(0)


class _Owned:
    def __init__(self, js: Any) -> None:
        self._js = js

    def __del__(self) -> None:
        js = self.__dict__.pop("_js", None)
        if js is not None:
            js.delete()


class Mesh:
    """A triangle mesh as numpy arrays, like manifold3d.Mesh (MeshGL)."""

    def __init__(
        self,
        vert_properties: np.ndarray,
        tri_verts: np.ndarray,
        run_index: np.ndarray | None = None,
        run_original_id: np.ndarray | None = None,
    ) -> None:
        self.vert_properties = np.asarray(vert_properties)
        self.tri_verts = np.asarray(tri_verts)
        self.run_index = run_index if run_index is not None else np.zeros(0, dtype=np.uint32)
        self.run_original_id = (
            run_original_id if run_original_id is not None else np.zeros(0, dtype=np.uint32)
        )


class CrossSection(_Owned):
    def __init__(
        self,
        contours: Sequence[Sequence[Sequence[float]]] | None = None,
        fillrule: FillRule = FillRule.Positive,
        *,
        _js: Any = None,
    ) -> None:
        if _js is None:
            polygons = to_js([[[float(x), float(y)] for x, y in c] for c in contours or []])
            _js = _wasm.CrossSection.new(polygons, fillrule.value)
        super().__init__(_js)

    @staticmethod
    def square(size: Sequence[float], center: bool = False) -> CrossSection:
        return CrossSection(_js=_wasm.CrossSection.square(_floats(size), center))

    @staticmethod
    def circle(radius: float, circular_segments: int = 0) -> CrossSection:
        return CrossSection(_js=_wasm.CrossSection.circle(float(radius), int(circular_segments)))

    @staticmethod
    def batch_boolean(parts: Sequence[CrossSection], op: OpType) -> CrossSection:
        batch = getattr(_wasm.CrossSection, op.value.lower())
        return CrossSection(_js=batch(to_js([p._js for p in parts])))

    def __sub__(self, other: CrossSection) -> CrossSection:
        return CrossSection(_js=self._js.subtract(other._js))

    def __xor__(self, other: CrossSection) -> CrossSection:
        return CrossSection(_js=self._js.intersect(other._js))

    def transform(self, m: np.ndarray) -> CrossSection:
        """`m` is 2x3, row-major; Manifold's JS API takes a 3x3 in column-major order."""
        a = np.asarray(m, dtype=np.float64)
        columns = [a[0, 0], a[1, 0], 0.0, a[0, 1], a[1, 1], 0.0, a[0, 2], a[1, 2], 1.0]
        return CrossSection(_js=self._js.transform(_floats(columns)))

    def simplify(self, epsilon: float = 1e-6) -> CrossSection:
        return CrossSection(_js=self._js.simplify(float(epsilon)))

    def to_polygons(self) -> list[np.ndarray]:
        return [np.asarray(p, dtype=np.float64) for p in self._js.toPolygons().to_py()]

    def area(self) -> float:
        return float(self._js.area())

    def is_empty(self) -> bool:
        return bool(self._js.isEmpty())

    def bounds(self) -> tuple[float, float, float, float]:
        b = self._js.bounds()
        return (b.min[0], b.min[1], b.max[0], b.max[1])


class Manifold(_Owned):
    def __init__(self, mesh: Mesh | None = None, *, _js: Any = None) -> None:
        self._failed: Error | None = None
        if _js is None and mesh is None:
            _js = _wasm.Manifold.cube(_floats([0, 0, 0]), False)  # the empty manifold
        elif _js is None and mesh is not None:
            props = np.atleast_2d(mesh.vert_properties)
            options = {
                "numProp": props.shape[1] if props.size else 3,
                "vertProperties": _array(props, "<f4", js.Float32Array),
                "triVerts": _array(mesh.tri_verts, "<u4", js.Uint32Array),
            }
            js_mesh = _wasm.Mesh.new(to_js(options, dict_converter=js.Object.fromEntries))
            try:
                _js = _wasm.Manifold.new(js_mesh)
            except JsException as exc:
                # manifold3d reports a bad mesh by status(); the JS build throws instead.
                code = getattr(getattr(exc, "js_error", None), "code", None)
                known = {e.value for e in Error}
                self._failed = Error(code) if code in known else Error.NotManifold
                _js = _wasm.Manifold.cube(_floats([0, 0, 0]), False)
        super().__init__(_js)

    @staticmethod
    def _of(js: Any) -> Manifold:
        return Manifold(_js=js)

    @staticmethod
    def cube(size: Sequence[float], center: bool = False) -> Manifold:
        return Manifold._of(_wasm.Manifold.cube(_floats(size), center))

    @staticmethod
    def sphere(radius: float, circular_segments: int = 0) -> Manifold:
        return Manifold._of(_wasm.Manifold.sphere(float(radius), int(circular_segments)))

    @staticmethod
    def cylinder(
        height: float,
        radius_low: float,
        radius_high: float = -1.0,
        circular_segments: int = 0,
        center: bool = False,
    ) -> Manifold:
        js = _wasm.Manifold.cylinder(
            float(height), float(radius_low), float(radius_high), int(circular_segments), center
        )
        return Manifold._of(js)

    @staticmethod
    def extrude(
        section: CrossSection,
        height: float,
        n_divisions: int = 0,
        twist_degrees: float = 0.0,
        scale_top: Sequence[float] = (1.0, 1.0),
    ) -> Manifold:
        js = _wasm.Manifold.extrude(
            section._js, float(height), int(n_divisions), float(twist_degrees), _floats(scale_top)
        )
        return Manifold._of(js)

    @staticmethod
    def revolve(
        section: CrossSection, circular_segments: int = 0, revolve_degrees: float = 360.0
    ) -> Manifold:
        js = _wasm.Manifold.revolve(section._js, int(circular_segments), float(revolve_degrees))
        return Manifold._of(js)

    @staticmethod
    def batch_boolean(parts: Sequence[Manifold], op: OpType) -> Manifold:
        batch = getattr(_wasm.Manifold, op.value.lower())
        return Manifold._of(batch(to_js([p._js for p in parts])))

    def __sub__(self, other: Manifold) -> Manifold:
        return Manifold._of(self._js.subtract(other._js))

    def __xor__(self, other: Manifold) -> Manifold:
        return Manifold._of(self._js.intersect(other._js))

    def transform(self, m: np.ndarray) -> Manifold:
        """`m` is 3x4, row-major; Manifold's JS API takes a 4x4 in column-major order."""
        a = np.asarray(m, dtype=np.float64)
        columns = [*a[:, 0], 0.0, *a[:, 1], 0.0, *a[:, 2], 0.0, *a[:, 3], 1.0]
        return Manifold._of(self._js.transform(_floats(columns)))

    def translate(self, offset: Sequence[float]) -> Manifold:
        return Manifold._of(self._js.translate(_floats(offset)))

    def slice(self, height: float) -> CrossSection:
        return CrossSection(_js=self._js.slice(float(height)))

    def project(self) -> CrossSection:
        return CrossSection(_js=self._js.project())

    def as_original(self) -> Manifold:
        return Manifold._of(self._js.asOriginal())

    def original_id(self) -> int:
        return int(self._js.originalID())

    def status(self) -> Error:
        return self._failed or Error(self._js.status())

    def is_empty(self) -> bool:
        return bool(self._js.isEmpty())

    def volume(self) -> float:
        return float(self._js.volume())

    def surface_area(self) -> float:
        return float(self._js.surfaceArea())

    def bounding_box(self) -> tuple[float, ...]:
        b = self._js.boundingBox()
        return (*b.min, *b.max)

    def to_mesh64(self) -> Mesh:
        """The mesh; the JS build only returns single precision (MeshGL), widened here."""
        m = self._js.getMesh()
        props = _numpy(m.vertProperties, "<f4").astype(np.float64)
        return Mesh(
            props.reshape(-1, int(m.numProp)) if props.size else np.zeros((0, 3)),
            _numpy(m.triVerts, "<u4").reshape(-1, 3),
            _numpy(m.runIndex, "<u4"),
            _numpy(m.runOriginalID, "<u4"),
        )
