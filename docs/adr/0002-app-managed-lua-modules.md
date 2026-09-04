# ADR-0002: App-managed Lua modules + importer + external-tool bridge

**Status:** accepted — 2026-08-19

Read by the agent about to change app-managed lua modules + importer + external-tool bridge, before the first edit; the Status line says what is on `main` now.

## Context

Hyprland ≥ 0.55 deprecates hyprlang; ≥ 0.56 loads `hyprland.lua` if it exists, else legacy `hyprland.conf` — never both (see `src/config/ConfigManager.cpp`). The user's box runs hyprlang with several tools emitting `.conf` snippets (matugen, wallust, noctalia, dms, shell-switch, HyprMod).

## Decision

- The app owns a managed directory of generated Lua modules (one per section) plus a generated `hyprland.lua` that `require()`s them and a `user.lua` escape hatch the app never rewrites.
- A first-run **migration wizard** imports the existing `.conf` tree (following `source=`) into the model — a headline feature, not an afterthought.
- External `.conf`-emitting tools are bridged (snippets transpiled to Lua so theming keeps working) rather than declared out of scope.
- The app does not round-trip-edit hand-written Lua.

## Consequences

- Deterministic writer, no fragile Lua parsing.
- Users keep arbitrary Lua scripting in `user.lua`.
- Bridge design and reload semantics are open decisions on the map.
