"""What chrome a Row wears, decided before a single widget exists.

The same split `plan.py` draws for a Page, drawn again for a Row. Everything ADR-0013 hangs
off the suffix strip -- which state pills show, what an expander's collapsed Value summary
reads, whether the dependency badge is up, whether the Row counts as modified, what the Help
popover says -- is a function of the Schema and the model, and none of it needs a toolkit to
decide. Keeping it here buys the same two
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

from hyprtweaker.engine.bridge import REGISTRY
from hyprtweaker.engine.ipc import LiveHyprland
from hyprtweaker.engine.model import (
    UNSET,
    CssGaps,
    Gradient,
    OptionValue,
    Vec2,
    display_text,
    parse_value,
)
from hyprtweaker.engine.schema import (
    OptionType,
    ResolvedOption,
    Restart,
    Schema,
    SupplementKind,
    Visibility,
    Widget,
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
UNAPPLIED_PILL: Final = "Didn't apply"
OVERRIDDEN_PILL: Final = "Overridden"
DEVICE_PILL: Final = "Per-device"
PLUGIN_PILL: Final = "Plugin option"
NOT_IN_HYPRLAND_PILL: Final = "Not in this Hyprland"
RETIRED_PILL: Final = "Retired in {release}"
SET_BY_TOOL_PILL: Final = "Set by {tool}"
"""Which of these a Row shows, and in what order, is `PILL_PRECEDENCE`'s alone."""

NOT_SET: Final = "Not set"
"""The app's word for "no value written": what a nullable Option with no curated
`null_label` falls back to, and what the monitor and bind editors name their empty choice.

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
    return option.null_label or NOT_SET


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


# --- the collapsed value summary --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValueSummary:
    """What an ExpanderRow shows while collapsed (ADR-0013 §4).

    An expander is the one Row shape that hides its own value: collapsed, a gradient with
    two stops at 45° and a gradient with one stop at 0° look identical. The summary is what
    makes the Row answer "what is it set to?" without being opened, which is the whole
    question a settings list exists to answer at a glance.
    """

    text: str
    """The dim label: an angle, a gap run, a pair of axes -- or the `null_label`."""

    swatches: tuple[str, ...] = ()
    """One CSS colour per gradient stop, in gradient order. Empty for every other type.

    Strings rather than parsed colours because this module is toolkit-free on purpose; the
    chrome hands them straight to `Gdk.RGBA.parse`. `#rrggbbaa` is the spelling both ends
    agree on -- alpha last, as everywhere outside Hyprland's packed ARGB word.
    """


_SUMMARISED = frozenset({Widget.GRADIENT, Widget.CSS_GAPS, Widget.VEC2})
"""The three widgets that are expanders, and therefore the three Rows that need a summary.

Keyed on `widget`, not on `type`, because that is what the Row factory dispatches on. The
two agree throughout the shipped Schema, but the Overlay exists precisely to override
`widget` -- and keying on `type` would put a collapsed-value preview on a Row that never
collapses the moment one did.

