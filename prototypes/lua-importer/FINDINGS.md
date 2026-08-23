# Prototype: evaluation-based Lua importer (wayfinder #30)

**Verdict: feasible and verifiably lossless.** A foreign `hyprland.lua` evaluated under a
recording `hl.*` stub captures cleanly into the model on every real-world file we have, the
captured model re-emits to Lua that the real Hyprland engine accepts, and re-importing the
emitted file reproduces the model exactly (fixpoint). ADR-0009's commitment stands.

## What was built (all throwaway)

- `runner.lua` — Lua 5.5 recording environment: full `hl.*` stub (declarative recorders,
  lazy nested `hl.dsp.*` action markers, submap tagging, query fns with canned answers,
  plugin namespaces that mirror engine truth), sandboxed `os`/`io`, Hyprland-style
  `require` (config-dir relative, dot/slash, `package.path` fallback, `searchpath`),
  function capture via `debug.getinfo` source ranges + recursive `debug.getupvalue`
  snapshots. Dumps the call stream as JSON.
- `import_lua.py` — driver + staging (fake `$HOME`/XDG for rice fixtures) + summary.
- `emit_model.py` — renders a captured model back to Lua (`gen.lua`) and fixpoint-compares
  two models. Its value renderer is, in effect, the writer's Lua-literal representation.
- `analyze_globals.py` — `luac5.5 -l` bytecode scan listing non-stdlib globals read by each
  extracted closure (the mechanical "extraction hole" detector).
- `run_all.py` — the full matrix; `fixtures/hyde/` — HyDE's upstream Lua port fetched at
  its release commit `b8cc6472` (the corpus predates it — worth promoting into
  `tests/corpus/` during spec).

## Results matrix

10 configs, three hand-written upstream Lua ports + all 7 prototype-#9 converter outputs
(our "exported output" proxy). Every row: **import 0 errors → emit → re-import → fixpoint
identical → `Hyprland --verify-config` ok.**

| source | calls captured | declarative | hybrid (closure in args) | script (`hl.on`/…) |
| --- | --- | --- | --- | --- |
| end-4 port (hand-written) | 377 | 368 (97.6%) | 7 | 1 |
| ML4W port (hand-written) | 137 | 136 (99.3%) | 0 | 1 |
| HyDE port (hand-written) | 165 | 157 (95.2%) | 5 | 3 |
| p9: end-4, hyde, jakoolit, ml4w, hyprv, local, default | 80–389 each | — | 0–3 | 0–18 |

Legacy share of hand-written files is tiny: end-4's `gen.lua` is 472 lines of which 8
blocks (~35 lines) are LEGACY/HYBRID extracts; everything else is editable model.

## Answers to the ticket's open questions

### 1. Sandboxing (`os`/`io`/`require`)

Two policies were implemented and measured:

- **block** (shell/io-write intercepted, benign stubs): sufficient for 9/10 configs —
  declarative ports make **zero** config-time use of shell, io writes, or `hl` queries.
- **record** (passthrough + logging): required for HyDE, which at config time uses
  `os.getenv` (70 uses), `io.popen` dir-existence tests (kills evaluation under block —
  `P.lib` nil at `hyde.lua:21`), `package.path` mutation + `package.searchpath`, custom
  `check_require`, and Hyprland's nonstandard `os.getpid`/`os.geteuid`.

Recommendation: **evaluate under block first; on failure, offer one consent-gated
passthrough run** — the file already executes on the user's compositor every reload, so
running it once at import adds no new exposure, and every shell/env/io touch is recorded
into the loss report. `os.exit` must always be trapped (HyDE libs call it); io *writes* can
always be swallowed (0 hits at config time anywhere). Auto-import on first run (ADR-0009)
should therefore be automatic only when block-evaluation succeeds.

### 2. Source-range extraction fidelity for closures

- `debug.getinfo` `linedefined`/`lastlinedefined` is exact for multi-line functions;
  single-line inline closures need a token-scan cut that is comment-, string-, and
  long-bracket-aware (all three variants occur in the corpus and each broke a naive scan:
  `--` inside `[[…]]`, keywords inside strings, `for … do` double-counting).
