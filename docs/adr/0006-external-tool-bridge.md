# ADR-0006: External tool bridge — no transpiler, tools emit Lua

**Status:** accepted — 2026-08-19

Read by the agent about to change external tool bridge — no transpiler, tools emit lua, before the first edit; the Status line says what is on `main` now.

## Context

ADR-0002 promised that external `.conf`-emitting tools keep working after migration to Lua. The original assumption (issue #11) was a `.conf`→Lua transpile layer — a watcher daemon or one-shot import. Facts gathered while resolving #11:

- The ecosystem has already moved. matugen's official template repo ships a Hyprland **Lua** template (`hyprland-colors.lua`); noctalia writes `noctalia.lua` itself and auto-detects the Lua engine (`hyprctl dispatch 'hl.dsp.no_op()'` probe); DMS 1.5 generates native Lua config; HyprMod has a Lua mode. wallust ships no templates but is fully user-template-driven (minijinja).
- shell-switch (the user's own script) is template-driven too (`shell-start.conf.template`, `shell-binds.conf.template`) and never calls `hyprctl`.
- There is **no ecosystem convention** for tool snippets under Lua: no conf.d-style auto-required directory; `require()` resolves relative to `~/.config/hypr/` only (Hyprland discussion #14396). De-facto pattern: tool writes `<tool>.lua` into the config dir, user adds a `require` line.
- Hyprland watches every `require()`d file (IN_CLOSE_WRITE) — a tool rewriting its Lua module triggers reload with no watcher of ours. Atomic-rename writes do *not* trigger; an explicit `hyprctl reload` is needed then (docs/research/live-apply.md).
- On this box, matugen's `$primary`/`$outline_variant` are consumed by *other* config files — cross-file variable flow through tool output is real.

## Decision

The Bridge is **not a transpiler**. It is the app helping each tool emit Lua directly, then wiring the `require`. Three mechanisms, no daemon, no file watcher of our own:

1. **Adopt upstream Lua output** — for tools that already ship it: matugen (install/retarget the official Lua template via `output_path`), noctalia (already writes `noctalia.lua`), DMS (Lua-native since 1.5). The migration wizard detects the tool, flips its config to Lua output (with confirmation), and adds the `require`.
2. **Template pack** — for template-driven tools without a shipped Hyprland Lua template: the app ships a Lua template plus the config stanza to install it. v1: wallust; shell-switch (user patches own script, template provided).
3. **One-shot import** — static snippets no tool regenerates (hand-written themes, repo-managed integration files, orphaned tool leftovers) go through the Importer once into app-managed modules, like any other part of the `.conf` tree.

### Placement & registry

- Tools with configurable output paths write to `~/.config/hypr/hyprtweaker/bridge/<tool>.lua`.
- Tools with fixed output paths (noctalia, DMS) are `require`d at their native locations.
- The Manifest records each bridge entry: tool, module path, mechanism. Bridge modules are tool-owned — the app never rewrites them and excludes them from hand-edit detection.

### Require order & precedence

Entrypoint order becomes: `vars` → `options/*` → entity modules → `legacy` → **`bridge/*`** → `user` last. Tools override GUI-set options; `user.lua` overrides everything. The post-reload read-back pass (ADR-0005) badges options a bridge module controls — "set by matugen" — rather than letting GUI writes silently lose. The badge must lead somewhere actionable (see theming-module ticket), not read as a lockout.

### Variables from tool output

A hyprlang `$var` defined in a tool-managed file maps at import time to a table access on the bridge module (`require('hyprtweaker/bridge/matugen')`), not to `vars.lua`. Lua's `require` memoization makes this order-independent.

### Reload edge

Tool writes trigger Hyprland's own reload via the require-watch. As belt-and-braces against atomic-rename writes, the installed template configs include a post-write hook (`post_hook` in matugen, `[hooks]` in wallust) issuing `hyprctl reload`.

### v1 supported set

matugen, noctalia, DMS, shell-switch, wallust (template shipped, dormant on this box). **HyprMod dropped**: superseded by hyprtweaker; two GUIs co-managing one config invites fights. Its leftover snippet is one-shot imported.

### Out of bridge scope

Scripts and tools shelling out to `hyprctl dispatch`/`keyword` with legacy syntax break under the Lua engine regardless of config files. The bridge does not shim `hyprctl`; the migration wizard's loss report warns instead (#14).

## Consequences

- No resident process, no systemd units, no transpile fidelity risk — the moving parts are the tools' own template engines.
- Per-tool integration is a data problem (template + config stanza + detection rule), so adding a tool later is cheap.
- Users of tools that hardcode `.conf` output and never adopt Lua get only one-shot import; if such a tool matters later, a transpile-on-change mechanism can be added behind the same registry without changing this model.
- Flipping a tool's output format touches files outside the App dir — always behind explicit confirmation in the wizard.

## Alternatives considered

- **Watcher daemon transpiling `.conf`→Lua on change** — rejected: every target tool can already emit Lua; a daemon adds a resident process and a second parser to keep faithful for zero v1 gain.
- **systemd path units + oneshot transpile** — same objection, minus the resident process.
- **GUI wins over tools (bridge before options)** — rejected: matugen theming would silently break the moment the user touches any color in the app.
- **Shimming `hyprctl` for legacy-syntax callers** — rejected as bridge scope; belongs to migration loss reporting.
