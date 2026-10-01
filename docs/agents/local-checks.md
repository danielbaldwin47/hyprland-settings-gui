# Local checks: done checks, worktrees, and running the app off the desktop

Each line was verified on 2026-10-01 on the owner's Omarchy machine (Hyprland 0.56.2) unless it says otherwise.

## Done checks

Run from the checkout or worktree root, with the main checkout's venv:

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
timeout 900 .venv/bin/pytest -q
```

- `pytest` with no path runs the three per-commit tiers in `pyproject.toml` `testpaths` (unit, static, ui): 2231 passed, 12 skipped in about 65 s on `main` at fc6b8e1. CI fails a run with more skips than `SKIP_CEILING` in `.github/workflows/ci.yml`; a ticket that adds or removes an intentional skip moves that number.
- The UI tier needs a Wayland display and sandboxes the config dir per test (`tests/ui/conftest.py`, autouse `sandboxed_config`), so it never reaches the desktop compositor. It skips without GTK; `HYPRTWEAKER_REQUIRE_UI=1` makes that skip a failure.
- `mypy` checks only the `files` list in `pyproject.toml` (the Engine and the gi-free modules above it, ADR-0011).

## Worktrees

A ticket worktree under `.claude/worktrees/` needs no venv of its own. Run `<main checkout>/.venv/bin/<tool>` by absolute path from the worktree root: the package is not installed into the venv, and `pythonpath = ["src"]` makes pytest load the worktree's `src/`. For anything outside pytest, set `PYTHONPATH=<worktree>/src`.

## Running the app

The owner's desktop is Omarchy, whose `~/.config/hypr` is already Lua: the app run plainly would offer to import it, write Modules into it and reload the live session. Run it through the sandbox instead:

```sh
.venv/bin/python tools/sandbox.py --shot <scratch>/shot.png            # screenshot after 6 s, exit
.venv/bin/python tools/sandbox.py --config tests/corpus/end-4             # start from a corpus rice
.venv/bin/python tools/sandbox.py --home <scratch>/home                # reuse state across runs
```

It starts a nested Hyprland with a fresh sandbox `$HOME`, launches the app inside it, and prints the nested `HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY`: `hyprctl` with that signature reads the nested session. Crop a screenshot to the app window before reading it (`hyprctl -j clients` with that signature gives its `at` and `size`).

- **A widget probe** reads properties, not pixels: build the window in-process the way the UI tier does (each `tests/ui/test_*_page.py` has a `build_window(tmp_path)`), drive it, and read the widget's properties and adjustments. Probe before any screenshot loop: a scroll bug once took ten screenshot cycles that two probes settled.
- **Live probes** of compositor behaviour (`hyprctl keyword`, `hyprctl dispatch`, temporary binds, `hyprctl reload`) go to a nested instance, never the desktop session: the sandbox or the Harness tier's `NestedHyprland`.
- Real monitors and input devices exist only on the desktop session; a nested instance shows one virtual output and the host's forwarded keyboard and pointer. A ticket whose proof needs real hardware says so, and the effort PR lists it for the owner (`implement-spec.md` step 8).

## Harness tier

```sh
timeout 900 .venv/bin/pytest tests/integration -m hyprland
```

The nested-compositor tier (ADR-0011): each test gets its own Hyprland, `$HOME` and signature, and asserts it cannot reach the host. It needs a host Wayland session, `Hyprland`, `hyprctl` and `grim`; CI has none of them, so a local run is the only one.

- On 0.56.2 a nested Hyprland opens as a window on the host (output `WAYLAND-1`): the harness's `HYPRLAND_HEADLESS_ONLY=1` no longer appears in the 0.56.2 binary. Expect windows to flash on the desktop during a run.
- For the same reason two self-tests fail on `main`: `test_the_headless_canvas_is_a_fixed_size_whatever_the_host_screen_is` and `test_the_output_name_the_preamble_pins_is_the_one_we_shoot` (`HEADLESS-1` reports width 0). Pixel-comparing tests built on `Canvas` inherit it until the harness is fixed.
- Count host instances before and after a run (`hyprctl instances -j | jq length`): an escaped nested compositor shows up there.
