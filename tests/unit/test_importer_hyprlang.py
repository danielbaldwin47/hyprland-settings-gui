"""The hyprlang grammar, one construct at a time.

Every test here names a rule from research #4 §1 and pins the behaviour that rule implies.
The rice corpus cannot do this job: real configs exercise maybe half the grammar, always in
combination, so a corpus test tells you *that* something changed and never *which* rule.
These are the synthetic fixtures that tell you which rule.

The last test is a golden over a fixture tree that uses every construct at once, which is
what catches an interaction two isolated rules were each happy with.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _golden import assert_matches_golden, render_keyword_stream
from _support import FIXTURE_DIR, GOLDEN_DIR

from hyprtweaker.engine.importer import (
    Assignment,
    DiagnosticCode,
    Handler,
    ParseResult,
    SpecialCategory,
    UnparsedLine,
    VariableDefinition,
    parse,
)

GRAMMAR_TREE = FIXTURE_DIR / "hyprlang" / "grammar"

FIXTURE_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "MY_FLAG": "1",
}
"""A fixed environment: hyprlang seeds `$var` from `environ` and resolves `# hyprlang if`
against it, so parsing with the real environment would make every golden machine-specific.
"""


def parse_text(tmp_path: Path, text: str, **kwargs: object) -> ParseResult:
    """Parse one inline config, with the fixed environment unless told otherwise."""
    entry = tmp_path / "hyprland.conf"
    entry.write_text(text)
    env = kwargs.pop("env", FIXTURE_ENV)
    return parse(entry, env=env, **kwargs)  # type: ignore[arg-type]


def assignments(result: ParseResult) -> dict[str, str]:
    return {k.key: k.value for k in result.keywords if isinstance(k, Assignment)}


def handlers(result: ParseResult) -> list[Handler]:
    return [k for k in result.keywords if isinstance(k, Handler)]


def unparsed(result: ParseResult) -> list[UnparsedLine]:
    return [k for k in result.keywords if isinstance(k, UnparsedLine)]


def only_special(result: ParseResult) -> SpecialCategory:
    """The one special-category record a fixture produced."""
    return next(k for k in result.keywords if isinstance(k, SpecialCategory))


def first_assignment(result: ParseResult) -> Assignment:
    return next(k for k in result.keywords if isinstance(k, Assignment))


def definitions(result: ParseResult) -> list[VariableDefinition]:
    return [k for k in result.keywords if isinstance(k, VariableDefinition)]


def codes(result: ParseResult) -> list[DiagnosticCode]:
    return [d.code for d in result.diagnostics]


class TestLineStructure:
    def test_a_trailing_backslash_joins_the_next_line_verbatim(self, tmp_path: Path) -> None:
        """Leading whitespace of the continuation is kept; no separator is inserted."""
        result = parse_text(tmp_path, "exec-once = one   \\\n    two\n")
        assert handlers(result)[0].value == "one    two"

    def test_a_continued_line_reports_the_line_it_started_on(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "# lead\nexec-once = one \\\ntwo\n")
        assert handlers(result)[0].origin.line == 2

    def test_a_file_ending_mid_continuation_is_an_error(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "exec-once = one \\\n")
        assert DiagnosticCode.TRAILING_BACKSLASH in codes(result)

    def test_the_first_equals_splits_and_later_ones_stay_in_the_value(
        self, tmp_path: Path
    ) -> None:
        result = parse_text(tmp_path, "bind = SUPER, equal, exec, echo a=b=c\n")
        assert handlers(result)[0].value == "SUPER, equal, exec, echo a=b=c"

    def test_a_bare_hash_truncates_and_a_double_hash_escapes(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "general {\n  a = keep ## literal\n  b = keep # drop\n}\n",
        )
        assert assignments(result) == {"general:a": "keep # literal", "general:b": "keep"}

    def test_a_line_with_an_equals_is_never_a_category_open(self, tmp_path: Path) -> None:
        """`key = value {` is a k=v line -- the brace check only applies without an `=`."""
        result = parse_text(tmp_path, "misc:logo = true {\n")
        assert assignments(result) == {"misc:logo": "true {"}

    def test_an_empty_left_hand_side_is_preserved_and_reported(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "= orphaned value\n")
        assert [u.code for u in unparsed(result)] == [DiagnosticCode.EMPTY_LHS]
        assert unparsed(result)[0].text.strip() == "= orphaned value"

    def test_a_line_with_no_equals_brace_or_close_is_preserved(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "just some words\n")
        assert [u.code for u in unparsed(result)] == [DiagnosticCode.INVALID_LINE]
        assert unparsed(result)[0].origin.line == 1

    def test_a_stray_close_is_reported(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "}\n")
        assert DiagnosticCode.STRAY_CATEGORY_CLOSE in codes(result)

    def test_a_category_left_open_at_end_of_file_is_reported(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "general {\n  border_size = 2\n")
        assert DiagnosticCode.UNCLOSED_CATEGORY in codes(result)
        assert assignments(result) == {"general:border_size": "2"}


class TestVariables:
    def test_a_definition_is_recorded_and_applied(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "$mod = SUPER\nbind = $mod, Q, exec, kitty\n")
        assert [(d.name, d.value) for d in definitions(result)] == [("mod", "SUPER")]
        assert handlers(result)[0].value == "SUPER, Q, exec, kitty"

    def test_the_longest_name_wins_and_no_delimiter_is_needed(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "$col = ff\n$colour = 00ff99\ngeneral {\n  c = $colourAND$col\n}\n",
        )
        assert assignments(result)["general:c"] == "00ff99ANDff"

    def test_a_variables_value_may_itself_contain_a_variable(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "$mod = SUPER\n$modAlt = $mod ALT\ngeneral {\n  a = $modAlt\n}\n",
        )
        assert assignments(result)["general:a"] == "SUPER ALT"

    def test_a_definition_captures_its_value_eagerly(self, tmp_path: Path) -> None:
        """A later redefinition of `$a` does not reach through a `$b` that referenced it.

        Verified against libhyprlang 0.6.8 directly: this config leaves the option at 1,
        so definitions are expanded at the point of definition, not at the point of use.
        """
        result = parse_text(
            tmp_path,
            "$a = 1\n$b = $a\n$a = 2\ngeneral {\n  border_size = $b\n}\n",
        )
        assert assignments(result) == {"general:border_size": "1"}

    def test_arithmetic_runs_on_a_definition_line(self, tmp_path: Path) -> None:
        """`$d = {{ g * 2 }}` stores `10` -- only unescaping is skipped for `$VAR =`."""
        result = parse_text(
            tmp_path, "$g = 5\n$d = {{ g * 2 }}\ngeneral {\n  border_size = $d\n}\n"
        )
        assert assignments(result) == {"general:border_size": "10"}

    def test_an_undefined_variable_stays_literal(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "general {\n  a = $NOPE\n}\n")
        assert assignments(result)["general:a"] == "$NOPE"
        assert not result.errors

    def test_the_environment_seeds_the_variable_list(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "general {\n  a = $HOME/x\n}\n")
        assert assignments(result)["general:a"] == "/home/tester/x"

    def test_the_left_hand_side_is_expanded_too(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "$sub = snap\ngeneral:$sub:enabled = true\n")
        assert assignments(result) == {"general:snap:enabled": "true"}

    def test_a_dollar_in_first_position_is_a_definition_even_with_a_colon(
        self, tmp_path: Path
    ) -> None:
        """`$NAME = value` is decided by the first character, before any expansion.

        Research #4 is ambiguous here: §1.2 keys the definition off `LHS[0] == '$'`, while
        §1.10 suggests `$cat:key = v` addresses a category. The prototype took §1.2 and
        converted all seven corpus rices correctly, so §1.2 is what this pins -- and no
        rice in `tests/corpus` uses the `$cat:key` spelling at all, so nothing observable
        rides on it.
        """
        result = parse_text(tmp_path, "$cat = general\n$cat:border_size = 2\n")
        assert [d.name for d in definitions(result)] == ["cat", "cat:border_size"]
        assert not assignments(result)

    def test_unbounded_recursion_stops_at_the_iteration_limit(self, tmp_path: Path) -> None:
        """A variable whose value re-introduces its own token would loop forever."""
        result = parse_text(tmp_path, "$a = $a$a\ngeneral {\n  x = $a\n}\n")
        assert DiagnosticCode.VARIABLE_RECURSION in codes(result)

    def test_escapes_are_applied_to_values_but_not_to_variable_definitions(
        self, tmp_path: Path
    ) -> None:
        result = parse_text(tmp_path, "$raw = a\\{b\ngeneral {\n  x = a\\{b\n}\n")
        assert definitions(result)[0].value == "a\\{b"
        assert assignments(result)["general:x"] == "a{b"


class TestArithmetic:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("{{ 2 + 3 }}", "5"),
            ("{{ 10 - 4 }}", "6"),
            ("{{ gap * 2 }}", "10"),
            ("{{ 10 / 4 }}", "2.5"),
        ],
    )
    def test_the_four_operators(self, tmp_path: Path, expression: str, expected: str) -> None:
        result = parse_text(tmp_path, f"$gap = 5\ngeneral {{\n  x = {expression}\n}}\n")
        assert assignments(result)["general:x"] == expected

    def test_results_are_formatted_as_a_c_float_not_a_python_double(
        self, tmp_path: Path
    ) -> None:
        """`std::format("{}", 0.1f + 0.2f)` is `0.3`, not `0.30000000000000004`."""
        result = parse_text(tmp_path, "general {\n  x = {{ 0.1 + 0.2 }}\n}\n")
        assert assignments(result)["general:x"] == "0.3"

    def test_operands_are_variable_names_without_the_dollar(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "$a = 7\n$b = 3\ngeneral {\n  x = {{ a - b }}\n}\n")
        assert assignments(result)["general:x"] == "4"

    def test_an_escaped_double_brace_is_left_alone(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "$x = literal \\{{ 1 + 1 }}\n")
        assert definitions(result)[0].value == "literal \\{{ 1 + 1 }}"

    @pytest.mark.parametrize(
        "expression",
        ["{{ 1 + }}", "{{ 1 ^ 2 }}", "{{ one + two }}", "{{ 1 / 0 }}"],
    )
    def test_a_bad_expression_is_reported_and_the_text_is_left_intact(
        self, tmp_path: Path, expression: str
    ) -> None:
        result = parse_text(tmp_path, f"general {{\n  x = {expression}\n}}\n")
        assert DiagnosticCode.BAD_EXPRESSION in codes(result)
        assert assignments(result)["general:x"] == expression


class TestCategories:
    def test_nesting_joins_with_colons(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "general {\n  snap {\n    enabled = true\n  }\n}\n")
        assert assignments(result) == {"general:snap:enabled": "true"}

    def test_the_nested_and_inline_spellings_are_the_same_key(self, tmp_path: Path) -> None:
        nested = parse_text(tmp_path, "general {\n  border_size = 2\n}\n")
        inline = parse_text(tmp_path, "general:border_size = 2\n")
        assert assignments(nested) == assignments(inline)

    def test_a_category_name_may_touch_its_brace(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "general{\n  border_size = 2\n}\n")
        assert assignments(result) == {"general:border_size": "2"}

    def test_a_handler_still_fires_inside_a_category(self, tmp_path: Path) -> None:
        """Handlers match on the left-hand side alone, ignoring the category stack."""
        result = parse_text(tmp_path, "general {\n  bind = SUPER, Q, exec, kitty\n}\n")
        assert handlers(result)[0].name == "bind"
        assert not assignments(result)

    def test_a_bare_top_level_key_is_flagged_as_an_orphan(self, tmp_path: Path) -> None:
        """hyprlang rejects it as "config option does not exist" and applies nothing."""
        result = parse_text(tmp_path, "workspace_swipe = true\n")
        entry = first_assignment(result)
        assert entry.orphan
        assert DiagnosticCode.ORPHAN_KEY in codes(result)

    def test_a_dotted_top_level_key_is_not_an_orphan(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "input:touchpad:natural_scroll = true\n")
        entry = first_assignment(result)
        assert not entry.orphan


class TestSpecialCategories:
    def test_a_keyed_block_carries_its_key_and_fields(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "device {\n  name = mouse-1\n  sensitivity = -0.5\n}\n")
        block = only_special(result)
        assert (block.category, block.key_field, block.key_value) == (
            "device",
            "name",
            "mouse-1",
        )
        assert [(f.key, f.value) for f in block.fields] == [
            ("name", "mouse-1"),
            ("sensitivity", "-0.5"),
        ]

    def test_the_key_must_be_the_first_field_but_the_block_survives(
        self, tmp_path: Path
    ) -> None:
        result = parse_text(tmp_path, "device {\n  sensitivity = 0.1\n  name = mouse-2\n}\n")
        assert DiagnosticCode.SPECIAL_KEY_NOT_FIRST in codes(result)
        block = only_special(result)
        assert block.key_value == "mouse-2"

    def test_the_inline_keyed_form_is_one_field(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "device[mouse-3]:sensitivity = 0.3\n")
        block = only_special(result)
        assert block.inline and block.key_value == "mouse-3"
        assert [(f.key, f.value) for f in block.fields] == [("sensitivity", "0.3")]

    def test_monitorv2_is_keyed_on_output(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "monitorv2 {\n  output = DP-1\n  mode = 1x1\n}\n")
        block = only_special(result)
        assert (block.key_field, block.key_value) == ("output", "DP-1")

    def test_plugin_is_static_and_nests(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "plugin {\n  hyprbars {\n    bar_height = 20\n  }\n}\n")
        block = only_special(result)
        assert block.key_field is None
        assert [(f.key, f.value) for f in block.fields] == [("hyprbars:bar_height", "20")]

    def test_a_block_nested_in_any_special_category_folds_into_the_field_path(
        self, tmp_path: Path
    ) -> None:
        """Not just `plugin`: hyprlang folds a nested block into the field path, and the
        special block survives the inner `}`.

        Verified against libhyprlang 0.6.8, which reports the field as
        `device:nested:k` and still applies `sensitivity` to `device[m]` afterwards.
        """
        result = parse_text(
            tmp_path,
            "device {\n"
            "  name = m\n"
            "  nested {\n    k = 1\n  }\n"
            "  sensitivity = 0.5\n"
            "}\n"
            "misc:after = 3\n",
        )
        block = only_special(result)
        assert [(f.key, f.value) for f in block.fields] == [
            ("name", "m"),
            ("nested:k", "1"),
            ("sensitivity", "0.5"),
        ]
        assert assignments(result) == {"misc:after": "3"}

    def test_a_special_category_name_matches_verbatim(self, tmp_path: Path) -> None:
        """`Device { }` is a plain category, not the `device` special category.

        Verified against libhyprlang 0.6.8, which rejects it with
        "config option <Device:name> does not exist".
        """
        result = parse_text(tmp_path, "Device {\n  name = m\n  sensitivity = 0.5\n}\n")
        assert not [k for k in result.keywords if isinstance(k, SpecialCategory)]
        assert assignments(result) == {"Device:name": "m", "Device:sensitivity": "0.5"}

    def test_windowrule_is_both_a_handler_and_a_keyed_category(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "windowrule = float, class:^(x)$\nwindowrule {\n  name = r1\n  float = true\n}\n",
        )
        assert [h.name for h in handlers(result)] == ["windowrule"]
        assert [k.category for k in result.keywords if isinstance(k, SpecialCategory)] == [
            "windowrule"
        ]


class TestHandlers:
    def test_exact_handlers_match_by_name(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "exec-once = waybar\nsubmap = clean\n")
        assert [(h.name, h.flags) for h in handlers(result)] == [
            ("exec-once", ""),
            ("submap", ""),
        ]

    @pytest.mark.parametrize(
        ("lhs", "name", "flags"),
        [
            ("bind", "bind", ""),
            ("bindle", "bind", "le"),
            ("bindm", "bind", "m"),
            ("envd", "env", "d"),
            ("gesture", "gesture", ""),
        ],
    )
    def test_flag_handlers_match_by_prefix(
        self, tmp_path: Path, lhs: str, name: str, flags: str
    ) -> None:
        result = parse_text(tmp_path, f"{lhs} = a, b\n")
        assert (handlers(result)[0].name, handlers(result)[0].flags) == (name, flags)

    def test_a_colon_disqualifies_handler_matching(self, tmp_path: Path) -> None:
        """Which is what keeps `binds:*` a config value rather than a `bind` invocation."""
        result = parse_text(tmp_path, "binds:workspace_back_and_forth = true\n")
        assert not handlers(result)
        assert assignments(result) == {"binds:workspace_back_and_forth": "true"}

    @pytest.mark.parametrize("name", ["windowrulev2", "layerrulev2"])
    def test_the_pre_054_rule_spellings_parse_and_are_flagged(
        self, tmp_path: Path, name: str
    ) -> None:
        result = parse_text(tmp_path, f"{name} = float, class:^(x)$\n")
        assert handlers(result)[0].name == name
        assert DiagnosticCode.DEPRECATED_KEYWORD in codes(result)

    def test_the_value_is_never_split(self, tmp_path: Path) -> None:
        """hyprlang hands the handler the raw right-hand side; arity is the handler's."""
        result = parse_text(tmp_path, "bind = SUPER, S, exec, sh -c 'a, b, c'\n")
        assert handlers(result)[0].value == "SUPER, S, exec, sh -c 'a, b, c'"


