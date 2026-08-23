"""The sandbox: what a foreign config may and may not do while it is being read.

This is the one place the app runs somebody else's code, so these are safety tests rather
than behaviour tests. Each one names the thing that must not happen and then proves it did
not -- by checking the world, not by checking that the code meant well: the file the config
tried to write is asserted absent, not merely "reported".
"""

from __future__ import annotations

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.importer.lua import (
    Consent,
    ConsentRequired,
    Policy,
    evaluate,
    import_lua,
    lua_binary,
)
from hyprtweaker.engine.schema import load_schema

pytestmark = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

GRANTED = Consent(evaluate=True)


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema(SAMPLE_VERSION, SCHEMA_DIR)


def write(tmp_path, body: str, name: str = "hyprland.lua"):  # type: ignore[no-untyped-def]
    entry = tmp_path / name
    entry.write_text(body, encoding="utf-8")
    return entry


# --- consent ----------------------------------------------------------------------------


def test_nothing_runs_without_consent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The gate ADR-0009 puts in front of the whole flow, in the type system.

    `Consent()` is the default, so a caller who never thought about this cannot
    accidentally execute a stranger's config -- they get an exception instead.
    """
    canary = tmp_path / "ran"
    entry = write(tmp_path, f'io.open({str(canary)!r}, "w"):close()\n')

    with pytest.raises(ConsentRequired):
        evaluate(entry, consent=Consent())

    assert not canary.exists(), "the file was evaluated despite consent being withheld"


def test_consent_to_evaluate_is_not_consent_to_side_effects() -> None:
    """Two grants, and the second is never inferred from the first."""
    assert Consent(evaluate=True).policy() is Policy.BLOCK
    assert Consent(evaluate=True, passthrough=True).policy() is Policy.PASSTHROUGH


# --- what blocking actually blocks -------------------------------------------------------


def test_a_config_that_writes_a_file_writes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "written-by-the-config"
    entry = write(
        tmp_path,
        f'local f = io.open({str(target)!r}, "w")\nf:write("hello")\nf:close()\n',
    )

    recording = evaluate(entry, consent=GRANTED)

    assert not target.exists(), "the sandbox let a write through"
    assert [write_.path for write_ in recording.writes] == [str(target)]


def test_a_config_that_runs_a_command_runs_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "touched-by-the-config"
    entry = write(
        tmp_path,
        f'os.execute("touch {target}")\nlocal p = io.popen("touch {target}")\np:close()\n',
    )

    recording = evaluate(entry, consent=GRANTED)

    assert not target.exists(), "the sandbox let a command run"
    assert {use.kind for use in recording.shell} == {"os.execute", "io.popen"}


def test_a_config_that_deletes_a_file_deletes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    victim = tmp_path / "precious"
    victim.write_text("keep me", encoding="utf-8")
    entry = write(tmp_path, f"os.remove({str(victim)!r})\n")

    evaluate(entry, consent=GRANTED)

    assert victim.read_text(encoding="utf-8") == "keep me"


def test_reading_is_allowed_because_reading_changes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A config that reads its own files still has to work: half the corpus does.

    Blocking reads would not make anything safer -- it would only make the import wrong.
    """
    (tmp_path / "theme.txt").write_text("10", encoding="utf-8")
    entry = write(
        tmp_path,
        'local f = io.open("theme.txt", "r")\n'
        "local v = tonumber(f:read('a'))\nf:close()\n"
        "hl.config({ decoration = { rounding = v } })\n",
    )

    recording = evaluate(entry, consent=GRANTED)

    assert recording.ok, recording.errors
    assert recording.calls[0].args == {"decoration": {"rounding": 10}}


