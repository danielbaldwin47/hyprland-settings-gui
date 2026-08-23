"""The full state re-read: what the compositor says becomes what the model holds.

The interesting cases are all about *not* believing a reply: a sentinel is not a value, an
unreadable answer is not an empty one, and an Option the model deliberately nulled is not
something to re-derive from whatever the compositor prints for that marker.
"""

from __future__ import annotations

import pytest
from _fake_hyprland import FakeHyprland, option_reply, run_with_fake
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.apply import ReRead, app_owned_options, read_state
from hyprtweaker.engine.ipc import CommandClient, IpcTimeout
from hyprtweaker.engine.model import UNSET, ConfigModel, CssGaps
from hyprtweaker.engine.schema import ResolvedOption, load_schema
from hyprtweaker.engine.state import Manifest, ModuleRecord

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)

ROUNDING = "decoration:rounding"
GAPS_IN = "general:gaps_in"
KB_VARIANT = "input:kb_variant"
FLOAT_GAPS = "general:float_gaps"


def model() -> ConfigModel:
    return ConfigModel(SCHEMA)


def options(*names: str) -> tuple[ResolvedOption, ...]:
    return tuple(SCHEMA[name] for name in names)


def reread(conversation: dict[str, str], target: ConfigModel, *names: str) -> ReRead:
    """Run one re-read of `names` against a scripted compositor."""

    async def scenario(fake: FakeHyprland) -> ReRead:
        return await read_state(target, CommandClient(fake.instance), options(*names))

    return run_with_fake(scenario, FakeHyprland(conversation))


# --- adopting ------------------------------------------------------------------------------


def test_a_value_the_live_config_sets_lands_in_the_model() -> None:
    target = model()
    conversation = {f"j/getoption {ROUNDING}": option_reply(SCHEMA[ROUNDING], 12)}

    result = reread(conversation, target, ROUNDING)

    assert target.get(ROUNDING) == 12
    assert result.adopted == (ROUNDING,)
    assert result.changed


def test_a_key_the_live_config_no_longer_sets_is_unset() -> None:
    target = model()
    target.set(ROUNDING, 12)
    conversation = {
        f"j/getoption {ROUNDING}": option_reply(SCHEMA[ROUNDING], 0, live_set=False)
    }

    result = reread(conversation, target, ROUNDING)

    assert target.get(ROUNDING) is UNSET
    assert result.cleared == (ROUNDING,)


def test_a_complex_type_round_trips_through_its_own_parser() -> None:
    target = model()
    conversation = {
        f"j/getoption {GAPS_IN}": option_reply(SCHEMA[GAPS_IN], CssGaps(1, 2, 3, 4))
    }

    reread(conversation, target, GAPS_IN)

    assert target.get(GAPS_IN) == CssGaps(1, 2, 3, 4)


# --- not believing the reply ----------------------------------------------------------------


def test_a_sentinel_reply_becomes_explicit_null_not_the_marker_text() -> None:
    """`getoption` answers `[[EMPTY]]` for an unset string -- verified on a live socket.

    Adopting it verbatim is prototype #8's most damaging defect class, one layer deeper:
    the marker would reach the model, the Row, and eventually the user's Lua.
    """
    target = model()
    conversation = {f"j/getoption {KB_VARIANT}": option_reply(SCHEMA[KB_VARIANT], "[[EMPTY]]")}

    reread(conversation, target, KB_VARIANT)

    assert target.is_set(KB_VARIANT)
    assert target.get(KB_VARIANT) is None
    assert target.display(KB_VARIANT) is None


def test_the_curated_null_value_is_recognised_as_no_value_too() -> None:
    target = model()
    conversation = {f"j/getoption {KB_VARIANT}": option_reply(SCHEMA[KB_VARIANT], "")}

    reread(conversation, target, KB_VARIANT)

    assert target.get(KB_VARIANT) is None