class TestDirectives:
    def test_a_true_condition_keeps_its_block(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "# hyprlang if MY_FLAG\ngeneral {\n  a = 1\n}\n# hyprlang endif\n",
        )
        assert assignments(result) == {"general:a": "1"}

    def test_a_false_condition_drops_its_block(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "# hyprlang if NOPE\ngeneral {\n  a = 1\n}\n# hyprlang endif\n",
        )
        assert not assignments(result)

    def test_negation_inverts_the_test(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "# hyprlang if !NOPE\ngeneral {\n  a = 1\n}\n# hyprlang endif\n",
        )
        assert assignments(result) == {"general:a": "1"}

    def test_a_taken_branch_is_reported_as_baked_in(self, tmp_path: Path) -> None:
        """ADR-0009 "Needs review": the branch depends on the importing machine."""
        result = parse_text(tmp_path, "# hyprlang if MY_FLAG\n# hyprlang endif\n")
        assert DiagnosticCode.CONDITIONAL_BAKED in codes(result)

    def test_config_variables_are_visible_to_a_condition(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "$FLAG = yes\n# hyprlang if FLAG\ngeneral {\n  a = 1\n}\n# hyprlang endif\n",
        )
        assert assignments(result) == {"general:a": "1"}

    def test_only_the_innermost_condition_is_consulted(self, tmp_path: Path) -> None:
        """A truthy nested `if` resurrects content inside a *failed* outer block.

        Comment lines keep being processed inside a failed block, so the inner directive
        still pushes -- and only the top of the stack is ever consulted
        (`config.cpp:676-684`). It reads like a bug and is real behaviour, which is exactly
        why the branch taken has to reach the Loss report rather than being assumed.
        """
        result = parse_text(
            tmp_path,
            "# hyprlang if NOPE\n"
            "# hyprlang if MY_FLAG\n"
            "general {\n  a = 1\n}\n"
            "# hyprlang endif\n"
            "general {\n  b = 2\n}\n"
            "# hyprlang endif\n"
            "general {\n  c = 3\n}\n",
        )
        assert assignments(result) == {"general:a": "1", "general:c": "3"}

    def test_a_stray_endif_is_reported(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "# hyprlang endif\n")
        assert DiagnosticCode.STRAY_ENDIF in codes(result)

    def test_an_unknown_directive_is_an_ordinary_comment(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "# hyprlang frobnicate\n# just a comment\n")
        assert not result.diagnostics

    def test_a_double_hash_is_a_comment_not_a_directive(self, tmp_path: Path) -> None:
        """Exactly one `#` is stripped before the `hyprlang` test.

        Verified against libhyprlang 0.6.8: with `##`, the `if` block is never closed, so
        everything after it stays skipped.
        """
        result = parse_text(
            tmp_path,
            "# hyprlang if NOPE\ngeneral {\n  a = 1\n}\n"
            "## hyprlang endif\ngeneral {\n  b = 2\n}\n",
        )
        assert not assignments(result)

    def test_noerror_suppresses_the_record_but_not_the_parse(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "# hyprlang noerror true\nnonsense line\n")
        assert not result.errors
        assert [u.code for u in unparsed(result)] == [DiagnosticCode.INVALID_LINE]
        assert result.diagnostics[0].suppressed

    def test_noerror_false_re_enables_recording(self, tmp_path: Path) -> None:
        result = parse_text(
            tmp_path,
            "# hyprlang noerror true\nbad one\n# hyprlang noerror false\nbad two\n",
        )
        assert [d.text.strip() for d in result.errors] == ["bad two"]


