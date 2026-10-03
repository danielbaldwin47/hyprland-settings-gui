"""What a generated Page contains, decided before a single widget exists.

The Config view is one Page per Section (ADR-0013, `CONTEXT.md`), and every question about
its *shape* -- which Options appear, in which Groups, in what order -- is answered from the
Schema alone. Keeping that answer here, as plain data, buys two things the widget factory
cannot: it is unit-testable on a machine with no GTK, and "did an Option go missing?" is a
question about a tuple rather than about a widget tree.

**Grouping is data, not code** (prototype #8's finding, and the reason it is a finding:
`input` renders 60 flat rows without one). A Section declares its curated Groups in the
Overlay, in display order, and each Option names its own in `group` (#157): that is how
"Scrolling" spans `input:*` and `input:touchpad:*`, which no stub-tree prefix can say. An
Option no curation names -- a plugin's, or one a release added before its release check --
falls back to a Group derived from its own path: `decoration:blur:size` under "Blur",
`decoration:rounding` in the Section's untitled lead Group, or under "Other settings" on a
Section whose curated Groups are titled. `plan_groups` is the one place that order lives,
and both Views call it.

Nothing here imports `gi`.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, replace

from hyprtweaker.engine.schema import (
    ResolvedOption,
    Schema,
    SupplementKind,
    Visibility,
    humanise,
)
from hyprtweaker.engine.schema.resolve import version_key


class View(enum.StrEnum):
    """The two sidebar arrangements (`CONTEXT.md`), as far as visibility is concerned.

    Only the Config view exists today -- the Tasks view and its switcher are #71 -- but the
    rule that separates them is ADR-0013's and belongs here rather than in whatever builds
    the Tasks sidebar later: the `hidden` tier is Config-view-only, so `debug:manual_crash`
    can never surface on a curated Page however the Advanced switch is set.
    """

    CONFIG = "config"
    TASKS = "tasks"


@dataclass(frozen=True, slots=True)
class Disclosure:
    """What the user can currently see: the inputs every visibility question is asked with.

    Not `Visibility` -- that is the Schema's *tier* enum, a fact about an Option. This is a
    fact about the window: where the Advanced switch stands, which View is active, and which
    Options a search hit has revealed One-off (ADR-0013 §5, ADR-0017). One value rather than
    three parameters so that a planner takes it whole and passes it on whole; the bare
    constructor is a fresh window's state.
    """

    show_advanced: bool = False
    view: View = View.CONFIG
    revealed: frozenset[str] = frozenset()
    """The One-off: Options a search hit has earned a place for on this visit.

    Carried here rather than as a flag on the Option because the exemption belongs to *this*
    rebuild -- an Option that carried its own "revealed" bit would stay revealed until
    something thought to clear it, which is the state ADR-0017 rejected temporary visibility
    modes to avoid."""


DEFAULT_DISCLOSURE = Disclosure()
"""A fresh window's: the Config view, the Advanced switch off, nothing revealed."""


_SEGMENT_TITLES = {
    "col": "Colors",
}
"""Path segments whose plain title-casing would be a config key rather than a word.

One entry, not a table to grow: every further case belongs in the Overlay's `group`, which
is reviewed, translatable and version-independent. This exists so `general:col.*` does not
render under a heading reading "Col" in the meantime."""


def group_title(option: ResolvedOption) -> str:
    """The heading an Option sits under. Empty means the Section's lead Group.

    Curated `group` first; otherwise the Option's own sub-path, which is the nesting
    Hyprland already declares (`decoration:blur:*`, `group:groupbar:col.*`).
    """
    if option.group:
        return option.group

    segments = option.path[1:-1]
    return " · ".join(_segment_title(segment) for segment in segments)


def _segment_title(segment: str) -> str:
    return _SEGMENT_TITLES.get(segment) or humanise(segment)


