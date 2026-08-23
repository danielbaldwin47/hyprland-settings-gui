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

Read-back carries a **settle window** (amended during #54): because `configreloaded` fires ~11 ms before the new values are readable, a key that disagrees on the first read is re-read for up to 250 ms before it counts as a mismatch. A key that agrees is never re-read, so the common path stays at one round-trip per key. Without it the first `getoption` after a reload can honestly still answer with the pre-write value — and a false mismatch is the expensive direction, since ADR-0016 wires mismatch to auto-revert, which would undo a change that had in fact applied.

Applies are **serialized through one queue**: one transaction in flight; edits arriving meanwhile coalesce into the next. Reload is O(whole config) and `configerrors` is one global slot — parallel applies cannot attribute errors.

The transaction returns a structured **ApplyResult** — ok / config errors (file:line-prefixed) / read-back mismatch / timeout — which #31 consumes.

### Eval preview

Continuous controls (sliders, colour pickers) preview per-tick via `eval 'hl.config{...}'` over the socket — sub-frame, correct prop refresh, real parse errors — and run a normal Apply transaction on release. File writes per drag tick are ruled out: each would be a full teardown reload. Eval state is transient (wiped by any reload) and eval wipes `configerrors`, so previews never run while a transaction is confirming. Discrete controls skip preview entirely.

### Rollback mechanism

Before each write, the transaction snapshots the previous bytes of every dirty Module into the ADR-0005 Journal. **Restore-last-good** = write those bytes back through a normal Apply transaction. This ADR provides the mechanism; *when* it fires automatically and what the user sees is #31's decision.

### Undo

One **Undo step** = one user gesture (a whole slider drag is one step: value-at-press → value-at-release). Steps are model-level deltas (option/entity old → new), held in a single global linear in-memory stack, replayed through the normal Apply pipeline. The stack dies with the session; the Journal remains the durable history but is not walkable as undo. Byte-level file undo rejected — it fights the tri-state model.

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
