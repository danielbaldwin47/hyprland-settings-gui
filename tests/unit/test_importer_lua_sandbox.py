"""The sandbox: what a foreign config may and may not do while it is being read.

This is the one place the app runs somebody else's code, so these are safety tests rather
than behaviour tests. Each one names the thing that must not happen and then proves it did
not -- by checking the world, not by checking that the code meant well: the file the config
tried to write is asserted absent, not merely "reported".
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.importer.lua import (
    Cancelled,
    Consent,
    ConsentRequired,
    Policy,
    evaluate,
    import_lua,
    lua_binary,
    sandbox,
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

    result = import_lua(entry, schema, consent=GRANTED, timeout=0.5)

    assert "Your config took longer than 0.5 seconds to run, so reading it was stopped." in [
        item.message for item in result.loss
    ]


# --- output bound (#242) ------------------------------------------------------------------


def _zombie_children() -> list[int]:
    """Our children that ended and were never waited for: a read that was not reaped."""
    found = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        except OSError:
            continue
        if fields[0] == "Z" and int(fields[1]) == os.getpid():
            found.append(int(stat.parent.name))
    return found


def _printing_config(tmp_path, stream: str):  # type: ignore[no-untyped-def]
    """A passthrough config whose command writes to `stream` for ever, naming its pid."""
    pidfile = tmp_path / "command.pid"
    redirect = " >&2" if stream == "stderr" else ""
    entry = write(tmp_path, f'os.execute("echo $$ > {pidfile}; exec yes{redirect}")\n')
    return entry, pidfile


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_a_config_that_prints_for_ever_is_stopped_at_the_output_budget(
    tmp_path, stream
) -> None:  # type: ignore[no-untyped-def]
    """The unbounded `communicate` held every byte until the timeout (400 MB written was a
    1.2 GB peak in the app). Now the read ends at the budget, long before the timeout, says
    why, and leaves neither the command nor a zombie behind."""
    entry, pidfile = _printing_config(tmp_path, stream)
    started = time.monotonic()
    try:
        recording = evaluate(
            entry, consent=Consent(evaluate=True, passthrough=True), timeout=30
        )

        assert time.monotonic() - started < 15, "the read ran on towards its timeout"
        assert recording.errors == (
            "Your config printed more than 1 MiB while it was read, so reading it was stopped.",
        )
        assert not recording.calls
        pid = int(pidfile.read_text())
        _within(10, lambda: not _alive(pid), "the command's end")
        assert _zombie_children() == []
    finally:
        if pidfile.is_file() and Path(f"/proc/{int(pidfile.read_text())}/cmdline").is_file():
            os.kill(int(pidfile.read_text()), signal.SIGKILL)  # only ever our own `yes`


@pytest.mark.parametrize(
    "printer",
    [
        'print("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")',
        'io.write("xxxxxxxxxxxxxxxxxxxxxxxx")',
    ],
)
def test_a_config_that_prints_through_lua_is_stopped_at_the_budget(tmp_path, printer) -> None:  # type: ignore[no-untyped-def]
    """`print` never reaches a pipe: the runner records it, so a loop of them filled the
    runner's memory (about 40 MB a second) until the timeout. Even under the default policy."""
    entry = write(tmp_path, f"while true do {printer} end\n")
    started = time.monotonic()

    recording = evaluate(entry, consent=GRANTED, timeout=30)

    assert time.monotonic() - started < 15, "the read ran on towards its timeout"
    assert recording.errors == (
        "Your config printed more than 1 MiB while it was read, so reading it was stopped.",
    )
    assert _zombie_children() == []


@pytest.mark.parametrize("printer", ["print()", 'io.write("")'])
def test_a_config_that_prints_nothing_for_ever_is_stopped_too(tmp_path, printer) -> None:  # type: ignore[no-untyped-def]
    """An empty print is still a recorded entry: 20 million of them were 288 MB."""
    entry = write(tmp_path, f"while true do {printer} end\n")
    started = time.monotonic()

    recording = evaluate(entry, consent=GRANTED, timeout=30)

    assert time.monotonic() - started < 15, "the read ran on towards its timeout"
    assert recording.errors == (
        "Your config printed more than 1 MiB while it was read, so reading it was stopped.",
    )