def test_the_config_cannot_reach_the_real_libraries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`debug` is the interesting one: with it, a config could walk out to the real `_ENV`.

    The recorder uses `debug` itself, from outside the sandbox -- so this asserts the
    boundary is the environment table and not the absence of the library.
    """
    entry = write(
        tmp_path,
        "hl.config({ decoration = { rounding = debug ~= nil and 1 or 0 } })\n"
        "hl.config({ general = { border_size = os.exit ~= nil and 1 or 0 } })\n",
    )

    recording = evaluate(entry, consent=GRANTED)

    assert recording.calls[0].args == {"decoration": {"rounding": 0}}, "debug was reachable"


def test_a_config_that_exits_is_trapped_and_still_reports(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`os.exit` is trapped under every policy, consent or not.

    Untrapped it would take the whole import with it, and a model missing everything after
    the exit would look exactly like a config that declared nothing after the exit.
    """
    entry = write(
        tmp_path,
        "hl.config({ decoration = { rounding = 7 } })\nos.exit(1)\n"
        "hl.config({ decoration = { rounding = 9 } })\n",
    )

    recording = evaluate(entry, consent=GRANTED)

    assert recording.exited
    assert len(recording.calls) == 1, "declarations after the exit should not appear"
    assert any("os.exit" in error for error in recording.errors)


def test_the_compositor_signature_never_reaches_the_child(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Anything the config shells out to must not be able to find the running session.

    The prototype found this via `Hyprland --verify-config`, which executes the file it
    checks: with the signature in the environment, an imported config can reconfigure the
    very session the user is importing from.
    """
    entry = write(
        tmp_path,
        'local signature = os.getenv("HYPRLAND_INSTANCE_SIGNATURE")\n'
        "hl.config({ misc = { disable_hyprland_logo = signature ~= nil } })\n",
    )

    recording = evaluate(
        entry,
        consent=GRANTED,
        env={
            "HYPRLAND_INSTANCE_SIGNATURE": "deadbeef",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin",
        },
    )

    assert recording.calls[0].args == {"misc": {"disable_hyprland_logo": False}}


# --- failures stay reportable ------------------------------------------------------------


def test_a_config_that_will_not_parse_still_produces_a_report(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """Nothing raises: the wizard needs a model to preview and a report to show even when
    the config is broken, because "your config does not load" is the most useful thing it
    could possibly say."""
    entry = write(tmp_path, "this is not lua at all ===\n")

    result = import_lua(entry, schema, consent=GRANTED)

    assert len(result.model.set_options()) == 0
    assert [item.code.value for item in result.loss] == ["L36"]
    assert result.loss.breakage


def test_a_config_that_raises_keeps_what_it_declared_first(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    entry = write(
        tmp_path,
        "hl.config({ decoration = { rounding = 4 } })\nerror('boom')\n",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert result.model.get("decoration:rounding") == 4
    assert any(item.code.value == "L36" for item in result.loss)


def test_a_config_that_never_finishes_is_cut_off(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """A foreign config is a program, and a program can loop forever."""
    entry = write(tmp_path, "while true do end\n")

    result = import_lua(entry, schema, consent=GRANTED, timeout=2.0)

    assert any("did not finish" in item.message for item in result.loss)


# --- the module system -------------------------------------------------------------------


def test_required_modules_are_resolved_the_way_hyprland_resolves_them(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`require` is config-dir relative under Hyprland, not cwd relative or path relative."""
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "look.lua").write_text(
        "hl.config({ decoration = { rounding = 12 } })\n", encoding="utf-8"
    )
    entry = write(tmp_path, 'require("parts/look")\n')

    recording = evaluate(entry, consent=GRANTED)

    assert recording.ok, recording.errors
    assert recording.calls[0].args == {"decoration": {"rounding": 12}}
    assert "parts/look.lua" in recording.requires


def test_a_required_module_runs_in_the_same_sandbox(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Otherwise the sandbox is one `require` deep and the second file is unguarded."""
    target = tmp_path / "written-from-a-module"
    (tmp_path / "sneaky.lua").write_text(
        f'io.open({str(target)!r}, "w"):close()\n', encoding="utf-8"
    )
    entry = write(tmp_path, 'require("sneaky")\n')

    evaluate(entry, consent=GRANTED)

    assert not target.exists()


def test_a_wildcard_require_cannot_smuggle_a_shell_command(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The directory listing behind `require("./dir/*")` is the importer's own shell-out.

    The path comes from the config being imported, so it is checked before it is
    interpolated -- otherwise the one command the importer runs on its own behalf is an
    injection point in a file the user has not read.
    """
    target = tmp_path / "injected"
    entry = write(tmp_path, f"""require("./x'; touch {target} ; echo '/*")\n""")

    evaluate(entry, consent=GRANTED)

    assert not target.exists(), "a crafted require name reached the shell"
