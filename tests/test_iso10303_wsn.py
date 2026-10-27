"""The iso10303 kernel's hand-written grammar against the WSN of ISO 10303-21 itself (design note
§1.4, SPEC AC-21). The productions are read from the user's own copy of the standard, named by
GCAD_ISO10303_21 (the PDF, or pdftotext -layout output); they never enter the repository.

Both grammars must accept and reject the same sentences: random sentences generated from the WSN,
the kernel's fixtures and their single-character mutations. The WSN describes the exchange
structure without separators (5.6 adds them in prose) and without line breaks (annex A removes
them), so sentences are compared in that form.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
from pathlib import Path

import pytest
from lark import Lark
from lark.exceptions import LarkError

from gcad.diagnostics import GcadError
from gcad.kernels import BUNDLED, load_kernel
from gcad.kernels.wsn import parse

SOURCE = os.environ.get("GCAD_ISO10303_21", "")
pytestmark = pytest.mark.skipif(
    not Path(SOURCE).is_file(),
    reason="set GCAD_ISO10303_21 to your copy of ISO 10303-21:2002 (PDF or text)",
)
KERNEL = load_kernel(BUNDLED / "iso10303").grammar()
FIXTURES = sorted((BUNDLED / "iso10303/examples").glob("*.stp")) + sorted(
    (Path(__file__).parent / "iso10303").glob("*.stp")
)
# Each table runs from its caption (the second time it appears: the first is the contents
# list) to the text that follows it.
TABLES = [
    ("Table 1 - WSN defining subsets of the basic alphabet", "5.3 Exchange structure"),
    ("Table 2 - WSN of token definitions", "5.6 Token separators"),
    ("Table 3 - WSN of the exchange structure", "6 Tokens"),
    ("Table 4 - String control directives", "Additional characters"),
]


def standard_text() -> str:
    if SOURCE.lower().endswith(".pdf"):
        return subprocess.run(
            ["pdftotext", "-layout", SOURCE, "-"], check=True, capture_output=True, text=True
        ).stdout
    return Path(SOURCE).read_text(encoding="utf-8", errors="replace")


def productions(text: str) -> str:
    """The WSN lines of Tables 1 to 4: page headers, footers, watermarks and prose dropped
    (a production line has no small letters outside its literals)."""
    out = []
    for caption, end in TABLES:
        start = text.index(caption, text.index(caption) + 1) + len(caption)
        for line in text[start : text.index(end, start)].splitlines():
            line = re.sub(r"(?<!\S)[`,\-]*`[`,\-]*(?!\S)", "", line)  # the copy's watermark
            bare = re.sub(r'"(?:[^"]|"")*"', "", line)
            if not line.strip() or "©" in line or re.search(r"[a-z]", bare):
                continue
            if re.fullmatch(r"\s*(ISO 10303-21:2002\(E\)|\d+)\s*", line):
                continue
            out.append(line.strip())
    return "\n".join(out)


def flatten(text: str) -> str:
    """A file as the WSN sees it: no comments, no line breaks, no spaces outside strings."""
    text = text.replace("\r", "").replace("\n", "")
    parts = re.split(r"('(?:[^']|'')*'|/\*.*?\*/)", text)
    out = []
    for p in parts:
        if p.startswith("/*"):
            continue
        out.append(p if p.startswith("'") else re.sub(r"[ \t]+|\\[NF]\\", "", p))
    return "".join(out)


@pytest.fixture(scope="module")
def wsn() -> tuple[Lark, object]:
    grammar = parse(productions(standard_text()))
    assert grammar.notes == [  # design note §1.6, numbers 15 and 16
        "production CHARACTER has no closing full stop",
        "production DATA_SECTION has no closing full stop",
    ]
    return Lark(grammar.to_lark("EXCHANGE_FILE"), parser="earley", lexer="dynamic"), grammar


def accepts_wsn(parser: Lark, text: str) -> bool:
    try:
        parser.parse(text)
    except LarkError:
        return False
    return True


def accepts_kernel(text: str) -> bool:
    try:
        KERNEL.to_gcore(text, "t.stp")
    except GcadError:
        return False
    return True


def test_fixtures_are_sentences_of_the_wsn(wsn: tuple[Lark, object]) -> None:
    parser, _ = wsn
    for path in FIXTURES:
        text = flatten(path.read_text(encoding="latin-1"))
        assert accepts_kernel(text), path.name
        assert accepts_wsn(parser, text), path.name


def test_random_wsn_sentences_are_accepted_by_the_kernel(wsn: tuple[Lark, object]) -> None:
    parser, grammar = wsn
    rng = random.Random(10303)
    for _ in range(300):
        sentence = grammar.sample("EXCHANGE_FILE", rng, depth=9)  # type: ignore[attr-defined]
        assert accepts_wsn(parser, sentence)
        assert accepts_kernel(sentence), sentence


def test_mutations_are_judged_alike(wsn: tuple[Lark, object]) -> None:
    """One character changed, added or dropped, within the basic alphabet: the two grammars
    agree on every mutant."""
    parser, _ = wsn
    rng = random.Random(21)
    alphabet = "".join(chr(c) for c in range(32, 127))
    small = [flatten(p.read_text(encoding="latin-1")) for p in FIXTURES]
    small = [s for s in small if len(s) < 3000]
    disagree = []
    for _ in range(400):
        text = rng.choice(small)
        k = rng.randrange(len(text))
        kind = rng.choice(["change", "insert", "drop"])
        c = rng.choice(alphabet)
        mutant = (
            text[:k] + c + text[k + 1 :]
            if kind == "change"
            else text[:k] + c + text[k:]
            if kind == "insert"
            else text[:k] + text[k + 1 :]
        )
        if accepts_wsn(parser, mutant) != accepts_kernel(mutant):
            disagree.append(mutant[max(0, k - 30) : k + 30])
    assert disagree == []
