# hyprtweaker

A polished GUI settings app for Hyprland that reads and writes the new Lua config (`hl.*` API, Hyprland ≥ 0.56), for the Hyprland user who would rather not hand-port a rice. GitHub issue #48 (Spec: hyprtweaker v1) specifies it, every decision in it backed by `docs/adr/`; `CONTEXT.md` is the vocabulary; `docs/agents/gate.md` is what must be true before work lands. A doc is read by the section a ticket names, `grep -n '^#' <file>` for its index.

## Repo map

- `src/hyprtweaker/engine/` — config engine, UI-free (ADR-0011): `importer` (hyprlang → model), `schema` (option schema: sources/resolve/infer), `model` (options, values), `writer` (Lua emit), `apply` (transaction pipeline), `ipc` (hyprctl commands/events), `state` (manifest, journal), `migration` (wizard flow, backup, export).
- `src/hyprtweaker/session.py` — session layer bridging engine and UI.
- `src/hyprtweaker/ui/` — `shell` (window, runtime), `pages` (plan, config), `rows` (factory, chrome, state), `dialogs`.
- `data/schema/` — the Generated schema per Hyprland version and the hand-curated Overlay; `tools/gen_schema.py` builds the former on a Release check.
- `tests/` — `unit`, `static`, `ui` (the per-commit tiers), `integration` (on demand, nested compositor), `golden` (frozen Importer/Writer output); `corpus` is third-party rice fixtures, excluded from lint.
- `docs/agents/` — `docs/agents/gate.md`; `docs/agents/tickets.md`, read before a ticket is written; `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/self-landing.md`, `docs/agents/needs-from-you.md`, `docs/agents/hyprland-release-check.md`, `docs/agents/domain.md`.
- `docs/adr/` — decisions; `docs/research/` and `docs/design/` — resolved wayfinder tickets, read by the section a ticket names.
- `tools/` — `tools/gate --help` names its subcommands and the last line each ends in.
- `CODING_STANDARDS.md` *(grows)* — what `/code-review` holds a diff to: a rule enters once a review has found it by hand twice, with its commits after it.

Every source module opens with a one-line purpose, so `grep -rm1 '^"""' src --include='*.py'` is the whole codebase on one screen.

Dev tools: the shared venv at `.venv` (system-site-packages, so `gi` resolves; ruff, mypy, pytest installed — recreate it with the two lines in `README.md` §Check). Use `.venv/bin/<tool>`.

## Gate

