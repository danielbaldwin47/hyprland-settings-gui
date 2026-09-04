# hyprtweaker

Read by a person landing on the repo, before running or contributing; agents start at `CLAUDE.md`.

A GUI settings app for Hyprland that reads and writes the new Lua config (`hl.*` API, Hyprland ≥ 0.56): it migrates an existing hyprlang `.conf` tree into app-owned Lua modules, exposes the whole declarative config surface as generated, curated GTK4/libadwaita pages, and applies changes instantly with read-back confirmation and undo.

## Spec

The product spec is [issue #48](https://github.com/danielbaldwin47/hyprland-settings-gui/issues/48); every decision in it is backed by an ADR in `docs/adr/`. `CONTEXT.md` is the vocabulary.

## Run

```sh
PYTHONPATH=src python3 -m hyprtweaker      # dev loop; meson is the canonical build (ADR-0011)
```

## Check

```sh
python3 -m venv --system-site-packages .venv && .venv/bin/pip install ruff mypy pytest
tools/gate check
```

`tools/gate check` ends in `gate check: pass` or `gate check: fail (<step>)`; see `docs/agents/gate.md`.

## Layout

`src/hyprtweaker/engine/` is the UI-free half (schema, model, importer, writer, apply, IPC, state); `src/hyprtweaker/ui/` is the GTK layer; `data/schema/` carries the per-Hyprland-version Generated schema and the hand-curated Overlay; `tests/` has the unit, static, UI and integration tiers. License: see `LICENSE`.
