# hyprlang grammar and legacy → Lua keyword mapping (Hyprland 0.54 `.conf` → 0.56.2 `hl.*`)

Read by the agent working in the Importer or on a Loss code, when a ticket names a section here. **Issue:** #4 (part of #1). **Date:** 2026-08-19. **Status:** research complete; no code.

**Question.** What does a faithful hyprlang→Lua importer need to handle, and does an official converter already exist?

**Short answer.**
- hyprlang is a tiny line-oriented format (`key = value`, `{}` categories, `$vars`, `source=`, three `# hyprlang` directives, `{{ a op b }}`); its whole grammar fits in §1. The hard part is not hyprlang, it is the per-keyword *value* grammars that Hyprland's legacy handlers implement (bind flags, dispatcher args, rule strings, monitor lines).
- Every legacy keyword has a Lua counterpart in v0.56.2 (§2). The mapping is mostly mechanical; the genuinely lossy/ambiguous spots are listed in §2.11 and total about twenty items (mods spelling, bare keycodes, `binds` multi-key, `bindm`, `unbind` matching, resize percentages, `fullscreenstate -1`, `unlock` toggle semantics, ≤0.53 windowrule syntax, `border_color` pairs, `rounding` range, workspace `border/shadow/rounding` inversion, `addreserved` order, `sdr_eotf` codes, gesture `dispatcher` action, per-device tablet keys, `exec` timing, `$var` scoping across `source`d files, unknown-key strictness, wiki-vs-binary drift).
- **No official converter exists** (§3): hyprwm ships none in Hyprland, hyprland-guiutils, or the wiki; the wiki's migration guidance is one banner pointing at the frozen 0.54 wiki. Four small third-party converters exist (one proprietary, none licensed permissively). Hyprland itself contains a *runtime* legacy-dispatcher shim (`src/config/legacy/DispatcherTranslator.cpp`) that is the best spec for dispatcher argument grammar.
- The user's real config (§4) uses ~15 keyword kinds and 12 dispatchers; all map, with 8 items needing importer logic (invalid `movewindow h/j/k` letters, gesture→callback, orphan keys, bool words, cross-file `$var` scoping, `unbind` canonicalisation, externally regenerated `.conf` files, shell-quoting).

**Sources (primary only).** hyprlang v0.6.8 (`README.md`, `src/config.cpp`, `include/hyprlang.hpp`); https://standards.hyprland.org/hyprlang/ ; hyprland-wiki at commit `486b6c3` (the 0.54.0 selector commit) and `ecc06f3` (last legacy-syntax revision, 2026-04-20) for legacy semantics, and at `2e15371` (2026-08-17) for Lua; Hyprland v0.56.2 sources `src/config/legacy/{ConfigManager,DispatcherTranslator}.cpp`, `src/config/lua/bindings/*.cpp`, `src/config/lua/types/*.cpp`, `src/config/lua/{ConfigManager,LuaEventHandler}.cpp`, `src/config/values/*`, `src/config/shared/**`, `src/desktop/rule/**`, `src/managers/KeybindManager.cpp`, `src/helpers/math/Direction.hpp`, `example/hyprland.lua`; `/usr/share/hypr/stubs/hl.meta.lua` from the installed 0.56.2; GitHub releases v0.55.0/v0.56.0; `gh search` over hyprwm; `~/.config/hypr/**` (read-only). Line numbers refer to those exact revisions.

**Contents.** §1 hyprlang grammar · §2 keyword→`hl.*` mapping (2.1 binds, 2.2 dispatchers, 2.3 selectors, 2.4 bind caveats, 2.5 windowrule, 2.6 layerrule, 2.7 workspace rules, 2.8 monitor, 2.9 rule ordering, 2.10 animation/gesture/device/permission/plugin/env/exec/layout/source/values, 2.11 lossy-case index) · §3 converters verdict · §4 corpus.

## 1. hyprlang grammar (what an importer must parse)

Sources: hyprlang v0.6.8 (`hyprlang/src/config.cpp`, `hyprlang/include/hyprlang.hpp`), https://standards.hyprland.org/hyprlang/, Hyprland wiki @0.54.0 (`content/Hypr Ecosystem/hyprlang.md`), Hyprland v0.56.2 `src/config/legacy/ConfigManager.cpp`. hyprlang is line-oriented: it never tokenizes values; every line is `key = value`, `name {`, `}`, or a comment (`hyprlang/src/config.cpp:669-913`).

### 1.1 Line structure

| Rule | Behaviour | Source |
|---|---|---|
| Physical → logical line | `std::getline`; if the line ends with `\`, trailing spaces/tabs before the `\` are dropped and the next physical line is appended verbatim (its leading whitespace kept). Reported line number = first physical line. A final line ending in `\` is an error ("Last line ends with backslash"). | `config.cpp:44-65`, `:970-975` |
| Trim | Whole line trimmed first; LHS and RHS trimmed separately after splitting. | `config.cpp:672,722-723` |
| Comments | `#` at column 0 (after trim) → whole line is a comment (and checked for `hyprlang` directives). Elsewhere, first unescaped `#` truncates the line; `##` is an escape and becomes a single literal `#`. Comment stripping happens **before** variable expansion. | `config.cpp:674-706`; standards "Escaping the # character"; wiki `hyprlang.md:75-80` |
| Empty line | Ignored after comment strip + trim. | `config.cpp:708-709` |
| `key = value` | Split at the **first** `=`; further `=` stay in the value. Empty LHS → error "Empty lhs.". | `config.cpp:711,722-728` |
| Category open | Line with no `=` that ends with `{`; name is the trimmed text before `{` (`general{` is fine). Pushed on a category stack. | `config.cpp:713,900-908` |
| Category close | Line must be exactly `}`; anything else containing `}` (and no `=`) → "Invalid config line". Stray `}` → "Stray category close". Unclosed at EOF → error. | `config.cpp:881-897,990-999,1044-1053` |
| Other line w/o `=`/`{`/`}` | "Invalid config line". | `config.cpp:713-717` |
| Nesting | Full key = stack joined with `:` + LHS, so `general { snap { enabled = 1 } }` ≡ `general:snap:enabled = 1`. | `config.cpp:280-285`; wiki `:96-102` |
| Inline addressing | `category:sub:key = v` works anywhere, including inside another block (it is simply prefixed). Special-category keyed form: `device[NAME]:key = v` (`[`…`]` extracted from the assembled name). | `config.cpp:291-299`; wiki `:104-110` |
| Escapes | Only `\{`, `\}`, `\\` are unescaped, on the RHS only, after variable expansion, and **not** for `$VAR = ...` lines (which return earlier). | `config.cpp:805-827`; wiki `:134-164` |
| Max line length / Unicode | Not found in sources (plain `std::string`, byte-oriented). | — |
| Handlers vs. categories | An unscoped keyword handler (`bind`, `exec`, …) matches by LHS only, so `bind = …` inside `general { }` still invokes the handler. | `config.cpp:844-846,866-873` |

### 1.2 Variables

- Definition: `$NAME = value` (LHS starts with `$`). Redefinition overwrites. Stored sorted longest-name-first. `hyprlang/src/config.cpp:518-527,730,805`.
- Expansion: for every known variable, `$NAME` substrings in LHS (unless the line itself defines a variable) and RHS are string-replaced; no delimiter needed (`$NAME$SUFFIX`, `$NAMEbcd`). Longest name wins on prefix collisions. `config.cpp:733-753,83,525`; standards "Variables"; wiki `:56-73`.
- Recursion: replacement loop repeats until no match, max 100 iterations, then error "Expanding variables exceeded max iteration limit" — so a var's value may itself contain `$OTHER`. `config.cpp:733,796-802`.
- Order dependence: expansion happens when the line is parsed, so a variable must be defined **before** use during file parsing. Only `parseDynamic` (`hyprctl keyword $VAR = x`) re-parses previously seen lines that used the var. `config.cpp:529-537,749-750,1058-1068`.
- Undefined `$FOO`: left as literal text (only known names are replaced; no error). `config.cpp:737-753`.
- `${NAME}` syntax: not found in sources.
- Environment variables: at the start of each `parse()` hyprlang snapshots `environ` and seeds the variable list with it, so `$HOME`, `$XDG_CONFIG_HOME`, etc. expand in any value, in hyprlang itself (not just Hyprland's `source`). `config.cpp:543-551,1070-1074`; wiki `:189`.
- Hyprland's `env = NAME,VALUE` handler calls `setenv` at parse time (`ConfigManager.cpp:1857-1870`), but the hyprlang env snapshot is taken at parse start (`config.cpp:1073-1074`), so such vars are visible as `$NAME` only on the next reload. Hyprland also exports `HYPRLAND_V_0_53=1` around `parse()` (`ConfigManager.cpp:710-745`).

### 1.3 `source = path` (Hyprland handler, `ConfigManager.cpp:1802-1855`)

- Registered as a plain handler (`registerHandler(&::handleSource, "source", {false})`, `ConfigManager.cpp:614`); the RHS reaches it already `$VAR`/env-expanded by hyprlang.
- Path `< 2` chars → error. Path is made absolute relative to the **currently parsed file** (`absolutePath(rawpath, m_configCurrentPath)`; `absolutePath` itself is external to these sources), then `glob()` with `GLOB_TILDE` (so `~` and glob patterns work). `:1803-1816`.
- No match → error "source= globbing error: found no match" (missing file is an error unless wrapped in `# hyprlang noerror true`, since noError only suppresses error recording — `config.cpp:1034`). `:1816-1820`.
- Each result: regular files parsed with `m_config->parseFile()` with `m_configCurrentPath` temporarily set to that file (nested relative `source` resolves relative to it); directories/non-regular skipped with a warning; first error string returned. Result order is glob's default (GLOB_NOSORT not set). `:1824-1854`.
- Sourced files share the same parser state: category stack, variables, `noerror`/`if` flags carry across (`config.cpp:1004-1056` never resets `currentFlags`).

### 1.4 `# hyprlang` directives (`config.cpp:567-617`, `:676-684`)

Only comment lines starting `# hyprlang` (`#` at col 0, then trimmed text starting `hyprlang`; whitespace-split) are directives:

| Directive | Semantics | Source |
|---|---|---|
| `# hyprlang noerror X` | X ∈ {`true`,`yes`,`enable`,`enabled`,`set`, empty} → suppress error recording; anything else (e.g. `false`) → re-enable. Lines are still parsed/applied. | `:579-585`, `:1034`; standards "Escaping errors" |
| `# hyprlang if VAR` / `# hyprlang if !VAR` | Pushes a block; VAR looked up in env first, then config vars, **without** `$`; truthy = exists and non-empty (`truthy()` body not in sources; wiki `:188`). No `==`/other operators found. | `:594-614` |
| `# hyprlang endif` | Pops; "stray endif" error if none open. | `:587-591` |
| Anything else | Ignored (no error). | `:570-571` |

Notes: while the innermost `if` failed, non-comment lines are skipped, but comment lines are still processed, so nested `if`s inside a failed block still push/pop and only the innermost block's state is consulted (`:676-684`). `noerror` is not honoured by `parseRawStream` (only `parseFile`) (`:982` vs `:1034`).

### 1.5 `{{ expr }}` arithmetic (`config.cpp:619-667`, `:755-794`)

- Evaluated in the RHS after variable substitution, repeatedly while `{{` remains; a `{{` preceded by an odd number of `\` is skipped (escaped). `:757-794`; wiki `:112-148`.
- Expression = exactly 3 whitespace-separated tokens: `A op B`, op ∈ `+ - * /`; A/B are variable **names without `$`** or numeric literals; parsed as `float`; result substituted via `std::format("{}", float)`. No precedence, no nesting, no chained ops (use intermediate vars). Spaces around operator required. `:627-662`; wiki `:116-132`.
- Failures are line errors ("Invalid expression type…", "does not look like a number…"). `:629,641-655`.

### 1.6 Special categories

- API: `addSpecialCategory(name, {key, ignoreMissing, anonymousKeyBased})` + `addSpecialConfigValue(cat, field, default)`. No key & not anonymous → one static category. Keyed → a new instance per distinct key value; anonymous → auto-numbered keys. `hyprlang.hpp:142-168`; `config.cpp:140-165`.
- Hyprland registers: `device` (key `name`), `monitorv2` (key `output`), `windowrule` (key `name`), `layerrule` (key `name`), `plugin` (static, `ignoreMissing=true`). `ConfigManager.cpp:518,567,591,595,626`.
- Block form: `device { name = X ... }` — the **first** field in a keyed block must be the key, else error "special category's first value must be the key". A second block with the same key value re-targets the existing instance (per-field last-wins). `config.cpp:359-391,395-441`.
- Inline keyed form: `device[X]:sensitivity = 1` (`config.cpp:291-335`); this is what `hyprctl keyword` uses (wiki `:104-110`).
- `ignoreMissing` (Hyprland's `plugin {}`): unknown fields silently ignored (`config.cpp:386-387`), which is why `plugin:foo:bar` for a not-yet-loaded plugin does not error.
- Repetition semantics: config values (`values`/special values) overwrite — last wins (`config.cpp:452-513`; wiki `:21-23`). Handler keywords are invoked every time they appear (accumulate). `windowrule` is both a handler (`windowrule = ...`, `ConfigManager.cpp:610`) and a keyed category (`windowrule { name = ... }`, `:591`).

### 1.7 Handler keywords (`config.cpp:838-875`, `:1102-1106`)

- Tried only when no config value matched. Handler receives `(COMMAND=LHS, VALUE=RHS)` as raw C-strings after trim/comment strip/var expansion/escape removal — hyprlang does **not** split on commas. `hyprlang.hpp:173`; `config.cpp:872`.
- Matching: `allowFlags=false` → exact `LHS == name`; `allowFlags=true` → `LHS.starts_with(name)` and LHS contains no `:`. All matching handlers are called (no break). Scoped handler names (`a:b`) must match the category stack. `config.cpp:844-873`.
- Hyprland flag handlers: `bind` (`bindm`, `bindl`, `bindeld`… flags = chars after `bind`; unknown flag → error), `gesture`, `env` (`envd`). All others exact: `exec`, `execr`, `exec-once`, `execr-once`, `exec-shutdown`, `monitor`, `unbind`, `workspace`, `windowrule`, `layerrule`, `bezier`, `animation`, `source`, `submap`, `plugin`, `permission`; `windowrulev2`/`layerrulev2` always error "deprecated". `ConfigManager.cpp:601-623,1514-1543,431-435`.
- `exec*` pass the raw string to the executor (`ConfigManager.cpp:1249-1291`); `env` splits on the first comma only (`CVarList(value, 2)`, `:1858`).

### 1.8 Value types (`config.cpp:452-511`, `:185-274`)

| Type | Accepted syntax | Notes / source |
|---|---|---|
| INT | decimal via `stoll` (after `isNumber(v,false)` — hyprutils, not in sources); `0x…` hex (must consume whole string); bool words by **prefix, lowercase**: `true`/`on`/`yes` → 1, `false`/`off`/`no` → 0 (so `none`→0, `yesterday`→1, `yes, please :)`→1) | `:195-197,259-271` |
| COLOR (stored as INT ARGB) | `rgba(RRGGBBAA)` (8 hex) or `rgba(r, g, b, a)` (ints — themselves parsed by the INT rules — plus float alpha 0–1); `rgb(RRGGBB)` or `rgb(r, g, b)`; `0xAARRGGBB` | `:198-258`. `#RRGGBB` not supported by hyprlang's own COLOR parser (would also start a comment) — Hyprland's `ParserUtils::parseColor` gained `#`-hash support in 0.55 (release notes "config: allow hashes for parsing colors (#14337)"). |
| FLOAT | `std::stof` (accepts numeric prefix; trailing garbage not rejected) | `:465-472` |
| VEC2 | `x y` split at first space; error if either side contains another space | `:474-490` |
| STRING | raw RHS (may be empty) | `:492-494` |
| CUSTOM | raw string to app handler | `:496-505` |
| GRADIENT (Hyprland custom) | space-separated colours (max 10) + optional last `Ndeg` token (int degrees); colours via `ParserUtils::parseColor` (not in sources); default registered as `0x…` hex | `ConfigManager.cpp:103-161,508-511` |

Other Hyprland custom types: css-gaps (`ConfigManager.cpp:168-186`), font weight (`:193-208`).

### 1.9 Error handling

- Errors are per-line and collected as one newline-joined string; Hyprland sets `throwAllErrors=true, allowMissingConfig=true`, so all errors are reported and parsing continues (`config.cpp:982-987,1034-1039`; `hyprlang.hpp:97-124`; `ConfigManager.cpp:486`).
- Unknown option → "config option <x> does not exist" unless a handler matches or the category has `ignoreMissing` (`config.cpp:446-449,386-387`).
- `getConfigValue(name)` returns empty `std::any` if the name is unknown; values not set by the user keep the registered default (`hyprlang.hpp:395-400`; `config.cpp:178-183,921-926`).
- Main config path: `Supplementary::Jeremy::getMainConfigPath()` (external to these sources); explicit path via `g_pCompositor->m_explicitConfigPath`; missing config → default generated (`ConfigManager.cpp:667-685`).

### 1.10 Importer pitfalls

