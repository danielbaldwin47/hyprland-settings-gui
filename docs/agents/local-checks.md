# Local checks: done checks, worktrees, and running the app off the desktop

Each line was verified on 2026-10-01 on the owner's Omarchy machine (Hyprland 0.56.2) unless it says otherwise.

## Done checks

Run from the checkout or worktree root, with the main checkout's venv:

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
timeout 900 .venv/bin/pytest -q -n auto
```

- `pytest` with no path runs the per-commit tiers (ADR-0011 tiers 1, 2 and 4) in `pyproject.toml` `testpaths` (unit, static, ui): 3672 passed, 12 skipped in about 28 s with `-n auto` and 155 s with `-n 0` on 2026-10-02 (#219). CI fails a run with more skips than `SKIP_CEILING` in `.github/workflows/ci.yml`; a ticket that adds or removes an intentional skip moves that number.
- **One pytest run at a time on the machine.** The controller takes a lock, `$XDG_RUNTIME_DIR/hyprtweaker-pytest.lock` (root `conftest.py`), before it collects. A run started while another holds it, from any checkout or worktree, prints `pytest: waiting for <lock>, held by PID <n> (<checkout>)` and queues, then `pytest: took <lock> after <s> s` when its turn comes; a run that gets the lock at once prints neither. The `timeout 900` counts the wait. xdist workers, pytest runs a test starts inside a locked run, and CI (`CI` set) take no lock.
- `-n auto` is pytest-xdist, installed in the shared venv, and means at most 8 workers (`MAX_WORKERS`, root `conftest.py`): uncapped on this 20-thread, 31 GB machine it started 20 workers at about 0.73 GB each plus 21 Xvfb, which is how systemd-oomd once killed the owner's terminal. It stays out of `addopts`: `meson test` runs the system pytest, which may lack xdist.
- Memory, measured by peak RSS on 2026-10-02 (#219): each of the 8 `-n auto` workers peaks at 0.33 to 0.52 GB, and a serial `tests/ui` run stays under 0.65 GB. A worker or serial run far above that is holding windows: the `released_windows` fixture (`tests/ui/conftest.py`) destroys and releases every window a test opens, and the app releases what it closes or replaces through `hyprtweaker.ui.release`. Before that, a serial `tests/ui` run held 153 windows and aborted on GLib's per-object weak-reference limit.
- The UI tier draws on an Xvfb of its own in each pytest process, on a display in 200-999 (§ Private X displays), runs on a private session bus (§ Private session bus), and sandboxes the config dir per test (`tests/ui/conftest.py`), so it never reaches the desktop compositor or the owner's session. It skips without GTK, `Xvfb` or `dbus-daemon`; `HYPRTWEAKER_REQUIRE_UI=1` makes that skip a failure. To watch it, set `HYPRTWEAKER_UI_HOST_DISPLAY=1`: its windows then map on the desktop session and it keeps the host's session bus, and Hyprland may raise its "Application Not Responding" dialog over them.
- `mypy` checks only the `files` list in `pyproject.toml` (the Engine and the gi-free modules above it, ADR-0011).

## Worktrees

A ticket worktree under `.claude/worktrees/` needs no venv of its own. Run `<main checkout>/.venv/bin/<tool>` by absolute path from the worktree root: the package is not installed into the venv, and `pythonpath = ["src"]` makes pytest load the worktree's `src/`. For anything outside pytest, set `PYTHONPATH=<worktree>/src`.

## Running the app

The owner's desktop is Omarchy, whose `~/.config/hypr` is already Lua: the app run plainly would offer to import it, write Modules into it and reload the live session. Run it through the sandbox instead:

```sh
.venv/bin/python tools/sandbox.py --shot <scratch>/shot.png                          # screenshot after 6 s, exit
.venv/bin/python tools/sandbox.py --config tests/corpus/end-4 --shot <scratch>/shot.png  # from a corpus rice
.venv/bin/python tools/sandbox.py --home <scratch>/home                              # run until SIGTERM, reuse state
```

It starts a nested Hyprland with a fresh sandbox `$HOME`, launches the app inside it, and prints the nested `HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY`: `hyprctl --instance <that signature> …` reads the nested session. Crop a screenshot to the app window before reading it (`hyprctl --instance <that signature> -j clients` gives its `at` and `size`).

- **Windowless.** By default the nested Hyprland runs on a spare card (§ Harness tier) with no host display, so nothing maps on the owner's desktop, on any workspace. Agents never pass `--window`; it is the owner's interactive mode, a host window on the focused workspace.
- **Fenced.** The app runs non-unique, so concurrent sandboxes never hand their launch to each other, and a copied `--config` has its top-level `hl.exec_cmd(` and `hl.env(` lines commented out, so a rice's autostart never runs against the owner's session.
- **Live probes** of compositor behaviour (`hyprctl keyword`, `hyprctl dispatch`, temporary binds, `hyprctl reload`) go to a nested instance: the sandbox or the Harness tier's `NestedHyprland`.
- **The shell fence** holds that line: `.claude/hooks/desktop-fence.sh`, a `PreToolUse` hook on Bash. It judges the command word of each simple command, behind `VAR=…`, `env`, `timeout` and other wrappers, and refuses:
  - `hyprctl`, any subcommand but `instances`, unless it names a nested instance by its signature written out: `hyprctl --instance <signature> …`, `-i <signature>`, or a `HYPRLAND_INSTANCE_SIGNATURE=<signature>` prefix. An unexpanded `$VAR`, an index (`-i 0` can be the desktop), the session's own signature, and an instance whose `hyprland.lock` pid runs with the account's `$HOME` are all refused.
  - `wtype`, unless a `WAYLAND_DISPLAY=<display>` prefix names a nested instance's display, as the sandbox prints it.
  - `Hyprland`, `hyprland` and `start-hyprland`: start one with the sandbox or the Harness; for the version, `pacman -Q hyprland`.
  - `ydotool`, always: it writes to the kernel's uinput and has no nested form.

  Four more are refused always, each refusal naming the working shape:
  - `pkill` and `killall`, which match by name across the whole session and can kill the owner's compositor, terminal or apps. Kill a PID you started and recorded: `kill <pid>`, `kill $!`.
  - A GTK start outside the probe route: `python` code (`-c` or stdin) that names `Gtk`, `Adw`, `Gdk` or `gi.repository`, `python -m hyprtweaker`, or a script under `src/hyprtweaker`. Run `tools/widget_probe.py <probe.py>` or `tools/sandbox.py`, or pytest.
  - An X server started from the shell: `Xvfb`, `Xorg`, `Xwayland`, `xvfb-run` (§ Private X displays). pytest and `tools/widget_probe.py` start their own Xvfb as children and pass. A `dbus-daemon` typed directly is not fenced: start one only through `start_bus` (§ Private session bus).
  - `rm`, `unlink`, `rmdir`, `mv`, `ln`, `shred` and `find -delete`/`-exec` on a path under `/tmp/.X11-unix/` or a `/tmp/.X<n>-lock`.

  `kill <pid>`, `pgrep`, `hyprctl instances -j | jq length`, `systemctl --user show-environment`, `pytest`, `mypy`, `ruff`, `grep Gtk src/`, `ls /tmp/.X11-unix`, and the refused words as data (`grep hyprctl docs/`, a quoted string, a heredoc) pass. Without `jq` it refuses any call that names one of the words above. It binds every Claude Code session opened in this repo, the owner's included; the owner's own terminal is unaffected. A script file, `bash -c '…'`, `eval`, an alias, a path relative to a `cd` into `/tmp/.X11-unix` or a write to the compositor's socket passes it: there the Live probes rule is the only fence.
- Real monitors and input devices exist only on the desktop session; a nested instance shows one virtual output and the host's forwarded keyboard and pointer. A ticket whose proof needs real hardware says so, and the effort PR lists it for the owner (`implement-spec.md` step 8).

### Private X displays

Read this before starting any X server. An agent's X server takes a display number in **200-999**, chosen by `start_xvfb` in `tests/ui/private_display.py`, which the UI tier and the widget probe runner already call. A script that needs its own X server calls that function too; the shell fence refuses `Xvfb`, `Xorg`, `Xwayland` and `xvfb-run` typed directly.

- **Why.** An X server unlinks the socket path of the display it binds, `/tmp/.X11-unix/X<n>`, without checking who listens there (xtrans `SocketUNIXCreateListener`), and `-displayfd` also skips the `/tmp/.X<n>-lock` check while it walks up from display 0. The desktop's Xwayland (Hyprland 0.56.2) listens on `/tmp/.X11-unix/X0` with no abstract socket to stop it, so from #146 (2026-10-01) the UI tier's `Xvfb -displayfd` replaced the desktop's `:0`: X11 apps the owner started afterwards reached an agent's Xvfb or nothing. Hence the explicit number, which keeps Xvfb's lock check, and never `-displayfd` or a number below 200.
- **Leftovers.** `start_xvfb` skips a number whose lock file or socket already exists, live or left by a crashed run, and leaves those files alone; its own Xvfb removes its lock and socket when it exits. Several processes starting at once each get a different number: Xvfb takes its lock file atomically before it creates a socket, and the loser moves to the next number.
- **Fence.** The UI tier and the widget probe runner refuse a display whose number is the session's own `DISPLAY`, with `refusing display :<n>: it is the desktop session's own DISPLAY …`.
- **Hands off the session's X files.** `/tmp/.X11-unix/X0`, `X0_` and `/tmp/.X0-lock` belong to the desktop; reading them (`ls`, `ss -xlp`) is the whole of an agent's business there, and the shell fence refuses `rm`, `mv`, `ln` and `unlink` on them.

### Private session bus

GTK and libadwaita reach the desktop over the session bus: libadwaita reads the settings portal's colour scheme and accent from it, and an app that registers there hands its launch to the owner's open window. The UI tier's every pytest process and the widget probe runner each start a `dbus-daemon` of their own with `start_bus` in `tests/ui/private_display.py`, and `pin_environment` points `DBUS_SESSION_BUS_ADDRESS` at it before GTK starts. A script that needs a session bus calls `start_bus`.

- **No service directory.** The daemon loads a configuration written for it (`session_bus_config`): the session bus type, one socket in a temporary directory, `EXTERNAL` auth and `session.conf`'s permissive policy, with no `<standard_session_servicedirs/>`, `<servicedir>` or `<include>`. The default `session.conf` on this machine lists 49 activatable names (2026-10-02), among them `org.freedesktop.portal.Desktop`, the portal backends, `org.a11y.Bus` and `ca.desrt.dconf`: a name request on such a bus starts the owner's real services, and they can outlive the daemon. On this bus `ListActivatableNames` answers `org.freedesktop.DBus` alone, and the portal answers `ServiceUnknown`. Never start a bus with `dbus-run-session` or `dbus-daemon --session`, which load `session.conf`.
- **Rendering is the same on every machine.** The pin also sets `GSETTINGS_BACKEND=memory` (dconf is never read) and `ADW_DISABLE_PORTAL=1`, so "follow the system" renders GTK's default light scheme, never the owner's. A test or probe of the system theme asserts the requested scheme (`ColorScheme.DEFAULT`), not a colour.
- **Set, never unset.** GIO falls back to `$XDG_RUNTIME_DIR/bus`, the owner's own socket, when `DBUS_SESSION_BUS_ADDRESS` is missing. The widget probe's import fence therefore refuses a missing or foreign address as it refuses a foreign display (`DBUS_SESSION_BUS_ADDRESS=<…> is not a session bus this route started`).
- **Ends with its owner.** The daemon is the route's child under `PR_SET_PDEATHSIG`, as the Xvfb is, and unlinks its socket as it goes: a `timeout` or out-of-memory kill of a worker takes it down, and at most an empty `/tmp/hyprtweaker-bus-*` directory stays. A normal exit also removes the directory. `tests/ui/test_private_bus.py` and `tests/ui/test_widget_probe.py` hold this for `SIGKILL`, `SIGTERM`, `timeout` and a clean exit.
- **Only the private address is ever connected to.** Prove a bus is private by comparing address strings and socket inodes, and by connecting to the address a route printed. Never connect to `/run/user/<uid>/bus`, never read the owner's settings portal, and never stop, restart or reconfigure the owner's `dbus`, portals, dconf or accessibility bus (`systemctl --user`, `dbus-update-activation-environment`, `busctl` or `gdbus` at the owner's address). Stop a daemon you started by the PID you recorded, never by pattern.
- **CI.** The `ui-smoke` job installs `dbus` explicitly (`--no-install-recommends`); a missing `dbus-daemon` skips the tier with a reason, and under `HYPRTWEAKER_REQUIRE_UI=1` fails the run.

### Widget probes

A **widget probe** builds part of the app in-process the way the UI tier does (each `tests/ui/test_*_page.py` has a `build_window(tmp_path)`), drives it, and reads the widget's properties and adjustments. It reaches states the sandbox cannot, such as the Config view or an uncurated mapping. Probe before any screenshot loop: a scroll bug once took ten screenshot cycles that two probes settled.

`tools/widget_probe.py` is the only way to run one, and its `shoot` the only way to screenshot one:

```sh
.venv/bin/python tools/widget_probe.py <scratch>/probe.py [args...]
```

```python
import widget_probe  # the first line: before gi and before any hyprtweaker.ui import

from gi.repository import Gtk

...
widget_probe.shoot(row.widget, "<scratch>/row.png")  # a PNG cropped to that widget
```

- **Private display and bus.** The runner starts an Xvfb and a session bus of its own (the UI tier's, `tests/ui/private_display.py`, § Private session bus) and pins GTK to them over X11, with no Wayland or Hyprland session or owner bus in reach and a throwaway config dir. It puts the worktree's `src` on the path and selects Gtk 4 and Adw 1, so a probe imports them as is.
- **The import is the fence.** In a probe the runner did not start, `import widget_probe` exits before GTK starts, with `widget_probe: refusing to start GTK: <what it found>, so this probe could map on the desktop session; run it with .venv/bin/python tools/widget_probe.py <probe.py> [args...]`. A probe without that import has only this rule for a fence.
- **Why `xvfb-run` is no fence.** The desktop session exports `GDK_BACKEND=wayland,x11,*` and `WAYLAND_DISPLAY=wayland-1`, and `xvfb-run` changes neither, so GTK opens on the desktop's Wayland first: on 2026-10-01 a probe mapped a window on the owner's desktop that way three times (#202). Unsetting `WAYLAND_DISPLAY` as well does not fence it: the Wayland backend then tries its default socket, and the x11 fallback finds the session's `DISPLAY=:0`, which is XWayland on the desktop.
- **Screenshots.** `shoot(widget, path)` presents the widget's window, waits until the widget is laid out, and writes the pixels of the surface it is drawn on inside its bounds, background included. That surface is the window, or the popover for a widget in one: call `popover.popup()` first. `shoot` refuses a widget a ScrolledWindow shows only in part, naming how much is hidden: scroll its top into view through the ScrolledWindow's vertical adjustment, or enlarge the window. `margin=8` takes 8 px of the surface around the widget, for a group whose title glyphs reach above its box. `settle(seconds)` runs the main loop, for after a click or a page switch.

## Harness tier

```sh
HARNESS_DRM_CARD=/dev/dri/card0 timeout 900 .venv/bin/pytest tests/integration -m hyprland
```

The nested-compositor tier (ADR-0011): each test gets its own Hyprland, `$HOME` and signature, and asserts it cannot reach the host. It needs `Hyprland`, `hyprctl` and `grim`, and a host Wayland session unless a card is handed over. CI runs it nightly on main only (the `harness` job: a `vkms` card in an Arch container), so for a branch a local run is the only one.

- **Agents always set `HARNESS_DRM_CARD=/dev/dri/card0`** on the owner's machine (the Intel iGPU; the NVIDIA card is `card1`). The child then runs in a `bwrap` that shows it only that card and no input devices, on a no-op seat, as that card's DRM master, with no host `WAYLAND_DISPLAY`: it is **windowless**, and the harness creates its one headless output. Verified 2026-10-01: the self-tests pass 6 of 6 while the desktop's client list never changes. The harness reports which check a card fails: NVIDIA, a connected monitor, no render node (a `vkms` card needs none), no `bwrap`.
- Without it, a nested Hyprland on 0.56.2 opens as a host window (output `WAYLAND-1`; `HYPRLAND_HEADLESS_ONLY` is gone from the binary), and on an NVIDIA host `HEADLESS-1` stays 0x0 so every `Canvas` test skips (#144; `HYPRTWEAKER_REQUIRE_HARNESS=1` makes the skip a failure): aquamarine's headless output asks for linear buffers only, and NVIDIA's GBM refuses them.
- One DRM master per card: two harness runs, or a run and a windowless sandbox, at once on the same card collide. Run them one after another.
- **The user manager is fenced.** Unfenced, Hyprland exports itself at start (`systemctl --user import-environment`, `dbus-update-activation-environment`) and unsets those variables at exit; nested runs once left the owner's portals pointed at a dead `wayland-2`. The harness sets `HYPRLAND_NO_SD_VARS` in every mode, and the windowless `bwrap` also hides `$XDG_RUNTIME_DIR/bus` and `/systemd`. `test_the_host_user_manager_environment_is_untouched` asserts it; after any manual run, `systemctl --user show-environment` must still name the desktop's `wayland-1`.
- Count host instances before and after a run (`hyprctl instances -j | jq length`): an escaped nested compositor shows up there.
- **The desktop guard.** A test reaches a live Hyprland only through `harness.guarded` or the `guarded_hyprland` fixture (`tests/integration/harness/guard.py`), and spawns `hyprctl` with its `.env`. The guard refuses the session's own compositor, read from its `hyprland.lock` pid as the one running with the account's `$HOME`, and any instance it cannot read; `NestedHyprland` and `tools/sandbox.py` instances pass.
- The refusal skips, or fails under `HYPRTWEAKER_REQUIRE_HARNESS=1`, with `refused <signature> is the session's own compositor ... Run it against the Harness's NestedHyprland (the guarded_hyprland fixture ...) or against a tools/sandbox.py instance`. So `test_ipc_live.py` and `test_schema_reproducible.py` run like any Harness file, each on its own nested compositor.
- `tests/unit/test_no_unguarded_instance.py` enforces it on every plain `pytest`: any `Instance.current` under `tests/`, or a `hyprctl`/`gen_schema` spawn without `env=<guarded>.env`, fails; `MAY_SPAWN_HYPRCTL` lists the exempt call sites with a reason each.