`tools/gate check` ends in `gate check: pass` or `gate check: fail (<step>)` — fmt, lint, types, unit, ui, cite, usage (`docs/agents/gate.md`) — and a doc that drifts from its tool is a failing step: `usage` holds `--help` to the subcommands dispatched and `docs/agents/gate.md` to naming each, and `cite` holds every path this file and `docs/agents/` name to a file on disk. A check reads what the run produced, the file written, the pixels, the exit code (Quill #108: the app agreed with its own defect in good faith, and six critics read it correct). Here that means a UI-facing ticket (a page, dialog or widget the user sees) proves itself with a programmatic widget probe of the running app or a screenshot cropped to the app window, stated in the PR body; green smoke tests are assembly proof, not appearance or interaction proof. An engine ticket proves itself in its unit/static tiers. Hyprland is installed, so a spec or behavior dispute is settled with `hyprctl` (`-j` for JSON) against the running compositor.

A facet once won is never lost: `tests/golden/` freezes the Importer's and Writer's output and the unit tier re-proves it; `tests/static` runs `Hyprland --verify-config` over every written Module; `tests/unit/test_overlay_completeness.py` fails the build on any Curation flag the Overlay leaves unanswered (ADR-0011); and `SKIP_CEILING` in `.github/workflows/ci.yml` fails a job whose skips exceed today's intentional count — lua5.4's absence once skipped 72 tests while the job stayed green. The PR that adds or removes an intentional skip moves that number with it.

## Vocabulary

`CONTEXT.md` defines each term with an *Avoid* line, and every doc, ticket, test name and commit uses the term. A concept with no term is added there in the same change. `docs/agents/domain.md` is how the skills consume it.

## Decisions

`docs/adr/`: context, options weighed, consequence, who and when. A change touching one adds a dated status line in its header, stands, narrowed or superseded, stating what is on `main` now (the `**Status:**` line; ADR-0011 §Testing's "amended during #55" is the shape). A result that contradicts an ADR says so out loud rather than silently overriding it (`docs/agents/domain.md` §Flag ADR conflicts).

## Rules earn their place *(grows)*

A rule in this file or under `docs/agents/` carries the date, ticket or measurement where the alternative failed. A rule broken after it was written becomes a guard under .claude/hooks/ that refuses exactly that shape, passes every doubt, and cites the paragraph it enforces (Quill: the twentieth judge run died at the foreground cap after the rule was written, 2026-09-01). A rule with no incident is a claim under test: the first run that contradicts it rewrites it with the evidence or deletes it.

- Seed session (this file, the gate, the doc reader lines), 2026-09-04: peak context ≈ 70k, 26 tool calls, one background test run — the shape a Size line quotes.
- Orientation is one investigator fork returning `file:line` ranges, read as targeted ranges: whole-file surveys of this repo have cost sessions 90k+ context, and the #131 leg re-derived the investigator's map with its own reads and greps and spent 78k before its first edit (2026-08-25, #141).
- Review findings are fixed in a fresh subagent handed the findings list and the branch; the main thread takes back only the fix diff summary and verdicts: one #131 session fixed in-thread and grew 97k after its implementation was already green.
- Widget state (properties, adjustments) is probed programmatically before any screenshot loop: a scroll bug once burned 10 screenshot cycles that two probes settled (run-3 audit, 2026-08-23).
- A UI-facing ticket states one verification beyond the headless tiers in its PR body: one leg rewrote the entire Binds page and never once looked at it (run-3 audit, 2026-08-23).
- Verification matches ticket scope and out-of-scope polish becomes a follow-up ticket: #131 spent ~45k on UI probes for an importer ticket, sweep hardening and mutation-proofs of a test gate.
- While reviews or CI run, arm one Monitor and hold: hand-polling added ~15k of wait turns to #131.
- Tickets are sized at ≈120k context with edges that name an artifact: every ticket of one audited 7-ticket run overshot 1.4–3.5x, and one wrong edge cost a leg 173 idle minutes (`docs/agents/tickets.md`).
- Agent-filed tickets get `needs-triage`, never `ready-for-agent`: one run shipped seven self-approved tickets (`docs/agents/triage-labels.md`).
- The inbox body is folded once per run, never re-read per leg: 10–50k tokens per leg on a 44KB body (`docs/agents/needs-from-you.md`).

Claims under test (no incident yet):

- Never create another venv, in `/tmp` or anywhere; `.venv` is the one.
- Settle spec/behavior disputes with `hyprctl` against the running compositor rather than by reading docs harder.
- Probes that *mutate* the running compositor (`hyprctl keyword`, `hyprctl reload`, temporary binds) snapshot the touched state first and restore + verify it after — this is the human's live session, not a fixture. Count what you changed (e.g. bind count before/after) and say so in the transcript.
- When a screenshot is warranted, crop to the app window, not the full output.

## Context

Cost is the number of tool calls: each leaves reasoning that stays for the session, and the smart zone is about 120k — implementation green is the halfway mark, since #131's fixes were green at 150k and the session peaked at 327k on verification and review aftermath. So the ticket is fetched once, with the sections its Reading line names; `CONTEXT.md` is read first; orientation before the first edit in an area is one fork (`cavecrew-investigator` / Explore) that returns `file:line` ranges, and this context reads the ranges and builds on that map; the Gate is one `tools/gate check`; a file opened with Read, Edit or Write changes through Edit, since an in-place `sed` on it comes back as a diff snippet; a run that can pass the ten-minute foreground cap (the Harness at ~45 s per config over `tests/corpus`) is launched in the background, everything independent of it is finished, and the turn ends. That ending is the wait.

## Landing

Work whose evidence is mechanical lands itself (`docs/agents/self-landing.md`: four gates, merge steps, cleanup — pre-authorized, background agents included): Gate green, `/code-review` done — in this repo always `mattpocock-skills:code-review`, the two-axis (Standards / Spec) review — the PR merged with `Closes #N`, and the session's cost (peak context, tool calls) in the closing comment, which the next ticket's Size line quotes. Work whose evidence needs a hand closes on the owner's `hand test: pass` with the installed version, from "do X, see Y" steps written out whole in one comment. Leftovers for the human go to the inbox issue and never block the chain (`docs/agents/needs-from-you.md`). Tickets live as GitHub Issues via `gh` (`docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/tickets.md`); a `Release check: Hyprland <ver>` issue follows `docs/agents/hyprland-release-check.md`.

## Docs

Any document an agent reads is edited through `/writing-for-agents` and opens with one line naming its reader and the moment it is read.
