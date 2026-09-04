# ADR-0015: Monitor profiles & hotplug behaviour

**Status:** accepted — 2026-08-22

Read by the agent about to change monitor profiles & hotplug behaviour, before the first edit; the Status line says what is on `main` now.

## Context

The #7 IA mockup shows a "Profile: docked" label on the Displays page; ADR-0008 settled the monitors editor and deliberately spun profiles off. Constraints:

- Hyprland already handles plain hotplug declaratively: monitor rules for disconnected outputs stay in `monitors.lua` dormant and apply on plug; `desc:`-when-unique identity survives replug (ADR-0008); `output = ""` catch-all covers unknown projectors. The docked/undocked *hardware* case needs no profiles at all.
- Profiles earn their place only when the **same connected set** wants different configs (extend vs mirror for a presentation) or when **workspace→monitor pinning** should flip with context.
- Auto-activation on the connected-output set needs a watcher process — ADR-0006 ruled "no daemon" — or `hl.on` event handlers, and the Lua scripting surface is still undecided fog.
- Conditional Lua inside `monitors.lua` would break the canonical machine-parseable contract (ADR-0007/0008): the file must always parse back to exactly the model.
- The glossary already fixes **Preset ≠ Profile** (ADR-0014): Presets are look-and-feel option bundles, never monitors.

## Decision

### Manual Monitor profiles in v1; no auto-activation

A **Monitor profile** is a named, manually activated capture. No daemon, no auto-match on the connected-output set in v1 — that graduates later, gated on the Lua scripting-surface decision.

### Capture scope

A profile captures **both**:

- the full monitor rule set — connected, disconnected ("Not connected" group), and the catch-all row — exactly as keyed in `monitors.lua` (so `desc:`-when-unique identity is untouched: profiles store rules under the same `output` strings);
- the **`monitor` field of each workspace rule** — an overlay patch on `workspace_rules.lua`, not a copy of the file. Anything less makes "docked" half a profile.

### Storage & activation

- Profiles live app-side at **`monitor-profiles/<slug>.json`** in the App dir — deliberately *not* `profiles/`, which reads as a sibling of `presets/` and invites confusion with ADR-0014 Presets.
- Activating a profile renders its content into the canonical `monitors.lua` and patches the workspace rules' `monitor` fields via **one normal Apply transaction** (ADR-0010). Config on disk always shows exactly the active state; the canonical-parse contract holds.
- Switching is display-breaking, so it runs behind the **confirm-or-revert countdown** from ADR-0008; revert restores the previous files and reloads.
- Hand edits to `monitors.lua` while a profile is active are **drift**: badge the profile, offer "Update profile" / "Detach".

### Hotplug behaviour in the app

The arrangement canvas refreshes on the socket2 monitor events. A newly connected output with no rule lands on the catch-all/native default and appears on the canvas with a "no rule — add one?" hint. No prompt to switch profiles in v1; a passive "matches profile 'docked'" toast stays in the fog with auto-activation.

## Alternatives considered

- **No profiles in v1** — rejected: plain hotplug works without them, but mirror-vs-extend and workspace pinning flips are real docked/presentation needs, and #7 already promised the affordance.
- **Auto-activation (daemon or `hl.on` handlers)** — deferred, not rejected: needs a watcher (ADR-0006: no daemon) or the still-undecided scripting surface. Revisit when that fog clears.
- **Conditional Lua in `monitors.lua`** — rejected: breaks the canonical machine-parseable contract; the file would no longer state the active config plainly.
- **Folder named `profiles/`** — rejected (user-raised): too easily confused with `presets/`; `monitor-profiles/` is self-describing.
- **Term "Arrangement"** — rejected: a profile captures workspace pinning too, more than an arrangement; "profile" matches the mockup and Monique prior art.
