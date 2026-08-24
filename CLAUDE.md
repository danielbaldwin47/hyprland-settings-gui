# hyprland-settings-gui

A polished GUI settings app for Hyprland that reads and writes the new Lua config (`hl.*` API, Hyprland ≥ 0.56).

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues on this repo (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Hyprland release check

On a new Hyprland release (`Release check: Hyprland <ver>` issue), follow `docs/agents/hyprland-release-check.md`.

### Self-landing

Agents merge their own PRs — pre-authorized, background agents included. Gates and merge steps: `docs/agents/self-landing.md`.

### Needs from you

Leftovers for the human — tasks or decisions surfacing during ticket work — go to the inbox issue and never block the chain. Protocol: `docs/agents/needs-from-you.md`.

### Code review

`/code-review` in this repo always means `mattpocock-skills:code-review` — the two-axis (Standards / Spec) review.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working in this repo

### Dev tools

Shared venv at `.venv` (system-site-packages: `gi` importable; ruff, mypy, pytest installed). Use `.venv/bin/<tool>` — never create another venv, in `/tmp` or anywhere.

### Orientation

Read `CONTEXT.md` first; delegate anything broader to a read-only subagent (`cavecrew-investigator` / Explore) and take targeted-range reads only — whole-file surveys of this repo have cost sessions 90k+ context. Map:

- `src/hyprtweaker/engine/` — config engine: `importer/` (hyprlang → model), `schema/` (option schema: sources/resolve/infer), `model/` (options, values), `writer/` (Lua emit), `apply/` (transaction pipeline), `ipc/` (hyprctl commands/events), `state/` (manifest)
- `src/hyprtweaker/session.py` — session layer bridging engine and UI
- `src/hyprtweaker/ui/` — `shell/` (window, runtime), `pages/` (plan, config), `rows/` (factory, chrome, state), `dialogs/`
- `tests/` — `unit/`, `integration/`, `ui/`, `golden/`, `static/`; `corpus/` is third-party rice fixtures, excluded from lint

### UI verification

Probe widget state programmatically (properties, adjustments) before any screenshot loop — a scroll bug once burned 10 screenshot cycles that two probes settled. When a screenshot is warranted, crop to the app window, not the full output.

A ticket whose deliverable is UI-facing (a page, dialog, or widget the user sees) requires at least one verification beyond the headless test tiers — a programmatic widget probe of the running app or a cropped screenshot — stated in the PR body. One leg rewrote the entire Binds page and never once looked at it; green smoke tests are assembly proof, not appearance or interaction proof.

### Live probes

Hyprland is installed; settle spec/behavior disputes with `hyprctl` (`-j` for JSON) against the running compositor rather than by reading docs harder.

Probes that *mutate* the running compositor (`hyprctl keyword`, `hyprctl reload`, temporary binds) snapshot the touched state first and restore + verify it after — this is the human's live session, not a fixture. Count what you changed (e.g. bind count before/after) and say so in the transcript.

### Ticket sizing (`/to-tickets` sessions)

Budget one implementing session ≈ 120k context per ticket: one package or subsystem, its tests included. Heavy UI-visual verification splits into its own ticket. Every ticket of one audited 7-ticket run overshot this 1.4–3.5x — err small. Every blocking edge names the artifact it waits for ("needs class X from #N's diff") — topic adjacency is not an edge: one wrong edge cost a leg 173 idle minutes, and one audited edge ran backwards.
