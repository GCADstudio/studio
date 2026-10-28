"""Process kernels (SPEC §7.2, FR-K7): a language served by an external program.

The program is started once and kept running between evaluations; each evaluation gets a
fresh working folder. It is spoken to in JSON-RPC 2.0, one JSON object per line on its stdin
and stdout (stderr is kept for error reports). The exchange is:

    initialize {protocol, client}                  -> {name, version}
    evaluate   {source, file, folder, workdir, params}
               -> {diagnostics, graph | step_path | mesh_path}
    shutdown   {}                                  -> null, then the program exits

`graph` is the canonical geometry graph (`gcad eval --dump graph`); `step_path` names a STEP
file and `mesh_path` an STL, OFF or 3MF file, written inside `workdir`. Lengths are in mm. A
diagnostic is {severity, message, line?, col?, end_line?, end_col?}, lines and columns
1-based. The program may send `log {message, severity?}` notifications while it works.

Nothing here knows any particular language: every kernel is a folder with a manifest that
names its program (gcad.kernels). The FR-C6 wall-time limit applies to each `evaluate` and is
enforced by ending the program and everything it started; a newer evaluation in the studio
ends it the same way. Starting the program has its own, longer allowance.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from gcad import __version__
from gcad.core.evaluator import EvalResult, Limits
from gcad.core.graph import ArgValue, Graph, graph_from_json, make_node
from gcad.diagnostics import Diagnostic, GcadError, Severity, Span
from gcad.importers import MESH_FORMATS, read_mesh
from gcad.kernels import Kernel, is_trusted

PROTOCOL = 1
STARTUP_SECONDS = 60.0  # starting a program and `initialize`; not part of the FR-C6 limit
SHUTDOWN_GRACE = 2.0  # seconds a program gets to exit after `shutdown`
_evaluation_lock = threading.Lock()  # one evaluation at a time across all kernel programs
_FIELD = re.compile(r"\{(python|kernel|env:[A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_command(kernel: Kernel) -> list[str]:
    """The manifest's command with {python}, {kernel} and {env:NAME} filled in."""
    setup = f" {kernel.setup}" if kernel.setup else ""

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "python":
            return sys.executable
        if key == "kernel":
            return str(kernel.path.resolve())
        name = key[4:]
        value = os.environ.get(name)
        if not value:
            raise GcadError(f"kernel '{kernel.id}' needs the environment variable {name}.{setup}")
        return value

    parts = [_FIELD.sub(fill, part) for part in kernel.command]
    program = parts[0]
    if not Path(program).is_file():
        found = shutil.which(program)
        if found is None:
            raise GcadError(f"kernel '{kernel.id}' cannot find the program '{program}'.{setup}")
        parts[0] = found
    return parts


def unavailable_reason(kernel: Kernel) -> str | None:
    """Why the kernel's program cannot be started here, or None."""
    if sys.platform == "emscripten":
        return "runs a program on your computer; install GCAD Studio locally to use it"
    try:
        resolve_command(kernel)
    except GcadError as exc:
        return exc.diagnostic.message
    return None


class _Ended(Exception):
    """The exchange stopped early; the message says why."""