Colours are not here: a colour button *is* its own preview, and font weights render their
value in the control. The row catalogue names exactly these three."""


def value_summary(option: ResolvedOption, value: OptionValue) -> ValueSummary | None:
    """The collapsed preview for one Option, or `None` when its Row is not an expander.

    Goes through `shown_value` like every other control, so an Option with no value
    summarises as "Same as outer gaps" rather than as the `-1` underneath it -- the summary
    is the most visible thing on a collapsed Row and the least excusable place to leak a
    sentinel.
    """
    if option.widget not in _SUMMARISED:
        return None

    shown = shown_value(option, value)
    if shown is NO_VALUE:
        return ValueSummary(no_value_label(option))

    try:
        typed = parse_value(option.type, shown)
    except (ValueError, TypeError):
        # A value this Option's own parser refuses -- a Hyprland version whose spelling
        # changed under a schema (ADR-0012). Showing it verbatim beats showing nothing:
        # the user can at least see what is in there.
        return ValueSummary(display_text(shown))

    if isinstance(typed, Gradient):
        return ValueSummary(
            f"{display_text(typed.angle)}°",
            tuple(f"#{color.rgba:08x}" for color in typed.colors),
        )
    if isinstance(typed, CssGaps):
        sides = (typed.top, typed.right, typed.bottom, typed.left)
        if len(set(sides)) == 1:
            # One number for the uniform case, because four identical numbers is four times
            # the ink for the same fact -- and uniform is what almost every rice writes.
            return ValueSummary(str(typed.top))
        return ValueSummary(" · ".join(str(side) for side in sides))
    if isinstance(typed, Vec2):
        return ValueSummary(f"{_axis(typed.x)}, {_axis(typed.y)}")

    return ValueSummary(display_text(typed))


def _axis(value: float) -> str:
    """One vec2 axis, always with a decimal point: `0.0`, not `0`.

    The row catalogue's spelling ("0.0, 0.5"), and it earns the extra character: a vec2 is
    the one type here that holds fractions, and `0, 0.5` reads as a pair of different kinds
    of number rather than as a coordinate.
    """
    text = f"{value:g}"
    return f"{text}.0" if "." not in text and "e" not in text else text


# --- the suffix strip -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pill:
    """One state pill: the label it shows and the sentence explaining it."""

    label: str
    tooltip: str
    backend: str | None = None
    """The tool whose controls on the Theming page this pill opens, or `None` for a pill
    that only explains. A pill that leads somewhere renders as a button (ADR-0014)."""


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
    summary: ValueSummary | None
    """The collapsed preview, or `None` for every Row that is not an expander."""

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
    unmet dependency or not, and taking it back out is a legitimate edit. So is a set Row
    the running Hyprland lacks: its key is a config error there, and Reset is the way out
    (#215). A read-only session is not, nor is a Retired Row: `Session._refuse` would drop
    the write, and an arrow that silently does nothing is worse than one visibly greyed out
    beside the words saying why."""

    subtitle: str
    """The Option's description, and under it, on a read-only Row, why it is read-only
    and what the user can do (#215). In the subtitle rather than only a pill's tooltip,
    because a tooltip is out of reach of the keyboard and of a screen reader."""


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

    @property
    def unapplied(self) -> frozenset[str]: ...

    @property
    def overridden(self) -> frozenset[str]: ...

    @property
    def device_overrides(self) -> Mapping[str, tuple[str, ...]]: ...

    @property
    def bridge_owners(self) -> Mapping[str, str]:
        """Option name to the theming tool whose loading Bridge module sets it (#163)."""
        ...

    @property
    def live_hyprland(self) -> LiveHyprland | None: ...

    def unknown_to_version(self, option: ResolvedOption) -> bool:
        """Whether a running Hyprland was described and this Option is not among its own.

        False with no compositor: absence of evidence badges nothing.
        """
        ...

    def retired_in(self, option: ResolvedOption) -> str | None:
        """The release that retired this Option while the user set it, or `None` (ADR-0012)."""
        ...

    def kept_value(self, name: str) -> OptionValue:
        """The value the Manifest keeps for this Option, typed, or `UNSET` (#215).

        Every retirement reason, announced or quiet: a kept value makes the Row read-only
        whether or not `retired_in` gives it a pill.
        """
        ...

    def value_of(self, option: ResolvedOption) -> OptionValue: ...

    def effective_value(self, option: ResolvedOption) -> Any: ...

    def is_modified(self, option: ResolvedOption) -> bool: ...


def row_value(option: ResolvedOption, context: RowContext) -> OptionValue:
    """The value a Row renders: the kept value of a Retired Option, else the model's.

    A Retired Option is Unset in the model -- the app stopped writing it -- so rendering
    the model would show Hyprland's default where the pill promises "your value is kept"
    (#215). Every control and the Value summary read through here.
    """
    kept = context.kept_value(option.name)
    return context.value_of(option) if kept is UNSET else kept


