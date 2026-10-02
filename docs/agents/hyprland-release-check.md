# Hyprland release check

The per-release protocol that keeps the Schema and the curated Tasks view from rotting as Hyprland drifts. Run it when a `Release check: Hyprland <ver>` issue (label `ready-for-agent`) appears — a scheduled watcher opens one per Hyprland release (see [ADR-0012](../adr/0012-release-drift-protocol.md) for the policy behind every step).

The Config view regenerates itself from the Schema; this protocol exists because the Overlay and the Tasks placement are curated by hand and drift silently otherwise.

**Deliverable:** one PR containing the new Generated schema and the Overlay updates, and a summary comment that carries the machine diff. The release is **handled** when the curation tests (§ 3) pass on the new schema and a human has reviewed the diff via the PR — Tasks placement and `unit` are polish and may lag.

## 1. Generate

Build the new version's Generated schema:

- Obtain Hyprland `<ver>`: distro package or build at the release tag.
- Run `tools/gen_schema.py` against all four sources: (a) `hyprctl -j descriptions` and (b) `hyprctl -j animations` (`--animations`, the animation tree's leaves), both from a nested headless Hyprland of `<ver>`, (c) that version's `/usr/share/hypr/stubs/hl.meta.lua`, (d) a source checkout at the tag (for `MS<Color>` types, `strChoice`, `vec2Range`, refresh bits, the device-overridable list). If (d) is unavailable the generator degrades (Color→string, empty ranges) — note it in the PR; the Overlay must fill the gaps.
- Pass `--predecessor data/schema/hyprland-<previous>.json` (the previous newest schema). The generator then stamps `added_in: "<ver>"` on every Option that file lacks, keeps the predecessor's own stamps on Options it has, and records `"predecessor": "<previous>"` in the provenance block. The stamp is mechanical: write none by hand. The app groups an Option with a stamp and no curated placement under *New in \<version\>* on its Tasks Page. A missing file stamps nothing. The reproducibility test below reads the recorded predecessor and passes it again, so the stamped file stays reproducible.
- Output: `data/schema/hyprland-<ver>.json`.

Done when the file exists and the generator reported all four sources consumed (or the degradation is noted).

Confirm it on the same machine, from the repo root, with `env -u HYPRLAND_INSTANCE_SIGNATURE HARNESS_DRM_CARD=/dev/dri/card0 .venv/bin/pytest tests/integration/test_schema_reproducible.py -m hyprland`. It starts its own nested Hyprland of the installed version, reruns the generator against it and fails if the committed schema is not what comes out; name the file by path, because `test_ipc_live.py` beside it talks to whichever compositor the environment names. It is the only tier that can check this — CI has no Hyprland — so running it here is the check, not a formality.

## 2. Diff

Compare against the previous newest schema, at five layers:

1. **Schema diff** — `tools/diff_schema.py data/schema/hyprland-<ver>.json --predecessor data/schema/hyprland-<previous>.json -o <scratch>/hyprland-<ver>.diff.json` classifies every change: added / removed / renamed / retyped / range change / enum-map change / default change / new Section or subsection. It prints the count per class.
   - **Renames** come in two classes. `renamed` is only what the Overlay's `renamed_from` maps, so a first run on a new release has none. `rename_candidates` lists each removed and added pair with an identical description and default; the pair also stays under `removed` and `added`. Confirm each candidate against the release notes, set `renamed_from` in step 3, then re-run the tool: the confirmed pairs move to `renamed`.
   - A renamed Option is still compared for type, range, enum map and default under its new name.
   - The tool reads Generated schemas only, and classifies the fields above. These fields are not classes, so read them in the PR's schema diff: an Option's description, declaration order, `widget`, `refresh`, `device_overridable`, `getoption_key` and `curation_flags`, and the schema's `animation_leaves` (layer 4's completeness test checks those).
