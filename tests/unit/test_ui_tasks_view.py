"""The curated Tasks view's shape, checked on a machine with no display.

Two different things are asserted here and they fail for different reasons. The **planner**
tests fix behaviour: how a claim resolves, what happens to a Section nobody curated. The
**mapping** tests audit the shipped `tasks.json` against the shipped Schema -- a typo, a
duplicate claim, a Section someone forgot. The second kind is the one that catches a
Hyprland release drifting away from the curation (ADR-0012), which is precisely the drift
the Tasks view is allowed to have and the Config view is not.
"""

from __future__ import annotations

from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.schema import Visibility, load_schema
from hyprtweaker.ui.pages.plan import PagePlan, View, is_visible, plan_config_view
from hyprtweaker.ui.pages.tasks import (
    CategorySpec,
    EntitySpec,
    GroupSpec,
    PageSpec,
    TasksMapping,
    load_tasks_mapping,
    new_in_group_title,
    plan_tasks_view,
)

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
MAPPING = load_tasks_mapping(SCHEMA_DIR)

CATEGORY_TITLES = ("Look", "Windows", "Input", "System")


def page_named(section: str, *, show_advanced: bool = True) -> PagePlan:
    """The one planned Page with this sidebar id. Raises if the curation lost it."""
    return next(
        page
        for category in plan_tasks_view(SCHEMA, MAPPING, show_advanced=show_advanced)
        for page in category.option_pages
        if page.section == section
    )


def placed_options(mapping: TasksMapping = MAPPING, *, show_advanced: bool = True) -> list[str]:
    return [
        option.name
        for category in plan_tasks_view(SCHEMA, mapping, show_advanced=show_advanced)
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
        if is_visible(option, show_advanced=True, view=View.TASKS)
    ]

    assert sorted(placed) == sorted(reachable)
    assert len(placed) == len(set(placed)), "an Option is on two curated Pages"


def test_what_tasks_withholds_is_exactly_what_config_still_reaches() -> None:
    """The safety net, stated as an equation rather than as a promise in a docstring."""
    in_tasks = set(placed_options())
    in_config = {
        option.name
        for plan in plan_config_view(SCHEMA, show_advanced=True)
        for group in plan.groups
        for option in group.options
    }

    assert in_config - in_tasks == {
        option.name for option in SCHEMA if option.visibility is Visibility.HIDDEN
    }


def test_no_curated_page_is_empty() -> None:
    """An empty destination is a curation mistake: it claims Options that do not exist."""
    for category in plan_tasks_view(SCHEMA, MAPPING, show_advanced=True):
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
    categories = plan_tasks_view(SCHEMA, uncurated("cursor"), show_advanced=True)
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


def test_the_fallback_page_joins_the_system_category() -> None:
    categories = plan_tasks_view(SCHEMA, uncurated("cursor"), show_advanced=True)
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
    sections = [
        page.section
        for category in plan_tasks_view(SCHEMA, MAPPING, show_advanced=True)
        for page in category.option_pages
    ]

    assert "tasks.new.debug" not in sections


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


def test_a_page_spanning_sections_leads_each_group_with_the_sections_name() -> None:
    """Four Sections' untitled lead Groups would otherwise merge into one unlabelled heap."""
    titles = [group.title for group in page_named("look.layouts").groups]

    assert titles == [
        SCHEMA.section_title(section)
        for section in ("layout", "dwindle", "master", "scrolling")
    ]
    assert "" not in titles


def test_a_single_section_page_groups_exactly_as_the_config_view_does() -> None:
    """A Page that happens to be one Section should read the same in both Views."""
    decoration = page_named("look.decoration")
    config = next(
        plan
        for plan in plan_config_view(SCHEMA, show_advanced=True)
        if plan.section == "decoration"
    )

    assert [group.title for group in decoration.groups] == [
        group.title for group in config.groups
    ]


def test_entity_destinations_are_passed_through_for_the_shell_to_place() -> None:
    """Their contents come from the model, so the planner only carries the sidebar id."""
    entities = [
        page.section
        for category in plan_tasks_view(SCHEMA, MAPPING, show_advanced=True)
        for page in category.pages
        if isinstance(page, EntitySpec)
    ]

    assert "binds" in entities
    assert "entity:animations" in entities


def test_with_advanced_off_the_curated_pages_withhold_rather_than_drop() -> None:
    """The count is what tells an empty-looking Page to explain itself instead of lying."""
    off = plan_tasks_view(SCHEMA, MAPPING, show_advanced=False)
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
