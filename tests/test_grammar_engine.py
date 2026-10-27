"""The grammar-kernel engine: templates, placeholders, spans, errors and kernel check."""

from pathlib import Path

import pytest

from gcad.cli import main
from gcad.core.sexpr import to_text
from gcad.diagnostics import GcadError
from gcad.kernels import load_kernel

GRAMMAR = r"""
start: stmt*
?stmt: NAME "(" [args] ")" ";"        -> call
     | "let" NAME "=" NUMBER ";"      -> let
     | "skip" ";"                     -> skip
     | "quote" NAME ";"               -> quote
args: arg ("," arg)*
?arg: NUMBER
    | NAME
    | NAME "=" NUMBER                 -> named
NAME: /[a-z_]+/
NUMBER: /-?\d+(\.\d+)?/
%ignore /\s+/
"""

RULES = """
[rules]
start = "$*"
call = "($1 $2*)"
args = "$*"
named = ":$1 $2"
let = "(define $1 $2)"
skip = ""
quote = '(echo $1:str) (echo $1:sym)'

[tokens]
NUMBER = "number"
"""


def make_kernel(tmp_path: Path, grammar: str = GRAMMAR, rules: str = RULES) -> Path:
    folder = tmp_path / "toy"
    folder.mkdir(parents=True)
    (folder / "kernel.toml").write_text(
        'id = "toy"\nname = "Toy"\nversion = "1"\nextensions = [".toy"]\nparser = "lalr"\n'
    )
    (folder / "grammar.lark").write_text(grammar)
    (folder / "rewrite.toml").write_text(rules)
    return folder


def gcore(folder: Path, source: str) -> list[str]:
    return [to_text(f, 1000) for f in load_kernel(folder).to_gcore(source, "x.toy")]


def test_placeholders(tmp_path: Path) -> None:
    folder = make_kernel(tmp_path)
    out = gcore(folder, "let w = 4; skip; box(w, 2, center = 1); sphere(); quote abc;")
    assert out == [
        "(define w 4)",
        "(box w 2 :center 1)",
        "(sphere)",  # the optional [args] is absent, so $2* adds nothing
        '(echo "abc")',
        "(echo abc)",
    ]


def test_spans_point_into_the_source(tmp_path: Path) -> None:
    folder = make_kernel(tmp_path)
    forms = load_kernel(folder).to_gcore("skip;\n  box(1);", "x.toy")
    call = forms[0]
    assert call.span is not None and (call.span.line, call.span.col) == (2, 3)
    head = call.items[0]  # type: ignore[union-attr]
    assert head.span is not None and (head.span.line, head.span.col) == (2, 3)


@pytest.mark.parametrize(
    ("source", "message", "line", "col"),
    [
        ("box(1,);", "unexpected ')'", 1, 7),
        ("box(1)", "unexpected end of input", 1, 7),
        ("box(1); @", "unexpected character '@'", 1, 9),
    ],
)
def test_syntax_errors(tmp_path: Path, source: str, message: str, line: int, col: int) -> None:
    folder = make_kernel(tmp_path)
    with pytest.raises(GcadError) as info:
        gcore(folder, source)
    diag = info.value.diagnostic
    assert message in diag.message
    assert diag.span is not None and (diag.span.line, diag.span.col) == (line, col)


def test_check_finds_missing_and_unused_rules(tmp_path: Path) -> None:
    rules = RULES.replace('skip = ""\n', "").replace("[tokens]", 'stale = "x"\n\n[tokens]')
    folder = make_kernel(tmp_path, rules=rules)
    report = load_kernel(folder).grammar().check()
    assert report.errors == ["no rewrite rule for 'skip'"]
    assert report.warnings == ["rewrite rule 'stale' matches no grammar rule"]
    assert main(["kernel", "check", str(folder)]) == 1
    with pytest.raises(GcadError, match="no rewrite rule for 'skip'"):
        gcore(folder, "skip;")


def test_bad_grammar_and_template_are_reported(tmp_path: Path) -> None:
    folder = make_kernel(tmp_path, grammar="start: undefined_rule\n")
    with pytest.raises(GcadError, match="grammar.lark"):
        load_kernel(folder).grammar()
    folder2 = make_kernel(tmp_path / "b", rules=RULES.replace('"($1 $2*)"', '"($1 $2x)"'))
    with pytest.raises(GcadError, match="bad placeholder"):
        load_kernel(folder2).grammar()


def test_describe_lists_highlight_classes(tmp_path: Path) -> None:
    folder = make_kernel(tmp_path)
    info = load_kernel(folder).describe()["highlight"]
    assert info["keywords"] == ["let", "quote", "skip"]  # type: ignore[index]
    assert "(" in info["operators"]  # type: ignore[index]
    assert info["number_terminals"] == ["NUMBER"]  # type: ignore[index]
