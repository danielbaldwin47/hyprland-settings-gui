# Live-apply mechanics and reload semantics (Hyprland v0.56.2)

Resolves issue #5. Sources are Hyprland tag `v0.56.2` (paths below are relative to the
Hyprland repo, `file:line`), the Hyprland wiki checkout at commit `2e15371` (2026-08-18,
paths relative to `content/`), the installed stubs `/usr/share/hypr/stubs/hl.meta.lua`, and
a few read-only experiments on this box (Hyprland 0.56.2, still running the **hyprlang**
manager — noted where relevant).

Feeds ADR-0003 (instant apply) and ADR-0002 (app-managed Lua modules).

---

## TL;DR

* A Lua reload is a **full teardown + re-execute**: the whole Lua state is destroyed and
  recreated, and binds, window/layer/workspace/monitor rules, animations, gestures, timers,
  `hl.on` subscriptions, device configs and every config value are cleared/reset before the
  main file runs again. Generated modules therefore only need to be idempotent *within one
  execution*, not across reloads. Repeated `hl.bind` inside one run does accumulate
  duplicates; named `hl.window_rule` merges by name.
* File-watch: every file that was actually loaded (main file + every `require()`d file
  tracked by a `package.searchers` hook, symlink targets too) is watched with inotify
  `IN_CLOSE_WRITE`; directories only if a tracked path *is* a directory. **No debounce**:
  each close-write triggers a synchronous `reload()`. Watch list is refreshed after each
  reload via the prop refresher.
* `hyprctl eval 'hl.config{...}'` applies transiently and schedules the right prop refresh;
  it is wiped by the next reload (all values `reset()`), which re-derives state from files.
  Same for `hl.bind` / `hl.window_rule` via eval.
* `hyprctl reload` re-reads the file(s) through the *current* manager; the config path is
  cached, so creating `hyprland.lua` on a hyprlang session does nothing until
  `hyprctl reload full-reset` (or restart). `hyprctl keyword` is refused under Lua
  (`"keyword can't work with non-legacy parsers. Use eval."`).
* Errors: `hyprctl reload` always returns `"ok"`; errors go to `hyprctl configerrors`
  (`getErrors()`), the on-screen error bar, and Lua `hl.on("config.reloaded")` fires with no
  payload. socket2 emits `configreloaded>>` (empty data). Beware: any `hyprctl eval` clears
  the stored error list.
* Recommended pipeline: write module atomically → wait for `configreloaded` on socket2 →
  poll `configerrors` (+ `getoption` for the touched keys) → on error, restore the previous
  module bytes (which triggers another reload). Optionally pre-apply via `eval` for
  sub-frame feedback on sliders and only write the file on release.

---

## 1. What a Lua reload does, step by step

Entry points: `hyprctl reload` → `Config::mgr()->reload()` (`src/debug/HyprCtl.cpp:1260-1278`),
inotify callback → `reload()` (`src/config/lua/ConfigManager.cpp:416-419`), and
`init()` → `reinitLuaState(); reload();` (`:628-632`).

`Config::Lua::CConfigManager::reload()` (`src/config/lua/ConfigManager.cpp:634-760`):

1. `Event::bus()->m_events.config.preReload.emit()` (`:635`) — internal bus only, not
   exposed to Lua (`knownEvents()` at `LuaEventHandler.cpp:266-303` has no `config.pre_reload`).
2. Marks `m_isParsingConfig = true` (scope guard resets it and sets
   `m_isFirstLaunch = false` on exit, `:637-641`).
3. Re-resolves the main config path via `Jeremy::getMainConfigPath()` (`:643`) — cached
   statically, see §4.
4. Resets the tracked path list to `{main}` (`:646-647`); the `require()` hook repopulates
   it while the config runs (`:561-598`, `trackConfigPath` `:68-75`).
