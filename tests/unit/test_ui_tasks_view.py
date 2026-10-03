"""The curated Tasks view's shape, checked on a machine with no display.

Two different things are asserted here and they fail for different reasons. The **planner**
tests fix behaviour: how a claim resolves, what happens to a Section nobody curated. The
**mapping** tests audit the shipped `tasks.json` against the shipped Schema -- a typo, a
duplicate claim, a Section someone forgot. The second kind is the one that catches a
Hyprland release drifting away from the curation (ADR-0012), which is precisely the drift
the Tasks view is allowed to have and the Config view is not.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR, curated

from hyprtweaker.engine.schema import (
    Schema,
    SupplementKind,
    Visibility,
    load_schema,
    supplement,
)
from hyprtweaker.ui.pages.plan import (
    NEW_IN_GROUP_DESCRIPTION,
    Disclosure,
    PagePlan,
    View,
    is_visible,
    new_in_group_title,
    plan_config_view,
    plan_section,
)
from hyprtweaker.ui.pages.tasks import (
    CategorySpec,
    EntitySpec,
    GroupSpec,
    PageSpec,
    TasksMapping,
    load_tasks_mapping,
    plan_tasks_view,
)

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
MAPPING = load_tasks_mapping(SCHEMA_DIR)

CATEGORY_TITLES = ("Look", "Windows", "Input", "System")


def page_named(section: str, *, show_advanced: bool = True) -> PagePlan:
    """The one planned Page with this sidebar id. Raises if the curation lost it."""
    return next(
        page
        for category in plan_tasks_view(
            SCHEMA, MAPPING, Disclosure(show_advanced=show_advanced)
        )
        for page in category.option_pages
        if page.section == section
    )


def placed_options(mapping: TasksMapping = MAPPING, *, show_advanced: bool = True) -> list[str]:
    return [
        option.name
        for category in plan_tasks_view(
            SCHEMA, mapping, Disclosure(show_advanced=show_advanced)
        )
        for page in category.option_pages
        for group in page.groups
        for option in group.options
    ]


# --- nothing is lost --------------------------------------------------------------------------


def test_every_option_the_tasks_view_may_show_is_on_exactly_one_page() -> None:
    """The completeness claim of #7: a View is a grouping and a naming, not a filter.

    "May show" is the hidden tier's exception and only that one: `debug`, `quirks`,
    `experimental` and `input-capture` have no curated home at any switch setting
    (ADR-0013 §5), and they stay reachable in the Config view, which the next test pins.
    """
    placed = placed_options()
    reachable = [
        option.name
        for option in SCHEMA
        if is_visible(option, Disclosure(show_advanced=True, view=View.TASKS))
    ]

    assert sorted(placed) == sorted(reachable)
    assert len(placed) == len(set(placed)), "an Option is on two curated Pages"


def test_what_tasks_withholds_is_exactly_what_config_still_reaches() -> None:
    """The safety net, stated as an equation rather than as a promise in a docstring."""
    in_tasks = set(placed_options())
    in_config = {
        option.name
        for plan in plan_config_view(SCHEMA, Disclosure(show_advanced=True))
        for group in plan.groups
        for option in group.options
    }

    assert in_config - in_tasks == {
        option.name for option in SCHEMA if option.visibility is Visibility.HIDDEN
    }


def test_no_curated_page_is_empty() -> None:
    """An empty destination is a curation mistake: it claims Options that do not exist."""
    for category in plan_tasks_view(SCHEMA, MAPPING, Disclosure(show_advanced=True)):
        for page in category.option_pages:
            assert page.groups, f"{page.section} builds no Groups"


# --- the mapping itself, audited against the shipped Schema -----------------------------------


def test_the_shipped_mapping_names_only_options_this_hyprland_has() -> None:
    """A typo in `tasks.json` is otherwise invisible: the key simply never matches.

    This failing on a *new* Hyprland is the drift protocol working (ADR-0012) -- upstream
    removed an Option and the curation has to follow. It is a prompt to re-curate, not a
    reason to loosen the check.
    """
    known = {option.name for option in SCHEMA}

    assert sorted(set(MAPPING.option_keys) - known) == []


def test_no_option_is_claimed_by_two_curated_groups() -> None:
    """First claim wins at runtime, so a duplicate is silent without this."""
    keys = MAPPING.option_keys

    assert len(keys) == len(set(keys))


def test_the_four_categories_are_the_ones_the_spec_names() -> None:
    assert tuple(category.title for category in MAPPING.categories) == CATEGORY_TITLES


def test_every_destination_has_a_distinct_sidebar_id() -> None:
    """Two destinations sharing an id would collide in the stack: one becomes unreachable."""
    ids = [
        page.id if isinstance(page, PageSpec) else page.section
        for category in MAPPING.categories
        for page in category.pages
    ]

    assert len(ids) == len(set(ids))


def test_the_hidden_tier_sections_are_deliberately_unhomed() -> None:
    """ADR-0013 §5 beats the design canvas's 19th "Advanced" page, and this is where."""
    assert not MAPPING.homed_sections & {
        "debug",
        "quirks",
        "experimental",
        "input-capture",
    }


