"""Compiled EXPRESS schemas (SPEC FR-I4): the dictionaries Part 21 files are checked against.

None is in the repository: the standards are sold by ISO, and their EXPRESS is their text. The
cache folder holds what the user puts there, once:

    gcad iso schema fetch AP242       the AP242 long form ISO publishes (also AP214, AP203, AP203E2)
    gcad iso schema add file.exp      any long-form EXPRESS, or a compiled dictionary (.json)
    gcad iso schema build --part 42=part42.pdf ...
                                      the resource parts (41, 42, 43, 50, 55, 108 with both
                                      corrigenda, 111, 112, ISO 13584-20) from the user's own copies

A file is checked against the long form of the application protocol its FILE_SCHEMA names when
that is cached, and against the resource parts otherwise. The cache is GCAD_ISO10303_SCHEMAS, or
`gcad/iso10303` in the user's cache folder.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from gcad.diagnostics import GcadError
from gcad.iso10303._nxstyle import express
from gcad.iso10303._nxstyle import iso10303 as nx

RESOURCES = "ISO10303_RESOURCES"  # the resource parts, under this name in the cache
FETCH = nx.FETCH


def schema_dir() -> Path:
    if os.environ.get("GCAD_ISO10303_SCHEMAS"):
        return Path(os.environ["GCAD_ISO10303_SCHEMAS"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "gcad" / "iso10303"


_EMPTY: Any = None


def resources() -> Any:
    """The resource parts' dictionary, or an empty one when it has not been built or added."""
    global _EMPTY
    path = schema_dir() / f"{RESOURCES}.json"
    if path.is_file():
        key = str(path)
        if key not in nx._AP_DICTS:
            nx._AP_DICTS[key] = express.Dictionary.load(key)
        return nx._AP_DICTS[key]
    if _EMPTY is None:
        _EMPTY = express.Dictionary.from_schemas([])
    return _EMPTY


def _dictionary(extra: tuple[str, ...] = ()) -> Any:
    d = resources()
    if extra:
        d = d.copy()
        for p in extra:
            d.merge(express.Dictionary.from_text(Path(p).read_text("utf-8", errors="replace")))
    return d


# The vendored checker finds its cache and its fall-back dictionary through these two names.
nx.schema_dir = lambda: str(schema_dir())
nx.dictionary = _dictionary


def cached() -> list[str]:
    folder = schema_dir()
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def has_resources() -> bool:
    return (schema_dir() / f"{RESOURCES}.json").is_file()


def fetch(ap: str) -> list[tuple[str, int, str | None]]:
    """Download an application protocol's long form (the explicit network step) and compile it."""
    if ap.upper() not in FETCH:
        raise GcadError(f"unknown application protocol {ap!r}; choose {', '.join(FETCH)}")
    try:
        made: list[tuple[str, int, str | None]] = nx.fetch_schema(ap)
    except OSError as exc:
        raise GcadError(f"could not fetch {FETCH[ap.upper()]}: {exc}") from None
    return made


def add(path: Path) -> list[tuple[str, int, str | None]]:
    """Long-form EXPRESS (.exp) or a dictionary compiled by GCAD or freecad-nxstyle (.json)."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not str(data.get("format", "")).startswith("nxstyle express dictionary"):
            raise GcadError(f"{path.name} is not a compiled EXPRESS dictionary")
        schemas = {e.get("schema") for e in data.get("entities", {}).values()}
        name = RESOURCES if not any(s and s.endswith("_mim_lf") for s in schemas) else path.stem
        schema_dir().mkdir(parents=True, exist_ok=True)
        target = schema_dir() / f"{name.upper()}.json"
        shutil.copyfile(path, target)
        nx._AP_DICTS.pop(str(target), None)
        return [(name.upper(), len(data.get("entities", {})), None)]
    made: list[tuple[str, int, str | None]] = nx.add_schema(
        path.read_text(encoding="utf-8", errors="replace"), str(path.name)
    )
    return made


def build(parts: list[str], corrigenda: list[str], exp: list[str]) -> Path:
    """Compile the resource parts from the user's own copies (PDF or pdftotext output)."""
    from gcad.iso10303._nxstyle import dictionary_tool

    schema_dir().mkdir(parents=True, exist_ok=True)
    out = schema_dir() / f"{RESOURCES}.json"
    argv = [a for p in parts for a in ("--part", p)]
    argv += [a for c in corrigenda for a in ("--cor", c)]
    argv += [a for e in exp for a in ("--exp", e)]
    status = dictionary_tool.main([*argv, "--out", str(out)])
    if status not in (None, 0):
        raise GcadError("building the resource dictionary failed")
    nx._AP_DICTS.pop(str(out), None)
    return out