def test_the_print_stop_takes_a_command_the_config_started_with_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The runner exits itself at the print budget; its process group still goes with it."""
    pidfile = tmp_path / "command.pid"
    entry = write(
        tmp_path,
        f'os.execute("sleep 600 >/dev/null 2>&1 & echo $! > {pidfile}")\n'
        'while true do print("xxxxxxxxxxxxxxxx") end\n',
    )
    try:
        recording = evaluate(
            entry, consent=Consent(evaluate=True, passthrough=True), timeout=30
        )

        assert recording.errors and "printed more than" in recording.errors[0]
        command = int(pidfile.read_text())
        _within(10, lambda: not _alive(command), "the command's end")
    finally:
        if pidfile.is_file():
            _kill_if_ours(int(pidfile.read_text()))


class _StatusSeenLate(subprocess.Popen):  # type: ignore[type-arg]
    """A child whose exit status is not yet visible to `poll` when its pipes reach EOF.

    The kernel closes a process's pipes before it becomes a zombie, so a loaded machine
    can see the EOF first; this makes that ordering certain (CI run 37103101388).
    """

    def poll(self):  # type: ignore[no-untyped-def]
        return None


def test_the_print_stop_takes_the_command_when_the_pipes_close_first(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The read sees the runner's pipes close before its exit status: the command it
    started still goes with it. It survived on CI, 3 reads in 300 on a loaded machine."""
    monkeypatch.setattr(subprocess, "Popen", _StatusSeenLate)
    pidfile = tmp_path / "command.pid"
    entry = write(
        tmp_path,
        f'os.execute("sleep 600 >/dev/null 2>&1 & echo $! > {pidfile}")\n'
        'while true do print("xxxxxxxxxxxxxxxx") end\n',
    )
    try:
        recording = evaluate(
            entry, consent=Consent(evaluate=True, passthrough=True), timeout=30
        )

        assert recording.errors and "printed more than" in recording.errors[0]
        command = int(pidfile.read_text())
        _within(10, lambda: not _alive(command), "the command's end")
    finally:
        if pidfile.is_file():
            _kill_if_ours(int(pidfile.read_text()))


def test_a_config_cannot_fake_or_catch_the_output_stop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The stop is the runner's own exit, outside the config's reach: `pcall` does not
    swallow it, and the config's `os.exit(77)` is only the trapped one."""
    faked = write(tmp_path, "os.exit(77)\n", "faked.lua")
    looped = write(
        tmp_path, 'while true do pcall(print, string.rep("x", 4096)) end\n', "looped.lua"
    )

    assert evaluate(faked, consent=GRANTED).errors == ("os.exit(77) trapped",)
    assert evaluate(looped, consent=GRANTED, timeout=30).errors == (
        "Your config printed more than 1 MiB while it was read, so reading it was stopped.",
    )


def _write_both(each: int) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys\n"
        f"sys.stdout.buffer.write(b'o' * {each}); sys.stdout.flush()\n"
        f"sys.stderr.buffer.write(b'e' * {each}); sys.stderr.flush()\n",
    ]


def _run_it(command: list[str], tmp_path):  # type: ignore[no-untyped-def]
    return sandbox._run(command, cwd=tmp_path, env=dict(os.environ), timeout=30, cancel=None)


