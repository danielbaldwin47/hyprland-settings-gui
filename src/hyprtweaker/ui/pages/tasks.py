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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hyprtweaker.engine.schema import ResolvedOption, Schema, SupplementKind
from hyprtweaker.engine.schema.resolve import schema_dir, version_key

from .plan import (
    DEFAULT_DISCLOSURE,
    NEW_IN_GROUP_DESCRIPTION,
    Disclosure,
    GroupPlan,
    PagePlan,
    View,
    group_title,
    is_visible,
    is_withheld,
    new_in_group_title,
)

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


ENTITY_PAGE_PREFIX = "entity:"


def entity_page_id(kind: str) -> str:
    """The sidebar id (and stack name) of the Entity Page for `kind`.

    The one place that spells the `entity:` prefix. It exists because Hyprland has Sections
    and Entity kinds of the same name (`binds`, `animations`, `gestures`), and the Config
    view stacks one page per Section beside one per Entity kind: a bare `binds` is two pages
    under one name, and GTK keeps the first and drops the second (#70, #120).

    `kind` is the id suffix, not the `EntitySet` attribute name: the startup commands are
    `entity_page_id("autostart")`, though `EntitySet` calls them `startup`.
    """
    return f"{ENTITY_PAGE_PREFIX}{kind}"


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
        return EntitySpec(section=entity_page_id(str(entry["entity"])))
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
    disclosure: Disclosure = DEFAULT_DISCLOSURE,
) -> tuple[CategoryPlan, ...]:
    """Every curated Page, plus a fallback Page for any Section the mapping never placed.

    The fallback is not an error path that ought to stay unused -- it is the designed
    behaviour on every Hyprland release between the release and its curation (ADR-0012).
    Exercise it in tests with a Section the mapping omits, never by trusting that the
    shipped mapping happens to be complete today.

    Planned under the Tasks view's tier rule whatever `disclosure.view` says, so the
    `hidden` tier has no route onto a curated Page (ADR-0013 §5).
    """
    tasks = replace(disclosure, view=View.TASKS)
    placed = _placements(mapping)
    planned: list[CategoryPlan] = []

    for category in mapping.categories:
        pages: list[PagePlan | EntitySpec] = []
        for destination in category.pages:
            if isinstance(destination, EntitySpec):
                pages.append(destination)
                continue
            pages.append(_plan_page(schema, destination, placed, tasks))
        planned.append(CategoryPlan(id=category.id, title=category.title, pages=tuple(pages)))

    return tuple(_with_fallbacks(planned, schema, mapping, placed, tasks))


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


def _plan_page(
    schema: Schema,
    spec: PageSpec,
    placed: dict[str, _Placement],
    disclosure: Disclosure,
) -> PagePlan:
    """One curated Page: its homed Sections first, then the Groups it curated by name.

    Sections first because they are what the Page is *about* -- the curated Groups on a Page
    like Rendering are settings pulled in from `misc`, and leading with borrowed settings
    would read as though `misc` were the subject.

    An Option the mapping places by name sits only where it was placed, on this Page or on
    another: one Option, one Row. One no group places, that a newer Hyprland added
    (`added_in`), leaves its Section's Group for a `New in <version>` Group at the foot of
    its home Page, so it stands out among the settings that were always there until
    curation places it (ADR-0012).
    """
    withheld = 0

    section_groups: dict[str, list[ResolvedOption]] = {}
    new_in: dict[str, list[ResolvedOption]] = {}
    multi = len(spec.sections) > 1
    for section in spec.sections:
        for option in schema.section(section):
            if option.name in placed:
                continue
            if not is_visible(option, disclosure):
                withheld += is_withheld(option, disclosure)
                continue
            if option.added_in is not None:
                new_in.setdefault(option.added_in, []).append(option)
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
            curated = schema.get(key)
            if curated is None:
                # The mapping names an Option this Hyprland does not have. A test keeps the
                # shipped mapping honest for the shipped Schema; at runtime an older or
                # newer compositor simply has fewer settings, which is not an error.
                continue
            if not is_visible(curated, disclosure):
                withheld += is_withheld(curated, disclosure)
                continue
            members.append(curated)
        if members:
            groups.append(GroupPlan(title=group.title, options=tuple(members)))

    groups.extend(
        GroupPlan(
            title=new_in_group_title(version),
            options=tuple(options),
            description=NEW_IN_GROUP_DESCRIPTION,
        )
        for version, options in sorted(new_in.items(), key=lambda item: version_key(item[0]))
    )

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


PLUGIN_GROUP_TITLE = "Plugin options"
"""The fallback Group of a loaded plugin's settings: no release added them (#175)."""


def _with_fallbacks(
    planned: list[CategoryPlan],
    schema: Schema,
    mapping: TasksMapping,
    placed: dict[str, _Placement],
    disclosure: Disclosure,
) -> list[CategoryPlan]:
    """Append a Page per uncurated Section: a release adds settings rather than hiding them.

    Keyed on the Section: a Section the mapping never homed has no Page for its Options to
    land on, so each gets one here. An Option a release adds to a Section the mapping
    already homes needs no Page of its own -- `_plan_page` groups it by its `added_in` stamp
    on the home Page. Between them, an uncurated Option is always shown and always flagged.
    """
    homed = mapping.homed_sections

    fallbacks: list[PagePlan] = []
    for section in schema.section_names:
        if section in homed:
            continue
        unplaced = [option for option in schema.section(section) if option.name not in placed]
        visible = [option for option in unplaced if is_visible(option, disclosure)]
        # The hidden tier has no Tasks home at any switch setting (ADR-0013 §5), so it is not
        # withheld: counting it would hint at a setting the switch cannot bring here.
        withheld = sum(1 for option in unplaced if is_withheld(option, disclosure))
        if not visible and not withheld:
            # Every Option here is either curated elsewhere by name or is the hidden tier.
            # Nothing to show, and nothing the user could turn on to see.
            continue
        # Titled with the release that added each Option where it is known (`added_in`,
        # which a runtime-supplemented Option carries too), so a Section only the running
        # Hyprland has is not announced as new in the shipped one.
        # A loaded plugin's setting has no release at all (#175): it is never "new in" one.
        by_version: dict[str, list[ResolvedOption]] = {}
        plugins: list[ResolvedOption] = []
        for option in visible:
            if (
                option.supplement is not None
                and option.supplement.kind is SupplementKind.PLUGIN
            ):
                plugins.append(option)
            else:
                version = option.added_in or schema.hyprland_version
                by_version.setdefault(version, []).append(option)
        groups = tuple(
            GroupPlan(
                title=new_in_group_title(version),
                options=tuple(members),
                description=NEW_IN_GROUP_DESCRIPTION,
            )
            for version, members in sorted(
                by_version.items(), key=lambda item: version_key(item[0])
            )
        ) + ((GroupPlan(title=PLUGIN_GROUP_TITLE, options=tuple(plugins)),) if plugins else ())
        fallbacks.append(
            PagePlan(
                section=f"tasks.new.{section}",
                title=schema.section_title(section),
                groups=groups,
                withheld=withheld,
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
