# Research: what matugen, wallust, noctalia, DMS and shell-switch emit for Hyprland 0.56 Lua

Resolves issue #167 (spec #153). Feeds #163 (Bridge registry), #166 (wiring a tool to its Bridge module) and #187 (Bridge setup in the migration wizard); settles the open facts in ADR-0006 and ADR-0014.

Date: 2026-10-02. Hyprland `0.56.2`. None of the five tools is installed on this machine (Omarchy, Hyprland 0.56.2), so every claim comes from the tools' own repositories, fetched over the network, plus two kinds of run in a scratch directory with `HOME` and the `XDG_*` variables pointed into it and no compositor reachable: matugen 4.2.0 and wallust 4.1.0-alpha built from source, and `Hyprland --verify-config` through the repo's `verify()` helper.

Labels on every finding:

- **[O]** observed: read in the template, source or documentation named in the parentheses.
- **[R]** ran: executed the tool or Hyprland on sample input (a 128 px synthetic PNG), output read.
- **[I]** inferred: follows from [O] facts but was not run or read as such.

Source ids (`M1`, `W2`, ...) resolve in the Sources section at the end, each with a repository, commit or tag, and date.

---

## TL;DR

| Tool | Emits Lua for Hyprland 0.56 today? | Shape | Written to | Output path configurable? |
| --- | --- | --- | --- | --- |
| matugen | **Yes, data only**: the official template returns a Lua table of colours and sets no Option | `return { image = ..., primary = "0xffRRGGBB", ... }` | wherever `output_path` says (README suggests `~/.config/hypr/colors.lua`) | Yes |
| wallust | **Nothing** for Lua. Hyprlang only, in a separate repo | text templater; any format works | wherever the entry's `target` says | Yes |
| noctalia v5 (5.0.0-beta1 and later) | **Yes**, opt-in (`builtin_ids = ["hyprland"]`) | `return { colors = {...}, apply_theme = function }`; `apply_theme()` calls `hl.config` | `~/.config/hypr/noctalia.lua` | No for the built-in template; Yes through a user template |
| noctalia v4 (`legacy-v4` branch, no tag) | **Yes**, untagged | top-level `hl.config({...})`, loaded with `dofile` | `~/.config/hypr/noctalia/noctalia-colors.lua` (and a `.conf` beside it) | No |
| DMS 1.5.0 and later | **Yes, Lua only** (1.4.x wrote `.conf`) | top-level `hl.config({...})`, loaded with `require("dms.colors")` | `~/.config/hypr/dms/colors.lua`, plus `layout.lua`, `cursor.lua`, `outputs.lua`, `windowrules.lua`, `binds.lua`, `binds-user.lua` | No |
| shell-switch | **Nothing**; hyprlang only; not a colour tool (writes startup and a launcher bind) | `exec-once = ...`, `bind = SUPER, Space, exec, ...` | `~/.config/hypr/shell-switcher-startup.conf`, `shell-switcher-binds.conf` | No (hard-coded in the script) |

Five findings change the Bridge design:

1. **Three module shapes, not one.** Data-only table (matugen), table plus `apply_theme()` (noctalia v5), side effect on load (DMS, noctalia v4). Requiring the module does not apply colours for the first two. The Entrypoint cannot be `require("<module path>")` for every tool.
2. **matugen's official Lua template sets no Option**, so "set by matugen" has nothing to read from the tool's output. The ownership signal exists only if the app ships its own template (draft below, verified).
3. **Every tool writes in place** (truncate, write, close), which Hyprland's `IN_CLOSE_WRITE` watch sees. ADR-0006's `post_hook = hyprctl reload` is a second reload with no debounce. The one exception is noctalia v4, which loads with `dofile` (untracked) and runs `hyprctl reload` itself.
4. **noctalia v5's own script edits the Entrypoint**: it appends `require("noctalia").apply_theme()` to `hyprland.lua` after every template render if the substring `require("noctalia")` is absent, which lands after `user.lua`. Emitting that line ourselves (a commented-out one also counts) stops it.
5. **DMS's full setup replaces `hyprland.lua`** (after a backup). The per-file subcommands (`dms setup colors`) write one file only.

---

## ADR-0006 "Facts gathered": confirmed or corrected

| ADR-0006 claim | Verdict | Evidence |
| --- | --- | --- |
| matugen's official template repo ships a Hyprland Lua template `hyprland-colors.lua` | **Confirmed, with a correction**: it is data-only. It returns a table of `"0xffRRGGBB"` strings and calls no `hl.*` function | [O] M1; [R] rendered, 51 keys |
| noctalia writes `noctalia.lua` itself | **Confirmed for v5 only**, and only when `hyprland` is in `builtin_ids` (default empty). v4 writes `noctalia/noctalia-colors.lua` | [O] N5 `assets/templates/hyprland/apply.sh`, `src/config/config_types.h:1621-1622`; N4 `Services/Theming/TemplateRegistry.qml:380-394` |
| noctalia auto-detects the Lua engine with `hyprctl dispatch 'hl.dsp.no_op()'` | **Confirmed for v5** (`apply.sh:31`); falls back to "does `hyprland.lua` exist". **Corrected for v4**: no probe, only the file check, and it renders both formats | [O] N5, N4 `Scripts/bash/template-apply.sh:347-387` |
| DMS 1.5 generates native Lua config | **Confirmed**: 1.5.0 (tag `v1.5.0`, 2026-07-08) switched `dmshyprland` from `hypr-colors.conf` to `hypr-colors.lua`. **No DMS setting chooses Lua or `.conf`**: since 1.5.0 DMS only emits Lua | [O] D `quickshell/matugen/configs/hyprland.toml` at `v1.4.6` vs `v1.5.0` |
| wallust ships no templates but is fully user-template-driven (minijinja) | **Corrected**: the separate `wallust-templates` repo ships `hyprland.conf` (hyprlang). Engine is minijinja 2.24; no Lua template exists anywhere | [O] W3, W1 `Cargo.toml:42` |
| shell-switch is template-driven (`shell-start.conf.template`, `shell-binds.conf.template`) and never calls `hyprctl` | **Confirmed**: upstream's `reload_compositor` exists and is never called. Hyprlang only; the word "lua" does not occur in the repository | [O] S; [O] F §1.2 |
| Atomic-rename writes do not trigger a reload; an explicit `hyprctl reload` is needed | **Confirmed, and moot**: none of the five tools writes atomically | [O] see Cross-cutting §1 |
| The installed configs should add `post_hook`/`[hooks]` issuing `hyprctl reload` | **Corrected**: redundant for every tool that writes a `require`d file in place, because the write already reloads. Needed only for `dofile`-loaded files | [O] `docs/research/live-apply.md` (the `ConfigWatcher.cpp` notes); see Cross-cutting §1 |
| `require()` resolves relative to `~/.config/hypr/` only | **Confirmed**, and both `require("a/b")` and `require("a.b")` resolve, as **two distinct modules** | [R] verify-config cases `module-path-*` |

