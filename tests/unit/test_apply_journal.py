"""The Apply transaction's Journal half, against a scripted compositor.

Real `Writer`, real temp App dir, real sockets, real Journal -- only the compositor is faked.
What these pin down is the bracket ADR-0010 and ADR-0016 need between them: the Snapshot is
taken while the previous bytes still exist, and the entry is written once the transaction
knows whether it **confirmed**, which is the flag Last known good is selected by.

`confirmed` is stricter than `ok` on purpose, and the test that says so is the one worth
reading twice: a transaction whose keys the compositor would not answer about has verified
nothing, and promoting its bytes to Last known good would make the app's idea of "good" a
state nobody ever checked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from _fake_hyprland import (
    NO_SUCH_OPTION,
    FakeHyprland,
    model_conversation,
    option_reply,
    run_with_fake,
)
from _support import SAMPLE_APP_VERSION, SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult, ApplyTransaction
from hyprtweaker.engine.ipc import CommandClient, EventStream
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ENTRYPOINT_NAME, ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.state import Journal
from hyprtweaker.engine.writer import Writer

GAPS_IN = "general:gaps_in"
ROUNDING = "decoration:rounding"
GENERAL_MODULE = "options/general.lua"
DECORATION_MODULE = "options/decoration.lua"


def fresh_model() -> ConfigModel:
    return ConfigModel(load_schema(SAMPLE_VERSION, SCHEMA_DIR))


def with_transaction(
    tmp_path: Path,
    model: ConfigModel,
    scenario: Callable[[ApplyTransaction, FakeHyprland], Awaitable[None]],
    *,
    conversation: dict[str, str] | None = None,
    settle_timeout: float = 0.05,
) -> Journal:
    """Drive one `ApplyTransaction` with a Journal attached, and hand back the Journal.

    A `scenario` rather than a single `run`, because most of what is interesting here is
    about the *second* transaction: the bytes one write leaves are the bytes the next one
    snapshots, and a single-apply harness cannot see that chain at all.
    """
    paths = ConfigPaths.rooted_at(tmp_path)
    journal = Journal(paths)
    fake = FakeHyprland(
        conversation if conversation is not None else model_conversation(model),
        reload_emits_event=True,
    )

    async def run(started: FakeHyprland) -> None:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            await scenario(
                ApplyTransaction(
                    model=model,
                    writer=Writer(paths, SAMPLE_APP_VERSION),
                    client=CommandClient(started.instance),
                    events=events,
                    journal=journal,
                    reload_timeout=0.5,
                    settle_timeout=settle_timeout,
                ),
                started,
            )

    run_with_fake(run, fake)
    return journal


def one_apply(
    tmp_path: Path, model: ConfigModel, *keys: str, **kwargs: object
) -> tuple[Journal, list[ApplyResult]]:
    results: list[ApplyResult] = []

    async def scenario(transaction: ApplyTransaction, _fake: FakeHyprland) -> None:
        results.append(await transaction.run(keys))

    journal = with_transaction(tmp_path, model, scenario, **kwargs)  # type: ignore[arg-type]
    return journal, results


# --- what one transaction records -------------------------------------------------------------


def test_a_clean_transaction_journals_its_change_and_confirms_it(tmp_path: Path) -> None:
    model = fresh_model()
    model.set(GAPS_IN, 6)

    journal, results = one_apply(tmp_path, model, GAPS_IN)

    assert results[0].outcome is ApplyOutcome.OK
    entries = journal.entries()
    assert len(entries) == 1
    assert entries[0].confirmed
    assert entries[0].keys == (GAPS_IN,)
    assert set(entries[0].modules) == {GENERAL_MODULE, ENTRYPOINT_NAME}


def test_the_first_write_of_a_module_records_it_as_absent_before(tmp_path: Path) -> None:
    model = fresh_model()
    model.set(GAPS_IN, 6)

    journal, _ = one_apply(tmp_path, model, GAPS_IN)

    change = journal.entries()[0].change(GENERAL_MODULE)
    assert change is not None and change.before is None
    assert b"gaps_in" in (journal.snapshot(change.after) or b"")


def test_the_bytes_one_write_leaves_are_the_bytes_the_next_one_snapshots(
    tmp_path: Path,
) -> None:
    """The chain ADR-0010's auto-revert walks back along: the pre-write Snapshot of a
    transaction *is* the state the previous, confirmed one left live."""
    model = fresh_model()
    model.set(GAPS_IN, 6)

    async def scenario(transaction: ApplyTransaction, fake: FakeHyprland) -> None:
        await transaction.run([GAPS_IN])
        model.set(GAPS_IN, 12)
        fake.conversation.update(model_conversation(model))
        await transaction.run([GAPS_IN])

    journal = with_transaction(tmp_path, model, scenario)

    first, second = journal.entries()
    assert first.change(GENERAL_MODULE).after == second.change(GENERAL_MODULE).before  # type: ignore[union-attr]
    # `gaps_in` is a css-gaps Option, so its Lua is a four-side table, not a number.
    assert b"top = 6" in (journal.snapshot(second.change(GENERAL_MODULE).before) or b"")  # type: ignore[union-attr]


def test_last_known_good_is_what_a_confirmed_write_left(tmp_path: Path) -> None:
    model = fresh_model()
    model.set(GAPS_IN, 6)

    journal, _ = one_apply(tmp_path, model, GAPS_IN)

    good = journal.last_known_good(GENERAL_MODULE)
    assert good is not None
    assert good.data == (tmp_path / "hypr" / "hyprtweaker" / GENERAL_MODULE).read_bytes()


def test_last_known_good_records_the_options_those_bytes_set(tmp_path: Path) -> None:
    """Restoring puts the model back too, and the app cannot learn that from the Lua (#62)."""
    model = fresh_model()
    model.set(GAPS_IN, 6)

    journal, _ = one_apply(tmp_path, model, GAPS_IN)

    good = journal.last_known_good(GENERAL_MODULE)
    assert good is not None
    assert GAPS_IN in good.options


# --- confirmed is stricter than ok ------------------------------------------------------------


def test_a_transaction_with_an_unconfirmed_key_is_ok_but_not_last_known_good(
    tmp_path: Path,
) -> None:
    """`no such option` is not evidence the write was wrong -- so the outcome stays `ok` --
    but it is not evidence it was right either, so the Snapshot does not become good."""
    model = fresh_model()
    model.set(GAPS_IN, 6)
    conversation = model_conversation(model)
    conversation[f"j/getoption {GAPS_IN}"] = NO_SUCH_OPTION

    journal, results = one_apply(tmp_path, model, GAPS_IN, conversation=conversation)

    assert results[0].ok and not results[0].confirmed
    assert not journal.entries()[0].confirmed
    assert journal.last_known_good(GENERAL_MODULE) is None


def test_a_rejected_transaction_is_journalled_but_never_confirmed(tmp_path: Path) -> None:
    model = fresh_model()
    model.set(GAPS_IN, 6)
    conversation = model_conversation(model)
    conversation["j/configerrors"] = (
        '[\n\t"/home/user/.config/hypr/hyprtweaker/options/general.lua:3: bad"\n]\n'
    )

    journal, results = one_apply(tmp_path, model, GAPS_IN, conversation=conversation)

    assert results[0].outcome is ApplyOutcome.CONFIG_ERRORS
    entry = journal.entries()[0]
    assert entry.outcome == "config-errors"
    assert not entry.confirmed
    # The bad bytes are still on disk and still recorded: that is what the revert reads.
    assert entry.change(GENERAL_MODULE) is not None


def test_a_read_back_mismatch_is_journalled_but_never_confirmed(tmp_path: Path) -> None:
    model = fresh_model()
    model.set(ROUNDING, 10)
    conversation = model_conversation(model)
    conversation[f"j/getoption {ROUNDING}"] = option_reply(model.option(ROUNDING), 3)

    journal, results = one_apply(tmp_path, model, ROUNDING, conversation=conversation)

    assert results[0].outcome is ApplyOutcome.READ_BACK_MISMATCH
    assert not journal.entries()[0].confirmed
    assert journal.last_known_good(DECORATION_MODULE) is None


# --- what is not recorded ---------------------------------------------------------------------


def test_a_transaction_that_moved_nothing_on_disk_journals_nothing(tmp_path: Path) -> None:
    """`NOTHING_TO_DO` issues no reload and replaces no bytes, so there is no history in it."""
    model = fresh_model()
    model.set(GAPS_IN, 6)

    async def scenario(transaction: ApplyTransaction, _fake: FakeHyprland) -> None:
        await transaction.run([GAPS_IN])
        assert (await transaction.run([GAPS_IN])).outcome is ApplyOutcome.NOTHING_TO_DO

    journal = with_transaction(tmp_path, model, scenario)

    assert len(journal.entries()) == 1


def test_an_unknown_key_is_refused_before_a_snapshot_is_taken(tmp_path: Path) -> None:
    model = fresh_model()

    journal, results = one_apply(tmp_path, model, "general:not_an_option")

    assert results[0].outcome is ApplyOutcome.ABORTED
    assert journal.entries() == ()


def test_a_transaction_without_a_journal_still_applies(tmp_path: Path) -> None:
    """History is not config. An app that refused an edit because the state dir was
    unwritable would be the tail wagging the dog."""
    model = fresh_model()
    model.set(GAPS_IN, 6)
    paths = ConfigPaths.rooted_at(tmp_path)
    fake = FakeHyprland(model_conversation(model), reload_emits_event=True)

    async def scenario(started: FakeHyprland) -> ApplyResult:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            return await ApplyTransaction(
                model=model,
                writer=Writer(paths, SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                reload_timeout=0.5,
                settle_timeout=0.05,
            ).run([GAPS_IN])

    assert run_with_fake(scenario, fake).outcome is ApplyOutcome.OK
    assert not paths.journal.exists()
