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
"""

from __future__ import annotations

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


def test_overlay_has_no_entries_for_options_that_do_not_exist(
    generated: generated_module.GeneratedSchema, overlay: overlay_module.Overlay
) -> None:
    """A stale Overlay key is a rename nobody noticed, or a typo silently doing nothing.

    The Overlay is version-independent and keeps entries for retired options on purpose
    (`deprecated_in`), so only entries that were *never* valid are an error.
    """
    known = {option.name for option in generated.options}
    stale = [
        name
        for name, entry in overlay.options.items()
        if name not in known and entry.deprecated_in is None
    ]
    assert not stale, (
        f"overlay entries matching no option in this schema: {stale} "
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
