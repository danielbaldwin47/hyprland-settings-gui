"""Attribution: whose file a `configerrors` line blames (ADR-0016 §Attribution).

The class decides the recovery, and one of the five authorises the app to write to the user's
config on its own. So the tests that matter most here are the *negative* ones -- an error in
`user.lua`, in `legacy.lua`, in a Bridge module, or in an app Module this transaction did not
write must never come back as `OWN_WRITE`.

The two line shapes are the ones the unit tier's scripted compositor answers with, and the
`require` one is the loud case: a Module that would not load at all is silently absent at
runtime, which for `binds.lua` means zero keybinds.
"""

from __future__ import annotations

from hyprtweaker.engine.apply import Ownership, attribute, own_write_modules

GENERAL = "options/general.lua"
DECORATION = "options/decoration.lua"

HOME = "/home/user/.config/hypr"


def error_in(relpath: str, line: int = 3) -> str:
    return f"{HOME}/hyprtweaker/{relpath}:{line}: unknown config key 'general.nope'"


def require_error(module: str) -> str:
    return f'require("hyprtweaker/{module}"): bad argument'


def one(line: str, *, written: tuple[str, ...] = ()) -> tuple[Ownership, str | None]:
    error = attribute([line], written=written)[0]
    return error.ownership, error.module


# --- the app's own write --------------------------------------------------------------------


def test_a_module_this_transaction_wrote_is_its_own_write() -> None:
    assert one(error_in(GENERAL), written=(GENERAL,)) == (Ownership.OWN_WRITE, GENERAL)


def test_a_module_this_transaction_did_not_write_is_not() -> None:
    """A hand edit is user intent (ADR-0005), so the recovery is a Banner offering
    Restore-last-good -- never an automatic write over what somebody typed."""
    assert one(error_in(GENERAL), written=(DECORATION,)) == (Ownership.APP_MODULE, GENERAL)


def test_a_failed_require_of_an_app_module_is_attributed_the_same_way() -> None:
    assert one(require_error(GENERAL), written=(GENERAL,)) == (Ownership.OWN_WRITE, GENERAL)


def test_a_require_target_without_its_extension_still_resolves() -> None:
    """`require_path` strips `.lua`, so the error names the module the way the Entrypoint
    does -- and the inverse has to put it back before anything matches."""
    error = attribute(['require("hyprtweaker/options/general"): bad argument'])[0]
    assert error.module == GENERAL


def test_own_write_modules_reports_in_write_order_and_without_duplicates() -> None:
    errors = [error_in(DECORATION, 2), error_in(GENERAL, 7), error_in(GENERAL, 9)]

    assert own_write_modules(errors, written=(GENERAL, DECORATION)) == (GENERAL, DECORATION)


def test_own_write_modules_is_empty_when_the_blame_lies_elsewhere() -> None:
    """The whole trigger for auto-revert: an empty tuple means "this write did not cause
    it", and the app must not answer by writing again."""
    errors = [f"{HOME}/user.lua:12: attempt to call a nil value"]

    assert own_write_modules(errors, written=(GENERAL,)) == ()


# --- everything the app must not touch -------------------------------------------------------


def test_user_lua_is_foreign() -> None:
    assert one(f"{HOME}/user.lua:12: oops", written=(GENERAL,)) == (Ownership.FOREIGN, None)


def test_legacy_lua_is_foreign_even_though_it_lives_in_the_app_dir() -> None:
    """Written once by the Importer and never again. Sitting inside the App dir is exactly
    why this has to be asked *before* the App-dir match rather than after it."""
    line = f"{HOME}/hyprtweaker/legacy.lua:4: oops"
    assert one(line, written=(GENERAL,)) == (Ownership.FOREIGN, None)


def test_a_bridge_module_is_foreign() -> None:
    line = f"{HOME}/hyprtweaker/bridge/matugen.lua:1: oops"
    assert one(line, written=(GENERAL,)) == (Ownership.FOREIGN, None)


def test_the_entrypoint_is_its_own_class() -> None:
    """App-owned and always regenerable, so its recovery is "regenerate", not "revert"."""
    assert one(f"{HOME}/hyprland.lua:2: oops") == (Ownership.ENTRYPOINT, "hyprland.lua")


def test_a_line_with_no_file_in_it_is_unknown_rather_than_guessed_at() -> None:
    assert one("something went wrong") == (Ownership.UNKNOWN, None)


def test_a_file_in_nobodys_territory_is_unknown() -> None:
    assert one("/etc/hypr/site.lua:9: oops") == (Ownership.UNKNOWN, None)


# --- parsing ---------------------------------------------------------------------------------


def test_the_line_is_kept_verbatim_with_its_prefix() -> None:
    """The `file:line` prefix is the only evidence of whose file failed, and the only part a
    user can paste into an editor's go-to-line box."""
    line = error_in(GENERAL, 42)
    error = attribute([line], written=(GENERAL,))[0]

    assert error.line == line
    assert error.number == 42
    assert error.path == f"{HOME}/hyprtweaker/{GENERAL}"


def test_a_require_failure_carries_no_line_number() -> None:
    assert attribute([require_error(GENERAL)])[0].number is None


def test_a_config_dir_that_is_not_where_the_app_computed_it_still_matches() -> None:
    """Suffix matching, not path comparison: the path Hyprland printed is the one it opened,
    which may have come through a symlinked dotfile directory. Missing the match would
    silently downgrade an own-write failure to somebody else's file."""
    line = f"/run/user/1000/dots-live/hypr/hyprtweaker/{GENERAL}:3: oops"
    assert one(line, written=(GENERAL,)) == (Ownership.OWN_WRITE, GENERAL)


def test_blank_lines_are_dropped() -> None:
    """A clean `configerrors` answers `[""]`, not `[]` (captured from 0.56.2)."""
    assert attribute([""]) == ()
