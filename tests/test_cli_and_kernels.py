import json
import subprocess
import sys
from pathlib import Path

import pytest

from gcad.cli import main
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


def test_module_entry_point_runs_headless(examples: Path, tmp_path: Path) -> None:
    out = tmp_path / "bracket.stl"
    proc = subprocess.run(
        [sys.executable, "-m", "gcad", "export", str(examples / "bracket.gcsg"), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "wrote" in proc.stdout and out.stat().st_size > 84
