# ADR-0010: Live-apply pipeline

**Status:** accepted — 2026-08-19

## Context

ADR-0003 decided instant apply and deferred the pipeline design to research. The facts are now in:

- Reload is a full teardown + re-execute; no Hyprland-side debounce; each `IN_CLOSE_WRITE` triggers a synchronous reload; atomic-rename writes are invisible to the watcher until an explicit reload re-attaches it (`docs/research/live-apply.md`).
- Measured on a nested Lua-engine Hyprland (#8): `eval` over the IPC socket 0.4 ms; atomic rename + explicit reload → value applied 25.4 ms; any `hyprctl` process spawn ~20 ms. `configreloaded` on socket2 fires ~11 ms **before** the new value is readable.
- `hyprctl reload` always replies `"ok"`; errors live in `configerrors`, which is cleared by the next reload **and by any eval**.
- A syntactically broken `require()`d module is silently absent while the rest loads — whole-and-valid-by-construction writes are mandatory (ADR-0005).
- 8 known restart-only cases; the Schema overlay carries a `restart` flag (#3, #8).
- Error-surfacing UI and auto-revert *policy* are owned by #31; this ADR fixes the mechanics they hook into.

## Decision

### Transport

The app speaks `.socket.sock` (commands) and `.socket2.sock` (events) **directly** and never spawns `hyprctl`. One long-lived socket2 listener runs for the app's lifetime.

### Apply transaction

One user-visible change = one **Apply transaction**:

1. Model edit marks Modules dirty. App-side debounce ~150 ms after last change; commit gestures (toggle, combo select, focus-out, slider release) apply immediately. Hyprland has no debounce, so the app is the coalescer.
2. Render every dirty Module deterministically, whole-file.
3. Local syntax gate: `luac -p` on each rendered file (Lua 5.5). Failure aborts the write before anything touches disk — a writer bug, never a user error. (`Hyprland --verify-config` is too heavy per-write and its `hl.dsp.*` is nil; it stays a migration-time gate, ADR-0009.)
4. Write all dirty Modules via atomic rename, then one explicit `reload` over the socket. Guaranteed single reload per transaction; no partial file is ever executed.
5. Confirm: wait for `configreloaded` (timeout 2 s = 1.5 s watchdog + margin), treat it as "reload started", then read `configerrors` and `getoption` for the touched keys (**Read-back**). No eval between reload and the error read. The Read-back pass doubles as the ADR-0005 drift-badge scan.

Read-back carries a **settle window** (amended during #54): because `configreloaded` fires ~11 ms before the new values are readable, a key that disagrees on the first read is re-read for up to 250 ms before it counts as a mismatch. A key that agrees is never re-read, so the common path stays at one round-trip per key. Without it the first `getoption` after a reload can honestly still answer with the pre-write value, and a false mismatch is the expensive direction: ADR-0016 badges an unexplained mismatch on the Row as "didn't apply" and raises the Banner, and the transaction never counts as confirmed, so its Snapshot never becomes that Module's last known good.

Applies are **serialized through one queue**: one transaction in flight; edits arriving meanwhile coalesce into the next. Reload is O(whole config) and `configerrors` is one global slot — parallel applies cannot attribute errors.

The transaction returns a structured **ApplyResult** — ok / config errors (file:line-prefixed) / read-back mismatch / timeout — which #31 consumes.

Read-back also reports **unconfirmed** keys (amended during #54): ones the compositor would not answer usefully about, as opposed to ones that disagreed. Hyprland 0.56.2 answers `getoption` for both font-weight Options with `invalid type (internal error)`, and a key the running compositor does not know answers `no such option`; neither is evidence the write was wrong, so neither may be reported as a mismatch. A transaction with unconfirmed keys is still `ok` — nothing is known to have failed — but it is not `confirmed`, which is the flag ADR-0016's last-known-good is selected by.

### Eval preview

Continuous controls — those that move under a held pointer, such as sliders — preview per-tick via `eval 'hl.config{...}'` over the socket — sub-frame, correct prop refresh, real parse errors — and run a normal Apply transaction on release. File writes per drag tick are ruled out: each would be a full teardown reload. Eval state is transient (wiped by any reload) and eval wipes `configerrors`, so previews never run while a transaction is confirming. Discrete controls skip preview entirely.

**Colour controls stay modal-commit (amended during #93).** A colour dialog is modal: the user picks, confirms, and the value commits once through the normal Apply path, with no per-tick preview. In the generated Rows today the gradient's angle slider is the only control that previews per tick (`docs/design/row-catalogue.md`).

### Rollback mechanism

Before each write, the transaction snapshots the previous bytes of every dirty Module into the ADR-0005 Journal. The snapshot is durable before the rename (amended during #132): the Writer hands each file it is about to replace to the Journal, which stores its bytes and names them in the state dir's pending record, `state/journal-pending.json`, both fsynced. The record is removed once the transaction's entry is appended. A record found by the next transaction is a write the app died in: the Journal appends it as an `interrupted` entry (never confirmed), and until then `Journal._collect` treats the Snapshots it names as referenced, so garbage collection never deletes the only copy of an overwritten hand edit. **Restore-last-good** (amended during #95) = write a Module's Snapshot bytes to disk, reload once, then re-read from the compositor exactly the Options the Journal recorded those bytes as setting. It is deliberately not an Apply transaction: that renders the model, and would overwrite the very bytes being restored before the reload. The re-read is what brings the model back into step with a file it did not render (`engine/apply/restore.py`). This ADR provides the mechanism; *when* it fires automatically and what the user sees is #31's decision.

### Undo

One **Undo step** = one user gesture (a whole slider drag is one step: value-at-press → value-at-release). Steps are model-level deltas (option/entity old → new), held in a single global linear in-memory stack, replayed through the normal Apply pipeline. An Entity step holds whole lists; a step whose list changed off the stack (a foreign reload adopted a hand edit, a profile was activated) is dropped, because replaying it would overwrite that change (amended during #189). The stack dies with the session; the Journal remains the durable history but is not walkable as undo. Byte-level file undo is rejected as the way to undo a model delta — it fights the tri-state model. Restore-last-good is a different operation, not an undo step: it lays down Snapshot bytes and then brings the model into step by re-read (§Rollback mechanism), so the model is never left stale and the file never becomes a second source of truth.

### Restart-flagged options

Options with the overlay `restart` flag write normally but skip Read-back verification and mark the app's **Pending restart** state; the Row badges "takes effect after Hyprland restart" (Row state from #7). No queuing, no deferred writes. `hl.env` stickiness (removal needs re-login) is surfaced the same way.

### Foreign reloads

Any `configreloaded` not correlated with an in-flight transaction (bridge tools, hand edits, `hyprctl reload` from a script) triggers a full state re-read + drift scan. Correlation is by in-flight flag, not by content.

## Consequences

- The engine needs an async socket layer and an event loop; that seam was already required by ADR-0001 and the Flatpak socket requirement (#6).
- Eval preview is a second, transient apply path — bounded to continuous widgets to keep the surface small.
- Serialization makes worst-case apply latency additive, but at 25 ms/transaction the queue is invisible.
- #31 gets clean inputs: ApplyResult, per-Module snapshots, restore-last-good.

## Alternatives considered

- **In-place write + inotify auto-reload** — 2× faster (13 ms) but can expose a half-written file to the watcher; rejected (#8: "12 ms is not worth the failure mode").
- **Eval-first, write-later for everything** — rejected: eval wipes `configerrors`, its state is lost on any reload, and two sources of truth per commit invite drift.
- **File-only v1 (no eval preview)** — rejected: per-tick reloads during a drag mean VM teardown + bind/animation rebuild many times a second.
- **Persistent undo stack across app restarts** — rejected: Journal already answers "what changed"; cross-session Ctrl+Z adds state for little gain.
- **`Hyprland --verify-config` per write** — rejected: seconds of compositor init per apply, and false failures on `hl.dsp.*`.
