"""Kernel registry (SPEC §5.2): a kernel is a folder with a kernel.toml manifest.

Search order, first match wins: GCAD_KERNEL_PATH entries, ./kernels, the user kernel folder,
then the kernels bundled with GCAD Studio.

Parsers:
    gcore   the source is GCore S-expression text (milestone M1)
    lark    grammar.lark + rewrite.toml (milestone M2)
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from gcad.core.sexpr import Expr, read
from gcad.diagnostics import GcadError

BUNDLED = Path(__file__).parent
PARSERS = ("gcore", "lark")
REQUIRED = ("id", "name", "version", "extensions", "parser")


@dataclass(frozen=True)
class Kernel:
    id: str
    name: str
    version: str
    extensions: tuple[str, ...]
    parser: str
    comment: str
    path: Path
    description: str = ""

    def to_gcore(self, source: str, file: str) -> list[Expr]:
        if self.parser == "gcore":
            return read(source, file)
        raise GcadError(f"kernel '{self.id}': parser '{self.parser}' arrives in milestone M2")

    def examples(self) -> list[Path]:
        folder = self.path / "examples"
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.suffix in self.extensions)


def load_kernel(folder: Path) -> Kernel:
    manifest = folder / "kernel.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GcadError(f"{folder}: no kernel.toml") from None
    except tomllib.TOMLDecodeError as exc:
        raise GcadError(f"{manifest}: {exc}") from None
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise GcadError(f"{manifest}: missing {', '.join(missing)}")
    if data["parser"] not in PARSERS:
        raise GcadError(f"{manifest}: parser must be one of {', '.join(PARSERS)}")
    exts = data["extensions"]
    if (
        not isinstance(exts, list)
        or not exts
        or not all(isinstance(e, str) and e.startswith(".") for e in exts)
    ):
        raise GcadError(f'{manifest}: extensions must be a list like [".ext"]')
    return Kernel(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data["version"]),
        extensions=tuple(e.lower() for e in exts),
        parser=str(data["parser"]),
        comment=str(data.get("comment", "")),
        path=folder,
        description=str(data.get("description", "")),
    )


def user_kernel_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gcad" / "kernels"


def search_path() -> list[Path]:
    paths = [Path(p) for p in os.environ.get("GCAD_KERNEL_PATH", "").split(os.pathsep) if p]
    return [*paths, Path.cwd() / "kernels", user_kernel_dir(), BUNDLED]


def discover() -> tuple[dict[str, Kernel], list[str]]:
    """All kernels on the search path, and warnings about folders that failed to load."""
    kernels: dict[str, Kernel] = {}
    warnings: list[str] = []
    for root in search_path():
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if (p / "kernel.toml").is_file()):
            try:
                kernel = load_kernel(folder)
            except GcadError as exc:
                warnings.append(exc.diagnostic.message)
                continue
            kernels.setdefault(kernel.id, kernel)
    return kernels, warnings


def kernel_for(path: Path, kernel_id: str | None = None) -> Kernel:
    kernels, _ = discover()
    if kernel_id:
        if kernel_id not in kernels:
            raise GcadError(f"no kernel '{kernel_id}' (see 'gcad kernels')")
        return kernels[kernel_id]
    suffix = path.suffix.lower()
    for kernel in kernels.values():
        if suffix in kernel.extensions:
            return kernel
    raise GcadError(f"no kernel handles '{suffix}' files; pass --kernel")
