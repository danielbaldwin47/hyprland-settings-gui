# Prototype: schema-driven options page — findings

Resolves issue [#8](https://github.com/danielbaldwin47/hyprland-settings-gui/issues/8) (part of #1).
Hyprland `0.56.2` (`efb5099`), libadwaita 1.9, PyGObject/GTK 4, 2026-08-19.

Throwaway code lives beside this file. `./run.sh --sections input` opens the page;
the **Curated** switch in the header rebuilds the same generator with `overlay.json`
turned off, which is the whole experiment.

## Verdict

**Generation gets you the page; curation gets you the settings app.** A page built purely
from `hyprctl -j descriptions` + the Lua stub is structurally sound — every one of the 353
options produced a working, type-correct, live-applying row with no per-option code — but
it is not shippable, and the options it gets wrong are the ones a user reaches for first
(keyboard layout, acceleration profile, touchpad drag lock).

Numbers, from `report.py` (machine-checked, not eyeballed):

| measure | result |
|---|---|
| options that render with **no machine-detectable defect**, all 353 | **273 (77%)** |
| … in `input` | 38/60 (63%) |
| … in `decoration` | 40/43 (93%) |
| … in `general` | 21/23 (91%) |
| options needing **no correctness-critical override** after hand curation of 126 options | **72 (57%)** |
| options needing a **human-written title** to stop reading like a config key | 126/126 (100%) — 124 written, **2 missed** |

The two measures disagree on purpose, and the gap is the finding: the defect scan says 77%
of rows are *not broken*; hand-curating three sections says only 57% are *right*. The scan
cannot see a missing group, a `[[EMPTY]]` that silently reads as `adaptive`, or a subtitle
that says "only used when `general:resize_on_border` is on" instead of greying the row out.

**Curation load is section-shaped, not option-shaped.** `decoration` is 93% clean raw and
needed 3 widget overrides; `input` is 63% clean and needed 39 semantic entries. Budget
curation per section, and do the dense device sections (`input`, `misc`, `group`) first.

Screenshots (same generator, same data, switch flipped):
`shots/input-raw.png` · `shots/input-curated.png` · `shots/general-raw.png` ·
`shots/general-curated.png` · `shots/decoration-curated.png`.

## What the raw page gets right

Worth keeping, because it is free:

- **Types.** Rules R1–R13 from [`docs/research/option-schema.md`](../../docs/research/option-schema.md)
  §1.4 reproduce exactly on live data: toggle 174, int-range 52, float-range 33, enum-map 30,
  string 24, gradient 16, vec2 6, color 6, css-gaps 3, enum-string 4, font-weight 2, free-int 2,
  free-float 1. No option fell through to an unrenderable state.
- **Bounds.** `min`/`max` from `descriptions` make every numeric row a bounded SpinRow that
  cannot produce an invalid config.
- **Enum maps.** All 30 `map`-carrying ints render as ComboRows with correct values, and
  **needed zero semantic overrides** — the only widget class with a perfect score.
- **Colours and gradients** render as real swatches straight from `MS<Color>` / stub typing;
  `general` and `decoration` look close to finished with nothing but grouping and titles.
- **Descriptions** are usable subtitle text for ~80% of rows.

## What the raw page gets wrong

Ordered by how much it hurts, with the count over all 353:

1. **Sentinels read as real values (23).** `input:accel_profile` defaults to `[[EMPTY]]`
   (= device default) and the generated ComboRow shows **`adaptive`, selected** — the page
   confidently states something false. Same for `scroll_method` (`2fg`), `tap_button_map`
   (`lrm`), the `-1` colours, the `-1` pressure ranges. This is the single most damaging class.
2. **`[[EMPTY]]` strings render as blank rows (13).** Six of the first six rows of `input`
   are `kb_model`/`kb_variant`/`kb_options`/`kb_rules`/`kb_file`/`scroll_points` — empty
   `AdwEntryRow`s with a config key for a title and, because **`AdwEntryRow` has no subtitle**,
   no description either. The most important section of the app opens on six blank lines.
3. **Reset buttons lie.** Comparing widget state to `descriptions.default` marks
   `[[EMPTY]]` rows as modified, and float rows whose default is `0.0117` as modified once
   the SpinRow rounds to `0.01`. Confirms §4 of the research: is-default needs a per-type
   comparison with float epsilon and sentinel normalisation, not `==`.
4. **Map-less int enums (13).** `float_switch_override_focus` 0..2, `drag_lock` 0..2,
   `transform` 0..6 as bare spinners. Nobody knows what "2" means.
5. **No grouping (all).** 60 flat rows in `input`. The stub's `HL.ConfigOpt` tree gives
   sub-prefixes (`touchpad`, `tablet`) but not an ordering, and not the cross-cutting groups
   a person expects ("Scrolling" mixes `input:*` and `input:touchpad:*`).
6. **Dependencies not modelled (8).** `scroll_points` is live while `accel_profile` is not
   `custom`; `extend_border_grab_area` is live while `resize_on_border` is off. The prose
   says so, the widget does not.
7. **Pickers missing (15).** XKB layout/variant/options (5), monitor (3), font (3), file (2),
   regex (2) are all free-text.
8. **Units missing (10)** and `debug`/`quirks`/`experimental`/`input-capture` (27) shown at
   full weight next to real settings.

## Instant apply: it is fast enough, and the obvious implementation is 40× too slow

Measured against a **nested Hyprland running the Lua engine** (`nested/start.sh`, own
instance signature, host session untouched), 12 iterations, `measure.py` → `latency.json`.
This closes the caveat left open in `docs/research/live-apply.md` §8 ("no Lua session on
this box").

| path | median | p90 |
|---|---|---|
| `eval` live preview, **direct IPC socket** | **0.4 ms** | 0.5 ms |
| `eval` live preview, via `hyprctl` binary | 19.8 ms | 20.7 ms |
| atomic rename + explicit reload → **value actually applied** | **25.4 ms** | 25.8 ms |
| in-place write, watcher's own `IN_CLOSE_WRITE` → applied | 13.1 ms | 14.1 ms |
| `getoption` round-trip, direct IPC | 0.5 ms | 0.8 ms |
| `getoption` round-trip, via `hyprctl` binary | 20.2 ms | 20.6 ms |

24/24 applies landed the correct value; `configerrors` stayed empty throughout.

Four things follow:

- **Never shell out to `hyprctl`.** Process spawn costs ~20 ms *per call* and dwarfs every
  other cost in the pipeline. Reading current values for one 60-option page is 1.2 s of
  `hyprctl` versus ~30 ms over the socket. The app must speak `.socket.sock` directly
  (which research #6 already wanted for Flatpak reasons — this makes it a performance
  requirement too).
- **Instant apply is genuinely instant.** 25 ms write-to-applied is well inside a frame
  budget's worth of perceived immediacy; the debounce recommended in research §8 is about
  reload amplification, not latency.
- **`configreloaded` fires ~11 ms *before* the new value is readable** (event at 14.1 ms,
  value at 25.4 ms). Treat it as "reload started", not "apply done"; verify by reading the
  keys back, exactly as §8 step 4 prescribes.
- **In-place write is 2× faster than atomic-rename + reload** but can expose a half-written
  file to the watcher. Keep atomic rename; 12 ms is not worth the failure mode.

Bonus, unplanned: **`Hyprland --verify-config -c <file>` parses a Lua config and prints
errors without starting a compositor** — a real pre-flight check the app can run before it
ever writes. Caveat found the hard way: under `--verify-config` the `hl.dsp.*` namespace is
nil, so any file containing `hl.bind(..., hl.dsp.x())` fails verification although it is
fine at runtime. Usable for the generated *options* module today; not for the binds module.
Worth confirming against #15/#10.

Also confirmed live, first time on this box: **`hyprctl keyword` is refused under the Lua
engine** — `"keyword can't work with non-legacy parsers. Use eval."`

## Curation policy

The schema layer is **two files** — this prototype is evidence for the split proposed in
research §6, with the field list adjusted by what curating 126 options actually needed.

### Generated, per Hyprland version, `schema/hyprland-<version>.json`

Everything the prototype's `schema.py` derives with no human in the loop: `name`, `lua_key`,
`path`, `type`, `widget`, `description`, `default`/`default_raw`, `min`, `max`, `map`,
`choices`, `vec2_range`, `device_overridable`, `getoption_key`, `refresh`, `since`.
Generation is cheap and reproducible; regenerate per release and diff.

### Hand-curated overlay, version-independent, `schema/overlay.json`

Keyed by option name, all fields optional. **Mandatory tier** — without these the row is
*wrong*, and 54/126 curated options needed at least one:

| field | why | needed |
|---|---|---|
| `nullable` + `null_label` | the sentinel class: `[[EMPTY]]`, `[[Auto]]`, `-1`. Renders "Device default" / "Auto" / "Inherit" instead of a lie, and writes the sentinel back | 17 |
| `widget` | picker/segmented overrides the rules cannot infer: `xkb-layout`, `monitor-picker`, `font-picker`, `file-picker`, `regex`, `segmented`, `float-list` | 16 |
| `depends_on` `{option, value}` | greys the row out instead of leaving prose in the subtitle | 15 |
| `labels` `{value: text}` | map-less int enums, plus prettier text for `map` keys | 11 |
| `range` `{min,max,step,soft_max}` | `vec2Range`, INT_MAX ints, clamps stated only in prose (`sensitivity` is −1…1, `descriptions` says 0…1) | 8 |
| `visibility` `default\|advanced\|hidden` | 27 options in `debug`/`quirks`/`experimental`/`input-capture`, plus per-option advanced | 12 |
| `known_values` (+ `open: true`) | strings whose values exist only in wiki prose or `strChoice` | 6 |

**Polish tier** — the page is correct without them but reads like a config file dump:

| field | why | needed |
|---|---|---|
| `title` | **every option, 126/126.** `input:touchpad:tap-to-click` → "Tap to click". Auto-title-casing the leaf is not enough (`col.active_border` → "Active border") | 124 written, 2 missed |
| `group` + `order` | the page's shape. Must be authored per section as an ordered list of `{title, description, options[]}`; the stub tree gives sub-prefixes, not sections a person recognises | 3 sections |
| `unit` | `px` / `ms` / `deg` / `/s` in the title, so the number means something | 22 |
| `help` | replaces `descriptions` text when it is terse, wrong, or repeats the enum list; seed from the wiki row | 22 |
| `help_url` | per-section wiki anchor behind an ⓘ button | per section |
| `restart` | `hyprland` / `monitor-reload` / `xwayland` badge; wiki prose only | 8 (from research) |

**The overlay needs a completeness test in CI, and this prototype proves it.** Curating 126
options by hand, I missed titles on `input:virtualkeyboard:share_states` and
`input:virtualkeyboard:release_pressed_on_close` and did not notice until `report.py`
counted them — they render as raw config keys in the middle of an otherwise finished page.
Research §6.2's proposed validation (every heuristically-flagged option carries a `widget`
or `labels`; every overlay key exists in the generated schema) should be extended with:
every visible option has a `title`, and every option appears in exactly one `group`.

Fields proposed by research §6.2 that curating 126 options did **not** call for:
`type_override` (the source-derived `MS<Color>` bit covered all 6 colours),
`deprecated_in`/`renamed_from` (nothing to rename yet — keep for the version-diff work in #18).

### Rules the runtime must carry regardless of the overlay

- **is-default** is per-type: float epsilon, sentinel normalisation, gradient/css-gap
  `toString()` comparison. Naïve `==` produced false "modified" badges in three widget classes.
- **Current values come from `getoption`, never `descriptions.current`** (research §4) — the
  prototype reads one batched call per page and that is the right granularity.
- **Group ordering is data, not code.** The overlay's `groups` list drives the page; anything
  not placed falls into a trailing "Other" group so a new Hyprland option can never vanish
  (this is the *New in `<version>`* behaviour decided in #7, and it works).

### libadwaita constraints found while building

Feed these into the widget catalogue from research #6:

- `AdwEntryRow` **has no subtitle** — string options cannot show their description in the
  standard row. Either put help behind the ⓘ button (what the prototype does) or use an
  `AdwActionRow` + `GtkEntry` for free-text options. This affects 24 string options.
- `AdwComboRow` **truncates long labels** ("Tiled → floa…", "Device d…"). Curated `labels`
  must be short; long explanations belong in `help`.
- Greying a row via `set_sensitive(False)` also dims its subtitle to near-unreadable, and
  appending "needs X = on" to the description makes a three-line subtitle. A dedicated
  suffix badge reads better — a small UI decision for the implementation spec.
- `AdwExpanderRow` (css-gaps, vec2, gradient) hides the current value until expanded; the
  row needs a value summary in the subtitle or a suffix.

## What this settles for the map

- Schema-driven generation is the right architecture: one generator, one row set, 353
  options, no per-option code.
- The overlay is **not optional** and is a first-class, hand-maintained, reviewed asset —
  roughly 40 semantic entries and one group list per dense section. Estimate the whole
  surface at ~150 semantic entries and 21 group lists.
- The **Config view** of #7 can be generated as-is. The **Tasks view** is the same rows with
  a different `groups` list, which is more evidence for the "one Schema, two Views" decision.
- Instant apply is settled as a latency question; what remains for #15 is reload
  amplification, error recovery, and the external-tool bridge.