---

## matugen

Not a daemon: a CLI that renders text templates from one image or colour, once per invocation.

**Emits for Lua.** [O] M1 (`templates/hyprland-colors.lua` at `707c7b7`, added by `ffdee19` on 2026-05-11):

```lua
return {
    image = "{{image}}",
<* for name, value in colors *>
    {{name}} = "0xff{{value.default.hex_stripped}}",
<* endfor *>
}
```

- [R] Rendered with matugen 4.2.0 (`image sample.png --source-color-index 0 -m dark`): a table with `image` and 50 colour roles in alphabetical snake_case (`primary`, `on_primary`, `outline_variant`, `surface_container_high`, `source_color`, ...). Values are `"0xffRRGGBB"` strings: alpha first, the opposite of hyprlang's `rgba(RRGGBBaa)`.
- [R] Hyprland 0.56.2 accepts those strings as colours, singly and inside a gradient table (`{colors = {c.primary, c.tertiary}, angle = 45}`). The negative control `active_border = "notacolor"` fails `--verify-config`, so the check bites.
- [O] The hyprlang sibling (`hyprland-colors.conf`) is also data-only: `$image = ...` and `$<role> = rgba(<hex>ff)`. The user's own config consumes the variables (`col.active_border = $primary`). The same holds under Lua: `local c = require("colors")`, then `c.primary`. [O] M2 README "Hyprland".

**Output path.** [O] Any path: `output_path` in `[templates.<name>]`, `~` expanded, relative paths resolved from the config file, a single path or an array (`src/template.rs`, struct `Template` / `OutputPath`; M3). Missing directories are created. A read-only target is skipped with an error line (`template.rs:337`). The file is rewritten by truncate-and-write (`template.rs:351`). [R] Re-running with identical input kept the inode and advanced the mtime, so every run is a Hyprland reload when the file is `require`d.

**Config stanza.** [O] M2:

```toml
[config]
[templates.hyprland]
input_path = '~/.config/matugen/templates/hyprland-colors.lua'
output_path = '~/.config/hypr/colors.lua'
post_hook = '...'      # optional; runs after this template is written; takes {{ }} keywords
```

Config file: `$XDG_CONFIG_HOME/matugen/config.toml` (`src/util/config.rs:54`, `directories::ProjectDirs`), or `-c <file>`. [O] M4 `configuration.html`.

**Include.** [O] M2: `require("colors")` at the top of `hyprland.lua`.

**Option keys and variables.** [O] The official template sets none. [I] Under the ownership-signal rule (ADR-0018: the app never evaluates tool Lua) matugen therefore names no Option, only variables: `<role>` and `image`. A user's own matugen template may define anything: the owner's old dotfiles use `$primary = rgb(...)` plus bare-hex siblings such as `$primary_hex` (G).

**Lua vs `.conf`.** [O] matugen has no notion of it: the user chose a template file. Retargeting a `.conf` entry means changing `input_path` and `output_path`.

**Reload.** [O] matugen never calls `hyprctl`. [O] The write reloads Hyprland when the file is `require`d (`live-apply.md`). [I] A `post_hook` that also runs `hyprctl reload` doubles it. The owner's old `config.toml` carried such a reload (`run_after`, which is not a matugen key) (G).

**Detection.** [I] `matugen` on `PATH`, and `$XDG_CONFIG_HOME/matugen/config.toml` holds a `[templates.*]` entry whose `output_path` is under `~/.config/hypr/`. `PATH` alone is weak: DMS requires the `matugen` binary and runs it with its own merged config (`core/internal/matugen/matugen.go:1082`, D).

**Re-run against the current wallpaper.** [R] `matugen image <path> --source-color-index 0 -m dark|light -t <scheme> [--contrast N]`.

- matugen stores no "current wallpaper": the caller supplies the image path.
- With several candidate source colours and no TTY, a run without `--source-color-index` or `--prefer` (or `source_color_index`/`prefer` in `[config]`) fails: `Multiple source colors found, no preference was inputted, and a terminal was not detected` (`src/color/color.rs:237`).
- If the user's `[config.wallpaper]` has `set = true`, the same run also sets the wallpaper (`src/main.rs:728`); `--dry-run` skips templates, wallpaper and hooks (`main.rs:722`).
- 4.2.0 adds `-m smart` and `-t scheme-smart` (CHANGELOG 4.2.0).

**What the app supplies.** Nothing for the data-only table. For ownership of Options, the app-owned template below ([R] renders, `luac -p` clean, Hyprland accepts, all 6 `hl.config` keys exist in `HL.ConfigKey`):

