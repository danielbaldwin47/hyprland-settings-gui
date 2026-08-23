# ADR-0018: Scripting surface & plugin scope

**Status:** accepted — 2026-08-22

## Context

Hyprland's Lua config has a scripting half the GUI cannot own: `hl.on` (32 events), `hl.timer`, `hl.layout.register`, function-valued bind/gesture actions, and the `hl.get_*`/`hl.dispatch` runtime queries. All of it is cleared and replayed on every reload and lives only inside the compositor's VM — no IPC query enumerates registered handlers or timers, so the app cannot read them back at runtime. Both importers park whole `hl.on` handlers and closure-valued actions in `legacy.lua` (#9, #30), and `user.lua` is the escape hatch that overrides the GUI (ADR-0005).

Distinct from that: `hl.plugin.load(path)` is declarative (a plain path list Hyprland diffs on reload), `hl.get_loaded_plugins` provides read-back, and plugin config values (`plugin:*`) appear in `descriptions` only once the plugin is loaded. The lua-api-surface research concluded custom layouts should be selectable, never authored.

ADR-0015 deferred two monitor-profile features to this decision: auto-activation on the connected-output set, and a passive "matches profile" toast.

## Decision

### Zero authoring UI for scripting

No editor for event handlers, timers, or custom layouts in v1. Scripting belongs to `user.lua`; the GUI represents it, never writes it.

### Read-only Scripting inventory

One **Scripting** Page (System category in the Tasks view; its own Page in Config view) listing what the escape hatch contains: a best-effort **static scan** of `user.lua` and `legacy.lua` for `hl.on(`, `hl.timer(`, `hl.layout.register(`, and `hl.plugin.load(` calls. Each hit shows its event/name, `file:line`, and an open-in-editor button. The scan never executes or evaluates user Lua, and the page is labelled best-effort — arbitrary code can hide calls behind loops or locals, and a miss costs nothing (the inventory is informational, never state the writer depends on).

### Custom layouts: select, not author

`lua:<name>` layouts found by the static scan join the `general:layout` combo and the workspace-rule layout picker as **Discovered layouts**. Authoring a provider stays in `user.lua`.

### Plugins: editable load list; no hyprpm

A **Plugins** group on the Scripting page edits the ordered `hl.plugin.load` path list — add, remove, enable-toggle — rendered into a canonical `plugins.lua` Module like any entity list. Loaded state is read back via `hl.get_loaded_plugins`. Plugin config options (`plugin:*`) surface through ADR-0012's runtime-supplement pattern: raw generated rows in the Config view, flagged as unschematised. hyprpm — installing, building, updating plugins — is out of scope for v1; the load list points at `.so` paths that already exist.

### ADR-0015 deferrals resolved

- **Profile-match toast: in v1.** While the app is open, its existing socket2 listener (the one refreshing the arrangement canvas) compares the connected-output set against saved Monitor profiles; on a match, a toast offers one-click activation through the normal ADR-0015 apply path. App-open only — no daemon, no generated handlers.
- **Auto-activation: out of scope for v1.** Working with the app closed requires a daemon (rejected, ADR-0006) or GUI-authored `hl.on` handlers (rejected above). Ruled out of this effort, not deferred again.

## Consequences

- The static scanner is a small engine component (regex/token level, no Lua execution) shared by the inventory, the layout combo, and nothing else — its misses must never affect writes.
- `plugins.lua` joins the canonical Module set: Entrypoint require list, Manifest hash, Journal snapshots, drift badge on hand edits.
- Runtime-supplemented `plugin:*` rows reuse the ADR-0012 flagged-row mechanism; no Overlay curation is owed for plugin options.
- The profile-match toast subscribes to the same monitor events as the canvas — no new listener, no background process.
- The importers' behaviour is unchanged: handlers and closures still land in `legacy.lua`; the inventory simply makes that file visible.

## Alternatives considered

- **Authoring UI for handlers/timers** (event picker + command builder) — rejected: the readable subset is tiny, the write-back story into arbitrary user Lua violates ADR-0002, and demand is unproven.
- **No inventory at all** (just "user.lua exists — open in editor") — rejected: cheap to do better; the escape hatch deserves a visible face, and the loss report / drift badges already point users at files they then can't survey.
- **Evaluating user.lua under the recording stub** (as the Lua importer does) to build an exact inventory — rejected: running user code on every app start for a read-only listing is consent-and-safety weight the feature doesn't earn; static scan is good enough for informational use.
- **hyprpm integration** — rejected for v1: separate tool with its own build toolchain and failure modes; the GUI's job ends at the load list.
- **Auto-activation via generated `hl.on` handlers in a Module** — rejected: the GUI would be authoring scripting it cannot read back, and a compositor-side handler rewriting `monitors.lua` races the app's own transactions.
