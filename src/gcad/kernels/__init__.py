"""Kernel registry (SPEC §5.2): a kernel is a folder with a kernel.toml manifest.

Search order, first match wins: GCAD_KERNEL_PATH entries, ./kernels, the user kernel folder,
then the kernels bundled with GCAD Studio.

Parsers:
    gcore   the source is GCore S-expression text
    earley  grammar.lark (any context-free grammar) + rewrite.toml
    lalr    grammar.lark (LALR(1), faster) + rewrite.toml
    process an external program speaking the stdio protocol (SPEC §7.2), named by the
            manifest's [process] command; see gcad.kernels.process

A kernel may also ship prelude.gcore: GCore definitions evaluated in a scope between the
built-ins and the user's program, for renames such as OpenSCAD's cube -> box.

A process kernel runs a program with the user's rights, so one from outside the bundled
kernels is used only once the user has trusted its folder (FR-K6, FR-K7).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from gcad.core.sexpr import Expr, SList, Sym, read
from gcad.diagnostics import GcadError
from gcad.kernels.grammar import GrammarKernel, load_grammar_kernel

BUNDLED = Path(__file__).parent
PARSERS = ("gcore", "earley", "lalr", "process")
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
    start: str = "start"
    #: process kernels: the program to start, with {python}, {kernel} and {env:NAME} fields
    command: tuple[str, ...] = ()
    #: process kernels: what to install or set when the program cannot be found
    setup: str = ""
    #: highlight classes for kernels without a grammar, in the shape describe() gives
    highlight: dict[str, object] | None = field(default=None, compare=False, hash=False)

    @property
    def is_grammar(self) -> bool:
        return self.parser in ("earley", "lalr")

    @property
    def is_process(self) -> bool:
        return self.parser == "process"

    @property
    def bundled(self) -> bool:
        return self.path.resolve().is_relative_to(BUNDLED.resolve())

    def matches(self, path: Path) -> int:
        """Length of the longest extension of this kernel that `path` ends with, else 0."""
        name = path.name.lower()
        return max((len(e) for e in self.extensions if name.endswith(e)), default=0)

    def grammar(self) -> GrammarKernel:
        if not self.is_grammar:
            raise GcadError(f"kernel '{self.id}' has no grammar (parser = {self.parser})")
        return load_grammar_kernel(self.id, self.path, self.parser, self.start)

    def to_gcore(self, source: str, file: str) -> list[Expr]:
        if self.parser == "gcore":
            return read(source, file)
        if self.is_process:
            raise GcadError(f"kernel '{self.id}' is a program; it returns geometry, not GCore")
        return self.grammar().to_gcore(source, file)

    def prelude(self) -> list[Expr]:
        path = self.path / "prelude.gcore"
        if not path.is_file():
            return []
        return read(path.read_text(encoding="utf-8"), f"{self.id}/prelude.gcore")

    def describe(self) -> dict[str, object]:
        info: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "extensions": list(self.extensions),
            "parser": self.parser,
            "comment": self.comment,
        }
        if self.is_grammar:
            info["highlight"] = self.grammar().describe()
        elif self.highlight is not None:
            info["highlight"] = self.highlight
        if self.is_process:
            from gcad.kernels.process import unavailable_reason

            info["process"] = True
            info["available"] = (reason := unavailable_reason(self)) is None
            if reason:
                info["reason"] = reason
            info["builtins"] = []
            return info
        info["builtins"] = self.builtin_names()
        return info

    def builtin_names(self) -> list[str]:
        """Names a program can call without defining them: GCore built-ins and the prelude's
        definitions (for example OpenSCAD's `cube`). Editors highlight them as functions."""
        from gcad.core.builtins import global_env
        from gcad.core.evaluator import Builtin

        names = {n for n, v in global_env().vars.items() if isinstance(v, Builtin)}
        for form in self.prelude():
            if isinstance(form, SList) and len(form.items) > 1 and isinstance(form.items[0], Sym):
                target = form.items[1]
                if isinstance(target, SList) and target.items:
                    target = target.items[0]
                if isinstance(target, Sym) and form.items[0].name in ("define", "module"):
                    names.add(target.name)
        return sorted(n for n in names if n[:1].isalpha() or n[:1] == "_")

    def examples(self) -> list[Path]:
        folder = self.path / "examples"
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if self.matches(p))


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
    process = data.get("process", {})
    command = process.get("command", []) if isinstance(process, dict) else None
    if data["parser"] == "process" and (
        not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command)
    ):
        raise GcadError(f'{manifest}: a process kernel needs [process] command = ["program", ...]')
    highlight = data.get("highlight")
    if highlight is not None and not isinstance(highlight, dict):
        raise GcadError(f"{manifest}: [highlight] must be a table")
    return Kernel(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data["version"]),
        extensions=tuple(e.lower() for e in exts),
        parser=str(data["parser"]),
        comment=str(data.get("comment", "")),
        path=folder,
        description=str(data.get("description", "")),
        start=str(data.get("start", "start")),
        command=tuple(command or ()),
        setup=str(process.get("setup", "")) if isinstance(process, dict) else "",
        highlight=highlight,
    )


def config_dir() -> Path:
    """GCAD Studio's per-user folder; GCAD_CONFIG_DIR overrides it (tests use that)."""
    if override := os.environ.get("GCAD_CONFIG_DIR"):
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gcad"


def user_kernel_dir() -> Path:
    return config_dir() / "kernels"


# --- trust for kernels that run a program (FR-K6, FR-K7) ------------------------------------


def fingerprint(kernel: Kernel) -> str:
    """SHA-256 over the kernel folder's files, examples aside: editing the program or the
    manifest withdraws the trust given to the old version."""
    digest = hashlib.sha256()
    root = kernel.path.resolve()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if rel.parts[0] in ("examples", "__pycache__"):
            continue
        digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _trust_file() -> Path:
    return config_dir() / "trusted-kernels.json"


def _trusted() -> dict[str, str]:
    try:
        data = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def is_trusted(kernel: Kernel) -> bool:
    if not kernel.is_process or kernel.bundled:
        return True
    return _trusted().get(str(kernel.path.resolve())) == fingerprint(kernel)


def trust(kernel: Kernel) -> None:
    entries = _trusted()
    entries[str(kernel.path.resolve())] = fingerprint(kernel)
    path = _trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    best = max(kernels.values(), key=lambda k: k.matches(path), default=None)
    if best is None or not best.matches(path):
        raise GcadError(f"no kernel handles '{path.suffix.lower()}' files; pass --kernel")
    return best