def is_visible(option: ResolvedOption, disclosure: Disclosure) -> bool:
    """Whether the Advanced switch lets this Option render right now.

    Both non-default tiers gate on the one global switch (ADR-0013 §5), and they differ in
    exactly one way: `hidden` -- `debug`, `quirks`, `experimental`, `input-capture` -- is
    Config-view-only, so no amount of switch-flipping puts "Crash Hyprland" on a curated
    Tasks Page. Search reaches every tier regardless and reveals its hit one-off (ADR-0017),
    through `disclosure.revealed`.

    A One-off exempts an Option from the **switch**, never from the **View's tier rule**,
    and the order of the tests below is that distinction. ADR-0013 §5 is unconditional --
    the `hidden` tier "appears only in the Config view ... never in Tasks" -- so a revealed
    Option that is checked before the tier rule renders "Crash Hyprland" on a curated Page
    the moment the user switches back to Tasks with the reveal still outstanding. The reveal
    is how search reaches a withheld Row; it is not a licence to put one where the View says
    it may never go.
    """
    if option.visibility is Visibility.DEFAULT:
        return True
    if option.visibility is Visibility.HIDDEN and disclosure.view is not View.CONFIG:
        return False
    if option.name in disclosure.revealed:
        return True
    return disclosure.show_advanced


def is_withheld(option: ResolvedOption, disclosure: Disclosure) -> bool:
    """Whether the Advanced switch, turned on, would render this Option where it now does not.

    What a Page's withheld count counts, in both Views: its hint promises the switch shows
    more, so an Option the View never renders (the hidden tier in Tasks) is not withheld.
    """
    return not is_visible(option, disclosure) and is_visible(
        option, replace(disclosure, show_advanced=True)
    )


@dataclass(frozen=True, slots=True)
class GroupPlan:
    """One `Adw.PreferencesGroup`: a heading and the Rows under it."""

    title: str
    options: tuple[ResolvedOption, ...]
    description: str = ""
    """Why this Group exists, when the heading alone does not say it.

    Empty for the ordinary Groups, whose heading is the whole story. It earns its place on
    the `New in <version>` fallback (#7, ADR-0012): "flagged" is what the spec asks of an
    Option the curated mapping has not placed, and a heading naming a version does not, on
    its own, tell the reader that these settings are uncurated rather than merely new."""


@dataclass(frozen=True, slots=True)
class PagePlan:
    """One `Adw.PreferencesPage`: a whole Section, as it will be rendered."""

    section: str
    title: str
    groups: tuple[GroupPlan, ...]
    withheld: int
    """Options this Section has that the Advanced switch is currently hiding.

    Carried rather than recomputed because a Page with every Option withheld -- `debug`,
    `quirks`, `experimental`, `input-capture`, `opengl` -- renders no Groups at all, and an
    empty Page that cannot say *why* it is empty reads as a broken app."""

    @property
    def option_count(self) -> int:
        return sum(len(group.options) for group in self.groups)


def new_in_group_title(version: str) -> str:
    """The heading uncurated Options appear under (ADR-0012, #7), in either View.

    Named for the Hyprland version rather than a bare "Other" because the version is the
    actionable part: it tells the user these arrived with an upgrade, and it tells whoever
    curates next exactly which release to diff.
    """
    return f"New in {version}"


NEW_IN_GROUP_DESCRIPTION = (
    "Settings this version of Hyprland has that the curated pages do not place yet. "
    "They work exactly as they do in the Config view."
)
"""The Tasks view's flag, which #7 and ADR-0012 ask for ("appears ... flagged, until it is
curated").

The heading alone reads as *new*, which is not the same claim: it would leave a user to
wonder whether an uncurated setting is half-supported. Saying it plainly is what makes the
degradation legible rather than merely visible.
"""

SUPPLEMENTED_GROUP_DESCRIPTION = (
    "Your Hyprland has these settings and this version of the app does not know them yet, "
    "so each gets a basic control. They are saved like any other setting."
)
"""The Config view's flag on a `New in <version>` Group: Options a Hyprland newer than every
shipped schema described, inferred from that description alone (ADR-0012 §Pinning). Says
why the controls are plain, and that nothing about them is half-working."""


OTHER_SETTINGS_TITLE = "Other settings"
"""The heading for an uncurated Option with no sub-path on a Section whose Groups are curated.

Only an Option a release added before its release check lands here (or a plugin's, whose
Section no curation reaches): an untitled Group after titled ones reads as a rendering
fault rather than as a heading."""


