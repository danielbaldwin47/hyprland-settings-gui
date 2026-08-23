# ADR-0016: Error surfacing & recovery after bad writes

**Status:** accepted — 2026-08-22

## Context

ADR-0010 fixed the mechanics: every Apply transaction returns a structured ApplyResult (ok / config errors / read-back mismatch / timeout), snapshots the pre-write bytes of every dirty Module into the Journal, and provides restore-last-good as a mechanism. This ADR owns the policy — when recovery fires automatically and what the user sees. Facts that shape it (`docs/research/live-apply.md`):

- `configerrors` lines are `file:line`-prefixed, so every error is attributable to a file.
- A broken `require()`d Module is *silently absent at runtime* (its pcall scope fails, the rest loads) but *not silent to the app*: `configerrors` carries a `require("<mod>"): …` line. The live consequence can be severe — a broken `binds.lua` means zero keybinds, which also triggers Hyprland's emergency mode.
- A syntax error in the **main file** is different: phase-1 refusal keeps the previous live state intact while the files on disk are broken — the "Hyprland rejected the last write and is running the previous config" state mocked in #7. The app `luac -p`-gates its own writes, so this state is only reachable via hand edits.
- The app never writes `user.lua` or Bridge modules, so it cannot fix errors in them — but it owns the Entrypoint and can control whether they are required.
- Reload always ends with `configreloaded`; errors survive only until the next reload or any eval.

## Decision

### Attribution drives policy

Each `configerrors` line is attributed by its file prefix to an **Ownership class**, and the class decides the recovery:

| Class | Recovery |
| --- | --- |
| App Module written by **this transaction** | **Auto-revert** (below) |
| App Module **not** written this transaction (hand edit, foreign reload) | Banner + actions **Restore last good** / **Open file**. No auto-write — a hand edit is user intent (ADR-0005 hand-edit detection already warns). |
| `user.lua` / Bridge module | Banner + **Open file at line**. The app never modifies foreign files; recovery it *can* offer is Quarantine (below). |
| Entrypoint (phase-1 refusal: previous config still live) | Red Banner "Hyprland rejected the last write and is running the previous config" + **Show error** + **Fix**: regenerate the Entrypoint — it is app-owned and always regenerable. |

### Auto-revert

When an ApplyResult carries config errors attributed to a Module this transaction just wrote:

1. Restore that Module's pre-write Snapshot bytes through a normal Apply transaction.
2. Revert the model delta and drop the failed gesture from the undo stack (it never becomes a redo).
3. Toast: "Hyprland rejected the change — reverted", with a **Details** action opening the error dialog.

No confirmation — instant apply has no cancel, and the restored bytes are the state that was live and confirmed moments before, so the cycle cannot loop. If the restore transaction itself errors (e.g. schema drift after a Hyprland upgrade), escalate to the Banner and stop auto-writing until the user acts.

### Last known good

**Last known good** is per-Module: the newest Journal Snapshot whose transaction confirmed clean (empty `configerrors` + read-back ok). Journal entries gain a `confirmed` flag written after Read-back. **Restore last good** restores implicated Modules only — never the whole tree.

### Quarantine (user.lua)

For errors in `user.lua` the app offers a consent-gated **"Disable user.lua until fixed"**: regenerate the Entrypoint without the `require("user")` line, badge the state prominently (Banner persists in warning form), one-click re-enable. Explicit, reversible, and the only recovery the app can offer for a file it must not edit. The same action is offered per Bridge module.

### Zero-binds emergency

When a reload ends with config errors **and zero binds** (Hyprland's emergency mode), stranded-user beats hand-edit sanctity: the app auto-restores implicated app-owned Modules to last known good without the class-2 manual gate. The overwritten hand edit is preserved in the Journal and reported in the Banner. If binds are still zero because the error is in a foreign file, the Banner states it plainly ("Your keybinds are not loaded — error in user.lua:12") and offers Quarantine.

### Surfacing

- **One persistent Banner** (`Adw.Banner` under the header bar, app-wide) for any unhealthy state: non-empty `configerrors` after the last reload, Entrypoint refusal, active Quarantine. Its button opens **one error dialog**: monospace `file:line` list with per-class action buttons. No dedicated "Problems" page — errors are rare and file-scoped.
- **Toasts** only for transient auto-revert events.
- **Per-Row badges** stay reserved for key-scoped states already decided elsewhere (drift "overridden in user.lua", Pending restart, Retired). Config errors are file-scoped and never appear on Rows. An unexplained read-back mismatch (value didn't take, no error, no override) badges the Row "didn't apply" and joins the Banner.
- **Startup and foreign reloads** feed the same pipeline: on launch and on any uncorrelated `configreloaded`, the full re-read + drift scan (ADR-0010) attributes any errors and raises the same Banner — breakage that happened while the app was closed surfaces identically.
- **Timeout** ApplyResult: re-poll once; if still unconfirmed, treat as a foreign-unknown state — full re-read, Banner if errors.

## Consequences

- The Journal grows a `confirmed` flag and becomes the source of "last known good" — pruning must never drop the newest confirmed Snapshot of a Module.
- The Entrypoint generator needs a Quarantine state (per-require disable) persisted in the Manifest.
- Auto-revert requires the Apply queue to admit a priority restore transaction while the failed one is being reported.
- The engine needs a binds-count probe after Read-back to detect the emergency case.

## Alternatives considered

- **Banner + manual restore for own-write errors** — rejected: instant apply has no cancel; a broken module can strand the user (all binds gone) while they hunt for a button.
- **Auto-editing user.lua (comment out the offending line)** — rejected: the app never writes foreign files (ADR-0005); Quarantine achieves recovery without touching the file.
- **Whole-tree restore on any error** — rejected: blast radius; per-Module restore matches the per-file snapshot design (ADR-0005) and keeps unrelated recent changes.
- **Dedicated Problems page** — rejected: errors are rare, file-scoped, and actionable from one dialog; a page would be furniture.
- **Treating the zero-binds emergency like any class-2 hand edit** — rejected: without binds the user may be unable to open a terminal to fix anything; the Journal preserves the overwritten edit.
