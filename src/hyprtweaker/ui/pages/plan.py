"""What a generated Page contains, decided before a single widget exists.

The Config view is one Page per Section (ADR-0013, `CONTEXT.md`), and every question about
its *shape* -- which Options appear, in which Groups, in what order -- is answered from the
Schema alone. Keeping that answer here, as plain data, buys two things the widget factory
cannot: it is unit-testable on a machine with no GTK, and "did an Option go missing?" is a
question about a tuple rather than about a widget tree.

**Grouping is data, not code** (prototype #8's finding, and the reason it is a finding:
`input` renders 60 flat rows without one). The Overlay's curated `group` wins wherever it
exists. Nothing carries one yet -- that curation is #82 -- so until then a Group is derived
from the Option's own path: `decoration:blur:size` sits under "Blur", `decoration:rounding`
sits in the Section's untitled lead Group. The stub tree gives sub-prefixes for free, which
is worth having; what it cannot give is the cross-cutting Groups a person expects
("Scrolling" spans `input:*` and `input:touchpad:*`), and that is exactly what #82 adds.

Nothing here imports `gi`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hyprtweaker.engine.schema import ResolvedOption, Schema, Visibility, humanise

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


def is_visible(option: ResolvedOption, *, show_advanced: bool) -> bool:
    """Whether the Advanced switch lets this Option render right now.

    Both non-default tiers gate on the one global switch (ADR-0013 §5). They differ only in
    the *Tasks* view, where `hidden` never appears at all -- a distinction this planner does
    not have to know, because it only ever plans the Config view.
    """
    return option.visibility is Visibility.DEFAULT or show_advanced


@dataclass(frozen=True, slots=True)
class GroupPlan:
    """One `Adw.PreferencesGroup`: a heading and the Rows under it."""

    title: str
    options: tuple[ResolvedOption, ...]


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


def plan_section(schema: Schema, section: str, *, show_advanced: bool = False) -> PagePlan:
    """Plan one Section's Page.

    Ordering is Hyprland's own declaration order throughout -- Groups appear in the order
    their first Option is declared, and Options within a Group likewise. Upstream's grouping
    intent comes free with that order and no curation should silently rewrite it; a curated
    `order` is a position *within* a Group, which is why it only ever breaks the tie.
    """
    options = schema.section(section)
    visible = [option for option in options if is_visible(option, show_advanced=show_advanced)]

    grouped: dict[str, list[ResolvedOption]] = {}
    for option in visible:
        grouped.setdefault(group_title(option), []).append(option)

    groups = tuple(
        GroupPlan(title=title, options=tuple(sorted(members, key=_within_group)))
        for title, members in sorted(grouped.items(), key=lambda item: item[1][0].order)
    )

    return PagePlan(
        section=section,
        title=schema.section_title(section),
        groups=groups,
        withheld=len(options) - len(visible),
    )


def _within_group(option: ResolvedOption) -> tuple[int, int]:
    """Curated position first, declaration order for everything the Overlay left alone."""
    if option.group_order is None:
        return (1, option.order)
    return (0, option.group_order)


def plan_config_view(schema: Schema, *, show_advanced: bool = False) -> tuple[PagePlan, ...]:
    """Every Section's Page, in the order Hyprland declares the Sections.

    One Page per Section unconditionally, including the ones the Advanced switch empties:
    the sidebar is the map of the config surface, and a Section that vanishes when a switch
    flips is a Section the user cannot learn exists.
    """
    return tuple(
        plan_section(schema, section, show_advanced=show_advanced)
        for section in schema.section_names
    )
