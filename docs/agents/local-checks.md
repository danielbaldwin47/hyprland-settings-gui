# Local checks: done checks, worktrees, and running the app off the desktop

Each line was verified on 2026-10-01 on the owner's Omarchy machine (Hyprland 0.56.2) unless it says otherwise.

## Done checks

Run from the checkout or worktree root, with the main checkout's venv:

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
timeout 900 .venv/bin/pytest -q -n auto
```

- `pytest` with no path runs the per-commit tiers (ADR-0011 tiers 1, 2 and 4) in `pyproject.toml` `testpaths` (unit, static, ui): 2245 passed, 12 skipped in about 12 s with `-n auto` (68 s serial) with #146. CI fails a run with more skips than `SKIP_CEILING` in `.github/workflows/ci.yml`; a ticket that adds or removes an intentional skip moves that number.
- **One pytest run at a time on the machine.** The controller takes a lock, `$XDG_RUNTIME_DIR/hyprtweaker-pytest.lock` (root `conftest.py`), before it collects. A run started while another holds it, from any checkout or worktree, prints `pytest: waiting for <lock>, held by PID <n> (<checkout>)` and queues, then `pytest: took <lock> after <s> s` when its turn comes; a run that gets the lock at once prints neither. The `timeout 900` counts the wait. xdist workers, pytest runs a test starts inside a locked run, and CI (`CI` set) take no lock.
- `-n auto` is pytest-xdist, installed in the shared venv. It stays out of `addopts`: `meson test` runs the system pytest, which may lack xdist.
- The UI tier draws on an Xvfb of its own in each pytest process and sandboxes the config dir per test (`tests/ui/conftest.py`), so it never reaches the desktop compositor. It skips without GTK or without `Xvfb`; `HYPRTWEAKER_REQUIRE_UI=1` makes that skip a failure. To watch it, set `HYPRTWEAKER_UI_HOST_DISPLAY=1`: its windows then map on the desktop session, and Hyprland may raise its "Application Not Responding" dialog over them.
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

It starts a nested Hyprland with a fresh sandbox `$HOME`, launches the app inside it, and prints the nested `HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY`: `hyprctl` with that signature reads the nested session. Crop a screenshot to the app window before reading it (`hyprctl -j clients` with that signature gives its `at` and `size`).

- **Windowless.** By default the nested Hyprland runs on a spare card (§ Harness tier) with no host display, so nothing maps on the owner's desktop, on any workspace. Agents never pass `--window`; it is the owner's interactive mode, a host window on the focused workspace.
- **Fenced.** The app runs non-unique, so concurrent sandboxes never hand their launch to each other, and a copied `--config` has its top-level `hl.exec_cmd(` and `hl.env(` lines commented out, so a rice's autostart never runs against the owner's session.
- **Live probes** of compositor behaviour (`hyprctl keyword`, `hyprctl dispatch`, temporary binds, `hyprctl reload`) go to a nested instance, never the desktop session: the sandbox or the Harness tier's `NestedHyprland`.
- Real monitors and input devices exist only on the desktop session; a nested instance shows one virtual output and the host's forwarded keyboard and pointer. A ticket whose proof needs real hardware says so, and the effort PR lists it for the owner (`implement-spec.md` step 8).

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

- **Private display.** The runner starts an Xvfb of its own (the UI tier's, `tests/ui/private_display.py`) and pins GTK to it over X11, with no Wayland or Hyprland session in reach and a throwaway config dir. It puts the worktree's `src` on the path and selects Gtk 4 and Adw 1, so a probe imports them as is.
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