class _Program:
    """One running kernel program and the JSON-RPC exchange with it."""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.home = Path(tempfile.mkdtemp(prefix="gcad-kernel-"))
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True  # one process group, ended as a whole
        try:
            self.proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.home,
                env=env,
                **options,
            )
        except OSError:
            shutil.rmtree(self.home, ignore_errors=True)
            raise
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr = bytearray()
        self.logs: list[Diagnostic] = []
        self.next_id = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.extend(line)
            del self.stderr[: max(0, len(self.stderr) - 8192)]  # keep the tail

    def stderr_tail(self) -> str:
        text = self.stderr.decode("utf-8", errors="replace").strip()
        return text[-1000:]

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def call(
        self,
        method: str,
        params: dict[str, object],
        seconds: float,
        stop: threading.Event | None = None,
        what: str = "evaluation",
    ) -> Any:
        """Send one request and wait up to `seconds` for its answer."""
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(request).encode() + b"\n")
            self.proc.stdin.flush()
        except OSError:
            raise _Ended(self._exit_message()) from None
        end = time.monotonic() + seconds
        while True:
            if stop is not None and stop.is_set():
                raise _Ended("evaluation stopped: a newer one replaced it")
            left = end - time.monotonic()
            if left <= 0:
                raise _Ended(f"{what} exceeded the time limit of {seconds:g} s")
            try:
                line = self.lines.get(timeout=min(left, 0.05))
            except queue.Empty:
                continue
            if line is None:
                raise _Ended(self._exit_message())
            message = self._parse(line)
            if message is None:
                continue
            if message.get("id") == self.next_id:
                if "error" in message:
                    error = message["error"]
                    text = error.get("message") if isinstance(error, dict) else error
                    raise GcadError(f"the kernel program reported: {text}")
                return message.get("result")

    def _parse(self, line: bytes) -> dict[str, Any] | None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            message = json.loads(text)
        except ValueError:
            message = None
        if not isinstance(message, dict):  # stray output: keep it as a log line
            self.logs.append(Diagnostic(Severity.INFO, text[:500]))
            return None
        if message.get("method") == "log":
            params = message.get("params") or {}
            self.logs.append(_diagnostic(params, None) or Diagnostic(Severity.INFO, str(params)))
            return None
        return message

    def _exit_message(self) -> str:
        try:
            code = self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            code = None
        tail = self.stderr_tail()
        detail = f": {tail}" if tail else ""
        return f"the kernel program stopped (exit code {code}){detail}"

    def end(self) -> None:
        """Ask the program to shut down; end it and its children if it does not."""
        if self.alive:
            try:
                self.call("shutdown", {}, SHUTDOWN_GRACE, what="shutdown")
                self.proc.wait(timeout=SHUTDOWN_GRACE)
            except (_Ended, GcadError, OSError, subprocess.TimeoutExpired):
                pass
        self.kill()

    def kill(self) -> None:
        if self.proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        shutil.rmtree(self.home, ignore_errors=True)


# Programs stay running between evaluations, one per kernel folder and command, because
# starting some languages costs seconds (importing CadQuery takes 2 to 6 s). A program that
# times out, is stopped or fails is ended and started afresh next time.
_programs: dict[tuple[str, tuple[str, ...]], _Program] = {}
_programs_lock = threading.Lock()


def _program_for(kernel: Kernel, command: list[str], stop: threading.Event | None) -> _Program:
    key = (str(kernel.path.resolve()), tuple(command))
    with _programs_lock:
        program = _programs.get(key)
        if program is not None and program.alive:
            return program
        _programs.pop(key, None)
    program = _Program(command)
    try:
        hello = {"protocol": PROTOCOL, "client": f"GCAD Studio {__version__}"}
        program.call("initialize", hello, STARTUP_SECONDS, stop, what="starting the kernel")
    except BaseException:
        program.kill()
        raise
    with _programs_lock:
        _programs[key] = program
    return program


def _forget(program: _Program) -> None:
    with _programs_lock:
        for key, value in list(_programs.items()):
            if value is program:
                del _programs[key]
    program.kill()


@atexit.register
def end_all() -> None:
    """Shut every kernel program down (at exit, and in tests)."""
    with _programs_lock:
        programs = list(_programs.values())
        _programs.clear()
    for program in programs:
        program.end()


def _offsets(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _span(raw: dict[str, Any], source: str | None, file: str) -> Span | None:
    line = raw.get("line")
    if source is None or not isinstance(line, int) or line < 1:
        return None
    starts = _offsets(source)
    line = min(line, len(starts))
    line_end = starts[line] - 1 if line < len(starts) else len(source)
    raw_col = raw.get("col")
    col = raw_col if isinstance(raw_col, int) and raw_col >= 1 else 1
    start = min(starts[line - 1] + col - 1, line_end)
    end = line_end
    end_line, end_col = raw.get("end_line"), raw.get("end_col")
    if isinstance(end_line, int) and end_line >= line and isinstance(end_col, int):
        end_line = min(end_line, len(starts))
        end = min(starts[end_line - 1] + end_col - 1, len(source))
    return Span(file, line, col, start, max(end, start))


def _diagnostic(raw: object, source: str | None, file: str = "") -> Diagnostic | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("message"), str):
        return None
    try:
        severity = Severity(raw.get("severity", "info"))
    except ValueError:
        severity = Severity.INFO
    return Diagnostic(severity, raw["message"], _span(raw, source, file))


def _inside(workdir: Path, name: object) -> Path:
    if not isinstance(name, str):
        raise GcadError("the kernel program named an output file that is not a string")
    path = (workdir / name).resolve()
    if not path.is_relative_to(workdir.resolve()) or not path.is_file():
        raise GcadError(f"the kernel program's output {name} is not a file in its working folder")
    return path


