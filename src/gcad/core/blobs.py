"""Content-addressed store for geometry that arrives as a file (a process kernel's STEP or mesh).

The graph keeps only the SHA-256 of the bytes, so node ids and graph hashes stay small and
stable; back-ends fetch the bytes here. The store lives for the process: the CLI, the studio
and the browser worker all evaluate and realize in one process.
"""

from __future__ import annotations

import hashlib
import threading

from gcad.diagnostics import GcadError

_lock = threading.Lock()
_blobs: dict[str, bytes] = {}
MAX_ENTRIES = 64  # oldest first out; one evaluation adds one entry


def put(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    with _lock:
        _blobs.pop(digest, None)
        _blobs[digest] = data
        while len(_blobs) > MAX_ENTRIES:
            del _blobs[next(iter(_blobs))]
    return digest


def get(digest: str) -> bytes:
    with _lock:
        data = _blobs.get(digest)
    if data is None:
        raise GcadError(f"imported geometry {digest[:12]} is no longer loaded; evaluate again")
    return data