def test_an_explicit_null_is_never_re_derived_from_what_the_compositor_prints() -> None:
    """`general:float_gaps` emits `-1`, meaning "same as the outer gaps".

    Whatever the compositor reports for that marker is its own interpretation. Parsing it
    back would turn one deliberate "no value" into four gaps of minus one.
    """
    target = model()
    target.set_null(FLOAT_GAPS)
    conversation = {
        f"j/getoption {FLOAT_GAPS}": option_reply(SCHEMA[FLOAT_GAPS], CssGaps(-1, -1, -1, -1))
    }

    result = reread(conversation, target, FLOAT_GAPS)

    assert target.get(FLOAT_GAPS) is None
    assert result.adopted == ()


def test_an_unreadable_reply_leaves_the_model_alone() -> None:
    target = model()
    target.set(GAPS_IN, 8)
    conversation = {f"j/getoption {GAPS_IN}": '{"option": "general:gaps_in", "set": true}'}

    result = reread(conversation, target, GAPS_IN)

    assert target.get(GAPS_IN) == CssGaps(8, 8, 8, 8)
    assert result.unreadable == (GAPS_IN,)
    assert not result.changed


def test_an_option_this_compositor_never_heard_of_is_reported_not_dropped() -> None:
    target = model()
    target.set(ROUNDING, 12)
    conversation = {f"j/getoption {ROUNDING}": "no such option"}

    result = reread(conversation, target, ROUNDING)

    assert target.get(ROUNDING) == 12
    assert result.unknown == (ROUNDING,)


def test_a_dead_socket_raises_rather_than_reporting_everything_as_unset() -> None:
    """Silence is not "nothing is set". Clearing the model on a dropped socket would make
    the next write delete every Module the user has."""
    target = model()
    target.set(ROUNDING, 12)

    async def scenario(fake: FakeHyprland) -> None:
        client = CommandClient(fake.instance, timeout=0.05)
        with pytest.raises(IpcTimeout):
            await read_state(target, client, options(ROUNDING))

    run_with_fake(scenario, FakeHyprland(never_answer=True))

    assert target.get(ROUNDING) == 12


# --- ownership ------------------------------------------------------------------------------


def test_a_fresh_install_owns_nothing_so_it_adopts_nothing() -> None:
    """Story 13: a user with no config starts with everything Unset."""
    manifest = Manifest(app_version="0.0.0", schema_version=SAMPLE_VERSION)

    assert app_owned_options(SCHEMA, manifest) == ()


def test_ownership_is_per_option_not_per_section() -> None:
    """The over-claim this record exists to end.

    `general:gaps_in` is the app's. `general:border_size` sits in the same Section and the
    same file's Section, but the app never wrote it -- so a re-read must not adopt it,
    render it as the app's own, and emit it into the app's Module on the next write. That
    would let the app's copy outlive the `user.lua` line the user later deletes.
    """
    manifest = Manifest(
        app_version="0.0.0",
        schema_version=SAMPLE_VERSION,
        modules={
            "options/general.lua": ModuleRecord.of("-- general", [GAPS_IN]),
            # Not an options Module, and not this writer's to claim.
            "binds.lua": ModuleRecord.of("-- binds", ["binds:workspace_back_and_forth"]),
        },
    )

    owned = app_owned_options(SCHEMA, manifest)

    assert [option.name for option in owned] == [GAPS_IN]


def test_ownership_is_ordered_by_hyprlands_own_declaration_order() -> None:
    manifest = Manifest(
        app_version="0.0.0",
        schema_version=SAMPLE_VERSION,
        modules={
            "options/decoration.lua": ModuleRecord.of("-- d", [ROUNDING]),
            "options/general.lua": ModuleRecord.of("-- g", [GAPS_IN]),
        },
    )

    owned = app_owned_options(SCHEMA, manifest)

    assert [option.order for option in owned] == sorted(option.order for option in owned)
    assert {option.name for option in owned} == {ROUNDING, GAPS_IN}
