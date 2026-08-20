# ADR-0005: Config model & file layout (hyprtweaker)

**Status:** accepted — 2026-08-19

## Context

ADR-0002 decided app-managed generated Lua modules + `user.lua` escape hatch, leaving the concrete model and on-disk layout open. Constraints from research/prototypes:

- Reload resets everything; the file tree is the whole truth — omission = Hyprland default (`docs/research/lua-api-surface.md`).
- Hyprland watches the main file and every `require()`d file, but not directories; adding a module file triggers nothing until the entrypoint is rewritten (`docs/research/live-apply.md`).
- Each `require()`d module runs in its own pcall scope: a broken module's contents are silently absent while the rest loads — generated files must be whole and valid by construction.
- App-level rollback = restore the previous bytes of a module.
- Hyprland picks `hyprland.lua` over `hyprland.conf` when both exist, never loads both.
- Lua refuses `descriptions`' `toString()` text for gradients and css-gaps; tables required. 19 of 353 options have three distinct representations (display, Lua literal, `getoption` parse), and the `getoption` type key is engine-dependent (`custom` under hyprlang vs `gradient`/`css`/`font_weight` under Lua) (#8 findings).
- The user rejects "manager over your dots" positioning: the app is one more tool in your box.

## Decision

**App name: `hyprtweaker`** (collision-checked; `hyprgui`, `hyprconfig`, `hyprsettings` all taken). User-facing copy never says "manage"/"managed" — tweak wording throughout (gnome-tweaks precedent).

### Layout

```
~/.config/hypr/
├── hyprland.lua            # generated entrypoint: header + require list; app-owned
├── hyprland.conf           # untouched; never moved or deleted
├── hyprtweaker/            # App dir — app-owned, rewritten freely
│   ├── manifest.json       # app version, schema version (Hyprland ver), module list
│   │                       #   + content hashes, migration provenance (date, source hash)
│   ├── vars.lua            # imported hyprlang $variables as a table; modules require it
│   ├── options/<section>.lua   # one per Section; hl.config({...}) only
│   ├── monitors.lua  binds.lua  window_rules.lua  layer_rules.lua
│   ├── workspace_rules.lua  animations.lua  gestures.lua  devices.lua
│   ├── env.lua  permissions.lua  autostart.lua   # one per Entity type
│   └── legacy.lua          # imported constructs the GUI can't represent;
│                           #   written once at import, never rewritten; GUI lists read-only
└── user.lua                # escape hatch; never touched; required LAST
```

`options/` subdir exists because `binds` is both a Section and an Entity type — flat naming collides.

### Entrypoint & require order

Header comment (generated-by + version stamp), then requires: `vars` → `options/*` (alphabetical; `hl.config` merges per leaf so order across disjoint keys is irrelevant) → entity modules → `legacy` → `user` **last**. The entrypoint is regenerated whenever the module set changes (directories aren't watched).

`user.lua` last means the escape hatch **overrides** the GUI (later wins for `hl.config`; binds append). After each reload the app compares `get_config`/`getoption` against its model and badges diverging options as "overridden in user.lua" instead of silently losing writes.

### Unset vs set

The model is tri-state per option: **unset** (not emitted; Hyprland default applies because reload resets all values) vs **explicitly set** (always emitted, even when equal to the current default, so the choice survives upstream default changes). Import marks every key present in the `.conf` tree as set.

### Value representations

Each option type carries up to three representations: display text, Lua literal, and the parse of what `getoption` returns. Gradients and css-gaps must be emitted as tables, never `toString()` text.

### Backups & history

- **Migration:** the `.conf` tree stays in place untouched — rollback is deleting `hyprland.lua`. Additionally a full copy of `~/.config/hypr/` goes to `$XDG_STATE_HOME/hyprtweaker/backups/<timestamp>/`. Never inside the hypr dir.
- **Ongoing:** change journal + per-write module snapshots in `$XDG_STATE_HOME/hyprtweaker/`, pruned (~100 changes / 30 days). Plain files, no git — savvy users can init their own repo. Full-tree backup again before schema-version upgrades.
- **Hand-edit detection:** manifest content hashes; on mismatch the app warns and offers adopt-into-legacy or overwrite.

## Consequences

- Rollback and partial-failure blast radius are per-file; diffs stay small.
- The GUI stays honest under `user.lua` overrides at the cost of a post-reload read-back pass.
- Renaming the app later means a config-dir migration — the name is load-bearing now.
- `legacy.lua` gives imported-but-uneditable constructs a durable home visible in the GUI.

## Alternatives considered

- Neutral `managed/` dir name — rejected for "handing over ownership" connotation.
- `user.lua` required first (GUI always wins) — rejected: an escape hatch that can't override isn't one.
- Git repo for history — rejected: structured undo (ADR-0003) already exists; nested repos annoy dotfile-repo users.