def row_state(option: ResolvedOption, context: RowContext) -> RowState:
    """Everything the suffix strip shows for one Option, right now."""
    dependency = unmet_dependency(option, context)
    summary = value_summary(option, row_value(option, context))
    retired = context.kept_value(option.name) is not UNSET
    why = _read_only_reason(option, context, summary)
    return RowState(
        pills=_pills(option, context),
        summary=summary,
        dependency=dependency,
        modified=context.is_modified(option),
        reset_tooltip=f"Reset to default: {default_label(option)}",
        # Three independent reasons to dim, and they compose: a read-only session (no
        # compositor to apply to), an unmet dependency, and an Option this Hyprland does not
        # take (Retired, or Not in this Hyprland: every edit would be a config error).
        editable=context.live and dependency is None and why is None,
        resettable=context.live and not retired,
        subtitle="\n".join(line for line in (option.description, why) if line),
    )


def _read_only_reason(
    option: ResolvedOption, context: RowContext, summary: ValueSummary | None
) -> str | None:
    """Why this Option's control is read-only and what the user can do, or `None` (#215).

    Only the two reasons the Row alone can explain. A read-only session has the Banner, and
    an unmet dependency has its badge.
    """
    kept = context.kept_value(option.name)
    if kept is not UNSET:
        # The summary's spelling on an expander, so the line and the collapsed Row agree.
        value = summary.text if summary is not None else value_label(option, kept)
        release = context.retired_in(option)
        if release is None:
            return f"Your value: {value}. It is kept for when Hyprland has this setting again."
        return (
            f"Your value: {value}. Hyprland {release} removed this setting; it is kept for "
            "when the setting returns."
        )
    live = context.live_hyprland
    if live is None or not context.unknown_to_version(option):
        return None
    if context.is_modified(option):
        return (
            f"Hyprland {live.version} does not have this setting. Reset removes it from your "
            "config."
        )
    return f"Hyprland {live.version} does not have this setting, so it cannot be changed here."


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


# --- the pills, one builder each -------------------------------------------------------------
#
# A builder answers whether its pill applies to this Row and, if so, what it says. It never
# decides order or whether another pill shows: that is `PILL_PRECEDENCE`, below.


def _unapplied_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    if option.name not in context.unapplied:
        return None
    # ADR-0016: "An unexplained read-back mismatch (value didn't take, no error, no
    # override) badges the Row 'didn't apply' and joins the Banner." The one Row badge
    # error surfacing is allowed, and only for the *unexplained* case -- a value
    # `user.lua` overrode on purpose is the drift badge's business, and a value Hyprland
    # complained about by name is the Banner's. This is the case with no explanation at
    # all: the app wrote the key and the live config does not set it.
    return Pill(
        UNAPPLIED_PILL, "This was written to your config, but Hyprland is not using it."
    )


def _overridden_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    if option.name not in context.overridden:
        return None
    # The sibling of "Didn't apply", and the reason that one is only for the
    # *unexplained* mismatch: this value did not take either, but for a reason the app
    # can name. `user.lua` is required last, so it wins on purpose -- that is the
    # escape hatch working, not a fault, and the Row says so rather than badging it as
    # a failure (ADR-0005; deferred here from #57 until there was a reader for it).
    return Pill(
        OVERRIDDEN_PILL,
        # Deliberately does not name `user.lua` outright: a Bridge module is loaded
        # after the app's Modules too and wins the same way, and telling someone to
        # go edit a file that is not the culprit is worse than saying less. Naming
        # the file needs Ownership class, which the Banner has and a Row does not.
        "Something loaded after the app's own settings sets this too, so its "
        "value wins -- usually your user.lua.",
    )