class TestSource:
    def test_a_glob_is_inlined_in_sorted_order(self, tmp_path: Path) -> None:
        (tmp_path / "parts").mkdir()
        (tmp_path / "parts" / "b.conf").write_text("exec-once = second\n")
        (tmp_path / "parts" / "a.conf").write_text("exec-once = first\n")
        result = parse_text(tmp_path, "source = parts/*.conf\n")
        assert [h.value for h in handlers(result)] == ["first", "second"]

    def test_a_nested_relative_source_resolves_against_its_own_file(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "mid.conf").write_text("source = b/leaf.conf\n")
        (tmp_path / "a" / "b" / "leaf.conf").write_text("exec-once = leaf\n")
        result = parse_text(tmp_path, "source = a/mid.conf\n")
        assert [h.value for h in handlers(result)] == ["leaf"]

    def test_parser_state_carries_across_the_file_boundary(self, tmp_path: Path) -> None:
        """The category stack and the variables are shared, exactly as hyprlang shares them."""
        (tmp_path / "part.conf").write_text("kb_variant = $v\n")
        result = parse_text(tmp_path, "$v = intl\ninput {\n  source = part.conf\n}\n")
        assert assignments(result) == {"input:kb_variant": "intl"}

    def test_a_sourced_file_that_leaves_a_category_open_is_reported(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "part.conf").write_text("general {\n  a = 1\n")
        result = parse_text(tmp_path, "source = part.conf\nmisc:b = 2\n")
        assert DiagnosticCode.UNCLOSED_CATEGORY in codes(result)
        assert assignments(result) == {"general:a": "1", "misc:b": "2"}

    def test_a_glob_matching_nothing_is_reported_and_preserved(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "source = nowhere/*.conf\n")
        assert DiagnosticCode.SOURCE_NO_MATCH in codes(result)
        assert [u.code for u in unparsed(result)] == [DiagnosticCode.SOURCE_NO_MATCH]

    def test_a_directory_match_is_skipped_with_a_warning(self, tmp_path: Path) -> None:
        (tmp_path / "parts").mkdir()
        (tmp_path / "parts" / "sub").mkdir()
        (tmp_path / "parts" / "a.conf").write_text("exec-once = only\n")
        result = parse_text(tmp_path, "source = parts/*\n")
        assert DiagnosticCode.SOURCE_NOT_A_FILE in codes(result)
        assert [h.value for h in handlers(result)] == ["only"]

    def test_a_cycle_is_refused_rather_than_recursed(self, tmp_path: Path) -> None:
        (tmp_path / "loop.conf").write_text("source = hyprland.conf\n")
        result = parse_text(tmp_path, "source = loop.conf\n")
        assert DiagnosticCode.SOURCE_CYCLE in codes(result)

    def test_a_too_short_path_is_refused(self, tmp_path: Path) -> None:
        result = parse_text(tmp_path, "source = x\n")
        assert DiagnosticCode.SOURCE_PATH_TOO_SHORT in codes(result)

    def test_an_unreadable_entry_file_is_reported_not_raised(self, tmp_path: Path) -> None:
        result = parse(tmp_path / "missing.conf", env=FIXTURE_ENV)
        assert DiagnosticCode.SOURCE_UNREADABLE in codes(result)
        assert not result.keywords

    def test_following_can_be_turned_off(self, tmp_path: Path) -> None:
        """Detect-only passes want the `source =` line itself, not the tree behind it."""
        (tmp_path / "part.conf").write_text("exec-once = sourced\n")
        result = parse_text(tmp_path, "source = part.conf\n", follow_source=False)
        assert [(h.name, h.value) for h in handlers(result)] == [("source", "part.conf")]

    def test_every_file_read_is_listed_in_order(self, tmp_path: Path) -> None:
        (tmp_path / "part.conf").write_text("exec-once = sourced\n")
        result = parse_text(tmp_path, "source = part.conf\n")
        assert [p.name for p in result.files] == ["hyprland.conf", "part.conf"]


