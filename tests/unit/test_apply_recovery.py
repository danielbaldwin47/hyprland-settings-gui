"""The recovery matrix: does each Ownership class route to ADR-0016's policy?

The ADR's table is the spec, so the first test transcribes it independently -- written from
the ADR text rather than from `_ACTIONS` -- and asserts the module agrees. A test that
imported the table it is checking would pass for any table at all.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine.apply.ownership import Ownership
from hyprtweaker.engine.apply.recovery import Action, Recovery, plan

APP_MODULE = "/home/user/.config/hypr/hyprtweaker/options/general.lua"
BINDS_MODULE = "/home/user/.config/hypr/hyprtweaker/options/binds.lua"
USER_LUA = "/home/user/.config/hypr/user.lua"
BRIDGE = "/home/user/.config/hypr/hyprtweaker/bridge/matugen.lua"
ENTRYPOINT = "/home/user/.config/hypr/hyprland.lua"


def error(path: str, line: int = 3, message: str = "unexpected symbol") -> str:
    return f"{path}:{line}: {message}"


# --- ADR-0016's table, transcribed --------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "written", "ownership", "actions"),
    [
        pytest.param(
            APP_MODULE,
            ("options/general.lua",),
            Ownership.OWN_WRITE,
            (Action.AUTO_REVERT,),
            id="app Module written by this transaction auto-reverts",
        ),
        pytest.param(
            APP_MODULE,
            (),
            Ownership.APP_MODULE,
            (Action.RESTORE_LAST_GOOD, Action.OPEN_FILE),
            id="hand-edited app Module offers restore and open, never a rewrite",
        ),
        pytest.param(
            USER_LUA,
            (),
            Ownership.FOREIGN,
            (Action.OPEN_FILE, Action.QUARANTINE),
            id="user.lua offers open-at-line and Quarantine",
        ),
        pytest.param(
            BRIDGE,
            (),
            Ownership.FOREIGN,
            (Action.OPEN_FILE, Action.QUARANTINE),
            id="a Bridge module gets the same offer as user.lua",
        ),
        pytest.param(
            ENTRYPOINT,
            (),
            Ownership.ENTRYPOINT,
            (Action.REGENERATE, Action.OPEN_FILE),
            id="Entrypoint refusal offers regenerate",
        ),
    ],
)
def test_each_ownership_class_routes_to_its_policy(
    path: str,
    written: tuple[str, ...],
    ownership: Ownership,
    actions: tuple[Action, ...],
) -> None:
    recovery = plan([error(path)], written=written)

    (problem,) = recovery.problems
    assert problem.ownership is ownership
    assert problem.actions == actions


def test_a_hand_edited_module_is_never_offered_an_automatic_rewrite() -> None:
    """ADR-0016 class 2: "No auto-write -- a hand edit is user intent"."""
    recovery = plan([error(APP_MODULE)], written=())

    (problem,) = recovery.problems
    assert Action.AUTO_REVERT not in problem.actions


def test_the_app_never_offers_to_write_a_foreign_file() -> None:
    """The app must not edit `user.lua`, so no restore and no regenerate is on offer."""
    recovery = plan([error(USER_LUA)])

    (problem,) = recovery.problems
    assert Action.RESTORE_LAST_GOOD not in problem.actions
    assert Action.REGENERATE not in problem.actions


def test_an_unattributable_line_offers_nothing() -> None:
    """A line in nobody's territory is shown verbatim and acted on by nothing."""
    recovery = plan(["something went wrong somewhere"])

    (problem,) = recovery.problems
    assert problem.ownership is Ownership.UNKNOWN
    assert problem.actions == ()
    assert problem.lines == ("something went wrong somewhere",)


# --- grouping ------------------------------------------------------------------------------


def test_many_errors_in_one_file_are_one_problem() -> None:
    recovery = plan([error(APP_MODULE, 3), error(APP_MODULE, 9), error(APP_MODULE, 12)])

    (problem,) = recovery.problems
    assert len(problem.errors) == 3
    assert problem.line == 3, "Open file lands on the first error Hyprland reported"


def test_problems_keep_the_order_hyprland_reported_them() -> None:
    recovery = plan([error(USER_LUA), error(APP_MODULE), error(ENTRYPOINT)])

    assert [problem.path for problem in recovery.problems] == [
        USER_LUA,
        APP_MODULE,
        ENTRYPOINT,
    ]


def test_a_require_failure_has_no_line_to_open_at() -> None:
    """`require("..."): ...` carries no line number, so Open file has nowhere to land."""
    recovery = plan(['require("hyprtweaker/options/binds"): bad argument'])

    (problem,) = recovery.problems
    assert problem.line is None
    assert problem.module == "options/binds.lua"


def test_lines_are_reported_verbatim() -> None:
    """The `file:line` prefix is the only evidence of whose file failed -- never reworded."""
    raw = error(APP_MODULE, 7, "unknown config key 'general.nope'")
    assert plan([raw]).lines == (raw,)


# --- the emergency gate --------------------------------------------------------------------


def test_zero_binds_strands_the_user_and_unlocks_the_app_owned_restore() -> None:
    recovery = plan([error(APP_MODULE)], written=(), binds=0)

    assert recovery.stranded
    assert recovery.auto_restorable == ("options/general.lua",)


def test_binds_present_leaves_the_consent_gate_up() -> None:
    recovery = plan([error(APP_MODULE)], written=(), binds=64)

    assert not recovery.stranded
    assert recovery.auto_restorable == ()


def test_an_unasked_probe_is_not_zero_binds() -> None:
    """`None` means the probe was never taken; treating it as zero would strand everyone."""
    recovery = plan([error(APP_MODULE)], written=())

    assert not recovery.stranded
    assert recovery.auto_restorable == ()


def test_zero_binds_never_authorises_touching_a_foreign_file() -> None:
    """Stranded beats hand-edit sanctity, but never the app's promise not to write user.lua."""
    recovery = plan([error(USER_LUA)], binds=0)

    assert recovery.stranded
    assert recovery.auto_restorable == (), "Quarantine is the only offer, and it needs consent"


def test_a_clean_reload_is_never_stranded() -> None:
    """No errors means nothing is wrong, whatever the bind count says."""
    recovery = plan([], binds=0)

    assert not recovery.unhealthy
    assert not recovery.stranded
    assert recovery.auto_restorable == ()


def test_a_stranded_reload_restores_only_the_blamed_modules() -> None:
    recovery = plan(
        [error(BINDS_MODULE), error(USER_LUA)],
        written=(),
        binds=0,
    )

    assert recovery.auto_restorable == ("options/binds.lua",)


# --- what the Banner asks ------------------------------------------------------------------


def test_nothing_wrong_is_not_unhealthy() -> None:
    assert not plan([]).unhealthy
    assert not Recovery().unhealthy


def test_an_entrypoint_error_is_flagged_as_a_refusal() -> None:
    assert plan([error(ENTRYPOINT)]).entrypoint_refused


def test_an_ordinary_module_error_is_not_a_refusal() -> None:
    assert not plan([error(APP_MODULE)]).entrypoint_refused


def test_by_action_finds_every_problem_offering_one_recovery() -> None:
    recovery = plan([error(USER_LUA), error(BRIDGE), error(APP_MODULE)])

    quarantinable = recovery.by_action(Action.QUARANTINE)
    assert [problem.path for problem in quarantinable] == [USER_LUA, BRIDGE]


def test_blank_lines_are_not_problems() -> None:
    """`configerrors` pads its array; a blank element is not a broken file."""
    assert not plan(["", "   "]).unhealthy