def plan_groups(
    schema: Schema, section: str, options: Iterable[ResolvedOption]
) -> tuple[GroupPlan, ...]:
    """The Groups `options`, all of one Section, form on a Page, in display order.

    The order, in both Views: the Section's curated Groups in the order the Overlay declares
    them, each with its curated description; then the Groups no curation names, derived from
    the Options' paths, in the order their first Option is declared. Within a Group, curated
    `group_order` leads and declaration order follows, so an uncurated Option whose path
    names a curated heading (a release's new `decoration:blur:*` under a curated "Blur")
    joins that Group after its curated Rows. Options in, Groups out, one Group per Option:
    nothing is dropped and nothing is repeated, whatever the curation says.

    `options` are the ones the caller shows here; a caller that closes the Page with
    `New in <version>` Groups leaves those Options out and appends the Groups after these.
    """
    curated = schema.section_groups(section)
    rank = {group.title: index for index, group in enumerate(curated)}
    descriptions = {group.title: group.description or "" for group in curated}

    grouped: dict[str, list[ResolvedOption]] = {}
    for option in sorted(options, key=lambda option: option.order):
        title = group_title(option) or (OTHER_SETTINGS_TITLE if curated else "")
        grouped.setdefault(title, []).append(option)

    def position(item: tuple[str, list[ResolvedOption]]) -> tuple[int, int]:
        title, members = item
        if title in rank:
            return (0, rank[title])
        return (1, members[0].order)

    return tuple(
        GroupPlan(
            title=title,
            options=tuple(sorted(members, key=_within_group)),
            description=descriptions.get(title, ""),
        )
        for title, members in sorted(grouped.items(), key=position)
    )


def plan_section(
    schema: Schema,
    section: str,
    disclosure: Disclosure = DEFAULT_DISCLOSURE,
) -> PagePlan:
    """Plan one Section's Page.

    Groups appear in `plan_groups` order: the Section's curated Groups as the Overlay
    declares them, then the Groups derived from uncurated Options' paths in the order their
    first Option is declared. Curation shapes a Page and never reorders Sections: the
    Section sequence is Hyprland's declaration order (`Schema.section_names`).

    Options a newer Hyprland added beyond the shipped schema close the Page in their own
    `New in <version>` Group, oldest version first, rather than joining a curated or
    sub-path Group, so they stand out as flagged (ADR-0012 §Pinning).
    """
    options = schema.section(section)
    visible = [option for option in options if is_visible(option, disclosure)]

    shown: list[ResolvedOption] = []
    newer: dict[str, list[ResolvedOption]] = {}
    for option in visible:
        flag = option.supplement
        if flag is not None and flag.kind is SupplementKind.NEWER_VERSION:
            newer.setdefault(flag.version, []).append(option)
        else:
            shown.append(option)

    groups = plan_groups(schema, section, shown) + tuple(
        GroupPlan(
            title=new_in_group_title(version),
            options=tuple(members),
            description=SUPPLEMENTED_GROUP_DESCRIPTION,
        )
        for version, members in sorted(newer.items(), key=lambda item: version_key(item[0]))
    )

    return PagePlan(
        section=section,
        title=schema.section_title(section),
        groups=groups,
        withheld=sum(1 for option in options if is_withheld(option, disclosure)),
    )


def _within_group(option: ResolvedOption) -> tuple[int, int]:
    """Curated position first, declaration order for everything the Overlay left alone."""
    if option.group_order is None:
        return (1, option.order)
    return (0, option.group_order)


def plan_config_view(
    schema: Schema, disclosure: Disclosure = DEFAULT_DISCLOSURE
) -> tuple[PagePlan, ...]:
    """Every Section's Page, in the order Hyprland declares the Sections.

    One Page per Section unconditionally, including the ones the Advanced switch empties:
    the sidebar is the map of the config surface, and a Section that vanishes when a switch
    flips is a Section the user cannot learn exists.

    Planned under the Config view's tier rule whatever `disclosure.view` says: the window
    falls back to this arrangement from the Tasks view when the curated mapping will not
    load, and the arrangement it falls back to admits the `hidden` tier.
    """
    config = replace(disclosure, view=View.CONFIG)
    return tuple(plan_section(schema, section, config) for section in schema.section_names)
