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
| **Row** | The generated widget for one Option or Entity field: title, description as subtitle, typed control, suffix strip (state pills, Value summary, Dependency badge, reset-when-modified, Help popover), and state — modified, inherit, advanced, restart-required, device-override, set-outside-the-app, unknown-to-this-version (ADR-0013). The dotted key lives in the Help popover and the search index, not the subtitle. |
| **Help popover** | The per-Row ⓘ popover: help text, dotted key (copyable), default value, "Learn more" wiki link. The Row's only reference chrome (ADR-0013). |
| **Value summary** | The dim collapsed-value preview on an ExpanderRow suffix: gradient = swatches + angle, css-gaps = "8" / "8 · 12 · 8 · 12", vec2 = "x, y" (ADR-0013). |
| **Dependency badge** | The "Requires \<option\>" pill on a Row whose `depends_on` is unmet; only the control goes insensitive (text stays readable), click navigates to the controlling Row. Rows are never hidden by dependencies (ADR-0013). |
| **Advanced** | Visibility class in the Schema overlay (`debug`, `quirks`, `experimental`, `input-capture`, plus curated singles). Revealed in place by the global **Advanced switch** ("Show advanced settings", hamburger menu); the `hidden` tier shows only in the Config view. Search always indexes everything and reveals a hit one-off (ADR-0013). |
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
| **Monitor profile** | A named, manually activated capture of the full monitor rule set plus each workspace rule's `monitor` pinning. Lives at `monitor-profiles/<slug>.json` in the App dir; activation renders it into the canonical modules via one Apply transaction behind confirm-or-revert. No auto-activation on hotplug in v1. Distinct from Preset (ADR-0015). |
| **Arrangement canvas** | The drag surface of connected displays at logical size (scale- and rotation-aware, edge snapping). Disconnected-output rules live off-canvas in a "Not connected" group. |
| **Confirm-or-revert** | The apply pattern for display-breaking changes: batch, apply, countdown; no confirmation restores the previous state (ADR-0008, prototyped in #7). |
| **Schema** | The typed, documented, curated description of every Option and Entity field the UI is generated from — the Generated schema plus the Overlay. |
| **Generated schema** | The machine-produced, per-Hyprland-version half of the Schema (`hyprland-<ver>.json`), built from `descriptions` + Lua stubs (ADR-0011). |
| **Overlay** | The hand-curated, version-independent half of the Schema: nullability, widget choice, titles, grouping, visibility, restart flags. Completeness is CI-enforced (ADR-0011). |
| **Engine** | The UI-free half of the app (`hyprtweaker.engine`): Schema loading, model, writer, importers, IPC, state. Never touches GTK; everything testable headless lives here (ADR-0011). |
| **Harness** | The nested-headless-Hyprland integration rig (state diff + screenshot diff) proven in prototype #9; the importer/writer's end-to-end test bed (ADR-0011). |
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
| **Apply transaction** | One coalesced write cycle: render dirty Modules whole, syntax-gate, atomic-rename all, one explicit reload, Read-back. Serialized — one in flight, later edits coalesce (ADR-0010). |
| **Read-back** | The confirm pass after a reload: `configerrors` + `getoption` of touched keys over the IPC socket. Doubles as the drift-badge scan. `configreloaded` means "reload started", not "apply done". |
| **ApplyResult** | The structured outcome of an Apply transaction — ok, config errors, read-back mismatch, or timeout. Consumed by error surfacing (ADR-0016). |
| **Eval preview** | Transient per-tick apply during a continuous gesture (slider, colour) via `eval 'hl.config{...}'` on the socket; wiped by any reload; made durable by the Apply transaction on release. |
| **Restore-last-good** | Writing a Module's Last-known-good Snapshot bytes back through a normal Apply transaction, implicated Modules only. Mechanism in ADR-0010; firing policy in ADR-0016. |
| **Ownership class** | The attribution of a `configerrors` line by its `file:line` prefix — own write this transaction, app Module hand-edited, `user.lua`/Bridge, or Entrypoint. Decides the recovery policy (ADR-0016). |
| **Auto-revert** | The no-confirmation recovery when a transaction's own write is rejected: restore that Module's pre-write Snapshot, revert the model delta, drop the gesture from the undo stack, toast with Details (ADR-0016). |
| **Last known good** | Per-Module, the newest Journal Snapshot whose transaction confirmed clean (empty `configerrors` + Read-back ok, recorded as a `confirmed` flag) (ADR-0016). |
| **Quarantine** | Consent-gated disabling of `user.lua` or a Bridge module by regenerating the Entrypoint without its require; badged, one-click reversible. The app's only recovery for files it never writes (ADR-0016). |
| **Banner** | The single persistent unhealthy-state surface (`Adw.Banner` under the header): shown for config errors, Entrypoint refusal, or active Quarantine; opens the one error dialog. Errors are file-scoped and never badge Rows (ADR-0016). |
| **Undo step** | One user gesture as a model-level delta, on a global linear in-memory stack, replayed through the Apply pipeline. Dies with the session; the Journal is history, not undo. |
| **Pending restart** | State of a restart-flagged Option after a write: applied to file, effective after Hyprland restart; Read-back skipped, Row badged. |
| **Retired** | State of an Option a newer Hyprland removed while the user still sets it: no longer emitted, value kept in the Manifest and restored if the option returns (downgrade or `renamed_from`), Row badged, one-time notice per release (ADR-0012). |
| **Theming module** | The Tasks-view Page fronting the color-generation backends (matugen, wallust — tabs, one active, switch by explicit confirm) and housing Presets. Where the "set by \<tool\>" badge deep-links (ADR-0014). |
| **Color source** | The single owner of colors at any moment: Wallpaper (matugen), Wallpaper (wallust), Preset, or Manual. Mechanically: which Bridge require the Entrypoint enables. Switching is always explicit and rides one Apply transaction (ADR-0014). |
| **Preset** | A named bundle of Option values saved by Capture scope; look-and-feel only — never binds/rules/monitors (Preset ≠ Monitor profile, ADR-0015; whole-setup carry-over is Export, ADR-0009). Lives at `presets/<slug>.json` in the App dir; applied via a normal Apply transaction (ADR-0014). |
| **Capture scope** | The checklist choosing what a Preset saves and applies: Colors, Gaps & layout, Animations, Fonts & cursor, Wallpaper (ADR-0014). |
| **Theme archive** | An exported Preset as `<slug>.hyprtweaker-theme` (tar.zst: `preset.json` + wallpaper image when captured). Imports through a preview — sections touched, before→after, wallpaper thumbnail, color-conflict choice — then one Apply transaction (ADR-0014). |
| **Release check** | The per-Hyprland-release protocol (`docs/agents/hyprland-release-check.md`): regenerate the Generated schema, three-layer diff (schema / stub API / wiki), curate the Overlay, one PR. Handled = CI overlay completeness green + diff reviewed (ADR-0012). |
| **Support window** | The Hyprland versions an app release ships Generated schemas for: latest + previous. Older versions ≥ 0.56 degrade to the nearest lower schema; newer-than-shipped versions are supplemented at runtime from live `descriptions`, flagged (ADR-0012). |
| **Search** | The view-independent finder: sidebar entry (Ctrl+F, or type-to-search) over one in-memory index of every Option (title, help, dotted key — all visibility tiers) and Entity (Binds, Rules, Monitor rules, Monitor profiles, Presets). Results group as Settings then Rules & entities; substring match, no fuzzy. A hit shows its Page with the Row flash-revealed one-off, resolving against the active View first and switching to Config only when the Row has no home there (ADR-0017). |
| **One-off reveal** | Search navigation showing an advanced/hidden Row in place for this visit despite the Advanced switch being off (ADR-0013, ADR-0017). |
| **Scripting page** | The read-only Page (System category in Tasks; own Page in Config) surfacing the escape hatch: the Scripting inventory plus the Plugin load list. Never authors scripting (ADR-0018). |
| **Scripting inventory** | Best-effort static scan of `user.lua` and `legacy.lua` for `hl.on` / `hl.timer` / `hl.layout.register` / `hl.plugin.load` calls, listed with event/name, `file:line`, open-in-editor. Never executes user Lua; informational only — misses never affect writes (ADR-0018). |
| **Discovered layout** | A `lua:<name>` custom layout found by the Scripting inventory; joins the `general:layout` combo and workspace-rule layout picker as selectable, never authorable (ADR-0018). |
| **Plugin load list** | The editable, ordered `hl.plugin.load` path list (add/remove/enable-toggle) on the Scripting page, rendered into canonical `plugins.lua`. Loaded state via `hl.get_loaded_plugins`; hyprpm out of scope (ADR-0018). |
| **Profile-match toast** | App-open-only toast when the connected-output set matches a saved Monitor profile, offering one-click activation; rides the canvas's socket2 listener. Auto-activation (app closed) is out of scope (ADR-0018). |
| **Frontier / Map** | Wayfinder terms — see `docs/agents/issue-tracker.md`. |