# --- degradation ------------------------------------------------------------------------------


def uncurated(section: str) -> TasksMapping:
    """The shipped mapping with one Section's home removed -- a release nobody curated yet."""
    return TasksMapping(
        categories=tuple(
            CategorySpec(
                id=category.id,
                title=category.title,
                pages=tuple(
                    PageSpec(
                        id=page.id,
                        title=page.title,
                        sections=tuple(n for n in page.sections if n != section),
                        groups=page.groups,
                    )
                    if isinstance(page, PageSpec)
                    else page
                    for page in category.pages
                ),
            )
            for category in MAPPING.categories
        )
    )


def test_a_section_the_mapping_never_placed_still_reaches_every_option() -> None:
    mapping = uncurated("cursor")

    assert sorted(placed_options(mapping)) == sorted(placed_options())


def test_an_uncurated_section_lands_in_the_new_in_version_group() -> None:
    """#7's designed degradation: new settings appear flagged, never silently absent."""
    categories = plan_tasks_view(SCHEMA, uncurated("cursor"), Disclosure(show_advanced=True))
    fallback = [
        page
        for category in categories
        for page in category.option_pages
        if page.section == "tasks.new.cursor"
    ]

    assert len(fallback) == 1
    assert [group.title for group in fallback[0].groups] == [
        new_in_group_title(SCHEMA.hyprland_version)
    ]
    assert fallback[0].title == SCHEMA.section_title("cursor")
    assert {option.name for option in fallback[0].groups[0].options} == {
        option.name for option in SCHEMA.section("cursor")
    }


def test_a_section_only_a_newer_hyprland_has_is_titled_with_that_hyprlands_version() -> None:
    """A Section the running 0.58.0 added (#177's supplement) is new in 0.58.0, not in the
    0.56.2 the app shipped: the heading must not name a release that never had it."""
    newer = supplement(
        SCHEMA,
        ({"name": "hotcorners:enabled", "description": "x", "default": True},),
        version="0.58.0",
    )

    categories = plan_tasks_view(newer, MAPPING, Disclosure())
    fallback = next(
        page
        for category in categories
        for page in category.option_pages
        if page.section == "tasks.new.hotcorners"
    )

    assert [(group.title, len(group.options)) for group in fallback.groups] == [
        ("New in 0.58.0", 1)
    ]


def test_a_loaded_plugins_settings_are_plugin_options_not_new_in_any_version() -> None:
    """#175 AC 4 in the Tasks view: a plugin's setting has no release, so it is never
    announced as new in one, and the newer-version description does not describe it."""
    with_plugin = supplement(
        SCHEMA,
        ({"name": "plugin:hyprbars:bar_height", "description": "x", "default": 15},),
        version="0.56.2",
        kind=SupplementKind.PLUGIN,
    )

    categories = plan_tasks_view(with_plugin, MAPPING, Disclosure())
    fallback = next(
        page
        for category in categories
        for page in category.option_pages
        if page.section == "tasks.new.plugin"
    )

    assert [(group.title, group.description) for group in fallback.groups] == [
        ("Plugin options", "")
    ]
    assert [option.name for option in fallback.groups[0].options] == [
        "plugin:hyprbars:bar_height"
    ]


def test_the_fallback_group_says_why_it_exists() -> None:
    """#7 and ADR-0012 both say "flagged", and a version heading is not on its own a flag:
    it reads as *new* rather than as *not yet placed on a curated page*."""
    categories = plan_tasks_view(SCHEMA, uncurated("cursor"), Disclosure(show_advanced=True))
    fallback = next(
        page
        for category in categories
        for page in category.option_pages
        if page.section == "tasks.new.cursor"
    )

    assert fallback.groups[0].description == NEW_IN_GROUP_DESCRIPTION