def test_the_budget_is_for_both_streams_together(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Neither stream alone is over the budget, together they are."""
    half = sandbox.OUTPUT_LIMIT_BYTES // 2 + 1

    with pytest.raises(sandbox.OutputLimitExceeded):
        _run_it(_write_both(half), tmp_path)


def test_output_exactly_at_the_budget_is_kept_whole(tmp_path) -> None:  # type: ignore[no-untyped-def]
    each = sandbox.OUTPUT_LIMIT_BYTES // 2

    status, stdout, stderr = _run_it(_write_both(each), tmp_path)  # type: ignore[misc]

    assert (status, len(stdout), len(stderr)) == (0, each, each)


def test_a_read_that_writes_a_little_keeps_its_result(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The valid-read fixture the budget was chosen against: output on both pipes, far
    under it, and the record still comes back whole."""
    entry = write(
        tmp_path,
        'os.execute("echo out; echo err >&2")\nhl.config({ general = { gaps_in = 3 } })\n',
    )

    recording = evaluate(entry, consent=Consent(evaluate=True, passthrough=True))

    assert recording.ok, recording.errors
    assert [call.name for call in recording.calls] == ["config"]


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


def test_a_config_dir_with_a_quote_in_its_name_runs_no_command(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The wildcard listing interpolates the config's own directory as well as the name.

    A directory the user named `cfg'; touch pwned; echo '` must still be one quoted path:
    the consent page promises that none of the config's commands run, and a quote in a
    folder name is not consent.
    """
    root = tmp_path / "cfg'; touch pwned; echo '"
    (root / "parts").mkdir(parents=True)
    (root / "parts" / "one.lua").write_text(
        "hl.config({ decoration = { rounding = 3 } })\n", encoding="utf-8"
    )
    entry = write(root, 'require("./parts/*")\n')

    recording = evaluate(entry, consent=GRANTED)

    assert not (root / "pwned").exists(), "a quote in the config dir reached the shell"
    assert recording.calls, "the wildcard require loaded nothing from the quoted dir"


def test_no_interpreter_says_what_to_install(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from hyprtweaker.engine.importer.lua import LuaUnavailable, sandbox

    monkeypatch.setattr(sandbox, "lua_binary", lambda: None)
    entry = write(tmp_path, "hl.config({})\n")

    with pytest.raises(LuaUnavailable) as raised:
        evaluate(entry, consent=GRANTED)

    assert str(raised.value) == (
        "Reading a Lua config needs Lua, which is not installed. Install Lua (lua5.5, "
        "lua5.4, lua5.3, lua or luajit) and try again."
    )


def test_passthrough_really_does_let_an_effect_through(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The other half of the consent story, and worth proving rather than assuming: a
    policy that silently blocked everything would look identical from the report."""
    target = tmp_path / "written-under-passthrough"
    entry = write(tmp_path, f'io.open({str(target)!r}, "w"):close()\n')

    recording = evaluate(entry, consent=Consent(evaluate=True, passthrough=True))

    assert target.exists(), "passthrough did not pass the effect through"
    assert recording.policy is Policy.PASSTHROUGH
    assert [w.path for w in recording.writes] == [str(target)]


def _alive(pid: int) -> bool:
    """`pid` is a process still running: not gone, and not a zombie waiting to be reaped."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:  # gone, or exiting as it is read (ProcessLookupError: F10, #148 review)
        return False
    return stat.rsplit(")", 1)[1].split()[0] not in {"Z", "X"}


OUR_SLEEP = b"sleep\x00600\x00"
"""The command line of the `sleep` these tests' configs start, as /proc spells it."""


def _kill_if_ours(pid: int) -> None:
    """Kill `pid` only while it is still the `sleep 600` a config of ours started.

    The pid comes from a pidfile, not a `Popen`, so nothing holds it: once that `sleep`
    ends, the number can be reused by any of the owner's processes (#270 item 6).
    """
    try:
        ours = Path(f"/proc/{pid}/cmdline").read_bytes() == OUR_SLEEP
    except OSError:
        return
    if ours and _alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_the_cleanup_leaves_a_process_that_is_not_ours_alone() -> None:
    """A pidfile naming an unrelated process: the cleanup finds it and leaves it running."""
    other = subprocess.Popen(["sleep", "30"])
    try:
        _kill_if_ours(other.pid)
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def _within(seconds: float, condition, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {seconds:g} s")
        time.sleep(0.01)


def test_a_cancelled_read_stops_the_config_and_the_commands_it_started(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Cancel in the wizard (#216) stops the read, not only the wait for it: the config's
    process group is killed, which takes a command a passthrough read started with it."""
    pidfile = tmp_path / "command.pid"
    entry = write(tmp_path, f'os.execute("echo $$ > {pidfile}; exec sleep 600")\n')
    cancel = threading.Event()
    outcome: list[BaseException] = []

    def read() -> None:
        try:
            evaluate(entry, consent=Consent(evaluate=True, passthrough=True), cancel=cancel)
        except BaseException as error:
            outcome.append(error)

    worker = threading.Thread(target=read)
    worker.start()
    _within(10, lambda: pidfile.is_file() and pidfile.read_text().strip(), "the command start")
    command = int(pidfile.read_text())
    try:
        cancel.set()
        worker.join(10)

        assert not worker.is_alive(), "evaluate kept running after its cancel was set"
        assert [type(error) for error in outcome] == [Cancelled]
        _within(10, lambda: not _alive(command), "the command's end")
    finally:
        _kill_if_ours(command)  # a failed run must not leave its `sleep` behind


EXITS_MID_READ = """
import sys, threading, time
from pathlib import Path
from hyprtweaker.engine.importer.lua import Consent, evaluate

entry, pidfile = Path(sys.argv[1]), Path(sys.argv[2])
grant = Consent(evaluate=True, passthrough=True)
threading.Thread(target=evaluate, args=(entry,), kwargs={"consent": grant}, daemon=True).start()
deadline = time.monotonic() + 10
while not (pidfile.is_file() and pidfile.read_text().strip()):
    if time.monotonic() > deadline:
        sys.exit("the command never started")
    time.sleep(0.01)
"""
"""An app that quits while its read worker runs: the worker thread dies with it."""


def test_a_read_still_running_when_the_app_exits_is_stopped(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The config runs in a session of its own, and only the dead worker would have timed
    it out: without a stop at exit, it would run on, with what it started, for ever."""
    pidfile = tmp_path / "command.pid"
    entry = write(tmp_path, f'os.execute("echo $$ > {pidfile}; exec sleep 600")\n')
    src = Path(__file__).resolve().parents[2] / "src"

    subprocess.run(
        [sys.executable, "-c", EXITS_MID_READ, str(entry), str(pidfile)],
        env={**os.environ, "PYTHONPATH": str(src)},
        check=True,
        timeout=30,
    )

    command = int(pidfile.read_text())
    try:
        _within(10, lambda: not _alive(command), "the command's end once the app exited")
    finally:
        _kill_if_ours(command)


def test_a_read_cancelled_before_it_starts_runs_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    canary = tmp_path / "ran"
    entry = write(tmp_path, f'io.open({str(canary)!r}, "w"):close()\n')
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        evaluate(entry, consent=Consent(evaluate=True, passthrough=True), cancel=cancel)

    assert not canary.exists()


def test_the_wildcard_listing_is_recorded_even_though_it_is_ours(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The importer shells out once on its own behalf, to list a directory a wildcard
    `require` names. An unrecorded process start would be a hole in the sandbox's account
    of itself, so it is recorded -- under its own kind, since it is not the config's doing.
    """
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "one.lua").write_text(
        "hl.config({ decoration = { rounding = 3 } })\n", encoding="utf-8"
    )
    entry = write(tmp_path, 'require("./parts/*")\n')

    recording = evaluate(entry, consent=GRANTED)

    assert recording.calls, "the wildcard require loaded nothing"
    assert "importer.listdir" in {use.kind for use in recording.shell}


def test_nothing_a_config_runs_can_reach_the_session_even_under_passthrough() -> None:
    """Addendum 40 of the #153 review: with the session bus address passed through, a
    config's `systemctl --user import-environment` rewrote the user manager's environment."""
    from hyprtweaker.engine.importer.lua.sandbox import _child_env

    child = _child_env(
        {
            "PATH": "/usr/bin",
            "HYPRLAND_INSTANCE_SIGNATURE": "sig",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }
    )

    assert child == {"PATH": "/usr/bin"}


def test_the_config_reads_no_stdin_of_the_apps(tmp_path: Path) -> None:
    from hyprtweaker.engine.importer.lua.sandbox import _run

    code, out, _err = _run(
        ["sh", "-c", "cat; echo end"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=5,
        cancel=None,
    ) or (None, "", "")

    assert (code, out) == (0, "end\n")