```lua
local colors = {
    image = "{{image}}",
<* for name, value in colors *>
    {{name}} = "0xff{{value.default.hex_stripped}}",
<* endfor *>
}

hl.config({
    general = {
        col = {
            active_border = colors.primary,
            inactive_border = colors.outline_variant,
        },
    },
    group = {
        col = {
            border_active = colors.primary,
            border_inactive = colors.outline_variant,
            border_locked_active = colors.error,
            border_locked_inactive = colors.outline_variant,
        },
    },
})

return colors
```

It keeps the official table shape (so `require(...).primary` works for variables the Importer maps) and adds the effect.

---

## wallust

Same family as matugen: a CLI, run once per palette. Stable is 3.5.2 (`b689616`, 2026-01-18); `main` is `4.1.0-alpha` (`e3e484a`, 2026-09-13). The `[templates]` stanza is the same in both.

**Emits for Lua.** [O] Nothing. The main repository ships no templates; `wallust-templates` (W3, `2e0d6bb`) ships `hyprland.conf` (hyprlang `general { col.active_border = rgb(...) ... }`). Its header comment names the keys `src`/`dst`, which wallust does not read: they are `template`/`target`. No file in either repository mentions Lua.

**Output path.** [O] `target`: absolute, `~` expanded, directories created; written with `std::fs::write`, in place (`src/template/mod.rs:64`). `template` is relative to `$XDG_CONFIG_HOME/wallust/templates/`. [R] Wrote `~/.config/hypr/hyprtweaker/bridge/wallust.lua` from a sample image; inode kept and mtime advanced on an identical re-run.

**Config stanza.** [O] W2 `docs/config/template.md`, `docs/hooks.md`; [R] the form below ran:

```toml
[templates]
hyprland = { template = 'hyprland.lua', target = '~/.config/hypr/hyprtweaker/bridge/wallust.lua' }

[hooks]                       # optional; runs once after all templates, via /bin/sh
reload = 'hyprctl reload'     # not needed (see Cross-cutting §1)
```

`[hooks]` is a map, so the order of several hooks is unspecified (`src/config.rs:64`). `--no-hooks` skips them.

**Variables.** [O] W2 `docs/templates/variables.md`: `color0` to `color15`, `background`, `foreground`, `cursor` (default format `#RRGGBB`; [R] rendered uppercase), `colors` (vector), `wallpaper`, `backend`, `palette`, `alpha`, `alpha_dec`. Filters: `strip` (drop `#`), `hexa`, `rgb`, `saturate(0.6)`, `lighten`, and others (`docs/templates/filters.md`).

**Lua vs `.conf`.** [O] Not applicable: it renders whatever template the user wrote.

**Reload.** [O] None by wallust. Hooks run after templates (`src/main.rs:180`). Wallust also writes terminal colour escape sequences to open terminals unless `-s` (`src/lib.rs:85-97`) and creates a default `wallust.toml` when none exists (`src/config.rs:290`).

**Detection.** [I] `wallust` on `PATH`, and `wallust.toml` has a `[templates]` entry whose `target` is under `~/.config/hypr/`. A present config file is weak evidence: the first run creates one.

**Re-run.** [R] `wallust run <image> [-s] [-q]`. No stored current wallpaper: the caller supplies the image. For a non-image source there are `wallust cs <scheme>` and `wallust theme <name>`. [O] Light or dark: `-S/--style dark|light` in 4.x; 3.x selects it through the palette name (`dark`, `light`, ...). [I] The flag is version-dependent.

