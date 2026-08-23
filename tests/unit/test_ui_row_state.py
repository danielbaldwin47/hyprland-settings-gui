"""The Row's chrome, decided on a machine with no display.

`state.py` is the half of the Row contract that has answers worth asserting -- which pills
show, whether a dependency is met, what the default *reads* as -- and ADR-0013's most
expensive claim lives here: a sentinel is never rendered as data. Prototype #8 measured that
defect class as the most damaging one it found, so it gets a test over the whole shipped
Schema rather than over a handful of examples.

The UI smoke tier then only has to ask whether GTK hangs the right widgets off these
answers.
"""

from __future__ import annotations

from typing import Any

from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.model import UNSET, ConfigModel, OptionValue
from hyprtweaker.engine.schema import ResolvedOption, Schema, Visibility, load_schema
from hyprtweaker.ui.rows.state import (
    ADVANCED_PILL,
    NO_VALUE,
    PENDING_RESTART_PILL,
    RESTART_PILL,
    default_label,
    help_content,
    no_value_label,
    row_state,
    shown_value,
    unmet_dependency,
    value_label,
)

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)

SENTINEL_MARKERS = ("[[EMPTY]]", "[[Auto]]")
"""What `descriptions` prints for an Option with no default. None of it is a value."""


class FakeContext:
    """A `RowContext` over a real model -- everything the state layer needs, no sockets."""

    def __init__(
        self,
        *,
        live: bool = True,
        pending_restart: frozenset[str] = frozenset(),
    ) -> None:
        self.schema: Schema = SCHEMA
        self.live = live
        self.pending_restart = pending_restart
        self.model = ConfigModel(SCHEMA)

    def value_of(self, option: ResolvedOption) -> OptionValue:
        return self.model.get(option.name)

    def effective_value(self, option: ResolvedOption) -> Any:
        value = self.model.get(option.name)
        return option.default if value is UNSET else value

    def is_modified(self, option: ResolvedOption) -> bool:
        return self.model.is_set(option.name)


# --- honest sentinels -------------------------------------------------------------------------


def test_an_unset_option_over_a_sentinel_default_has_no_value_to_show() -> None:
    """`[[EMPTY]]` resolves to a `None` default, so there is nothing to fall back to."""
    option = SCHEMA["input:kb_variant"]

    assert option.default is None
    assert shown_value(option, UNSET) is NO_VALUE
    assert value_label(option, UNSET) == "None"


def test_a_value_that_is_the_curated_null_spelling_shows_as_the_label_not_the_number() -> None:
    """The pressure floats default to `-1` *and* spell their null as `-1`.

    A Row showing "-1.00" would be reporting a pressure nobody chose: the number is the
    tablet driver's word for "use my own default", which is exactly what `null_label` says.
    """
    option = SCHEMA["input:tablettool:pressure_range_min"]

    assert option.default == -1
    assert shown_value(option, UNSET) is NO_VALUE
    assert shown_value(option, -1) is NO_VALUE
    assert value_label(option, -1) == "Device default"
    assert shown_value(option, 0.4) == 0.4


def test_an_explicit_null_shows_as_the_label_too() -> None:
    option = SCHEMA["general:locale"]

    assert shown_value(option, None) is NO_VALUE
    assert value_label(option, None) == "System locale"


def test_a_minus_one_that_is_a_real_value_stays_a_number() -> None:
    """`misc:force_default_wallpaper = -1` means "pick a random one", not "no value".

    The ambiguity is the reason a numeric `-1` is a curation flag rather than an automatic
    null (`CONTEXT.md`, Sentinel): one Option's "unset" is another's "random". The curated
    label is what makes it readable; what must not happen is the value disappearing into a
    "no value" placeholder that would stop the Row reporting the setting at all.
    """
    option = SCHEMA["misc:force_default_wallpaper"]

    assert not option.nullable
    assert shown_value(option, -1) == -1
    assert value_label(option, -1) == "Random"


def test_no_option_in_the_schema_renders_a_sentinel_marker() -> None:
    """The whole shipped Schema, both for the default and for the "no value" text."""
    for option in SCHEMA:
        for text in (default_label(option), no_value_label(option)):
            assert not any(marker in text for marker in SENTINEL_MARKERS), option.name
            assert text, option.name