def _set_by_tool_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # ADR-0006: a Bridge module is required after the app's own Modules, so the tool's value
    # is the one Hyprland uses -- the answer to "why does my change not stick". The control
    # stays editable (ADR-0014: no inline unlock): the user's value is kept in the app's own
    # Module and applies again once the tool stops setting the key (#163 reading 3). The pill
    # leads to the one place that changes who sets it, the tool on the Theming page.
    tool = context.bridge_owners.get(option.name)
    if tool is None:
        return None
    spec = REGISTRY.get(tool)
    name = spec.title if spec is not None else tool
    return Pill(
        SET_BY_TOOL_PILL.format(tool=name),
        f"{name} sets this, so Hyprland uses {name}'s value. A value you set here is kept "
        f"and applies once {name} no longer sets it. Click to open {name} on the Theming "
        "page.",
        backend=tool,
    )


def _retired_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # ADR-0012 §Retirement: "the Row is badged". A release removed an Option the user set,
    # so the app stopped writing it and keeps the value in the Manifest. The same Option
    # unset is only `Not in this Hyprland`, which this row of the table suppresses: the
    # user's value is the news, not the compositor's version.
    release = context.retired_in(option)
    if release is None:
        return None
    return Pill(
        RETIRED_PILL.format(release=release),
        f"Hyprland {release} removed this setting, so it cannot be changed here. Your value "
        "is kept and comes back if the setting returns.",
    )


def _not_in_hyprland_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # The `unknown-to-this-version` Row state (CONTEXT.md, #77): the running compositor
    # was described and this Option is not among its own, because the app degraded onto
    # a Schema newer than it (ADR-0012). The control is read-only (#215): every edit would
    # be a config error and an auto-revert. A set one keeps its Reset, the way out of the
    # error its key raises. A *set* Option a newer release removed is Retired instead
    # (#178), whose row in `PILL_PRECEDENCE` suppresses this one.
    live = context.live_hyprland
    if live is None or not context.unknown_to_version(option):
        return None
    cannot = (
        f"Hyprland {live.version} does not have this setting, so it cannot be changed here."
    )
    if context.is_modified(option):
        return Pill(NOT_IN_HYPRLAND_PILL, f"{cannot} Reset removes it from your config.")
    return Pill(NOT_IN_HYPRLAND_PILL, cannot)


def _pending_restart_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # "Applied to file, effective after Hyprland restart" (CONTEXT.md). Claimed only
    # once a transaction actually laid the bytes down -- `ApplyResult.pending_restart`
    # is the record of that, and promising a restart will produce a setting that was
    # never written is the falsehood this pill has to avoid.
    if option.restart is None or option.name not in context.pending_restart:
        return None
    return Pill(
        PENDING_RESTART_PILL, f"Saved. It takes effect {_RESTART_EFFECT[option.restart]}."
    )


def _restart_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    if option.restart is None:
        return None
    return Pill(RESTART_PILL, f"Changing this takes effect {_RESTART_EFFECT[option.restart]}.")


def _new_in_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # ADR-0012 §Pinning: a Hyprland newer than every shipped schema described this Option
    # and the app inferred a minimal record for it, so it renders flagged -- a generic
    # control, no curated title or help -- until a real Generated schema replaces it.
    flag = option.supplement
    if flag is None or flag.kind is not SupplementKind.NEWER_VERSION:
        return None
    return Pill(
        f"New in {flag.version}",
        f"Hyprland {flag.version} added this setting after this version of the app was made, "
        "so it gets a basic control until you update the app.",
    )


def _plugin_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # ADR-0018 §Plugins: a loaded plugin's setting, inferred from its description alone,
    # so it renders flagged -- a generic control, no curated title or help -- for good.
    flag = option.supplement
    if flag is None or flag.kind is not SupplementKind.PLUGIN:
        return None
    return Pill(
        PLUGIN_PILL,
        "Added by a loaded plugin. The app knows only its name and type, so it gets a "
        "basic control.",
    )