def test_an_ordinary_curated_group_never_carries_the_new_in_flag() -> None:
    """The flag has to mean something, so it may not appear on Groups that are fine. Their
    description is the one the Overlay curates for the Group, if any (#157)."""
    descriptions = {
        group.title: group.description for group in page_named("look.general").groups
    }

    assert NEW_IN_GROUP_DESCRIPTION not in descriptions.values()
    assert descriptions["Borders"] == ""
    curated_snapping = next(
        group.description
        for group in SCHEMA.section_groups("general")
        if group.title == "Snapping"
    )
    assert curated_snapping
    assert descriptions["Snapping"] == curated_snapping


def test_the_fallback_page_joins_the_system_category() -> None:
    categories = plan_tasks_view(SCHEMA, uncurated("cursor"), Disclosure(show_advanced=True))
    holder = [
        category
        for category in categories
        if any(page.section == "tasks.new.cursor" for page in category.option_pages)
    ]

    assert [category.id for category in holder] == ["system"]
    assert len(categories) == len(CATEGORY_TITLES)


def test_an_uncurated_hidden_section_gets_no_fallback_page_at_all() -> None:
    """`debug` is unhomed by design; a fallback Page for it would put "Crash Hyprland" one
    click from the default view, which ADR-0013 §5 forbids however the switch is set."""
    for show_advanced in (True, False):
        sections = [
            page.section
            for category in plan_tasks_view(
                SCHEMA, MAPPING, Disclosure(show_advanced=show_advanced)
            )
            for page in category.option_pages
        ]

        assert "tasks.new.debug" not in sections


def fallback_page(
    mapping: TasksMapping, section: str, *, show_advanced: bool, schema: Schema = SCHEMA
) -> PagePlan:
    return next(
        page
        for category in plan_tasks_view(
            schema, mapping, Disclosure(show_advanced=show_advanced)
        )
        for page in category.option_pages
        if page.section == f"tasks.new.{section}"
    )


def test_an_uncurated_section_withholds_its_advanced_options_rather_than_dropping_them() -> (
    None
):
    """#136: `cursor` has 21 default options and 1 advanced. With the switch off the page
    shows the 21 and counts the 1, so the hint can say it exists; with it on, all 22 show."""
    off = fallback_page(uncurated("cursor"), "cursor", show_advanced=False)
    on = fallback_page(uncurated("cursor"), "cursor", show_advanced=True)

    assert (off.option_count, off.withheld) == (21, 1)
    assert (on.option_count, on.withheld) == (22, 0)


def test_an_uncurated_all_advanced_section_keeps_a_page_that_counts_what_it_withholds() -> None:
    """`opengl` raised to one advanced setting (no shipped Section is all advanced since the
    review of spec #154). Skipping the page whole left no trace that it exists; the page
    stays, empty of groups, and the count tells it to explain itself."""
    schema = Schema(
        hyprland_version=SCHEMA.hyprland_version,
        options=tuple(
            replace(option, visibility=Visibility.ADVANCED)
            if option.section == "opengl"
            else option
            for option in SCHEMA
        ),
        sections=SCHEMA.sections,
    )
    off = fallback_page(uncurated("opengl"), "opengl", show_advanced=False, schema=schema)
    on = fallback_page(uncurated("opengl"), "opengl", show_advanced=True, schema=schema)

    assert (off.groups, off.withheld) == ((), 1)
    assert (on.option_count, on.withheld) == (1, 0)


