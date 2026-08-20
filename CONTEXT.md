# CONTEXT — Hyprland Lua settings app

Domain glossary. Terms here are the ones to use in tickets, code, and docs. Sharpened by `/domain-modeling` as wayfinder tickets resolve (map: GitHub issue #1).

| Term | Meaning |
| --- | --- |
| **hyprtweaker** | The app. Positioning: one more tool in your box, never a "manager" over your dots — user-facing copy avoids "manage(d)" wording (ADR-0005). |
| **Option** | A single `hl.config` value (e.g. `decoration:rounding`) as reported by `hyprctl -j descriptions`: name, description, default, current, min/max, enum map. |
| **Section** | A top-level grouping of options as Hyprland defines it (`general`, `decoration`, `input`, …) — 21 of them. A property of the config, not of the UI. |
| **View** | One of the two sidebar arrangements the user switches between: **Tasks** (curated Pages, the default) and **Config** (one Page per Section, generated from the Schema). A View changes grouping, naming and order only — never which Options exist (issue #7). |
| **Page** | One destination in the sidebar; an `Adw.PreferencesPage`. In the Config view a Page is exactly one Section or one Entity kind; in the Tasks view a Page may span several Sections, by a curated mapping. |
| **Group** | A `PreferencesGroup` inside a Page: the titled block a handful of Rows sit in. |
| **Row** | The generated widget for one Option or Entity field: title, dotted key as subtitle, typed control, and state — modified, inherit, advanced, restart-required, device-override, set-outside-the-app, unknown-to-this-version. |
| **Advanced** | Visibility class in the Schema overlay: Rows hidden until the Advanced toggle is on (`debug`, `quirks`, `experimental`, `input-capture`, plus curated singles). |
| **Entity** | A non-option config object with its own `hl.*` constructor: monitor, bind, window/layer/workspace rule, animation, curve, gesture, device, env, permission, autostart command. |
| **Bind** | One keybind Entity: Trigger + Action + flags + optional description, owned by root or a Submap. Identity is list position — duplicates are legal and fire in order (ADR-0007). |
| **Trigger** | The input half of a Bind: modifier set plus exactly one of keysym, key code, mouse button, wheel direction, switch, or catch-all. |
| **Action** | The effect half of a Bind: a typed dispatcher call (name + arguments), or "Run command". Function-valued actions belong to user.lua and are read-only in the GUI. |
| **Submap** | A named mode grouping Binds, with an optional reset target. A Submap no Bind enters is "unreachable". |
| **Capture** | The dialog flow that records a Trigger from real input (system shortcuts inhibited), with manual text entry as fallback. |
| **Conflict** | Two enabled Binds in the same Submap with the same Trigger. Warned and navigable — jump to, rebind, or disable the other — never blocked (duplicates are legal). |
| **Rule** | A window or layer rule Entity: Match + Effects + enabled + optional Label. Ordered — later Rules win per Effect; identity is list position (ADR-0008). |
| **Match** | The set of typed match props a Rule tests (regex, bool, int, workspace selector, tag), each negatable. At least one is required. |
| **Effect** | One typed property a Rule applies when its Match holds. Unknown/plugin effects pass through as raw key+value, never dropped. |
| **Label** | A Rule's optional human name, emitted as `name`. Naming a Rule also makes it runtime-toggleable. |
| **Pick a window / Pick a layer** | The helper that prefills a Match from a live open window (or layer namespace). Helper data only — never rule state. |
| **Workspace rule** | A rule Entity whose identity is its workspace selector string — Hyprland merges duplicates, so the app enforces one per selector (ADR-0008). |
| **Monitor rule** | The per-output display Entity. Identity is the `output` string; new rules prefer `desc:` when unique, else the connector. `output = ""` is the **catch-all** ("Any other display"). |
| **Arrangement canvas** | The drag surface of connected displays at logical size (scale- and rotation-aware, edge snapping). Disconnected-output rules live off-canvas in a "Not connected" group. |
| **Confirm-or-revert** | The apply pattern for display-breaking changes: batch, apply, countdown; no confirmation restores the previous state (ADR-0008, prototyped in #7). |
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
| **Lua importer** | The evaluation-based converter for a foreign `hyprland.lua`: the file runs under a recording `hl.*` stub; declarative calls land in the model, script constructs land in `legacy.lua`, conditionals bake (ADR-0009). |
| **Migration wizard** | The guarded import flow around either importer: detect, preview + Loss report, back up, switch, verify, keep-or-rollback (1-min idle = rollback). Runs on first run and on any later Import (ADR-0009). |
| **Loss report** | The wizard's account of what conversion changed, in three classes — Info, Needs review, Breakage it cannot fix (e.g. `hyprctl dispatch` in scripts). Persisted to the state dir, viewable later. |
| **Import / Export** | First-class interop: Import accepts any `hyprland.lua`/`.conf` at any time via the Migration wizard; Export writes one self-contained flattened `hyprland.lua` that runs without the app. A foreign `hyprland.lua` found at startup is an auto-offered Import; the original becomes `hyprland.lua.bak`. |
| **Bridge** | The mechanism keeping external config-emitting tools effective under Lua: help each tool emit Lua directly (adopt upstream output, or install a Template pack), wire the `require`; never transpile on the fly (ADR-0006). v1 tools: matugen, noctalia, DMS, shell-switch, wallust. |
| **Bridge module** | A tool-owned Lua module the Entrypoint requires (after generated Modules, before `user.lua`): `hyprtweaker/bridge/<tool>.lua` when the tool's output path is configurable, the tool's native path otherwise. Never rewritten by the app; options it controls are badged "set by \<tool\>". |
| **Template pack** | The Lua template + config stanza the app installs into a template-driven tool (matugen, wallust, shell-switch) so it emits a Bridge module instead of `.conf`. |
| **Instant apply** | Write-on-change; Hyprland reloads on save; per-option reset and undo instead of an Apply button. |
| **Frontier / Map** | Wayfinder terms — see `docs/agents/issue-tracker.md`. |
