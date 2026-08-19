# CONTEXT — Hyprland Lua settings app

Domain glossary. Terms here are the ones to use in tickets, code, and docs. Sharpened by `/domain-modeling` as wayfinder tickets resolve (map: GitHub issue #1).

| Term | Meaning |
| --- | --- |
| **Option** | A single `hl.config` value (e.g. `decoration:rounding`) as reported by `hyprctl -j descriptions`: name, description, default, current, min/max, enum map. |
| **Section** | A top-level grouping of options as Hyprland defines it (`general`, `decoration`, `input`, …) — 21 of them. A property of the config, not of the UI. |
| **Page** | One destination in the sidebar; an `Adw.PreferencesPage`. A Page may cover one Section, several, or one Entity kind — the Page ↔ Section mapping is curated, not derived (see issue #7). |
| **Group** | A `PreferencesGroup` inside a Page: the titled block a handful of Rows sit in. |
| **Row** | The generated widget for one Option or Entity field: title, dotted key as subtitle, typed control, and state — modified, inherit, advanced, restart-required, device-override, set-outside-the-app, unknown-to-this-version. |
| **Advanced** | Visibility class in the Schema overlay: Rows hidden until the Advanced toggle is on (`debug`, `quirks`, `experimental`, `input-capture`, plus curated singles). |
| **Entity** | A non-option config object with its own `hl.*` constructor: monitor, bind, window/layer/workspace rule, animation, curve, gesture, device, env, permission, autostart command. |
| **Schema** | The typed, documented, curated description of every Option and Entity field the UI is generated from — `descriptions` + Lua stubs + hand-curated overlay. |
| **Managed dir** | The app-owned directory of generated Lua modules; the app rewrites these freely. |
| **Module** | One generated Lua file in the managed dir (per section / entity type). |
| **user.lua** | The escape hatch file the app never rewrites; arbitrary user Lua. |
| **Importer** | The hyprlang → Lua converter that reads a `hyprland.conf` tree (following `source=`) into the model. |
| **Migration wizard** | The first-run flow around the Importer: detect, preview, back up, switch engine, verify, keep-or-rollback. |
| **Bridge** | The mechanism keeping external `.conf`-emitting tools (matugen, wallust, noctalia, dms, shell-switch) effective under Lua. |
| **Instant apply** | Write-on-change; Hyprland reloads on save; per-option reset and undo instead of an Apply button. |
| **Frontier / Map** | Wayfinder terms — see `docs/agents/issue-tracker.md`. |
