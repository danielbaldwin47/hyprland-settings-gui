# ADR-0014: Theming module — dual backends, Color source, Presets

**Status:** accepted — 2026-08-22

## Context

ADR-0006 left a thread hanging: the "set by matugen" badge must lead somewhere actionable, not read as a lockout. Issue #25 asked what that somewhere is — a theming layer on top of the pure settings: GUI control over wallpaper-driven color generation (matugen, wallust) plus named presets. The structural tension: those tools write Bridge modules, and Bridge modules override GUI-set options (ADR-0006), so a preset applying colors through normal GUI writes would silently lose while a wallpaper pipeline is active. Someone has to own colors at any given moment.

## Decision

The **Theming module** is a Tasks-view page that fronts the color-generation backends and is the home of Presets.

### Backends

Both **matugen and wallust are GUI-driven in v1** — backend tabs at the top of the page, each exposing its own parameters (scheme type, mode, contrast, … for matugen; wallust's equivalents). Exactly one backend is active at a time. Browsing the inactive tab never switches colors; switching is an explicit confirm on that tab. The module edits the backend's own config (the Template pack stanza, ADR-0006) and triggers regeneration against the current wallpaper; it never replaces the user's wallpaper script, which keeps working because it just runs the tool.

### Color source

Exactly one **Color source** is active: `Wallpaper (matugen)` · `Wallpaper (wallust)` · `Preset` · `Manual`. Mechanically, the active source is which Bridge require the Entrypoint enables — Preset/Manual gate both bridge requires off, so preset and hand-set colors are ordinary GUI options with nothing overriding them. Every source switch is explicit (a confirm, a preset apply choice, or a "resume wallpaper colors" action) and lands as one Apply transaction (Entrypoint regeneration + reload, ADR-0010).

Applying a color-carrying Preset while a Wallpaper source is active raises one dialog — **"Use preset's colors"** (pause wallpaper colors, source → Preset) or **"Keep wallpaper colors"** (apply everything else) — with a "remember my choice" checkbox backing a global preference. Default is unset: ask each time.

### Presets

A **Preset** is a named bundle of Option values, chosen at save time via a **Capture scope** checklist — Colors / Gaps & layout / Animations / Fonts & cursor / Wallpaper — as narrow or broad as wanted. Colors are frozen to the concrete values live at capture, whatever generated them.

**Preset ≠ Profile.** Keybinds, rules, and monitors are excluded: they are workflow, not theme, and the carry-my-whole-setup-to-a-new-machine story is already ADR-0009's first-class Export/Import.

On disk: `~/.config/hypr/hyprtweaker/presets/<slug>.json` — app data, never `require`d by Hyprland. JSON manifest: name, created, capture scope, dotted-key→value map, app + Hyprland version stamps. Applying a preset is a normal Apply transaction with a pre-write Snapshot; undo toast applies.

### Wallpaper

Wallpaper is one more capture-scope checkbox, and apply-preview offers **change / keep mine**. Applying sets the image via a detected wallpaper daemon (swww or hyprpaper, one IPC call) — the app never configures the daemon, keeping the out-of-scope line intact. No supported daemon detected → the checkbox is insensitive with a hint. If a Wallpaper color source is active and the preset changes the wallpaper, colors regenerate from the new image — no special case.

### Badge deep-link

The "set by matugen/wallust" badge follows the Dependency-badge convention (ADR-0013): click navigates to the Theming page, scrolled to the backend controls. No inline unlock on the Row; one place owns the pipeline.

### Sharing

In scope for v1. Export writes a **Theme archive** — `<slug>.hyprtweaker-theme`, a tar.zst of `preset.json` plus the wallpaper image when captured. Import opens a preview in the migration-wizard idiom: sections touched, before→after values, wallpaper thumbnail, the color-conflict choice inline; confirming runs one Apply transaction with Snapshot. Version-stamped; unknown keys from a newer app warn and skip, never fail.

## Consequences

- Source switching regenerates the Entrypoint (require gating), so it rides the existing Apply pipeline — no new write path.
- The module edits tool-owned config files outside the App dir (matugen/wallust config stanzas) — like the wizard's output-flip in ADR-0006, behind the user's explicit action.
- Per-backend parameter UI is data (like Template packs), so a third backend later is additive.
- Local sharing is trivially the JSON file; the archive format exists only to carry the wallpaper.

## Alternatives considered

- **matugen-only v1** — rejected by the user: both backends drivable, tab-switch with confirm.
- **Presets as full profiles (binds included)** — rejected: overlaps ADR-0009 Export, invites "theme broke my binds".
- **Presets as Lua modules required by Hyprland** — rejected: presets are app data applied through the pipeline; requiring them would create a second override layer to reason about.
- **A second bridge layer for preset colors** — rejected: gating the existing bridge requires via Color source is one concept instead of two stacked override systems.
