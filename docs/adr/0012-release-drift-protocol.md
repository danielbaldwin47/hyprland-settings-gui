# ADR-0012: Hyprland release drift protocol

**Status:** accepted — 2026-08-19

Read by the agent about to change hyprland release drift protocol, before the first edit; the Status line says what is on `main` now.

## Context

`hyprctl -j descriptions` changes every Hyprland release, and the Tasks view's Page ↔ Section mapping, the Overlay, and the engine's typed dispatcher/rule tables are curated by hand (#7, #8, ADR-0011). Without a standing protocol they rot silently — the Config view regenerates itself, the curated layers do not. ADR-0011 fixed the schema layer (per-version Generated schema + version-independent Overlay + CI completeness test) but left open who reacts to a release, what the reaction covers, what the app does on versions it has never seen, and what happens to options a release removes.

## Decision

### Pinning and the unseen version

Generated schemas are produced at build time per supported release and shipped with the app (ADR-0011 unchanged). At runtime: exact version match, else nearest lower schema. On a **newer** version than any shipped schema, the app additionally supplements from the live `hyprctl -j descriptions`: options absent from the shipped schema get minimal shape-inferred records (no stub or source data, conservative widgets) and render flagged in their Section's *New in \<version\>* group. The supplement is a degradation state, not a schema source — the next release check replaces it with a real Generated schema.

### Support window: latest + previous

Each app release ships Generated schemas for the current and the previous Hyprland release. Older versions down to 0.56 (the Lua floor) get nearest-lower degradation, best-effort and untested. The Overlay stays version-independent; `deprecated_in` / `renamed_from` keep entries for retired options harmless across the window.

### Retirement: retire and keep

When a release removes an option the user has set, the app **stops emitting it and keeps the value**: the model marks it Retired-in-\<ver\>, the value persists in the Manifest, the Row is badged, and a one-time notice lists the release's retired options. If the option returns (downgrade, or a rename mapped by `renamed_from`) the value is restored — renames migrate silently with an Info notice. Emitting a removed key is a config error under Lua, and silently discarding user state is worse than carrying it.

### Trigger: watcher → agent → PR

A scheduled watcher (weekly cron, e.g. a GitHub Action polling `hyprwm/Hyprland` releases) opens a `Release check: Hyprland <ver>` issue labelled `ready-for-agent` per release. An agent runs the protocol in `docs/agents/hyprland-release-check.md` — regenerate, three-layer diff (schema / stub API / wiki), curate, verify — and opens one PR. A human reviews and merges.

### Done bar: CI completeness + reviewed diff

A release is **handled** when the CI overlay completeness test passes on the new schema (every added option has widget, nullability, title and its flagged heuristics covered) and the diff PR is human-reviewed. Tasks placement (`group`/`order`), `help`, and `unit` are polish that may lag — until curated, added options live in *New in \<version\>* groups, which is a designed degradation (#7), not a defect. New entity kinds are out of the protocol's scope and become their own issues.

## Consequences

- Drift is caught by a standing loop, not by a user noticing a wrong page; the curated Tasks view degrades visibly instead of rotting silently.
- The support window bounds curation and CI cost to two schemas per app release.
- Retirement never destroys user state and never emits known-bad config.
- The protocol doc is the agent's single source for the steps; this ADR holds only the policy.

## Alternatives considered

- **Strip removed options silently** — rejected: destroys user state with no trace beyond the Journal.
- **Move removed options to `user.lua`** — rejected: still a config error on reload, now in the one file the app must not rewrite.
- **Keep emitting until the user acts** — rejected: ships a known config error to every reload; #31's error surface is for unexpected failures.
- **Latest-only window** — rejected: one `pacman -Syu` lag on any second machine drops it off the map for pennies of extra curation.
- **All schemas since 0.56** — rejected: unbounded CI matrix and curation for versions nobody runs.
- **Manual trigger (release-checklist step or hand-opened issue)** — rejected as the default: relies on noticing; the watcher is cheap and the manual path still works when it misfires.
- **Requiring Tasks placement or a Harness pass before "handled"** — rejected: both are valuable but slow the loop; the fallback group and non-blocking Harness issues keep them honest.