def test_every_nullable_option_has_curated_words_for_its_absence() -> None:
    """The leak ADR-0013 names: a missing `null_label` puts `[[EMPTY]]` in a placeholder."""
    for option in SCHEMA:
        if option.nullable:
            assert option.null_label, option.name
            assert no_value_label(option) == option.null_label


# --- the default, in words --------------------------------------------------------------------


def test_an_enum_default_reads_as_the_entry_the_dropdown_would_select() -> None:
    """ "Default: 0" is true and useless; the popover has to say what the combo says."""
    assert default_label(SCHEMA["general:layout"]) == "Dwindle"
    assert default_label(SCHEMA["input:tablettool:eraser_button_mode"]) == "Hardware default"


def test_a_boolean_default_reads_as_a_state_rather_than_as_a_python_literal() -> None:
    assert default_label(SCHEMA["general:resize_on_border"]) == "Off"
    assert default_label(SCHEMA["misc:enable_swallow"]) == "Off"


# --- state pills ------------------------------------------------------------------------------


def _pills(option: ResolvedOption, context: FakeContext) -> tuple[str, ...]:
    return tuple(pill.label for pill in row_state(option, context).pills)


def test_a_default_tier_option_wears_no_pills() -> None:
    assert _pills(SCHEMA["general:gaps_in"], FakeContext()) == ()


def test_both_non_default_tiers_wear_the_advanced_pill() -> None:
    """Revealed in place, and legible as revealed: an advanced Row that looked ordinary
    would leave the user unable to tell why it appeared (ADR-0013 §5)."""
    advanced = next(o for o in SCHEMA if o.visibility is Visibility.ADVANCED)
    hidden = next(o for o in SCHEMA if o.visibility is Visibility.HIDDEN)

    assert _pills(advanced, FakeContext()) == (ADVANCED_PILL,)
    assert _pills(hidden, FakeContext()) == (ADVANCED_PILL,)


def test_a_restart_flagged_option_says_so_before_it_is_touched() -> None:
    assert _pills(SCHEMA["xwayland:enabled"], FakeContext()) == (RESTART_PILL,)


def test_a_restart_flagged_option_that_was_written_badges_pending_restart() -> None:
    """ "Applied to file, effective after Hyprland restart" (`CONTEXT.md`).

    Driven by `ApplyResult.pending_restart`, which names only keys whose bytes actually
    landed -- so a refused edit never promises the user that a restart will produce it.
    """
    context = FakeContext(pending_restart=frozenset({"xwayland:enabled"}))

    assert _pills(SCHEMA["xwayland:enabled"], context) == (PENDING_RESTART_PILL,)
    assert _pills(SCHEMA["render:cm_enabled"], context) == (RESTART_PILL,)


def test_an_advanced_restart_flagged_option_wears_both_pills_in_order() -> None:
    option = SCHEMA["debug:gl_debugging"]

    assert _pills(option, FakeContext()) == (ADVANCED_PILL, RESTART_PILL)


# --- dependency badges ------------------------------------------------------------------------

GRAB_AREA = "general:extend_border_grab_area"
RESIZE_ON_BORDER = "general:resize_on_border"


def test_an_unmet_dependency_names_the_option_that_gates_it() -> None:
    context = FakeContext()
    badge = unmet_dependency(SCHEMA[GRAB_AREA], context)

    assert badge is not None
    assert badge.option == RESIZE_ON_BORDER
    assert badge.label == f"Requires {SCHEMA[RESIZE_ON_BORDER].title}"


def test_a_met_dependency_raises_no_badge_and_leaves_the_control_editable() -> None:
    context = FakeContext()
    context.model.set(RESIZE_ON_BORDER, True)

    state = row_state(SCHEMA[GRAB_AREA], context)

    assert state.dependency is None
    assert state.editable


def test_an_unmet_dependency_dims_the_control_and_nothing_else() -> None:
    """ADR-0013 §3: the Row is never hidden, and its text is never dimmed -- a user who
    cannot edit an option is precisely the one who needs to read why."""
    state = row_state(SCHEMA[GRAB_AREA], FakeContext())

    assert state.dependency is not None
    assert not state.editable


