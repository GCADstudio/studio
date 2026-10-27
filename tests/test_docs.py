"""The kernel built in docs/kernel-authoring.md must work exactly as the guide says."""

import re
from pathlib import Path

import pytest

from gcad.cli import main

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "kernel-authoring.md"


def guide_files() -> dict[str, str]:
    """Each '**`stack/<file>`**' heading followed by a fenced block."""
    text = GUIDE.read_text(encoding="utf-8")
    pattern = re.compile(r"\*\*`(stack/[\w.]+)`\*\*[^\n]*\n\n```\w*\n(.*?)```", re.S)
    return dict(pattern.findall(text))


def test_guide_kernel_builds_and_produces_the_documented_gcore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = guide_files()
    assert set(files) == {"stack/kernel.toml", "stack/grammar.lark", "stack/rewrite.toml"}
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    text = GUIDE.read_text(encoding="utf-8")
    sample = re.search(r"The language:.*?```\n(.*?)```", text, re.S)
    assert sample is not None
    (tmp_path / "stack" / "examples").mkdir()
    (tmp_path / "stack" / "examples" / "snowman.stack").write_text(sample.group(1))

    assert main(["kernel", "check", str(tmp_path / "stack")]) == 0
    capsys.readouterr()
    monkeypatch.setenv("GCAD_KERNEL_PATH", str(tmp_path))
    snowman = str(tmp_path / "stack" / "examples" / "snowman.stack")
    assert main(["eval", snowman, "--dump", "gcore"]) == 0
    documented = re.search(
        r"`--dump gcore` shows what your rules produced:\n\n```scheme\n(.*?)```", text, re.S
    )
    assert documented is not None
    assert capsys.readouterr().out == documented.group(1)