def _device_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    # The `device-override` Row state (ADR-0013, CONTEXT.md). Distinct from
    # "Overridden", which is about a *file* loaded after the app's own and is therefore
    # the same story for every Option it touches: this one is scoped to particular
    # hardware, so the Row still holds true for every other device and the pill has to
    # say which. Not an error and not a failure to apply -- a per-device setting
    # winning over the global one is `hl.device` working exactly as documented.
    devices = context.device_overrides.get(option.name)
    if not devices:
        return None
    named = ", ".join(devices)
    return Pill(DEVICE_PILL, f"{named} has its own value for this, which wins for that device.")


def _advanced_pill(option: ResolvedOption, context: RowContext) -> Pill | None:
    if option.visibility is Visibility.DEFAULT:
        return None
    return Pill(
        ADVANCED_PILL,
        "Shown because “Show advanced settings” is on."
        if option.visibility is Visibility.ADVANCED
        else "A low-level setting: shown only here, in the Config view.",
    )


# --- the precedence table --------------------------------------------------------------------


class PillKind(enum.Enum):
    """A row of `PILL_PRECEDENCE`, so one row can name another it suppresses."""

    UNAPPLIED = enum.auto()
    RETIRED = enum.auto()
    SET_BY_TOOL = enum.auto()
    OVERRIDDEN = enum.auto()
    NOT_IN_HYPRLAND = enum.auto()
    PENDING_RESTART = enum.auto()
    RESTART = enum.auto()
    NEW_IN = enum.auto()
    PLUGIN = enum.auto()
    DEVICE = enum.auto()
    ADVANCED = enum.auto()


@dataclass(frozen=True, slots=True)
class PillRule:
    """One pill's row: which pill, how to build it, and the pills it makes redundant."""

    kind: PillKind
    build: Callable[[ResolvedOption, RowContext], Pill | None]
    suppresses: frozenset[PillKind] = frozenset()


MAX_PILLS: Final = 2
"""A Row shows at most this many pills (inbox #79); the last one shown lists the rest."""

PILL_PRECEDENCE: Final[tuple[PillRule, ...]] = (
    PillRule(PillKind.UNAPPLIED, _unapplied_pill),
    PillRule(PillKind.RETIRED, _retired_pill, frozenset({PillKind.NOT_IN_HYPRLAND})),
    # The tool is what overrides the app's value, and this pill names it (#165).
    PillRule(PillKind.SET_BY_TOOL, _set_by_tool_pill, frozenset({PillKind.OVERRIDDEN})),
    PillRule(PillKind.OVERRIDDEN, _overridden_pill),
    PillRule(PillKind.NOT_IN_HYPRLAND, _not_in_hyprland_pill),
    PillRule(PillKind.PENDING_RESTART, _pending_restart_pill, frozenset({PillKind.RESTART})),
    PillRule(PillKind.RESTART, _restart_pill),
    PillRule(PillKind.NEW_IN, _new_in_pill),
    PillRule(PillKind.PLUGIN, _plugin_pill),
    PillRule(PillKind.DEVICE, _device_pill),
    PillRule(PillKind.ADVANCED, _advanced_pill),
)
"""The one place pill order is decided, highest rank first (ADR-0013 suffix strip).

What failed first, then who owns the value, then what is unusual about the Option. A new
pill adds a row here at its rank -- with the pills it makes redundant, if any -- and never
compares itself with another pill anywhere else.
"""


def _pills(option: ResolvedOption, context: RowContext) -> tuple[Pill, ...]:
    built = [(rule, rule.build(option, context)) for rule in PILL_PRECEDENCE]
    applicable = [(rule, pill) for rule, pill in built if pill is not None]
    suppressed = {kind for rule, _ in applicable for kind in rule.suppresses}
    ranked = [pill for rule, pill in applicable if rule.kind not in suppressed]
    if len(ranked) <= MAX_PILLS:
        return tuple(ranked)

    *shown, last = ranked[:MAX_PILLS]
    rest = ", ".join(pill.label for pill in ranked[MAX_PILLS:])
    # `replace`, so a capped pill that leads somewhere still does.
    return (*shown, replace(last, tooltip=f"{last.tooltip}\nAlso: {rest}."))