**What the app supplies.** Template (the whole of ADR-0006's "Template pack" for wallust) plus the stanza above. [R] The template below rendered with wallust 4.1.0-alpha, passes `luac -p`, and Hyprland accepts the output; its four colour keys mirror what the other three tools set:

```lua
-- Generated by wallust ({{ backend }}/{{ palette }}). Installed by hyprtweaker: do not edit.
local colors = {
    background = "rgb({{ background | strip }})",
    foreground = "rgb({{ foreground | strip }})",
    cursor = "rgb({{ cursor | strip }})",
    color0 = "rgb({{ color0 | strip }})",
    -- color1 .. color15 likewise
}

hl.config({
    general = {
        col = {
            active_border = {
                colors = {
                    "rgb({{ color1 | saturate(0.6) | strip }})",
                    "rgb({{ color2 | saturate(0.6) | strip }})",
                    "rgb({{ color3 | saturate(0.6) | strip }})",
                },
                angle = 45,
            },
            inactive_border = "rgba({{ color0 | strip }}ee)",
        },
    },
    -- group.col.border_active / border_inactive: add the same pair for parity with noctalia and DMS
})

return colors
```

Template authors must avoid a literal `{{`, `{%` or `{#` (nested Lua tables such as `{{0.05, 0.69}, ...}` hit it). [O] The escape is `{{ "{{" }}` (W2 `docs/templates/syntax.md`). [I] The sample above was written to avoid them; the escape itself was not run.

---

## noctalia

Two generations ship different Hyprland integrations. Neither is installed here, and nothing on this machine says which the owner runs: the corpus fixture `tests/corpus/local/noctalia/noctalia-colors.conf` is v4-era. **Unknown, needs the owner**: which version the owner runs. The registry must handle v5; v4 is a one-shot-import case (below).

### noctalia v5 (C++ shell; `noctalia` binary; `v5.0.0-beta1` and later; read at `v5.2.1`)

**Emits for Lua.** [O] N5 `assets/templates/hyprland/hyprland.lua` (since commit `2c1b74473`, 2026-05-15, first in tag `v5.0.0-beta1`). The file renders to:

```lua
-- Generated by Noctalia

local primary = "rgb(b0c6ff)"        -- {{colors.primary.default.hex_stripped}}
local surface = "rgb(121318)"
-- on_surface, secondary, on_secondary, error, on_error likewise

local function apply_theme()
    hl.config({
        general = { col = { active_border = primary, inactive_border = surface } },
        group = { col = { border_active = secondary, border_inactive = surface,
                          border_locked_active = error, border_locked_inactive = surface },
                  groupbar = { col = { active = secondary, inactive = surface,
                                       locked_active = error, locked_inactive = surface },
                               text_color = on_secondary, text_color_inactive = on_surface,
                               text_color_locked_active = on_error, text_color_locked_inactive = on_surface } },
    })
end

return { colors = { primary = primary, surface = surface, on_surface = on_surface,
                    secondary = secondary, on_secondary = on_secondary,
                    error = error, on_error = on_error },
         apply_theme = apply_theme }
```

[R] The real template, rendered with matugen as a stand-in engine (noctalia's `{{colors.<role>.default.hex_stripped}}` syntax is the same), loads under `--verify-config`, both through `require("noctalia").apply_theme()` and when only the `colors` table is read.

**Options set.** [O] Exactly 14, all by `apply_theme()`: `general:col.active_border`, `general:col.inactive_border`; `group:col.border_active`, `border_inactive`, `border_locked_active`, `border_locked_inactive`; `group:groupbar:col.active`, `inactive`, `locked_active`, `locked_inactive`; `group:groupbar:text_color`, `text_color_inactive`, `text_color_locked_active`, `text_color_locked_inactive`. Values are `"rgb(RRGGBB)"` strings. [R] All 14 exist in `HL.ConfigKey` (`/usr/share/hypr/stubs/hl.meta.lua`). Variables: `require("noctalia").colors.<primary|surface|on_surface|secondary|on_secondary|error|on_error>`.

**Output path and include.** [O] `apply.sh` (the template's own script, run as `input`, `output`, `apply` by `assets/templates/builtin.toml:168-172`):

- output `${XDG_CONFIG_HOME:-$HOME/.config}/hypr/noctalia.lua` in Lua mode, `noctalia.conf` otherwise; fixed by the script.
- `apply` runs as the template's `post_hook` after **every** render, even when the output is unchanged (`docs/user/theming/templates.mdx`, "Hooks"). If `grep -qF 'require("noctalia")' hyprland.lua` fails it **appends** `-- For Noctalia Color templates` and `require("noctalia").apply_theme()` to the end of `hyprland.lua` (creating the file if absent).
- `undo.sh` runs when the template is disabled: it rewrites `hyprland.lua` in place to delete those two lines and deletes `noctalia.lua`.

**Lua vs `.conf`.** [O] `apply.sh:28-41`: `hyprctl dispatch 'hl.dsp.no_op()'` printing `ok` means Lua; otherwise Lua if `hyprland.lua` exists, else `.conf`. The probe needs `HYPRLAND_INSTANCE_SIGNATURE` in noctalia's environment. Flipping to Lua therefore needs nothing from the user beyond running under Hyprland 0.56 with a `hyprland.lua`.

**Enabling.** [O] The template is opt-in: `[theme.templates] builtin_ids = []` by default (`example.toml:158-160`, `src/config/config_types.h:1621-1622`). Config lives in `~/.config/noctalia/*.toml` (merged alphabetically, "never rewritten"); the GUI writes `~/.local/state/noctalia/settings.toml`, which wins (`docs/user/configuration/index.mdx`).

**Reload.** [O] noctalia calls no `hyprctl reload`. It writes with `std::ofstream` (truncate, in place) and **skips the write when the rendered text equals the file** (`src/theme/template_engine.cpp:677-686`), so an unchanged palette does not reload Hyprland. Hyprland reloads on the close-write of the `require`d `noctalia.lua`.

**Triggers.** [O] Shell start (`m_themeService.apply()`, `application_services.cpp:753`; callback at `:743`), palette change (wallpaper change when `[theme].source = "wallpaper"`, mode change, config reload), and `noctalia msg templates-apply`.

**Detection.** [I] `noctalia` on `PATH`, and `"hyprland"` appears in `builtin_ids` across `~/.config/noctalia/*.toml` and `~/.local/state/noctalia/settings.toml`; or `~/.config/hypr/noctalia.lua` exists. `noctalia theme --list-templates` lists template ids.

**Re-run.** [O] `noctalia msg templates-apply` ("rerender the enabled configured theme templates for the current palette"; needs the shell running, `docs/user/ipc/media-and-ui.mdx`). Noctalia holds the wallpaper itself, so the app supplies no image.

**Retargeting.** [I] (documentation only; not run) A user template in a drop-in file the app owns, `~/.config/noctalia/hyprtweaker.toml`, with `[theme.templates.user.<id>]`, `input_path` = an app-installed copy of upstream `hyprland.lua`, `output_path = "$XDG_CONFIG_HOME/hypr/hyprtweaker/bridge/noctalia.lua"`, no `post_hook`. That avoids `apply.sh`, so noctalia never touches the Entrypoint. Cost: the app pins a copy of the template.

### noctalia v4 (QML shell on Quickshell; branch `legacy-v4` at `a08ff3619`, which is `v4.7.7` plus 37 commits)

- [O] **No v4 tag contains a Lua template.** `v4.7.7` is the newest tag and predates it; `Assets/Templates/hyprland.lua` arrived on 2026-05-14 (`9ff4fe614`, `1f6bb6d93`) and exists only on the branch.
- [O] Template: top-level `hl.config({...})` (no `return`) setting 10 keys: `general:col.active_border`, `inactive_border`; `group:col.border_*` (4); `group:groupbar:col.*` (4).
- [O] Output paths hard-coded in `Services/Theming/TemplateRegistry.qml:380-394`: `~/.config/hypr/noctalia/noctalia-colors.conf` **and** `.../noctalia-colors.lua`, always both.
- [O] `Scripts/bash/template-apply.sh:334-391` appends `dofile("$HOME/.config/hypr/noctalia/noctalia-colors.lua")` to `hyprland.lua` when it lacks the substring `noctalia-colors.lua`, then runs `hyprctl reload` unconditionally. [O] `dofile` is not watched by Hyprland (`live-apply.md`), which is why v4 reloads itself.
- [O] Triggers: wallpaper, colour-scheme or dark-mode change, through its own IPC targets `wallpaper`, `colorScheme`, `darkMode` (`Services/Control/IPCService.qml:455,489,598`). No IPC command re-renders without changing something. **Unknown**: a clean regenerate command for v4.
- [I] Recommendation in "Decisions": v4 is not wired; its `.conf` is one-shot imported and the user is told Lua needs noctalia 5.

---

## DMS (DankMaterialShell)

Quickshell shell plus a Go CLI `dms`. Read at `v1.6.2` (`2db7646f`, 2026-09-17) and `main` (`af27f0b`, 2026-10-02); Lua emission begins at `v1.5.0` (2026-07-08, commit `0b55bf5d` 2026-05-18).

**Emits for Lua.** [O] D, all under `~/.config/hypr/dms/`, all Lua, none configurable:

| File | Written by | Sets |
| --- | --- | --- |
| `colors.lua` | the `matugen` binary, from `quickshell/matugen/templates/hypr-colors.lua`, via `quickshell/matugen/configs/hyprland.toml` (`output_path = 'CONFIG_DIR/hypr/dms/colors.lua'`) | 10 Options (below) |
| `layout.lua` | DMS QML on every Settings change and on Lua detection (`Services/HyprlandService.qml:478`) | `general:border_size`, `general:resize_on_border` always; `general:gaps_in`/`gaps_out` only when DMS manages gaps; `general:layout` when set; the chosen layout's own keys; `decoration:rounding`; `layer_rule` xray entries |
| `cursor.lua` | DMS QML (`HyprlandService.qml:571`) | `hl.env` for cursor theme/size; `cursor:hide_on_key_press`, `hide_on_touch`, `inactive_timeout` when set |
| `outputs.lua`, `windowrules.lua`, `binds.lua`, `binds-user.lua` | `dms setup` / QML | `hl.monitor`, `hl.window_rule`, `hl.bind` entities |

`colors.lua` format ([R] rendered, loads under `--verify-config` via `require("dms.colors")`): `general:col.active_border` and `group:col.border_active` and `group:groupbar:col.active` from `primary`; `general:col.inactive_border`, `group:col.border_inactive`, `group:groupbar:col.inactive`, `border_locked_inactive`, `locked_inactive` from `outline`; the two `locked_active` keys from `error`. Values are `"rgb(RRGGBB)"` strings. The file's header says "Remove `require("dms.colors")` from hyprland.lua to override."

**Include.** [O] The shipped `hyprland.lua` ends with `require("dms.colors")`, `dms.outputs`, `dms.layout`, `dms.cursor`, `dms.binds`, `dms.binds-user`, `dms.windowrules` (`core/internal/config/embedded/hyprland.lua`). DMS spells them with dots. [O] Settings has a "fix" action that appends a missing `require("dms.<name>")` to `hyprland.lua` when the user clicks it (`quickshell/Modules/Settings/Widgets/ConfigInclude.qml:76-96`); nothing does it unprompted.

**Full deploy is destructive.** [O] `dms setup` (no subcommand) calls `deployHyprlandConfig` (`core/internal/config/deployer.go:635-743`): backs up `hyprland.lua`, **overwrites it** with DMS's template (re-merging only monitor lines), moves `hyprland.conf`, `hyprland.*.backup.*` and `dms/*.conf` into `~/.config/hypr/.dms-backups/<timestamp>/`, and writes the seven `dms/*.lua` files if absent. `dms setup colors|layout|outputs|cursor|windowrules|binds` (`cmd/dms/commands_setup.go:33-94`) writes that one file only. `CleanupStrayHyprlandConfFile` also moves a stray `hyprland.conf` out of the tree whenever DMS runs under Hyprland with a `hyprland.lua` present.

**Lua vs `.conf`.** [O] No setting. DMS reads which format the main config is: `dms config resolve-include` reports `configFormat`, and in `.conf` mode `ConfigInclude.qml` refuses to write and shows "This install is still using hyprland.conf. Run dms setup to migrate". Flipping means `dms setup`, which replaces the Entrypoint: the app must never suggest it.

**Reload.** [O] `colors.lua`: no explicit reload; the write reloads Hyprland through the require watch. `layout.lua` and `cursor.lua`: DMS runs `hyprctl reload` after each write (`HyprlandService.qml:319,489,578`).

**Triggers.** [O] DMS runs `dms matugen queue --state-dir ... --shell-dir ... --config-dir ... --kind image|hex --value ... --mode ... --icon-theme ... --matugen-type ...` on wallpaper, theme or settings changes (`quickshell/Common/Theme.qml:1771`). The `hyprland` template runs only when `Hyprland` is on `PATH` (`core/internal/matugen/matugen.go:61`). DMS also merges the user's own `~/.config/matugen/config.toml` `[templates]` into that run (`matugen.go:640-731`), so user and DMS templates share one matugen invocation.

**Detection.** [I] `dms` on `PATH`, and `~/.config/hypr/dms/colors.lua` exists (or `hyprland.lua` contains `dms.colors`).

**Re-run.** **Unknown / not offered.** [O] There is no argument-free regenerate: the shell passes its own state, shell dir and wallpaper. `dms ipc call theme dark|light|toggle` changes mode as a side effect (`Theme.qml:2440-2460`). [I] The Theming page should not offer "Regenerate" for DMS.

---

## shell-switch

Not a theming tool: it chooses which shell (noctalia, dms, ...) starts and binds `SUPER+Space` to that shell's launcher. Upstream `gitlab.com/theblackdon/shell-switch` at `93173b7` (2026-01-26); the repository holds no Lua.

**Emits.** [O] S, from `templates/hyprland/`, rendered by `sed` over five placeholders (`shell-switch:91-132`):

```
# shell-start.conf.template                 # shell-binds.conf.template
exec-once = {{LAUNCH_CMD}}                  bind = SUPER, Space, exec, {{LAUNCHER_CMD}}
```

Paths: `~/.config/hypr/shell-switcher-startup.conf` and `shell-switcher-binds.conf`, hard-coded in `lib/compositor.sh:96,120`; the template list is hard-coded in `update_compositor_configs` (`shell-switch:141-181`). The tool lives in `~/.config/shell-switch/` (a clone, symlinked into `~/.local/bin`). **There is no config stanza and no hook.** The owner's own research (F §1.2-2.2) reads their checkout: it has an uncommitted edit adding the `ghibli`/forest shells to `lib/shell-manager.sh`'s `SHELL_DB`, so it is not a clean clone. That checkout is on another machine; this one has no shell-switch, so those two facts are secondhand.

**Include.** [O] The user's `hyprland.conf` carries `source` lines for both files (`hyprland.conf` lines 319-323 per F). Under Lua: `require("shell-switcher-startup")`, `require("shell-switcher-binds")`.

**Options and Entities.** [O] No Option. It produces one startup command and one Bind: Entities.

**Reload and trigger.** [O] Never reloads (`reload_compositor` is dead code, F §1.2). Runs only when the user runs `shell-switch`, interactively: `main()` ignores arguments, so there is no non-interactive switch. Re-run is not applicable (no wallpaper).

**Detection.** [I] `~/.config/shell-switch/shell-switch` exists (or `shell-switch` on `PATH`), and the two `shell-switcher-*.conf` files exist.

**What the app supplies.** [R] Two Lua templates, rendered with the script's own `sed` expressions, `luac -p` clean, accepted by `--verify-config`:

```lua
-- shell-start.lua.template
hl.on("hyprland.start", function()
    hl.exec_cmd([==[{{LAUNCH_CMD}}]==])
end)

-- shell-binds.lua.template
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd([==[{{LAUNCHER_CMD}}]==]))
```

(each with the same `-- managed by shell-switch` header comment as the hyprlang pair). The long brackets keep quotes in a command from breaking the Lua; the existing `sed` rule still forbids `|` and `&` in a command. Plus a three-function patch to the user's script: `get_startup_config_path` and `get_binds_config_path` return `.lua` paths for `hyprland`, and `update_compositor_configs` selects `shell-start.lua.template` and `shell-binds.lua.template`. [I] `hl.on("hyprland.start", ...)` loads cleanly; that it fires once per session like `exec-once` was not run.

---

## Cross-cutting findings

1. **Writes are in place, so a `require`d file reloads Hyprland with no help.** matugen `OpenOptions::truncate` (`src/template.rs:351`), wallust `std::fs::write` (`src/template/mod.rs:64`), noctalia `std::ofstream` (`template_engine.cpp:686`), DMS `cat > file` and `os.WriteFile`, shell-switch `sed ... > file`. [O] Hyprland's `IN_CLOSE_WRITE` watch covers each (`live-apply.md`, the `ConfigWatcher.cpp` notes), and there is no debounce, so a `hyprctl reload` post-hook adds a second full reload. [O] Files loaded with `dofile` are not watched: only noctalia v4 relies on that and reloads itself. [R] matugen and wallust bump the mtime even for identical content; noctalia skips identical content.
2. **A module's contract is the tool's, and three exist.** `data` (matugen official), `apply` (noctalia v5: `require("noctalia").apply_theme()`), `effect` (DMS, noctalia v4, and every app-owned template). The Entrypoint writer today emits only `require("<slash path>")` for a bridge (`src/hyprtweaker/engine/writer/modules.py:177-195`; `ConfigPaths.require_path` is slash-form), so an `apply` entry needs the exact line stored with the tool.
3. **Dot and slash module names load twice.** [R] `require("hyprtweaker/bridge/matugen")` and `require("hyprtweaker.bridge.matugen")` return different tables (`package.loaded` is keyed by the string). A self-applying module required both ways applies twice; a data module is harmless. DMS and noctalia spell theirs `dms.colors` and `noctalia`; the app's own bridge modules use the slash form. ADR-0006's "Lua's `require` memoization makes this order-independent" holds only if every consumer uses one spelling.
4. **noctalia v5 edits the Entrypoint.** Its `apply.sh` appends to the end of `hyprland.lua` (after `user.lua`, against ADR-0006's require order) and `undo.sh` removes those lines. The guard is `grep -qF 'require("noctalia")'`, a substring match, so a quarantined `-- require("noctalia")` line (the form `modules.py:195` writes) also satisfies it. v4's guard is the substring `noctalia-colors.lua`.
5. **Template engines collide with Lua.** matugen and noctalia use `{{ }}` and `<* *>`; wallust uses `{{ }}`, `{% %}`, `{# #}`; shell-switch uses `{{NAME}}` through `sed`. App-authored templates must avoid those sequences (a nested-table literal `{{` is the usual trap) or escape them.
6. **Colour value formats differ and Hyprland accepts all of them.** [R] `"0xffRRGGBB"` (matugen), `"rgb(RRGGBB)"` (noctalia, DMS, wallust draft), `"rgba(RRGGBBee)"`, and a gradient table `{colors = {...}, angle = 45}`. The Importer's hyprlang `$var = rgba(...)` form maps to a string of the same shape.

### Hyprland 0.56.2 verification

Run through the repo's `verify()` (`tests/static/test_writer_verify_config.py`, session variables stripped) from a scratch script; every case wrote a `hyprland.lua` plus the tool's file into a temporary directory. 13 of 13 behaved as expected:

| Case | Result |
| --- | --- |
| matugen official table, consumed as `colors.primary` in `hl.config` | loads |
| negative control: `active_border = "notacolor"` | rejected (rc 1) |
| matugen table in a gradient table | loads |
| `require` with slash path, with dot path, with both (two distinct modules) | loads, loads, loads; `a ~= b` |
| noctalia v5 `require("noctalia").apply_theme()`; and reading `.colors` only | loads |
| noctalia v4 `dofile(os.getenv("HOME") .. ".../noctalia-colors.lua")` | loads |
| DMS `require("dms.colors")` | loads |
| wallust draft template | loads |
| shell-switch draft templates, both | load |
| matugen app-owned template (effect plus table) | loads |

`luac -p` passed for every rendered file. All 23 Option keys named in this document, and `hl.config`, `hl.bind`, `hl.on("hyprland.start")`, `hl.exec_cmd`, `hl.dsp.exec_cmd`, `hl.env`, `hl.monitor`, exist in `/usr/share/hypr/stubs/hl.meta.lua`. `--verify-config` proves the file loads, not that the values land: it does not read Options back.

---

## Decisions this settles (recommendations)

1. **Template pack contents.**
   - wallust: the template above and the `[templates]` stanza; no `[hooks]` entry. Output `~/.config/hypr/hyprtweaker/bridge/wallust.lua`. Owned Options: the four colour keys (`general:col.active_border`, `inactive_border`, `group:col.border_active`, `border_inactive`).
   - shell-switch: the two Lua templates and the three-function patch, shown to the user as a diff. The app does not edit the user's script (an unclean checkout the app cannot see). Until the user applies it, the Importer keeps the `.conf` pair as a one-shot import.
2. **matugen retarget stanza.** Copy the app-owned template (the draft above) to `~/.config/matugen/templates/hyprland-colors.lua`. Change the user's existing Hyprland entry to `input_path` = that file and `output_path = '~/.config/hypr/hyprtweaker/bridge/matugen.lua'`; leave `post_hook` and every other key as the user wrote them; do not add a reload hook. The user's old template file stays untouched. If no Hyprland entry exists, add `[templates.hyprtweaker_hyprland]` with the same two paths. Rationale: the official template sets no Option, so Preset/Manual cannot gate matugen's colours by omitting a `require` (ADR-0014); the app-owned template restores that and keeps the official table keys for variable bindings. *As built (#166, review of spec #153):* the app's template is `~/.config/matugen/templates/hyprtweaker-hyprland.lua`, beside the user's own `hyprland-colors.lua` rather than over it, and both stanza paths are absolute, so a non-default `XDG_CONFIG_HOME` works.
3. **"Adopt upstream Lua", per tool.**
   - noctalia v5: enable `hyprland` in `builtin_ids` through an app-owned drop-in (`~/.config/noctalia/hyprtweaker.toml`), and make the Entrypoint carry `require("noctalia").apply_theme()` itself, in the bridge block, so `apply.sh` finds the substring and never appends.
   - noctalia v4: not adopted. Tell the user Lua needs noctalia 5; one-shot import the existing `.conf`.
   - DMS: adopt `dms/colors.lua` only: the Entrypoint carries `require("dms.colors")` (dot form, DMS's own spelling). Do not run or suggest `dms setup`. `layout.lua`, `cursor.lua`, `outputs.lua`, `windowrules.lua` and the bind files are DMS Settings' own; see Q4.
4. **Colour sources or always-on bridges.** matugen and wallust are **Color sources** the app drives (it edits their stanza and runs them). noctalia and DMS are **gate-only bridges**: the app can switch their `require` off and on (their own palette UI stays theirs) but has no parameters to edit and no regenerate. Gating off works for DMS (documented, never re-added) and for noctalia only while a `require("noctalia")` line, commented or not, stays in the Entrypoint.
5. **Which tools name their Options statically.** noctalia v5 (14 keys, above), noctalia v4 (10), DMS `colors.lua` (10) and `layout.lua` (the keys in its table), and the two app-owned templates (wallust 4, matugen 6 as built: the four border keys plus the two locked-group borders, by construction). matugen's *official* template names none; a user's own matugen template names whatever the user wrote, so it is **not static** and gets variable binding (Q2), not ownership.

---

## For #163: open questions, each with a recommendation

**Q1. Entry shape.** The registry cannot hold only `module_path`. *Recommend:* each tool entry stores `contract` (`data` | `apply` | `effect`), the exact Entrypoint line(s) to emit (`require("noctalia").apply_theme()`, `require("dms.colors")`, `require("hyprtweaker/bridge/wallust")`), the file path the tool writes, `owned_keys` (colon-form Option keys, possibly empty), and `min_version`/format notes. The Entrypoint writer then takes lines, not paths, for the bridge block.

**Q2. Where do variables go for `data` tools?** *Recommend:* only matugen-official and a user's custom matugen template are `data`. The Importer maps `$primary` to `require("hyprtweaker/bridge/matugen").primary` (the app-owned template keeps the official key names), and the Option row says "bound to matugen", not "set by matugen". An unmappable custom variable (`$primary_hex`) becomes a loss-report item.

**Q3. One spelling per module.** *Recommend:* the registry stores each tool's canonical spelling, the writer and the require-detector use only it, and detection of "already required" matches both spellings (DMS's own resolver treats `dms/colors` and `dms.colors` as one file) so the app never emits the second form. The app's own modules stay slash-form.

**Q4. Does the bridge for DMS cover more than colours?** `layout.lua` and `cursor.lua` own `general:border_size`, `general:resize_on_border` and `decoration:rounding` unconditionally, plus gaps and layout when set, and DMS rewrites them on every Settings change. *Recommend:* v1 wires and badges `dms.colors` only; a detected `layout.lua` that the Entrypoint requires adds its static keys to `owned_keys` as a second, separately gateable entry, so the badge is honest about gaps and rounding without the app taking over DMS's layout page.

**Q5. noctalia's Entrypoint edits.** *Recommend:* treat `apply.sh`/`undo.sh` rewrites of `hyprland.lua` as foreign edits the existing hand-edit detection already catches, and regenerate the Entrypoint with consent; do not try to prevent them. Pre-emitting the line (Decision 3) makes the append a no-op; `undo.sh` removing it follows the user disabling the template, so the app should treat it as the user's intent.

**Q6. Reload hooks.** *Recommend:* drop ADR-0006's "installed configs include a reload hook". Install none for `require`d files; do not strip a hook the user already has. Revisit only for a `dofile` tool.

**Q7. Detection needs two facts, tool and wiring.** *Recommend:* `detect` returns `installed` (binary), `configured` (the config names a Hyprland output or enabled template) and `wired` (the Entrypoint carries the line), so the wizard (#187) can offer "set up" when `installed` and `configured` are true and `wired` is false, and the Theming page can show "installed, not wired".

**Q8. Which noctalia and DMS versions.** Needs the owner: the noctalia version, and whether DMS is 1.5 or newer (older writes `.conf`). *Recommend:* the registry reads versions from `noctalia --version` and `dms version` and gates on `>= 5.0.0` and `>= 1.5.0`, with a "needs update to emit Lua" state instead of a silent failure.

---

## Adjacent finding, outside the five tools

The owner's daily session runs **Omarchy 4.0.4**, not any of these tools. Its own template system renders `/usr/share/omarchy/default/themed/hyprland.lua.tpl` (user overrides in `~/.config/omarchy/themed/`) with `hl.config({general={col={active_border, inactive_border}}, group={col={border_active, border_inactive}}})`, run by `omarchy-theme-set-templates`. [O] Omarchy is not in ADR-0006's v1 set, and it sets the same four keys the bridged tools do, so a colour-owner conflict on Omarchy exists today. Out of scope here; worth a ticket if Omarchy users are an audience (ADR-0004).

---

## Sources

Every repository was fetched on 2026-10-02; line numbers refer to the commit named.

- **M1** matugen-themes `github.com/InioX/matugen-themes` at `707c7b7d` (2026-08-29): `templates/hyprland-colors.lua`, `templates/hyprland-colors.conf`; Lua template added by `ffdee19` (2026-05-11).
- **M2** the same repository, `README.md` §"Hyprland" (lines 512-534).
- **M3** matugen `github.com/InioX/matugen` at `ca94b8b0` (main, 2026-09-20; release `v4.2.0`, `bb27a35`, 2026-08-05): `src/template.rs`, `src/main.rs`, `src/util/config.rs`, `src/util/arguments.rs`, `src/color/color.rs`, `example/config.toml`, `CHANGELOG.md`. Built with `cargo build --release` in scratch; reported `matugen 4.2.0`.
- **M4** matugen docs `github.com/InioX/InioX.github.io` at `d41f6838` (2026-07-07): `templates/matugen/{configuration,usage,getting-started}.html`.
- **W1** wallust `codeberg.org/explosion-mental/wallust` at `a09480bc` (main, 2026-09-14; `4.1.0-alpha` = `e3e484a`; stable `3.5.2` = `b689616`): `src/`, `wallust.toml`, `docs/`. Built in scratch.
- **W2** the same repository, `docs/config/template.md`, `docs/hooks.md`, `docs/templates/{variables,filters,syntax}.md`, `docs/v4.md`.
- **W3** `codeberg.org/explosion-mental/wallust-templates` at `2e0d6bb7` (2026-04-18): `hyprland.conf`.
- **N5** noctalia-shell `github.com/noctalia-dev/noctalia-shell` at tag `v5.2.1` (`6ef43e2b`, 2026-10-01): `assets/templates/builtin.toml`, `assets/templates/hyprland/{hyprland.lua,hyprland.conf,apply.sh,undo.sh}`, `docs/user/{theming,configuration,ipc,compositor-settings}`, `src/theme/`, `src/app/application_services.cpp`, `src/config/config_types.h`, `example.toml`.
- **N4** the same repository, branch `legacy-v4` at `a08ff361` (2026-08-29; `v4.7.7` + 37 commits): `Assets/Templates/hyprland.lua`, `Services/Theming/TemplateRegistry.qml`, `Scripts/bash/template-apply.sh`, `Services/Control/IPCService.qml`.
- **D** DMS `github.com/AvengeMedia/DankMaterialShell` at `v1.6.2` (`2db7646f`, 2026-09-17), `v1.5.0` (2026-07-08) and main `af27f0ba` (2026-10-02): `quickshell/matugen/`, `quickshell/Services/HyprlandService.qml`, `quickshell/Common/{Theme.qml,ConfigIncludeResolve.js}`, `core/internal/{config,matugen,luaconfig}/`, `core/cmd/dms/commands_{setup,matugen,config}.go`.
- **S** shell-switch `gitlab.com/theblackdon/shell-switch` at `93173b7a` (2026-01-26): `shell-switch`, `lib/{compositor,shell-manager,common}.sh`, `templates/hyprland/`, `install.sh`.
- **F** the owner's research on their local shell-switch checkout, `github.com/danielbaldwin47/forest-shell`, `.wayfinder/research/shell-switch-integration.md` at `0a1f33b9` (2026-07-31), and `integration/shell-switch/registration.env`.
- **G** the owner's old dotfiles, `github.com/danielbaldwin47/diggledots` at `4ac7a5f1` (2026-04-11): `matugen/config.toml`, `matugen/templates/colors-hyprland.conf`. Read for the shape of a real user stanza; nothing copied.
- **H** Hyprland `v0.56.2` as installed here; this repository's `docs/research/live-apply.md` (file-watch rules) and `/usr/share/hypr/stubs/hl.meta.lua`.
- **O** Omarchy `4.0.4` packaged files under `/usr/share/omarchy/`.
