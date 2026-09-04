# ADR-0007: Keybinds editor model

**Status:** accepted — 2026-08-19

Read by the agent about to change keybinds editor model, before the first edit; the Status line says what is on `main` now.

## Context

Binds are the largest Entity class in every corpus rice (89–197 per config) and the riskiest to edit blind. Constraints from research/prototypes:

- `hl.bind` is positional: `(keys: string, dispatcher|function, opts?)`. Keys are one `+`-separated string; binds **append** — duplicates are legal and all fire in file order; identity is position, not key (`docs/research/lua-api-surface.md` §4).
- `hyprctl binds` cannot see `code:N` binds (`parseKeyString` puts keycodes in `sMkKeys`; IPC reports `key:"", keycode:0`) — read-back over IPC is structurally unreliable (`prototypes/importer/FINDINGS.md` §4.2).
- Dead keysym names (`Enter`, `XF86Lock`) are silently inert under hyprlang but a **hard config error** under Lua; `catchall` cannot carry modifiers; the `{ mouse = true }` flag is not read by v0.56.2 (`FINDINGS.md` §4.4–4.5, lua-api-surface Q1).
- `HL.BindOptions` includes `auto_consuming`, which the stub omits but the code parses (lua-api-surface Q7); `click`/`drag` imply `release`; `long_press`/`release` are incompatible with `repeating`.
- GDK keyvals are xkb keysyms, so a GTK capture dialog can emit Hyprland key names directly; Hyprland implements the shortcuts-inhibit protocol (`docs/research/libadwaita-patterns.md` §3).
- User principle (grilling on #12): **no proprietary state** — the config files are the interface; hand edits to them must show up in the app.

## Decision

### Model owns bind state; `binds.lua` is round-trippable

The app's model is the source of truth for binds, **write-only over IPC**: never reconstruct binds from `hyprctl binds` (blind to `code:N`); post-reload verification for binds is `configerrors` only.

Hand-editability is preserved by the *file*, not IPC: `binds.lua` is emitted in a **canonical, machine-parseable form** the app can also read back. On external change (manifest hash mismatch / file watch), the app re-parses `binds.lua`; constructs the parser can't represent are surfaced as read-only rows (offered adopt-into-`legacy.lua`), never silently dropped or overwritten.

### Bind entity

An ordered entry: **Trigger + Action + flags + optional description + owning Submap**.

- **Trigger** (structured, never a raw string internally): mods list (canonical spellings `SHIFT CAPS CTRL ALT MOD2 MOD3 SUPER MOD5`) + exactly one of: xkb keysym name, `code:N`, mouse button (`mouse:272`…), wheel (`mouse_up/down/left/right`), `switch:[on:|off:]<name>`, or `catchall` (submap-only; parser rejects mods on it). Emitted as the canonical `"SUPER + SHIFT + Q"` string.
- **Action**: a typed dispatcher call — `(namespace path, args table)` mirroring the `hl.dsp.*` schema; serialise the call expression, never runtime state. Arg validation mirrors construction-time checks as form validation. Function-valued actions belong to `user.lua` and render read-only.
- **Flags**: the `HL.BindOptions` set **including `auto_consuming`**, with constraints enforced in the editor (`click`/`drag` ⇒ `release`, mutually exclusive; `long_press`/`release` × `repeating`). **`mouse = true` is never emitted** — inert; `drag()`/`resize()` dispatchers are self-contained.
- **Identity is position.** Order is preserved and user-reorderable; duplicates are legal.
- **Submap**: first-class entity (name, optional reset-target, ordered binds), emitted as nested `hl.define_submap`. A submap no bind enters is badged unreachable.

### Add flow: two doors

"**Run command**" (`hl.dsp.exec_cmd`: command entry + advanced window-rules table) and "**Hyprland action**" (dispatcher picker grouped by namespace, generated arg forms). Exec is the majority bind type in every corpus rice; it is not buried behind 71 dispatchers.

### Capture

GNOME Settings pattern: `Adw.Dialog` + capture-phase `Gtk.EventControllerKey` + `inhibit_system_shortcuts`, **plus** HyprMod-style held-modifier tracking and `Gtk.GestureClick` so modifier-only and mouse-button binds capture. Validation is looser than GNOME's: modifier-less binds allowed; bare letters warn, never block; names with no xkb keysym **are blocked** (hard error under Lua) with a hint. Manual text entry with live xkb validation as fallback. `code:N` displays as "key code N" plus current-layout keysym hint.

### Conflicts: warn, navigate, never block

Conflict = same (submap, modmask, trigger) among enabled binds (`submap_universal` conflicts against all submaps). Duplicates are legal Hyprland semantics, so the app never blocks or auto-removes. The conflict surface must carry the *other* bind's identity (description/action, combo, submap) inline and offer: **jump to it in context** (navigate + flash the row), **rebind it** (open capture on the other bind directly), or **disable it** — not just "there is a conflict". Saved duplicates keep a warn badge on both rows stating fire order.

### Imported edge cases

Dead-keysym binds arrive commented out from the Importer → disabled row, error badge, re-capture affordance. `catchall`-with-mods and multi-key `binds` (`A&B`) approximations are badged "imported approximately". Multi-key binds are read-only with raw-text editing — no capture UX (0 uses in corpus, mapping only approximate).

### Placement

One "Keyboard" Page in the Tasks view: root binds, then one group per Submap, per-device binds via the `device` flag on the row.

## Alternatives considered

- Reading bind state back via `hyprctl binds` — rejected: structurally blind to `code:N` (common in corpus for layout-independent number rows).
- Single dispatcher picker with exec inside — rejected: hurts the dominant case.
- Blocking conflicts (GNOME behaviour) — rejected: duplicate binds all fire by definition in Hyprland; blocking would forbid a legal, used pattern.
- Full multi-key (`&`) editor — rejected: rare, approximate under Lua, capture UX disproportionate.
