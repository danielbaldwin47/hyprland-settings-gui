"""The curated Tasks view: four categories over the same Schema the Config view renders.

The Config view is generated and therefore cannot drift; Tasks is *curated* and therefore
can. That asymmetry is the whole design (#7). Curation buys a sidebar organised by what
someone wants to change rather than by what Hyprland calls it -- and it costs a mapping that
a new Hyprland release can leave behind. So the mapping is held as data (`data/schema/
tasks.json`), and everything an Option can do that the mapping did not anticipate resolves
to *appearing anyway*, in a `New in <version>` group, rather than to disappearing.

That last rule is the one to preserve when changing this file. A curated view whose failure
mode is a missing setting is worse than no curated view at all: the user cannot tell "not
supported" from "we forgot", and the Config view they would fall back to is the thing they
came here to avoid reading. Every code path below that could drop an Option instead places
it somewhere visible and flags it.

Nothing here imports `gi` -- same reason as `plan.py`, so "did an Option go missing?" stays
a question about a tuple on a machine with no display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyprtweaker.engine.schema import ResolvedOption, Schema
from hyprtweaker.engine.schema.resolve import schema_dir

from .plan import GroupPlan, PagePlan, View, group_title, is_visible

TASKS_FILENAME = "tasks.json"
FORMAT_VERSION = 1

FALLBACK_CATEGORY = "system"
"""Where a Section the mapping never placed puts its Page.

System rather than a fifth "Other" category: an uncurated Section is a temporary state that
the next curation pass removes, and a category that exists only to hold mistakes would
outlive them on screen. System is already the home of the least task-shaped Pages.
"""

ORPHAN_CATEGORY_TITLE = "Other"
"""The category invented only when the mapping has no `system` category to append to.

