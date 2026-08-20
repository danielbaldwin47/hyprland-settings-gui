# CONTEXT — Hyprland Lua settings app

Domain glossary. Terms here are the ones to use in tickets, code, and docs. Sharpened by `/domain-modeling` as wayfinder tickets resolve (map: GitHub issue #1).

| Term | Meaning |
| --- | --- |
| **hyprtweaker** | The app. Positioning: one more tool in your box, never a "manager" over your dots — user-facing copy avoids "manage(d)" wording (ADR-0005). |
| **Option** | A single `hl.config` value (e.g. `decoration:rounding`) as reported by `hyprctl -j descriptions`: name, description, default, current, min/max, enum map. |
| **Section** | A top-level grouping of options (`general`, `decoration`, `input`, …) and the navigation unit in the app's sidebar. |
| **Entity** | A non-option config object with its own `hl.*` constructor: monitor, bind, window/layer/workspace rule, animation, curve, gesture, device, env, permission, autostart command. |
| **Schema** | The typed, documented, curated description of every Option and Entity field the UI is generated from — `descriptions` + Lua stubs + hand-curated overlay. |
| **App dir** | `~/.config/hypr/hyprtweaker/` — the app-owned directory of generated Lua modules; the app rewrites these freely (formerly "managed dir"). |
| **Module** | One generated Lua file in the App dir: one per Section (`options/<section>.lua`) or per Entity type (`binds.lua`, `monitors.lua`, …). |
| **Entrypoint** | The generated `~/.config/hypr/hyprland.lua`: header + `require` list, regenerated when the Module set changes. `user.lua` is required last. |
| **Manifest** | `manifest.json` in the App dir: app version, schema version, Module list + content hashes (hand-edit detection), migration provenance. |
| **user.lua** | The escape hatch file the app never rewrites; arbitrary user Lua. Required last, so it overrides the GUI; diverging Options are badged "overridden in user.lua". |
| **legacy.lua** | Imported constructs the GUI can't represent; written once by the Importer, never rewritten, listed read-only in the GUI. |
| **Unset** | An Option the model does not emit — Hyprland's default applies (reload resets all values). Opposite: explicitly set, always emitted even when equal to the default. |
| **Value representations** | The up-to-three forms of one Option value: display text, Lua literal, and the parse of `getoption` output. 19/353 options differ in all three; gradients and css-gaps must be emitted as Lua tables, never `toString()` text. |
| **Snapshot / Journal** | Per-write Module copies + change log in `$XDG_STATE_HOME/hyprtweaker/`, pruned; migration takes a full-tree backup there. |
| **Importer** | The hyprlang → Lua converter that reads a `hyprland.conf` tree (following `source=`) into the model. |
| **Migration wizard** | The first-run flow around the Importer: detect, preview, back up, switch engine, verify, keep-or-rollback. |
| **Bridge** | The mechanism keeping external `.conf`-emitting tools (matugen, wallust, noctalia, dms, shell-switch) effective under Lua. |
| **Instant apply** | Write-on-change; Hyprland reloads on save; per-option reset and undo instead of an Apply button. |
| **Frontier / Map** | Wayfinder terms — see `docs/agents/issue-tracker.md`. |
