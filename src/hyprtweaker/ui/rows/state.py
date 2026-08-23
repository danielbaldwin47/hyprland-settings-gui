"""What chrome a Row wears, decided before a single widget exists.

The same split `plan.py` draws for a Page, drawn again for a Row. Everything ADR-0013 hangs
off the suffix strip -- which state pills show, whether the dependency badge is up, whether
the Row counts as modified, what the Help popover says -- is a function of the Schema and
the model, and none of it needs a toolkit to decide. Keeping it here buys the same two
things: it is unit-testable on a machine with no display, and "does a sentinel leak into the
UI?" becomes a question about a string rather than about a widget tree.

The load-bearing idea is `NO_VALUE`. Three different model states mean "this Option has no
value" -- Unset over a sentinel default, an explicit null, and a value that *is* the curated
`null_value` -- and every one of them has to render as the Option's `null_label` ("Device
default", "Automatic") rather than as the number or the marker underneath. Prototype #8
measured rendering a sentinel as data as its most damaging defect class; `shown_value` is
the one place that judgement is made, so no control can make it differently.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Final, Protocol

from hyprtweaker.engine.model import UNSET, OptionValue, display_text
from hyprtweaker.engine.schema import (
    OptionType,
    ResolvedOption,
    Restart,
    Schema,
    Visibility,
    humanise,
)


class _NoValue(enum.Enum):
    """A singleton so `NO_VALUE` is narrowable and `None` keeps its own meaning.

    `None` is already taken here -- it is the model's explicit null -- so "renders as the
    null label" needs an object of its own rather than another overload of `None`.
    """

    TOKEN = enum.auto()

    def __repr__(self) -> str:
        return "NO_VALUE"


NO_VALUE: Final = _NoValue.TOKEN
"""This Option has no value to show: render its `null_label`, never a number or a marker."""

ADVANCED_PILL: Final = "Advanced"
RESTART_PILL: Final = "Restart"
PENDING_RESTART_PILL: Final = "Pending restart"

_UNLABELLED_NULL: Final = "Not set"
"""What a nullable Option with no curated `null_label` falls back to.

Unreachable with a complete Overlay -- the ADR-0011 completeness test requires a
`null_label` on every nullable Option -- and deliberately not a sentinel: if curation ever
regresses, the Row should read "Not set" rather than `[[EMPTY]]`."""

_RESTART_EFFECT: Final = {
    Restart.HYPRLAND: "the next time Hyprland starts",
    Restart.MONITOR_RELOAD: "the next time the monitors are reloaded",
    Restart.XWAYLAND: "the next time XWayland starts",
}


# --- what a value shows as --------------------------------------------------------------------


def shown_value(option: ResolvedOption, value: OptionValue) -> Any:
    """The value a control should render, or `NO_VALUE` when there is honestly none.

    Folds the three spellings of "no value" into one answer:

    * **Unset over a sentinel default.** `descriptions` prints `[[EMPTY]]`/`[[Auto]]`/`-1`,
      the generator resolves those to a `None` default, and an Unset Option therefore has
      nothing to fall back to but the label.
    * **Explicit null**, the model's third state (ADR-0005).
    * **A value equal to the curated `null_value`.** The two pressure-range floats default
      to `-1` *and* carry `-1` as their null spelling, so a Row that showed the number would
      be reporting "minus one" for what the tablet driver calls its own default.
    """
    if value is UNSET:
        value = option.default
    if value is None or _spells_no_value(option, value):
        return NO_VALUE
    return value


def _spells_no_value(option: ResolvedOption, value: Any) -> bool:
    """Whether a concrete value *is* this Option's curated "no value"."""
    null_value = option.null_value
    if not option.nullable or null_value is None:
        return False
    return _same_value(value, null_value)


def no_value_label(option: ResolvedOption) -> str:
    """The curated "no value" text: "Device default", "Automatic", "Same as outer gaps"."""
    return option.null_label or _UNLABELLED_NULL


def value_label(option: ResolvedOption, value: OptionValue) -> str:
    """One value as the words a Row shows for it -- never a raw enum number or a sentinel.

    Goes through the same three enum sources the combo offers (`labels`, the generated
    `map`, `known_values`), so "Default: Dwindle" in the Help popover reads as the entry the
    dropdown would select rather than as `0`.
    """
    shown = shown_value(option, value)
    if shown is NO_VALUE:
        return no_value_label(option)
    return _labelled(option, shown)


def default_label(option: ResolvedOption) -> str:
    """Hyprland's own default, as words. What reset promises and the popover reports."""
    return value_label(option, UNSET)


def _labelled(option: ResolvedOption, value: Any) -> str:
    if option.labels is not None:
        label = option.labels.get(_label_key(value))
        if label is not None:
            return label
    if option.map is not None and not isinstance(value, bool):
        for name, mapped in option.map.items():
            if mapped == value:
                return humanise(name)
    known = option.known_values
    if known is not None and isinstance(value, str) and value in known.values:
        return humanise(value)
    if option.type is OptionType.BOOL:
        return "On" if value else "Off"
    return display_text(value)


