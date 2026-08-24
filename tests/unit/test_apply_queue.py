"""Sequencing: one transaction at a time, and one transaction per burst (ADR-0010).

These are the tests with the races in them, so they run against a stub transaction rather
than a compositor. That is the point of the seam: "never two at once" and "edits arriving
during a transaction join the next one" are claims about the queue alone, and mixing a
socket into them would only add ways for the test to be slow or flaky without adding
anything it proves.

The stub counts its own concurrency. A queue that overlapped transactions would still pass
every assertion about *results* -- the overlap only shows up if something is watching for
it while it happens.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

import pytest

from hyprtweaker.engine.apply import ApplyOutcome, ApplyQueue, ApplyResult

T = TypeVar("T")

FAST = 0.01
"""A debounce short enough to keep the suite quick, long enough to survive a loaded runner."""

SLOW = 5.0
"""A debounce no test is willing to wait out: reaching a result through it proves a commit
gesture skipped the wait rather than merely shortened it."""


def run(scenario: Callable[[], Awaitable[T]]) -> T:
    """`asyncio.run` per test, as the IPC tests do -- no pytest-asyncio dependency."""
    return asyncio.run(scenario())


class StubTransaction:
    """Records what it was asked to apply, and whether it was ever asked twice at once."""

    def __init__(self, *, duration: float = 0.0, fails: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.overlaps = 0
        self.duration = duration
        self.fails = fails
        self._running = 0

    async def run(self, keys: Sequence[str]) -> ApplyResult:
        self.calls.append(tuple(keys))
        self._running += 1
        if self._running > 1:
            self.overlaps += 1
        try:
            if self.duration:
                await asyncio.sleep(self.duration)
            if self.fails:
                raise RuntimeError("the compositor exploded")
            return ApplyResult(ApplyOutcome.OK, keys=tuple(keys))
        finally:
            self._running -= 1


# --- coalescing -------------------------------------------------------------------------------


class TestCoalescing:
    """AC: concurrent edits coalesce."""

    def test_a_burst_of_touches_is_one_transaction(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.touch("general:gaps_in")
                queue.touch("general:gaps_out")
                queue.touch("general:border_size")
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [
            ("general:border_size", "general:gaps_in", "general:gaps_out")
        ]

    def test_the_quiet_period_extends_while_edits_keep_arriving(self) -> None:
        """A slider drag is a stream of edits; each one restarts the wait, not a new apply."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=0.05) as queue:
                for step in range(4):
                    queue.touch(f"decoration:rounding:{step}")
                    await asyncio.sleep(0.02)  # each gap is shorter than the debounce
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert len(transaction.calls) == 1
        assert len(transaction.calls[0]) == 4

    def test_the_same_key_twice_is_applied_once(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.touch("decoration:rounding")
                queue.touch("decoration:rounding")
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [("decoration:rounding",)]

    def test_edits_during_a_transaction_join_the_next_one(self) -> None:
        """Not this one: its bytes are already rendered and its reload already asked for."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction(duration=0.05)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit("first")
                await asyncio.sleep(0.02)  # the first transaction is now running
                queue.commit("second")
                queue.commit("third")
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [("first",), ("second", "third")]


class TestSerialization:
    """AC: transactions never overlap."""

    def test_two_applies_never_run_at_once(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction(duration=0.02)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                results = [
                    asyncio.create_task(queue.apply(f"key:{index}")) for index in range(5)
                ]
                await asyncio.gather(*results)
            return transaction

        transaction = run(scenario)

        assert transaction.overlaps == 0

    def test_busy_is_true_only_while_a_transaction_runs(self) -> None:
        async def scenario() -> tuple[bool, bool, bool]:
            transaction = StubTransaction(duration=0.05)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                before = queue.busy
                task = asyncio.create_task(queue.apply("decoration:rounding"))
                await asyncio.sleep(0.02)
                during = queue.busy
                await task
                return before, during, queue.busy

        assert run(scenario) == (False, True, False)

    def test_drain_waits_for_the_transaction_in_flight(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction(duration=0.05)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit("decoration:rounding")
                await queue.drain()
                assert not queue.busy
            return transaction

        assert len(run(scenario).calls) == 1


# --- the two gestures ------------------------------------------------------------------------


class TestCommitGesture:
    """A decided gesture must not wait out a debounce it did not ask for (ADR-0003)."""

    def test_commit_skips_the_quiet_period(self) -> None:
        async def scenario() -> ApplyResult:
            async with ApplyQueue(StubTransaction(), debounce=SLOW) as queue:
                return await asyncio.wait_for(queue.apply("decoration:rounding"), timeout=1.0)

        assert run(scenario).outcome is ApplyOutcome.OK

    def test_a_commit_ends_a_drag_already_in_its_quiet_period(self) -> None:
        """Releasing a slider applies now, rather than waiting out the drag's own debounce."""

        async def scenario() -> tuple[str, ...]:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=SLOW) as queue:
                queue.touch("decoration:rounding")
                await asyncio.sleep(FAST)
                await asyncio.wait_for(queue.apply("decoration:rounding"), timeout=1.0)
            return transaction.calls[0]

        assert run(scenario) == ("decoration:rounding",)

    def test_pending_names_what_has_not_been_applied_yet(self) -> None:
        async def scenario() -> tuple[frozenset[str], frozenset[str]]:
            async with ApplyQueue(StubTransaction(), debounce=SLOW) as queue:
                queue.touch("a")
                queue.touch("b")
                waiting = queue.pending
                await asyncio.wait_for(queue.apply("c"), timeout=1.0)
                return waiting, queue.pending

        assert run(scenario) == (frozenset({"a", "b"}), frozenset())