class TestNothingIsSilentlyDropped:
    """The Loss report can only report what the parser kept (ADR-0009)."""

    def test_every_malformed_line_reaches_the_stream_with_its_file_and_line(
        self, tmp_path: Path
    ) -> None:
        result = parse_text(
            tmp_path,
            "general {\n"
            "  border_size = 2\n"
            "}\n"
            "no equals here\n"
            "= empty lhs\n"
            "}\n"
            "source = nowhere/*.conf\n",
        )
        preserved = [(u.origin.line, u.text.strip()) for u in unparsed(result)]
        assert preserved == [
            (4, "no equals here"),
            (5, "= empty lhs"),
            (6, "}"),
            (7, "source = nowhere/*.conf"),
        ]
        assert all(u.origin.file.name == "hyprland.conf" for u in unparsed(result))

    def test_a_malformed_line_in_a_sourced_file_keeps_that_files_name(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "part.conf").write_text("# lead\nnonsense\n")
        result = parse_text(tmp_path, "source = part.conf\n")
        assert [(u.origin.file.name, u.origin.line) for u in unparsed(result)] == [
            ("part.conf", 2)
        ]

    def test_the_stray_close_of_an_unbalanced_file_does_not_swallow_later_lines(
        self, tmp_path: Path
    ) -> None:
        result = parse_text(tmp_path, "}\nmisc:a = 1\n")
        assert assignments(result) == {"misc:a": "1"}