def _label_key(value: Any) -> str:
    """A value as the string key the Overlay's `labels` map uses.

    JSON has no integer keys, so the Overlay stores `'2'` for an int Option and `'flat'`
    for a string one -- the same asymmetry `factory._typed` handles in the other direction.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --- the suffix strip -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pill:
    """One state pill: the label it shows and the sentence explaining it."""

    label: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class DependencyBadge:
    """An unmet `depends_on`, ready to render (ADR-0013 §3).

    Carries the controlling Option's *name* as well as its title, because the badge
    navigates: clicking it has to find that Row, not merely name it.
    """

    option: str
    label: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class HelpContent:
    """Everything the ⓘ popover holds. Static per Option -- built once, never refreshed."""

    text: str
    dotted_key: str
    default_label: str
    help_url: str | None


@dataclass(frozen=True, slots=True)
class RowState:
    """The whole of one Row's chrome, recomputed whenever the model moves under it."""

    pills: tuple[Pill, ...]
    dependency: DependencyBadge | None
    """`None` when the Option has no `depends_on`, or when it is satisfied."""

    modified: bool
    """Whether the model emits this Option at all -- ADR-0005's tri-state, not `!=`.

    Not a comparison against the default, and deliberately so: an Option set to exactly
    today's default is still set, still survives upstream changing that default, and still
    needs the arrow that takes the decision back (ADR-0013 §6 as amended during #57)."""

    reset_tooltip: str
    editable: bool
    """Whether the *control* may be used. Never the Row: a Row the user cannot edit still
    has to be readable, so only the control is dimmed (ADR-0013 §3)."""

    resettable: bool
    """Whether the reset arrow may be *clicked*, which is a different question from
    `editable` in both directions.

    A dependency-disabled Row is still resettable -- the value is in the config either way,
    unmet dependency or not, and taking it back out is a legitimate edit. A read-only
    session is not: `Session._refuse` would drop the write, and an arrow that silently does
    nothing is worse than one visibly greyed out beside a Banner saying why."""


class RowContext(Protocol):
    """What a Row needs to know about the running app. `Session` is the implementation.

    A Protocol rather than the concrete class so this module stays a decision layer: the
    unit tier drives it with a dozen-line stand-in, and nothing here can reach for a socket.
    """

    @property
    def schema(self) -> Schema: ...

    @property
    def live(self) -> bool: ...

    @property
    def pending_restart(self) -> frozenset[str]: ...

    def effective_value(self, option: ResolvedOption) -> Any: ...

    def is_modified(self, option: ResolvedOption) -> bool: ...


def row_state(option: ResolvedOption, context: RowContext) -> RowState:
    """Everything the suffix strip shows for one Option, right now."""
    dependency = unmet_dependency(option, context)
    return RowState(
        pills=_pills(option, context),
        dependency=dependency,
        modified=context.is_modified(option),
        reset_tooltip=f"Reset to default: {default_label(option)}",
        # Two independent reasons to dim, and they compose: a read-only session (no
        # compositor to apply to) and an unmet dependency.
        editable=context.live and dependency is None,
        resettable=context.live,
    )


def help_content(option: ResolvedOption) -> HelpContent:
    """The ⓘ popover's contents (ADR-0013 §7).

    The dotted key lives here and nowhere else on the Row -- the subtitle is the
    description, which is the point of §1. Search still indexes the key (ADR-0017).
    """
    return HelpContent(
        text=option.description,
        dotted_key=option.dotted_key,
        default_label=default_label(option),
        help_url=option.help_url,
    )


def unmet_dependency(option: ResolvedOption, context: RowContext) -> DependencyBadge | None:
    """The badge for a `depends_on` that is not currently satisfied, else `None`.

    Judged against the controlling Option's *effective* value -- what Hyprland is actually
    doing -- rather than against the model's, because an Unset controller is still on or off
    according to its own default, and the dependent Row is insensitive or not accordingly.
    """
    dependency = option.depends_on
    if dependency is None:
        return None

    controlling = context.schema.get(dependency.option)
    if controlling is None:
        # A Hyprland version this Overlay entry outlived (ADR-0012). Nothing to gate on and
        # nothing to navigate to, so the Row is simply editable.
        return None

    if _same_value(context.effective_value(controlling), dependency.value):
        return None

    return DependencyBadge(
        option=controlling.name,
        label=f"Requires {controlling.title}",
        tooltip=f"Needs “{controlling.title}” set to "
        f"{value_label(controlling, dependency.value)} — click to go there.",
    )


def _same_value(left: Any, right: Any) -> bool:
    """Equality that will not let `True` pass for `1`, or `False` for `0`.

    `bool` is an `int` in Python, and this module compares model values against two
    different kinds of curated constant where that bites: a `depends_on` wanting the number
    `1` (`input:tablettool:eraser_button_mode`) beside seventy-four wanting `True`, and a
    `null_value` of `-1` or `""` on Options that hold real booleans elsewhere. One rule for
    both, so the two cannot drift into disagreeing about what "the same value" means.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _pills(option: ResolvedOption, context: RowContext) -> tuple[Pill, ...]:
    pills: list[Pill] = []

    if option.visibility is not Visibility.DEFAULT:
        pills.append(
            Pill(
                ADVANCED_PILL,
                "Shown because “Show advanced settings” is on."
                if option.visibility is Visibility.ADVANCED
                else "A low-level setting: shown only here, in the Config view.",
            )
        )

    restart = option.restart
    if restart is not None:
        effect = _RESTART_EFFECT[restart]
        if option.name in context.pending_restart:
            # "Applied to file, effective after Hyprland restart" (CONTEXT.md). Claimed only
            # once a transaction actually laid the bytes down -- `ApplyResult.pending_restart`
            # is the record of that, and promising a restart will produce a setting that was
            # never written is the falsehood this pill has to avoid.
            pills.append(Pill(PENDING_RESTART_PILL, f"Saved. It takes effect {effect}."))
        else:
            pills.append(Pill(RESTART_PILL, f"Changing this takes effect {effect}."))

    return tuple(pills)