# --- results ---------------------------------------------------------------------------------


class TestResults:
    """Who hears about a transaction, and what they hear."""

    def test_apply_returns_the_transaction_that_carried_the_keys(self) -> None:
        async def scenario() -> ApplyResult:
            async with ApplyQueue(StubTransaction(), debounce=FAST) as queue:
                return await queue.apply("decoration:rounding")

        assert run(scenario).keys == ("decoration:rounding",)

    def test_a_caller_waiting_through_a_transaction_gets_the_next_one(self) -> None:
        """Their edit was not in the batch already rendered, so neither is its result."""

        async def scenario() -> tuple[tuple[str, ...], tuple[str, ...]]:
            async with ApplyQueue(StubTransaction(duration=0.05), debounce=FAST) as queue:
                first = asyncio.create_task(queue.apply("first"))
                await asyncio.sleep(0.02)
                second = asyncio.create_task(queue.apply("second"))
                return (await first).keys, (await second).keys

        assert run(scenario) == (("first",), ("second",))

    def test_the_subscriber_hears_about_an_apply_nobody_awaited(self) -> None:
        """A debounced touch has no caller left by the time it applies -- error surfacing
        (#60) would otherwise never hear about the apply a slider drag ended in."""

        async def scenario() -> list[ApplyResult]:
            seen: list[ApplyResult] = []
            async with ApplyQueue(
                StubTransaction(), debounce=FAST, on_result=seen.append
            ) as queue:
                queue.touch("decoration:rounding")
                await queue.drain()
            return seen

        seen = run(scenario)

        assert [result.keys for result in seen] == [("decoration:rounding",)]

    def test_a_broken_subscriber_does_not_stop_later_applies(self) -> None:
        """The worker is the session's only apply path; one bad callback must not retire it."""

        def explode(_: ApplyResult) -> None:
            raise RuntimeError("subscriber bug")

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST, on_result=explode) as queue:
                await queue.apply("first")
                await queue.apply("second")
            return transaction

        assert run(scenario).calls == [("first",), ("second",)]


