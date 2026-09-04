# Hyprland release check

Read by the agent claiming a `Release check: Hyprland <ver>` issue, before step 1.

The per-release protocol that keeps the Schema and the curated Tasks view from rotting as Hyprland drifts. Run it when a `Release check: Hyprland <ver>` issue (label `ready-for-agent`) appears — a scheduled watcher opens one per Hyprland release (see [ADR-0012](../adr/0012-release-drift-protocol.md) for the policy behind every step).

The Config view regenerates itself from the Schema; this protocol exists because the Overlay and the Tasks placement are curated by hand and drift silently otherwise.

**Deliverable:** one PR containing the new Generated schema, the machine diff, the Overlay updates, and a summary comment. The release is **handled** when the CI overlay completeness test passes on the new schema and a human has reviewed the diff via the PR — Tasks placement, `help`, and `unit` are polish and may lag.

## 1. Generate

Build the new version's Generated schema:

- Obtain Hyprland `<ver>`: distro package or build at the release tag.
- Run `tools/gen_schema.py` against all three sources: (a) `hyprctl -j descriptions` from a nested headless Hyprland of `<ver>`, (b) that version's `/usr/share/hypr/stubs/hl.meta.lua`, (c) a source checkout at the tag (for `MS<Color>` types, `strChoice`, `vec2Range`, refresh bits, the device-overridable list). If (c) is unavailable the generator degrades (Color→string, empty ranges) — note it in the PR; the Overlay must fill the gaps.
- Output: `data/schema/hyprland-<ver>.json`.

Done when the file exists and the generator reported all three sources consumed (or the degradation is noted).

Confirm it on the same machine with `pytest tests/integration -m hyprland`, which reruns the generator against the live compositor and fails if the committed schema is not what comes out. It is the only tier that can check this — CI has no Hyprland — so running it here is the check, not a formality.

## 2. Diff

Compare against the previous newest schema, at four layers:

1. **Schema diff** — classify every change: added / removed / **renamed** (heuristic: identical description + default across a removed/added pair — confirm against the release notes before recording) / retyped / range change / enum-map change / default change / new Section or subsection.
2. **Stub API diff** — `hl.meta.lua` old vs new: entity constructors and their arg tables, the `hl.dsp.*` dispatcher table, `BindOptions` fields, rule match props and effects, `HL.EventName`.
3. **Wiki diff** — the hyprland-wiki repo (github.com/hyprwm/hyprland-wiki), `content/Configuring/**` at the matching point: re-run the restart-required regex (the `restart` overlay field is wiki prose only — nothing in source or IPC exports it), and note changed help anchors.
4. **Entity catalogue diff** — `src/hyprtweaker/engine/entities_catalog.py`, the hand-curated half of the Entity surface (#70). Nothing in CI covers it, so it is the one layer that rots in silence: re-probe the new version and compare. `ANIMATION_LEAVES` against `hyprctl -j animations`; the `hl.device` key set, `GESTURE_DIRECTIONS`/`GESTURE_ACTIONS`, `PERMISSION_TYPES`/`PERMISSION_MODES` and the required-field rules against `Hyprland --verify-config` (a rejected key names itself); `GESTURE_DIRECTION_COVERS` by re-running the direction-pair sweep. Step 1 already stands up a Hyprland of `<ver>`, so all of it runs in that session.

Output: `data/schema/hyprland-<ver>.diff.json` (machine, shipped beside the schema — the app's *New in \<version\>* grouping and Retired detection read it) plus a human summary for the PR.

Done when every change in all four layers is classified — an unclassified change is a diff bug, not a skippable line.

## 3. Curate

Update `data/schema/overlay.json`:

- **Added option** → the mandatory tier: `widget`, `nullable`/`null_label`, `title`; plus `labels` / `known_values` / `range` / `depends_on` / `visibility` wherever the coverage heuristics flag the option (map-less small-int, `[a/b/c]` description, sentinel default, vec2/css_gaps/font_weight, font/monitor/regex/file strings).
- **Renamed** → `renamed_from` on the new name (the app migrates the user's value silently, Info notice).
- **Removed** → `deprecated_in: <ver>` on the old entry (kept — the Overlay is version-independent; the entry still serves older schemas in the support window).
- **Restart-list change** → update `restart` fields, hand-verified against the wiki prose.
- **Stub API changes** (new dispatcher, new match prop, new effect, changed arg table) → update the engine's typed tables. A **new entity kind** is out of this protocol's scope: open a `ready-for-human` issue for it and say so in the PR.
- **Entity catalogue changes** → update `entities_catalog.py` in the same PR. A leaf or field the app does not know is not a cosmetic gap: an unknown `hl.device` key is a hard error that takes the whole Module down, and a leaf the catalogue lacks is one the user cannot set. Unknown values already degrade to *shown, flagged* (ADR-0012's rule for Options, applied to Entities), so the PR is a curation update, never a rescue.

Done when the CI overlay completeness test passes against the new schema locally.

## 4. Verify

- CI tier: overlay completeness + unit golden files + `Hyprland --verify-config` over written outputs.
- Recommended, non-blocking: the nested-Hyprland Harness (`-m hyprland`) on `<ver>` over `tests/corpus/`. Harness failures do not block the PR — file each as its own issue and link them in the summary.

## 5. Ship

- Enforce the support window: `data/schema/` carries **latest + previous** only — delete older schema files (git history keeps them).
- Open the PR: schema + diff + overlay + engine-table updates, summary comment with per-class counts, options still unplaced in *New in \<ver\>* groups, and any follow-up issues opened.
- Close the release-check issue when the PR merges.
