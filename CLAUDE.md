# hyprland-settings-gui

A polished GUI settings app for Hyprland that reads and writes the new Lua config (`hl.*` API, Hyprland ≥ 0.56).

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues on this repo (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Hyprland release check

On a new Hyprland release (`Release check: Hyprland <ver>` issue), follow `docs/agents/hyprland-release-check.md`.

### Specs and tickets

`/to-spec` and `/to-tickets` read `docs/agents/tickets.md` before drafting: what a spec and a ticket carry here, ticket size, the Model line, and the critic that reads a draft before it is published.

### Effort workflow

One branch and one draft PR per effort, specs merged into it, one review per spec and one per effort, CI once the PR leaves draft, the owner merges. See `docs/agents/implement-spec.md`; it overrides the `implement-spec` skill, and names each subagent's model and brief.

### Needs from you

Owner calls go in the effort PR's ready comment; anything outside an effort goes to the inbox issue. See `docs/agents/needs-from-you.md`.

### Code review

`/code-review`, wherever this repo's docs say it, is `/mattpocock-skills:code-review`, invoked by that full name: a second skill named `code-review` ships with Claude Code.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working in this repo

- **UX is king.** Where a ticket, an open call or a review leaves a choice, take the reading that gives the end user the better experience: clear copy, visible state, safe defaults, no dead ends. Implementation convenience comes second.
- Agent-facing docs (this file, `CONTEXT.md`, everything under `docs/agents/`): load `/mattpocock-skills:writing-for-agents` before writing or editing one.
- Shared venv at `.venv` (system-site-packages: `gi` importable; ruff, mypy, pytest, pytest-xdist installed). Use `.venv/bin/<tool>`, from worktrees too; never create another venv. Done means the checks in `docs/agents/local-checks.md` § Done checks pass.
- The owner's desktop compositor is their daily session (Omarchy, Lua config). Run the app with `tools/sandbox.py` (windowless: nothing of an agent's may map on the owner's desktop), and send every live probe (`hyprctl keyword`, `dispatch`, `reload`, temporary binds) to a nested Hyprland: `docs/agents/local-checks.md` § Running the app. Settle spec and behaviour disputes there with `hyprctl -j` rather than by reading docs harder.
- UI-facing work (a page, dialog or widget the user sees) is proven by a widget probe or a cropped screenshot, not only by green UI-tier tests: one session rewrote the entire Binds page and never once looked at it.

### Orientation

Read `CONTEXT.md` first; delegate anything broader to a read-only subagent (Explore) and take targeted-range reads only — whole-file surveys of this repo have cost sessions 90k+ context. Build on the investigator's returned map — the #131 session re-derived it with its own reads and greps and spent 78k before its first edit. Map:

- `src/hyprtweaker/engine/` — config engine: `importer/` (hyprlang → model), `schema/` (option schema: sources/resolve/infer), `model/` (options, values), `writer/` (Lua emit), `apply/` (transaction pipeline), `ipc/` (hyprctl commands/events), `state/` (manifest)
- `src/hyprtweaker/session.py` — session layer bridging engine and UI
- `src/hyprtweaker/ui/` — `shell/` (window, runtime), `pages/` (plan, config), `rows/` (factory, chrome, state), `dialogs/`
- `tests/` — `unit/`, `integration/`, `ui/`, `golden/`, `static/`; `corpus/` is third-party rice fixtures, excluded from lint
- `tools/` — `gen_schema.py` (release check), `sandbox.py` (the app against a nested Hyprland)

### Session budget

Implementation green is the halfway mark: #131's fixes were green at 150k context and the session peaked at 327k on verification and review aftermath. Verification matches ticket scope; out-of-scope polish becomes a follow-up ticket. While reviews or CI run, arm one Monitor and hold.
