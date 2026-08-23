"""The grammar against a real multi-file rice, as a snapshot.

`tests/fixtures/hyprlang/grammar` proves each rule in isolation; this proves the rules
survive contact with a config a human actually wrote. end-4 is the corpus rice that leans
hardest on the grammar -- 17 `.conf` files reached through relative `source =` lines, dozens
of variables, submaps spanning files, and `# hyprlang if` branches -- so a snapshot of its
keyword stream is the single best regression net the Importer has.

The rice is pinned by commit in `tests/corpus/corpus.lock.json`, so this snapshot only
moves when the grammar moves.

Regenerate deliberately, never reflexively::

    UPDATE_GOLDEN=1 pytest tests/unit/test_importer_corpus.py

Then read the diff. A rice whose stream changed shape is a rice that will convert
differently for every user running it.
"""

from __future__ import annotations

import pytest
from _golden import assert_matches_golden, render_keyword_stream
from _support import CORPUS_DIR, GOLDEN_DIR

from hyprtweaker.engine.importer import (
    Assignment,
    DiagnosticCode,
    Handler,
    ParseResult,
    SourceEnter,
    UnparsedLine,
    parse,
)

RICE = "end-4"
ENTRY = CORPUS_DIR / RICE / "hyprland.conf"

CORPUS_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_STATE_HOME": "/home/tester/.local/state",
    "XDG_CACHE_HOME": "/home/tester/.cache",
}
"""A synthetic environment. hyprlang seeds `$var` from `environ` and resolves
`# hyprlang if` against it, so parsing with the real one would make the snapshot depend on
who ran it -- and prototype #9 found `# hyprlang if` branches are baked in at conversion
time, which is exactly the machine-dependence this pins down."""

pytestmark = pytest.mark.skipif(
    not ENTRY.is_file(),
    reason=f"corpus rice {RICE} is not checked out (see tests/corpus/fetch.sh)",
)


@pytest.fixture(scope="module")
def result() -> ParseResult:
    return parse(ENTRY, env=CORPUS_ENV)


def test_the_rice_matches_its_snapshot(result: ParseResult) -> None:
    assert_matches_golden(
        render_keyword_stream(result, CORPUS_DIR / RICE),
        GOLDEN_DIR / "importer" / f"{RICE}.stream.txt",
        f"the keyword stream for {RICE}",
    )


def test_the_whole_source_tree_is_followed(result: ParseResult) -> None:
    """A `source =` the parser fails to follow is a silently half-imported config."""
    entered = [k.file for k in result.keywords if isinstance(k, SourceEnter)]
    assert len(entered) >= 17, f"only entered {len(entered)} files"
    assert len(set(entered)) == len(entered), "a file was entered twice"


def test_no_variable_the_rice_defines_is_left_unexpanded(result: ParseResult) -> None:
    """Every `$var` a rice defines must be substituted; a leftover is a parse miss.

    Only variables the rice *defines* are checked. A `$` that survives because it names
    something hyprlang never knew (a shell variable inside an `exec` string, an awk field)
    is correct behaviour, not a defect.
    """
    defined = set(result.variables)
    leftovers: list[str] = []
    for keyword in result.keywords:
        if not isinstance(keyword, Assignment | Handler):
            continue
        leftovers += [
            f"{keyword.origin}: ${name} in {keyword.value!r}"
            for name in defined
            if f"${name}" in keyword.value
        ]
    assert not leftovers, "unexpanded variables: " + "; ".join(leftovers[:5])


def test_every_rejected_line_is_still_in_the_stream(result: ParseResult) -> None:
    """Whatever the parser could not read, the Loss report must still be able to show."""
    for keyword in result.keywords:
        if not isinstance(keyword, UnparsedLine):
            continue
        assert keyword.text.strip(), "an unparsed line reached the stream with no text"
        assert keyword.origin.line > 0
        assert keyword.origin.file.is_file()


def test_the_rice_parses_without_structural_damage(result: ParseResult) -> None:
    """Category and directive nesting must balance -- these would corrupt every later key."""
    structural = {
        DiagnosticCode.UNCLOSED_CATEGORY,
        DiagnosticCode.STRAY_CATEGORY_CLOSE,
        DiagnosticCode.STRAY_ENDIF,
        DiagnosticCode.SOURCE_CYCLE,
        DiagnosticCode.VARIABLE_RECURSION,
        DiagnosticCode.TRAILING_BACKSLASH,
    }
    found = [d for d in result.diagnostics if d.code in structural]
    assert not found, [f"{d.origin}: {d.code.value}" for d in found]
