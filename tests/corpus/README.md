# hyprlang rice corpus

Test corpus for the hyprlang → Lua importer prototype and visual-equivalence checks
(issue #17, unblocks #9). Each `tests/corpus/<rice>/` is the Hyprland config tree of a
popular rice, pinned to a specific upstream commit, laid out so that `hyprland.conf` is
at the rice root and relative `source=` lines resolve in place. Rices whose `source=`
lines are absolute (`~/.config/hypr/...`, `$HOME/...`, `$XDG_*`) carry a `ROOT` file
describing the mapping; by convention `~/.config/hypr → <rice>/` and any other
home-relative path → `<rice>/_home/<path>`.

Reproduce with `tests/corpus/fetch.sh` (pins live in `corpus.lock.json`, see below).
Total size ≈ 2.2 MB / ~420 files. Wallpapers, images, files > 200 KB and the
hyprlock/hypridle/hyprpaper/hyprsunset confs (never sourced from `hyprland.conf`) are
omitted; the fetch script prints exactly what it dropped.

## Rices

| dir | upstream | pinned commit | licence | Hyprland target | upstream config root | notes |
|---|---|---|---|---|---|---|
| `end-4/` | [end-4/dots-hyprland](https://github.com/end-4/dots-hyprland) | `f6b97c46` (2026-05-09) | GPL-3.0 | ≥ 0.53 (new `windowrule = match:…` one-line syntax); upstream tracks Hyprland git | `dots/.config/hypr` | last commit before `010f070e` (2026-05-11) deleted every `.conf` and finished the `hyprland.lua` migration. Tree holds both `.conf` **and** the parallel `.lua` port (17 + 17 files) → useful as ground truth for the importer |
| `hyde/` | [HyDE-Project/HyDE](https://github.com/HyDE-Project/HyDE) (ex prasanthrangan/hyprdots) | `a51460a7` (2026-05-26) | GPL-3.0 | ≥ 0.53 (`# hyprlang if HYPRLAND_V_0_53` migration shims; per-file "compatible with v0.53+" headers) | `Configs/.config/hypr` + `Configs/.local/share/{hyde,hypr}` | last `master` commit before the `rc→master` release `b8cc6472` (2026-07-27) that replaced `hyprland.conf` with `hyprland.lua`. Real config lives in `~/.local/share/hyde/hyprland.conf` (→ `_home/.local/share/hyde/`) which sources `~/.local/share/hypr/*.conf` and back into `~/.config/hypr` |
| `jakoolit/` | [JaKooLit/Hyprland-Dots](https://github.com/JaKooLit/Hyprland-Dots) | `c093e836` (2026-02-22, `main` HEAD; dots v2.3.20) | GPL-3.0 | ≥ 0.53 (`configs/WindowRules.conf` header); ships `WindowRules-pre-53.conf` for older | `config/hypr` | still hyprlang upstream. `wallpaper_effects/` (2.8 MB images) omitted |
| `ml4w/` | [mylinuxforwork/dotfiles](https://github.com/mylinuxforwork/dotfiles) | `437a2eb2` (2026-05-19) | GPL-3.0 | 0.53–0.55 (`windowrule {}` blocks; a script comments "0.55+ Lua dispatcher syntax") | `dotfiles/.config/hypr` | last commit before `66068ae9` "conf files removed" (2026-05-19). Tree holds both `.conf` (73) and the parallel `.lua` port (72) |
| `hyprv/` | [SolDoesTech/HyprV4](https://github.com/SolDoesTech/HyprV4) | `c81cf650` (2024-01-15, `main` HEAD) | none (no LICENSE file) | ~0.34 era (Jan 2024): `windowrulev2`, `drop_shadow`, `new_is_master` | `HyprV/hypr` | `SolDoesTech/HyprV` no longer exists; V4 is the newest generation. Legacy-syntax sample |
| `hyprland-default/` | [hyprwm/Hyprland](https://github.com/hyprwm/Hyprland) `example/hyprland.conf` | `0002f148` = tag `v0.54.0` | BSD-3-Clause | 0.54 exactly | `example` | the stock config; block-style `windowrule { name = … match:class = … }` |
| `local/` | this box, `~/.config/hypr` (captured 2026-08-19, `fetch.sh --local`) | n/a | n/a (private) | 0.56.2 (still hyprlang on the box) | `~/.config/hypr` + `~/repos/forest-shell/integration/hyprland` | sanitised: `/home/<user>` → `~`, only files reachable via `source=`. Mix of hand-written and tool-generated (noctalia, dms, hyprland-gui) fragments |

## hyprlang features used (grep counts over `*.conf`)

| feature | end-4 | hyde | jakoolit | ml4w | hyprv | default | local |
|---|---|---|---|---|---|---|---|
| `$var =` definitions | 14 | 240 | 47 | 58 | 2 | 4 | 52 |
| `source =` | 16 (relative) | 34 | 20 | 26 (`~/.config/hypr/…`) | 2 (`~/.config/hypr/…`) | 0 | 10 |
| `source =` via variable (`$configs/…`, `$XDG_*`, `$ANIMATION_PATH`) | – | 22 | 20 | – | – | – | 1 (`$config/hypr/…`) |
| `source =` glob | – | – | – | – | – | – | – |
| `# hyprlang if/endif/noerror` directives | 25 | 60 | – | – | – | – | – |
| `env =` | 6 | 18 | 16 | 22 | 6 | 2 | 14 |
| `exec-once =` | 11 | 18 | 16 | 11 | 9 | 0 | 1 |
| `submap =` | 3 | – | – | – | – | – | – |
| `plugin { … }` | 1 (hyprbars) | – | – | – | – | – | – |
| `bind*` total / of which `bindd`-family (with description) | 197 / 22 | 116 / 116 | 158 / 145 | 222 / 2 | 52 / 0 | 48 / 0 | 68 / 0 |
| `unbind =` | – | – | – | – | – | – | 1 |
| `windowrule =` (new ≥0.53 one-line) | 62 | 118 | 332 | – | 7 (old `windowrule = float,^(x)$`) | – | 3 |
| `windowrule { }` blocks | – | – | – | 19 (`conf/windowrules/default.conf`) | – | 3 | 1 (forest-rules) |
| `windowrulev2 =` (legacy) | – | – | – | – | 9 | – | – |
| `layerrule =` | 74 | 23 | 15 | 3 | – | – | – |
| `workspace =` rules | 1 | – | – | – | – | – | – |
| `monitor =` | 1 | 1 | 6 | 11 | 1 | 1 | 0 |
| `bezier =` / `animation =` | 9 / 14 | 132 / 184 | 118 / 148 | 57 / 71 | 1 / 5 | 5 / 17 | 10 / 18 |
| `gesture =` (≥0.51) | 5 | 5 | 4 | 1 | – | 1 | 3 |
| `device { }` / `device:` | – | – | 1 | – | 1 | 1 | – |
| `permission =` | – | – | – | – | – | – | 1 |
| `rgba($var)` colour-through-variable | – | 6 | – | – | – | – | – |
| `{{ }}` arithmetic / templating | – | – | – | – | – | – | – (only in a comment) |
| generated-by-tool confs | – | `themes/colors.conf` (wallbash), `$XDG_STATE_HOME/hyde/*.conf` (from config.toml, not in corpus) | `wallust/wallust-hyprland.conf` | `colors.conf` (matugen/wallust), `conf/<topic>.conf` 1-line switchers rewritten by ML4W app | – | – | `colors.conf`, `noctalia/*.conf`, `dms/cursor.conf`, `hyprland-gui.conf`, `shell-switcher-*.conf` |
| files: `.conf` / `.lua` / scripts | 17 / 17 / 9 | 47 / 0 / 0 | 42 / 0 / 66 | 73 / 72 / 18 | 5 / 0 / 0 | 1 / 0 / 0 | 11 / 0 / 0 |

Notable constructs worth importer test cases:

- **end-4**: `# hyprlang if !dontLoadDefaultX` around each `source=`, `# hyprlang noerror true/false`
  around optional `custom/*.conf`; `submap = global` / `submap = virtual-machine`; `plugin { hyprbars { … } }`
  block; 12 distinct bind flag combos (`bind`, `bindd`, `binde`, `bindid`, `bindit`, `binditn`, `bindl`, `bindld`, `bindle`, `bindln`, `bindm`, `bindp`); `layerrule` heavy (74).
- **HyDE**: 240 variables, many namespaced with dots (`$start.IDLE_DAEMON`, `$unt-idle.service`);
  `# hyprlang if !XDG_DATA_HOME` to default `$XDG_*` vars; `$ANIMATION_PATH`/`$WORKFLOWS_PATH`/`$LAYOUT_PATH`
  indirection then `source = $VAR`; a migration shim (`_home/.local/share/hypr/migration.conf`,
  `.local/share/hyde/migration/hypr/0.52_windowrules.conf`) selected via `# hyprlang if HYPRLAND_V_0_53`;
  `rgba($wallbash_pry1ff)` colours; sources a runtime-generated `$XDG_STATE_HOME/hyde/hyprland.conf` guarded by `noerror`.
- **JaKooLit**: `$configs`/`$UserConfigs`/`$scriptsDir` path vars defined in *several* files (redefinition);
  `source= $configs/…` with a space after `=`; 332 window rules; 145 `bindd`; six alternate
  `animations/*.conf` and `Monitor_Profiles/` selected by scripts.
- **ML4W**: two-level indirection (`hyprland.conf → conf/decoration.conf → conf/decorations/rounding.conf`);
  `$primary = $primary` self-referencing variable in `hyprland.conf` (colour vars come from
  `colors.conf`); `windowrule { }` block style; every `.conf` has a sibling `.lua` port.
- **HyprV4**: legacy `windowrulev2`, media binds with empty modifier (`bind = , xf86audioraisevolume, exec, …`, lower-case keysyms
  in `media-binds.conf`), 2024-era option names (`decoration:drop_shadow`, `master:new_is_master`) —
  exercises the deprecated-option path.
- **Hyprland 0.54 default**: block-style `windowrule { name = … }` with `match:class`, `$mainMod` var,
  `device { name = … }` block, `screenShader.frag` referenced.
- **local**: `$config = $HOME/.config` then `source = $config/hypr/colors.conf` (nested var in path);
  `source = ./dms/cursor.conf` (dot-relative); sources outside `~/.config/hypr` (`~/repos/forest-shell/…`);
  `unbind =`; `permission =`; comments containing `source =` lines that must not be followed.

## Caveats

- end-4 and ML4W are captured mid-migration: their `.lua` siblings are upstream's own hand-written
  translation of the same `.conf`, at the same commit — treat as reference output, not input.
  HyDE's Lua port came in a later commit and is not included.
- HyDE and ML4W ship files that are meant to be regenerated by their shells (wallbash/matugen/wallust);
  the corpus has the checked-in defaults. HyDE's `$XDG_STATE_HOME/hyde/*.conf` do not exist in the
  repo at all (generated from `config.toml`); the importer should treat them as missing + `noerror`.
- HyprV4 has no licence file; the corpus keeps it as a small legacy-syntax sample only. All other
  rices are GPL-3.0 (test fixtures, unmodified) or BSD-3-Clause (Hyprland example).
- `local/` was sanitised mechanically (`/home/<user>` → `~`, `$USER` → `user`, lines matching
  token/secret/password/api-key dropped — none matched); do not add machine-specific files there
  without re-running the sanitiser.
- No rice in this set uses `source=` globs or `{{ }}` arithmetic; if the importer needs those,
  add a synthetic fixture rather than another rice.

## Files

- `corpus.lock.json` — rice → repo, commit, root path, extra paths, excludes, note.
- `fetch.sh` — re-fetches every rice at its pinned commit (partial clone + sparse-checkout, then
  rsync with the size/type filters) into `tests/corpus/<rice>/`. `fetch.sh --local` recaptures
  `~/.config/hypr` following `source=` recursively. Hand-written `ROOT` files survive re-fetches.
- `<rice>/ROOT` — entry point and path mapping notes for that rice.
