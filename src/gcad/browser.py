"""The studio inside the browser (SPEC §4.1): `gcad.server.Studio` in a Pyodide web worker.

The worker passes each JSON-RPC request through `handle` and posts back the response; the
methods are the server's own. Only where files live differs: a folder that the worker backs
with IndexedDB, shown to the visitor as "this browser".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from gcad.server import Studio

ROOT_LABEL = "this browser"


class BrowserStudio(Studio):
    def rpc_hello(self) -> dict[str, object]:
        return {**super().rpc_hello(), "root": ROOT_LABEL, "mode": "web"}

    def rpc_file_list(self) -> dict[str, object]:
        return {**super().rpc_file_list(), "root": ROOT_LABEL}

    def rpc_file_save(self, path: str, text: str) -> dict[str, object]:
        """An uploaded EXPRESS schema (.exp, or a compiled .schema.json) is compiled into the
        browser's schema cache, so STEP files are checked here too (SPEC FR-I4)."""
        saved = super().rpc_file_save(path, text)
        if path.endswith((".exp", ".schema.json")):
            from gcad.iso10303 import schemas

            made = schemas.add(self.resolve(str(saved["path"])))
            saved["schemas"] = [name for name, _, _ in made]
        return saved


_studio: BrowserStudio | None = None


def start(root: str) -> None:
    global _studio
    Path(root).mkdir(parents=True, exist_ok=True)
    # EXPRESS schemas uploaded for checking STEP files live with the visitor's files (IndexedDB).
    os.environ.setdefault("GCAD_ISO10303_SCHEMAS", str(Path(root) / ".iso10303-schemas"))
    _studio = BrowserStudio(Path(root))


def handle(request: str) -> str:
    """One JSON-RPC request as JSON text to its response as JSON text."""
    if _studio is None:
        raise RuntimeError("start() the browser studio first")
    return json.dumps(_studio.respond(json.loads(request)))