A plain word rather than the `New in <version>` heading: that string names a *Group* of
uncurated Options (`CONTEXT.md`: a Group is the titled block inside a Page), and reusing it
one level up would put a Group's name where a category's belongs, telling the reader that a
whole sidebar section is a version rather than a subject.
"""


def new_in_group_title(version: str) -> str:
    """The heading uncurated Options appear under (ADR-0012, #7).

    Named for the Hyprland version rather than a bare "Other" because the version is the
    actionable part: it tells the user these arrived with an upgrade, and it tells whoever
    curates next exactly which release to diff.
    """
    return f"New in {version}"


NEW_IN_GROUP_DESCRIPTION = (
    "Settings this version of Hyprland has that the curated pages do not place yet. "
    "They work exactly as they do in the Config view."
)
"""The flag #7 and ADR-0012 ask for ("appears ... flagged, until it is curated").

The heading alone reads as *new*, which is not the same claim: it would leave a user to
wonder whether an uncurated setting is half-supported. Saying it plainly is what makes the
degradation legible rather than merely visible.
"""


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """One curated Group: a heading and the Option keys placed under it, in author order."""

    title: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageSpec:
    """One curated option Page and what it claims.

    `sections` is a *home*: every Option of those Sections lands here unless some group
    claims it by name. `groups` is an explicit placement that outranks any home, which is
    what lets `input` split four ways and `misc` dissolve into the Pages its settings
    actually belong on.
    """

    id: str
    title: str
    sections: tuple[str, ...] = ()
    groups: tuple[GroupSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """A reference to a Page the shell builds from the model rather than from the Schema.

    Carries only the sidebar id: an Entity Page knows its own title and its own contents,
    and duplicating either here would be a second place for them to disagree.
    """

    section: str


Destination = PageSpec | EntitySpec


@dataclass(frozen=True, slots=True)
class CategorySpec:
    """One of the four sidebar categories, with its destinations in author order."""

    id: str
    title: str
    pages: tuple[Destination, ...]


@dataclass(frozen=True, slots=True)
class TasksMapping:
    """The whole curated mapping, as read from `tasks.json`."""

    categories: tuple[CategorySpec, ...]

    @property
    def option_keys(self) -> tuple[str, ...]:
        """Every Option key the mapping places by name, in file order.

        Used by the completeness test to prove no key is claimed twice; a duplicate is
        otherwise invisible, because the second claim simply loses.
        """
        return tuple(
            key
            for category in self.categories
            for page in category.pages
            if isinstance(page, PageSpec)
            for group in page.groups
            for key in group.options
        )

    @property
    def homed_sections(self) -> frozenset[str]:
        return frozenset(
            section
            for category in self.categories
            for page in category.pages
            if isinstance(page, PageSpec)
            for section in page.sections
        )


def load_tasks_mapping(directory: Path | None = None) -> TasksMapping:
    """Read the curated mapping. Raises if it is unreadable -- unlike Prefs.

    Deliberately strict where `prefs.py` is forgiving, because the two failures are not
    alike. A missing preference costs the user a re-toggle; a missing mapping means the
    default view has no Pages, and an app that silently opens empty is harder to diagnose
    than one that says the mapping is broken. The file ships with the app, so a failure here
    is a packaging bug that should be loud.
    """
    path = (directory or schema_dir()) / TASKS_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected an object")
    version = payload.get("format_version")
    if version != FORMAT_VERSION:
        raise ValueError(f"{path}: format_version {version!r}, expected {FORMAT_VERSION}")

    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise ValueError(f"{path}: 'categories' must be a list")

    return TasksMapping(categories=tuple(_category(entry, path) for entry in categories))


def _category(entry: Any, path: Path) -> CategorySpec:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: a category must be an object")
    pages = entry.get("pages")
    if not isinstance(pages, list):
        raise ValueError(f"{path}: category {entry.get('id')!r} has no 'pages' list")
    return CategorySpec(
        id=str(entry["id"]),
        title=str(entry["title"]),
        pages=tuple(_destination(page, path) for page in pages),
    )


def _destination(entry: Any, path: Path) -> Destination:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: a destination must be an object")
    if "entity" in entry:
        return EntitySpec(section=str(entry["entity"]))
    return PageSpec(
        id=str(entry["id"]),
        title=str(entry["title"]),
        sections=tuple(str(name) for name in entry.get("sections", ())),
        groups=tuple(_group(group, path) for group in entry.get("groups", ())),
    )


def _group(entry: Any, path: Path) -> GroupSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: a group must be an object")
    return GroupSpec(
        title=str(entry["title"]),
        options=tuple(str(key) for key in entry.get("options", ())),
    )


@dataclass(frozen=True, slots=True)
class CategoryPlan:
    """One category's heading and the destinations under it, in sidebar order."""

    id: str
    title: str
    pages: tuple[PagePlan | EntitySpec, ...]

    @property
    def option_pages(self) -> tuple[PagePlan, ...]:
        return tuple(page for page in self.pages if isinstance(page, PagePlan))


def plan_tasks_view(
    schema: Schema,
    mapping: TasksMapping,
    *,
    show_advanced: bool = False,
) -> tuple[CategoryPlan, ...]:
    """Every curated Page, plus a fallback Page for any Section the mapping never placed.

    The fallback is not an error path that ought to stay unused -- it is the designed
    behaviour on every Hyprland release between the release and its curation (ADR-0012).
    Exercise it in tests with a Section the mapping omits, never by trusting that the
    shipped mapping happens to be complete today.
    """
    placed = _placements(mapping)
    by_name = {option.name: option for option in schema}
    planned: list[CategoryPlan] = []

    for category in mapping.categories:
        pages: list[PagePlan | EntitySpec] = []
        for destination in category.pages:
            if isinstance(destination, EntitySpec):
                pages.append(destination)
                continue
            pages.append(
                _plan_page(schema, destination, placed, by_name, show_advanced=show_advanced)
            )
        planned.append(CategoryPlan(id=category.id, title=category.title, pages=tuple(pages)))

    return tuple(_with_fallbacks(planned, schema, mapping, placed, show_advanced=show_advanced))


@dataclass(frozen=True, slots=True)
class _Placement:
    """Where one named Option was curated to: which Page, under which heading."""

    page_id: str
    group_title: str
    order: int


def _placements(mapping: TasksMapping) -> dict[str, _Placement]:
    """Every by-name claim in the mapping. First claim wins; the test forbids a second."""
    placements: dict[str, _Placement] = {}
    for category in mapping.categories:
        for page in category.pages:
            if not isinstance(page, PageSpec):
                continue
            for group in page.groups:
                for order, key in enumerate(group.options):
                    placements.setdefault(key, _Placement(page.id, group.title, order))
    return placements


def _claimed_elsewhere(placed: dict[str, _Placement], name: str, page_id: str) -> bool:
    """Whether some *other* Page named this Option, so its Section's home must not take it.

    The one predicate the home-versus-named precedence turns on (`groups` outrank
    `sections`), spelled once: written inline it reads as a comparison between a placement
    and a page id, which is not the question being asked.
    """
    claim = placed.get(name)
    return claim is not None and claim.page_id != page_id


def _plan_page(
    schema: Schema,
    spec: PageSpec,
    placed: dict[str, _Placement],
    by_name: dict[str, ResolvedOption],
    *,
    show_advanced: bool,
) -> PagePlan:
    """One curated Page: its homed Sections first, then the Groups it curated by name.

    Sections first because they are what the Page is *about* -- the curated Groups on a Page
    like Rendering are settings pulled in from `misc`, and leading with borrowed settings
    would read as though `misc` were the subject.
    """
    withheld = 0

    section_groups: dict[str, list[ResolvedOption]] = {}
    multi = len(spec.sections) > 1
    for section in spec.sections:
        for option in schema.section(section):
            if _claimed_elsewhere(placed, option.name, spec.id):
                continue
            if not is_visible(option, show_advanced=show_advanced, view=View.TASKS):
                withheld += 1
                continue
            title = _section_group_title(schema, option, section, multi=multi)
            section_groups.setdefault(title, []).append(option)

    groups = [
        GroupPlan(title=title, options=tuple(options))
        for title, options in sorted(section_groups.items(), key=lambda item: item[1][0].order)
    ]

    for group in spec.groups:
        members: list[ResolvedOption] = []
        for key in group.options:
            curated = by_name.get(key)
            if curated is None:
                # The mapping names an Option this Hyprland does not have. A test keeps the
                # shipped mapping honest for the shipped Schema; at runtime an older or
                # newer compositor simply has fewer settings, which is not an error.
                continue
            if not is_visible(curated, show_advanced=show_advanced, view=View.TASKS):
                withheld += 1
                continue
            members.append(curated)
        if members:
            groups.append(GroupPlan(title=group.title, options=tuple(members)))

    return PagePlan(
        section=spec.id,
        title=spec.title,
        groups=tuple(groups),
        withheld=withheld,
    )


def _section_group_title(
    schema: Schema, option: ResolvedOption, section: str, *, multi: bool
) -> str:
    """The heading an Option sits under on a curated Page.

    On a single-Section Page this is exactly the Config view's answer, so a Page that
    happens to be one Section reads the same in both views. On a Page spanning several --
    Layouts is four -- the Section's own title leads, because the alternative is every
    Section's untitled lead Group merging into one heap of unrelated settings.
    """
    derived = group_title(option)
    if not multi:
        return derived
    section_title = schema.section_title(section)
    return f"{section_title} · {derived}" if derived else section_title


def _with_fallbacks(
    planned: list[CategoryPlan],
    schema: Schema,
    mapping: TasksMapping,
    placed: dict[str, _Placement],
    *,
    show_advanced: bool,
) -> list[CategoryPlan]:
    """Append a Page per uncurated Section: a release adds settings rather than hiding them.

    Keyed on the Section rather than on the individual Option, which is a real limit worth
    stating: an Option added to a Section the mapping *already* homes lands on that home
    Page unflagged, because nothing here can tell it apart from the Options that were always
    there. Detecting that needs a per-Option "added in" fact the Schema does not carry -- it
    would come from diffing two shipped Generated schemas (ADR-0012's standing drift loop),
    not from anything visible at plan time. Whole uncurated Sections are what this catches,
    and they are the case where an Option would otherwise be unreachable rather than merely
    unsorted.
    """
    homed = mapping.homed_sections

    fallbacks: list[PagePlan] = []
    for section in schema.section_names:
        if section in homed:
            continue
        visible = [
            option
            for option in schema.section(section)
            if option.name not in placed
            and is_visible(option, show_advanced=show_advanced, view=View.TASKS)
        ]
        if not visible:
            # Every Option here is either curated elsewhere by name or is the hidden tier,
            # which has no Tasks home at any switch setting (ADR-0013 §5). Nothing to show.
            continue
        fallbacks.append(
            PagePlan(
                section=f"tasks.new.{section}",
                title=schema.section_title(section),
                groups=(
                    GroupPlan(
                        title=new_in_group_title(schema.hyprland_version),
                        options=tuple(visible),
                        description=NEW_IN_GROUP_DESCRIPTION,
                    ),
                ),
                withheld=0,
            )
        )

    if not fallbacks:
        return planned

    if any(category.id == FALLBACK_CATEGORY for category in planned):
        return [
            CategoryPlan(
                id=category.id,
                title=category.title,
                pages=category.pages + tuple(fallbacks),
            )
            if category.id == FALLBACK_CATEGORY
            else category
            for category in planned
        ]

    # The mapping has no System category to append to -- someone renamed or removed it.
    # A category of our own rather than dropping the Pages on the floor: this function
    # exists so that no Option can go missing, and "the fallback silently did nothing"
    # would be the one bug it must never have.
    return [
        *planned,
        CategoryPlan(
            id=FALLBACK_CATEGORY,
            title=ORPHAN_CATEGORY_TITLE,
            pages=tuple(fallbacks),
        ),
    ]