def _geometry(result: dict[str, Any], workdir: Path) -> Graph | None:
    if result.get("graph") is not None:
        doc = result["graph"]
        if not isinstance(doc, dict):
            raise GcadError("the kernel program's graph is not a JSON object")
        try:
            return graph_from_json(doc)
        except ValueError as exc:
            raise GcadError(f"the kernel program's graph is not valid: {exc}") from None
    if result.get("step_path") is not None:
        return _imported("step", _inside(workdir, result["step_path"]).read_bytes())
    if result.get("mesh_path") is not None:
        path = _inside(workdir, result["mesh_path"])
        fmt = str(result.get("mesh_format") or path.suffix.lstrip(".")).lower()
        if fmt not in MESH_FORMATS:
            raise GcadError(f"the kernel program wrote a '{fmt}' mesh; readable: {MESH_FORMATS}")
        data = path.read_bytes()
        vertices, tris = read_mesh(data, fmt)
        if not len(tris):
            raise GcadError("the kernel program's mesh is empty")
        bounds = tuple(float(v) for v in (*vertices.min(axis=0), *vertices.max(axis=0)))
        return _imported(fmt, data, bounds)
    return None


def _imported(fmt: str, data: bytes, bounds: tuple[float, ...] | None = None) -> Graph:
    """One `import` node; the graph carries the bytes under their SHA-256, as for a source
    that is itself geometry (core.builtins import_source)."""
    digest = hashlib.sha256(data).hexdigest()
    args: dict[str, ArgValue] = {"format": fmt, "data": digest, "entity": 0.0}
    if bounds is not None:
        args["bounds"] = bounds
    return Graph(make_node("import", args), blobs={digest: data})


def evaluate_process(
    kernel: Kernel,
    source: str,
    file: str,
    limits: Limits | None = None,
    folder: Path | None = None,
) -> EvalResult:
    """Run the kernel's program on `source`. `folder` is where the source file lives, so a
    language can find the files it includes; it defaults to the folder of `file`."""
    limits = limits or Limits()
    if not is_trusted(kernel):
        message = (
            f"kernel '{kernel.id}' runs a program on your computer and its folder is not "
            f"trusted yet: {kernel.path} (trust it with 'gcad kernel trust')"
        )
        return EvalResult(None, [Diagnostic(Severity.ERROR, message)])
    try:
        command = resolve_command(kernel)
    except GcadError as exc:
        return EvalResult(None, [exc.diagnostic])
    if folder is None:
        parent = Path(file).parent
        folder = parent.resolve() if str(parent) not in ("", ".") and parent.is_dir() else None
    diagnostics: list[Diagnostic] = []
    graph: Graph | None = None
    with _evaluation_lock:
        try:
            program = _program_for(kernel, command, limits.stop)
        except OSError as exc:
            message = f"kernel '{kernel.id}' could not start {command[0]}: {exc.strerror}"
            return EvalResult(None, [Diagnostic(Severity.ERROR, message)])
        except _Ended as exc:
            return EvalResult(None, [Diagnostic(Severity.ERROR, str(exc))])
        except GcadError as exc:
            return EvalResult(None, [exc.diagnostic])
        program.logs.clear()
        workdir = Path(tempfile.mkdtemp(prefix="run-", dir=program.home))
        try:
            params: dict[str, object] = {
                "source": source,
                "file": file,
                "folder": str(folder) if folder else None,
                "workdir": str(workdir),
                "params": {},
            }
            result = program.call("evaluate", params, limits.max_seconds, limits.stop)
            if not isinstance(result, dict):
                raise GcadError("the kernel program's evaluate result is not an object")
            for raw in result.get("diagnostics") or []:
                d = _diagnostic(raw, source, file)
                if d is not None:
                    diagnostics.append(d)
            if not any(d.severity is Severity.ERROR for d in diagnostics):
                graph = _geometry(result, workdir)
                if graph is None:
                    diagnostics.append(
                        Diagnostic(Severity.ERROR, "the kernel program returned no geometry")
                    )
        except _Ended as exc:
            _forget(program)  # busy or gone: end it; the next evaluation starts a new one
            diagnostics.append(Diagnostic(Severity.ERROR, str(exc)))
        except GcadError as exc:
            diagnostics.append(exc.diagnostic)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        diagnostics = program.logs + diagnostics
    if graph is not None:
        graph.metadata = {"title": Path(file).stem, "source": Path(file).name, "kernel": kernel.id}
    return EvalResult(graph, diagnostics)