- `key =` with empty RHS: STRING sets `""`; INT/FLOAT/VEC2 error; handlers get `""`. `config.cpp:265-266,467,492`.
- `=` inside values is fine (first `=` splits) — e.g. `bind = SUPER, equal, exec, foo`. `:711`.
- A `key = value {` line is a k=v line (the `{` check applies only when no `=`). `:713-719`.
- Trailing commas / comma splitting: hyprlang never splits on commas; each Hyprland handler does its own `CVarList` split (e.g. `bind` uses `numbArgs` = 4/5/6 so the last field may contain commas: `ConfigManager.cpp:1554-1555`).
- `#` inside a value (e.g. hex colours) needs `##`; `#` in the middle of `rgba(...)` is not protected. `config.cpp:686-704`.
- Multiple spaces in VEC2 error; `1.5x` parses as float 1.5. `:476-483,467`.
- Variables and expressions apply to LHS too (`$cat:key = v` works if `$cat` defined). `:739-743`.
- Special keyed blocks: key must be the first field. `:434-437`.
- Handler names are matched by prefix when `allowFlags` — any LHS starting with `bind`/`env`/`gesture` (without `:`) hits those handlers. `:869`.
- Line continuation `\` joins lines with no separator inserted. `:53-62`.
- Orphan keys outside any category (e.g. a bare `workspace_swipe = true` at top level) are "config option does not exist" errors — the value is not applied. `config.cpp:446-449`.
## 2. Legacy keyword → `hl.*` mapping

Quick reference (details and citations in the subsections):

| Legacy keyword | Lua API (v0.56.2) | Section |
|---|---|---|
| `bind[lremntisodpcgukx] = MODS, key[, desc][, devs], DISPATCHER, ARGS` | `hl.bind("MOD + MOD + key", hl.dsp.…(…), { locked=, repeating=, release=, … })` | 2.1 |
| `bindm = MODS, key, movewindow\|resizewindow` | `hl.bind(keys, hl.dsp.window.drag() / .resize())` | 2.1 |
| `unbind = MODS, key` / `unbind = all` | `hl.unbind("MOD + key")` / `hl.unbind("all")` | 2.1 |
| `submap = name` … `submap = reset` | `hl.define_submap("name", [reset,] function() … end)` | 2.1 |
| every dispatcher name + arg string | `hl.dsp.*` (namespaces `window`, `workspace`, `group`, `cursor`, plus top-level) | 2.2 |
| `windowrule { name; match:*; effects }` / `windowrule = eff v, match:x v` | `hl.window_rule({ name=, match={…}, effect=value })` | 2.5 |
| `windowrulev2 = …` (≤0.53) | rejected by 0.56.2 legacy engine; needs a ≤0.53 rename table | 2.5 |
| `layerrule { … }` / `layerrule = …` | `hl.layer_rule({ match={namespace=}, … })` | 2.6 |
| `workspace = SEL, rule:val, …` | `hl.workspace_rule({ workspace="SEL", … })` | 2.7 |
| `monitor = NAME, MODE, POS, SCALE, k v…` / `monitorv2 {}` | `hl.monitor({ output=, mode=, position=, scale=, … })` | 2.8 |
| `bezier = n, x0,y0,x1,y1` | `hl.curve("n", {type="bezier", points={{x0,y0},{x1,y1}}})` | 2.10.1 |
| `animation = leaf, on, speed, curve[, style]` | `hl.animation({leaf=, enabled=, speed=, bezier=, style=})` | 2.10.1 |
| `gesture[p] = fingers, dir, [mod:], [scale:], action[, args]` | `hl.gesture({fingers=, direction=, action=, mods=, scale=, …})` | 2.10.2 |
| `device { name = X; … }` / `device[X]:key = v` | `hl.device({ name="X", key=v })` | 2.10.3 |
| `permission = regex, type, mode` | `hl.permission("regex", "type", "mode")` | 2.10.4 |
| `plugin = path` / `plugin { NAME { k = v } }` / `plugin:NAME:k` | `hl.plugin.load(path)` / `hl.config({plugin={NAME={k=v}}})` | 2.10.5 |
| `env = K,V` / `envd = K,V` | `hl.env("K","V")` / `hl.env("K","V", true)` | 2.10.6 |
| `exec-once` / `exec` / `execr` / `execr-once` / `exec-shutdown` | `hl.on("hyprland.start", …)` + `hl.exec_cmd` / top-level `hl.exec_cmd` / `hl.dsp.exec_raw` / `hl.on("hyprland.shutdown", …)` | 2.10.7 |
| `general:layout = …`, `dwindle{}`, `master{}`, `scrolling{}` | `hl.config({general={layout=}, dwindle={…}, …})` | 2.10.8 |
| `source = path` (globs, `~`, `$HOME`) | `require("mod")` / `require("~/abs/path")` / `require("./dir/*")` | 2.10.9 |
| `cat:sub:key = value` / `cat { sub { key = value } }` incl. `col.*` gradients | `hl.config({cat={sub={key=value}}})`; gradient → `{colors={…}, angle=N}` | 2.10.10 |
| `$VAR = v` / `$VAR` | Lua `local VAR = v` / `..` concatenation; env vars → `os.getenv` | 1.2, 2.10.10 |

### 2.1 `bind*` → `hl.bind`

Sources: `DT` = `src/config/legacy/DispatcherTranslator.cpp`; `CM` = `src/config/legacy/ConfigManager.cpp`; `KM` = `src/managers/KeybindManager.cpp` (all Hyprland v0.56.2); `LBD` = `src/config/lua/bindings/LuaBindingsDispatchers.cpp`; `LBT` = `…/LuaBindingsToplevel.cpp`; `LBI` = `…/LuaBindingsInternal.cpp`; `LBU` = `…/LuaBindingsDispatcherUtils.cpp`; `LBR` = `…/LuaBindingsRegistration.cpp`; `meta` = `/usr/share/hypr/stubs/hl.meta.lua` (0.56.2); `W054-B/D` = wiki @0.54.0 `Configuring/Binds.md` / `Dispatchers.md`; `Wlua-B/D` = current wiki `Configuring/Basics/Binds.md` / `Dispatchers.md`.

Key structural fact: **`hl.bind` is positional, not a table**: `hl.bind(keys: string, dispatcher: HL.Dispatcher|function, opts?: HL.BindOptions): HL.Keybind` (meta:822, LBT:132-256). Mods+key are ONE `+`-separated string (LBT:58-130). There is no `mods=`/`key=`/`action=` table form.

```
legacy:  bind[flags] = MODS, key, dispatcher, args              (CM:1492-1655; W054-B:8-9,119-150)
         bindd       = MODS, key, description, dispatcher, args (CM:1554-1584; W054-B:259)
         bindk       = MODS, key, [!]dev1 dev2, dispatcher, args (CM:1539,1554,1580,1606-1614)
