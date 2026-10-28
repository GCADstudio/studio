import json
import subprocess
import sys
from pathlib import Path

import pytest

from gcad.cli import main
from gcad.diagnostics import GcadError
from gcad.kernels import discover


def test_bundled_csg_kernel_is_discovered() -> None:
    kernels, _ = discover()
    assert ".gcsg" in kernels["csg"].extensions


def test_eval_dumps(examples: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", str(examples / "test_part.gcsg"), "--dump", "graph"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["nodes"][doc["root"]]["op"] == "difference"
    assert main(["eval", str(examples / "test_part.gcsg"), "--dump", "gcore"]) == 0
    assert "(difference" in capsys.readouterr().out


def test_errors_give_exit_code_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.gcsg"
    bad.write_text("(box [1 2)")
    assert main(["eval", str(bad)]) == 1
    assert "bad.gcsg:1:" in capsys.readouterr().err
    assert main(["eval", str(tmp_path / "x.unknown")]) == 1


def test_a_kernel_added_as_a_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "kernels" / "tiny"
    (folder / "examples").mkdir(parents=True)
    (folder / "kernel.toml").write_text(
        'id = "tiny"\nname = "Tiny"\nversion = "0.0.1"\nextensions = [".tiny"]\nparser = "gcore"\n'
    )
    (folder / "examples" / "one.tiny").write_text("(sphere 3)")
    broken = tmp_path / "kernels" / "broken"
    broken.mkdir()
    (broken / "kernel.toml").write_text('id = "broken"\n')
    monkeypatch.setenv("GCAD_KERNEL_PATH", str(tmp_path / "kernels"))
    kernels, warnings = discover()
    assert "tiny" in kernels
    assert any("missing" in w for w in warnings)
    assert main(["kernel", "check", str(folder)]) == 0
    assert main(["kernel", "check", str(broken)]) == 1


def _kernel(root: Path, id: str, extra: str) -> Path:
    folder = root / id
    (folder / "examples").mkdir(parents=True)
    (folder / "kernel.toml").write_text(
        f'id = "{id}"\nname = "{id}"\nversion = "1"\nparser = "gcore"\n{extra}\n'
    )
    return folder


def test_kernels_claim_exact_file_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gcad.kernels import kernel_for, load_kernel
    from gcad.server import Studio

    root = tmp_path / "kernels"
    named = _kernel(root, "named", 'filenames = ["Stackfile", "notes.txt"]')
    _kernel(root, "texts", 'extensions = [".txt"]')
    _kernel(root, "both", 'extensions = [".shp2"]\nfilenames = ["Twofile"]')
    (named / "examples" / "Stackfile").write_text("(sphere 2)")
    (named / "examples" / "Stackfile.orig").write_text("(sphere 2)")
    monkeypatch.setenv("GCAD_KERNEL_PATH", str(root))

    assert kernel_for(Path("case/Stackfile")).id == "named"
    # An exact file name outranks any extension that also fits.
    assert kernel_for(Path("notes.txt")).id == "named"
    assert kernel_for(Path("other.txt")).id == "texts"
    assert kernel_for(Path("Twofile")).id == "both"
    assert kernel_for(Path("part.shp2")).id == "both"
    for near_miss in ("Stackfile.orig", "myStackfile", "stackfile", "Stackfile2"):
        with pytest.raises(GcadError, match="no kernel handles"):
            kernel_for(Path(near_miss))

    kernel = load_kernel(named)
    assert kernel.extensions == () and kernel.filenames == ("Stackfile", "notes.txt")
    assert [p.name for p in kernel.examples()] == ["Stackfile"]
    assert kernel.describe()["filenames"] == ["Stackfile", "notes.txt"]
    assert main(["kernel", "check", str(named)]) == 0

    project = tmp_path / "project"
    (project / "case").mkdir(parents=True)
    for name in ("Stackfile", "Stackfile.orig", "myStackfile", "a.txt"):
        (project / "case" / name).write_text("(sphere 1)")
    listed = {f["name"]: f["kernel"] for f in Studio(project).rpc_file_list()["files"]}
    assert listed == {"Stackfile": "named", "a.txt": "texts"}


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("", "give extensions, filenames or both"),
        ('filenames = ["a/b"]', "filenames must be a list of file names"),
        ('filenames = "Stackfile"', "filenames must be a list of file names"),
        ('extensions = ["txt"]', "extensions must be a list"),
    ],
)
def test_bad_file_name_manifests_are_rejected(tmp_path: Path, extra: str, message: str) -> None:
    from gcad.kernels import load_kernel

    with pytest.raises(GcadError, match=message):
        load_kernel(_kernel(tmp_path, "bad", extra))


def test_module_entry_point_runs_headless(examples: Path, tmp_path: Path) -> None:
    out = tmp_path / "bracket.stl"
    proc = subprocess.run(
        [sys.executable, "-m", "gcad", "export", str(examples / "bracket.gcsg"), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "wrote" in proc.stdout and out.stat().st_size > 84