class TestGrammarGolden:
    """Every construct at once, over a real multi-file tree.

    Regenerate deliberately, never reflexively::

        UPDATE_GOLDEN=1 pytest tests/unit/test_importer_hyprlang.py

    A regenerated golden nobody read turns a failing test into a silent change in how every
    user's config converts.
    """

    def test_the_grammar_tree_matches_its_golden(self) -> None:
        result = parse(GRAMMAR_TREE / "hyprland.conf", env=FIXTURE_ENV)
        assert_matches_golden(
            render_keyword_stream(result, GRAMMAR_TREE),
            GOLDEN_DIR / "importer" / "grammar.stream.txt",
            "the hyprlang keyword stream",
        )

    def test_the_golden_tree_exercises_every_diagnostic_the_grammar_can_raise(self) -> None:
        """A construct with no fixture is a construct no golden protects.

        The four exempt codes need a malformed *tree* rather than a malformed line, so they
        live in the unit tests above where a `tmp_path` can be built for each.
        """
        result = parse(GRAMMAR_TREE / "hyprland.conf", env=FIXTURE_ENV)
        raised = {d.code for d in result.diagnostics}
        exempt = {
            DiagnosticCode.SOURCE_CYCLE,
            DiagnosticCode.SOURCE_UNREADABLE,
            DiagnosticCode.SOURCE_PATH_TOO_SHORT,
            DiagnosticCode.SOURCE_NOT_A_FILE,
            DiagnosticCode.UNCLOSED_CATEGORY,
            DiagnosticCode.TRAILING_BACKSLASH,
            DiagnosticCode.VARIABLE_RECURSION,
        }
        missing = set(DiagnosticCode) - raised - exempt
        assert not missing, f"no fixture raises these: {sorted(c.value for c in missing)}"
