"""The Overlay completeness test: an uncurated Option fails the build (ADR-0011).

Prototype #8 is the argument for this file. Generating a page from `descriptions` alone
produces 353 working, type-correct rows -- and the ones it gets wrong are the ones a user
reaches for first. `input:accel_profile` defaults to `[[EMPTY]]`, meaning "whatever
libinput decides", and the generated ComboRow showed **`adaptive`, selected**: the page
confidently stating something false. Twenty-three options had that defect, thirteen more
rendered as blank rows with a config key for a title.

None of that is detectable at runtime, and none of it is fixable by the generator, because
the missing facts live in wiki prose and human judgement. So the Generated schema raises a
`CurationFlag` wherever it knows it is ignorant, and this test refuses to let a flag go
unanswered. The same prototype hand-curated 126 options and still missed two titles until
a script counted them -- which is exactly why this is a test and not a review checklist.

**Groups** (#157, #158). Every Option of every Section sits in a Group its Section
declares, so no Page renders as one flat list of unrelated Rows. The Groups are written in
`tools/overlay_groups.toml` and applied by `tools/curate_overlay.py`, never by hand. A table
row follows these conventions; the mechanical ones are tests below:

- A Section of one to four Options gets a single titled Group.
- A Group usually holds 3 to 12 Options. Split above about 12 where there is a natural seam.
- A title is one to three words in sentence case and names what the user adjusts. It never
  repeats its Section's title, never says "Group" or "Groups", is unique within its
  Section, and reads well as "<Section> · <title>" on a Tasks Page spanning several
  Sections ("Groups · Tab bar", "Miscellaneous · Swallowing").
- A description is optional: one sentence of at most 120 characters, saying "setting",
  "Hyprland" and "this version", never "schema", "overlay", "option" or "config variable"
  (the one exception is "XKB option", the term the user types). Omit it when the title
  says it all.
- Groups run in the order a person works down the Page, most-used first; a Group whose
  members are all advanced goes last.
- Stub-tree sub-prefixes are not Groups. A Group is what a person adjusts together:
  "Scrolling" spans `input:*` and `input:touchpad:*`.
- Two Rows in one Group never share a title: a cross-cutting Group retitles its members
  where they would ("Mouse scroll speed", "Touchpad scroll speed").
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from hyprtweaker.engine.schema import (
    CurationFlag,
    OverlayEntry,
    Schema,
    Visibility,
    Widget,
)
from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema import overlay as overlay_module
from hyprtweaker.engine.schema.resolve import available_versions
from hyprtweaker.ui.pages.plan import Disclosure, plan_config_view
from hyprtweaker.ui.pages.tasks import load_tasks_mapping, plan_tasks_view

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import overlay_help

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "data" / "schema"

# Which Overlay fields answer which flag. `widget` answers every one of them: it is the
# human saying "I looked at this row and the generated control is what I want", which is a
# real curation decision even when it changes nothing.
FLAG_SATISFIED_BY: dict[CurationFlag, tuple[str, ...]] = {
    CurationFlag.NEEDS_NULLABLE: ("nullable",),
    CurationFlag.NEEDS_LABELS: ("labels", "widget"),
    CurationFlag.NEEDS_KNOWN_VALUES: ("known_values", "widget"),
    CurationFlag.NEEDS_RANGE: ("range", "widget"),
    CurationFlag.NEEDS_WIDGET: ("widget",),
}


def schema_versions() -> list[str]:
    return list(available_versions(SCHEMA_DIR))


@pytest.fixture(scope="module")
def overlay() -> overlay_module.Overlay:
    return overlay_module.load(SCHEMA_DIR / "overlay.json")


@pytest.fixture(params=schema_versions(), scope="module")
def version(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="module")
def generated(version: str) -> generated_module.GeneratedSchema:
    return generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")


@pytest.fixture(scope="module")
def schema(
    generated: generated_module.GeneratedSchema, overlay: overlay_module.Overlay
) -> Schema:
    return Schema.merge(generated, overlay)


def test_there_are_schemas_to_check() -> None:
    """Guards the suite: an empty schema directory must not read as full coverage."""
    versions = schema_versions()
    assert versions, f"no hyprland-<ver>.json files in {SCHEMA_DIR}"
    assert (SCHEMA_DIR / "overlay.json").is_file()


def test_support_window_is_latest_plus_previous() -> None:
    """`data/schema/` carries at most two schemas (ADR-0012 support window)."""
    versions = schema_versions()
    assert len(versions) <= 2, (
        f"shipping {len(versions)} schemas {versions}, but the support window is "
        "latest + previous -- delete the older files, git history keeps them"
    )


def test_every_option_has_a_curated_title(
    generated: generated_module.GeneratedSchema, overlay: overlay_module.Overlay
) -> None:
    """A generated title reads like a config key, so every Option carries a written one.

    `derive_title` exists as a fallback for options a *newer* Hyprland added that no
    shipped Overlay has seen (ADR-0012). Inside a shipped schema it is never the answer:
    prototype #8 measured 126 of 126 curated options needing a human-written title, and
    auto-title-casing the leaf still gets `col.active_border` wrong.
    """
    missing = [
        option.name
        for option in generated.options
        if not (entry := overlay.entry(option.name)) or not entry.title
    ]
    assert not missing, (
        f"{len(missing)} option(s) have no curated title in overlay.json: {missing[:10]}"
    )


def test_every_curation_flag_is_answered(
    generated: generated_module.GeneratedSchema, overlay: overlay_module.Overlay
) -> None:
    """Each flag the generator raised must be answered by an Overlay field."""
    failures: list[str] = []

    for option in generated.options:
        entry = overlay.entry(option.name) or OverlayEntry()
        for flag in option.curation_flags:
            fields = FLAG_SATISFIED_BY[flag]
            if not any(getattr(entry, field) is not None for field in fields):
                failures.append(
                    f"{option.name}: {flag.value} unanswered (set one of {', '.join(fields)})"
                )

    assert not failures, "uncurated options:\n  " + "\n  ".join(failures)


def test_nullable_options_have_a_null_label(schema: Schema) -> None:
    """A nullable row with no label renders its sentinel (ADR-0013).

    `null_label` is what the entry's placeholder text becomes, so a missing one is how
    `[[EMPTY]]` reaches the screen.
    """
    missing = [option.name for option in schema if option.nullable and not option.null_label]
    assert not missing, f"nullable options with no null_label: {missing}"


def test_every_option_resolves_to_widget_title_and_nullability(schema: Schema) -> None:
    """The guarantee the rest of the app relies on (ticket #50 acceptance criterion)."""
    for option in schema:
        assert isinstance(option.widget, Widget), option.name
        assert option.title, option.name
        assert option.title != option.name, f"{option.name}: title is the raw key"
        assert isinstance(option.nullable, bool), option.name
        assert isinstance(option.visibility, Visibility), option.name


def stale_overlay_keys(
    deprecated_in: Mapping[str, str | None], shipped: Iterable[Iterable[str]]
) -> list[str]:
    """Overlay keys no shipped schema has and no `deprecated_in` explains (#186).

    The Overlay is version-independent (ADR-0012): an entry for an Option the previous
    release lacks, or the latest one dropped, is harmless. So "exists" is the union over
    every shipped schema, and `deprecated_in` (removed in) stays unset for an Option a
    later release *added*, where it would record a false fact.
    """
    known = {name for names in shipped for name in names}
    return [
        name for name, removed in deprecated_in.items() if name not in known and removed is None
    ]


def test_an_overlay_key_is_stale_only_when_no_shipped_schema_has_it() -> None:
    previous, latest = {"a:only_before", "a:both"}, {"a:both", "a:only_after"}
    entries = {
        "a:only_before": None,
        "a:only_after": None,
        "a:both": None,
        "a:removed": "0.56.1",
        "a:typo": None,
    }

    assert stale_overlay_keys(entries, (previous, latest)) == ["a:typo"]


def test_overlay_has_no_entries_for_options_that_exist_in_no_shipped_schema(
    overlay: overlay_module.Overlay,
) -> None:
    """A stale Overlay key is a rename nobody noticed, or a typo silently doing nothing."""
    stale = stale_overlay_keys(
        {name: entry.deprecated_in for name, entry in overlay.options.items()},
        (
            {o.name for o in generated_module.load(SCHEMA_DIR / f"hyprland-{v}.json").options}
            for v in schema_versions()
        ),
    )
    assert not stale, (
        f"overlay entries matching no option in any shipped schema: {stale} "
        "(set deprecated_in if the option was removed by a release)"
    )


def test_depends_on_targets_exist(schema: Schema) -> None:
    """A dependency badge must be able to name -- and navigate to -- its controlling Row."""
    broken = [
        f"{option.name} -> {option.depends_on.option}"
        for option in schema
        if option.depends_on is not None and option.depends_on.option not in schema
    ]
    assert not broken, f"depends_on pointing at unknown options: {broken}"


def test_depends_on_is_not_self_referential(schema: Schema) -> None:
    self_referential = [
        option.name
        for option in schema
        if option.depends_on is not None and option.depends_on.option == option.name
    ]
    assert not self_referential, f"options depending on themselves: {self_referential}"


def test_hidden_sections_are_hidden(schema: Schema) -> None:
    """`debug`, `quirks`, `experimental` and `input-capture` never show by default.

    27 options of raw compositor plumbing sat at full weight next to real settings in
    prototype #8. The tier is set per Section in the Overlay rather than repeated on each
    option, so this checks the wiring actually reaches every one of them.
    """
    for section in ("debug", "quirks", "experimental", "input-capture"):
        options = schema.section(section)
        assert options, f"no options found in section {section}"
        for option in options:
            assert option.visibility is Visibility.HIDDEN, (
                f"{option.name} is in {section} but resolves to {option.visibility}"
            )


def test_labels_cover_the_values_they_describe(schema: Schema) -> None:
    """Curated labels for a bounded int must name every value in range.

    A partially-labelled combo silently drops the values nobody wrote text for, which is
    how an option loses a setting the user had.
    """
    failures: list[str] = []

    for option in schema:
        if not option.labels or option.range is None:
            continue
        low, high = option.range.min, option.range.max
        if low is None or high is None or high - low > 16:
            continue
        expected = {str(value) for value in range(int(low), int(high) + 1)}
        if missing := expected - set(option.labels):
            failures.append(f"{option.name}: no label for {sorted(missing)}")

    assert not failures, "incomplete label sets:\n  " + "\n  ".join(failures)


def test_known_values_include_the_default(schema: Schema) -> None:
    """A combo whose list omits its own default cannot show an unmodified Option."""
    failures = [
        f"{option.name}: default {option.default!r} not in {option.known_values.values}"
        for option in schema
        if option.known_values is not None
        and not option.known_values.open
        and option.default is not None
        and option.default not in option.known_values.values
    ]
    assert not failures, "\n  ".join(failures)


def test_labelled_string_values_are_known_values(schema: Schema) -> None:
    """String labels must describe values the combo can actually offer."""
    failures: list[str] = []

    for option in schema:
        if not option.labels or option.known_values is None:
            continue
        if all(label.lstrip("-").isdigit() for label in option.labels):
            continue
        if unknown := set(option.labels) - set(option.known_values.values):
            failures.append(f"{option.name}: labels for unknown values {sorted(unknown)}")

    assert not failures, "\n  ".join(failures)


# --- Groups (#157) --------------------------------------------------------------------------

HYPRLAND_SECTION_ORDER = (
    "general",
    "decoration",
    "animations",
    "input",
    "gestures",
    "group",
    "misc",
    "binds",
    "xwayland",
    "opengl",
    "render",
    "cursor",
    "ecosystem",
    "debug",
    "layout",
    "dwindle",
    "master",
    "scrolling",
    "experimental",
    "input-capture",
    "quirks",
)
"""The order Hyprland declares its Sections in, which the sidebar keeps (ADR-0013)."""

BANNED_WORDS = overlay_help.BANNED
"""Words a Group's title or description, a help line or a combo label never uses (spec #154
S6): the user sees settings, not the app's data model. One list, `tools/overlay_help.py`'s.
"XKB option" is the one allowed use, the term the user types."""

GROUP_WORD = re.compile(r"\bgroups?\b", re.IGNORECASE)
"""A title saying "Group" names the widget rather than what it holds; on the `group`
Section's Page it reads "Groups · Group ..." on the Tasks view."""


def ungrouped(schema: Schema) -> list[str]:
    """Options of a curated Section with no Group their Section declares.

    Exempt: an Option this schema's own release added (`added_in` is its version) and no
    curation has placed yet. ADR-0012 lets placement lag the release check, and it shows in
    a `New in <version>` Group meanwhile; stamps carry forward, so the next release's schema
    no longer exempts it and the lag is one release at most.
    """
    return [
        option.name
        for option in schema
        if option.group not in {group.title for group in schema.section_groups(option.section)}
        and not (option.group is None and option.added_in == schema.hyprland_version)
    ]


def test_every_option_sits_in_a_group_its_section_declares(schema: Schema) -> None:
    missing = ungrouped(schema)

    assert not missing, (
        f"{len(missing)} option(s) have no Group: {missing[:10]}. Place "
        "them in tools/overlay_groups.toml, then run tools/curate_overlay.py"
    )


def release(schema: Schema, name: str, *, added_in: str) -> Schema:
    """`schema` as though `name` were added by Hyprland `added_in` and not yet curated."""
    return Schema(
        hyprland_version=schema.hyprland_version,
        options=tuple(
            replace(option, added_in=added_in, group=None, group_order=None)
            if option.name == name
            else option
            for option in schema
        ),
        sections=schema.sections,
    )


def test_an_option_this_release_added_may_wait_for_its_group(schema: Schema) -> None:
    added = release(schema, "input:kb_layout", added_in=schema.hyprland_version)

    assert ungrouped(added) == []


def test_an_option_an_earlier_release_added_may_not(schema: Schema) -> None:
    added = release(schema, "input:kb_layout", added_in="0.1.0")

    assert ungrouped(added) == ["input:kb_layout"]


def test_an_option_with_no_stamp_may_not(schema: Schema) -> None:
    unstamped = release(schema, "input:kb_layout", added_in="0.1.0")
    unstamped = Schema(
        hyprland_version=unstamped.hyprland_version,
        options=tuple(replace(option, added_in=None) for option in unstamped),
        sections=unstamped.sections,
    )

    assert ungrouped(unstamped) == ["input:kb_layout"]


def test_every_declared_group_has_an_option(schema: Schema) -> None:
    empty = [
        (section, group.title)
        for section in schema.section_names
        for group in schema.section_groups(section)
        if not any(option.group == group.title for option in schema.section(section))
    ]

    assert not empty, f"declared Groups no Option names: {empty}"


def test_a_small_section_has_one_titled_group_and_a_large_one_splits(schema: Schema) -> None:
    """Five or more settings split into at least two Groups; one to four stay in one."""
    wrong = []
    for section in schema.section_names:
        size = len(schema.section(section))
        count = len(schema.section_groups(section))
        if (size <= 4 and count != 1) or (size > 4 and count < 2):
            wrong.append((section, size, count))

    assert not wrong, f"Sections whose Group count breaks the size convention: {wrong}"


def curated_groups(schema: Schema) -> list[tuple[str, str, str | None]]:
    return [
        (section, group.title, group.description)
        for section in schema.section_names
        for group in schema.section_groups(section)
    ]


def test_group_titles_are_short_and_never_repeat_their_page(schema: Schema) -> None:
    bad = [
        (section, title)
        for section, title, _ in curated_groups(schema)
        if not 1 <= len(title.split()) <= 3
        or title != title[0].upper() + title[1:]
        or title.casefold() == schema.section_title(section).casefold()
        or BANNED_WORDS.search(title)
        or GROUP_WORD.search(title)
    ]

    assert not bad, f"Group titles breaking the conventions in this module's docstring: {bad}"


def test_group_descriptions_are_one_short_sentence_in_the_apps_voice(schema: Schema) -> None:
    bad = [
        (section, title, description)
        for section, title, description in curated_groups(schema)
        if description is not None
        and (
            len(description) > 120
            or not description.endswith(".")
            or ". " in description
            or BANNED_WORDS.search(description.replace("XKB option", ""))
        )
    ]

    assert not bad, f"Group descriptions breaking the conventions: {bad}"


def test_no_two_rows_in_a_group_share_a_title(schema: Schema) -> None:
    """Two "Natural scrolling" Rows under "Scrolling" leave the user guessing which is which."""
    seen: dict[tuple[str, str, str], str] = {}
    clashes = []
    for option in schema:
        if option.group is None:
            continue
        key = (option.section, option.group, option.title)
        if key in seen:
            clashes.append((seen[key], option.name))
        seen[key] = option.name

    assert not clashes, f"Rows sharing a title within one Group: {clashes}"


def test_combo_labels_are_in_the_apps_voice(schema: Schema) -> None:
    """A label is copy the user reads as much as a title is (spec #154 S6)."""
    offending = [
        (option.name, label)
        for option in schema
        for label in (option.labels or {}).values()
        if BANNED_WORDS.search(label.replace("XKB option", ""))
    ]

    assert not offending, f"Labels using a word the app does not use: {offending}"


def test_no_page_in_either_view_repeats_a_group_heading(schema: Schema) -> None:
    """Two "Behaviour" headings on one Page read as one Group split in two."""
    shown = Disclosure(show_advanced=True)
    pages = [*plan_config_view(schema, shown)] + [
        page
        for category in plan_tasks_view(schema, load_tasks_mapping(SCHEMA_DIR), shown)
        for page in category.option_pages
    ]
    repeated = [
        (page.section, title)
        for page in pages
        for title in {group.title for group in page.groups}
        if [group.title for group in page.groups].count(title) > 1
    ]

    assert not repeated, f"Pages repeating a Group heading: {repeated}"


def test_sections_keep_hyprlands_declaration_order(schema: Schema) -> None:
    """Curating a Page's Groups never reorders the sidebar (`order` is within a Group)."""
    assert schema.section_names == HYPRLAND_SECTION_ORDER
    assert tuple(page.section for page in plan_config_view(schema)) == HYPRLAND_SECTION_ORDER


def test_every_option_lands_in_exactly_one_group_in_both_views(schema: Schema) -> None:
    """Curated Groups reshape a Page and never drop or repeat a Row (#123's double render).

    The Config view shows every Option; the Tasks view every one but the hidden tier, which
    has no curated home (ADR-0013 §5)."""
    shown = Disclosure(show_advanced=True)
    config = [
        option.name
        for page in plan_config_view(schema, shown)
        for group in page.groups
        for option in group.options
    ]
    tasks = [
        option.name
        for category in plan_tasks_view(schema, load_tasks_mapping(SCHEMA_DIR), shown)
        for page in category.option_pages
        for group in page.groups
        for option in group.options
    ]

    assert sorted(config) == sorted(option.name for option in schema)
    assert sorted(tasks) == sorted(
        option.name for option in schema if option.visibility is not Visibility.HIDDEN
    )