2. **Stub API diff** — `hl.meta.lua` old vs new: entity constructors and their arg tables, the `hl.dsp.*` dispatcher table, `BindOptions` fields, rule match props and effects, `HL.EventName`.
3. **Wiki diff** — `hyprwm/hyprland-wiki` `content/Configuring/**` at the matching point: re-run the restart-required regex (the `restart` overlay field is wiki prose only — nothing in source or IPC exports it), and note changed help anchors.
4. **Entity catalogue diff** — `src/hyprtweaker/engine/entities_catalog.py`, the hand-curated half of the Entity surface (#70). Nothing in CI covers it, so it is the one layer that rots in silence: re-probe the new version and compare. The animation leaves are not in this layer: step 1's `tools/gen_schema.py` records them into the schema from `hyprctl -j animations`, and the completeness test fails the build on a tree the catalogue cannot resolve (`SHIPPED_ANIMATION_LEAVES` is the fallback for a schema without the block, and the order the Animations dropdown offers leaves in: add a new leaf there at its place in the tree, or it sorts last). Check the `hl.device` key set, `GESTURE_DIRECTIONS`/`GESTURE_ACTIONS`, `PERMISSION_TYPES`/`PERMISSION_MODES` and the required-field rules against `Hyprland --verify-config` (a rejected key names itself); check `GESTURE_DIRECTION_COVERS` by re-running the direction-pair sweep. Step 1 already stands up a Hyprland of `<ver>`, so all of it runs in that session.
5. **Dispatcher catalogue diff** — `CATALOG` in `src/hyprtweaker/engine/dispatchers.py`, curated from what the compositor answers (#126, ADR-0007); a changed signature leaves a bind form that offers the wrong keys. From the repo root, against a nested Hyprland of `<ver>` only:

   ```sh
   env -u HYPRLAND_INSTANCE_SIGNATURE HARNESS_DRM_CARD=/dev/dri/card0 UPDATE_GOLDEN=1 \
     .venv/bin/pytest tests/integration/test_dispatcher_probe.py -m hyprland
   ```

   - The run writes the probed shapes to `tests/golden/dispatcher-probe-<ver>.json`; `diff` it against the previous version's file for the record diff. Commit the new record: the unit tier checks the catalogue against the newest record in `tests/golden/`.
   - The run's other tests are the catalogue diff. A drifted dispatcher fails by path with both shapes, for example `window.float: required keys ['window'] differ from the compositor's []` or `omits keys the compositor reads: ['action']`.
   - `test_the_unseen_keys_still_do_nothing` re-fires the keys the catalogue leaves out because nothing changed when they were fired (`window` on the group dispatchers, `layout_aware` on the fullscreen ones). It fails by path and key when one starts to act: give it an `ArgSpec` and an effect probe in `tests/integration/harness/dispatcher_probe.py`, so the next record holds it.
   - It needs `foot`. Name the file by path: a bare `tests/integration -m hyprland` also runs `test_ipc_live.py`.

Output: `hyprland-<ver>.diff.json` (machine) in a scratch directory, plus a human summary for the PR; the PR comment carries both. The diff is for the PR's reviewer and is not committed to `data/schema/`: the app reads nothing from it. Its *New in \<version\>* grouping reads the `added_in` stamp in the schema (step 1), and Retired detection reads the running Hyprland's live option names.

Done when every change in all five layers is classified — an unclassified change is a diff bug, not a skippable line.

## 3. Curate

Update `data/schema/overlay.json`:

- **Added option** → the mandatory tier: `widget`, `nullable`/`null_label`, `title`; plus `labels` / `known_values` / `range` / `depends_on` / `visibility` wherever the coverage heuristics flag the option (map-less small-int, `[a/b/c]` description, sentinel default, vec2/css_gaps/font_weight, font/monitor/regex/file strings).
- **Added option** → also its `group`, in the same release check: add it to a Group in `tools/overlay_groups.toml`, then run `tools/curate_overlay.py` (never edit `group` or `order` in `overlay.json`). The completeness test exempts an ungrouped option for the release that added it only, so an option left in *New in \<version\>* fails the next release's check.
- **Added option** → also its Row text, in the same release check: `tools/seed_overlay_help.py` adds the option to `tools/overlay_help.toml` with its upstream line in a comment (its `wiki:` line reads `(none)`, since the seed reads the static `docs/research/option-schema.coverage.json`: take the wiki text from layer 3's wiki diff); give it `help` (prose that replaces the upstream line, rules in `tools/overlay_help.py`) or a `skip` reason, then run `tools/curate_overlay.py`. `tests/unit/test_overlay_help.py` fails while an option is undecided.
- **Renamed** → `renamed_from` on the new name (the app migrates the user's value silently, Info notice), and the name moved in both tables, `tools/overlay_groups.toml` and `tools/overlay_help.toml`, before `tools/curate_overlay.py` runs.
- **Removed** → `deprecated_in: <ver>` on the old entry (kept — the Overlay is version-independent; the entry still serves older schemas in the support window).
- **Restart-list change** → update `restart` fields, hand-verified against the wiki prose.
- **Stub API changes** (new dispatcher, new match prop, new effect, changed arg table) → update the engine's typed tables. A **new entity kind** is out of this protocol's scope: open a `ready-for-human` issue for it and say so in the PR.
- **Entity catalogue changes** → update `entities_catalog.py` in the same PR. A leaf or field the app does not know is not a cosmetic gap: an unknown `hl.device` key is a hard error that takes the whole Module down, and a leaf the catalogue lacks is one the user cannot set. Unknown values already degrade to *shown, flagged* (ADR-0012's rule for Options, applied to Entities), so the PR is a curation update, never a rescue.
- **Dispatcher catalogue changes** → edit the entries layer 5 named in `dispatchers.py`, then rerun its command without `UPDATE_GOLDEN=1` until all four tests pass. A curated entry lists every key the probe saw act (read or changed state); a key fired with no effect stays off the form, with its verdict in the comment above the entry (the editor keeps a saved copy); a shape `ArgSpec` cannot state in full keeps the raw table, with a `free_form_reason` the bind editor shows the user as one plain sentence. Commit the new record.

Done when the curation tests pass against the new schema locally: `tests/unit/test_overlay_completeness.py`, `tests/unit/test_overlay_help.py` and `tests/unit/test_curate_overlay.py`.

## 4. Verify

- CI tier: overlay completeness + unit golden files + `Hyprland --verify-config` over written outputs.
- Recommended, non-blocking: the nested-Hyprland Harness (`-m hyprland`, with `HARNESS_DRM_CARD` set: `docs/agents/local-checks.md` § Harness tier) on `<ver>` over `tests/corpus/`. Harness failures do not block the PR — file each as its own issue and link them in the summary.

## 5. Ship

- Enforce the support window: `data/schema/` carries **latest + previous** only — delete older schema files (git history keeps them), and with each one its `tests/golden/dispatcher-probe-<that version>.json`.
- Open the PR, base `main` (`docs/agents/issue-tracker.md` § Open a PR): schema + overlay + engine-table updates, summary comment with the machine diff and its per-class counts, options still unplaced in *New in \<ver\>* groups, and any follow-up issues opened. Its body carries `Closes #<release-check issue>`.
- Once CI is green, add the `ready-to-merge` label. The owner merges, and the merge closes the release-check issue.
