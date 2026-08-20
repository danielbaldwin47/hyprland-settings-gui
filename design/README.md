# Design canvas — app shell & information architecture

Asset for [issue #7](https://github.com/danielbaldwin47/hyprland-settings-gui/issues/7).

Canvas: <https://claude.ai/code/artifact/1894736d-2b84-416f-8784-d905eba497b4>

## Artboards

| Artboard | What it shows |
| --- | --- |
| `Sections` | Three IA directions — A task-first (recommended), B mirror-the-config, C few-doors-and-search — with the full Direction A mapping of 21 Sections + entities onto 19 Pages. |
| `Main` | The shell: `NavigationSplitView` sidebar + a generated Decoration page, help popover, per-row reset, undo toast. |
| `RowTypes` | The Row contract: one row per value type (counts from `docs/research/option-schema.md`) and every state a Row can be in. |
| `Search` | Search in the sidebar header — options, entities and actions — with the target row flashed on the page behind. |
| `Keybinds` | Shortcuts list with conflict marking, plus the capture dialog (Adwaita/GNOME-Settings capture rules). |
| `WindowRules` | Ordered rule list with match + effects, and the rule editor (match group / then-do-this group). |
| `Monitors` | Arrangement canvas with snapping, per-monitor settings, confirm-or-revert alert. |
| `Migration` | The five wizard steps (detect → preview → back up → switch & verify → keep or roll back) and the everyday banner states. |

## Editing

Artboards are generated. Edit the fragments, not the `.dc.html` files:

```
design/frag/<Name>.html     body fragment (may start with its own <style> block)
design/partials/sidebar.html  the shared sidebar; `<!--#sidebar <key>-->` selects a row
design/_adw.css             shared libadwaita-ish tokens and widget CSS
design/sizes.json           artboard frame sizes
design/canvas.json          canvas layout, titles, sticky notes
```

Rebuild and re-publish:

```bash
node design/build.mjs
node "<design skill dir>/seed-canvas.mjs" \
  --template "<design skill dir>/payload.template.html" \
  --out /tmp/hyprland-settings-shell.html --title "Hyprland Settings Shell" \
  --artboard design/Main.dc.html --artboard design/Sections.dc.html \
  --artboard design/RowTypes.dc.html --artboard design/Search.dc.html \
  --artboard design/Keybinds.dc.html --artboard design/WindowRules.dc.html \
  --artboard design/Monitors.dc.html --artboard design/Migration.dc.html \
  --canvas design/canvas.json
```

Then publish that file to the same artifact URL.

Colours, radii and metrics come from libadwaita's named colours (`#fafafb` window,
`#ebebed` sidebar, `#3584e4` accent, 12px boxed lists, 600px `Adw.Clamp`); the widget
choices come from `docs/research/libadwaita-patterns.md`.
