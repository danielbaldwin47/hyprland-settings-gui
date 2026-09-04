# Gate

Read by the agent about to land a change, at the moment the implementation is green and before `/code-review`; also by whoever wonders why `tools/gate check` said `fail`.

One tier, one command:

```sh
tools/gate check
```

It runs, in order, `fmt` (ruff format, checking not writing), `lint` (ruff check), `types` (mypy over the Engine and the toolkit-free modules `pyproject.toml` lists), `unit` (`tests/unit` + `tests/static`, with `HYPRTWEAKER_REQUIRE_LUAC=1` so a missing `luac` fails instead of skipping), `ui` (`tests/ui`, with `HYPRTWEAKER_REQUIRE_UI=1` so a dead GTK fails instead of skipping), then `cite` and `usage`. Each step's output lands in `target/gate/<step>.log`; only the failing step's log is printed. The last line is one of:

```
gate check: pass
gate check: fail (<step>)
```

`tools/gate cite` holds every backticked path in `CLAUDE.md` and `docs/agents/*.md` to a file on disk (`gate cite: pass` / `gate cite: fail (<path>)`); `tools/gate usage` holds `tools/gate --help` and this file to naming every subcommand dispatched (`gate usage: pass` / `gate usage: fail (<name>)`). `check` runs both last, so a doc that drifts from its tool is a failing step.

The same suites run in CI (`.github/workflows/ci.yml`) with a skip ceiling, and under `meson test` for packaging (`tests/meson.build`); the integration tier (`tests/integration`, `-m hyprland`, a nested compositor) is on demand and not part of `check` (ADR-0011 §Testing).
