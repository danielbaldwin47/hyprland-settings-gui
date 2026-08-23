"""The drift badge: knowing that something later in the load order won.

`user.lua` is required last, so a key it sets beats the Module the app wrote. ADR-0005 fixes
how that is noticed -- "after each reload the app compares `get_config`/`getoption` against
its model and badges diverging options as *overridden in user.lua*" -- so it falls out of
the Read-back the transaction already performs.

Notably *not* by reading `user.lua`. ADR-0018 considered evaluating it under the importer's
recording stub and rejected it: "running user code on every app start for a read-only
listing is consent-and-safety weight the feature doesn't earn". A badge earns even less.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from _fake_hyprland import FakeHyprland
from _support import Runner, session_for

from hyprtweaker.engine.apply.result import UNREADABLE, Mismatch
from hyprtweaker.engine.model import UNSET
from hyprtweaker.engine.model.values import CssGaps

ROUNDING = "decoration:rounding"


def mismatch(expected: Any, actual: Any, *, live_set: bool = True) -> Mismatch:
    return Mismatch(name=ROUNDING, expected=expected, actual=actual, live_set=live_set)


# --- what counts as an override -----------------------------------------------------------


def test_a_live_value_that_disagrees_is_an_override() -> None:
    """The quiet shape: the Module ran, and something later set the key to its own value."""
    assert mismatch(10, 20).overridden is True


def test_a_live_value_that_agrees_is_not_an_override() -> None:
    """The finding this test exists for: a `user.lua` that happens to set what the GUI set
    is not drift, and badging it would tell the user an edit failed that landed perfectly.

    ADR-0005 says *diverging* options, and agreement is not divergence.
    """
    assert mismatch(10, 10).overridden is False


def test_a_float_that_survived_the_wire_is_not_an_override() -> None:
    """Hyprland stores config floats as 32-bit, so an exact comparison would call every
    fractional Option the app ever wrote an override. `values_match` is the arbiter."""
    assert mismatch(0.95, 0.949999988079071).overridden is False


def test_a_complex_value_compares_as_a_value_and_not_as_an_object() -> None:
    assert mismatch(CssGaps(4, 8, 4, 8), CssGaps(4, 8, 4, 8)).overridden is False
    assert mismatch(CssGaps(4, 8, 4, 8), CssGaps(5, 5, 5, 5)).overridden is True


def test_a_key_the_live_config_does_not_set_is_not_an_override() -> None:
    """That is the loud shape -- the Module never ran -- and it is `unapplied`'s to report.

    The two must never both fire on one key: one says "nothing is setting this", the other
    says "something else is setting this", and they cannot both be true.
    """
    unset_live = mismatch(10, None, live_set=False)

    assert unset_live.overridden is False
    assert unset_live.unapplied is True


def test_an_unreadable_value_is_not_an_override() -> None:
    """ADR-0010's Unconfirmed: no answer is not evidence of disagreement, and must never be
    badged as one."""
    assert mismatch(10, UNREADABLE).overridden is False


def test_a_key_the_model_does_not_set_is_not_an_override() -> None:
    """Nothing was asked for, so nothing can have overridden it."""
    assert mismatch(UNSET, 20).overridden is False


# --- what the session does with it ----------------------------------------------------------


def test_the_session_reports_only_the_diverging_keys() -> None:
    """`_note` is the single funnel every observed reload passes through, so the badge is
    computed in exactly one place for its own transactions and for foreign ones alike."""
    with TemporaryDirectory() as root:
        session = session_for(FakeHyprland(), Path(root), Runner())
        session._note(
            [],
            written=[],
            binds=1,
            mismatches=[
                Mismatch(name=ROUNDING, expected=10, actual=20, live_set=True),
                Mismatch(name="general:border_size", expected=2, actual=2, live_set=True),
                Mismatch(name="general:gaps_in", expected=5, actual=None, live_set=False),
            ],
        )

        assert session.overridden == {ROUNDING}, "only the key that actually diverged"
        assert session.unapplied == {"general:gaps_in"}, "the loud shape stays separate"


def test_a_clean_transaction_clears_a_stale_badge() -> None:
    """Replaced per transaction, never accumulated: a badge that outlived the write that
    earned it would be describing a value that has since applied perfectly well."""
    with TemporaryDirectory() as root:
        session = session_for(FakeHyprland(), Path(root), Runner())
        session._note(
            [], written=[], binds=1, mismatches=[Mismatch(ROUNDING, 10, 20, live_set=True)]
        )
        assert session.overridden == {ROUNDING}

        session._note([], written=[], binds=1, mismatches=[])

        assert session.overridden == frozenset()
