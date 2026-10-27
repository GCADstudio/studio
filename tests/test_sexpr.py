import pytest

from gcad.core.sexpr import Kw, Num, SList, Str, Sym, read, to_json, to_text
from gcad.diagnostics import GcadError


def test_atoms_and_spans() -> None:
    forms = read('; comment\n(box :size [1 -2.5 3e2] "a\\"b")', "f.gcsg")
    assert len(forms) == 1
    call = forms[0]
    assert isinstance(call, SList) and call.bracket == "("
    head, kw, vec, text = call.items
    assert head == Sym("box", head.span)
    assert isinstance(kw, Kw) and kw.name == "size"
    assert isinstance(vec, SList) and vec.bracket == "["
    assert [i.value for i in vec.items if isinstance(i, Num)] == [1.0, -2.5, 300.0]
    assert isinstance(text, Str) and text.value == 'a"b'
    assert call.span is not None and (call.span.line, call.span.col) == (2, 1)
    assert head.span is not None and (head.span.line, head.span.col) == (2, 2)


def test_symbols_that_look_like_operators() -> None:
    forms = read("(- x) (+ 1 2) (<= a b) $fn hole-d")
    names = [f.items[0].name for f in forms[:3] if isinstance(f, SList)]  # type: ignore[union-attr]
    assert names == ["-", "+", "<="]
    assert isinstance(forms[3], Sym) and forms[3].name == "$fn"
    assert isinstance(forms[4], Sym) and forms[4].name == "hole-d"


@pytest.mark.parametrize(
    ("source", "message", "line", "col"),
    [
        ("(box 1", "never closed", 1, 1),
        ("(box 1]", "expected ')'", 1, 7),
        ("\n  )", "unexpected ')'", 2, 3),
        ('(echo "abc)', "string is never closed", 1, 7),
    ],
)
def test_errors_point_at_the_source(source: str, message: str, line: int, col: int) -> None:
    with pytest.raises(GcadError) as info:
        read(source, "f.gcsg")
    diag = info.value.diagnostic
    assert message in diag.message
    assert diag.span is not None and (diag.span.line, diag.span.col) == (line, col)


def test_canonical_text_round_trips() -> None:
    source = (
        '(difference (box [40 30 10]) (translate [20 15 -1] (cylinder :h 12 :r 4.5 :name "x")))'
    )
    forms = read(source)
    assert to_text(forms[0], width=1000) == source
    pretty = to_text(forms[0], width=20)
    assert "\n" in pretty
    assert to_text(read(pretty)[0], width=1000) == source
    assert to_json(forms[0])["list"][0] == {"sym": "difference"}  # type: ignore[index]
