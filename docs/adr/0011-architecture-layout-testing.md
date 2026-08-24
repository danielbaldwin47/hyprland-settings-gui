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

pytest, three tiers; the engine carries the coverage, the UI stays thin:

1. **Unit (per-commit)** — pure-engine golden files:
   - Importer: `.conf` tree → model snapshot JSON
   - Writer: model → Lua text
   - Round-trip: import → write → re-import must be identical
   - Fixtures: `tests/corpus/` rices + synthetic fixtures for grammar edges the corpus lacks (`source=` globs, `{{ }}` arithmetic, sentinel values, `# hyprlang` directives)
2. **Static (per-commit, when Hyprland present)** — `Hyprland --verify-config` over every written Lua output; no compositor needed.
3. **Integration (on demand)** — prototype #9's nested-headless-Hyprland harness promoted to `tests/integration/`: state diff via `hyprctl -j` + screenshot diff. Marked `-m hyprland`, auto-skipped when no Hyprland binary; too slow (~45 s/config) for per-commit.

   **"nightly" dropped, amended during #55.** A nested Hyprland needs a **host Wayland session**: `HYPRLAND_HEADLESS_ONLY=1` is not sufficient on its own — backend creation fails with `CBackend::create() failed!` even when a DRM render node is passed explicitly, because the DRM backend wants a *seat*, and on a developer box the login session already owns it. A stock GitHub `ubuntu-latest` runner has neither, so a nightly job would skip 100% of the compositor tests and report green — worse than no job, because it would read as coverage. The tier therefore runs on demand, and `HYPRTWEAKER_REQUIRE_HARNESS=1` turns the skip into a hard failure for any environment that is *supposed* to be able to host it. Nightly remains desirable and is blocked on a virtual seat in CI (`seatd` + `vkms`, or nesting inside a headless sway/cage); until that spike lands, on-demand is the honest cadence.

### Build & conventions

- **meson** is the canonical build now (GNOME convention; grows into desktop file/icons/gresource install later — distribution packaging itself remains an open map item). Dev loop: `meson devenv`.
- `pyproject.toml` carries Python tooling: **ruff** (lint + format), **mypy** on `engine/` plus the toolkit-free modules above it (`session.py`, `ui/pages/plan.py`, `ui/rows/state.py`; amended during #56 and again during #57 — the exemption was earned by PyGObject's partial stubs and `gi`-dynamic code, neither of which applies to a module that never imports `gi`, so every UI module that decides rather than draws joins this list), pytest config.
- GitHub Actions CI: ruff + mypy + unit tests + overlay completeness test.
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
- **Integration tests nightly in CI** — rejected on discovery (amended during #55): the runner has no seat, so the job could only ever skip. See tier 3 above; revisit if the virtual-seat spike succeeds.
- **mypy across the UI** — rejected: PyGObject stubs are perpetually partial; the cost lands on the layer with the least logic.