def test_a_dependency_is_judged_against_the_effective_value_not_the_model() -> None:
    """An Unset controller is still on or off by its own default, and its dependants are
    insensitive or not accordingly -- `depends_on` is about what Hyprland is doing."""
    option = SCHEMA["decoration:shadow:range"]
    context = FakeContext()

    assert not context.is_modified(SCHEMA["decoration:shadow:enabled"])
    assert SCHEMA["decoration:shadow:enabled"].default is True
    assert unmet_dependency(option, context) is None


def test_a_dependency_badge_reports_the_value_it_wants_in_words() -> None:
    badge = unmet_dependency(SCHEMA["input:scroll_points"], FakeContext())

    assert badge is not None
    assert "Custom" in badge.tooltip


def test_an_int_valued_dependency_is_judged_as_an_int() -> None:
    """One `depends_on` wants the number 1 where the rest want `True`, and `1 == True` in
    Python -- so the comparison must refuse to let either stand in for the other."""
    option = SCHEMA["input:tablettool:eraser_button_override"]
    context = FakeContext()

    assert option.depends_on is not None
    assert option.depends_on.value == 1
    assert unmet_dependency(option, context) is not None, "0 is not 1"

    context.model.set("input:tablettool:eraser_button_mode", 1)
    assert unmet_dependency(option, context) is None

    context.model.set("input:tablettool:eraser_button_mode", 0)
    assert unmet_dependency(option, context) is not None


def test_every_dependency_in_the_overlay_points_at_an_option_that_exists() -> None:
    """A badge reading "Requires <nothing>" is unclickable and unexplainable."""
    for option in SCHEMA:
        if option.depends_on is not None:
            assert SCHEMA.get(option.depends_on.option) is not None, option.name


# --- modified, reset, and the read-only session -----------------------------------------------


def test_a_row_counts_as_modified_when_the_model_emits_it_at_all() -> None:
    """ADR-0005's tri-state, not `!=`: an Option deliberately set to today's default is
    still set, and its reset arrow is how the user takes that decision back."""
    option = SCHEMA["general:border_size"]
    context = FakeContext()

    assert not row_state(option, context).modified

    context.model.set(option.name, option.default)
    assert row_state(option, context).modified


def test_the_reset_tooltip_promises_the_default_in_the_same_words_the_row_shows() -> None:
    state = row_state(SCHEMA["general:layout"], FakeContext())

    assert state.reset_tooltip == "Reset to default: Dwindle"


def test_a_read_only_session_leaves_every_control_uneditable() -> None:
    context = FakeContext(live=False)

    assert not row_state(SCHEMA["general:gaps_in"], context).editable


# --- the help popover -------------------------------------------------------------------------


def test_the_help_popover_carries_the_key_the_subtitle_no_longer_shows() -> None:
    """ADR-0013 §1 moved the dotted key off the Row and into the ⓘ."""
    option = SCHEMA["input:kb_layout"]
    content = help_content(option)

    assert content.dotted_key == option.dotted_key
    assert content.dotted_key not in content.text
    assert content.text == option.description


def test_every_option_has_help_text_and_a_default_to_show() -> None:
    for option in SCHEMA:
        content = help_content(option)
        assert content.default_label, option.name
        assert content.text, option.name
        assert content.dotted_key, option.name


def test_the_wiki_link_falls_back_to_the_sections_own_anchor() -> None:
    """No Option carries a `help_url` of its own yet, so every link is the Section's --
    which is what stops 353 Rows needing 353 hand-written URLs."""
    links = {help_content(option).help_url for option in SCHEMA.section("input")}

    assert links == {"https://wiki.hypr.land/Configuring/Variables/#input"}


def test_the_three_undocumented_sections_simply_have_no_link() -> None:
    """`experimental`, `input-capture` and `quirks` have no wiki page to point at.

    Recorded rather than papered over: an invented anchor would 404, and the popover drops
    the link rather than offering a dead one. If the wiki grows those sections, the Overlay
    is where that lands -- this test is the thing that will notice.
    """
    without = {option.section for option in SCHEMA if help_content(option).help_url is None}

    assert without == {"experimental", "input-capture", "quirks"}


def test_no_options_subtitle_is_its_dotted_key() -> None:
    """The Row subtitle is the description (ADR-0013 §1, superseding `CONTEXT.md`)."""
    for option in SCHEMA:
        assert option.description != option.dotted_key, option.name
        assert option.description != option.name, option.name
