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
    # A file that does not conform (a semantic checker's errors) still has its model.
    if not evaluated.result.ok or graph is None:
        return 1
    if args.dump == "graph":
        print(json.dumps(graph.to_json(spans=True), indent=2, sort_keys=True))
    elif args.dump == "annotations":
        for a in evaluated.result.annotations:
            print(a.summary())
    elif args.dump == "hash":
        print(graph.hash())
    elif args.dump == "summary":
        nodes = graph.nodes()
        print(
            f"{'ok' if not failed else 'built, not conformant'}: kernel {evaluated.kernel.id}, "
            f"{graph.root.dim}D model, {len(nodes)} distinct nodes, graph {graph.hash()[:16]}"
            + (
                f", {len(evaluated.result.annotations)} annotations"
                if evaluated.result.annotations
                else ""
            )
        )
    return 1 if failed else 0


def cmd_export(args: argparse.Namespace) -> int:
    src = Path(args.file)
    if args.output:
        out = Path(args.output)
    elif args.format and args.format.lower() in FORMATS:
        out = Path(src.stem + FORMATS[args.format.lower()].extensions[0])
    else:
        raise GcadError("give an output file with -o, or a format with -f")
    options: dict[str, object] = {
        "ascii": args.ascii,
        "schema": args.schema,
        "epoch": args.epoch,
        "dxf_version": args.dxf_version,
    }
    evaluated, report = export_file(
        src, out, args.format, args.kernel, args.backend, args.tol, options
    )
    failed = print_diagnostics(evaluated.result.diagnostics)
    if failed or report is None:
        return 1
    names = ", ".join(str(p) for p in report.files)
    print(f"wrote {names} ({report.format_name}; back-end {report.backend}; {report.summary})")
    return 0


def cmd_kernel_check(args: argparse.Namespace) -> int:
    """FR-K4: manifest, grammar, rewrite coverage, prelude, then every example."""
    kernel = load_kernel(Path(args.dir))
    print(f"manifest ok: {kernel.id} {kernel.version} ({kernel.parser})")
    failures = 0
    if kernel.is_grammar:
        report = kernel.grammar().check()
        for w in report.warnings:
            print(f"warning: {w}", file=sys.stderr)
        for e in report.errors:
            print(f"error: {e}", file=sys.stderr)
        failures += len(report.errors)
        rules = len(kernel.grammar().templates)
        print(f"grammar ok: {rules} rewrite rules" if not report.errors else "grammar FAIL")
    prelude = kernel.prelude()
    if prelude:
        print(f"prelude ok: {len(prelude)} forms")
    examples = kernel.examples()
    if not examples:
        print("warning: no examples/ to run", file=sys.stderr)
    for path in examples:
        evaluated = evaluate_source(path.read_text(encoding="utf-8"), str(path), kernel)
        failed = print_diagnostics(evaluated.result.diagnostics)
        if failed or evaluated.result.graph is None:
            failures += 1
            print(f"FAIL {path.name}")
        else:
            print(f"ok   {path.name}  graph {evaluated.result.graph.hash()[:16]}")
    return 1 if failures else 0


def cmd_kernel_describe(args: argparse.Namespace) -> int:
    """What an editor needs: extensions, comment syntax, highlight classes (FR-K5)."""
    target = Path(args.kernel)
    if (target / "kernel.toml").is_file():
        kernel = load_kernel(target)
    else:
        kernels, _ = discover()
        if args.kernel not in kernels:
            raise GcadError(f"no kernel '{args.kernel}' (give an id or a kernel folder)")
        kernel = kernels[args.kernel]
    print(json.dumps(kernel.describe(), indent=2))
    return 0


def cmd_iso_schema(args: argparse.Namespace) -> int:
    """FR-I4: the EXPRESS schemas ISO 10303-21 files are checked against, kept in a cache."""
    from gcad.iso10303 import schemas

    if args.action == "list":
        print(f"cache: {schemas.schema_dir()}")
        for name in schemas.cached():
            print(f"  {name}")
        if not schemas.cached():
            print("  (empty: gcad iso schema fetch AP242, or gcad iso schema build)")
        return 0
    if args.action == "fetch":
        made = schemas.fetch(args.what)
    elif args.action == "add":
        made = schemas.add(Path(args.what))
    else:
        parts = [p for p in args.part or []]
        made = [(schemas.build(parts, args.cor or [], args.exp or []).stem, 0, None)]
    for name, count, old in made:
        extra = f", replacing {old}" if old else ""
        print(f"{name}: {count or 'compiled'} entities{extra}")
    return 0


