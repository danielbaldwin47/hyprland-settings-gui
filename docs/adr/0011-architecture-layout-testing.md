# ADR-0011: Architecture, project layout, testing

**Status:** accepted — 2026-08-19

## Context

ADR-0001 fixed the stack (Python + GTK4 + libadwaita) and demanded a clean engine/UI seam; ADRs 0005–0010 defined what the engine must do (config model, importers, bridge, apply pipeline). What's left is where the code lives, how the seam is enforced, how the schema layer tracks Hyprland releases, and how any of it is tested. The relevant facts are in:

- Research #6: widget-per-type Rows are built in Python at runtime; Blueprint helps only for a static shell; packaging prior art is meson/PKGBUILD/Flatpak.
- Prototype #8: the overlay needs a CI completeness test — uncurated options render falsehoods (sentinels as real values).
- Prototype #9: the nested-headless-Hyprland verification harness (`hyprctl -j` state diff + screenshot diff, ~45 s/config) caught 2 real converter bugs and was explicitly flagged to become the importer's test harness. `Hyprland --verify-config` parses Lua with no compositor.
- The rice corpus lives at `tests/corpus/` (task #17); no corpus rice uses `source=` globs or `{{ }}` arithmetic, so those need synthetic fixtures.

## Decision

### Package layout

`src/hyprtweaker/` with two subpackages:

- **`engine/`** — no GTK, importable headless:
  - `schema/` — load Generated schema + Overlay, resolve widget/nullability/visibility per Option
  - `model/` — Options (tri-state unset) + Entities; the single in-memory truth
  - `writer/` — deterministic Module rendering and the `luac -p` syntax gate; synchronous, and ignorant of the compositor
  - `apply/` — the Apply transaction, its queue and ApplyResult (ADR-0010). Split out of `writer/` during #54: the transaction is async and socket-bound, and folding an event loop into the renderer would have made the one part of the engine that must stay pure the hardest part to test
  - `importer/` — hyprlang Importer + Lua importer + Loss report (ADR-0009)
  - `ipc/` — socket/socket2 clients, `getoption`/`configerrors`/events; never spawns `hyprctl`
  - `state/` — Snapshots, Journal, Manifest (ADR-0005)
  - `migration/` — first-run detection, backups, the crash-safety sentinel, Export, and the five-step wizard flow (ADR-0009). Added during #63: the wizard is a state machine over the importers rather than part of either, and keeping it here — not in the UI — is what lets its ordering guarantees (nothing written before the Loss report, backup before the first write, sentinel before the Entrypoint, silence rolls back) be tested headless, and lets a relaunched app finish a rollback the dialog never got to show
- **`ui/`** —
  - `shell/` — window, sidebar, the two Views
  - `rows/` — widget-per-type Row factory
  - `pages/` — generated page factory + curated Tasks mapping
  - `dialogs/` — Capture, Migration wizard, confirm-or-revert
  - **Two modules sit at the `ui/` root, added during #72** — not because a home was missing, but because both are used from more than one of the subpackages above and neither belongs to any single one: `search.py` (ADR-0017's index — ranking is a decision about text, imports no `gi`, and is unit-tested against a golden like `pages/plan.py`) and `flash.py` (the navigate-and-flash pulse, shared by the Binds page's conflict jump and a Search hit; it owns a display-scoped CSS provider, which is why it is a module rather than a function copied twice). A third module here would be a sign the subpackages are wrong, not that the root is a category

**One module sits between them, amended during #56:** `src/hyprtweaker/session.py` — the Schema, model, Writer and live connection wired together for one run of the app. It is imported by `ui/` but imports no `gi`, so it belongs to neither subpackage: putting it under `ui/` would make the whole edit-to-compositor path reachable only from a machine with a display, and putting it under `engine/` would give the engine an opinion about application lifetime. It is the seam that lets `tests/unit` drive a real edit against a scripted socket and `tests/integration` drive the same object against a nested Hyprland.

### Seam enforcement

**`engine` never imports `gi`.** Enforced by a unit test that imports every `hyprtweaker.engine` module with `gi` masked out of `sys.modules` — the build fails the moment the seam leaks. The engine is the part that runs in tests, in the schema generator, and in any future CLI.

### Widgets: all-Python, no Blueprint

Most of the UI is generated at runtime (Rows, Pages), where Blueprint cannot help; the static shell is small. Dropping Blueprint removes the `blueprint-compiler` dependency and a meson build step. Revisit only if the hand-written shell grows unwieldy.

### Schema versioning

- `data/schema/hyprland-<ver>.json` — the **Generated schema**, produced by `tools/gen_schema.py` against a live or nested Hyprland (`hyprctl -j descriptions` + `hl.meta.lua` stub parse + type table from #3).
- `data/schema/overlay.json` — the **Overlay**, hand-curated and version-independent (ADR fields per #8: mandatory `nullable`/`null_label`, `widget`, `depends_on`, `labels`, `range`, `visibility`, `known_values`; polish `title`, `group`, `order`, `unit`, `help`, `restart`).
- Each app release ships the Generated schema(s) for the Hyprland version(s) it was generated against. At runtime: exact version match, else nearest lower schema plus the already-decided degradation (Row "unknown-to-this-version", *New in \<version\>* fallback group from #7).
- CI **overlay completeness test**: every Option in every shipped Generated schema must resolve to a widget, nullability, and title. A new schema with uncurated options fails the build.

### Testing

pytest, four tiers; the engine carries the coverage, the UI stays thin. Tiers 1, 2 and 4 are the per-commit tiers: they sit in `pyproject.toml` `testpaths` and in `meson test`. Tier 3 is outside both. Three environment variables turn a skip into a failure where the environment is supposed to host the tier: `HYPRTWEAKER_REQUIRE_UI` (tier 4), `HYPRTWEAKER_REQUIRE_HARNESS` (tier 3) and `HYPRTWEAKER_REQUIRE_LUAC` (tier 1, the `luac` syntax check in `tests/unit/test_writer_modules.py`). The data directories are not tiers: `tests/golden/` and `tests/corpus/` feed tier 1, `tests/fixtures/` feeds the tiers that need a config tree.

1. **Unit (per-commit)** — pure-engine golden files:
   - Importer: `.conf` tree → model snapshot JSON
   - Writer: model → Lua text
   - Round-trip: import → write → re-import must be identical
   - Fixtures: `tests/corpus/` rices + synthetic fixtures for grammar edges the corpus lacks (`source=` globs, `{{ }}` arithmetic, sentinel values, `# hyprlang` directives)
   - The expected outputs live in `tests/golden/` (`tests/golden/writer/` for Lua text), compared through `tests/unit/_golden.py`; `UPDATE_GOLDEN=1` regenerates them and the diff is read before commit.
2. **Static (per-commit, when Hyprland present)** — `Hyprland --verify-config` over every written Lua output; no compositor needed.
3. **Integration (on demand, and nightly in CI)** — prototype #9's nested-headless-Hyprland harness promoted to `tests/integration/`: state diff via `hyprctl -j` + screenshot diff. Marked `-m hyprland`, auto-skipped when no Hyprland binary; too slow (~45 s/config) for per-commit.

   **"nightly" dropped, amended during #55.** A nested Hyprland needs a **host Wayland session**: `HYPRLAND_HEADLESS_ONLY=1` is not sufficient on its own — backend creation fails with `CBackend::create() failed!` even when a DRM render node is passed explicitly, because the DRM backend wants a *seat*, and on a developer box the login session already owns it. A stock GitHub `ubuntu-latest` runner has neither, so a nightly job would skip 100% of the compositor tests and report green — worse than no job, because it would read as coverage. The tier therefore runs on demand, and `HYPRTWEAKER_REQUIRE_HARNESS=1` turns the skip into a hard failure for any environment that is *supposed* to be able to host it. Nightly stayed blocked until #195, the last paragraph of this tier.

   **`HYPRLAND_HEADLESS_ONLY` gone, amended during #144.** Hyprland 0.56.2 has no such switch: the nested instance always shows a `WAYLAND-1` window on the host, and its headless output allocates on the host GPU, which NVIDIA cannot do. The harness runs on a non-NVIDIA card through the opt-in `HARNESS_DRM_CARD`; see `docs/agents/local-checks.md` § Harness tier. **Amended again (#185): with `HARNESS_DRM_CARD` the child is windowless.** It gets no host display, runs Hyprland's DRM backend on that monitor-less card with one headless output, and its `bwrap` hides the host's session bus and systemd user manager; `HYPRLAND_NO_SD_VARS` stops it exporting itself there in either mode. Without a card, the nested instance still shows a `WAYLAND-1` window.

   **A card on a stock runner, probed in #89 (CI runs 36954816037, 36955251244, 36955434792, 36955628513 on `ubuntu-latest`, Ubuntu 24.04.5, kernel 6.17 azure).** The seat is not the blocker (the no-op seat in `bwrap` needs none); a card is, and one gate refusal stands between the runner and a green job. Probed facts: (1) the only card is `hyperv_drm` (`card1`), whose `Virtual-1` connector reads `connected` and which has no render node, so `drm_card_problem` refuses it on both counts. (2) `vkms` is not in the base kernel's modules; `apt-get install linux-modules-extra-$(uname -r)` (about 25 s) provides it, `modprobe vkms` works, and `echo off | sudo tee /sys/class/drm/card0-Virtual-2/status` turns its connector to `disconnected`. (3) `vkms` has **no render node**, and `vgem` is not built for this kernel, so no render node exists on the runner. `drm_card_problem` refuses a card without one, so the harness gate rejects the one card a runner can offer. (4) Hyprland is not in Ubuntu 24.04's repositories; `docker run --privileged -v /dev:/dev -v /sys:/sys archlinux` followed by `pacman -S hyprland grim bubblewrap mesa` gives Hyprland 0.56.2 with mesa 26.2 llvmpipe, and `bwrap` works inside that container. (5) Launched by hand in that container (root, `--i-am-really-stupid`, `LIBSEAT_BACKEND=noop`, `AQ_DRM_DEVICES=/dev/dri/card0`), Hyprland 0.56.2 starts on the forced-off `vkms` card, logs `DRM device has no render node, using primary`, and `hyprctl output create headless` yields `HEADLESS-1` at 1920x1080 (not the 0x0 of an NVIDIA host). Not probed: the tier itself on that card, since the gate refuses it; a green `HYPRTWEAKER_REQUIRE_HARNESS=1 pytest tests/integration` is unproven. **So the recipe stands or falls on one gate change: accept a card without a render node when its driver is `vkms`** (a virtual device that no desktop displays on), and let `drm_wrapped` bind the render node only when there is one. #89's scope excluded gate changes, so it made none; the owner took the gate change in #195.

   **Nightly restored, amended during #195.** `drm_card_problem` accepts a card without a render node when it is `vkms`, and `drm_wrapped` then binds the card alone; every other refusal stands. Since Linux 6.15 `vkms` is a faux device, and the runner shows it as `card0 -> /sys/devices/faux/vkms` with driver `faux_driver` (run 36957432658); the gate matches that shape. The `harness` job in `.github/workflows/ci.yml` runs on `schedule` (nightly) and `workflow_dispatch` only; the per-commit jobs exclude `schedule`. It loads `vkms`, forces its connectors off, and runs `HYPRTWEAKER_REQUIRE_HARNESS=1 pytest tests/integration` in a privileged `archlinux` container as an unprivileged user (Hyprland refuses root, and the harness passes no override), after `sysctl kernel.apparmor_restrict_unprivileged_userns=0` so that user's `bwrap` gets a user namespace. The first run (36957432658) found the probe windows' `class` read `probe_window.py`: without a session bus GDK takes the Wayland app id from the program name, so `probe_window.py` now sets it (a #88 defect). Green: runs 36957877501 and 36958325374 on Hyprland 0.56.2, each 37 passed, 1 xfailed, 6 skipped — the four `test_ipc_live.py` tests and `test_schema_reproducible.py` read a host Hyprland session and the user-manager test needs systemd, none of which a container has. A `schedule` trigger fires only from the default branch, so the nightly trigger itself was proven by dispatch.
4. **UI smoke (per-commit)** — `tests/ui/`: the shell, pages and dialogs assembled and driven through real widgets, to prove they wire up, not how they look (appearance is a widget probe or a screenshot from `tools/sandbox.py`). Runs where GTK4, libadwaita and `Xvfb` exist and skips itself elsewhere, so the engine tiers stay runnable on a bare machine; `HYPRTWEAKER_REQUIRE_UI=1` makes that skip a failure, and CI sets it on the job that installs GTK. Each pytest process opens an Xvfb display of its own, so the tier runs under `pytest -n auto` and never maps a window on the desktop session; `HYPRTWEAKER_UI_HOST_DISPLAY=1` puts it on the host display on purpose. No compositor is needed. Its test modules import the toolkit inside the test functions so a machine without PyGObject skips instead of failing at collection. **Added during #48 and amended during #146 and #147.** This tier was built under spec #48 and is numbered 4 rather than slotted in, because other documents and test files cite tiers 2 and 3 by number.

### Build & conventions

- **meson** is the canonical build now (GNOME convention; grows into desktop file/icons/gresource install later — distribution packaging itself remains an open map item). Dev loop: `meson devenv`.
- `pyproject.toml` carries Python tooling: **ruff** (lint + format), **mypy** on `engine/` plus the toolkit-free modules above it (`session.py`, `ui/pages/plan.py`, `ui/pages/tasks.py`, `ui/pages/declaration_kinds.py`, `ui/pages/entity_text.py`, `ui/rows/state.py`, `ui/rows/gesture.py`, `ui/search.py`; amended during #56, #57, #72, #137 and #220 — the exemption was earned by PyGObject's partial stubs and `gi`-dynamic code, neither of which applies to a module that never imports `gi`, so every UI module that decides rather than draws joins this list; `pyproject.toml` `[tool.mypy] files` is the list, and a module that never imports `gi` belongs on it), pytest config.
- GitHub Actions CI: ruff + mypy + unit tests + overlay completeness test, the UI tier (`ui-smoke`, with `HYPRTWEAKER_REQUIRE_UI=1`), the meson build and `meson test`, and a skip ceiling that fails a run with more skipped tests than expected (`SKIP_CEILING` in `.github/workflows/ci.yml`). Nightly and on dispatch only, the Harness tier (`harness`, tier 3, with `HYPRTWEAKER_REQUIRE_HARNESS=1`; amended during #195).
- Commit style unchanged.

## Consequences

- Every ADR-0005..0010 mechanism has a named home; `/to-spec` can address packages, not prose.
- The gi-mask test makes the seam a build invariant instead of a convention.
- Golden-file tests make importer/writer regressions diff-shaped and reviewable.
- The integration harness reuses proven prototype code; promoting it is porting, not research.
- Shipping per-version schemas means a release is pinned to the Hyprland versions it was generated against; drift is handled by degradation states, not by guessing.

## Alternatives considered

- **Two distributions (engine lib + app)** — rejected: one audience (ADR-0004), one repo, no consumer for a standalone engine; the seam is a package boundary, not a release boundary.
- **Blueprint for the static shell** — rejected for now: one more toolchain dep for a small hand-written surface; generated UI can't use it anyway.
- **Runtime schema generation on the user's machine** — rejected: needs a running Hyprland of that exact version at first launch, unreproducible bug reports, and the Overlay must be curated against a known option list anyway.
- **Integration tests per-commit** — rejected: ~45 s/config × 7 rices is minutes per push; on-demand keeps the harness honest without stalling the loop.
- **Integration tests nightly in CI** — rejected on discovery during #55 (the runner has no seat, so the job could only ever skip), adopted in #195: the seat was not the blocker but a render-node-less card was, and the gate now accepts `vkms`. See tier 3.
- **A self-hosted runner for tier 3** — rejected in #195: its cost and upkeep fall on the owner, and a stock runner with `vkms` hosts the tier.
- **mypy across the UI** — rejected: PyGObject stubs are perpetually partial; the cost lands on the layer with the least logic.