- **Upvalues close, not break, extraction**: literal upvalues re-materialize as `local`
  defs; function upvalues extract recursively (end-4's `zoomfunction`); table upvalues as
  literals (HyDE's `hs`). Loop-generated closures — same source range captured N times with
  different upvalue values (4 in end-4, 4 in HyDE) — bake correctly via per-instance
  materialization; the fixpoint proves equivalence.
- **The one real hole is globals**: a closure reading a non-stdlib global escapes
  source-range extraction. Measured: 2/9 HyDE scripts (both read the `hyde` meta-table);
  0/29 everywhere else. Mechanically detectable — `luac5.5 -l`, scan `GETTABUP _ENV` —
  so the importer can flag exactly these as **Needs review** in the loss report.

### 3. Editable model vs `legacy.lua`

95–99% of a hand-written file's calls land in the editable model; only whole `hl.on`
handlers and closure-valued actions land in legacy (≤8 small blocks per rice). One
structural caveat for the loss report: **evaluation flattens meta-config machinery**.
HyDE's toml-driven theming (config.toml → `hl.config`) bakes to its current values; after
import, switching a HyDE theme no longer re-themes Hyprland. That is ADR-0009's "baked and
reported" by design, but the report needs a Breakage-class line: "this config reads
external state (`config.toml`, shell) that the imported copy will not re-read."

### 4. Can the recording stub share code with the writer?

Yes — at the **value-model layer**, not the stub layer. The stub's captured normal form
(plain Lua tables/scalars + `{__dsp = name, args}` action markers + script refs) is exactly
what the writer's *Lua literal* representation consumes; the prototype's emitter (~60-line
`lua_value`) is the writer core and doubled as the fixpoint harness. The `getoption`-parse
and display representations stay writer/read-back-side only. Dsp markers round-trip as
structured `{name, args}` — the same shape as ADR-0007's typed dispatcher Action.

## Other findings with spec impact

- **Stub fidelity decides bake correctness for queryable surfaces.** `hl.plugin.<ns>` must
  be `nil` unless `hl.plugin.load` ran this reload: end-4's converted config guards
  `if hl.plugin.hyprbars ~= nil then …` — a truthy stub baked the wrong branch and produced
  a config the engine *rejects* (`unknown config key plugin.hyprbars.*`). Every canned
  query answer is recorded; any config-time query use → Needs review (0 occurrences in the
  whole corpus, so canned answers are a non-issue in practice).
- **Record argc explicitly.** `hl.config({})` (single empty table) and zero-arg calls are
  indistinguishable in the captured args value; HyDE hit this via `hl.config(theme_config)`
  with an empty theme.
- **`define_submap` evaluates its body immediately**; tagging binds with the active submap
  and regrouping on emission round-trips perfectly (end-4's 191-bind `global` submap).
- **`Hyprland --verify-config` is NOT side-effect-free.** It *executes* the Lua file with
  live bindings: `hl.exec_cmd` spawns for real, and the spawned process inherits
  `HYPRLAND_INSTANCE_SIGNATURE` — verifying the HyDE gen file ran its
  `hyprctl seterror '[HyDE] Hyprland does not detect colors!…'` against the user's live
  compositor (a persistent bar until `hyprctl seterror disable`). The importer's static
  gate must run verify with `HYPRLAND_INSTANCE_SIGNATURE` (and ideally the whole
  `XDG_RUNTIME_DIR` socket path) stripped from the environment so config-time spawns
  can't reach the live session.
- **The fixpoint + `--verify-config` combo is the importer's test harness**: fast (~0.3 s
  per config, no compositor), and it caught five real bugs in this prototype (offset-drift
  in comment stripping, long-bracket strings, `for…do` depth, named-function extraction,
  the plugin-namespace bake). Promote it alongside #16's nested-Hyprland harness.

## Files

Prototype code lives on the throwaway branch
[`prototype/lua-importer`](https://github.com/danielbaldwin47/hyprland-settings-gui/tree/prototype/lua-importer/prototypes/lua-importer)
(main keeps only this findings doc): `runner.lua`, `import_lua.py`, `emit_model.py`,
`analyze_globals.py`, `run_all.py`, `fixtures/hyde/` (upstream GPL-3.0 fixture, pinned
`b8cc6472`), `results/` (captured models + generated Lua).
