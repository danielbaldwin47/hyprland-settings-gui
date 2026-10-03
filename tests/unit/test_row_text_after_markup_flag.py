"""A row's plain text is set after its markup flag, never beside it (#228).

Given to a constructor, `title` and `subtitle` are parsed as Pango markup before a
`use_markup=False` beside them, or a `set_use_markup(False)` after, lands: GTK logs a
warning for every `&` or `<` in user text (a `make && run` command, a `class a&b` rule),
and the UI tier's log gate fails the test that shows one. Only a test that feeds such text
trips the gate, so this scan holds the whole of `src/` to the pattern `shell/finder.py`
uses: the flag in the constructor, the texts set after it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
TEXTS = {"title", "subtitle"}


def texts_before_the_flag(source: str) -> list[int]:
    """Lines of each constructor that takes a `title` or `subtitle` its markup flag misses.

    That is a call passing `use_markup=False` beside the texts, or one whose result the same
    function turns markup off on afterwards (`row.set_use_markup(False)`).
    """
    tree = ast.parse(source)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and TEXTS & _keywords(node).keys():
            flag = _keywords(node).get("use_markup")
            if isinstance(flag, ast.Constant) and flag.value is False:
                lines.append(node.lineno)
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        turned_off = {
            ast.unparse(call.func.value)
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_use_markup"
            and [ast.unparse(arg) for arg in call.args] == ["False"]
        }
        lines.extend(
            assign.value.lineno
            for assign in ast.walk(function)
            if isinstance(assign, ast.Assign)
            and isinstance(assign.value, ast.Call)
            and TEXTS & _keywords(assign.value).keys()
            and ast.unparse(assign.targets[0]) in turned_off
        )
    return sorted(lines)


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}


def test_the_scan_finds_texts_given_before_the_flag() -> None:
    source = (
        'a = Adw.ActionRow(title="x", use_markup=False)\n'
        "b = Adw.ActionRow(use_markup=False)\n"
        'b.set_title("x")\n'
        'c = Adw.ExpanderRow(\n    subtitle="y",\n    use_markup=False,\n)\n'
        'd = Adw.ActionRow(title="z")\n'
        "def later():\n"
        '    e = Adw.ActionRow(subtitle="w")\n'
        "    e.set_use_markup(False)\n"
    )

    assert texts_before_the_flag(source) == [1, 4, 10]


def test_no_row_in_src_takes_its_texts_before_the_flag() -> None:
    offenders = [
        f"{path.relative_to(SRC.parent)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in texts_before_the_flag(path.read_text())
    ]

    assert offenders == [], (
        "pass use_markup=False alone and set the texts after (see ui/shell/finder.py)"
    )


MARKUP_BY_DEFAULT = {"Toast", "EntryRow"}
"""Widgets whose `title` libadwaita parses as markup unless told otherwise."""


def user_text_titles(source: str) -> list[int]:
    """Lines of an `Adw.Toast` or `Adw.EntryRow` built with a `title` that is not a literal.

    F7 of the #148 review: a preset name, a path or a typed key holding `&` or `<` rendered
    blank. A toast goes through `plain_toast`; an EntryRow sets its title after
    `use_markup=False`.
    """
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in MARKUP_BY_DEFAULT:
            continue
        title = _keywords(node).get("title")
        if title is not None and not isinstance(title, ast.Constant):
            lines.append(node.lineno)
    return lines


def test_no_toast_or_entry_row_takes_user_text_as_its_title() -> None:
    found = [
        f"{path.relative_to(SRC)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in user_text_titles(path.read_text())
    ]
    assert found == []


def test_the_scan_finds_a_toast_built_from_user_text() -> None:
    assert user_text_titles("Adw.Toast(title=f'Applied {name}')") == [1]
    assert user_text_titles("Adw.EntryRow(title=key)") == [1]
    assert user_text_titles("Adw.Toast(title='Saved')") == []
