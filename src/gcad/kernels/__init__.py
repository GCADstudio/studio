"""Kernel registry (SPEC §5.2): a kernel is a folder with a kernel.toml manifest.

Search order, first match wins: GCAD_KERNEL_PATH entries, ./kernels, the user kernel folder,
then the kernels bundled with GCAD Studio.

Parsers:
    gcore   the source is GCore S-expression text
    earley  grammar.lark (any context-free grammar) + rewrite.toml
    lalr    grammar.lark (LALR(1), faster) + rewrite.toml

A kernel may also ship prelude.gcore: GCore definitions evaluated in a scope between the
built-ins and the user's program, for renames such as OpenSCAD's cube -> box.

`checker` in kernel.toml names a semantic checker the engine provides (FR-K9). It reads the
source after the grammar has accepted it and adds diagnostics: what a context-free grammar
cannot decide, such as an ISO 10303-21 file against its EXPRESS schemas.
"""

from __future__ import annotations

import importlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from gcad.core.sexpr import Expr, SList, Sym, read
from gcad.diagnostics import Diagnostic, GcadError
from gcad.kernels.grammar import GrammarKernel, load_grammar_kernel

BUNDLED = Path(__file__).parent
PARSERS = ("gcore", "earley", "lalr")
REQUIRED = ("id", "name", "version", "extensions", "parser")
#: semantic checkers a kernel may name (FR-K9): module path of a check(source, file) function
CHECKERS = {"iso10303-21": "gcad.iso10303.check"}


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
    checker: str = ""

    @property
    def is_grammar(self) -> bool:
        return self.parser in ("earley", "lalr")

    def grammar(self) -> GrammarKernel:
        if not self.is_grammar:
            raise GcadError(f"kernel '{self.id}' has no grammar (parser = {self.parser})")
        return load_grammar_kernel(self.id, self.path, self.parser, self.start)

    def to_gcore(self, source: str, file: str) -> list[Expr]:
        if self.parser == "gcore":
            return read(source, file)
        return self.grammar().to_gcore(source, file)

    def check(self, source: str, file: str) -> list[Diagnostic]:
        """The diagnostics of the kernel's semantic checker, if it names one."""
        if not self.checker:
            return []
        module = importlib.import_module(CHECKERS[self.checker])
        diagnostics: list[Diagnostic] = module.check(source, file)
        return diagnostics

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
    if data.get("checker", "") not in ("", *CHECKERS):
        raise GcadError(f"{manifest}: checker must be one of {', '.join(CHECKERS)}")
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
        start=str(data.get("start", "start")),
        checker=str(data.get("checker", "")),
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