def cmd_iso_check(args: argparse.Namespace) -> int:
    """FR-I4: header, form and every rule, as `gcad eval` reports them, but with no limit."""
    from gcad.iso10303.check import report

    path = Path(args.file)
    text = path.read_text(encoding="latin-1")
    result = report(text, str(path), rules=True)
    failed = print_diagnostics(result.diagnostics)
    print(
        f"{path.name}: checked against {result.schema or 'no schema'}; rules: {result.evaluated} "
        f"evaluated, {result.violated} violated, {result.unevaluated} not evaluated"
    )
    return 1 if failed else 0


def cmd_grammar_import(args: argparse.Namespace) -> int:
    """FR-K8: a grammar written in another notation, as Lark (Wirth Syntax Notation for now)."""
    from gcad.kernels.wsn import parse

    grammar = parse(Path(args.file).read_text(encoding="utf-8"))
    for note in grammar.notes:
        print(f"warning: {note}", file=sys.stderr)
    print(grammar.to_lark(args.start), end="")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """FR-U1: the local studio on 127.0.0.1, a random port and a session token."""
    import urllib.parse
    import webbrowser

    from gcad.server import StudioServer

    root = Path(args.root or ".").resolve()
    server = StudioServer(root, port=args.port)
    url = server.url
    if args.file:
        rel = Path(args.file).resolve()
        if not rel.is_relative_to(root):
            raise GcadError(f"{args.file} is outside the studio folder {root}")
        url += "&open=" + urllib.parse.quote(rel.relative_to(root).as_posix())
    print(f"GCAD Studio on {url}", flush=True)
    print(f"folder: {root}   (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
    return 0


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
    p.add_argument(
        "--dump", choices=["summary", "gcore", "graph", "hash", "annotations"], default="summary"
    )
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("export", help="evaluate a source file and export it")
    p.add_argument("file")
    p.add_argument("-o", "--output", help="output file; its extension picks the format")
    p.add_argument("-f", "--format", help="format id (see 'gcad formats')")
    p.add_argument("--kernel", help="kernel id (default: from the file extension)")
    p.add_argument(
        "--backend",
        default="auto",
        help="auto (occt for STEP/IGES, manifold otherwise), manifold (M) or occt (B)",
    )
    p.add_argument("--tol", type=float, help="linear tessellation tolerance in mm")
    p.add_argument("--ascii", action="store_true", help="ASCII instead of binary STL")
    p.add_argument("--schema", help="STEP application protocol: AP214 (default) or AP242")
    p.add_argument("--dxf-version", help="DXF version: R2000 (default), R12, ... R2018")
    p.add_argument(
        "--epoch",
        type=int,
        help="Unix time stamped into STEP, IGES and COLLADA (default: SOURCE_DATE_EPOCH or now)",
    )
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve", help="open the studio in your browser (local only)")
    p.add_argument("file", nargs="?", help="a source file to open")
    p.add_argument("--root", help="folder to open and save files in (default: current folder)")
    p.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("iso", help="ISO 10303 (STEP): schemas and conformance checks")
    isub = p.add_subparsers(dest="iso_command", required=True)
    s = isub.add_parser("schema", help="the EXPRESS schemas files are checked against")
    s.add_argument("action", choices=["list", "fetch", "add", "build"])
    s.add_argument("what", nargs="?", help="fetch: AP242, AP214, AP203 or AP203E2; add: a file")
    s.add_argument(
        "--part", action="append", help="build: PART=your copy (PDF or text), e.g. 42=p42.pdf"
    )
    s.add_argument("--cor", action="append", help="build: PART=a corrigendum, e.g. 108=cor1.pdf")
    s.add_argument("--exp", action="append", help="build: more EXPRESS to add")
    s.set_defaults(func=cmd_iso_schema)
    s = isub.add_parser("check", help="check a STEP file: header, schema, every rule")
    s.add_argument("file")
    s.set_defaults(func=cmd_iso_check)

    p = sub.add_parser("grammar", help="grammar notations")
    gsub = p.add_subparsers(dest="grammar_command", required=True)
    g = gsub.add_parser("import", help="convert a grammar to Lark (FR-K8)")
    g.add_argument("file")
    g.add_argument("--notation", choices=["wsn"], required=True)
    g.add_argument("--start", required=True, help="the production a sentence starts from")
    g.set_defaults(func=cmd_grammar_import)

    p = sub.add_parser("kernel", help="kernel authoring tools")
    ksub = p.add_subparsers(dest="kernel_command", required=True)
    k = ksub.add_parser("check", help="validate a kernel folder and run its examples")
    k.add_argument("dir")
    k.set_defaults(func=cmd_kernel_check)
    k = ksub.add_parser("describe", help="print a kernel's extensions and highlight classes")
    k.add_argument("kernel", help="kernel id or folder")
    k.set_defaults(func=cmd_kernel_describe)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except GcadError as exc:
        print(exc, file=sys.stderr)
        return 1
