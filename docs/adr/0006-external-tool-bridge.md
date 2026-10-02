# ADR-0006: External tool bridge — no transpiler, tools emit Lua

**Status:** accepted — 2026-08-19

## Context

ADR-0002 promised that external `.conf`-emitting tools keep working after migration to Lua. The original assumption (issue #11) was a `.conf`→Lua transpile layer — a watcher daemon or one-shot import. Facts gathered while resolving #11:

- The ecosystem has already moved. matugen's official template repo ships a Hyprland **Lua** template (`hyprland-colors.lua`); noctalia writes `noctalia.lua` itself and auto-detects the Lua engine (`hyprctl dispatch 'hl.dsp.no_op()'` probe); DMS 1.5 generates native Lua config; HyprMod has a Lua mode. wallust ships no templates but is fully user-template-driven (minijinja).
- shell-switch (the user's own script) is template-driven too (`shell-start.conf.template`, `shell-binds.conf.template`) and never calls `hyprctl`.
- There is **no ecosystem convention** for tool snippets under Lua: no conf.d-style auto-required directory; `require()` resolves relative to `~/.config/hypr/` only (Hyprland discussion #14396). De-facto pattern: tool writes `<tool>.lua` into the config dir, user adds a `require` line.
- Hyprland watches every `require()`d file (IN_CLOSE_WRITE) — a tool rewriting its Lua module triggers reload with no watcher of ours. Atomic-rename writes do *not* trigger; an explicit `hyprctl reload` is needed then (docs/research/live-apply.md).
- On this box, matugen's `$primary`/`$outline_variant` are consumed by *other* config files — cross-file variable flow through tool output is real.

**Facts corrected, amended during #163** from #167's research (`docs/research/theming-tools.md`, Hyprland 0.56.2):

- matugen's official Lua template is **data-only**: it returns a table of colours and sets no Option, so requiring it changes nothing and "set by matugen" has nothing to read. The app installs its own matugen template (the official table plus an `hl.config` effect) instead.
- noctalia writes `noctalia.lua` only from **noctalia 5**, and only with `hyprland` in its `builtin_ids`; the module returns a table whose `apply_theme()` sets the colours, and its script appends `require("noctalia").apply_theme()` to `hyprland.lua` unless that substring is already there, commented or not. noctalia 4 writes `noctalia/noctalia-colors.lua`, loads it with `dofile` (unwatched) and reloads itself; it is detected, not bridged.
- DMS 1.5 and later write **Lua only**, at fixed paths under `~/.config/hypr/dms/`; there is no Lua-or-`.conf` setting. `dms setup` replaces `hyprland.lua` and is never run or suggested.
- wallust's template repository ships a hyprlang template only; no Lua template exists anywhere, so wallust is a Template pack.
- Every v1 tool writes its file **in place**, which Hyprland's `require` watch sees. The atomic-rename case below does not arise.

## Decision

The Bridge is **not a transpiler**. It is the app helping each tool emit Lua directly, then wiring the `require`. Three mechanisms, no daemon, no file watcher of our own:

1. **Adopt upstream Lua output** — for tools that already ship it: noctalia 5 (enabled through an app-owned drop-in config), DMS 1.5+ (`dms/colors.lua` only in v1). matugen moved to the Template pack during #163: its official template sets no Option. The migration wizard detects the tool, flips its config to Lua output (with confirmation), and adds the `require`.
2. **Template pack** — for template-driven tools without a Hyprland Lua template that sets Options: the app ships a Lua template plus the config stanza to install it. v1: matugen (its Hyprland entry retargeted to the app's template), wallust; shell-switch (user patches own script, template provided).
3. **One-shot import** — static snippets no tool regenerates (hand-written themes, repo-managed integration files, orphaned tool leftovers) go through the Importer once into app-managed modules, like any other part of the `.conf` tree.

### Placement & registry

- Tools with configurable output paths write to `~/.config/hypr/hyprtweaker/bridge/<tool>.lua`.
- Tools with fixed output paths (noctalia, DMS) are `require`d at their native locations.
- The Manifest records each bridge entry: tool, module path, mechanism. Bridge modules are tool-owned — the app never rewrites them and excludes them from hand-edit detection.
- **Amended during #163.** The registry (`engine/bridge/registry.py`) stores each tool's exact Entrypoint **line**, not only a module path, because module contracts differ: `require("hyprtweaker/bridge/matugen")`, `require("noctalia").apply_theme()` (native path), `require("dms.colors")` (native path). A Manifest entry is one per module: tool, module, line, file, mechanism, and a state — loading, **waiting** for the tool's first run, or **off** for the Color source (ADR-0014). Every entry always renders one line: the require, or the require commented with its reason (`-- off: Color source is Preset`, `-- waiting for matugen's first run`); a quarantined one keeps Quarantine's own block (ADR-0016). So the Entrypoint alone says what each bridge is doing, and noctalia's script always finds its line.
- **First run.** The app never writes a Bridge module, and requiring a missing file errors on every reload, so an entry whose file does not exist renders as waiting. A file a tool creates is not watched until it is required, so the app loads it itself — at launch, after every foreign reload, after a tool run it started, and on the Theming page's "Load now" — through one Entrypoint transaction.
- **Lost Manifest.** The entries are rebuilt from the app's own Entrypoint's lines. A `bridge/*.lua` no entry names keeps loading, as before the registry existed.

### Require order & precedence

Entrypoint order becomes: `vars` → `options/*` → entity modules → `legacy` → **`bridge/*`** → `user` last. Within the bridges (amended during #163): the shells first (noctalia, DMS, shell-switch), then the Color source backends (matugen, wallust), so the backend the user chose loads last of them; then any unregistered `bridge/*.lua`. Tools override GUI-set options; `user.lua` overrides everything. The post-reload read-back pass (ADR-0005) badges options a bridge module controls — "set by matugen" — rather than letting GUI writes silently lose. The badge must lead somewhere actionable (see theming-module ticket), not read as a lockout.

### Variables from tool output

A hyprlang `$var` defined in a tool-managed file maps at import time to a table access on the bridge module (`require('hyprtweaker/bridge/matugen')`), not to `vars.lua`. **Amended during #163:** `require` memoizes by the exact string, so `require("a/b")` and `require("a.b")` load the file twice, and a self-applying module required both ways applies twice. Each module therefore has **one spelling** — the registry's — which the Writer and every variable binding use; anything detecting an existing require matches both spellings.

### Reload edge

Tool writes trigger Hyprland's own reload via the require-watch. ~~As belt-and-braces against atomic-rename writes, the installed template configs include a post-write hook (`post_hook` in matugen, `[hooks]` in wallust) issuing `hyprctl reload`.~~ **Amended during #163:** no reload hook is installed. Every v1 tool writes in place, so the write already reloads, and a `hyprctl reload` hook would reload a second time with no debounce. A hook the user already has is left as they wrote it. Only a `dofile`-loaded file would need one, and no bridged tool uses `dofile`.

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
