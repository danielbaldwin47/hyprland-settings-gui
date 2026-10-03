# hyprtweaker

A GUI settings app for Hyprland that reads and writes the Lua config (`hl.*` API, Hyprland ≥ 0.56).

## Agent skills

- **Issue tracker**: GitHub Issues on this repo via `gh`; see `docs/agents/issue-tracker.md`.
- **Triage labels**: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`; see `docs/agents/triage-labels.md`.
- **Hyprland release check**: on a `Release check: Hyprland <ver>` issue, follow `docs/agents/hyprland-release-check.md`.
- **Implementing specs**: the upstream `implement-spec` skill, one spec per PR, `/mattpocock-skills:code-review` once per PR. Open calls go in the PR body. A finding outside the spec becomes a `needs-triage` issue, never a build in the same run. The owner merges every PR; a session never does (`.claude/hooks/pr-base-guard.sh`).
- **Code review**: `/code-review` in this repo means `/mattpocock-skills:code-review`, by that full name; a second skill named `code-review` ships with Claude Code.
- **Domain docs**: single-context, `CONTEXT.md` + `docs/adr/`; see `docs/agents/domain.md`.

## Working in this repo

- **UX is king.** Where a choice is open, take the reading that gives the end user the better experience: clear copy, visible state, safe defaults, no dead ends.
- Agent-facing docs (this file, `CONTEXT.md`, `docs/agents/`): load `/mattpocock-skills:writing-for-agents` before editing one.
- Shared venv at `.venv` (system-site-packages: `gi` importable; ruff, mypy, pytest, pytest-xdist installed). Use `.venv/bin/<tool>`, from worktrees too; never create another venv. Done means `docs/agents/local-checks.md` § Done checks pass. One pytest run at a time on this machine: concurrent `-n auto` suites have tripped systemd-oomd.
- The owner's desktop compositor is their daily session. Run the app with `tools/sandbox.py` (windowless) and send every live probe (`hyprctl keyword`, `dispatch`, `reload`, temporary binds) to a nested Hyprland: `local-checks.md` § Running the app. Settle behaviour disputes there with `hyprctl -j`.
- UI-facing work is proven by a widget probe or a cropped screenshot of the running app, not only by green UI-tier tests.

### Orientation

Read `CONTEXT.md` first; take targeted-range reads, and delegate broad surveys to a read-only subagent. Map:

- `src/hyprtweaker/engine/` — config engine: `importer/` (hyprlang → model), `schema/` (option schema: sources/resolve/infer), `model/` (options, values), `writer/` (Lua emit), `apply/` (transaction pipeline), `ipc/` (hyprctl commands/events), `state/` (manifest)
- `src/hyprtweaker/session.py` — session layer bridging engine and UI
- `src/hyprtweaker/ui/` — `shell/` (window, runtime), `pages/` (plan, config), `rows/` (factory, chrome, state), `dialogs/`
- `tests/` — `unit/`, `integration/`, `ui/`, `golden/`, `static/`; `corpus/` is third-party rice fixtures, excluded from lint
- `tools/` — `gen_schema.py` (release check), `sandbox.py` (the app against a nested Hyprland)