def test_a_curated_page_counts_only_what_the_switch_can_reveal() -> None:
    """The hidden tier never renders in Tasks (ADR-0013 §5), so a curated Page that names a
    hidden Option must not hint at a setting the Advanced switch cannot bring there."""
    hidden = next(option for option in SCHEMA if option.visibility is Visibility.HIDDEN)
    advanced = next(option for option in SCHEMA if option.visibility is Visibility.ADVANCED)
    default = next(option for option in SCHEMA if option.visibility is Visibility.DEFAULT)
    mapping = TasksMapping(
        categories=(
            CategorySpec(
                id="system",
                title="System",
                pages=(
                    PageSpec(
                        id="system.mixed",
                        title="Mixed",
                        sections=(),
                        groups=(
                            GroupSpec(
                                title="Mixed",
                                options=(default.name, advanced.name, hidden.name),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )

    for show_advanced, withheld in ((False, 1), (True, 0)):
        page = next(
            page
            for category in plan_tasks_view(
                SCHEMA, mapping, Disclosure(show_advanced=show_advanced)
            )
            for page in category.option_pages
            if page.section == "system.mixed"
        )
        assert page.withheld == withheld, f"show_advanced={show_advanced}"


def test_a_revealed_advanced_option_on_a_fallback_page_is_shown_not_withheld() -> None:
    (option,) = SCHEMA.section("opengl")
    page = next(
        page
        for category in plan_tasks_view(
            SCHEMA,
            uncurated("opengl"),
            Disclosure(show_advanced=False, revealed=frozenset({option.name})),
        )
        for page in category.option_pages
        if page.section == "tasks.new.opengl"
    )

    assert (page.option_count, page.withheld) == (1, 0)


# --- how a claim resolves ---------------------------------------------------------------------


def test_a_named_group_outranks_the_section_that_homes_the_option() -> None:
    """`misc` is homed on Windows & Groups, yet the splash settings render under Look."""
    splash = [
        option.name for group in page_named("look.general").groups for option in group.options
    ]
    windows = [
        option.name
        for group in page_named("windows.windows").groups
        for option in group.options
    ]

    assert "misc:disable_hyprland_logo" in splash
    assert "misc:disable_hyprland_logo" not in windows
    assert "misc:enable_swallow" in windows


def test_a_page_spanning_sections_names_each_group_after_its_section() -> None:
    """Four Sections' Groups on one Page read "<Section> · <Group>", Section by Section in
    the mapping's order, each in its own curated order: "Split direction" alone would not
    say whether it is Dwindle's or Master's."""
    titles = [group.title for group in page_named("look.layouts").groups]

    assert titles == [
        f"{SCHEMA.section_title(section)} · {group.title}"
        for section in ("layout", "dwindle", "master", "scrolling")
        for group in SCHEMA.section_groups(section)
    ]
    assert len(titles) == len(set(titles))


def test_an_untitled_lead_group_on_a_page_spanning_sections_takes_its_sections_title() -> None:
    """With `layout` uncurated its settings form one untitled Group; on Layouts, which spans
    four Sections, that Group is headed "Layout" rather than left blank (`_under_section`)."""
    schema = curated("layout", {}, {})
    page = next(
        page
        for category in plan_tasks_view(schema, MAPPING, Disclosure(show_advanced=True))
        for page in category.option_pages
        if page.section == "look.layouts"
    )
    titles = [group.title for group in page.groups]

    assert "" not in titles
    assert titles[0] == "Layout"
    assert [option.name for option in page.groups[0].options] == [
        "layout:single_window_aspect_ratio",
        "layout:single_window_aspect_ratio_tolerance",
    ]


CURATED_DECORATION = curated(
    "decoration",
    {"Shadow": "Drawn under each window.", "Blur": None},
    {
        "Shadow": ["decoration:shadow:range", "decoration:shadow:enabled"],
        "Blur": ["decoration:blur:enabled"],
    },
)


def page_in(schema: Schema, section: str, mapping: TasksMapping = MAPPING) -> PagePlan:
    return next(
        page
        for category in plan_tasks_view(schema, mapping, Disclosure(show_advanced=True))
        for page in category.option_pages
        if page.section == section
    )


@pytest.mark.parametrize("schema", [SCHEMA, CURATED_DECORATION], ids=["shipped", "curated"])
def test_a_single_section_page_groups_exactly_as_the_config_view_does(schema: Schema) -> None:
    """A Page that happens to be one Section reads the same in both Views: the same
    Groups, in the same order, with the same descriptions and Rows (#157, one sorter)."""
    config = plan_section(schema, "decoration", Disclosure(show_advanced=True))

    assert page_in(schema, "look.decoration").groups == config.groups


def test_curated_groups_lead_a_tasks_page_in_their_sections_order() -> None:
    groups = page_in(CURATED_DECORATION, "look.decoration").groups

    assert [(group.title, group.description) for group in groups[:3]] == [
        ("Shadow", "Drawn under each window."),
        ("Blur", ""),
        ("Other settings", ""),
    ]
    # Curated order first; the uncurated `shadow:*` Options join the heading their path
    # already names, after the curated ones.
    assert [option.name for option in groups[0].options][:3] == [
        "decoration:shadow:range",
        "decoration:shadow:enabled",
        "decoration:shadow:render_power",
    ]


def test_a_page_spanning_sections_lists_them_in_the_mappings_order() -> None:
    """Section by Section as the mapping names them, each in its own curated order."""
    mapping = TasksMapping(
        categories=(
            CategorySpec(
                id="windows",
                title="Windows",
                pages=(PageSpec(id="both", title="Both", sections=("misc", "group")),),
            ),
        )
    )
    schema = curated(
        "misc",
        {"Swallowing": "A terminal hides while the app it opened is open."},
        {"Swallowing": ["misc:swallow_regex", "misc:enable_swallow"]},
    )

    groups = page_in(schema, "both", mapping).groups

    # Ruling A4 of the #148 review: "Miscellaneous" names no thing, so its Groups lead bare;
    # the other Section's still say whose they are, after them.
    assert [(group.title, group.description) for group in groups[:2]] == [
        ("Swallowing", "A terminal hides while the app it opened is open."),
        ("Other settings", ""),
    ]
    titles = [group.title for group in groups]
    assert not any(title.startswith("Miscellaneous") for title in titles)
    first_group = next(i for i, title in enumerate(titles) if title.startswith("Groups · "))
    assert all(title.startswith("Groups · ") for title in titles[first_group:])


def test_entity_destinations_are_passed_through_for_the_shell_to_place() -> None:
    """Their contents come from the model, so the planner only carries the sidebar id."""
    entities = [
        page.section
        for category in plan_tasks_view(SCHEMA, MAPPING, Disclosure(show_advanced=True))
        for page in category.pages
        if isinstance(page, EntitySpec)
    ]

    assert "entity:binds" in entities
    assert "entity:animations" in entities


def test_with_advanced_off_the_curated_pages_withhold_rather_than_drop() -> None:
    """The count is what tells an empty-looking Page to explain itself instead of lying."""
    off = plan_tasks_view(SCHEMA, MAPPING, Disclosure(show_advanced=False))
    shown = sum(
        len(group.options)
        for category in off
        for page in category.option_pages
        for group in page.groups
    )
    withheld = sum(page.withheld for category in off for page in category.option_pages)

    assert withheld > 0
    assert shown + withheld == len(placed_options())


# --- reading the file -------------------------------------------------------------------------


def test_the_mapping_reads_groups_in_the_order_the_curator_wrote_them() -> None:
    keyboard = next(
        page
        for category in MAPPING.categories
        for page in category.pages
        if isinstance(page, PageSpec) and page.id == "input.keyboard"
    )

    assert [group.title for group in keyboard.groups] == [
        "Layout",
        "Typing",
        "Virtual keyboards",
    ]
    assert isinstance(keyboard.groups[0], GroupSpec)


# --- per-Option "New in" (#123) --------------------------------------------------------------


def stamped(**versions: str) -> Schema:
    """The shipped Schema with `added_in` set on some Options (names use `__` for `:`).

    One schema ships today, so a release that added an Option to an already-homed Section
    is built here, not found: the same trick `uncurated` plays for whole Sections.
    """
    by_name = {name.replace("__", ":"): version for name, version in versions.items()}
    return Schema(
        hyprland_version=SCHEMA.hyprland_version,
        options=tuple(
            # A release's added Option has no curation yet: no Overlay Group places it.
            replace(option, added_in=by_name[option.name], group=None, group_order=None)
            if option.name in by_name
            else option
            for option in SCHEMA
        ),
        sections=SCHEMA.sections,
    )


def cursor_page(
    schema: Schema, mapping: TasksMapping = MAPPING, *, show_advanced: bool = True
) -> PagePlan:
    return next(
        page
        for category in plan_tasks_view(
            schema, mapping, Disclosure(show_advanced=show_advanced)
        )
        for page in category.option_pages
        if page.section == "system.cursor"
    )


CURSOR_GROUPS = [group.title for group in SCHEMA.section_groups("cursor")]
"""The cursor Page's curated Groups, in order: a `New in <version>` Group closes after them."""


def group_names(page: PagePlan) -> dict[str, list[str]]:
    return {group.title: [option.name for option in group.options] for group in page.groups}


def with_group_on_cursor_page(*keys: str) -> TasksMapping:
    """The shipped mapping with a curated Group on the cursor Page, beside its home Section."""
    return TasksMapping(
        categories=tuple(
            CategorySpec(
                id=category.id,
                title=category.title,
                pages=tuple(
                    replace(page, groups=(*page.groups, GroupSpec("Pointer jumps", keys)))
                    if isinstance(page, PageSpec) and page.id == "system.cursor"
                    else page
                    for page in category.pages
                ),
            )
            for category in MAPPING.categories
        )
    )


def test_a_new_option_in_a_homed_section_joins_a_new_in_group_on_its_home_page() -> None:
    page = cursor_page(stamped(cursor__no_warps="0.99.0"))

    groups = group_names(page)
    assert groups["New in 0.99.0"] == ["cursor:no_warps"]
    assert [group.description for group in page.groups if group.title == "New in 0.99.0"] == [
        NEW_IN_GROUP_DESCRIPTION
    ]
    assert page.groups[-1].title == "New in 0.99.0"


def test_a_new_option_its_section_has_curated_into_a_group_sits_in_that_group() -> None:
    """ADR-0012:31: added Options live in `New in` Groups *until curated*. Once the
    Overlay names its Group, the stamp no longer moves it (#157)."""
    base = curated("cursor", {"Warping": None}, {"Warping": ["cursor:no_warps"]})
    schema = Schema(
        hyprland_version=base.hyprland_version,
        options=tuple(
            replace(option, added_in="0.99.0") if option.name == "cursor:no_warps" else option
            for option in base
        ),
        sections=base.sections,
    )

    groups = group_names(cursor_page(schema))

    assert groups["Warping"] == ["cursor:no_warps"]
    assert "New in 0.99.0" not in groups


def test_options_with_no_stamp_plan_exactly_as_before() -> None:
    assert cursor_page(stamped()) == cursor_page(SCHEMA)
    assert [g.title for g in cursor_page(SCHEMA).groups] == CURSOR_GROUPS
    assert CURSOR_GROUPS
    assert "" not in CURSOR_GROUPS


def test_each_version_gets_its_own_group_oldest_first() -> None:
    page = cursor_page(stamped(cursor__no_warps="0.99.0", cursor__zoom_rigid="0.58.0"))

    assert [g.title for g in page.groups] == [*CURSOR_GROUPS, "New in 0.58.0", "New in 0.99.0"]
    assert group_names(page)["New in 0.58.0"] == ["cursor:zoom_rigid"]


def test_a_version_is_ordered_numerically_not_as_text() -> None:
    page = cursor_page(stamped(cursor__no_warps="0.100.0", cursor__zoom_rigid="0.58.0"))

    assert [g.title for g in page.groups][len(CURSOR_GROUPS) :] == [
        "New in 0.58.0",
        "New in 0.100.0",
    ]


def test_curating_a_new_option_onto_its_home_page_removes_it_from_the_fallback() -> None:
    """The Section loop used to keep an Option a same-Page group had placed: shown twice."""
    mapping = with_group_on_cursor_page("cursor:no_warps")
    page = cursor_page(stamped(cursor__no_warps="0.99.0"), mapping)

    groups = group_names(page)
    assert "New in 0.99.0" not in groups
    assert groups["Pointer jumps"] == ["cursor:no_warps"]
    assert sum(names.count("cursor:no_warps") for names in groups.values()) == 1


def test_a_same_page_curated_group_does_not_duplicate_an_unstamped_option() -> None:
    page = cursor_page(SCHEMA, with_group_on_cursor_page("cursor:no_warps"))

    shown = [name for names in group_names(page).values() for name in names]
    assert shown.count("cursor:no_warps") == 1
    assert len(shown) == len(set(shown))


def test_an_advanced_new_option_is_withheld_and_counted_until_advanced_is_on() -> None:
    schema = stamped(cursor__use_cpu_buffer="0.99.0")

    closed = cursor_page(schema, show_advanced=False)
    assert "New in 0.99.0" not in group_names(closed)
    assert closed.withheld == cursor_page(SCHEMA, show_advanced=False).withheld

    opened = cursor_page(schema, show_advanced=True)
    assert group_names(opened)["New in 0.99.0"] == ["cursor:use_cpu_buffer"]
    assert opened.withheld == 0


def test_a_new_option_is_still_on_exactly_one_page() -> None:
    schema = stamped(cursor__no_warps="0.99.0", general__gaps_in="0.99.0")
    names = [
        option.name
        for category in plan_tasks_view(schema, MAPPING, Disclosure(show_advanced=True))
        for page in category.option_pages
        for group in page.groups
        for option in group.options
    ]

    assert sorted(names) == sorted(placed_options())


def test_a_group_on_the_page_its_section_names_is_not_prefixed_again() -> None:
    """#148 hand-test 7, ruling A4: Rendering read "Rendering · Performance and quality"
    beside unprefixed groups from Miscellaneous, while "OpenGL · NVIDIA workarounds" says
    something the bare title would not."""
    titles = [group.title for group in page_named("system.rendering").groups]

    assert "Performance and quality" in titles
    assert not any(title.startswith(("Rendering · ", "Miscellaneous · ")) for title in titles)
    assert any(title.startswith("OpenGL · ") for title in titles)
