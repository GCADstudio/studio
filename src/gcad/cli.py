"""The gcad command line (FR-CLI1, FR-CLI2). Needs no display, GPU or browser."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gcad import __version__
from gcad.backends import available_backends
from gcad.core.sexpr import to_text
from gcad.diagnostics import Diagnostic, GcadError, Severity
from gcad.exporters import FORMATS
from gcad.kernels import discover, load_kernel
from gcad.pipeline import evaluate_file, evaluate_source, export_file


def print_diagnostics(diagnostics: list[Diagnostic]) -> bool:
    """Print to stderr; return True when any is an error."""
    for d in diagnostics:
        print(d, file=sys.stderr)
    return any(d.severity is Severity.ERROR for d in diagnostics)


def cmd_kernels(args: argparse.Namespace) -> int:
    kernels, warnings = discover()
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.json:
        rows = [
            {
                "id": k.id,
                "name": k.name,
                "version": k.version,
                "extensions": list(k.extensions),
                "parser": k.parser,
            }
            for k in kernels.values()
        ]
        print(json.dumps(rows, indent=2))
        return 0
    for k in kernels.values():
        print(f"{k.id:<12} {k.version:<8} {' '.join(k.extensions):<14} {k.name}")
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    for f in FORMATS.values():
        status = "ready" if f.available else f"planned {f.milestone}"
        print(f"{f.id:<6} {' '.join(f.extensions):<12} {f.needs:<5} {status:<11} {f.name}")
    print()
    for name, status in available_backends().items():
        print(f"back-end {name}: {status}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    evaluated = evaluate_file(Path(args.file), args.kernel)
    failed = print_diagnostics(evaluated.result.diagnostics)
    graph = evaluated.result.graph
    if args.dump == "gcore":
        print("\n".join(to_text(f) for f in evaluated.forms))
    if failed or graph is None:
        return 1
    if args.dump == "graph":
        print(json.dumps(graph.to_json(spans=True), indent=2, sort_keys=True))
    elif args.dump == "hash":
        print(graph.hash())
    elif args.dump == "summary":
        nodes = graph.nodes()
        print(
            f"ok: kernel {evaluated.kernel.id}, {graph.root.dim}D model, "
            f"{len(nodes)} distinct nodes, graph {graph.hash()[:16]}"
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    src = Path(args.file)
    if args.output:
        out = Path(args.output)
    elif args.format and args.format.lower() in FORMATS:
        out = Path(src.stem + FORMATS[args.format.lower()].extensions[0])
    else:
        raise GcadError("give an output file with -o, or a format with -f")
    options = {"ascii": args.ascii}
    evaluated, report = export_file(
        src, out, args.format, args.kernel, args.backend, args.tol, options
    )
    failed = print_diagnostics(evaluated.result.diagnostics)
    if failed or report is None:
        return 1
    detail = f"{report.triangles} triangles, " if report.triangles is not None else ""
    names = ", ".join(str(p) for p in report.files)
    print(f"wrote {names} ({report.format_name}; {detail}volume {report.volume:.6g} mm^3)")
    return 0


def cmd_kernel_check(args: argparse.Namespace) -> int:
    kernel = load_kernel(Path(args.dir))
    print(f"manifest ok: {kernel.id} {kernel.version} ({kernel.parser})")
    examples = kernel.examples()
    if not examples:
        print("warning: no examples/ to run", file=sys.stderr)
    failures = 0
    for path in examples:
        evaluated = evaluate_source(path.read_text(encoding="utf-8"), str(path), kernel)
        failed = print_diagnostics(evaluated.result.diagnostics)
        if failed or evaluated.result.graph is None:
            failures += 1
            print(f"FAIL {path.name}")
        else:
            print(f"ok   {path.name}  graph {evaluated.result.graph.hash()[:16]}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gcad", description="GCAD Studio command line")
    parser.add_argument("--version", action="version", version=f"gcad {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("kernels", help="list the language kernels on the search path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_kernels)

    p = sub.add_parser("formats", help="list export formats and geometry back-ends")
    p.set_defaults(func=cmd_formats)

    p = sub.add_parser("eval", help="evaluate a source file and report or dump the result")
    p.add_argument("file")
    p.add_argument("--kernel", help="kernel id (default: from the file extension)")
    p.add_argument("--dump", choices=["summary", "gcore", "graph", "hash"], default="summary")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("export", help="evaluate a source file and export it")
    p.add_argument("file")
    p.add_argument("-o", "--output", help="output file; its extension picks the format")
    p.add_argument("-f", "--format", help="format id (see 'gcad formats')")
    p.add_argument("--kernel", help="kernel id (default: from the file extension)")
    p.add_argument("--backend", default="manifold", help="manifold (M) or occt (B)")
    p.add_argument("--tol", type=float, help="linear tessellation tolerance in mm")
    p.add_argument("--ascii", action="store_true", help="ASCII instead of binary STL")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("kernel", help="kernel authoring tools")
    ksub = p.add_subparsers(dest="kernel_command", required=True)
    k = ksub.add_parser("check", help="validate a kernel folder and run its examples")
    k.add_argument("dir")
    k.set_defaults(func=cmd_kernel_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except GcadError as exc:
        print(exc, file=sys.stderr)
        return 1