5. **Phase 1 – syntax check without touching state** (`:649-696`): clears
   `package.loaded` for all non-stdlib modules (`:656-680`) so `require()` re-executes them,
   then `luaL_loadfile(main)`. On a *syntax* error: `m_errors = {that error}`,
   `m_lastConfigVerificationWasSuccessful = false`, `postConfigReload()` and **return** —
   the previous live state (binds, rules, values) is kept intact (`:692-696`; wiki
   `Configuring/Start.md:141` "Fundamental lua syntax errors will make Hyprland refuse to
   reload your config and pop an error").
6. **Phase 2 – clear everything** (`:698-722`):
   * `Config::animationTree()->reset()`
   * `Config::workspaceRuleMgr()->clear()`
   * `Config::monitorRuleMgr()->clear()`
   * `Desktop::Rule::ruleEngine()->clearAllRules()` (window + layer rules)
   * `g_pTrackpadGestures->clearGestures()`
   * `cleanTimers()` (all `hl.timer`s cancelled, `:497-504`)
   * `clearLuaLayoutProviders()`
   * `m_luaWindowRules/m_luaLayerRules` (named-rule merge maps) cleared
   * `m_errors`, `m_deviceConfigs`, `m_registeredPlugins` cleared
   * `m_eventHandler->clearEvents()` — **all `hl.on` subscriptions dropped**
     (`LuaEventHandler.cpp:224-233`)
   * `clearHeldLuaRefs()`
   * every keybind whose handler is `__lua` has its registry ref released, then
     `g_pKeybindManager->clearKeybinds()` (`:715-721`)
7. `reinitLuaState()` (`:724`) — `lua_close` + `luaL_newstate`, stdlib, `debug.sethook`
   removed, bindings re-registered, new `CLuaEventHandler`, `require` wrapper and searcher
   hook re-installed, `print` hooked (`:506-626`). Any Lua-side globals/closures from the
   previous run are gone.
8. Phase 1 again on the fresh state (`:727-730`), plugin Lua functions re-registered (`:733`).
9. **All config values reset**: `for v in m_configValues: v->reset(); v->resetSetByUser()`
   (`:735-738`). So `getoption ... set: false` for anything the new run doesn't touch.
10. Executes the main chunk under `guardedPCall(..., LUA_TIMEOUT_CONFIG_RELOAD_MS=1500,
    "config reload")` with a traceback handler (`:740-753`, watchdog `:443-486`, constant
    at `ConfigManager.hpp:108`). Runtime errors inside the main file abort *that file*;
    each `require()`d file is a separate pcall scope so its error is recorded via
    `addError("require(\"...\"): ...")` and the parent continues (`:215-260`, `:320-356`;
    wiki `Configuring/Start.md:104-107`).
    `m_lastConfigVerificationWasSuccessful = m_errors.empty()`.
11. `lua_gc(LUA_GCCOLLECT)` (`:757`) then `postConfigReload()` (`:762-855`):
    * uncaches window decorations for all windows;
    * **emergency mode** if there are errors and zero binds: runs `EMERGENCY_PCALL`
      (SUPER+Q terminal, SUPER+R hyprland-run, SUPER+M exit; `Emergency.hpp:4-46`);
    * if `m_errors` non-empty and `debug.suppress_errors` off → on-screen
      `ErrorOverlay` with up to ~15 lines ("Your config has errors:"), else the
      "autogenerated" warning, else the overlay is destroyed (`:779-802`);
    * not on first launch: `monitorRuleMgr()->scheduleReload()`, `ensureMonitorStatus()`,
      `ensureVRR()` (`:813-818`); XWayland toggle (`:821-830`);
    * `handlePluginLoads()` — may recursively `reload()` if the plugin set changed
      (`:848`, `:1119-1130`);
    * `refresher()->scheduleRefresh(REFRESH_ALL)` (`:850`) — one deferred prop refresh
      (input devices, screen shader, blur FBs, window states/rules, monitor states,
      layouts, cursor zoom, **config watcher list**, group bar gradients;
      `src/config/supplementary/propRefresher/PropRefresher.cpp:29-159`), which then emits
      `config.props_refreshed(true)` (`:158`);
    * `Event::bus()->m_events.config.reloaded.emit()` → Lua `config.reloaded` handlers,
      then socket2 `configreloaded>>` (`:852-854`).

### Idempotency implications for generated modules

* Because everything is cleared before re-execution, a module may unconditionally call
  `hl.config`, `hl.bind`, `hl.window_rule`, `hl.monitor`, `hl.animation`, `hl.on` etc. on
  every run. Nothing "leaks" between reloads except OS-level effects (env vars, spawned
  processes, dbus env pushes).
* Within a single run: `hl.bind` **appends** (`KeybindManager.cpp:187-195`, no dedupe), so
  the same key bound in two modules yields two keybinds. `hl.window_rule{name=...}` merges
  into the existing named rule (`LuaBindingsConfigRules.cpp:1190-1197`) — unnamed rules
  always create a new one. `hl.config` is last-writer-wins.
* **`hl.exec_cmd` at top level re-runs on every reload** — it spawns immediately with no
  first-launch guard (`LuaBindingsToplevel.cpp:321-334`). Autostart must go inside
  `hl.on("hyprland.start", ...)`: `start` is emitted exactly once, from the renderer's
  first frame (`src/render/Renderer.cpp:2057-2062`), so handlers re-registered by a reload
  never fire again (wiki `Configuring/Basics/Autostart.md:10-18`, `Expanding-functionality.md:41`).
* `hl.env` sets the compositor env immediately, but after first launch it early-returns if
  the value is unchanged (`LuaBindingsConfigRules.cpp:502-508`); with `dbus=true` it
  spawns `dbus-update-activation-environment` immediately on non-first launches (`:519-533`).
  Removing an `hl.env` line does not unset the variable.
* `hl.on("config.reloaded", ...)` registered by the config **does** fire at the end of the
  same reload that registered it (subscriptions are re-created in step 10, event emitted in
  step 11).
* Cost: a reload rebuilds the Lua VM and re-registers ~all bindings; the watchdog caps the
  user script at 1.5 s. Empirically on this box (hyprlang manager, ~10 sourced files),
  `hyprctl reload` round-trips in 7–67 ms including process spawn; a `-r`/prop refresh
  follows on the next loop iteration. Expect the Lua path to be in the same order of
  magnitude but not measured here (no Lua session available without switching the box).

Legacy comparison (`src/config/legacy/ConfigManager.cpp:726-750`, `:763-770`): hyprlang
reload also clears binds/rules/animations/gestures and re-parses; `exec` (not `exec-once`)
lines re-run on reload (`:1259-1266`), so the "top-level exec re-runs" hazard is the same in
both worlds.

---

## 2. File-watch scope and debounce

`src/config/shared/inotify/ConfigWatcher.cpp`:

* Watched set = `Config::mgr()->getConfigPaths()` unless `misc:disable_autoreload` is set
  (`:32-35`). For Lua that is `m_configPaths` = main file + every path resolved by
  `require()` (explicit `/`, `./`, `../`, `~/` paths and normal `package.path` searches;
  wildcard requires each tracked, `:215-260`, `:561-598`). Files pulled in via `dofile`,
  `loadfile` or `__require` are **not** tracked.
* Mask per path: files `IN_CLOSE_WRITE | IN_DONT_FOLLOW`; if the path is a directory,
  additionally `IN_CREATE | IN_DELETE | IN_MOVED_TO | IN_MOVED_FROM` (`:53-62`). A tracked
  path is only a directory if `require()` resolved to one, so in practice **directories are
  not watched** — a new file dropped in the modules dir triggers nothing until the main
  file (which `require`s it) is rewritten.
* Symlinks: `IN_DONT_FOLLOW` on the link itself plus a second watch on the canonical target
  with `IN_CLOSE_WRITE` (`:64-72`).
* Event handling: `wl_event_loop_add_fd` on the inotify fd (`EventLoopManager.cpp:127-128`);
  each event calls the callback → `reload()` synchronously (`ConfigWatcher.cpp:80-111`,
  `lua/ConfigManager.cpp:416-419`). **There is no debounce or coalescing.** Two
  `close_write`s = two full reloads. `IN_CLOSE_WRITE` means an editor/app that writes via
  temp-file + `rename()` triggers *no* event on the watched inode (the rename target gets a
  new inode; the old watch fires only for MOVED_* on directories, which aren't watched) —
  unless the watched path is a symlink, whose target watch is re-resolved on the next
  `update()`.
* Watch list refresh: `Config::watcher()->update()` runs in the prop refresher on
  `REFRESH_CONFIG_WATCHER` (`PropRefresher.cpp:148-149`), part of `REFRESH_ALL` scheduled
  by every `postConfigReload()`. All old watches are removed and re-added (`:37-73`), so
  after an atomic-rename write the *next* reload (any trigger) re-attaches to the new inode.

Practical rule for the app: write the module in place (open/truncate/write/close) if you
want inotify to fire, or write atomically and then explicitly `hyprctl reload`. Since the
manager rebuilds the watch list after each reload, either works as long as at least one
reload happens after a rename.

---

## 3. `hyprctl eval` — transient changes and persistence

`hyprctl eval <lua>` / `hyprctl repl <lua>` → `CConfigManager::eval(code, repl)`
(`HyprCtl.cpp:1109-1122`; `lua/ConfigManager.cpp:880-953`):

* Rejected with `"eval is only supported with the lua config manager"` under hyprlang
  (`HyprCtl.cpp:1111-1112`; observed on this box).
* Clears `m_errors`, `m_evalIssues`, `m_prints`; sets `m_isEvaluating = true` (`:884-893`).
  Code is loaded as `return <code>;` first, else as a chunk (`:894-901`), run under
  `guardedPCall(..., LUA_TIMEOUT_EVAL_MS=250, "hyprctl eval")` (`:903`). Returns `"ok"`
  or the error/print output; `repl` returns `tostring` of results.
* Runs in the **same Lua state** as the config, with all `hl.*` available. So
  `hl.config{general={gaps_in=6}}` parses into the live value, sets `setByUser=true`
  (`types/LuaConfigInt.cpp:24-25`), and because `isDynamicParse()` is true while
  evaluating (`lua/ConfigManager.cpp:1386-1388`), schedules that value's prop-refresh bits
  (`LuaBindingsConfigRules.cpp:1004-1008`). `hl.window_rule`, `hl.monitor`, `hl.device`,
  `hl.workspace_rule` likewise register live and schedule refreshes (`:731`, `:1098`, `:1162`,
  `:1271`). `hl.bind` appends a keybind immediately (§1). `hl.exec_scheduled_prop_refresh_immediately()`
  forces the pending refresh now (`LuaBindingsToplevel.cpp:530-533`, wiki
  `Expanding-functionality.md:150-165`).
* **Persistence: none.** The next reload (file save, `hyprctl reload`, plugin change)
  resets every value (`:735-738`), clears binds/rules/events and rebuilds the VM, so
  eval-time changes vanish unless the same change is also in a file. Conversely, an eval
  change is not visible in any file — `getConfigString()` returns "Not supported under lua"
  (`:1071-1073`).
* Side effect to know: `eval` wipes `m_errors` at entry (`:884`), so `hyprctl configerrors`
  after any eval no longer reflects the last reload (`configerrors` = `getErrors()` =
  joined `m_errors`, `HyprCtl.cpp:725-745`, `lua/ConfigManager.cpp:1057-1066`). Snapshot
  errors *before* using eval.
* `hyprctl dispatch <x>` under Lua is sugar for `eval 'return hl.dispatch(<x>)'`
  (`HyprCtl.cpp:1124-1141`).
* Runtime errors raised by `hl.*` calls during eval are collected and returned in the eval
  reply (`addError` while `m_isEvaluating`, `:857-870`); outside eval/parse (timers,
  keybinds) they become a notification "Runtime error in lua:" (`:869`).

---

## 4. `reload` vs `reload full-reset`; `keyword` under Lua

* `hyprctl reload` → `Config::mgr()->reload()` and returns `"ok"` unconditionally
  (`HyprCtl.cpp:1275-1277`). Uses the existing manager and the **cached** main path
  (`Jeremy.cpp:19-58`: computed once, re-evaluated only if `needsPathRecheck` or safe-mode
  changed).
* `hyprctl reload full-reset` (`HyprCtl.cpp:1262-1273`): destroys the manager,
  `Jeremy::flushCachedCfgPath()`, `initConfigManager()` (re-picks Lua if `hyprland.lua`
  exists — `src/config/ConfigManager.cpp:20-52`; Lua wins over `.conf` when both exist,
  `Jeremy.cpp:36-45`), flushes `CConfigValueBase` caches, `init()`, flushes again. This is
  the only way to switch hyprlang↔Lua live; wiki: "`full-reset` should not be used unless
  really necessary" (`Configuring/Advanced and Cool/Using-hyprctl.md:65-66`). Under
  legacy→Lua the new manager's `init()` runs `reload()` with `m_isFirstLaunch = true`, so
  monitor rule application/XWayland toggling in `postConfigReload` are skipped for that
  first pass (`:813`, `:823`) until the next reload, and `hyprland.start` will **not**
  re-fire (already emitted) — so `hl.on("hyprland.start")` autostarts in the new Lua config
  don't run; only top-level `hl.exec_cmd` would (and would then re-run every reload).
  Legacy `exec-once` entries already ran. Not tested on this box (would flip the user's session).
* `hyprctl keyword` (`HyprCtl.cpp:1160-1163`): refused unless the manager is legacy —
  `"keyword can't work with non-legacy parsers. Use eval."`. Under hyprlang it works
  (observed: `hyprctl keyword general:gaps_in 6` → `ok`, `getoption` unchanged since the
  value was already `6 6 6 6`).
* `hyprctl -r <cmd>` forces the same monitor/layout/input/shader/blur refresh after any
  command (`HyprCtl.cpp:2087-2088`, `:2119-2135`; wiki `Using-hyprctl.md:320`).
* `Hyprland --verify-config` (`src/main.cpp:38`, `:289-292`) instantiates the manager,
  runs the config, and exits with `!configVerifPassed()` — a way to lint a candidate
  config **without** touching the live session, but it spins up a full compositor init
  (heavy; ~seconds) and uses the default path resolution (`HYPRLAND_CONFIG` env or
  `--config`) so it can be pointed at a temp file.

---

## 5. Reading effective state

| Need | Command | Notes / source |
| --- | --- | --- |
| One option value + whether the config set it | `hyprctl -j getoption <key>` → `{"option","int"/"float"/"bool"/"str"/"vec2"/"custom"/"css"/"gradient"/"font_weight","set"}` | `HyprCtl.cpp:1647-1740`. Lua manager accepts `general:gaps_in` or `general.gaps_in` (`lua/ConfigManager.cpp:1030-1052`, `:1136-1141`); legacy accepts only colon form (observed: dot form → `no such option`). `set` mirrors `setByUser`, which is reset on every reload. |
| Same from inside Lua | `hl.get_config("general.gaps_in")` (colon also accepted) → value or `nil, "unknown config key ..."` | `LuaBindingsConfigRules.cpp:1020-1044`; via `hyprctl repl 'return hl.get_config("general.gaps_in")'` |
| All options with metadata | `hyprctl -j descriptions` | `Config::Values::getAsJson()` (`HyprCtl.cpp:1979-1981`). **`current` is the static default-value table, not the live value** — Lua values are copies (`types/LuaConfigUtils.cpp:24-49`); observed `descriptions.current = "5 5 5 5"` while `getoption` said `6 6 6 6`. Use it for name/description/type/min/max/map only. |
| Binds | `hyprctl -j binds` | `HyprCtl.cpp:1026-1085`. Under Lua every bind has `"dispatcher": "__lua", "arg": "<registry ref>"` (`LuaBindingsToplevel.cpp:155-157`), so the *action* is opaque; `key`/`modmask`/`submap`/flags/`description` are usable. The app must treat its own model as the source of truth for what a bind does. |
| Monitors | `hyprctl -j monitors` (`monitors all` incl. disabled) | effective mode/position/scale/transform/VRR etc. |
| Devices | `hyprctl -j devices` | mice/keyboards/tablets/touch with names for `hl.device` |
| Workspace rules | `hyprctl -j workspacerules` | reflects `workspaceRuleMgr` after reload |
| Layers | `hyprctl -j layers` | per-monitor levels + namespaces (for layer rules) |
| Animations | `hyprctl -j animations` | `[[leaves...],[beziers...]]`; `overridden` = explicitly configured |
| Errors | `hyprctl -j configerrors` → JSON array of lines | see §6 |
| Manager type | `hyprctl repl 'return hl.version()'` errors under hyprlang; `hyprctl systeminfo` also reports config type (`typeToString`, `src/config/ConfigManager.cpp:77-82`) | cheap probe for "is this session Lua?" |

Wiki reference for the info list: `Configuring/Advanced and Cool/Using-hyprctl.md:258-294`.

---

## 6. Error surfacing and rollback

Channels, in order of usefulness to the app:

1. **`hyprctl -j configerrors`** — the joined `m_errors` of the last parse (Lua:
   `getErrors()` `lua/ConfigManager.cpp:1057-1066`; formatted `HyprCtl.cpp:725-745`).
   Clean reads as `[""]`, **not** `[]` — the joined-errors string is what gets serialised,
   and an empty one still occupies an element (captured off the socket while implementing
   #52; the IPC client filters blank lines, so "no errors" is an empty tuple there).
   Contains syntax errors (phase 1), `require("x"): <err>` per broken
   module, `hl.config`-level messages like `"<file>:<line>: unknown config key 'x'"` /
   `"error setting 'general.gaps_in': ..."` (`LuaBindingsConfigRules.cpp:1000-1003`), and
   `configError`-raised messages from `hl.*` (they call `addError` while parsing,
   `:857-863`). Cleared by the next reload and by any `eval` (§3).
2. **On-screen error bar** (`ErrorOverlay`) with the same text, up to ~15 lines
   (`:779-802`); disappears on a subsequent clean reload. `debug.suppress_errors` hides it.
3. **socket2 `configreloaded>>`** — always emitted at the end of `postConfigReload`, success
   or failure, empty payload (`:852-854`; wiki `IPC/_index.md` events table). Also
   emitted after a phase-1 syntax failure (early `postConfigReload()` at `:692-696`).
4. **Lua `config.reloaded`** — no arguments (`LuaEventHandler.cpp:150`, wiki
   `Expanding-functionality.md:70`); **`config.props_refreshed(bool executedAsScheduled)`**
   (`:151-152`, wiki `:71`). Neither carries error info; a Lua-side handler could call
   nothing useful to fetch errors (no `hl.get_errors`).
5. Runtime errors outside parse/eval → notification popup "Runtime error in lua:" (`:869`);
   wiki `Configuring/Start.md:144`.
6. `hyprctl reload` reply is always `"ok"` (`HyprCtl.cpp:1275-1277`), `hyprctl eval` reply
   is the real error text (`:1118-1121`).

Rollback options:

* **Because phase 1 leaves state untouched on syntax errors**, a syntactically broken
  generated module leaves the previous live state in place — but only if the *main file*
  is the syntactically broken one. A syntax error inside a `require()`d module surfaces as
  a runtime `require("mod"): ...` error in phase 2, after state was cleared, so *that
  module's* contents are simply absent (e.g. all binds from `binds.lua` gone) while other
  modules load. Generated modules must therefore be syntactically valid by construction
  (the writer must never emit partial files).
* App-level rollback = restore the previous bytes of the module and let inotify/`hyprctl
  reload` re-apply. Keep an in-memory (or on-disk `.bak`) copy of the last known-good
  version of each generated module.
* Optional pre-flight: `luac5.5 -p <file>` syntax check of the generated file before
  writing (Hyprland v0.56.2 links Lua 5.5: `CMakeLists.txt:291`
  `pkg_search_module(LUA ... lua5.5 ...)`; `/usr/bin/luac5.5` exists on this box), or
  `Hyprland --verify-config` with `HYPRLAND_CONFIG` pointed at a temp copy of the tree
  (heavy).
* Value-level validation without a reload: `hyprctl eval 'hl.config{...}'` returns the
  parse error for bad values (`error setting 'x': ...`) — the same parser as at reload.
  Not fully free of side effects (the value is applied live if valid), but it is exactly the
  "instant apply" we want anyway.

---

## 7. Events the app can subscribe to over socket2

Format `EVENT>>DATA\n`, data truncated to 1024 bytes, newlines in data replaced by spaces
(`src/managers/EventManager.cpp:126-131`); a client that lets 64 events queue up is dropped
(`:168-176`). Path `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock`
(`:21`; wiki `IPC/_index.md`).

Relevant to a settings app (full list in wiki `IPC/_index.md` "Events list"):

| event | data | use |
| --- | --- | --- |
| `configreloaded` | empty | reload finished (success or not) → poll `configerrors`, refresh views |
| `monitoradded` / `monitoraddedv2` | `NAME` / `ID,NAME,DESCRIPTION` | refresh monitor page |
| `monitorremoved` / `monitorremovedv2` | same | |
| `activelayout` | `KEYBOARDNAME,LAYOUTNAME` | keyboard page |
| `submap` | `SUBMAPNAME` | binds page state |
| `openlayer` / `closelayer` | `NAMESPACE` | layer-rule pickers |
| `workspace*`, `createworkspace*`, `destroyworkspace*`, `moveworkspace*`, `renameworkspace` | ids/names | workspace-rule pickers |
| `openwindow` / `closewindow` / `windowtitle*` / `activewindow*` | address, class, title | window-rule "pick a window" helper |

Observed on this box during `hyprctl reload`: exactly one `configreloaded>>` line (python
listener; a first `socat -u` attempt raced and missed it — connect and drain before
triggering the reload).

There is **no** socket2 event for a prop refresh, for eval, or for individual value changes;
Lua-side `config.props_refreshed` is only reachable from inside the config. If the app needs
it, a generated `hl.on("config.props_refreshed", ...)` could `hl.exec_cmd` a notifier, but
that costs a process spawn per refresh — not recommended.

---

## 8. Recommended instant-apply pipeline

Assumes ADR-0002 layout: `~/.config/hypr/hyprland.lua` (generated, `require()`s modules)
plus `~/.config/hypr/<app>/modules/*.lua` (generated per section) and `user.lua`.

1. **Model edit** → mark section dirty; debounce in the app (e.g. 100–150 ms after last
   change, immediately on slider release / focus-out). Hyprland has no debounce, so the app
   must be the one that coalesces.
2. **Optional live preview for continuous controls** (sliders, colour pickers): on each
   tick, `hyprctl eval 'hl.config{<section>={<key>=<value>}}'` (or the matching
   `hl.window_rule`/`hl.monitor`) — sub-frame apply, correct prop refresh scheduled, real
   parse errors returned. On release, fall through to step 3, which makes it durable. Under
   hyprlang (this box today) the equivalent is `hyprctl keyword section:key value`.
3. **Persist**: render the whole affected module deterministically; syntax-check the text
   locally; write it. Two choices:
   * in-place write (truncate + write + close) → inotify `IN_CLOSE_WRITE` → auto reload; or
   * atomic rename + explicit `hyprctl reload` (safer against half-written files; the
     watcher re-attaches after the reload).
   Prefer atomic-rename + explicit reload: guaranteed single reload per apply, no partial
   file ever executed.
4. **Confirm**: wait for `configreloaded>>` on a long-lived socket2 connection (with a
   timeout ~2 s = the 1.5 s reload watchdog + margin), then `hyprctl -j configerrors`.
   Optionally `hyprctl -j getoption <touched keys>` and check `set == true` and the value.
   Do not run any `eval` between the reload and the `configerrors` read.
5. **On error**: show the messages inline (`hl.*` errors are prefixed `<file>:<line>:` by
   `Internal::getSourceInfo`, `LuaBindingsInternal.cpp:409-`), restore the last known-good module bytes, reload again, and re-verify.
   Because reload clears everything first, the restore is exact.
6. **Never** emit top-level `hl.exec_cmd`; autostart lives in `hl.on("hyprland.start")`.
   Never emit `hl.env` for things the user expects to be un-settable by removing them
   (document that env removal requires re-login).

Caveats / open questions

* No Lua session on this box: the Lua-side timings, the emergency-mode overlay, and the
  full-reset switch were not exercised. Should be re-checked once a test box or a nested
  Hyprland session (`Hyprland --config /tmp/x.lua` inside the current session) is available.
* Reload is O(whole config) — every apply re-runs every module. Fine for a settings app
  (tens of ms), but frequent slider ticks must go through eval, not the file.
* `hl.bind` duplicates: if `user.lua` binds a key the generated `binds.lua` also binds, both
  exist; the app should surface `hyprctl -j binds` duplicates by (`modmask`,`key`,`submap`).
* Reading bind *actions* back is impossible under Lua (`__lua` handler); the importer must
  build the model from files/legacy config, not from `hyprctl binds`.
* Watch list is only rebuilt after a reload; a module added to the modules dir is unnoticed
  until the main file changes or `hyprctl reload` runs (the app should always reload
  explicitly after adding a new module).
* `hyprctl reload` triggered by external tools (matugen etc. writing `.conf` bridges) will
  also re-execute the app's modules — harmless given idempotency, but the app should treat
  any `configreloaded` (not just its own) as "refresh state from Hyprland".
* Whether the compositor path cache means `hyprctl reload` after the app *creates*
  `hyprland.lua` on a hyprlang session does nothing — yes; the migration wizard must end
  with `hyprctl reload full-reset` (and warn about its side effects) or ask for a
  re-login.

---

## Appendix: commands used on this box (all read-only or no-op)

```
hyprctl version                    # Hyprland 0.56.2 (tag v0.56.2, commit efb5099)
hyprctl repl 'return hl.version()' # "eval is only supported with the lua config manager"
hyprctl -j getoption general:gaps_in   # {"option":"general:gaps_in","custom":"6 6 6 6","set":true}
hyprctl -j getoption general.gaps_in   # "no such option" (legacy wants ':')
hyprctl keyword general:gaps_in 6      # ok (same value; nothing changed)
hyprctl reload                         # 7-67 ms wall; socket2 emitted `configreloaded>>`
hyprctl -j binds | head ; hyprctl -j monitors ; hyprctl -j animations ; hyprctl -j descriptions | head
hyprctl configerrors                   # empty
```