lua:     hl.bind("MOD1 + MOD2 + key", hl.dsp.X{...}, { locked=true, description="...", ... })  (LBT:132-256; meta:437-453,822)
```
`hl.bind` returns an `HL.Keybind` object (LBT:253-254; meta:640-672). Legacy `bind` is registered with hyprlang `allowFlags=true` (CM:607 `registerHandler(&::handleBind, "bind", {true})`); flags = chars after `bind` (CM:1514-1543).

#### Flag letters

| Legacy flag | Legacy meaning | Lua opts field | Lossy? |
|---|---|---|---|
| `l` | locked: works under input inhibitor/lockscreen (CM:1518; W054-B:137) | `locked` (LBT:189; meta:439) | no |
| `r` | release: trigger on key release (CM:1519; W054-B:138) | `release` (LBT:190; meta:440) | no |
| `e` | repeat when held (CM:1520; W054-B:142) | `repeating` — note the name (LBT:188; meta:438). Same exclusivity: `(long_press||release) && repeat` is an error (CM:1545-1546 vs LBT:223-224) | no |
| `m` | mouse bind: `bindm = MODS, key, movewindow|resizewindow [1|2]` — 3 args, HANDLER forced to `"mouse"`, COMMAND = 3rd arg (CM:1521,1559-1561,1586-1589; W054-B:271-313). Excludes `e/r/l` (CM:1548-1549) | **No opts field is read.** `hlBind` never sets `kb.mouse` (only tests it, LBT:226); `HL.BindOptions` has no `mouse` (meta:437-453). Wiki and `example/hyprland.lua:290-291` show `{ mouse = true }` (Wlua-B:223-224) but it is inert in v0.56.2 code. Mouse behaviour comes from the dispatcher: `hl.dsp.window.drag()` / `hl.dsp.window.resize()` / `resize({keep_aspect_ratio=bool})` (LBD:681-697,1067-1077,1094-1099,1386-1387), which set `releasePending` so the release re-fires the bind (LBD:682-683,689-690; KM:782,787) | semantically ~equal; the `mouse` SKeybind flag itself (KM:268 conflict check, KM:785) is not settable from Lua |
| `n` | non-consuming (CM:1522; W054-B:143) | `non_consuming` (LBT:191; meta:441) | no |
| `a` | auto-consuming (CM:1523; not in W054-B table) | `auto_consuming` (LBT:192; Wlua-B:88) — **missing from `HL.BindOptions` stub** (meta:437-453) but read by code | no (stub gap only) |
| `t` | transparent: can't be shadowed (CM:1524; W054-B:145) | `transparent` (LBT:193; meta:442) | no |
| `i` | ignore mods (CM:1525; W054-B:146) | `ignore_mods` (LBT:194; meta:443) | no |
| `s` | multi-key / "separate": `binds = Control_L&Shift_L, K&J, …` — every `&`-token becomes a keysym; mods become keysyms too (CM:1526,1567-1574,1578; W054-B:189-207) | No opts flag. Approximation: put every keysym in the key string joined by `+`: `hl.bind("Control_L + Shift_L + K + J", …)` — non-mod tokens accumulate into `kb.sMkKeys` (LBT:87-128), multi-sym binds require a full-set match (KM:671-692). Legacy `multiKey` path (KM:665-670) is a different matcher | **approximate** |
| `o` | long press (CM:1527; W054-B:141) | `long_press` (LBT:196; meta:445) | no |
| `d` | has description; description is the 3rd comma field (`bindd = MODS,key,desc,disp,args`, CM:1528,1554-1557,1582; W054-B:253-266) | `description` or `desc` string in opts (LBT:199-205; meta:449-450) | no (legacy desc cannot contain commas, W054-B:256; Lua can) |
| `p` | bypass app keybind-inhibit (CM:1529; W054-B:149) | `dont_inhibit` (LBT:195; meta:444) | no |
| `c` | click (implies release) (CM:1530-1533; W054-B:139) | `click` (sets `kb.release=true`) (LBT:210-213; meta:447) | no |
| `g` | drag (implies release) (CM:1534-1537; W054-B:140) | `drag` (LBT:215-218; meta:448); `click&&drag` error both sides (CM:1551; LBT:220-221) | no |
| `u` | submap universal (CM:1538; W054-B:150,423-427) | `submap_universal` (LBT:197; meta:446) | no |
| `k` | per-device: extra 3rd field `[!]dev1 dev2 …` (space-separated, `!` prefix = exclusive) (CM:1539,1554-1562,1580,1606-1614) | `device = { inclusive = bool, list = {"dev1","dev2"} }` (LBT:232-248; meta:451; Wlua-B:197-216). `!` → `inclusive=false` | no |
| `x` | allow input capture (CM:1540) | `allow_input_capture` (LBT:249; meta:452) | no |
| (unknown) | error `"bind: invalid flag"` (CM:1541) | — | — |

Combos (`bindel`, `bindle`, `bindeld`, `bindm`…) are just the union of the per-letter opts; order of letters is irrelevant on both sides (CM:1514-1543 loops over chars).

**MODS string.** Legacy `stringToModMask` is a case-insensitive *substring* test after upper-casing (KM:221-242): any of `SHIFT, CAPS, CTRL|CONTROL, ALT|MOD1, MOD2, MOD3, SUPER|WIN|LOGO|MOD4|META, MOD5` anywhere in the string. So `SUPER_SHIFT`, `SUPERSHIFT`, `SUPER SHIFT`, `super shift`, `$mainMod SHIFT` all parse; a non-empty MODS with mask 0 is an error (CM:1601-1604). Lua: `parseKeyString` splits the whole key string on `+`, trims tokens (LBT:60,72), mods must be exact upper-case tokens from `modFromSv` (`SHIFT, CAPS, CTRL, CONTROL, ALT, MOD1, MOD2, MOD3, SUPER, WIN, LOGO, MOD4, META, MOD5`; LBT:30-48) and must precede the key (LBT:76-80). Importer: split legacy MODS on `_`, space, or by known substrings, upper-case, join with ` + `. `SUPER_SHIFT` passed verbatim → `Unknown keysym: "SUPER_SHIFT"` (LBT:109-118). Empty MODS (`bind = , Print, …`; W054-B:20-25) → `hl.bind("Print", …)` (Wlua-B:102).

**Key formats.** Legacy `parseKey` (CM:1481-1490): bare number `> 9` → keycode; `code:N` → keycode; `catchall`; else raw string kept and resolved at press time (KM:710-725, both exact and case-insensitive keysym). Lua (LBT:58-130):
- `code:N` → keycode (LBT:99-107). **Bare numeric keycode (`bind = SUPER, 42, …`) has no Lua form** — `"42"` goes to `xkb_keysym_from_name` (LBT:109) → convert to `code:42`.
- `catchall` → `kb.catchAll` (LBT:66-69); allowed only inside a submap in both (CM:1619-1621; LBT:151-152).
- `mouse:272`, `mouse_up/down/left/right`, `switch:…`, `switch:on:…`, `switch:off:…` are "special syms": kept verbatim as `kb.key`, cannot be combined with another key (LBT:51-56,87-97). Same strings as legacy (KM:461,537-545; W054-B:172-178,209-232). Switch names are compared as exact strings (KM:693-695) → case/space must match `hyprctl devices`.
- xkb names: resolved at bind time with `XKB_KEYSYM_CASE_INSENSITIVE` (LBT:109) → `Q` and `q` both bind lowercase-q keysym; unknown names are a config *error* in Lua (LBT:111-118; hint for `Enter`→`Return` LBT:115-116), whereas legacy accepted anything and silently never matched (KM:713-722). `kb.key` keeps the user's spelling (LBT:121,128), `displayKey` = full string (LBT:157).

**bindm mapping.** `bindm = ALT, mouse:272, movewindow` → `hl.bind("ALT + mouse:272", hl.dsp.window.drag())`; `bindm = ALT, mouse:273, resizewindow` → `hl.dsp.window.resize()`; `resizewindow 1` → `resize({keep_aspect_ratio=true})`, `resizewindow 2` → `resize({keep_aspect_ratio=false})` (LBD:1072-1077,1094-1099; legacy semantics W054-B:289; Wlua-B:222-233). Legacy `mouse` handler receives `"1"/"0"`+arg for press/release (KM:809-810 → DT:675-677); Lua relies on `m_passPressed` (KM:804) + `releasePending`.

**submap.** Legacy `submap = name[, resetTarget]` sets `m_currentSubmap` for all following `bind`s; `submap = reset` ends (CM:1795-1800; W054-B:371-481). Lua: `hl.define_submap(name, [reset_target,] fn)` — sets current submap for binds created inside `fn`, restores previous after (LBT:258-287; meta:826; Wlua-B:335-439). Nested legacy submaps that re-declare `submap = parent` collapse into nested `define_submap` calls (Wlua-B:385-421). Entering: `bind = ALT, R, submap, resize` → `hl.bind("ALT + R", hl.dsp.submap("resize"))`; `submap, reset` → `hl.dsp.submap("reset")` (DT:520-522,839; LBD:178-180,267-275). Current submap readable via `hl.get_current_submap()` (meta:838).

**unbind.** Legacy `unbind = MODS, key` removes by exact `(modmask, key, keycode, catchAll)` (CM:1657-1674; KM:197-202); `unbind = all` clears everything (CM:1660-1665). Lua `hl.unbind(keys)` (LBT:404-416; meta:860) removes binds whose `displayKey` equals the string, or whose space-stripped, lower-cased form equals it (KM:204-219); `hl.unbind("all")` clears all (LBT:405-407). So the importer must emit the same key string as the target `hl.bind` (modulo spaces/case). (Wlua-B:539-546 claims case-sensitivity; code normalizes to lowercase — KM:205-215.) **Lossy in practice:** an `unbind = SUPER, Q` that targets a bind emitted by an earlier `hl.bind(mainMod .. " + Q", …)` only works if the emitted string normalises to `super+q`; a legacy `unbind` of a bind that was declared with a *different mod spelling* (`SUPER_SHIFT` vs `SUPER SHIFT`) matched by modmask in legacy but will not match by string in Lua unless the importer canonicalises both.

**exec.** `bind = …, exec, cmd` → `hl.dsp.exec_cmd(cmd[, rules_table])` (dispatcher, deferred; LBD:233-250,1399; meta:873). Config-time `exec = cmd` keyword → top-level `hl.exec_cmd(cmd[, rules])` (runs immediately; LBT:321-335; meta:830). `execr` → `hl.dsp.exec_raw(cmd)` (LBD:252-260,1400).

### 2.2 Dispatchers → `hl.dsp.*`

Every key of `m_dispMap` (DT:796-868; the same 71 names are the only legacy dispatchers registered, KM:39-114). Lua registration: LBD:1343-1418; stub: meta:870-938. Lua `window=` args are optional selectors resolved via `pushWindowUpval`/`windowFromUpval` (LBI:316-332); nil → focused window.

| Legacy | Legacy arg grammar (DT / W054-D) | Lua | Notes / lossy |
|---|---|---|---|
| `exec` | `[rule; rule] cmd` string → `executor()->spawn(args)` (DT:65-70; W054-D:27,181-198) | `hl.dsp.exec_cmd(cmd, rules?)` (LBD:145-165,233-250; Wlua-D:74,249-257) | With no rules table Lua calls the same `spawn(string)` overload (LBD:160) as legacy (DT:66) — bracket-rule prefix strings can be passed verbatim (executor not in sources to confirm parsing). Rules table keys are window-rule effect names (`float`/`floating`, `move`, `workspace`, …; LBI:509-573). Empty cmd → error (LBD:148-149) |
| `execr` | raw cmd (DT:72-75) | `hl.dsp.exec_raw(cmd)` (LBD:167-172,252-260) | no |
| `killactive` | none (DT:77-79) | `hl.dsp.window.close()` (LBD:498-500,699-703) | no |
| `forcekillactive` | none (DT:81-83) | `hl.dsp.window.kill()` (LBD:502-504,705-709) | no |
| `closewindow` | window (DT:85-87) | `hl.dsp.window.close({window=sel})` | no |
| `killwindow` | window (DT:89-91) | `hl.dsp.window.kill({window=sel})` | no |
| `signal` | int (DT:93-99) | `hl.dsp.window.signal({signal=N})` (LBD:506-508,711-719) | no |
| `signalwindow` | `window,signal` split at first `,` (DT:101-115; W054-D:37) | `hl.dsp.window.signal({signal=N, window=sel})` | no |
| `togglefloating` | `` / `active` / window (DT:44-49,117-120) | `hl.dsp.window.float({window=sel})` (default action toggle; LBD:510-512,721-728; LBI:444-452) | no |
| `setfloating` | same (DT:122-125) | `hl.dsp.window.float({action="on", window=sel})` — actions: `toggle`/`""`, `enable`/`on`, `disable`/`off`; unknown → toggle (LBI:306-314) | no |
| `settiled` | same (DT:127-130) | `hl.dsp.window.float({action="off", window=sel})` | no |
| `pseudo` | `` / `active` / window (DT:132-135) | `hl.dsp.window.pseudo({window=sel})` (LBD:583-585,815-822) | no |
| `workspace` | workspace selector string passed straight to `changeWorkspace` (DT:137-139; W054-D:38,127-161: `1`, `+1`, `m+1`, `r-1`, `e+1`, `e~2`, `name:foo`, `previous`, `previous_per_monitor`, `empty[m][n]`, `special[:name]`) | `hl.dsp.focus({workspace="e+1"})` — same string passed to `changeWorkspace` (LBD:1130-1132,1162-1174; Wlua-D:78,200-230) | no; workspace may also be a number or HL.Workspace (LBI:179-200; meta:394) |
| `movetoworkspace` | `ws` or `ws,window` (split at LAST `,`) (DT:209-223) | `hl.dsp.window.move({workspace=ws, window=sel})` (LBD:480-487,860-869) | no; `follow` omitted/true = not silent (LBD:862-863) |
| `movetoworkspacesilent` | same (DT:225-239) | `hl.dsp.window.move({workspace=ws, follow=false, window=sel})` | no |
| `renameworkspace` | `id name` (numeric id via `stoi`; no name → clear) (DT:141-158) | `hl.dsp.workspace.rename({workspace=sel, name="…"})` (LBD:1225-1231,1280-1294) | Lua superset (any workspace selector via `query().string()`, LBD:1226) |
| `fullscreen` | `[0|1] [toggle|set|unset]`; `1` = maximize (DT:160-186; W054-D:44) | `hl.dsp.window.fullscreen({mode="fullscreen"|"maximized"|"0"|"1", action="toggle"|"set"|"unset", layout_aware?=true, window?})` (LBD:514-547,730-780) | no (`"0"`/`"1"` accepted as mode, LBD:737-740). Legacy `layoutAware` is always true (DT:167) = Lua default (LBD:733) |
| `fullscreenstate` | `internal client [action]`; `-1` = keep current; action ignored by translator (DT:188-207; W054-D:45,219-243) | `hl.dsp.window.fullscreen_state({internal=N, client=N, action?="set", layout_aware?, window?})` (LBD:549-581,782-813) | **lossy**: Lua requires both numbers (LBD:798-801) and has no `-1` handling in code (LBD:549-551) though Wlua-D:284 lists it; legacy `3` (max+fs) not listed in Wlua-D:282-288. Default Lua action `set` (LBD:786) ≈ translator behaviour |
| `movefocus` | direction, first char `l/r/u/d` (DT:241-246) | `hl.dsp.focus({direction="l"})` — accepts `l/left, r/right, u/up/t, d/down/b` (LBD:1104-1106,1145-1153; LBI:294-304) | no |
| `movewindow` | direction, or `mon:<monitor>[ silent]` (DT:248-268; W054-D:50) | dir → `hl.dsp.window.move({direction=d})` (LBD:828-846); `mon:X` → `hl.dsp.window.move({monitor="X", follow=not silent})` (LBD:489-496,871-880) | no |
| `swapwindow` | direction or window (DT:270-290) | dir → `hl.dsp.window.swap({direction=d})`; window → `hl.dsp.window.swap({target=sel})` (also `with`/`other`) (LBD:612-621,924-950) | no (Lua uses `CA::swapWith`, legacy uses `switchTargets`) |
| `centerwindow` | none (arg `1` "respect reserved" from W054-D:52 is ignored by translator DT:292-294) | `hl.dsp.window.center()` (LBD:595-597,971-975) | legacy `1` lost on both sides |
| `togglegroup` | none (DT:296-298) | `hl.dsp.group.toggle()` (LBD:43-45,87-91) | no |
| `changegroupactive` | `b`/`prev` back, else forward; or index ≥1 (`<=0` → last) (DT:300-323) | `hl.dsp.group.prev()` / `hl.dsp.group.next()` / `hl.dsp.group.active({index=N})` (LBD:47-53,93-105,119-127) | index semantics of `setGroupActive` not in sources |
| `movegroupwindow` | `b`/`prev` back, else forward (DT:325-327) | `hl.dsp.group.move_window({forward=false|true})` (LBD:55-57,107-117) | no |
| `focusmonitor` | monitor (dir/id/name/`current`/`+1`) (DT:329-334) | `hl.dsp.focus({monitor="DP-1"})` (LBD:1108-1113,1155-1160) | no; monitor may be object/number (LBI:156-177) |
| `movecursortocorner` | `0..3` (DT:336-340) | `hl.dsp.cursor.move_to_corner({corner=N, window?})` (LBD:35-37,67-75) | no |
| `movecursor` | `x y` (DT:342-354) | `hl.dsp.cursor.move({x=,y=})` (LBD:39-41,77-85) | no |
| `workspaceopt` | always error "deprecated" (DT:356-358) | none | dead |
| `exit` | none (DT:360-362) | `hl.dsp.exit()` (LBD:174-176,262-265) | no |
| `movecurrentworkspacetomonitor` | monitor (DT:364-374) | `hl.dsp.workspace.move({monitor=m})` (LBD:1256-1264,1312-1329) | no |
| `focusworkspaceoncurrentmonitor` | workspace (DT:398-403) | `hl.dsp.focus({workspace=ws, on_current_monitor=true})` (LBD:1134-1139,1162-1174) | no |
| `moveworkspacetomonitor` | `ws monitor` (space) (DT:376-396) | `hl.dsp.workspace.move({workspace=ws, monitor=m})` (LBD:1243-1254,1318-1324) | no |
| `togglespecialworkspace` | none or name (DT:405-421) | `hl.dsp.workspace.toggle_special(name?)` — plain string arg, not a table (LBD:1207-1223,1274-1278) | no |
| `forcerendererreload` | none (DT:423-425) | `hl.dsp.force_renderer_reload()` (LBD:221-223,333-336) | no |
| `resizeactive` | resizeparams: `dx dy` relative, `exact W H`, `%` forms (DT:427-437 via `parseWindowVectorArgsRelative`; W054-D:17,53) | `hl.dsp.window.resize({x=,y=,relative=bool})` (LBD:635-638,1082-1092) | **lossy**: `exact A B` → `relative=false`; `dx dy` → `relative=true`; percentage forms (`20 25%`, `exact 50% 50%`) have no Lua field (numbers only) |
| `moveactive` | resizeparams (DT:439-446) | `hl.dsp.window.move({x=,y=,relative=bool})` (LBD:640-643,848-858) | same lossiness |
| `resizewindowpixel` | `resizeparams,window` (split first `,`) (DT:460-473) | `hl.dsp.window.resize({x,y,relative,window=sel})` | same |
| `movewindowpixel` | `resizeparams,window` (DT:448-458) | `hl.dsp.window.move({x,y,relative,window=sel})` | same |
| `cyclenext` | tokens: `prev|p|last|l`, `next|n`, `tile|tiled`, `float|floating`; `visible`/`hist` already dropped by translator (DT:475-493; W054-D:57) | `hl.dsp.window.cycle_next({next=bool, tiled=bool, floating=bool, window?})` (LBD:599-606,977-998) | `visible`/`hist` lost (both sides, DT:489) |
| `focuswindowbyclass` | alias of focuswindow (DT:835) | `hl.dsp.focus({window=sel})` | no |
| `focuswindow` | window (DT:495-500) | `hl.dsp.focus({window=sel})` (LBD:1115-1120,1176-1181) | no |
| `tagwindow` | `tag [window]` (space split) (DT:502-514) | `hl.dsp.window.tag({tag="+code", window?})` (LBD:623-625,1000-1009) | no; Lua-only `hl.dsp.window.clear_tags()` (LBD:627-629,1011-1015) |
| `toggleswallow` | none (DT:516-518) | `hl.dsp.window.toggle_swallow()` (LBD:631-633,1017-1020) | no |
| `submap` | `reset` or name (DT:520-522) | `hl.dsp.submap(name)` (LBD:178-180,267-275) | no |
| `pass` | window (DT:524-529) | `hl.dsp.pass({window=sel})` — `window` **required** in code (LBD:277-285 `requireTableFieldWindowSelector`), also sets `releasePending` (LBD:187-188) | no |
| `sendshortcut` | `mod,key[,window]` (3 comma fields; key = name / `code:N` / `mouse:N` / number>9) (DT:531-589) | `hl.dsp.send_shortcut({mods="SUPER", key="F4", window?})` (LBD:353-421,442-454); `mods` uses same `stringToModMask` (LBD:403) so legacy mod spellings work here | no |
| `sendkeystate` | `mod,key,down|repeat|up,window` (4 fields) (DT:591-627) | `hl.dsp.send_key_state({mods, key, state="down"|"repeat"|"up", window?})` (LBD:423-440,456-478) | no |
| `layoutmsg` | free string, per layout (DT:629-631; dwindle/master/scrolling tables in `wiki054md/…/Dwindle-Layout.md:42-56`, `Master-Layout.md:34-70`, `Scrolling-Layout.md:36-53`) | `hl.dsp.layout("swapwithmaster master")` — string passthrough (LBD:193-195,287-295) | no (Lua layouts may also handle via `HL.LayoutProvider.layout_msg`, meta:434) |
| `dpms` | `on|off|toggle [monitor]` (starts_with match; anything else = off) (DT:633-651) | `hl.dsp.dpms({action="on"|"off"|"toggle", monitor?})` (LBD:197-208,297-311) | default differs: legacy no-arg → off, Lua no-arg → toggle (LBI:444-452) |
| `swapnext` | `l|last|prev|b|back` = prev, else next (DT:653-655) | `hl.dsp.window.swap({next=true})` / `swap({prev=true})` (LBD:608-610,952-966) | no |
| `swapactiveworkspaces` | `mon1 mon2` (DT:657-668) | `hl.dsp.workspace.swap_monitors({monitor1=,monitor2=})` (LBD:1266-1272,1331-1341) | no |
| `pin` | `` / `active` / window; toggle only (DT:670-673) | `hl.dsp.window.pin({window?})` (action optional; LBD:645-647,1021-1028) | no |
| `mouse` | internal for `bindm`; arg `movewindow` / `resizewindow [1|2]`, prefixed with press state (DT:675-677; KM:809-810) | `hl.dsp.window.drag()` / `hl.dsp.window.resize()` / `resize({keep_aspect_ratio=bool})` (LBD:681-697,1067-1077,1094-1099) | see flag `m` |
| `bringactivetotop` | none (DT:679-681) | `hl.dsp.window.bring_to_top()` (LBD:649-651,1030-1033) | no |
| `alterzorder` | `top|bottom[,window]` (DT:683-692) | `hl.dsp.window.alter_zorder({mode="top", window?})` (LBD:653-655,1035-1044) | no |
| `focusurgentorlast` | none (DT:694-696) | `hl.dsp.focus({urgent_or_last=true})` (LBD:1122-1124,1183-1187) | no |
| `focuscurrentorlast` | none (DT:698-700) | `hl.dsp.focus({last=true})` (LBD:1126-1128,1189-1193) | no |
| `lockgroups` | `lock`/``/`lockgroups` → enable; `toggle`; else (`unlock`) disable (DT:702-711) | `hl.dsp.group.lock({action=…})` (LBD:59-61,129-135) | **translate `unlock` → `"off"`**: Lua's parser maps unknown strings (incl. `unlock`) to *toggle* (LBI:306-314) |
| `lockactivegroup` | `lock` enable, `toggle`, else disable (DT:713-722) | `hl.dsp.group.lock_active({action=…})` (LBD:63-65,137-143) | same `unlock` caveat; legacy empty → off, Lua empty → toggle |
| `moveintogroup` | direction (DT:724-729) | `hl.dsp.window.move({into_group="l"})` (LBD:661-663,882-891) | no |
| `moveintoorcreategroup` | direction (DT:731-736) | `hl.dsp.window.move({into_or_create_group="l"})` (LBD:673-675,893-902) | no |
| `moveoutofgroup` | `` / `active` / window (DT:738-744) | `hl.dsp.window.move({out_of_group=true, window?})` (bool) or `{out_of_group="l"}` (dir) (LBD:665-667,904-919) | Lua adds direction |
| `movewindoworgroup` | direction (DT:746-751) | `hl.dsp.window.move({direction="l", group_aware=true})` (LBD:669-671,834-840) | no |
| `setignoregrouplock` | no-op (deprecated) (DT:861) | none | dead |
| `denywindowfromgroup` | `on`/`toggle`/else off (DT:753-762) | `hl.dsp.window.deny_from_group({action=…})` (LBD:677-679,1059-1065) | Lua no window arg either |
| `event` | data string (DT:764-766) | `hl.dsp.event(str)` (LBD:210-212,313-321) | no |
| `global` | `app:shortcut` (DT:768-770) | `hl.dsp.global(str)` (LBD:214-219,323-331; sets `releasePending`) | no |
| `setprop` | `window prop value…` (space split, window REQUIRED) (DT:772-783; W054-D:200-217) | `hl.dsp.window.set_prop({prop=, value=, window?})` (LBD:657-659,1046-1057; Wlua-D:259-272) | no (Lua window optional) |
| `forceidle` | seconds (`getPlusMinusKeywordResult`) (DT:785-790) | `hl.dsp.force_idle(seconds)` (LBD:225-227,338-346) | no |
| `releaseinputcapture` | none (DT:792-794) | `hl.dsp.release_input_capture()` (LBD:229-231,348-351) | no |

**Absent from `m_dispMap` in v0.56.2** (legacy line fails with `"Bad dispatcher"`, DT:31-32): `splitratio` (and there is no `hl.dsp.splitratio` either, LBD:1343-1418 — the scrolling/dwindle `layoutmsg` strings are the replacement). Older wiki names like `focuswindowbyclass` survive as aliases (DT:835).

**Lua-only dispatchers** (no legacy name): `hl.dsp.no_op()` (LBD:1198-1205), `hl.dsp.window.clear_tags` (LBD:1011-1015), `hl.dsp.workspace.change_id({workspace,id})` (LBD:1233-1241,1296-1310), `hl.dsp.window.swap({target})`, `hl.dsp.group.active({index})`.

### 2.3 Window / monitor / workspace selector strings

- Legacy `windowFromArg`: empty or `"active"` → focused window; otherwise `Desktop::viewState()->query().selector(arg).runWindow()` (DT:44-49). All other legacy dispatchers with a window field call the same `query().selector(str)` (e.g. DT:86,105,214,283,452,496,509,525,583,687,740,777).
- Lua: every `window=` field goes through `windowSelectorFromLuaSelectorOrObject` (LBI:202-223): an `HL.Window` object is turned into `"address:0x…"`, a string/number is passed verbatim; then at dispatch time the same `query().selector(str).runWindow()` runs (LBI:316-321; LBD:183,412,434,616,1116). `HL.WindowSelector = string|integer|HL.Window` (meta:395). So **the legacy selector grammar is identical on the Lua side** — pass the string through unchanged.
- Grammar (the selector implementation itself is not in the provided sources): W054-D:13 — bare regex = class regex, `class:`, `initialclass:`, `title:`, `initialtitle:`, `tag:`, `pid:`, `address:`, `activewindow`, `floating`, `tiled`. Wlua-D:24-42 adds `stableid:` and window objects; "if no window is provided, the active window is used".
- Monitor / workspace selectors: strings/numbers passed verbatim, objects converted to name / id string (LBI:156-200); resolved with `monitorState()->query().relativeTo(focused).configString(str)` (LBI:64-78,404-407) and `getWorkspaceIDNameFromString` / `workspaceState()->query().string()` (LBI:80-99,389-402) — the same calls the translator uses (DT:52-63,255,330,383).

### 2.4 Bind/dispatcher behaviour differences an importer must handle

1. **Shape**: `hl.bind(keystring, dispatcher, opts)`; the dispatcher is an `HL.Dispatcher` userdata produced by `hl.dsp.*` factories (LBU:69-81,114-126) — it cannot be called directly (LBU:24-26); run with `hl.dispatch(d)` (LBT:352-375; meta:828). A plain Lua function is also accepted (LBT:148-149; LBR:64-85). Multiple legacy `bind`s on one key can either stay as multiple `hl.bind` calls (order preserved, KM appends) or become one function calling `hl.dispatch` repeatedly (Wlua-B:164-177).
2. **Variables**: hyprlang `$mainMod` has no Lua counterpart; use Lua locals and `..` concatenation (`mainMod .. " + K"`, Wlua-B:382). Legacy exec strings with `$VAR` need Lua string escaping (`\\` inside `"…"`, e.g. Wlua-B:288).
3. **Mods**: substring-tolerant legacy (`SUPER_SHIFT`, `SUPERSHIFT`) vs strict `+`-tokens in Lua (LBT:30-48,60-85). Emit `"SUPER + SHIFT + Q"`.
4. **Keys**: bare numeric keycode → `code:N`; unknown xkb names become config errors (LBT:111-118); `Enter` → `Return`.
5. **Flag renames**: `e`→`repeating`, `p`→`dont_inhibit`, `i`→`ignore_mods`, `n`→`non_consuming`, `a`→`auto_consuming`, `o`→`long_press`, `u`→`submap_universal`, `k`→`device={…}`, `x`→`allow_input_capture`, `d`→`description=`; `m` has no opts equivalent (LBT:188-249).
6. **bindm**: choose dispatcher `hl.dsp.window.drag()` / `resize()` and drop the flag; `mouse=true` in opts is ignored by v0.56.2 code (LBT:161-251, no read of `"mouse"`; meta:437-453).
7. **binds (multi-key)**: approximate with `A + B` key strings; not identical matcher (KM:665-692).
8. **submap**: sequential `submap = x … submap = reset` blocks → `hl.define_submap("x", [reset,] function() … end)` (LBT:258-287); binds must be created *inside* the callback; nested submaps become nested calls.
9. **unbind**: emit the exact display string (spaces/case-insensitively compared, KM:204-219), not `MODS, key`.
10. **exec**: `bind …, exec, X` → `hl.dsp.exec_cmd("X")`; keyword `exec = X` → `hl.exec_cmd("X")` (LBT:321-335). Both `exec_cmd` forms accept an optional rules table (LBI:509-573; Wlua-D:256).
11. **Toggle actions**: legacy `unlock` → `"off"`, legacy empty for `dpms`/`lockactivegroup` meant *off*, Lua empty means *toggle* (LBI:306-314,444-452; DT:633-651,713-722).
12. **resizeparams**: percentages have no Lua representation; convert `exact` → `relative=false`, deltas → `relative=true` (LBD:1082-1092,848-858).
13. **fullscreenstate `-1`** and legacy trailing `action` token: not representable / not handled in Lua code (LBD:549-581,798-801).
14. **Deprecated/no-op**: `workspaceopt`, `setignoregrouplock`, `splitratio` — emit a comment, no Lua call. `hl.dsp.no_op()` exists for conditional binds (LBD:1198-1205; Wlua-D:92).
15. **Global shortcuts / pass**: `pass`, `sendshortcut`, `global` mark the bind `releasePending` (LBD:187-188,417-418,215-216) so release events are delivered exactly as the legacy special-dispatcher list did (KM:782).
16. **Blocking**: Lua bind callbacks run on the compositor loop and must not block (Wlua-B:36-43); keep external commands as `hl.dsp.exec_cmd`.
### 2.5 `windowrule` / `windowrulev2` → `hl.window_rule`

**Legacy grammar actually parsed by v0.56.2 (`src/config/legacy/ConfigManager.cpp`)**

- v0.56.2 registers `windowrule` as a hyprlang *special category* keyed by `name` with `enable` (INT 1) plus one value per match prop (`match:<prop>`) and one per effect (`src/config/legacy/ConfigManager.cpp:591-592`, `:648-664`). This is the block form documented in the 0.54 wiki (`wiki054md/.../Window-Rules.md:17-25`):
  ```ini
  windowrule {
    name = apply-something
    match:class = my-window
    border_size = 10
  }
  ```
- It also registers a keyword handler `windowrule = ...` (`ConfigManager.cpp:610`, `handleWindowrule` at `:2016-2054`). Grammar: comma-separated elements; **every element must contain a space** (`:2027-2029` → error `invalid field {}: missing a value`); the token before the space is either `match:<prop>` (`:2031-2038`) or an effect name (`:2039-2045`); anything else → `invalid field type` (`:2046-2047`). I.e. `windowrule = match:class my-window, border_size 10` (`wiki054md/.../Window-Rules.md:29`). Anonymous keyword rules go to `m_keywordRules` (`:2049`).
- `windowrulev2` is **rejected**: the handler unconditionally errors `"windowrulev2 is deprecated. Correct syntax can be found on the wiki."` (`ConfigManager.cpp:431-435`, registered `:622`). `layerrulev2` likewise (`:449-453`, `:623`).
- The old ≤0.53 form `windowrule = float, class:^(kitty)$` / v1 `windowrule = float, ^(kitty)$` is **not accepted** by v0.56.2: `float` alone has no space (`:2027-2029`), and `class:...` is neither `match:`-prefixed nor an effect (`:2046`). The exact ≤0.53 grammar (`class:`/`title:`/`initialClass:`/`onworkspace:`/`floating:0|1` etc.) is **not found in the provided sources** — the 0.54 wiki already documents only the `match:` syntax (`wiki054md/.../Window-Rules.md:15-64`), and the current wiki just points to the 0.54 pages for "old hyprlang syntax" (`wiki/hyprland-wiki/content/Configuring/Basics/Window-Rules.md:6-8`). Any ≤0.53 → v3 rename table has to come from another source (0.53 wiki); flag as unverified. **Importer implication:** configs still on ≤0.53 `windowrulev2` syntax cannot be validated against v0.56.2 at all; the importer would need its own ≤0.53 rename table if it wants to accept them.
- Value conventions in legacy v3: booleans go through `truthy()` = `"1"` or prefix `true`/`yes`/`on`, case-insensitive (`src/helpers/MiscFunctions.cpp:829-843` v0.56.2); so `float on`, `float 1`, `float true` all work; `match:float 0` etc.

**Lua API (`src/config/lua/bindings/LuaBindingsConfigRules.cpp`)**

- `hl.window_rule{...}` registered at `LuaBindingsConfigRules.cpp:1397`; handler `hlWindowRule` `:1162-1276`. Reserved keys: `name` (string, `:1177-1181`), `enabled` (bool, `:1183-1187`), `match` (table, `:1199-1226`); every other string key is an effect (`:1228-1271`). A named rule reused across calls is *updated* (`:1189-1197`); returns an `HL.WindowRule` handle with `is_enabled`/`set_enabled` (`hl.meta.lua:788-791`, `:862`).
- `match` values: bool → `"true"/"false"`, number → `lua_tointeger` string, string as-is (`:1207-1212`); the key is looked up in the shared `matchPropFromString` table (`:1219`) — same table as legacy (`src/desktop/rule/Rule.cpp:26-45`). So match field names are identical to legacy `match:<x>` minus the prefix.
- Effect keys: typed table `Internal::WINDOW_RULE_EFFECT_DESCS` (`src/config/lua/bindings/LuaBindingsInternal.hpp:49-109`); unknown keys fall back to `windowEffects()->get(key)` (dynamic/plugin effects, `:1240-1257`) with string/bool/number stringified (`LuaBindingsInternal.cpp:475-489`). `border_color` additionally accepts a raw legacy string when gradient parsing fails (`LuaBindingsInternal.cpp:496-501`). `move/size/max_size/min_size` accept a string `"x y"` or a 2-array of numbers/strings (`src/config/lua/types/LuaConfigExpressionVec2.cpp:26-67`).
- LSP stub `HL.WindowRuleSpec` lists only `enabled`, `match`, `name` (`hl.meta.lua:597-601`) — effect fields are **not** typed in the stub (a stub gap, not an API gap).

**Matcher fields (legacy `match:X` → Lua `match = { X = ... }`)** — names from `src/desktop/rule/Rule.cpp:26-45`, engines `:52-72`:

| legacy `match:` | Lua `match.` | engine / value type | notes |
|---|---|---|---|
| `class` | `class` | RE2 regex string (`Rule.cpp:52`) | `RE2::FullMatch` = whole-string match (`RegexMatchEngine.cpp:16`); `^(..)$` anchors are redundant but harmless. `negative:` prefix negates (`RegexMatchEngine.cpp:7-10`; wiki `Window-Rules.md:83` (0.54) / `:92-93` (current)) |
| `title` | `title` | regex | same |
| `initial_class` | `initial_class` | regex | (≤0.53 `initialClass:` — not in sources) |
| `initial_title` | `initial_title` | regex | |
| `tag` | `tag` | TagMatchEngine, `isTagged` (`TagMatchEngine.cpp:12`) | plain name, `name*` = dynamic-only (`Window-Rules.md:219-221` 0.54) |
| `xwayland` | `xwayland` | bool via `truthy` (`BoolMatchEngine.cpp:6`) | Lua bool `true`/`false` (`:1208-1209`); legacy `0/1/on/yes/true` |
| `float` | `float` | bool | **name is `float`, not `floating`**; the `floating→float` alias exists only for *effects* in `buildRuleFromTable` (exec rules) (`LuaBindingsInternal.cpp:532-533`), not for match |
| `fullscreen` | `fullscreen` | bool | |
| `pin` | `pin` | bool | (≤0.53 `pinned:` — not in sources) |
| `focus` | `focus` | bool | |
| `group` | `group` | bool | |
| `modal` | `modal` | bool | already present in 0.54 wiki (`:59`) |
| `fullscreen_state_internal` | `fullscreen_state_internal` | int (`IntMatchEngine.cpp:8`) 0-3 | Lua numbers OK (`:1210`) |
| `fullscreen_state_client` | `fullscreen_state_client` | int 0-3 | |
| `workspace` | `workspace` | workspace static selector string (`WorkspaceMatchEngine.cpp:11`) | id, `name:x`, or selector `w[t1]`, `f[1]s[false]`… (`Workspace-Rules.md:62-69` current) |
| `content` | `content` | regex vs numeric id **or** name (`WindowRule.cpp:448`) | legacy wiki says int 0-3 (`0.54:63`); current says string none/photo/video/game (`current:71`); both work in both |
| `xdg_tag` | `xdg_tag` | regex | |
| `namespace` | `namespace` | regex | layer rules only (`LayerRule.cpp:107-110`) |
| — | (`exec_token`, `exec_pid`) | internal exec-rule props (`Rule.cpp:70-71`), no string names — not user-settable |

**Effects (legacy `NAME VALUE` → Lua `NAME = value`)** — names from `src/desktop/rule/windowRule/WindowRuleEffectContainer.cpp:12-72` (identical strings on both sides; legacy hyprlang keys are the same snake_case names). Legacy value parsing: `WindowRule.cpp:213-354`; Lua types: `LuaBindingsInternal.hpp:49-109`.

| effect (same name both sides) | legacy value | Lua type / value | notes |
|---|---|---|---|
| `float`, `tile`, `fullscreen`, `maximize`, `center`, `pseudo`, `no_initial_focus`, `pin`, `persistent_size`, `allows_input`, `dim_around`, `decorate`, `focus_on_activate`, `keep_aspect_ratio`, `nearest_neighbor`, `no_anim`, `no_blur`, `no_dim`, `no_focus`, `no_follow_mouse`, `no_max_size`, `no_shadow`, `no_shortcuts_inhibit`, `opaque`, `force_rgbx`, `sync_fullscreen`, `immediate`, `xray`, `render_unfocused`, `no_screen_share`, `no_vrr`, `no_auto_hdr`, `confine_pointer`, `stay_focused` | `truthy` string (`WindowRule.cpp:224-257`) | `CLuaConfigBool` — bool or 0/1 (`LuaConfigBool.cpp:10-27`) | Lua rejects `"on"`/`"yes"` strings for these (bool type). Rule `decorate` defaults true (`hpp:82`) |
| `fullscreen_state` | `"<internal> [<client>]"` ints (`WindowRule.cpp:59-79`) | **string** `"1 2"` (`hpp:57`; wiki current `:124`) | not a table |
| `move`, `size`, `max_size`, `min_size` | two space-separated expressions, `Math::parseExpressionVec2` (`WindowRule.cpp:266-274`); vars monitor_w/h, window_x/y/w/h, cursor_x/y (0.54 wiki `:114-130`) | `CLuaConfigExpressionVec2`: string `"x y"` or `{x, y}` array of numbers/strings (`LuaConfigExpressionVec2.cpp:26-67`) | old `%`/`cursor`/`onscreen` tokens of ≤0.53 not in sources; v3 uses expressions only |
| `monitor` | string id or name (`WindowRule.cpp:276-280`) | string `"1"`/`"DP-1"`, may add `" silent"` (current wiki `:129`) | `monitor` is `CLuaConfigString` (`hpp:60`); numbers accepted only because `lua_isstring` is true for numbers (`LuaConfigString.cpp:14`) |
| `workspace` | string: `3`, `name:x`, `special:x`, `3 silent`, `unset` (0.54 wiki `:106`) | string, same (`hpp:61`; current wiki `:130`) | |
| `group` | string opts `set [always]`, `new`, `lock [always]`, `barred`, `deny`, `invade`, `override ...`, `unset` (0.54 wiki `:177-193`) | string, same (`hpp:62`; current `:206-223`) | |
| `suppress_event` | space list → `parseStringList` (`WindowRule.cpp:202-211,282`): fullscreen, maximize, activate, activatefocus, fullscreenoutput (+ `x11configurerequest` current `:134`) | **string** with spaces (`hpp:63`), not a table | |
| `content` | none/photo/video/game via `NContentType::fromString` (`WindowRule.cpp:284`) | string (`hpp:64`) | |
| `no_close_for` | int ms (`WindowRule.cpp:297-303`) | integer (`hpp:65`) | |
| `scrolling_width` | float (`:314-319`) | number (`hpp:66`) | added in prelua wiki (`wikiprelua/.../Window-Rules.md:113`) |
| `rounding` | int ≥0 (`:305-312`) | int, Lua-side range 0..20 (`hpp:67`) | **Lua rejects >20; legacy had no upper bound** |
| `border_size` | int (`:298-303`) | int (`hpp:68`) | |
| `rounding_power` | float clamped 1..10 (`:321-326`) | number 1..10 (`hpp:69`) | |
| `scroll_mouse`, `scroll_touchpad` | float clamped 0.01..10 (`:327-333`) | number 0.01..10 (`hpp:70-71`) | |
| `animation` | `"style [opt]"` string (`:279-280`; wiki `:141`) | string e.g. `"popin 80%"` (`hpp:72`; current `:165`) | |
| `idle_inhibit` | `none|always|focus|fullscreen` (`:81-92`) | string (`hpp:73`) | |
| `opacity` | `"a [override] [b [override] [c [override]]]"` (`:94-137`); 1 value copies to inactive & fullscreen (`:130-133`) | **string**, identical grammar (`hpp:74`; current wiki `:168`) | numbers alone are not accepted by the typed desc (string) — pass `"0.8"` |
| `tag` | `+name` set / `-name` unset / `name` toggle (wiki `:145`) | string (`hpp:75`) | |
| `border_color` | `color [color]` or `color... Ndeg [color... [Ndeg]]` (`:139-200`) | `CLuaConfigGradient`: color string or `{colors={...}, angle=N}` (`LuaConfigGradient.cpp:21-60`) **or** the raw legacy string as fallback (`LuaBindingsInternal.cpp:496-501`) | Lua gradient table describes only *one* gradient; active+inactive pair needs the legacy string form |
| `tonemap` | `on|1`, `off|0`, `clamp`, `limited` (`:285-295`) | string (`hpp:108`; current wiki `:201`) | |
| any dynamic/plugin effect not in the tables | raw string (`WindowRule.cpp:214-215`) | any string/bool/number stringified (`ConfigRules.cpp:1240-1257`) | this is how `plugin:` rules flow |

Legacy-only / lossy notes for window rules:
- Not found in the v0.56.2 effect table (so gone from *both* legacy and Lua by 0.56): old camel/concat names `noscreenshare`, `nomaxsize`, `noinitialfocus`, `windowdance`, `prop <name> <val>`, `unset` (as a rule), `bordercolor`, `idleinhibit`, `suppressevent`, `keepaspectratio`, `focusonactivate`, `forcergbx`, `syncfullscreen`, `nearestneighbor`, `renderunfocused`, `noclosefor`, `persistentsize`, `scrollmouse`, `scrolltouchpad`, `dimaround`, `noshortcutsinhibit`, `fullscreenstate`, `maxsize`, `minsize`, `noborder`, `nodim`, `noblur`, `noanim`, `noshadow`, `nofocus`, `stayfocused`, `allowsinput` — v0.56.2 only knows the snake_case names in `WindowRuleEffectContainer.cpp:12-72`. `noborder` has no snake_case counterpart (`border_size 0` is the wiki idiom); `windowdance`, `prop` and rule-level `unset` do not exist (only `workspace unset`, `group unset`).
- Wiki-only names not in the v0.56.2 binary tables: `no_wobble`, `no_xdg_drags` (current wiki `:187`, `:202`) — not in `WindowRuleEffectContainer.cpp` v0.56.2 (would fall through to `windowEffects()->get()` and error).
- Type shifts: bool effects `on/yes/1` → Lua `true`/`1` (`LuaConfigBool.cpp:10-27` rejects other numbers and strings); `rounding` gains 0..20 range; `move/size` become 2-arrays or strings; `border_color` may be a table.
- Legacy named rules can also be toggled at runtime via `hyprctl keyword 'windowrule[name]:enable false'` (0.54 wiki `:307-323`, `ConfigManager.cpp:892-895`); Lua uses `enabled = false` in the spec or `rule:set_enabled(false)` (`ConfigRules.cpp:1183-1198`, current wiki `:359-374`).

### 2.6 `layerrule` → `hl.layer_rule`

- Legacy: same grammar as `windowrule` — `layerrule = blur on, match:namespace waybar` or block `layerrule { name=…; match:namespace=…; no_anim=on }` (`wiki054md/.../Window-Rules.md:325-363`; handler `ConfigManager.cpp:2056-2093`, special category `:595-596`, `:658-664`). Old `layerrule = blur, namespace-regex` (value-less token) fails at `:2067-2069`; `layerrulev2` errors (`:449-453`).
- Lua: `hl.layer_rule{ name?, enabled?, match = { namespace = "…" }, <effects> }` (`LuaBindingsConfigRules.cpp:1278-1390`, registered `:1398`); match values must be string or bool (`:1315-1323`; note: numbers *not* accepted here unlike window_rule); returns `HL.LayerRule` (`hl.meta.lua:674-677`). Stub `HL.LayerRuleSpec` (`hl.meta.lua:555-569`).
- Only match prop that has effect: `namespace` regex (`LayerRule.cpp:107-110`; wiki current `:384-386`).

| legacy effect (v0.56.2 name) | legacy value (`LayerRule.cpp:48-86`) | Lua field & type (`LuaBindingsConfigRules.cpp:182-193`; stub `hl.meta.lua:555-569`) | notes |
|---|---|---|---|
| `no_anim` | truthy | `no_anim` bool | old `noanim` — not in table |
| `blur` | truthy | `blur` bool | |
| `blur_popups` | truthy | `blur_popups` bool | old `blurpopups` |
| `ignore_alpha` | float clamped 0..1 (`:78-83`) | `ignore_alpha` number 0..1 | old `ignorealpha`; **`ignorezero` has no entry in v0.56.2** (`LayerRuleEffectContainer.cpp:12-25`) — use `ignore_alpha = 0` |
| `dim_around` | truthy | `dim_around` bool | |
| `xray` | truthy | `xray` bool | 0.54 wiki still says `unset` for default (`:348`) — no `unset` handling in `LayerRule.cpp:59-64`; just omit |
| `animation` | string style (`:84`) | `animation` string | |
| `order` | int (`:66-71`) | `order` integer | |
| `above_lock` | int clamped 0..2 (`:40-46`) | `above_lock` integer 0..2 | old `abovelock` |
| `no_screen_share` | truthy | `no_screen_share` bool | |
| `unset` (old) | — | — | not found in v0.56.2 |

### 2.7 workspace rules → `hl.workspace_rule`

- Legacy: `workspace = WORKSPACE, RULES` (`wiki054md/.../Workspace-Rules.md:38-48`); handler `ConfigManager.cpp:1676-1796`: first comma-field is the workspace ident, resolved by `getWorkspaceIDNameFromString` (`:1681`), rest split on `,` and each rule matched by substring `find("key:")` (`:1712-1786`). Selectors accepted (existing workspaces only): `r[A-B]`, `s[bool]`, `n[bool]|n[s:x]|n[e:x]`, `m[monitor]`, `w[(flags)A-B|X]` flags t/f/g/v/p, `f[-1|0|1|2]`, combinable e.g. `w[tv1]s[false]` (`Workspace-Rules.md:13-36, 71-82` 0.54; same in current `:39-52`). Plain ids: `3`, `name:x`, `special:x` (`:104-111`).
- Lua: `hl.workspace_rule{ workspace = "<same selector string>", enabled?, ...fields }` (`LuaBindingsConfigRules.cpp:623-735`, registered `:1399`); `workspace` required string (`:633-640`); same `getWorkspaceIDNameFromString` (`:648`); `replaceOrAdd` merges into an existing enabled rule with the same workspace string (`:729`; `WorkspaceRuleManager.cpp:25-32`) — same as legacy (`ConfigManager.cpp:1795`). Returns `HL.WorkspaceRule` (`hl.meta.lua:815-818`, `:863`); stub `HL.WorkspaceRuleSpec` (`hl.meta.lua:603-622`).

| legacy rule (`ConfigManager.cpp`) | legacy value | Lua field (`LuaBindingsConfigRules.cpp:197-227`) | type / notes |
|---|---|---|---|
| `monitor:[m]` (`:1748-1749`) | string | `monitor` | string (`desc:` prefix ok) |
| `default:[b]` (`:1750-1752`) | int/bool | `default` | bool |
| `persistent:[b]` (`:1753-1755`) | | `persistent` | bool |
| `gapsin:[x]` (`:1713-1719`) | 1-4 ints CSS style (`ComplexDataTypes.hpp:94-116`) | `gaps_in` | int or `{top,right,bottom,left}` (`LuaConfigCssGap.cpp:18-60`), stub `integer|HL.CssGap` |
| `gapsout:[x]` (`:1720-1726`) | | `gaps_out` | same |
| — | — | `float_gaps` (`:208-209`) | **Lua-only** (no legacy `floatgaps:` in `:1712-1786`) |
| `bordersize:[x]` (`:1727-1730`) | int | `border_size` | integer |
| `border:[b]` (`:1731-1733`) → `m_noBorder = !b` | bool | `no_border` | **inverted**: `border:false` → `no_border = true` |
| `shadow:[b]` (`:1734-1736`) → `m_noShadow = !b` | bool | `no_shadow` | inverted |
| `rounding:[b]` (`:1737-1739`) → `m_noRounding = !b` | bool | `no_rounding` | inverted |
| `decorate:[b]` (`:1740-1742`) | bool | `decorate` | bool, default true |
| `defaultName:[s]` (`:1756-1757`) | string | `default_name` | string |
| `on-created-empty:[c]` (`:1758-1760`) | command string (`[float] firefox`) | `on_created_empty` | string; legacy ran `cleanCmdForWorkspace` on it, Lua stores raw (`:220-221`) |
| `layoutopt:KEY:VAL` (`:1761-1772`) | one option per rule | `layout_opts = { KEY = VAL }` | table; string/bool/number values stringified (`:670-712`); e.g. `layoutopt:orientation:top` → `layout_opts = { orientation = "top" }` (current wiki `Layouts/Master-Layout.md:87`) |
| `layout:[s]` (`:1773-1775`) | string | `layout` | string |
| `animation:[s]` (`:1776-1778`) | string | `animation` | string (current wiki `:73`, `:86`) |
| — | — | `no_wobble` (current wiki `:66`) | **not in v0.56.2 field table** (`:197-227`) — wiki ahead of binary |

### 2.8 `monitor` → `hl.monitor` / `HL.MonitorSpec`

**Legacy line grammar (`ConfigManager.cpp:1293-1387`, parser `src/config/shared/monitor/Parser.cpp`)**

`monitor = NAME, MODE, POSITION, SCALE[, KEY, VALUE]...` (`wiki054md/.../Monitors.md:10-17`), or the short forms `monitor = NAME, disable|disabled` (`:1299-1301`), `monitor = NAME, transform, N` (`:1302-1316`, updates an existing rule's transform in place), `monitor = NAME, addreserved, TOP, BOTTOM, LEFT, RIGHT` (`:1317-1332`; wiki `:179`).
- NAME: connector (`DP-1`), `desc:<description without (port)>` (wiki `:128-148`), or empty = fallback/catch-all rule (wiki `:91-92`, `:121`).
- MODE (`Parser.cpp:110-143`): empty or `pref*` → preferred; `highrr` → (-1,-1); `highres` → (-1,-2); `maxwidth` → (-1,-3); `modeline <clock> <h...> <v...> [+hsync -vsync Interlace]` (`:14-90`); else `WxH[@Hz]` split on `x` and `@` (`:124-134`) — lowercase `x` only, `@` optional (float Hz).
- POSITION (`Parser.cpp:145-191`): empty/`auto`/`auto-right` (right), `auto-left`, `auto-up`, `auto-down`, `auto-center-right|left|up|down` (`:149-164`); else `XxY` ints (`:175-182`), negatives allowed (wiki `:48-73`).
- SCALE (`Parser.cpp:193-211`): empty/`auto*` → -1 (auto); else float ≥ 0.25.
- Trailing pairs (`ConfigManager.cpp:1341-1377`): `mirror NAME`, `bitdepth 8|10` (`Parser.cpp:229-232`, only `"10"` enables 10-bit), `cm PRESET` (`:234-242`; presets `auto srgb dcip3 dp3 adobe wide edid hdr hdredid` per wiki `:233-243`), `sdrsaturation F`, `sdrbrightness F` (`:244-262`), `transform 0-7` (`:213-227`), `vrr N` (`:264-273`, negative → unset), `icc PATH` (`:275-282`). Anything else → `invalid syntax at "..."` (`ConfigManager.cpp:1373-1375`). Note `sdr_eotf`, `addreserved`, `supports_*`, `*_luminance` are **not** accepted as trailing pairs in the one-line form; they exist only in the `monitorv2 {}` block (`ConfigManager.cpp:568-588`, `:788-880`; wiki `:282-310`).
- `monitorv2 { output=…; mode=…; position=…; scale=…; addreserved = T, B, L, R; mirror; bitdepth; cm; sdr_eotf (0/1/2 or name, `:822-833`); sdrbrightness; sdrsaturation; vrr; transform; supports_wide_color; supports_hdr; sdr_min_luminance; sdr_max_luminance; min_luminance; max_luminance; max_avg_luminance; icc }` (`:568-588`, `:788-880`).

**Lua (`LuaBindingsConfigRules.cpp:1103-1160`, field table `:80-176`; stub `HL.MonitorSpec` `hl.meta.lua:571-595`; `hl.monitor` registered `:1396`, returns nil `hl.meta.lua:856`)**: `hl.monitor{ output = "…", ...}`; `output` required string (`:1112-1119`); if a rule with the same output already exists, the new call **starts from a copy of it** and overrides only the given fields (`:1122-1125`), then `monitorRuleMgr()->add` replaces by name (`MonitorRuleManager.cpp:37-42`). Legacy `handleMonitor` always builds a fresh rule (`:1297`) except the `transform`/`addreserved` shorthands.

| legacy token | Lua field | type / mapping | notes |
|---|---|---|---|
| NAME (`DP-1`, `desc:…`, empty) | `output` | string (`:1112-1119`; wiki current `:105-106`, `:156-159`) | `""` fallback still valid (`current wiki :135`) |
| MODE `1920x1080@144` / `preferred` / `highres` / `highrr` / `maxwidth` / `modeline …` | `mode` | string → same `parseMode` (`:82-83`) | default `"preferred"`; **capital `X` not accepted** (`Parser.cpp:124`) |
| POSITION `0x0` / `auto` / `auto-*` / `auto-center-*` | `position` | string → same `parsePosition` (`:84-85`) | default `"auto"`; all `auto-*` variants preserved |
| SCALE `1`, `1.5`, `auto` | `scale` | string or number → `parseScale` (`:86-87`; stub `string|number`) | numbers OK because `lua_isstring` accepts numbers (`LuaConfigString.cpp:14`); `-1` legacy shorthand → parseScale rejects `<0.25` (`Parser.cpp:203`) → use `"auto"` |
| `disable` / `disabled` (2nd field) | `disabled = true` | bool (`:98-102`) | |
| `transform, N` (trailing or shorthand) | `transform` | **integer 0..7** (`:103-107`) — still ints, no enum names | |
| `mirror, NAME` | `mirror` | string (`:108-112`) | |
| `bitdepth, 10` | `bitdepth` | integer, `==10` → 10-bit else 8 (`:113-117`) | |
| `cm, X` | `cm` | string, same `parseCM` (`:118-119`) | Lua default `"srgb"` vs monitorv2 default `"auto"` (`ConfigManager.cpp:576`) — one-line legacy had no default set (rule default) |
| monitorv2 `sdr_eotf = 0/1/2|name` | `sdr_eotf` | string only via `NTransferFunction::fromString` (`:120-124`) | **numeric 0/1/2 remap is legacy-only** (`ConfigManager.cpp:822-833`); use `default`/`srgb`/`gamma22` (current wiki `:222`) |
| `sdrbrightness, F` | `sdrbrightness` | number (`:125-129`) | |
| `sdrsaturation, F` | `sdrsaturation` | number (`:130-134`) | |
| `vrr, N` | `vrr` | integer -1..3, `<0` = unset (`:135-140`) | Lua default -1 (unset) vs monitorv2 INT 0 (`ConfigManager.cpp:580`) |
| `icc, PATH` | `icc` | string (`:141-142`) | |
| `addreserved, T, B, L, R` (shorthand) / monitorv2 `addreserved = T,B,L,R` | `reserved` **or** `reserved_area` | int (all sides) or `{top,right,bottom,left}` css-gap (`:88-97`; `LuaConfigCssGap.cpp:18-60`) | **argument order changes**: legacy positional T,B,L,R → named keys; there is no separate keyword |
| monitorv2 `supports_wide_color`, `supports_hdr` | same names | int -1..1 (`:143-152`) | |
| monitorv2 `sdr_min_luminance`, `sdr_max_luminance`, `min_luminance`, `max_luminance`, `max_avg_luminance` | same names | float/int/float/int/int (`:153-176`) | |

Lossy / gotchas: none of the legacy tokens lack a Lua field; the differences are (a) `addreserved` positional order → css-gap keys, (b) `sdr_eotf` numeric codes, (c) `-1` scale shorthand, (d) `hl.monitor` merges into an existing same-output rule whereas legacy `monitor =` (non-shorthand) replaces wholesale, (e) transform stays a 0-7 integer (no enum names in `hl.meta.lua:593`), (f) `Multi-GPU.md`/`Tearing.md` contain no monitor-line grammar beyond `immediate` window rule / env vars (checked, nothing to map).

### 2.9 Evaluation order / last-wins

- Legacy: named (special-category) window rules are registered first, then anonymous keyword rules, in config order (`ConfigManager.cpp:945-963`: `reloadRules` iterates `listKeysForSpecialCategory("windowrule")`, then `layerrule`, then `m_keywordRules`). Wiki: "rules are evaluated top to bottom, but all named rules get evaluated first, then all anonymous ones"; last matching rule wins for an effect (`wiki054md/.../Window-Rules.md:6-8`, `:259-289`).
- Lua: `hl.window_rule`/`hl.layer_rule` call `ruleEngine()->registerRule` immediately in call order (`LuaBindingsConfigRules.cpp:1195`, `:1305`); the engine just appends (`src/desktop/rule/Engine.cpp:14-16`). Re-calling with an existing `name` updates the same rule object in place, keeping its original position (`:1189-1191`). Whole rule set is cleared on reload (`src/config/lua/ConfigManager.cpp:702-707`). No `priority` field exists in `hlWindowRule` (`:1162-1276`) or the stub (`hl.meta.lua:597-601`). The current wiki keeps the same "last match takes precedence" and the "named rules take precedence over anonymous ones" claim (`wiki/.../Basics/Window-Rules.md:10-12`, `:316-340`) — the latter is **not** reflected in the Lua registration code (pure call order), so treat it as wiki carry-over. **Importer implication:** to preserve legacy precedence, emit all named (block) rules first, then anonymous `windowrule =` rules, each group in source order.
- Workspace rules: both paths use `replaceOrAdd` → merge into an existing rule with the same workspace string (`ConfigManager.cpp:1795`; `LuaBindingsConfigRules.cpp:729`; `WorkspaceRuleManager.cpp:25-32`). Monitor rules: `add` replaces by name (`MonitorRuleManager.cpp:37-42`) — plus the Lua copy-then-override step noted above.
### 2.10 Everything else: animation/bezier, gesture, device, permission, plugin, env, exec*, layout, source, plain values

Citation shorthand (all v0.56.2 unless noted):
- **LEG** = `src/config/legacy/ConfigManager.cpp`
- **CR** = `src/config/lua/bindings/LuaBindingsConfigRules.cpp`; **TL** = `…/LuaBindingsToplevel.cpp`; **INT** = `…/LuaBindingsInternal.cpp`
- **LCM** = `src/config/lua/ConfigManager.cpp`; **EVT** = `src/config/lua/LuaEventHandler.cpp`
- **T/** = `src/config/lua/types/`; **PU** = `src/config/shared/parserUtils/ParserUtils.cpp`; **CV** = `src/config/values/ConfigValues.cpp`; **CDT** = `src/config/shared/complex/ComplexDataTypes.hpp`; **AT** = `src/config/shared/animation/AnimationTree.cpp`; **EX** = `src/config/supplementary/executor/Executor.cpp`
- **STUB** = `/usr/share/hypr/stubs/hl.meta.lua`; **EX.LUA** = `example/hyprland.lua` (v0.56.2)
- **W54/** = wiki @0.54.0 `content/Configuring/...`; **W56/** = current wiki `content/Configuring/...`

Registered legacy keyword handlers: `exec, execr, exec-once, execr-once, exec-shutdown, monitor, bind, unbind, workspace, windowrule, layerrule, bezier, animation, source, submap, plugin, permission, gesture (flags on), env (flags on)` — LEG:601-619; special categories `device` (LEG:518), `plugin` (LEG:626). Registered Lua fns: `hl.config, get_config, device, monitor, window_rule, layer_rule, workspace_rule, env, permission, plugin.load, gesture, curve, animation` — CR:1392-1409; `hl.on, bind, define_submap, timer, dispatch, version, get_loaded_plugins, exec_cmd, …` — TL:536-552. There is **no `hl.set`**; ordinary options go through `hl.config({...})` (CR:969-1017, STUB:824).

#### 2.10.1 animation / bezier → hl.animation / hl.curve

Legacy grammar (comma list, `CVarList`):
- `bezier = NAME, X0, Y0, X1, Y1` — exactly 4 numeric points, "too many arguments" if a 6th arg (LEG:1389-1423; W54/Animations.md:79).
- `animation = NAME, ONOFF, SPEED, CURVE[, STYLE]` — NAME must exist in animation tree (LEG:1432-1436), ONOFF parsed by `ParserUtils::parseInt` so `1/0/true/false/on/off/yes/no` accepted (LEG:1439; PU:134-150), off ⇒ node set `enabled=0, speed=1, curve "default"` (LEG:1445-1448), SPEED must be number >0 (LEG:1451-1462), CURVE must be an existing bezier (LEG:1464-1467), STYLE validated via `styleValidInConfigVar` (LEG:1471-1476); W54/Animations.md:11-21 (speed in ds, 1ds=100ms).

Lua:
- `hl.curve(NAME, { type = "bezier", points = { {X0,Y0}, {X1,Y1} } })` — CR:278-341; each coord parsed as float **clamped range -1..2** (CR:323) — legacy accepts any float → lossy for out-of-range beziers. `hl.curve(NAME, { type = "spring", mass=, stiffness=, dampening= })` all numbers ≥0.5 (CR:342-386) — Lua-only. W56/Advanced and Cool/Animations.md:90,102.
- `hl.animation({ leaf = NAME, enabled = BOOL, speed = FLOAT, bezier = NAME | spring = NAME, style = STR })` — CR:390-475: `leaf` string, must exist (CR:394-403); `enabled` bool or 0/1 (CR:405-410, T/LuaConfigBool.cpp:10-25); if false ⇒ same as legacy off (CR:412-415); `speed` float **0<x≤100** (CR:417-426; legacy unbounded); curve given as `bezier=` (must exist, CR:430-441) **or** `spring=` (CR:442-452, internally `"spring:"+name`); missing ⇒ error "bezier or spring is required" (CR:453-454); `style` optional string, same validator as legacy (CR:456-471). STUB:821 (`animation fun(...)`), EX.LUA:145-161 (`hl.animation({ leaf = "windowsIn", enabled = true, speed = 4.1, spring = "easy", style = "popin 87%" })`).
- Wiki W56 Animations.md:16,121,129,136 shows `curve = "default"` — **wrong per source**; the binding only reads `bezier`/`spring` (CR:430-454). Use `bezier=`.

| Legacy | Lua | Lossy? |
|---|---|---|
| `bezier = n, a,b,c,d` (W54/Animations.md:79; LEG:1389) | `hl.curve("n", {type="bezier", points={{a,b},{c,d}}})` (CR:278-341) | coords outside -1..2 rejected (CR:323) |
| `animation = leaf, 1, spd, curve[, style]` (LEG:1426) | `hl.animation({leaf="leaf", enabled=true, speed=spd, bezier="curve", style="style"})` (CR:390) | speed >100 rejected (CR:417); ONOFF words → `true/false` (0/1 numbers still ok, T/LuaConfigBool.cpp:11-14) |
| `animation = leaf, 0` | `hl.animation({leaf="leaf", enabled=false})` (CR:412-415) | none |
| built-in curve `default` (AT:62 sets global to "default") | same name usable in `bezier="default"` (EX.LUA:145) | none; user-defined `bezier = default, …` overrides it in both |
| — | springs (`type="spring"`, `spring=` field) | Lua-only |
| `animations:enabled`, `animations:workspace_wraparound` (CV:264-265; W54/Variables.md:164-165) | `hl.config({animations={enabled=, workspace_wraparound=}})` (STUB:1425-1427; W56/Basics/Variables.md:198-203) | none |
| `animations:first_launch_animation` | **not found** in CV, LEG, STUB or either wiki | — |

Animation tree leaves (identical for both since shared, AT:15-59): `global, windows{In,Out,Move}, layers{In,Out}, fade{In,Out,Switch,Shadow,Glow,Dim,Layers{In,Out},Popups{In,Out},Dpms}, border, borderangle, shadowangle, glowangle, workspaces{In,Out}, specialWorkspace{In,Out}, zoomFactor, monitorAdded`. New vs 0.54 wiki: `fadeGlow`, `shadowangle`, `glowangle` (W56 Animations.md:55,66,67 vs W54 Animations.md:36-67). Styles (W54:38-67 = W56:43-75): windows `slide[ left|right|top|bottom]`, `popin [NN%]`, `gnomed`; layers `slide, popin, fade`; workspaces `slide, slidevert, fade, slidefade [NN%], slidefadevert`; `*angle` `once|loop`. Speed unit ds in both (W54:17, W56:23).

#### 2.10.2 gesture → hl.gesture

Legacy: `gesture[flags] = FINGERS, DIRECTION, [mod:MASK,] [scale:F,] ACTION[, ARGS]` — LEG:1931-2010: fingers 2..9 (LEG:1938-1943), direction via `dirForString` (LEG:1945-1948), flag `p` = bypass inhibitors (LEG:1955-1960; W54/Gestures.md:61-73), `mod:` / `scale:` prefixed tokens in any order (LEG:1962-1978, scale clamped 0.1..10), actions `dispatcher NAME ARGS…`, `workspace`, `resize`, `move`, `special NAME`, `close`, `float [float|tile]`, `fullscreen [maximize]`, `cursorZoom ZOOM [mult]`, `scrollMove`, `unset` (LEG:1980-2007; W54/Gestures.md:48-58).

Lua: `hl.gesture({ fingers=, direction=, action=, mods=, scale=, disable_inhibit=, workspace_name=, mode=, zoom_level= })` — CR:737-967: `fingers` int 2..9 (CR:741-747), `direction` string same `dirForString` (CR:749-756), `action` may be a **Lua function** (CR:809-813) or a table `{start=,update=,finish=}` (CR:814-861, live gestures) or a string (CR:924-946): `workspace, resize, move, special, close, float, fullscreen, cursor_zoom|cursorZoom, scroll_move, unset`; string extras `zoom_level`, `workspace_name`, `mode` (CR:882-886), `mods` string → modmask (CR:890-900), `scale` float 0.1..10 (CR:902-912), `disable_inhibit` bool (CR:914-924). STUB:460-469 (`HL.GestureSpec`), STUB:832; W56/Advanced and Cool/Gestures.md:14-29,49-60,166-175.

| Legacy | Lua | Lossy? |
|---|---|---|
| `gesture = 3, horizontal, workspace` | `hl.gesture({fingers=3, direction="horizontal", action="workspace"})` (W56 Gestures.md:25) | no |
| `mod: ALT` token | `mods = "ALT"` (CR:890) | no |
| `scale: 1.5` token | `scale = 1.5` (CR:902) | no |
| `gesture[p] = …` (`gesturep`) | `disable_inhibit = true` (CR:914) | no |
| `special, NAME` | `action="special", workspace_name="NAME"` (CR:934) | no |
| `float, float\|tile` / `fullscreen, maximize` | `action="float"/"fullscreen", mode="…"` (CR:938-940) | no |
| `cursorZoom, Z[, mult]` | `action="cursor_zoom", zoom_level=Z, mode="mult"` (CR:942; also `"live"` W56:59) | no |
| `scrollMove` | `action="scroll_move"` (CR:944) | no |
| `unset` | `action="unset"` (CR:946) | no |
| `dispatcher, NAME, ARGS` (LEG:1980-1982) | **no `dispatcher` string action** in Lua (CR:924-946); use `action = function() hl.dispatch(hl.dsp.…()) end` (TL:352-375) | lossy: needs dispatcher→`hl.dsp` translation (section 2.2) |
| — | Lua fn / live `{start,update,finish}` (CR:809-861; W56:76-164) | Lua-only |
| directions `swipe,horizontal,vertical,left,right,up,down,pinch,pinchin,pinchout` (W54:32-39) | same (W56:35-42) | no |

#### 2.10.3 device { } → hl.device

Legacy: `device { name = X  key = v … }` special category keyed by `name` (LEG:518); values registered LEG:519-565 (`sensitivity, accel_profile, rotation, kb_file, kb_layout, kb_variant, kb_options, kb_rules, kb_model, repeat_rate, repeat_delay, natural_scroll, tap_button_map, numlock_by_default, resolve_binds_by_sym, disable_while_typing, clickfinger_behavior, middle_button_emulation, tap-to-click, tap-and-drag, drag_lock, left_handed, scroll_method, scroll_button, scroll_button_lock, scroll_points, scroll_factor, transform, output, enabled, region_position, absolute_region_position, region_size, relative_input, active_area_position, active_area_size, flip_x, flip_y, drag_3fg, keybinds, share_states, release_pressed_on_close, tags, eraser_button_mode, eraser_button_override, pressure_range_min, pressure_range_max`); device name = `hyprctl devices` name with spaces→`-` (LEG:1196-1200); hyprctl form `device[NAME]:key` (W54/Keywords.md:118-122); W54/Keywords.md:64-130.

Lua: `hl.device({ name = "…", key = value, … })` — CR:1046-1101: `name` required string (CR:1055-1062), spaces→`-` (CR:1062); every other key must be in `DEVICE_FIELDS` (CR:230-274) else error "unknown field" (CR:1080-1084); values parsed by typed parsers (bool/int-with-range/float/string/vec2). STUB:508-552 (`HL.DeviceSpec`), STUB:827; W56/Advanced and Cool/Devices.md:13-46; EX.LUA:244-247.

| Legacy key | Lua key | Note |
|---|---|---|
| all keys listed above except the four tablet-tool ones | same name (CR:231-273) | — |
| `tap-to-click`, `tap-and-drag` (LEG:537-538) | `tap_to_click`, `tap_and_drag` (CR:249-250) | **renamed** (dash→underscore) |
| `eraser_button_mode`, `eraser_button_override`, `pressure_range_min`, `pressure_range_max` (LEG:562-565) | **absent** from `DEVICE_FIELDS` (CR:230-274) and `HL.DeviceSpec` (STUB:508-552) | lossy: not settable per-device in Lua |
| `sensitivity` float | float, range -1..1 (CR:231) | legacy unranged |
| bool-ish `0/1` | `true/false` or `0/1` (T/LuaConfigBool.cpp:11-25) | `yes/on` words not accepted |
| `region_position = 10 20` (VEC2) | `{10, 20}` or `"10 20"` (T/LuaConfigVec2.cpp:17-52) | — |
| `tags` comma list | `tags = "a,b"` string (CR:273; W56 Devices.md:44-46) | — |

#### 2.10.4 permission → hl.permission

Legacy: `permission = REGEX, TYPE, MODE` (comma split via `CVarList2`) — LEG:1896-1929: types `screencopy, cursorpos, plugin, keyboard|keeb, input-capture` (LEG:1902-1911), modes `ask, allow, deny` (LEG:1913-1918); **only applied on first launch** (LEG:1925-1926). W54/Permissions.md:31-43. Requires `ecosystem:enforce_permissions = true` (W56 Permissions.md:22-23; STUB:1132).

Lua: `hl.permission({ binary = REGEX, type = T, mode = M })` (`target` accepted as alias of `binary`, CR:563-573) **or positional** `hl.permission(REGEX, T, M)` (CR:574-583); same type/mode strings (CR:591-606); same first-launch-only (CR:616-617). STUB:472-476,858; W56/Advanced and Cool/Permissions.md:34-47; EX.LUA:76-78 uses positional.

| Legacy | Lua | Lossy? |
|---|---|---|
| `permission = /usr/bin/grim, screencopy, allow` | `hl.permission("/usr/bin/grim", "screencopy", "allow")` or table form | no (regex containing `,` no longer needs escaping) |
| `keeb` alias | still accepted (CR:600) | no |

#### 2.10.5 plugin

Legacy: `plugin = /abs/path.so` → `m_declaredPlugins` (LEG:1887-1894, handler LEG:616), loaded by `handlePluginLoads` (LEG:1203-1214). `plugin { NAME { key = v } }` / `plugin:NAME:key = v`: special category `plugin` with `ignoreMissing` (LEG:626); keys exist only after a plugin registers them via `addPluginConfigVar("plugin:…")` (LEG:1216-1224); W54/Plugins/Development/Advanced.md:126,142.

Lua:
- `hl.plugin.load(PATH)` → `m_registeredPlugins` (CR:537-554; registered as `hl.plugin.load` CR:1403-1405; LCM:1124 loads them). STUB:949-951 (`HL.PluginNamespace: load fun(...), [string] any`). **Not documented in W56 wiki** (grep found only `hyprctl plugin load` W56/Plugins/Using-Plugins.md:85) — source/stub only.
- Plugin config: plugin values registered via `registerPluginValue` become `m_configValues` keys with `:`→`.` and `-`→`_` (LCM:1136-1141,1148-1163) so `plugin:NAME:key` → `hl.config({ plugin = { NAME = { key = v } } })` (walk CR:978-1012); W56/Plugins/Using-Plugins.md:99-114 (guard with `if hl.plugin.NAME ~= nil` because unknown keys error, CR:1000-1002).
- Plugin-exposed Lua functions: `hl.plugin.NAMESPACE.fn(...)` (LCM:1266-1300; W56 Using-Plugins.md:102).
- `hl.get_loaded_plugins()` (TL:294-319; STUB:843).

| Legacy | Lua | Lossy? |
|---|---|---|
| `plugin = path` | `hl.plugin.load("path")` | no |
| `plugin:NAME:key = v` / `plugin { NAME { key = v } }` | `hl.config({plugin={NAME={key=v}}})` | key dashes → underscores (LCM:1139); **errors if plugin not yet loaded** (CR:1000-1002) whereas legacy silently ignored unknown plugin keys (`ignoreMissing`, hyprlang `config.cpp:386-387`) — importer should wrap in `if hl.plugin.NAME ~= nil then … end` |
| `exec-once = hyprpm reload` (W54/Plugins/Using-Plugins.md:63) | `hl.on("hyprland.start", function() hl.exec_cmd("hyprpm reload") end)` (W56 Using-Plugins.md:67; Autostart.md:13-17) | no |

#### 2.10.6 env / envd

Legacy `env = NAME,VALUE` — LEG:1857-1885: `CVarList(value, 2)` ⇒ split at **first comma only**, value keeps further commas (LEG:1858); empty name error (LEG:1860-1861); after first launch, skipped if unchanged (LEG:1863-1868); `setenv(…,1)` (LEG:1870); `envd` (`d` flag, handler flags on LEG:619) additionally runs `systemctl --user import-environment NAME && … dbus-update-activation-environment --systemd NAME` via `handleRawExec` (LEG:1872-1882) — which at first launch is queued as exec-once, otherwise spawned immediately (LEG:1249-1256). Values are raw strings, no quotes (W54/Keywords.md:143-177; W54/Environment-variables.md:17-40).

Lua `hl.env(NAME, VALUE[, DBUS])` — CR:478-535: two string args (CR:481-500), same unchanged-skip after first launch (CR:503-507), `setenv` (CR:509), optional 3rd bool = dbus export (CR:511-533; command identical, name single-quoted; queued at first launch else `spawnRaw`). STUB:829 (`env fun(...)`); W56/Advanced and Cool/Environment-variables.md:21-30 (documents 2-arg form + `os.getenv()` for expansion; **3rd dbus arg is source-only**); EX.LUA:58-59.

| Legacy | Lua | Lossy? |
|---|---|---|
| `env = K,V` | `hl.env("K", "V")` | no; V must be a Lua string (quote it; commas fine, e.g. `GDK_BACKEND,wayland,x11,*` → `hl.env("GDK_BACKEND", "wayland,x11,*")`) |
| `envd = K,V` | `hl.env("K", "V", true)` (CR:511-533) | no (undocumented in wiki) |
| `$VAR` in value (hyprlang) | `os.getenv("VAR")` (W56 Env.md:29-30) | translation needed |

#### 2.10.7 exec-once / exec / execr / execr-once / exec-shutdown → hl.on + hl.exec_cmd

Legacy semantics (LEG:1249-1290; W54/Keywords.md:24-40):
- `exec = cmd`: first launch → queued `addExecOnce({cmd, withRules=true})`, later reloads → `spawn(cmd)` i.e. runs on **every reload** (LEG:1259-1267).
- `execr = cmd`: same but `withRules=false` (LEG:1249-1257).
- `exec-once` / `execr-once`: only first launch, queued (LEG:1269-1281).
- `exec-shutdown = cmd`: queued `addExecShutdown` (LEG:1283-1290), spawned on `exit` event (EX:58-62).
- "withRules" means the `[rules…] cmd` prefix is parsed (EX:99-121, `buildFromExecString`); **both** paths end in `execl("/bin/sh","-c",…)` (EX:203) — `execr` only skips `[…]` parsing.
- Queued exec-once commands run on the `start` event after dbus env import (EX:24-45).

Lua:
- Events (EVT:165-166, EVT:266-303; STUB:5-35): `"hyprland.start"`, `"hyprland.shutdown"`, `"config.reloaded"`, `"config.props_refreshed"`, plus window/layer/monitor/workspace/keybinds/screenshare/input events. `hl.on(EVENT, fn)` → `HL.EventSubscription` (TL:377-402; STUB:857). Unknown event ⇒ error listing known events (TL:389-399).
- `hl.exec_cmd(cmd[, rules_table])` (TL:321-335; STUB:830): spawns **immediately** (`executor()->spawn(SExecRequest{cmd, !rule, rule})`, TL:332); with no rules table `withRules=true` ⇒ legacy `[rules] cmd` prefix still parsed (Executor.hpp:14-22, EX:86-90,99-121); with a table (`{float=true, move={0,0}}`) rules built from window-rule effect descs (INT:509-573; W56/Basics/Dispatchers.md:74,251-256).
- Whole config is re-executed in a fresh Lua state on every reload (LCM:634-723 `reinitLuaState`, events cleared LCM:711).
- Raw (no `[…]` parsing) only exists as dispatcher `hl.dsp.exec_raw(cmd)` (`LuaBindingsDispatchers.cpp:252,1400`, STUB:874) → `hl.dispatch(hl.dsp.exec_raw("…"))`; note W56 Dispatchers.md:75 claims exec_raw skips `sh -c`, but `spawnRaw`→`spawnRawProc` still uses `/bin/sh -c` (EX:93-94,203).

| Legacy | Lua | Lossy? |
|---|---|---|
| `exec-once = cmd` | `hl.on("hyprland.start", function() hl.exec_cmd("cmd") end)` (W56/Basics/Autostart.md:10-20; EX.LUA:45-49) | no |
| `execr-once = cmd` | `hl.on("hyprland.start", function() hl.dispatch(hl.dsp.exec_raw("cmd")) end)` (STUB:874) or `hl.exec_cmd` if cmd doesn't start with `[` | minor |
| `exec = cmd` (each reload) | top-level `hl.exec_cmd("cmd")` (re-run each reload, LCM:634-723) | **timing**: legacy first-launch exec is deferred to `start` event (LEG:1260-1262); Lua spawns during config parse (TL:332). Faithful form: `hl.exec_cmd` at top level *and* nothing else — accept the timing shift, or guard with a `hl.on("hyprland.start")` on first load |
| `execr = cmd` | top-level `hl.dispatch(hl.dsp.exec_raw("cmd"))` | as above |
| `exec-shutdown = cmd` | `hl.on("hyprland.shutdown", function() hl.exec_cmd("cmd") end)` (EVT:166; Autostart.md:22) | no |
| `exec = [float;size 50% 50%] cmd` | `hl.exec_cmd("[float;size 50% 50%] cmd")` still parsed (EX:106-121) or `hl.exec_cmd("cmd", {float=true, size=…})` (INT:509-573) | rule-effect names must be Lua-rule keys (`floating`→`float` alias INT:530-531) |
| `config.reloaded` hook | `hl.on("config.reloaded", …)` (EVT:151) | Lua-only |

#### 2.10.8 layout keyword & general:layout

- There is **no `layout` keyword** in the legacy handler table (LEG:601-619); layout is the variable `general:layout` (CV:179: `[dwindle/master/scrolling/monocle/lua:<name>]`, W54/Variables.md:58) → `hl.config({ general = { layout = "dwindle" } })` (STUB:1344; W56/Basics/Variables.md:77; EX.LUA:104).
- Per-layout categories `dwindle:*`, `master:*`, `scrolling:*` (CV:662+, STUB:1729-1767) → `hl.config({ dwindle = {...} })`, `master = {...}`, `scrolling = {...}` (EX.LUA:182-198). `monocle` has **no options** in CV (single hit is the layout name) and no `HL.ConfigOpt.Monocle` (STUB:1314-1335); W54/Monocle-Layout.md has only dispatchers. `layout:*` category (`single_window_aspect_ratio[_tolerance]`, CV:653; STUB:1725-1727) exists in both.
- `dwindle:force_split` has an OptionMap ⇒ Lua also accepts strings `"follow_mouse"|"left"|"right"` (CV:662; T/LuaConfigInt.cpp:28-47).
- Custom Lua layouts: `hl.layout.register(name, { recalculate = fn, layout_msg = fn? })` then `general.layout = "lua:name"` (STUB:432-434,940-941; W56/Layouts/Custom-Layouts.md:11-30). Lua-only.

#### 2.10.9 source = → require

Legacy `source = PATH` — LEG:1802-1855 (see §1.3).

Lua:
- `package.path` is prefixed with `<configdir>/?.lua;<configdir>/?/init.lua` (LCM:534-546) so `require("awesomeconf.keybinds")` / `require("awesomeconf/keybinds")` resolve under the hyprland.lua dir (W56/Start.md:44-56).
- `require` is replaced by a safe wrapper (LCM:548-554, 312-360): explicit paths starting `/`, `./`, `../`, `~/` (LCM:77-79) are resolved **relative to the main config path** (LCM:85-90, 92-111; tries `X`, `X.lua`, `X/init.lua`), wildcards `*?[` supported via `requireWildcard` (LCM:81-83,215,319-320; W56/Start.md:58-62); errors inside a required file are collected as config errors and an empty table returned (LCM:346-352; W56/Start.md:105-107) except "module not found" which throws (LCM:341-343; W56/Start.md:113-131). Original available as `__require` (LCM:550; W56/Start.md:132-133). Required files are tracked for reload-watching (LCM:558-599).
- No `hl.source` — **not found** in CR/TL/STUB.

| Legacy | Lua | Lossy? |
|---|---|---|
| `source = ~/.config/hypr/x.conf` | `require("x")` (module name) or `require("~/.config/hypr/x.lua")` | file must be Lua — the importer has to convert every sourced file too, and it must **rename** (`.conf` → `.lua`) |
| `source = ~/.config/hypr/custom/*` | `require("./custom/*")` (LCM:319-320; W56/Start.md:60) | relative base differs (main config dir vs. current file); glob order both unsorted-glob |
| linear inline parse sharing `$vars` | linear `require` execution; each file its own error scope (W56/Start.md:105-107); **Lua locals do not cross files** — legacy `$var` defined in one file and used in a sourced file must become a global or a returned table | semantics differ |
| `source` of a file outside `~/.config/hypr` (e.g. `~/repos/…/x.conf`) | absolute or `~/` path in `require` (LCM:77-79) | fine |

#### 2.10.10 Plain variables `cat:sub:key = value` → `hl.config({...})`

API: `hl.config(TABLE)` walks nested tables, joining keys with `.` and looking up `m_configValues` (CR:978-1012); unknown key ⇒ error `unknown config key` (CR:1000-1002); dynamic (post-load) calls schedule prop refresh (CR:1006-1007). Key names are `luaConfigValueName(name)`: `:`→`.` and `-`→`_` (LCM:1136-1141), e.g. `input:touchpad:tap-to-click` (CV:331) → `input.touchpad.tap_to_click` (STUB:298), `input-capture:capture_modifiers` (CV:722) → `input_capture.capture_modifiers` (STUB:301). Read back with `hl.get_config("general.border_size")` (colon form also accepted, CR:1020-1044; STUB:837). Multiple `hl.config` calls merge (W56/Basics/Variables.md:18-37). Full key list: STUB:38-392 (`HL.ConfigKey`) and typed table `HL.ConfigOpt` STUB:1314-1777. Top-level `autogenerated = true` is a real key (LCM:414; `DefaultConfig.hpp:11`).

Keys with dots (`general:col.active_border`, CV:174): because the walker joins with `.`, both `general = { col = { active_border = … } }` (STUB:1343,1355-1359; EX.LUA:93-96) **and** `general = { ["col.active_border"] = … }` produce `general.col.active_border` (CR:986-996). No rename.

Value conversions (Lua parsers under T/):

| Legacy form | Legacy parser | Lua accepted forms | Cite | Lossy? |
|---|---|---|---|---|
| bool `true/false/yes/no/on/off/1/0` (W54/Variables.md:20) | `parseInt` prefix match (PU:134-150); hyprlang INT prefix rule (§1.8) | `true`/`false` or numbers `0`/`1` only | T/LuaConfigBool.cpp:10-27 | word forms `yes/on` (and sloppy `yes, please :)`) must be normalised to `true`/`false` |
| int `5`, `0x..` | hyprlang INT | Lua integer; bool → 0/1; strings only for OptionMap ints (`"left"`) | T/LuaConfigInt.cpp:12-63 | numeric strings `"5"` rejected |
| float `0.5` | FLOAT | number or integer; bool→0/1 | T/LuaConfigFloat.cpp:12-50 | — |
| str | STRING | Lua string (numbers coerce) | T/LuaConfigString.cpp:13-29 | — |
| vec2 `10 20` (W54:23) | hyprlang VEC2 | `{10, 20}` or `"10 20"` / `"10,20"` | T/LuaConfigVec2.cpp:17-52; STUB:396 | — |
| CSS gap `5,10,15,20` (1–4 comma values, CSS shorthand; W54:50-52) | `CCssGapData::parseGapData` (LEG:167-183; CDT:94-118) | integer, or `{top=,right=,bottom=,left=}` (missing → 0) | T/LuaConfigCssGap.cpp:18-68; STUB:397 | 2/3-value shorthand must be expanded to 4 explicit fields |
| color `rgba(2233ee88)`, `rgba(34,51,238,0.53)`, `rgb(2233ee)`, `rgb(r,g,b)`, `0xAARRGGBB`, `#rgb/#rrggbb/#rrggbbaa`, decimal int | `ParserUtils::parseColor` (PU:23-131) | **string** in any of those forms; a Lua integer literal (`0xee1a1a1a`) also works because `lua_isstring` coerces number→decimal string and parseColor accepts pure numbers (T/LuaConfigColor.cpp:26-37; PU:126-129; EX.LUA:113) | W56 Variables.md:53-60 | no `{r,g,b,a}` table |
| gradient `rgba(..) rgba(..) 45deg` (space list, ≤10 colors, angle token ends list, W54:26) | LEG:103-160 (`deg` → radians LEG:118) | single color string, or `{ colors = {"rgba(..)", …}, angle = 45 }` (angle degrees, number) | T/LuaConfigGradient.cpp:21-86; STUB:398; EX.LUA:94 | Lua has no 10-color cap (LEG:129 vs none); `-45deg` → `angle=-45`; a single-colour value can stay a plain string |
| font_weight `bold` / `700` (W54:27) | `parseWeight` (LEG:191-206; CDT:158-175) | integer ≥0 or name string (case-insens.) | T/LuaConfigFontWeight.cpp:16-45; STUB:50 | legacy clamps invalid to 400 (CDT:171-173); Lua accepts any int ≥0 |
| expression vec2 (e.g. `size 50% 50%` rule values) | Math::parseExpressionVec2 | string or `{x, y}` of numbers/strings | T/LuaConfigExpressionVec2.cpp:26-58 | — |
| `$VAR` substitution (hyprlang) | hyprlang | Lua locals / `..` concat | — | translate |

Categories present in both (STUB:1314-1335 = `HL.ConfigOpt`; W54/Variables.md sections): `general, decoration, animations, input, gestures, group, misc, binds, xwayland, opengl, render, cursor, ecosystem, debug, layout, dwindle, master, scrolling, experimental, input_capture(-capture), quirks`.

#### 2.10.11 Renamed / removed / added options and other importer notes

Renamed / removed / added between W54 Variables.md and W56 Basics/Variables.md (diff of table rows; verified against CV where noted):
- `input:touchpad:tap-to-click`, `tap-and-drag` (W54:257,259; legacy name unchanged CV:331,335) → Lua keys `input.touchpad.tap_to_click` / `tap_and_drag` (W56:296,298; STUB:298; LCM:1139).
- `misc:vfr` (W54:403) → `debug:vfr` (W56:645; CV:646). Not under misc in 0.56.2.
- `misc:disable_hyprland_qtutils_check` (W54:428) → `misc:disable_hyprland_guiutils_check` (W56:494; CV:512).
- Removed (0.54 only): `debug:watchdog_timeout` (W54:570), `render:cm_fs_passthrough` (W54:493), `decoration:shadow:ignore_window` (W54:154), `dwindle:pseudotile` (v0.55.0 release notes). None present in CV → `hl.config` would raise `unknown config key`; importer must drop them with a comment.
- Added in W56 (not in W54): `binds:drag_center_window`, `binds:window_direction_monitor_fallback` (W56:534), `cursor:warp_on_monitor_change` (W56:598), `input:follow_mouse_shrink` (W56:234), `decoration:glow:*` (W56:161-172; STUB:1042-1046), `decoration:motion_blur:*` (W56:174-181), `experimental:wp_cm_1_2` (W56:671), `input-capture:*` (W56:673-680; CV:722-723), `quirks:prefer_hdr, skip_non_kms_dmabuf_formats` (W56:622-629; STUB:360-361), `misc:bell_sound, float_force_onscreen, new_float_force_onscreen, initial_workspace_token_timeout, screencopy_force_8b, session_lock_blur`, `render:commit_timing_enabled, fp16_sdr_tf, icc_vcgt_enabled, keep_unmodified_copy, non_shader_cm_interop, not_shown_fifo_lock, use_fp16, use_shader_blur_blend`, `debug:ds_handle_same_buffer[_fifo], fifo_pending_workaround, invalidate_fp16, log_damage, render_solitary_wo_damage`, `gestures:scrolling:move_snap_cursor, move_snap_to_grid`, `group:groupbar:disable_when_only, middle_click_close`, `input:tablettool:eraser_button_mode/override, pressure_range_min/max`.
- W56 documents `decoration.wobble.*` (W56:183-196) but it is **absent** from CV and STUB at v0.56.2 (grep) → wiki is ahead of 0.56.2; do not emit.
- `misc:disable_hyprland_logo` exists in both (STUB:329,1248; EX.LUA:208).
- W54 type `MOD` (W54:24) is not a config value type in Lua; mods appear only as strings in `hl.gesture{mods=}` / bind key strings.
- Legacy handlers `windowrulev2`/`layerrulev2` exist only to emit errors (LEG:622-623).
- `hl.on` known event set also grows with plugin custom events (EVT:300-302).
- Legacy hyprctl `keyword device[NAME]:key` (W54/Keywords.md:121) has no direct Lua keyword form; use `hl.device({name=…, key=…})` at runtime (schedules `REFRESH_INPUT_DEVICES`, CR:1099).
- Everything set by `hl.*` at load is wiped and re-declared on reload (LCM:698-723): gestures (`clearGestures`), device configs, plugin list, event handlers, timers, Lua layouts.
- **Unknown keys are fatal-ish in Lua, silent-ish in legacy**: hyprlang reports "config option does not exist" but keeps going (§1.9); `hl.config` raises `unknown config key` (CR:1000-1002) — same outcome (error collected, rest applied) but the importer should validate keys against `HL.ConfigKey` (STUB:38-392) so it can comment out dead options instead of emitting errors.
### 2.11 Index of lossy / ambiguous cases (what the importer must warn about)

| # | Case | Why | Section |
|---|---|---|---|
| L1 | MODS spelling (`SUPER_SHIFT`, `SUPERSHIFT`, mixed case) | legacy substring match vs Lua strict `+` tokens | 2.1 |
| L2 | Bare numeric keycode `bind = SUPER, 42, …` | must become `code:42` | 2.1 |
| L3 | Unknown xkb key names, `Enter` | silent in legacy, config error in Lua | 2.1 |
| L4 | `binds` multi-key (`&`-joined) | no exact Lua flag; approximate with `A + B` | 2.1 |
| L5 | `bindm` | no `mouse` opt; encode via `hl.dsp.window.drag()/resize()` | 2.1 |
| L6 | `unbind` | string-matched in Lua; requires canonical key strings | 2.1 |
| L7 | `bindd` description with commas | fine in Lua; legacy could not | 2.1 |
| L8 | `resizeactive`/`moveactive`/`*pixel` with `%` | Lua takes numbers + `relative` only | 2.2 |
| L9 | `fullscreenstate -1` and trailing action token | not representable in Lua | 2.2 |
| L10 | `lockgroups unlock`, empty-arg `dpms`/`lockactivegroup` | legacy = off, Lua unknown/empty = toggle → emit `"off"` | 2.2 |
| L11 | `centerwindow 1`, `cyclenext visible/hist`, `workspaceopt`, `setignoregrouplock`, `splitratio` | dropped/deprecated in both | 2.2 |
| L12 | Gesture `dispatcher NAME ARGS` action | Lua has no string form → callback with `hl.dispatch` | 2.10.2 |
| L13 | ≤0.53 `windowrulev2` / `windowrule = float, class:…` | rejected by 0.56.2 legacy engine; importer needs its own ≤0.53 rename table (not in primary sources) | 2.5 |
| L14 | Window-rule bool words `on/yes`, `rounding > 20`, `border_color` active+inactive pair, `opacity` numeric | Lua types stricter (bool/0-1, range 0..20, one gradient per table, string) | 2.5 |
| L15 | Rule precedence named-before-anonymous | Lua is pure call order → emit named first | 2.9 |
| L16 | Workspace `border:/shadow:/rounding:` | inverted to `no_border/no_shadow/no_rounding` | 2.7 |
| L17 | `layerrule ignorezero`, `unset` | absent in 0.56.2 → `ignore_alpha = 0` / omit | 2.6 |
| L18 | Monitor `addreserved T,B,L,R`, `scale -1`, monitorv2 `sdr_eotf 0/1/2`, capital `X` in mode, `hl.monitor` merge-vs-replace | order/enum/format changes | 2.8 |
| L19 | Bezier coords outside -1..2, animation speed > 100 | rejected by Lua | 2.10.1 |
| L20 | Per-device `eraser_button_*`, `pressure_range_*`; `tap-to-click` → `tap_to_click` | absent / renamed | 2.10.3 |
| L21 | `plugin:NAME:key` for a not-yet-loaded plugin | silent in legacy, error in Lua → guard | 2.10.5 |
| L22 | `exec = cmd` first-launch timing | legacy deferred to start event; Lua spawns at parse | 2.10.7 |
| L23 | `source` → `require`: `.conf`→`.lua` rename, relative-path base, `$var` scoping across files, error scoping | structural | 2.10.9, 1.2 |
| L24 | Bool words `yes/no/on/off`, CSS-gap 2/3-value shorthand, numeric strings | Lua parsers stricter | 2.10.10 |
| L25 | Removed options (`misc:vfr`→`debug:vfr`, `dwindle:pseudotile`, `render:cm_fs_passthrough`, `decoration:shadow:ignore_window`, `debug:watchdog_timeout`, `misc:disable_hyprland_qtutils_check`) | `hl.config` errors on unknown keys | 2.10.11 |
| L26 | Wiki-vs-binary drift (`curve=` in wiki animations, `mouse=true`, `no_wobble`, `decoration.wobble.*`, `no_xdg_drags`, `auto_consuming` missing from stub) | validate against `hl.meta.lua` + source, not the wiki | 2.1, 2.5, 2.10 |
| L27 | `# hyprlang if/endif`, `noerror`, `{{ }}` | no Lua analogue; `if` → Lua `if os.getenv(...)`, `noerror` → `pcall`/drop, arithmetic → Lua expressions | 1.4, 1.5 |
| L28 | `$VAR` used in LHS or inside other var names, undefined `$VAR` left literal | Lua needs explicit string building | 1.2 |

## 3. Do official converters exist? Verdict

**No.** As of 2026-08-19 nothing in the hyprwm org converts a hyprlang `hyprland.conf` to `hyprland.lua`. Evidence:

| Where we looked | What is there | Source |
|---|---|---|
| `hyprwm/Hyprland` v0.56.2 source | Both engines are still shipped side by side (`src/config/legacy/**` = hyprlang engine, `src/config/lua/**` = Lua engine); Lua is used when `hyprland.lua` exists, otherwise the legacy engine loads `hyprland.conf` ("config: use lua by default, generate lua if no config present"; "config: find lua paths first (#14335)"). The only "translation" code is `src/config/legacy/DispatcherTranslator.cpp`, a **runtime** shim that maps legacy dispatcher *strings* (`workspace, e+1`, `movefocus, l`, …) onto the shared `Config::Actions` API so `hyprctl dispatch` and legacy binds keep working. It never emits Lua and has no file-conversion mode. | `curl api.github.com/repos/hyprwm/Hyprland/contents/src/config/legacy?ref=v0.56.2` (5 files: ConfigManager.{cpp,hpp}, DefaultConfig.hpp, DispatcherTranslator.{cpp,hpp}); v0.55.0 release notes (https://github.com/hyprwm/Hyprland/releases/tag/v0.55.0) |
| v0.55.0 / v0.56.0 release notes | 0.55.0 (2026-05-09) is the release that made Lua the default: "config/lua: init lua config manager, use lua if available (#13817)", "config: use lua by default, generate lua if no config present", "cmake: install the default example hyprland.lua (#14174)". Breaking-changes section lists only removed/moved options (`dwindle:pseudotile`, `decoration:shadow:ignore_window`, `render:cm_fs_passthrough`, `misc:vfr`→`debug:`). No migration tool or guide is mentioned in either release. | https://github.com/hyprwm/Hyprland/releases/tag/v0.55.0 , /v0.56.0 |
| `hyprwm/hyprland-guiutils` (successor of the archived `hyprland-qtutils`) | Only the QML utility apps (`utils/` dir); `gh search code` for "convert"/"migrate"/"hyprlang" in both repos returns nothing config-related. | `gh api repos/hyprwm/hyprland-guiutils/contents`, `gh search code --repo hyprwm/hyprland-guiutils ...` |
| Wiki (`hyprwm/hyprland-wiki` @ 2e15371) | Every Configuring page carries the banner "Looking for the old hyprlang syntax? Check the 0.54 wiki pages. Since Hyprland 0.55, hyprlang is deprecated in favor of lua." That is the whole migration guidance: the frozen 0.54 wiki (https://wiki.hypr.land/0.54.0/) as the legacy reference plus the new pages. `grep -rniE "migrat|convert" content/Configuring` finds no migration guide. | `content/Configuring/Start.md:6-8` and identical banners in Basics/*.md, Advanced and Cool/*.md |
| Wiki git history | The switch happened in one commit: `e99eac8 2026-04-26 Update Configuration pages for the new lua configuration system (#1434)`, followed by `40e0d8c 2026-05-04 all: more removal of .conf and hyprlang`. Last legacy-syntax revision of `content/Configuring/` is `ecc06f3` (2026-04-20). | `git log` in the wiki clone |

Third-party converters found (`gh search repos`), none official, all young and small:

| Repo | Language / licence | Notes |
|---|---|---|
| https://github.com/pynappo/hyprlang-to-lua.nvim (created 2026-05-02, 3 stars) | Lua, Neovim + tree-sitter-hyprlang; no licence file | Best documented. README says "best-effort … 90% of the way"; explicit limitations: string interpolation with `$vars` only inside keybinds; `source` only if the file is under `hypr/`; **duplicate matchers (`match:class` twice) keep only one**. Headless mode via `nvim --headless`. |
| https://github.com/garrettHensley/hyprvert (2026-05-20, 1 star) | Go, no licence | README: "100% vibe coded… Use at your own risk"; colour formats and local-vs-global variables need manual touch-up. |
| https://github.com/Gravity-I-Pull-You-Down/hyprlangtranspiler (2026-08-10, 3 stars) | Go, **"All Rights Reserved" proprietary** | Cannot be reused. Its README's rule table is nonetheless a useful checklist (`hl.config`, `hl.monitor`, `hl.env`, `require`, `hl.bind` flags `repeating/mouse/locked`, `hl.workspace_rule`, `hl.curve` bezier/spring, `hl.animation{leaf=...}`, dotted keys → nested tables). |
| https://github.com/Ubiufboeuf/hyprlang-to-lua (2026-08-07, 0 stars) | TypeScript, no licence | Not evaluated further. |

**Verdict:** an importer must be written in-house. The pieces to reuse are (a) `DispatcherTranslator.cpp` as the authoritative spec of legacy dispatcher argument grammar, (b) `src/config/legacy/ConfigManager.cpp` handlers as the spec of every keyword's arg splitting, and (c) `hl.meta.lua` + `src/config/lua/bindings/*.cpp` as the spec of the target API. Third-party projects can be consulted for test cases but none is licensed for reuse in a way we should depend on (two have no licence, one is proprietary).
## 4. Test corpus: constructs actually used in `~/.config/hypr/hyprland.conf`

Read-only survey (2026-08-19) of `~/.config/hypr/hyprland.conf` and every file it `source=`s: `keybinds.conf`, `colors.conf`, `hyprland-gui.conf`, `shell-switcher-startup.conf`, `shell-switcher-binds.conf`, `noctalia/noctalia-colors.conf`, `dms/cursor.conf`, `ghibli-theme.conf`, `~/repos/forest-shell/integration/hyprland/forest-binds.conf`, `forest-rules.conf`. (`dms/colors.conf`, `dms/layout.conf`, `dms/windowrules.conf`, `wallust/wallust-hyprland.conf` exist but are **not** sourced; two `source` lines for `monitor.conf`/`input.conf` are commented out.) Running Hyprland is 0.56.2 with **no** `hyprland.lua`, so the legacy engine is in use.

Keyword tally over the sourced set (non-comment lines): `bind` 56, `bindel` 6, `bindl` 4, `bindm` 2, `unbind` 1, `animation` 18, `bezier` 10, `env` 14, `source` 10, `windowrule` 7 (4 block, 3 inline), `gesture` 3, `permission` 1, `exec-once` 1, `$var` definitions ≈60, plus category blocks `general`, `decoration{shadow,blur}`, `animations`, `dwindle`, `master`, `misc`, `xwayland`, `render`, `ecosystem`, `group{groupbar}` and inline `cat:sub:key = v` lines. Dispatchers used: `exec` 30, `workspace` 11, `movetoworkspace` 11, `movewindow` 5, `movefocus` 4, and one each of `togglespecialworkspace`, `togglefloating`, `resizewindow` (bindm), `movewindow` (bindm), `pseudo`, `killactive`, `fullscreen`, `focuscurrentorlast`.

### 4.1 Grammar features exercised

| Feature (§1 ref) | Where | Maps cleanly? |
|---|---|---|
| `$var = value` + `$var` in values (§1.2) | `colors.conf` (≈45 colour vars), `$config = $HOME/.config`, `$terminal/$fileManager/$menu`, `$mainMod` (keybinds.conf), `$interval`/`$curve` (animation speed/curve), `noctalia-colors.conf` **redefines** `$primary/$surface/…` after `colors.conf` | Yes, but two lossy points: (a) `$primary` is defined in `colors.conf` and *redefined* in `noctalia-colors.conf`, and `general:col.active_border = $primary` is evaluated at parse time in `hyprland.conf` (before the redefinition) and again inside `noctalia-colors.conf` (after) — Lua must model this as sequential `hl.config` calls with locals re-assigned in file order; (b) `$config = $HOME/.config` uses env-var expansion (§1.2) → `os.getenv("HOME") .. "/.config"`. Variables cross file boundaries in hyprlang; Lua `local`s do not (§2.10.9). |
| Env-var expansion in `source =` (`$config/hypr/colors.conf`), `~/…`, `./dms/cursor.conf` relative to the sourcing file, absolute paths, paths outside `~/.config/hypr` (`~/repos/forest-shell/...`) (§1.3) | hyprland.conf lines 16, 104, 317-338 | Yes → `require("keybinds")`, `require("~/repos/forest-shell/integration/hyprland/forest-binds")` (§2.10.9). `./dms/cursor.conf` is relative to hyprland.conf's directory in both engines here (main file). All ten sourced files must themselves be converted to `.lua`. |
| Comments incl. trailing `# comment` after values (`$mainMod = SUPER # Sets …`, `bind = $mainMod, P, pseudo, # dwindle`) (§1.1) | keybinds.conf, misc block | Yes; note the trailing comma before `#` yields an empty 4th arg (`pseudo,` → args `""`), fine for `hl.dsp.window.pseudo()`. |
| Sloppy bool `enabled = yes, please :)` (§1.8 prefix rule → 1) | animations block | Lua needs `true` (§2.10.10). Importer must normalise bool words. |
| **Orphan keys outside any category**: `workspace_swipe = true`, `workspace_swipe_fingers = 4` at top level | hyprland.conf ~line 33 | Not valid hyprlang (no category → "config option does not exist", §1.10) *and* not valid options in 0.54+ anyway (0.54 `Variables.md:303-318` gestures section has no `workspace_swipe`/`_fingers`; `hyprctl getoption gestures:workspace_swipe` → "no such option" on 0.56.2). Importer: comment out with a note. |
| Duplicate key in one block (`blur { size = 6 … size = 3 }`) — last wins (§1.6) | decoration block | Yes; emit only the last (`size = 3`) or both in order (Lua tables can't repeat a key → keep last). |
| Same category opened in several files (`general {}` in hyprland.conf, noctalia-colors.conf, ghibli-theme.conf; `decoration {}` ×2; `animations {}` ×2) — later assignments override earlier | multiple files | Yes: sequential `hl.config({...})` calls merge (§2.10.10). Order of `require` must equal `source` order. |
| Inline addressing `decoration:blur:brightness = 0.95`, `ecosystem:no_donation_nag = 1`, `misc:animate_manual_resizes = 1` (§1.1) | hyprland-gui.conf | Yes → `hl.config({decoration={blur={brightness=0.95}}})` etc. |
| Dotted keys `col.active_border`, `col.border_locked_active`, `groupbar { col.active }` | general/group blocks | Yes → `general.col.active_border` / `group.groupbar.col.active` (§2.10.10). |
| Not used: `# hyprlang noerror/if/endif`, `{{ }}` arithmetic, `\` continuation, `##` escapes, `device {}`, `monitor =`, `monitorv2 {}`, `layerrule`, `workspace` rules, `submap`, `plugin`, `envd`, `exec`, `exec-shutdown`, `bindd`/`bindk`/`binds`, `layout` (only `general:layout = scrolling`) | — | The importer must still support them (§1, §2), but this config is not a test for them. |

### 4.2 Keywords and their Lua targets

| Construct (count) | Example from config | Lua target | Clean? |
|---|---|---|---|
| `bind = MODS, key, exec, cmd` (30) | `bind = $mainMod, M, exec, command -v hyprshutdown >/dev/null 2>&1 && hyprshutdown \|\| hyprctl dispatch exit` | `hl.bind(mainMod .. " + M", hl.dsp.exec_cmd("command -v hyprshutdown >/dev/null 2>&1 && hyprshutdown || hyprctl dispatch exit"))` (§2.1) | Yes. Commands containing `"`, `\`, `$(...)` (forest-binds.conf screenshot line has `'…$(date +%Y%m%d-%H%M%S).png'`) need Lua string escaping — use `[[…]]` long strings when a `"`/`\` is present. `hyprctl dispatch exit` still works under Lua (DispatcherTranslator). |
| `bind = $mainMod SHIFT, key, …` / `bind = CTRL ALT, Q, …` / `bind = ALT SHIFT, S, …` | keybinds.conf, forest-binds.conf | `"SUPER + SHIFT + S"`, `"CTRL + ALT + Q"` — mods must be `+`-joined uppercase tokens (§2.1) | Yes (mechanical). |
| Empty mods `bindel = ,XF86AudioRaiseVolume, exec, …` (6), `bindl = , XF86AudioNext, …` (4) | keybinds.conf | `hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("…"), { locked = true, repeating = true })`; `bindl` → `{ locked = true }` (§2.1 flags `e`,`l`) | Yes — identical to `example/hyprland.lua:294-305`. |
| `bindm = $mainMod, mouse:272, movewindow` / `mouse:273, resizewindow` | keybinds.conf | `hl.bind(mainMod .. " + mouse:272", hl.dsp.window.drag())`, `… mouse:273", hl.dsp.window.resize())` (§2.1 flag `m`) | Yes (drop the flag; `{mouse=true}` is inert). |
| `bind = $mainMod, mouse_down, workspace, e+1` / `mouse_up, workspace, e-1` | keybinds.conf | `hl.bind(mainMod .. " + mouse_down", hl.dsp.focus({ workspace = "e+1" }))` (§2.2 `workspace`) | Yes. |
| `workspace, N` (9), `movetoworkspace, N` (10), `movetoworkspace, special:magic`, `togglespecialworkspace, magic` | keybinds.conf | `hl.dsp.focus({workspace=N})`, `hl.dsp.window.move({workspace=N})`, `hl.dsp.window.move({workspace="special:magic"})`, `hl.dsp.workspace.toggle_special("magic")` | Yes. |
| `movefocus, l/r/u/d` (4), `movewindow, h/l/k/j` (4) | keybinds.conf | `hl.dsp.focus({direction="l"})`, `hl.dsp.window.move({direction="l"})` — **note** the config uses vim letters `h/j/k` for `movewindow`; legacy `movewindow` takes only the first char and `Math::fromChar` accepts only `r/l/t/u/b/d` (`src/helpers/math/Direction.hpp:14-24`; DT:248-268 → error "Unsupported direction"), so `movewindow, h`/`k`/`j` are already broken in legacy (and `movewindow, l` bound to the *right* arrow moves **left**). Lua `parseDirection` accepts `l/left, r/right, u/up/t, d/down/b` (LBI:294-304) — `h`, `j`, `k` would be config errors. | **Lossy/pre-existing bug**: importer should map `h→l`, `j→d`, `k→u` (or flag) — cannot pass through verbatim. |
| `killactive,` `togglefloating,` `pseudo,` `fullscreen, 0` `focuscurrentorlast,` | keybinds.conf, hyprland-gui.conf | `hl.dsp.window.close()`, `hl.dsp.window.float()`, `hl.dsp.window.pseudo()`, `hl.dsp.window.fullscreen({mode="0"})` (or `"fullscreen"`), `hl.dsp.focus({last=true})` (§2.2) | Yes. |
| `unbind = SUPER, Q` (hyprland-gui.conf) after `bind = SUPER, Q, exec, $terminal` (hyprland.conf) | hyprland-gui.conf | `hl.unbind("SUPER + Q")` — matches only if the earlier `hl.bind` key string normalises to `super+q` (§2.1 unbind) | Yes if the importer canonicalises key strings everywhere (`"SUPER + Q"`), otherwise silently no-op. Also note the original bind is `bind = SUPER, Q` (literal) while the rest use `$mainMod`; both become `"SUPER + Q"`. |
| `gesture = 4, vertical, workspace` | hyprland.conf | `hl.gesture({fingers=4, direction="vertical", action="workspace"})` (§2.10.2) | Yes. |
| `gesture = 3, left, dispatcher, layoutmsg, move +col` / `…right, dispatcher, layoutmsg, move -col` | hyprland.conf | No `dispatcher` string action in Lua → `hl.gesture({fingers=3, direction="left", action=function() hl.dispatch(hl.dsp.layout("move +col")) end})` (§2.10.2) | **Lossy shape change** (string action → callback), semantics preserved. |
| `env = K,V` (14; incl. `GDK_BACKEND,wayland,x11,*` and `QT_QPA_PLATFORM,wayland;xcb`) | hyprland.conf, dms/cursor.conf | `hl.env("GDK_BACKEND", "wayland,x11,*")` — first-comma split, rest is the value (§2.10.6) | Yes. Duplicates (`XCURSOR_SIZE`/`HYPRCURSOR_SIZE` set in both files) → last `setenv` wins in both engines. |
| `ecosystem { enforce_permissions = 1 }` + `permission = /usr/(bin\|local/bin)/grim, screencopy, allow` | hyprland.conf | `hl.config({ecosystem={enforce_permissions=true}})`; `hl.permission("/usr/(bin|local/bin)/grim", "screencopy", "allow")` (§2.10.4) | Yes (first-launch-only in both). |
| `exec-once = qs -p /home/daniel/repos/forest-shell/shell.qml` | shell-switcher-startup.conf | `hl.on("hyprland.start", function() hl.exec_cmd("qs -p …") end)` (§2.10.7) | Yes. |
| `general { … col.active_border = $primary … layout = scrolling }` ×3 files; `col.active_border = rgba(6a5740ee) rgba(7ba153ee) 45deg` (ghibli) | hyprland.conf, noctalia-colors.conf, ghibli-theme.conf | `hl.config({general={col={active_border=primary}, layout="scrolling", …}})`; gradient → `{colors={"rgba(6a5740ee)","rgba(7ba153ee)"}, angle=45}` (§2.10.10) | Yes. Single-colour values may stay strings (`"rgb(a8c8ff)"`). |
| `decoration { rounding, rounding_power, active/inactive_opacity, shadow{enabled,range,offset = -2 2,render_power,sharp,color,color_inactive}, blur{enabled,size,passes,vibrancy,new_optimizations} }`; `decoration:blur:brightness`; `decoration:shadow:enabled = 0`; `offset = 0 0` | several files | `hl.config({decoration={shadow={offset={-2,2}, color=primary, …}, blur={…}}})` — vec2 → `{x,y}` (§2.10.10) | Yes. `blur { enabled = no }` (ghibli) → `false`. |
| `animations { enabled = yes, please :) ; bezier = …×6 ; animation = …×8 }` + 2 beziers/5 animations in hyprland-gui.conf + 2/6 in ghibli-theme.conf; speed/curve via `$interval`/`$curve`; styles `slide`, `popin 90%`, `slidefadevert`, `slidefade 14%`; leaves `windows, windowsIn, windowsOut, windowsMove, fade, workspaces, workspacesIn, workspacesOut, layers`; curve named `default` redefined (`bezier = default, 0, 1, 0, 1`) and `linear` (`1,1,1,1`) | 3 files | `hl.curve("wind", {type="bezier", points={{0.05,0.69},{0.1,1}}})`, `hl.animation({leaf="windowsIn", enabled=true, speed=6.9, bezier="easeOut", style="popin 90%"})` (§2.10.1). All bezier coords are within Lua's -1..2 range (max 1.1); all speeds ≤10 → within 0..100. | Yes. Same leaf re-declared across files (`windowsIn` ×3) → last call wins in both engines; `bezier = default` override is legal in both. |
| `dwindle { preserve_split = true }`, `master { new_status = master }`, `misc { force_default_wallpaper = -1; disable_hyprland_logo = true }`, `xwayland { force_zero_scaling = true }`, `render { direct_scanout = true }`, `group { col.* } groupbar { col.* }`, `misc:animate_manual_resizes = 1`, `misc:focus_on_activate = 1`, `ecosystem:no_donation_nag = 1` | several | `hl.config({...})` with the same key paths (all present in `HL.ConfigKey`, hl.meta.lua:38-392) | Yes. |
| `windowrule { name = …; match:class = …; match:title = …; match:xwayland/float/fullscreen/pin = true/false; suppress_event = maximize; no_focus = true; move = 20 monitor_h-120; float = yes; size = 1180 760; center = true }` (4 blocks) | hyprland.conf, forest-rules.conf | `hl.window_rule({name="…", match={class="…", xwayland=true, float=true, fullscreen=false, pin=false}, no_focus=true})`; `move={"20","monitor_h-120"}` or `move="20 monitor_h-120"`; `float=true` (not `"yes"`); `size={1180,760}` (§2.5) | Yes; bool words → booleans. |
| `windowrule = immediate 1, match:class ^(steam_app_\d+)$` (×3: `immediate`, `no_blur`, `no_shadow`) — inline anonymous form | hyprland.conf | `hl.window_rule({match={class="^(steam_app_\\d+)$"}, immediate=true, no_blur=true, no_shadow=true})` (can merge the three into one rule since matchers are identical; or keep three) (§2.5) | Yes; regex backslash must be escaped in Lua `"…"` or use `[[^(steam_app_\d+)$]]`. Anonymous rules should be emitted *after* named ones to preserve legacy precedence (§2.9). |
| `source = …` ×10 (incl. `$config/…`, `~/…`, `./dms/…`, absolute, `~/repos/…`) | hyprland.conf | `require(...)` ×10 in the same order (§2.10.9); every target must be converted and renamed `.lua`; third-party-managed files (`shell-switcher-*.conf` "managed by shell-switch", `dms/cursor.conf` "Auto-generated by DMS", `hyprland-gui.conf` "Generated by HyprMod", `noctalia-colors.conf`) will be **regenerated as `.conf` by their tools** — the importer can convert them once, but the generators must be pointed at Lua or the `require` will go stale. | Mechanically yes; operationally lossy (external generators). |

### 4.3 Verdict on the corpus

Everything in this config maps to a Lua construct that exists in v0.56.2. Items that are not a 1:1 rewrite and need importer logic or a human decision:

1. `movewindow, h/j/k` — invalid direction letters (pre-existing legacy bug); needs `h→l, j→d, k→u` remap or a warning.
2. `gesture = 3, left, dispatcher, layoutmsg, …` — becomes a Lua callback (`action = function() hl.dispatch(hl.dsp.layout("move +col")) end`).
3. Top-level orphan `workspace_swipe*` lines — dead in both engines; comment out.
4. `enabled = yes, please :)` and other bool words — normalise to `true/false`.
5. Variable redefinition across files (`$primary` in colors.conf vs noctalia-colors.conf) and `$config = $HOME/.config` — Lua locals per file + `os.getenv`; the importer must keep source order and re-evaluate `hl.config` per file exactly where the legacy file set the value.
6. `unbind = SUPER, Q` — only effective if bind key strings are canonicalised identically.
7. Files regenerated by external tools (`shell-switcher-*.conf`, `dms/cursor.conf`, `hyprland-gui.conf`, `noctalia-colors.conf`) — conversion is one-shot; the generators still write hyprlang.
8. Shell one-liners with quotes/`$(…)`/`\d` regexes need Lua string escaping (prefer `[[…]]`).
