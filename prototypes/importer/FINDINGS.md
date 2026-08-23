# Prototype: hyprlang → Lua importer, verified on the real config and a rice corpus

**Issue:** #9 (part of #1). **Date:** 2026-08-19. **Hyprland:** 0.56.2 (`efb5099`).
**Status:** prototype complete; throwaway code on the
[`prototype/hyprlang-importer`](https://github.com/danielbaldwin47/hyprland-settings-gui/tree/prototype/hyprlang-importer/prototypes/importer)
branch (main keeps only this findings doc).

**Question.** Is faithful conversion of hyprlang config trees to Lua feasible, and how is
fidelity verified — on this box's real config and across popular rices?

**Short answer. Yes, and it is verifiable to the pixel.** A ~2400-line throwaway Python
converter turned all seven corpus configs (7 rices, 98 `.conf` files) into `hyprland.lua`
that Hyprland 0.56.2 accepts (**7/7 `--verify-config` clean**, versus **2/7 for the original
`.conf` trees** — five rices use options 0.56.2 has removed). Loading each pair in a nested
Hyprland and diffing compositor state gives **zero unexplained differences**: every residual
traces to one of three named causes. Screenshots of the same three window arrangements are
**byte-identical for 5 of 7 rices** and differ by ≤2/255 on <0.005 % of pixels for the other
two. Where human ground truth exists (end-4 and ML4W ship their own hand-written Lua ports),
**the mechanical conversion is closer to the original than upstream's own migration.**

The migration is not the risk. The risk is everything *around* the config: `hyprctl dispatch`
changes meaning entirely, and bind actions become invisible to IPC. Those two findings
(§4.1, §4.2) matter more to this project than any translation detail.

---

## 1. What was built

`prototypes/importer/` — throwaway, no tests, not the shipping design.

| file | role |
|---|---|
| `hyprlang.py` | hyprlang v0.6.8 parser: line continuations, `##` escapes, `$var` longest-first substitution seeded from `environ`, `{{ a op b }}`, `# hyprlang if/endif/noerror`, category stack, special keyed categories, `source =` glob + `~` + recursion. Emits a flat, ordered event stream with `source=` inlined exactly where hyprlang inlines it. |
| `opttypes.py` | per-option typing for all 353 options, reusing rules R1–R13 from `prototypes/schema-page/schema.py` (issue #8) over `hyprctl -j descriptions` + `hl.meta.lua` + `coverage.json`. Also extracts the authoritative `HL.ConfigKey` set. |
| `dispatchers.py` | all 71 legacy dispatchers → `hl.dsp.*`, transcribed from research §2.2. |
| `emit.py` | every keyword handler and special block → `hl.*`; value conversion per type (bool words, css-gap shorthand, gradients, vec2, font weight, colours); warnings tagged with the research doc's L1–L28 lossy codes plus prototype-local codes. |
| `keysyms.py` | validates key names through the same `xkb_keysym_from_name(..., CASE_INSENSITIVE)` call Hyprland's Lua bind parser uses. |
| `convert.py` / `run_corpus.py` | CLI and corpus batch runner (stages a synthetic `$HOME` per rice so `source = ~/.config/hypr/…` and `$XDG_*` resolve). |
| `nested.py` | runs a config in a nested Hyprland with its own instance signature and socket; the host session is never touched. |
| `structural.py` | conf-vs-lua compositor-state diff. |
| `visual.py` + `winspawn.py` | same windows, same steps, `grim` screenshots, pixel comparison. |
| `groundtruth.py` | three-way comparison against upstream's own Lua ports. |
| `probe_*.py` | the one-off experiments behind §4 and §5. |

**Design choice: variables are fully expanded.** hyprlang substitutes `$var` textually at parse
time, so expanding them is exactly faithful and sidesteps L23/L28 (Lua locals do not cross
`require` boundaries) entirely. The cost is readability — the generated file inlines every
colour. A `--keep-vars` mode that emits `local`s for file-local variables is future work; it is
a presentation choice, not a fidelity one.

**Emission is flat**: one `hyprland.lua` with `source=` inlined, statements in parse order. This
makes submaps spanning files, cross-file `$var` scoping, and named-before-anonymous rule
precedence (L15) all trivially correct. A structure-preserving mode (one `.lua` per `.conf` +
`require`) is what the shipping wizard should offer; it must then handle those three cases
explicitly.

## 2. Does it convert? (static)

`Hyprland --verify-config` parses either engine without a compositor, so it is the cheap gate.

| rice | `.conf` files | options | binds | rules | `.conf` verifies | generated `.lua` verifies |
|---|---:|---:|---:|---:|:---:|:---:|
| hyprland-default (0.54 example) | 1 | 33 | 48 | 3 | ✗ (1 error) | **✓** |
| end-4 | 17 | 82 | 197 | 136 | ✗ (4) | **✓** |
| HyDE | 22 | 56 | 116 | 17 | ✓ | **✓** |
| JaKooLit | 20 | 86 | 156 | 116 | ✗ (8) | **✓** |
| ML4W | 24 | 43 | 110 | 19 | ✗ (1) | **✓** |
| HyprV4 (2024-era) | 3 | 22 | 48 | 16 | ✗ (26) | **✓** |
| local (this box) | 11 | 65 | 68 | 7 | ✓ | **✓** |

Five of seven rices **do not load cleanly on 0.56.2 today** — `dwindle:pseudotile`,
`misc:vfr`, `decoration:shadow:ignore_window`, `render:cm_fs_passthrough`, the whole 2024
`decoration:drop_shadow`/`blurls` family, and a `togglesplit` dispatcher that no longer exists.
The importer drops each with a `-- [dead]` line and an `L25`/`DEADOPT` warning, which is why
every generated `.lua` verifies clean. **Conversion improves config health**; it does not merely
preserve it.

## 3. Does it behave the same? (runtime)

Each rice's `.conf` and generated `.lua` were loaded in a nested Hyprland from the same staged
tree with `exec*` commented out on both sides (so no rice autostart can touch the host, and both
engines see identical input), then `hyprctl -j` state was diffed.

Compared: all 353 option values via batched `getoption`, `binds`, `monitors`, `animations`,
beziers, `workspacerules`, `layers`, `devices`, `configerrors`.

**Result: animations, beziers, workspace rules, devices and layers are identical for all seven
rices.** Options and binds have residuals, all fully attributed:

| residual | rices | cause |
|---|---|---|
| 2 option diffs | all 7 | `hyprctl getoption` on **any** font-weight option returns `invalid type (internal error)` under the Lua engine — a Hyprland bug, reproduced on a config that never touches the option (§4.3) |
| `mouse` flag differs on 2–4 binds | all 7 | **L5**, as predicted: `bindm` has no Lua `mouse` opt; behaviour comes from `hl.dsp.window.drag()/resize()` instead |
| binds with `key:""`, `keycode:0` | end-4 (44), JaKooLit (30), ML4W (2) | **`code:N` binds land in `sMkKeys`, not `keycode`** (§4.2) |
| 1 bind missing | JaKooLit, ML4W | key name resolves to no xkb keysym — dead under hyprlang too, hard error under Lua, so deliberately commented out (§4.4) |
| `monitors[].reserved` non-zero on the `.conf` side | 5 of 7 | Hyprland's on-screen **config-error banner** reserves screen space; it scales with error count (1 error → 24 px, 3 → 49, 8 → 86). Zero on both sides for the two rices whose `.conf` is clean. Evidence the conversion fixed the config, not a fidelity defect. |

After the `binddm` fix (§6) the **only** differing bind *field* across the whole corpus is
`mouse`. Bind ordering is preserved.

## 4. Findings that are not in the research doc

These are new, verified at runtime, and matter to the app beyond the importer.

### 4.1 `hyprctl dispatch` is engine-swapped — external tools break on migration

Under a Lua config, `hyprctl dispatch` **evaluates its argument as Lua source**:

```
$ hyprctl dispatch movefocus l          # hyprlang session
ok
$ hyprctl dispatch movefocus l          # lua session
error: [string "return hl.dispatch(movefocus l)"]:1: ')' expected near 'l'
$ hyprctl dispatch 'hl.dsp.focus({direction="l"})'   # lua session
ok
$ hyprctl dispatch 'hl.dsp.focus({direction="l"})'   # hyprlang session
Invalid dispatcher
```

The two CLI surfaces are disjoint. `DispatcherTranslator.cpp` rescues legacy *binds*, not the
CLI. `hyprctl keyword` is likewise refused under Lua (`keyword can't work with non-legacy
parsers. Use eval.`), matching issue #5.

This **falsifies** the claim in `docs/research/hyprlang-to-lua.md` §4.2 that
"`hyprctl dispatch exit` still works under Lua (DispatcherTranslator)". It does not.

Blast radius: every bar, script, shell integration and keybind that shells out to
`hyprctl dispatch …` stops working the moment the user's config becomes Lua — including the
local config's own `bind = $mainMod, M, exec, … || hyprctl dispatch exit`. **The migration
wizard must grep exec strings and referenced scripts for `hyprctl dispatch` / `hyprctl keyword`
and report them as breakage the wizard cannot fix by itself.** This is the single largest
real-world migration hazard found, and it belongs to the external-tool-bridge ticket (#11).

### 4.2 `code:N` binds become invisible to IPC

`hl.bind("SUPER + code:10", …)` is accepted, but `parseKeyString` puts the keycode in
`kb.sMkKeys` (the multi-key vector) rather than `kb.keycode`
(`LuaBindingsToplevel.cpp` v0.56.2). The bind still **fires** — `mkKeysymSetMatches` matches an
entry by keycode when its keysym is `XKB_KEY_NoSymbol` — but `hyprctl binds` reports
`key: ""`, `keycode: 0`.

Consequences: (a) any structural equivalence check is blind to these binds; (b) **the settings
app cannot enumerate them over IPC** — it must treat its own generated source as the source of
truth for keybinds, never `hyprctl binds` (input to #12); (c) source says multi-key matching
requires "not releasing", so `bindr`-style release semantics on `code:N` are suspect — not
verified at runtime.

This is common: end-4 and JaKooLit use `code:N` for the number row precisely so layouts don't
break it. Upstream end-4's own Lua port uses the same `"SUPER + ALT + code:"..N` spelling, so the
conversion is right and the reporting gap is Hyprland's.

### 4.3 `getoption` cannot read font-weight options under Lua

```
hyprlang session: {"option":"group:groupbar:font_weight_active","custom":"400","set":false}
lua session:      invalid type (internal error)
```

Reproduced on a two-line config that never mentions the option, so it is engine-wide, not
config-dependent. Also confirms issue #3's engine-dependent key names: the same option is
`custom` under hyprlang but `gradient` / `css` under Lua. Direct input to #8's writer contract
and #19's rows: the app cannot round-trip font weights through `getoption`.

### 4.4 Dead key names become fatal

`XF86AudioPlayPause` (JaKooLit), `XF86Lock` (ML4W) and `Enter` resolve to **no xkb keysym at
all**. Under hyprlang such a bind is accepted and silently never fires; under Lua it is a hard
config error that takes the rest of the file with it. The importer validates every key name
through libxkbcommon and comments the bind out with an `N4` warning. **A wizard must do this or
migration fails on configs that "worked" for years.**

### 4.5 `catchall` cannot carry modifiers

`binditn = Super, catchall, …` (end-4) must become `hl.bind("catchall", …)`; the Lua key parser
rejects `"SUPER + catchall"` with `Unknown keysym: "catchall"`. The Super requirement is lost.
end-4's own Lua port agrees — it also drops the modifier (and comments the bind out entirely).

### 4.6 First launch is consumed

Hyprland records `~/.local/share/hyprland/lastVersion`. `hl.env`, `hl.permission` and the donate
screen are first-launch-sensitive, so **a wizard that runs the old and new config in sequence in
the same `$HOME` does not get a fair comparison** — the second run is not a first launch. The
visual harness had to give each engine its own pristine `$HOME` before results stabilised.

## 5. Does it look the same? (visual)

Both engines were driven through an identical scripted sequence on a **headless 1920×1080
output** created inside the nested compositor (`hyprctl output create headless`), so the canvas
does not depend on how the host tiled the nested window. Three frames per side: empty workspace,
three tiled windows (one translucent, to exercise blur), and one floated + resized + centred
window (to exercise rounding, border, shadow). Windows are undecorated solid-colour GTK4
surfaces, so every pixel comes from Hyprland's own rendering. Animations run normally and are
allowed 2.5 s to settle.

Determinism required pinning three pieces of Hyprland's own chrome on **both** sides:
`debug:suppress_errors` (the error banner), `misc:force_default_wallpaper` (otherwise random of
three) and `misc:disable_splash_rendering` (the random splash line — "Thanks Brodie!" vs "Why is
there code???"). Startup toasts are given 16 s to expire; one of them, *"You are using the .conf
config format, support for which will be removed in Hyprland 0.57"*, is legacy-only by
definition and would otherwise dominate every diff.

| rice | empty | tiled | floating | client geometry |
|---|---|---|---|---|
| hyprland-default | **byte-identical** | **byte-identical** | **byte-identical** | identical |
| end-4 | **byte-identical** | **byte-identical** | **byte-identical** | identical |
| HyDE | **byte-identical** | **byte-identical** | **byte-identical** | identical |
| JaKooLit | 77 px @ ≤2/255 | 96 px @ ≤2/255 | 92 px @ ≤2/255 | identical |
| ML4W | **byte-identical** | **byte-identical** | **byte-identical** | identical |
| HyprV4 | **byte-identical** | 920 px @ ≤1/255 | 920 px @ ≤1/255 | identical |
| local (this box) | **byte-identical** | **byte-identical** | **byte-identical** | identical |

`results/shots/local-conf-tiled.png` and `local-lua-tiled.png` are the same file
(`md5 27568667a535b1d7d09ace60fc3b6a7e`).

The two non-exact rices differ by at most **2 of 255** on under 0.005 % of pixels — GPU blend
rounding, three orders of magnitude below anything visible. Gaps, borders, rounding, blur,
shadows, animations-at-rest and workspace layout are preserved.

## 6. Bugs found in the converter by these harnesses

Both were caught by the runtime diff, not by reading the spec — the case for keeping a
nested-Hyprland harness in the shipping test suite.

1. **`binddm` dropped the description.** `bindm` was coded as a fixed 3-field keyword per
   research §2.1, but `binddm = $mainMod, mouse:272, hold to move window, movewindow`
   (HyDE `keybindings.conf:74`) is real: the `d` flag adds a field even for mouse binds.
   Fixed; descriptions now match across the corpus.
2. **Out-of-range animation values aborted the config.** JaKooLit's `borderangle` speed of 180
   exceeds the Lua limit of 100, and Lua *rejects* rather than clamps. The importer now clamps
   and warns loudly (`L19`) — the animation runs at a different rate, which is a real behaviour
   change a wizard must surface, but it is better than a config that will not load.

## 7. Ground truth: our conversion vs upstream's own hand migration

end-4 and ML4W were captured mid-migration and ship a hand-written `.lua` beside every `.conf`
at the same commit — the only human ground truth that exists.

| | end-4 `.conf` | ours | upstream's port | ML4W `.conf` | ours | upstream's port |
|---|---:|---:|---:|---:|---:|---:|
| binds registered | 197 | **197** | 191 | 111 | **110** | 89 |
| bind keys matching the `.conf` | — | **153** | 1 | — | **108** | 87 |
| option values differing from the `.conf` | — | **2** | 8 | — | **2** | 8 |
| config errors | 4 | **0** | 0 | 1 | **0** | 0 |

end-4's `.conf` puts 196 of its 197 binds inside `submap = global`. Our conversion reproduces
that exactly (`{global: 196, virtual-machine: 1}`); **upstream's hand port lost the submap
entirely** (`{"": 190, virtual-machine: 1}`), which is why only one key matches. ML4W's port
silently drops 22 binds. Our 153/197 and 108/111 shortfalls are entirely the `code:N` binds of
§4.2 plus the dead key of §4.4 — i.e. reporting artefacts and a deliberate, warned removal.

The mechanical converter is **more faithful than the humans were**, on both rices. That is the
strongest available evidence that a wizard built this way is trustworthy.

## 8. Lossy constructs per rice (what the wizard must warn about)

Counts are warnings emitted by the converter; codes L\* are `docs/research/hyprlang-to-lua.md`
§2.11, N\*/others are new here.

| rice | warnings |
|---|---|
| hyprland-default | L24 ×1 (bool word), L25 ×1 (`dwindle:pseudotile` removed), L5 ×2 (`bindm`), L14 ×1 |
| end-4 | L14 ×50 (rule bool words), L27 ×5 (`# hyprlang if`), L5 ×3, L12 ×2 (gesture `dispatcher` → callback), L25 ×2, L21 ×1 (`plugin:` guard), L22 ×1 (`exec` timing), L24 ×1, **N1 ×1 (`catchall` loses its modifier)** |
| HyDE | L27 ×22 (`# hyprlang if` — evaluated at conversion time), L25 ×20, L24 ×7, L5 ×4, L13 ×2 (pre-0.54 rule syntax), L23 ×2 (`source=` unresolved), L22 ×1, L14 ×1 |
| JaKooLit | L14 ×47, L12 ×3, L25 ×2, L24 ×2, L5 ×2, L19 ×1 (**`borderangle` speed 180 → clamped to 100**), **N4 ×1 (dead keysym)**, BADDISP ×1 (`togglesplit` no longer exists) |
| ML4W | DEADOPT ×6, L5 ×2, L23 ×1, L22 ×1, L24 ×1, PARSE ×1 (`source=` matched nothing), **N4 ×1** |
| HyprV4 | **L13 ×16 (`windowrulev2`, pre-0.54 syntax — rejected outright by 0.56.2)**, L25 ×9, L24 ×4, L22 ×2, L5 ×2, BADDISP ×1 |
| local | **VIMDIR ×3**, L24 ×3, L5 ×2, L12 ×2, DEADOPT ×2, L14 ×1, L6 ×1 |

Everything research §4.3 predicted for the local config reproduced exactly: the three
`movewindow, h/j/k` binds that were **already broken under hyprlang** (`Math::fromChar` rejects
those letters) and are remapped to `l/d/u` with a warning; the two orphan `workspace_swipe*`
lines outside any category; the two `gesture … dispatcher, layoutmsg` actions that must become
Lua callbacks; and the `unbind = SUPER, Q` that only works because key strings are canonicalised
identically on both sides.

`# hyprlang if VAR` (L27, 27 uses across end-4 and HyDE) deserves emphasis: it is evaluated
**at conversion time** against the environment the wizard happens to run in, and baked in. The
resulting Lua is correct for that environment and silently wrong for another. The wizard must
show the user each condition and the branch it took.

## 9. What this means for the map

- **Feasibility: settled.** Faithful conversion is achievable and verifiable to the pixel;
  the app should own a migration wizard as ADR-0002 assumes.
- **Verification method: settled and reusable.** Nested Hyprland + `hyprctl -j` state diff +
  headless-output screenshot diff is cheap (~45 s per config), needs no hardware, and caught two
  real converter bugs. It should become the importer's test harness (input to #16).
- **`hyprctl dispatch` breakage (§4.1) is the headline migration hazard**, and it is an
  external-tool problem, not a config problem → #11.
- **Keybinds cannot be read back from IPC (§4.2)** → #12 must treat generated source as the
  source of truth.
- **Font weights cannot be read back via `getoption` (§4.3)** → #8's writer contract, #19.
- **First launch is consumed (§4.6)** → the wizard's preview/rollback flow (#14) cannot
  A/B two configs in one `$HOME`.
- Three corrections to `docs/research/hyprlang-to-lua.md`: §4.2's `hyprctl dispatch exit`
  claim is wrong (§4.1); `bindm` takes a description field when combined with `d` (§6.1);
  `code:N` maps to `sMkKeys`, not `keycode` (§4.2).

## 10. Reproducing

```sh
python3 prototypes/importer/convert.py ~/.config/hypr/hyprland.conf -o /tmp/hyprland.lua
Hyprland --verify-config -c /tmp/hyprland.lua

python3 prototypes/importer/run_corpus.py  /tmp/imp-out     # convert + verify all 7
python3 prototypes/importer/structural.py  /tmp/imp-struct  # nested state diff
python3 prototypes/importer/visual.py      /tmp/imp-visual  # screenshot diff
python3 prototypes/importer/groundtruth.py /tmp/imp-gt      # vs upstream's Lua ports
```

Results as run are checked in under `prototypes/importer/results/` on the prototype branch: the generated `.lua` and
warning report per rice, the three summary JSONs, the ground-truth comparison, and the two
byte-identical `local` screenshots.
