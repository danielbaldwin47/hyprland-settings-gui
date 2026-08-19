# CONTEXT — Hyprland Lua settings app

Domain glossary. Terms here are the ones to use in tickets, code, and docs. Sharpened by `/domain-modeling` as wayfinder tickets resolve (map: GitHub issue #1).

| Term | Meaning |
| --- | --- |
| **Option** | A single `hl.config` value (e.g. `decoration:rounding`) as reported by `hyprctl -j descriptions`: name, description, default, current, min/max, enum map. |
| **Section** | A top-level grouping of options (`general`, `decoration`, `input`, …) and the navigation unit in the app's sidebar. |
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