class TestFailureIsolation:
    """A transaction that raises must not take instant apply down for the session."""

    def test_the_caller_hears_the_exception(self) -> None:
        async def scenario() -> None:
            async with ApplyQueue(StubTransaction(fails=True), debounce=FAST) as queue:
                await queue.apply("decoration:rounding")

        with pytest.raises(RuntimeError, match="exploded"):
            run(scenario)

    def test_the_worker_survives_it(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction(fails=True)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                with pytest.raises(RuntimeError):
                    await queue.apply("first")
                transaction.fails = False
                result = await queue.apply("second")
                assert result.outcome is ApplyOutcome.OK
            return transaction

        assert run(scenario).calls == [("first",), ("second",)]

    def test_a_failed_transaction_still_leaves_the_queue_idle(self) -> None:
        """A `drain()` that never returns after one bad apply would hang the app's shutdown."""

        async def scenario() -> None:
            async with ApplyQueue(StubTransaction(fails=True), debounce=FAST) as queue:
                with pytest.raises(RuntimeError):
                    await queue.apply("decoration:rounding")
                await asyncio.wait_for(queue.drain(), timeout=1.0)

        run(scenario)


class TestClosing:
    """Shutdown drops pending edits rather than starting a reload nobody will see."""

    def test_an_edit_after_close_does_not_resurrect_the_worker(self) -> None:
        """`touch` starts the worker on demand, so a late focus-out could otherwise revive
        a queue the app has already shut down."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            queue = ApplyQueue(transaction, debounce=FAST)
            queue.start()
            await queue.aclose()
            queue.touch("decoration:rounding")
            await asyncio.sleep(FAST * 3)
            return transaction

        assert run(scenario).calls == []

    def test_apply_after_close_says_so_rather_than_hanging(self) -> None:
        async def scenario() -> None:
            queue = ApplyQueue(StubTransaction(), debounce=FAST)
            queue.start()
            await queue.aclose()
            await queue.apply("decoration:rounding")

        with pytest.raises(RuntimeError, match="closed"):
            run(scenario)

    def test_close_is_idempotent(self) -> None:
        async def scenario() -> None:
            queue = ApplyQueue(StubTransaction(), debounce=FAST)
            queue.start()
            await queue.aclose()
            await queue.aclose()

        run(scenario)

    def test_a_waiting_caller_is_cancelled_rather_than_left_hanging(self) -> None:
        async def scenario() -> None:
            queue = ApplyQueue(StubTransaction(duration=SLOW), debounce=FAST)
            waiting = asyncio.create_task(queue.apply("decoration:rounding"))
            await asyncio.sleep(FAST)
            await queue.aclose()
            await asyncio.wait_for(waiting, timeout=1.0)

        with pytest.raises(asyncio.CancelledError):
            run(scenario)


# --- the priority restore (ADR-0016) ----------------------------------------------------------


class TestPriorityRestore:
    """AC: the queue admits a priority restore transaction.

    ADR-0016's Consequences ask for one by name, because auto-revert has to write *while the
    failed transaction is still being reported*. Two properties, and the second is the one
    with the bug in it: the restore must not wait, and it must not swallow.
    """

    def test_a_restore_runs_over_its_own_keys_alone(self) -> None:
        """A restore that absorbed the pending edits would confirm keys the user is still
        choosing -- and the byte-for-byte check that proves the recovery worked would then
        start failing on perfectly healthy configs."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=SLOW) as queue:
                queue.touch("general:gaps_in")
                await queue.apply_now("decoration:rounding")
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [("decoration:rounding",)]

    def test_the_edits_it_jumped_are_still_applied_afterwards(self) -> None:
        """Jumping the queue is not the same as emptying it."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit("general:gaps_in")
                await queue.apply_now("decoration:rounding")
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [("decoration:rounding",), ("general:gaps_in",)]

    def test_a_restore_does_not_wait_out_a_debounce(self) -> None:
        """A restore queued behind a burst leaves the user looking at a config Hyprland
        rejected for as long as they keep typing."""

        async def scenario() -> ApplyResult:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=SLOW) as queue:
                queue.touch("general:gaps_in")
                async with asyncio.timeout(1.0):
                    return await queue.apply_now("decoration:rounding")

        assert run(scenario).outcome is ApplyOutcome.OK

    def test_a_restore_still_waits_for_the_transaction_in_flight(self) -> None:
        """ "Priority" is about which transaction runs next, never about running two: a
        reload is O(whole config) and `configerrors` is one global slot (ADR-0010)."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction(duration=0.05)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit("general:gaps_in")
                while not queue.busy:
                    await asyncio.sleep(0)
                await queue.apply_now("decoration:rounding")
            return transaction

        transaction = run(scenario)

        assert transaction.overlaps == 0
        assert transaction.calls == [("general:gaps_in",), ("decoration:rounding",)]

    def test_a_queue_is_not_idle_while_a_restore_is_waiting(self) -> None:
        """`drain` is what a caller waits on to know the config is settled, and a recovery
        that had not run yet is the least settled the config ever gets."""

        async def scenario() -> list[tuple[str, ...]]:
            transaction = StubTransaction(duration=0.02)
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                waiting = asyncio.create_task(queue.apply_now("decoration:rounding"))
                # Let the task reach the queue: `drain` can only account for work that has
                # actually been submitted, here and in the app.
                while not queue.recovering:
                    await asyncio.sleep(0)
                await queue.drain()
                # Snapshot at the moment `drain` returned, not after awaiting the task.
                settled = list(transaction.calls)
                await waiting
            return settled

        assert run(scenario) == [("decoration:rounding",)]

    def test_a_closed_queue_refuses_a_restore_rather_than_dropping_it(self) -> None:
        async def scenario() -> None:
            queue = ApplyQueue(StubTransaction(), debounce=FAST)
            await queue.aclose()
            with pytest.raises(RuntimeError):
                await queue.apply_now("decoration:rounding")

        run(scenario)


# --- entity edits -----------------------------------------------------------------------------


class TestEntityCommits:
    """AC: a bind edit reaches disk (#64, ADR-0007).

    An Entity edit carries no Option keys, because a transaction renders the model whole and
    `keys` only scope the Read-back -- which binds have none of, being write-only over IPC.
    The queue drops a batch with nothing dirty in it, so this is the flag that keeps a bind
    edit from being silently swallowed on its way to the file.
    """

    def test_an_entity_commit_runs_a_transaction(self) -> None:
        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit_entities()
                await queue.drain()
            return transaction

        transaction = run(scenario)

        assert transaction.calls == [()], "the bind edit never reached the writer"

    def test_an_empty_commit_still_does_nothing(self) -> None:
        """The guard this works around must stay in place for Options."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit()
                await queue.drain()
            return transaction

        assert run(scenario).calls == []

    def test_an_entity_commit_coalesces_with_option_edits(self) -> None:
        """One reload for a gesture that moved both halves of the model."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.touch("general:gaps_in")
                queue.commit_entities()
                await queue.drain()
            return transaction

        assert run(scenario).calls == [("general:gaps_in",)]

    def test_the_entity_flag_does_not_leak_into_the_next_batch(self) -> None:
        """A second transaction must not run just because an earlier one had entities."""

        async def scenario() -> StubTransaction:
            transaction = StubTransaction()
            async with ApplyQueue(transaction, debounce=FAST) as queue:
                queue.commit_entities()
                await queue.drain()
                queue.commit()
                await queue.drain()
            return transaction

        assert run(scenario).calls == [()]
