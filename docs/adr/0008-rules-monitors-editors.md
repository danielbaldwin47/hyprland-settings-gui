# ADR-0008: Rules & monitors editors model

**Status:** accepted — 2026-08-19

## Context

Rules are the second-largest Entity class in the corpus (window rules 3–332 per rice, layer rules up to 74; workspace rules are rare). Monitors run 1–11 per rice. Constraints from research/prototypes:

- Window/layer rules are **strictly ordered**: registration is pure call order under Lua, and the last matching rule wins per effect. The wiki's "named rules evaluate first" is legacy carry-over not reflected in the Lua registration code (`docs/research/hyprlang-to-lua.md` §2.9).
- A **named** rule reused across calls merges into the same rule object; anonymous rules always append. All three rule kinds take `enabled` (`docs/research/lua-api-surface.md` §7–9).
- Match props are a fixed typed table (18: regex / bool / int / workspace-selector / tag), RE2 **full-match** semantics, `negative:` prefix negates. Effects are a typed table (57 static) plus a dynamic/plugin pass-through for unknown keys (`lua-api-surface.md` §7, `hyprlang-to-lua.md` §2.5).
- `border_color` as a Lua gradient table describes only one gradient; an active+inactive pair needs the raw legacy string fallback (`hyprlang-to-lua.md` §2.5).
- Workspace rules **merge by workspace selector string** (`replaceOrAdd`); monitor rules **merge per `output`** — a repeat call starts from a copy of the existing rule and patches fields (`lua-api-surface.md` §3, §9).
- `hyprctl -j monitors` supplies `availableModes`, `description`, current geometry/scale/transform/vrr/mirrorOf/disabled per connected output; `hyprctl -j clients` supplies class/title/initial\*/xwayland/floating/tags per open window — live helper data, not rule state.
- Monitor rules for disconnected outputs are legal and common (docks); `output = ""` is the catch-all fallback rule. Connector names (`DP-1`) shuffle across docks/reboots; `desc:` matching survives replug but collides on identical monitors (wiki Monitors.md).
- User principle (ADR-0007): **no proprietary state** — config files are the interface; hand edits must show up in the app. IA is settled (#7): separate Window Rules and Layer Rules pages, workspace rules on the Workspaces page, monitors on the Displays page with an arrangement canvas and confirm-or-revert.

## Decision

### Canonical modules, write-only IPC — the ADR-0007 contract, generalised

`window_rules.lua`, `layer_rules.lua`, `workspace_rules.lua`, `monitors.lua` are emitted in the same **canonical, machine-parseable form** as `binds.lua`: the model is source of truth, nothing is reconstructed from IPC (`hyprctl -j clients/monitors` feeds *helpers only*), post-reload verification is `configerrors`. On external change the app re-parses its own canonical file; constructs the parser can't represent become read-only rows with an adopt-into-`legacy.lua` offer.

### Window rule entity & list

- An ordered entry: **Match + Effects + enabled + optional Label**. **Identity is position**; the Label, when set, is emitted as `name` (readable config, runtime `set_enabled`). Disabling emits `enabled = false` — the rule stays in the file.
- **Flat ordered list** (user-confirmed over grouped-by-app): display order = file order = evaluation order, drag-reorderable, with a filter bar (text over label/match/effects + chips per match prop and effect). Footer states the semantics: "later rules win when they set the same property." Row title = Label or an auto-summary ("class kitty → float, opacity 0.9"); enabled switch inline.
- To preserve imported legacy precedence the Importer emits named (block) rules before anonymous ones, each in source order (research §2.9); after import the GUI shows and owns the single resulting order.

### Rule editor anatomy

- **Match group**: add-prop picker over the 18 typed props; per-prop typed input — regex entry (validated, full-match semantics surfaced by showing anchors as implicit), bool tri-state, int spin, workspace-selector entry, tag entry — string-valued props (regex, workspace selector, tag) with a negate toggle (`negative:` lives inside the value string, so only strings can carry it; a bool negates through its own switch, an int through its value — amended by #67). Live badge: "matches N open windows" (client-side approximation; RE2 vs Python divergence is accepted for exotic patterns). At least one prop required.
- **Effects group**: add-effect picker grouped by category (Placement / Focus / Appearance / Behaviour / Advanced), rows generated from the entity Schema with the same typed widgets as options. Unknown/plugin effects: a raw key+value "custom effect" row, pass-through, never dropped. String-grammar effects (`opacity`, `fullscreen_state`, `suppress_event`) get helper widgets that emit the string. `border_color`: gradient editor for the single-gradient case; raw-string mode for the active+inactive pair.

### Pick a window

A live picker over `hyprctl -j clients` (icon via `.desktop` class match) prefills a Match: `class` always (escaped, exact), `title` / `initial_class` / `initial_title` / `xwayland` opt-in via checklist. Available only while Hyprland runs; the editor degrades to manual entry otherwise.

### Layer rules

Same list model and editor shell; match is practically `namespace` (regex), effects are the 10 typed layer effects. "Pick a layer" lists namespaces from `hyprctl -j layers`.

### Workspace rules

**Identity is the workspace selector string** (Hyprland merges duplicates), so the list enforces one row per selector — adding a duplicate focuses the existing row. Selector input has two modes: **simple** (workspace id / `name:x` / `special:x` pickers) and **advanced** (raw selector string — `w[]`, `r[]`, `f[]`, `s[]`, `n[]`, `m[]` — validated against the grammar). Fields are Schema-generated rows; `layout_opts` is a free key/value table.

### Monitors

- Model: one rule per `output` + optional catch-all. **Identity is the `output` string.** New rules from a connected display default to `desc:<description>` when the description is non-empty and unique among connected + configured outputs, else the connector (user-confirmed); a per-rule toggle "Match by: this exact display / port `DP-1`" shows both forms.
- **Arrangement canvas**: connected outputs as logical-size rects (mode ÷ scale, transform-aware), drag with edge snapping; commit writes integer `"XxY"` positions.
- Per-monitor rows: Enabled switch (`disabled`); Resolution + Refresh combos built from `availableModes` plus the special modes (`preferred`/`highres`/`highrr`/`maxwidth`), custom `modeline` as an advanced raw entry; Scale (presets 1 / 1.25 / 1.5 / 2, free spin ≥ 0.25, `auto`, fractional-blur warning); Rotation combo (8 entries, emits `transform` 0–7); Mirror combo of other outputs; VRR combo (−1 unset / 0 off / 1 on / 2 fullscreen / 3 fullscreen-video); 10-bit switch (`bitdepth`); Reserved area as the css-gap row; Advanced colour group (`cm` preset, `sdr_eotf` by name only — numeric codes are legacy, `sdrbrightness`/`sdrsaturation`, HDR luminance fields, `supports_wide_color`/`supports_hdr` tri-states).
- **Disconnected & catch-all**: rules matching no connected output sit in a "Not connected" group — editable, deletable, badged, off-canvas. `output = ""` renders as a fixed "Any other display" row, also off-canvas.
- **Apply semantics**: display-breaking fields (mode, position, scale, transform, enabled, mirror, bitdepth, cm) batch and apply behind the #7 confirm-or-revert countdown; revert restores the previous `monitors.lua` and reloads. Benign fields (vrr, reserved area, sdr brightness/saturation) stay instant per ADR-0003.

### Placement

Settled by #7: Window Rules and Layer Rules pages, workspace rules on Workspaces, monitors on Displays. Monitor **profiles** (the "Profile: docked" label in the #7 mockup) are explicitly *not* decided here — spun off as their own ticket.

## Alternatives considered

- **Grouped-by-app rule list** — rejected (user-confirmed): hides the ordering that decides effect conflicts; cross-group reorder is ambiguous while the writer must keep a real file order anyway.
- **Reconstructing monitor config from `hyprctl -j monitors`** — rejected: IPC reflects *state*, not rules — `desc:` identities, the catch-all, and disconnected-output rules are unrecoverable from it.
- **Always-connector monitor identity** — rejected: silently targets the wrong screen after dock shuffles; `desc:`-when-unique degrades gracefully to connector for duplicate models.
- **Structured editor for the `border_color` active+inactive pair** — rejected: the pair only exists via the raw legacy string; a raw mode is honest and rare.
- **Everything-instant monitor apply** — rejected: mode/scale/transform mistakes can black-screen the session; confirm-or-revert is the established pattern (#7, Monique, hyprland-monitors).
