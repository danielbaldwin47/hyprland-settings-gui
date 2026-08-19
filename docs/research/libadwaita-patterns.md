# Research: libadwaita settings-app patterns, prior art, packaging

Resolves GitHub issue #6 (part of #1). Date: 2026-08-19.

**Verified environment (this box):** libadwaita **1.9.3**, GTK **4.22.4**, python-gobject 3.56.3, meson 1.12.0, Hyprland 0.56.2 (`pacman -Q`, `Adw.get_*_version()`, `hyprctl version`). `blueprint-compiler` and `flatpak` are **not** installed (`pacman -Q` → not found); `blueprint-compiler` 0.22.2 is in `[extra]`. Every Adw/GTK class named below was confirmed present in `/usr/share/gir-1.0/Adw-1.gir` / `Gtk-4.0.gir` and instantiated once from Python (`Adw.SwitchRow`, `SpinRow`, `ComboRow`, `EntryRow`, `ExpanderRow`, `ButtonRow`, `ShortcutLabel`, `ShortcutsDialog`, `WrapBox`, `ToggleGroup`, `NavigationSplitView`, `ToolbarView`, `ToastOverlay`, `Banner`, `StatusPage`, `PreferencesDialog`, `AlertDialog`, `Gtk.ColorDialogButton`, `ListView`, `DragSource`, `DropTarget`, `EventControllerKey`, `DrawingArea`, `Fixed`, `SearchEntry`, `GestureDrag`, `Scale`) without error.

Upstream doc versions used: libadwaita "main" docs currently render 1.10.rc, GTK docs 4.23.3 — "since" versions are quoted so 1.9 / 4.22 gating is explicit.

Hyprland value types this maps onto (wiki `Configuring/Basics/Variables.md`, hyprland-wiki repo): `int`, `bool`, `float`, `color`, `vec2` (`{ 20, 20 }`), `str`, `gradient` (`{ colors = {"rgba(...)", ...}, angle? = 45 }` or a single color), `font_weight` (100–1000 or presets), `css_gaps` (an integer, or `{ top?, left?, right?, bottom? }`). Local `hyprctl -j descriptions` (353 options): 174 bool, 75 int with min/max, 30 int with `map` (enum), 13 float with range, 52 str (colours/gradients as strings, e.g. `general:col.inactive_border` default `ff444444 0deg`), 6 list (vec2 e.g. `decoration:shadow:offset` `[0, 0]`), 3 str gaps (`general:gaps_in` = `5 5 5 5`).

---

## 1. Value type → widget table

Legend: **Adw** = libadwaita 1.9.3, **Gtk** = GTK 4.22.4. All are verified installed. Column "since" is the libadwaita/GTK version that introduced the class or property.

| Value type | Widget (verified) | Since | Notes / bindings | Source |
|---|---|---|---|---|
| **bool** | `Adw.SwitchRow` (`active`, watch `notify::active`; row click toggles) | Adw 1.4 | Use `Gtk.Switch` in an `ActionRow` suffix only if a custom subtitle widget is needed. | [class.SwitchRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.SwitchRow.html) |
| **int (min/max)** | `Adw.SpinRow.new_with_range(min, max, step)`; `digits=0`, `snap-to-ticks`, `numeric` | Adw 1.4 | For wide ranges (0–100 %) optionally a `Gtk.Scale` in an `ActionRow` suffix with `add_mark()`; SpinRow implements `Gtk.Editable` and has `output`/`input` signals for unit suffixes ("px", "ms") — confirmed via `GObject.signal_lookup`. | [class.SpinRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.SpinRow.html), [signal.SpinRow.output](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/signal.SpinRow.output.html) |
| **float (min/max)** | `Adw.SpinRow` with `digits=2`, `climb-rate` set; for 0.0–1.0 opacity/dim values a `Gtk.Scale` (`draw-value`, `digits`, `set_format_value_func`) inside `Adw.ActionRow` reads better | Adw 1.4 / Gtk 4 | Scale gives direct manipulation for "feel" values (opacity, sensitivity −1..1); SpinRow for precise (rounding_power). | [class.Scale](https://docs.gtk.org/gtk4/class.Scale.html) |
| **string** | `Adw.EntryRow` (`show-apply-button=True` → `apply` signal; also `entry-activated`); `input-purpose`, `add_prefix/add_suffix` | Adw 1.2 | With instant-apply (ADR-0003) the apply button is the natural "commit" affordance so keystrokes don't cause a Hyprland reload each. `Adw.PasswordEntryRow` exists but no Hyprland option is a secret. | [class.EntryRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.EntryRow.html) |
| **enum (`map`)** | `Adw.ComboRow` + `Gtk.StringList`; `selected` index ↔ map value; `enable-search` (1.4) + `search-match-mode` (1.6) for long lists (layouts, xkb layouts); `use-subtitle` needs `expression` | Adw 1.0 | `map` in `descriptions` is a list of `{name: value}` — keep an ordered list of `(label, value)` and translate index. | [class.ComboRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ComboRow.html) |
| **small enum (≤4 values)** | `Adw.ToggleGroup` (+ `Adw.Toggle` name/label/icon; `active-name`) in an `ActionRow` suffix | Adw 1.7 | Good for `resize_corner`, on/off/auto tri-states; `.flat`/`.round` styles. | [class.ToggleGroup](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ToggleGroup.html) |
| **color** | `Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True))` as `ActionRow` suffix; watch `notify::rgba`; convert `Gdk.RGBA` ↔ `rgba(rrggbbaa)` (`Gdk.RGBA.parse` accepts `#rrggbbaa`; `to_string()` yields `rgba(r,g,b,a)` decimal). **libadwaita has no colour row** (index search finds only `AccentColor`/`ColorScheme`). `Gtk.ColorButton`/`ColorChooser*` are deprecated since 4.10. | Gtk 4.10 | Show a colour swatch + hex label in the row subtitle; `.monospace`. | [class.ColorDialogButton](https://docs.gtk.org/gtk4/class.ColorDialogButton.html), [class.ColorDialog](https://docs.gtk.org/gtk4/class.ColorDialog.html) |
| **gradient + angle** | `Adw.ExpanderRow` titled by the option; header suffix = a small `Gtk.DrawingArea` swatch (`Gtk.Snapshot.append_linear_gradient` or cairo) previewing the gradient; nested rows: one `ActionRow` per stop with `ColorDialogButton` suffix + remove button, an `Adw.ButtonRow` "Add colour stop" (1.6), and an `Adw.SpinRow` "Angle" (0–360, `output` signal appends "°"). Single-colour case = 1 stop, angle hidden. | Adw 1.6 / Gtk 4.10 | HyprMod does exactly this with `Gtk.ColorDialogButton` (`hyprmod/ui/options/color.py`); hyprset had `ColorExpanderRow.py`. | [class.ExpanderRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ExpanderRow.html), [class.ButtonRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ButtonRow.html) |
| **vec2** | `Adw.ActionRow` with two linked `Gtk.SpinButton`s in a `Gtk.Box` (`.linked`) as suffix, or an `ExpanderRow` with two `SpinRow`s ("X", "Y") when the row needs its own description | Adw 1.4 | e.g. `decoration:shadow:offset`. | [style-classes `.linked`](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/style-classes.html) |
| **css_gaps (1/2/4 values)** | `Adw.ExpanderRow` with `show-enable-switch`-style toggle replaced by an `Adw.ToggleGroup` in the header suffix: **Uniform / Per-side**. Uniform → one `SpinRow`; Per-side → four `SpinRow`s (Top, Right, Bottom, Left) plus a "link" toggle button. Writer emits an int for uniform, `{ top=, right=, bottom=, left= }` for per-side (Lua form per wiki). | Adw 1.7 | Two-value CSS shorthand collapses to per-side in the UI; the writer may re-collapse. | wiki `Variables.md` (`css_gaps`) |
| **font_weight** | `Adw.ComboRow` of the presets + an "Custom…" entry revealing a `SpinRow` 100–1000 | Adw 1.0 | | wiki `Variables.md` |
| **keyboard shortcut** | Display: `Adw.ShortcutLabel` (`accelerator`, `disabled-text`) — **Gtk.ShortcutLabel is deprecated since 4.18**. Capture: an `Adw.Dialog` with a `Gtk.EventControllerKey` in `CAPTURE` phase (see §3). | Adw 1.8 / Gtk 4 | `Adw.ShortcutsDialog` (1.8) is display-only (app's own shortcuts), not an editor. | [class.ShortcutLabel](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ShortcutLabel.html), [class.ShortcutsDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ShortcutsDialog.html), [gtk4 class.ShortcutLabel](https://docs.gtk.org/gtk4/class.ShortcutLabel.html) |
| **list of rules / binds (ordered, reorderable)** | `Gtk.ListBox` (`.boxed-list`, selection none) bound to a `Gio.ListStore` via `Adw.PreferencesGroup.bind_model()` (1.8) or `Gtk.ListBox.bind_model`; each row an `Adw.ActionRow`/`ExpanderRow` with a drag-handle prefix (`list-drag-handle-symbolic`). Reorder = `Gtk.DragSource` (`prepare` → `Gdk.ContentProvider.new_for_value(index)`, `drag-begin` → `set_icon(Gtk.WidgetPaintable)`) on each row + `Gtk.DropTarget.new(GObject.TYPE_UINT, Gdk.DragAction.MOVE)` on the list (`drop(value,x,y)` → `get_row_at_y`, `drag_highlight_row`). **`Gtk.ListBox` has no built-in reorder API**; **`Gtk.ListView` cannot be a boxed list** ("at the moment", boxed-lists guide). Use `Gtk.ListView` + `SignalListItemFactory` only for very long flat lists (`.rich-list`). | Adw 1.8 / Gtk 4 | Filtering: `Gtk.SearchEntry` (`search-changed`, `key-capture-widget`) + `Gtk.FilterListModel`. | [class.ListBox](https://docs.gtk.org/gtk4/class.ListBox.html), [class.DragSource](https://docs.gtk.org/gtk4/class.DragSource.html), [class.DropTarget](https://docs.gtk.org/gtk4/class.DropTarget.html), [boxed-lists](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/boxed-lists.html), [section-list-widget](https://docs.gtk.org/gtk4/section-list-widget.html) |
| **monitor arrangement canvas** | Custom `Gtk.Widget` subclass overriding `do_snapshot()` (or `Gtk.DrawingArea.set_draw_func` with cairo) drawing scaled monitor rects from `hyprctl -j monitors` (`x,y,width,height,scale,transform`), with `Gtk.GestureDrag` (`drag-begin/update/end` offsets) + `Gtk.GestureClick` for selection, edge snapping in code. Alternative: `Gtk.Fixed` with one button per monitor moved via `Fixed.move()` (nwg-displays, GTK3) — works but GTK docs discourage `Fixed` for layout; Monique's GTK4 `DrawingArea`+`GestureDrag` (`src/monique/canvas.py`) is the cleaner reference. | Gtk 4 | Pair with a `PreferencesGroup` of per-monitor rows (mode `ComboRow`, scale `SpinRow`, transform `ComboRow`, VRR `SwitchRow`) and a confirm-or-revert countdown (`Adw.AlertDialog` + `GLib.timeout_add_seconds`). | [class.DrawingArea](https://docs.gtk.org/gtk4/class.DrawingArea.html), [class.GestureDrag](https://docs.gtk.org/gtk4/class.GestureDrag.html), [class.Fixed](https://docs.gtk.org/gtk4/class.Fixed.html), [vfunc.Widget.snapshot](https://docs.gtk.org/gtk4/vfunc.Widget.snapshot.html) |
| **command / exec entry** | `Adw.EntryRow` (`input-purpose` FREE_FORM, `.monospace`, apply button); prefix = terminal icon; suffix = a `Gtk.MenuButton` with recently used commands / `.desktop` app picker (caelestia-settings scans `.desktop` files for class autofill). Multi-line scripts: `Gtk.TextView` with `.inline`/`.card` (has built-in undo). | Adw 1.2 | Autostart list = reorderable list above. | [class.EntryRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.EntryRow.html), [class.TextView](https://docs.gtk.org/gtk4/class.TextView.html) |
| **tags / flags (bind flags, rule flags)** | `Adw.WrapBox` (1.7) of toggle-style chips (`Gtk.ToggleButton.pill`) inside an `ActionRow`/`ExpanderRow` | Adw 1.7 | Bind flags `locked/release/repeating/...` per wiki `Binds.md`. | [class.WrapBox](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.WrapBox.html) |
| **read-only value / diagnostics** | `Adw.ActionRow` with `.property` (title dimmed, subtitle emphasised) and `subtitle-selectable` | Adw 1.1 | e.g. "Hyprland version", "instance signature". | [class.ActionRow](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ActionRow.html) |
| **per-option reset** | Suffix `Gtk.Button` (`edit-undo-symbolic`, `.flat`, `.circular`) shown only when value ≠ default (`Gtk.Revealer` or `visible` binding) — the GNOME Settings keyboard row pattern (`cc-keyboard-shortcut-row.c` reset button in a revealer bound to `is-value-default`) | Gtk 4 | Instant apply + reset per ADR-0003. | g-c-c `panels/keyboard/cc-keyboard-shortcut-row.c` |

Style-class notes (verified in [style-classes.html](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/style-classes.html)): `.dim-label` is deprecated since 1.7 → use `.dimmed`; `.boxed-list-separate` ≡ `PreferencesGroup.separate-rows` (1.6); `.numeric` for tabular figures; `.error/.warning/.success` for validation on entries.

---

## 2. App shell recommendation

**Shape:** `Adw.ApplicationWindow` → `Adw.ToastOverlay` → `Adw.NavigationSplitView` (sidebar `Adw.NavigationPage` with `Adw.ToolbarView` + header + `Gtk.ListBox.navigation-sidebar` of Sections; content `Adw.NavigationPage` with `ToolbarView` (`top-bar-style = FLAT`, the documented recommendation for windows with sidebars and for `PreferencesPage` content) hosting one `Adw.PreferencesPage` per Section, each with `PreferencesGroup`s of the rows from §1). Add an `Adw.Breakpoint` (`max-width: 400sp` → `split_view.collapsed = True`) so it works on the T480 at half-width; docs example uses exactly this. Sources: [class.NavigationSplitView](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.NavigationSplitView.html), [enum.ToolbarStyle](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/enum.ToolbarStyle.html), [adaptive-layouts](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/adaptive-layouts.html). Adw 1.9 also adds `Adw.Sidebar`/`Adw.ViewSwitcherSidebar` (`mode = page` under the breakpoint) — nice-to-have, not required.

**Why not `Adw.PreferencesDialog` as the main shell:** it is a *dialog* (must be presented on an `Adw.Window`, not resizable, "never larger than parent"), and its search only substring-matches `PreferencesRow:title` and `ActionRow:subtitle` (verified in `adw-preferences-dialog.c` `filter_search_results`) — it does not index entity lists (binds/rules/monitors). Use `PreferencesDialog` for the app's *own* preferences (managed-dir location, backup policy) and for the migration wizard's sub-pages (`push_subpage`). Sources: [class.PreferencesDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesDialog.html), [class.Dialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.Dialog.html); `PreferencesWindow` is deprecated since 1.6 (GIR confirms).

**Search:** own implementation — `Gtk.SearchBar` (`key-capture-widget = window`, so typing anywhere starts a search) + `Gtk.SearchEntry` in the sidebar header; results are a flat `Gtk.ListBox` over the Schema (option name, description, section, plus entity titles), activating a result navigates to the page and scrolls/flashes the row (`PreferencesPage.scroll_to_top()` exists; per-row scroll needs `Gtk.Viewport.scroll_to`/`Gtk.Widget.grab_focus`). This mirrors HyprMod's global search (`hyprmod/ui/search.py`) and reproduces what `PreferencesDialog` search does, but over the schema. Sources: [class.SearchBar](https://docs.gtk.org/gtk4/class.SearchBar.html), [class.SearchEntry](https://docs.gtk.org/gtk4/class.SearchEntry.html).

**Toasts for undo (ADR-0003):** the documented libadwaita undo pattern — keep one `Adw.Toast` reference, `priority = HIGH`, `button-label = "_Undo"`, `action-name = "win.undo"` (or `button-clicked`), default `timeout` 5 s (toasts pause while hovered/focused), and re-`add_toast()` the same toast to batch rapid changes ("Changed general:gaps_in", then "3 changes"). Connect `dismissed` to drop the undo record if the model keeps a bounded stack. Use `Adw.Banner` at the top of a `PreferencesPage` (`banner` property, 1.7) for persistent states: "Config has errors — Hyprland is running the last good config" with `button-label = "Show"`, and for "You are editing hyprland.conf; switch to Lua" during migration. `Adw.AlertDialog` (`choose()`/`choose_finish()` async, `set_response_appearance(DESTRUCTIVE)`) for the confirm-or-revert countdown after monitor changes and for "Replace conflicting bind?". `Adw.StatusPage` for empty lists ("No window rules yet") and for "Hyprland not running" (`icon-name`, `description`, `child` = button). Sources: [class.Toast](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.Toast.html), [class.ToastOverlay](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ToastOverlay.html), [class.Banner](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.Banner.html), [class.AlertDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.AlertDialog.html), [class.StatusPage](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.StatusPage.html).

**Entities (binds, rules, monitors, autostart):** each Entity page = `Adw.PreferencesPage` with a `PreferencesGroup` whose `header-suffix` is an "Add" button, rows bound to a `Gio.ListStore` (`PreferencesGroup.bind_model`, 1.8), reorder via DnD (§1), edit in an `Adw.Dialog` (`presentation-mode = AUTO` → bottom sheet when narrow) with a nested `PreferencesPage`. Sources: [class.PreferencesGroup](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.PreferencesGroup.html), [enum.DialogPresentationMode](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/enum.DialogPresentationMode.html).

---

## 3. Shortcut capture (distilled from GNOME Settings)

Source: gnome-control-center `main`, `panels/keyboard/cc-keyboard-shortcut-editor.c` / `.blp`, `cc-keyboard-manager.c`, `keyboard-shortcuts.c` (https://gitlab.gnome.org/GNOME/gnome-control-center/-/tree/main/panels/keyboard). Note: g-c-c's `.ui` files are now Blueprint (`.blp`).

1. **Where the controller lives.** The editor is an `Adw.Dialog`; a `Gtk.EventControllerKey` with `propagation-phase: capture` is attached to the dialog itself (`.blp`: `EventControllerKey { propagation-phase: capture; key-pressed => $on_key_pressed_cb(template); }`). No `grab_focus`, no keyboard grab. Capture phase means it beats the entry rows/buttons inside the dialog ([enum.PropagationPhase](https://docs.gtk.org/gtk4/enum.PropagationPhase.html)).
2. **Capture-mode gating** = "the visible stack page is the *edit* page" (`get_shortcut_editor_page(self) == PAGE_EDIT`), otherwise the handler returns `GDK_EVENT_PROPAGATE`. Once a valid combo arrives the stack flips to the standard/custom page, which implicitly ends capture.
3. **Wayland inhibit.** Before capturing, `gdk_toplevel_inhibit_system_shortcuts(GDK_TOPLEVEL(surface), NULL)` on the parent window's surface (`gtk_widget_get_native → gtk_native_get_surface`); `gdk_toplevel_restore_system_shortcuts` on Escape/Backspace/valid capture/unrealize. This is the `zwp_keyboard_shortcuts_inhibit_manager_v1` protocol; **Hyprland implements it** (`src/protocols/ShortcutsInhibit.cpp` in hyprwm/Hyprland), so Super-combos reach the app while capturing. Without it, the compositor eats `SUPER+…`.
4. **Normalisation** (`normalize_keyval_and_mask`): `explicit = gtk_accelerator_get_default_mod_mask() | GDK_SHIFT_MASK`; `used = state & explicit`; re-translate the *keycode* with `gdk_display_translate_key(dpy, keycode, state & ~explicit, group, …)` to get the unshifted keyval (and once more with Shift to recover digits on AZERTY); map `ISO_Left_Tab→Tab`, `Sys_Req+Alt→Print`; then strip `GDK_LOCK_MASK`. Keep the keycode too (`CcKeyCombo {keyval, keycode, mask}`) for display fallback.
5. **Special keys:** unmodified `Escape` → cancel and close; unmodified `BackSpace` → clear/disable the binding; a modifier-only press is recorded with `custom_is_modifier = TRUE` so `accel_valid = is_valid_binding && is_valid_accel && !custom_is_modifier` stays false — the UI waits for a non-modifier key. Commit happens on the first valid **press**, no key-release handling.
6. **Validation** (`keyboard-shortcuts.c`): `is_valid_accel = gtk_accelerator_valid(keyval, mask) || (keyval == Tab && mask != 0)`; `is_valid_binding` rejects bare (or Shift-only) letters/digits/space and a `forbidden_keyvals[]` list (Home/arrows/PgUp/PgDn/End/Tab/KP_Enter/Return/Mode_switch) unless another modifier is held. **For Hyprland this must be looser**: Hyprland allows modifier-less binds (e.g. `Print`) and mouse binds (`bindm`), so validate only "non-empty and not modifier-only", and warn (not block) on unmodified letters.
7. **Display:** `Adw.ShortcutLabel` fed by `gtk_accelerator_name(keyval, mask)` (`<Super>q`), `disabled-text = "None"`; human string via `gtk_accelerator_get_label_with_keycode`. Header: Cancel (start) / Add or Done (`.suggested-action`) toggled by a `set_header_mode()` state machine; Remove is a `destructive-action pill` button in the page.
8. **Conflicts:** `cc_keyboard_manager_get_collision()` compares `mask` equality then `keyval` (or keycode when keyval==0) across all shortcut tables, skipping the item being edited; message `This key combination is already being used for “%s”. This shortcut will be disabled.`; on Done, `resolve_keyboard_shortcut_collision` removes **only the colliding combo** from the other item, then applies the new one. Reset re-adds defaults and removes them from any colliding item.

**Mapping to Hyprland/Lua:** `hl.bind("SUPER + SHIFT + Q", …)` takes modifier names + an xkb keysym name (`XKB_KEY_` suffix) or `code:N` (wiki `Binds.md`). GDK keyvals *are* xkb keysyms, so `Gdk.keyval_name(keyval)` gives the Hyprland key name directly; modifiers map `SUPER_MASK→SUPER`, `CONTROL_MASK→CTRL`, `ALT_MASK→ALT`, `SHIFT_MASK→SHIFT`. Store `(mods, key)`, render with `Adw.ShortcutLabel` by building `<Super><Shift>q`. HyprMod tracks held modifiers via `key-pressed`/`key-released` instead of the GDK bitmask (`hyprmod/ui/binds/dialog.py`) so that modifier-only binds and `bindm` mouse-drag captures work — worth copying for the mouse case. Conflict detection = same `(mods, key, flags)` in the model; offer "Replace" (remove the other bind's combo) exactly like g-c-c.

---

## 4. Blueprint vs Python-built widgets — verdict

Facts ([blueprint docs](https://gnome.pages.gitlab.gnome.org/blueprint-compiler/)): Blueprint compiles `.blp` → GtkBuilder XML at build time (`custom_target(... 'batch-compile' ...)` feeding `gnome.compile_resources`); the only build dependency is Python + typelibs; **zero runtime dependency**; still labelled *experimental* ("future versions may have breaking changes"); supports templates (`template $Foo : Adw.PreferencesPage`), `bind` expressions, `styles [...]`, `responses [...]`, `condition("max-width: 400sp")`, `_()` translations. Anything **dynamic** (a variable number of rows generated from the Schema, per-monitor widgets, per-entity dialogs) must still be built in code. Arch has `extra/any/blueprint-compiler` 0.22.2 (not installed here); the GNOME Flatpak SDK 49 ships `sdk/blueprint-compiler.bst`; g-c-c itself has moved its `.ui` files to `.blp`.

**Verdict: Python-built widgets for everything schema-driven (the bulk of the app), with the option of Blueprint only for the static shell** (window, sidebar, dialogs' chrome, migration wizard pages). Rationale: (a) ADR-0001's engine/UI seam and the Schema-driven page factory mean ~90 % of rows are generated — Blueprint adds a build step without helping there; (b) with 1 developer, avoiding a second language and a still-experimental toolchain (plus a `subprojects/blueprint-compiler.wrap` or the Arch package as `makedepends`) keeps `python -m hyprsettings` runnable from a checkout without compiling resources; (c) `Gtk.Template` works equally with `.ui` XML or `.blp`, so Blueprint can be adopted later for the static parts if the shell stabilises — the decision is reversible. If Blueprint is used, gate it behind a meson option so the AUR/Flatpak builds pull `blueprint-compiler` only when needed.

---

## 5. Prior art

Data collected via `gh repo view` / repo trees on 2026-08-19; stars/pushes are as of that day.

| Project | Licence | Stack | Stars / last push | Config areas | Read/write strategy |
|---|---|---|---|---|---|
| [HyprMod](https://github.com/BlueManCZ/hyprmod) | GPL-3.0 | Python 3.12, GTK4/libadwaita | 924 / 2026-08-08 | all schema options, animations + bezier, binds (incl. `bindm`), window/layer/workspace rules, monitors, autostart, env, cursor, plugins, profiles | own managed file `~/.config/hypr/hyprland-gui.{conf,lua}` + auto-appended `source=`/`require()`; live apply over IPC; **Lua mode** auto-detected, Lua *read* by running a Lua wrapper that emits JSON; hyprlang→Lua migration dialog |
| [hyprsettings](https://github.com/acropolis914/hyprsettings) | GPL-3.0 | Python + pywebview (WebKit) + JS | 347 / 2026-05-14 | general/monitors/binds/rules/autostart/env/animations/input/… ; colour/gradient/bezier editors | round-trip parser of `hyprland.conf` preserving comments (`PARSER_SPEC.md`), follows `source=` with `$var`/globs; hyprlang only |
| [hyprviz](https://github.com/timasoft/hyprviz) (fork of hyprgui) | GPL-2.0 | Rust, gtk4-rs (no libadwaita) | 192 / 2026-08-17 | nearly all option groups + binds/rules/monitors/animations/gestures/env; bundled guides | own `~/.config/hypr/hyprviz.conf` sourced last; profiles swap the source line; `hyprctl reload`; "0.55 not supported yet" (no Lua) |
| [nwg-shell-config](https://github.com/nwg-piotr/nwg-shell-config) / [nwg-displays](https://github.com/nwg-piotr/nwg-displays) | MIT | Python, GTK3 | 130 / 1080; 2026-05/06 | shell presets, keyboard/pointer, autostart; displays + workspaces | generated `includes.conf` / `monitors.conf` the user must `source`; nwg-displays canvas = `Gtk.Fixed` + `Fixed.move()` on motion with edge-snap lines (`nwg_displays/main.py`) |
| [HyprPanel](https://github.com/Jas-SinghFSU/HyprPanel) settings | MIT | TypeScript on Astal (GTK3) | 2202 / 2026-04-23 | panel-only (bar, menus, theme colours, matugen) | own JSON config; bare `Gtk.ColorButton` w/o alpha; no keybind capture; not a Hyprland-config editor |
| [hyprland-guiutils](https://github.com/hyprwm/hyprland-guiutils) / [hyprland-qtutils](https://github.com/hyprwm/hyprland-qtutils) | BSD-3 | C++ hyprtoolkit / QML Qt6 | 45 / 47 | dialog, donate/update/welcome screens, `run` | no settings widgets; guiutils supersedes qtutils; upstream's toolkit direction is hyprtoolkit |
| hyprland-gtk-settings | — | — | — | **no repo of that name exists**; closest are HyprConf and KyaroruKYO/hyprland-settings below | |
| [Monique](https://github.com/ToRvaLDz/monique) | none | Python GTK4/libadwaita | 185 / 2026-08-05 | monitors + workspaces (Hyprland/Sway/Niri), profiles, hotplug daemon | writes `monitors.conf` **or `monitors.lua`** (user adds `require`); confirm-or-revert 10 s; canvas = `Gtk.DrawingArea` + cairo + `GestureDrag`/`GestureClick` with snapping (`src/monique/canvas.py`) |
| [hyprland-monitors](https://github.com/ImFelipeOliveira/hyprland-monitors) | MIT | Rust egui | 1 / 2026-08-18 | monitors only | **`hyprctl eval 'hl.monitor({...})'`** for atomic live apply; rewrites only single-line `hl.monitor({...})` calls in `monitors.lua`; 15 s auto-revert |
| [Mozaik](https://github.com/saliherdemk/Mozaik), [HyprConf](https://github.com/SchnuBby2205/HyprConf), [KyaroruKYO/hyprland-settings](https://github.com/KyaroruKYO/hyprland-settings) | none / MIT / MIT | Qt6 / Python Adw / Rust Adw | 0–1, 2026-06/07 | rules only / INI-declared subset / 341 scalars | regex or "rewrite below first block" edits of user Lua — fragile; no live apply |
| [hyprset](https://github.com/hyprland-community/hyprset), [ML4W hyprland-settings](https://github.com/mylinuxforwork/hyprland-settings), [vsHyprland-Manager](https://github.com/victorsosaMx/vsHyprland-Manager), [caelestia-settings](https://github.com/Jojo252511/caelestia-settings) | none / GPL-3 / MIT / MIT | Python Adw / Python Adw (Flatpak) / Python GTK3 / Python Adw + Rust | 141 (dead 2024) / 154 / 68 / 2 | partial options; hyprctl-only; presets + cairo preview strip; monitors/keybinds/rules | hyprparser-py; `hyprctl keyword` + replay script on login (README now redirects to HyprMod); own per-section files; rice-specific split files |

No `hl.config`-aware GUI other than HyprMod has traction; the Lua ecosystem otherwise consists of converters (`hyprconf2lua`, `hyprlang2lua`, `hyprvalidate`). Sources: repo READMEs/trees above; [hypr.land Lua announcement](https://hypr.land/news/26_lua/).

**Lessons**

- *Steal from HyprMod:* schema-driven page factory (`hyprmod/data/schema/options.json` → `ui/options/factory.py`); tri-state per-option model (live/saved/default, `is_dirty`) powering Undo and a "Pending changes" page; managed-file + auto-appended `require()`; monitor confirm/revert countdown; modifier tracking via key-pressed/released for `bindm`; `Gtk.ColorDialogButton` gradient rows; global search; `DrawingArea` monitor preview.
- *Differentiate from HyprMod:* it cannot edit the user's own Lua and reads Lua by executing it in an external `lua` process; six helper libraries is heavy. Our ADR-0002 (app-owned modules, deterministic writer, `user.lua` escape hatch, no round-trip of hand-written Lua) is the simpler design; the importer for hyprlang is the headline feature.
- *Steal from hyprsettings:* `source=` discovery with `$var`, `~`, globs; offline wiki text as inline help. *Avoid:* WebKit stack, auto-sorting keys, comment-banner heuristics.
- *Steal from hyprviz:* per-topic guides next to editors; profile = swap one include line; atomic writes. *Avoid:* hyprgui's rewrite-in-place with `.bak`.
- *Steal from Monique/nwg-displays:* separate `monitors.lua`; canvas with snapping; hotplug profiles. *Steal from hyprland-monitors:* `hyprctl eval 'hl.monitor{...}'` for atomic live apply of entity-shaped data.
- *Steal from HyprPanel:* uniform "label + typed inputter + reset button" row contract; typed inputs directory. *Steal from caelestia-settings:* keybind search/filter and `.desktop` scanner for window-rule class autofill. *Steal from vsHyprland-Manager:* cairo live-preview strip of border/rounding/shadow.
- *Avoid:* regex edits of arbitrary Lua (HyprConf/Mozaik); hyprctl-only + login replay (ML4W); GTK3.
- *Gaps a new app can own:* editing the user's real Lua tree safely, `hyprctl eval` live-apply for every `hl.*` construct, uniform confirm-or-revert beyond monitors, hypr* ecosystem pages (hyprpaper/hypridle/hyprlock).

---

## 6. Packaging recommendation

ADR-0004 makes packaging "fog", so this is a sketch that keeps `python -m …` runnable now and packaging cheap later.

### 6.1 Meson layout (GNOME Builder Python template)

The live PyGObject site has no packaging guide (`guide/packaging.html` is 404; `guide/deploy.html` says "There is currently no nice deployment story"); the de-facto reference is GNOME Builder's Python GTK4 template (`src/plugins/meson-templates/resources/…`, https://gitlab.gnome.org/GNOME/gnome-builder). Its structure:

```
meson.build                     # project(), i18n/gnome imports, subdir(data|src|po),
                                # gnome.post_install(glib_compile_schemas: true, gtk_update_icon_cache: true, update_desktop_database: true)
data/meson.build                # desktop (i18n.merge_file + desktop-file-validate test), metainfo (+ appstreamcli validate --no-net test), gschema, icons/
data/<appid>.{desktop.in,metainfo.xml.in,gschema.xml}, data/icons/hicolor/{scalable,symbolic}/apps/<appid>*.svg
src/meson.build                 # pkgdatadir = prefix/datadir/<project>; moduledir = pkgdatadir/<module>
                                # gnome.compile_resources(..., gresource_bundle: true, install_dir: pkgdatadir)
                                # configure_file(<name>.in → bindir launcher, PYTHON/VERSION/pkgdatadir/localedir)
                                # install_data(py sources, install_dir: moduledir)
src/<name>.in                   # #!@PYTHON@; sys.path.insert(1, pkgdatadir); Gio.Resource.load(pkgdatadir/<name>.gresource)._register(); main.main(VERSION)
src/<module>/{__init__,main,window}.py ; src/<appid>.gresource.xml ; po/
```
Key point: modules go to `$datadir/<project>/<module>/` via `install_data`, **not** site-packages, and only the generated launcher knows `pkgdatadir`. App id must be reverse-DNS (`io.github.danielbaldwin47.HyprSettings` style; Flathub: 3–5 components, no generic suffix) — [desktop-entry file naming](https://specifications.freedesktop.org/desktop-entry-spec/latest/file-naming.html), [Flathub app-id rules](https://docs.flathub.org/docs/for-app-authors/requirements#application-id).

Add one file the template lacks: `src/<module>/config.py.in` (`PKGDATADIR='@pkgdatadir@'`, `VERSION`, `LOCALEDIR`, `APP_ID`) installed to `moduledir` by `configure_file`, and a `paths.py` (see 6.5).

### 6.2 AppStream + desktop entry

Required metainfo tags: `id`, `name`, `summary`, `description`, `metadata_license`, `launchable`; at least one `releases`; Flathub additionally wants `content_rating type="oars-1.1"`, `url type="homepage"`, a default screenshot — [AppStream spec](https://www.freedesktop.org/software/appstream/docs/sect-Metadata-Application.html), [Quickstart](https://www.freedesktop.org/software/appstream/docs/chap-Quickstart.html#sec-qsr-app), [Flathub metainfo guidelines](https://docs.flathub.org/docs/for-app-authors/metainfo-guidelines). Desktop entry: `Type=Application`, `Name`, `Exec` (unless `DBusActivatable=true`), `Icon=<appid>`, `Categories=Settings;` (menu-spec), `Keywords=Hyprland;Wayland;`, `StartupNotify=true` — [recognized keys](https://specifications.freedesktop.org/desktop-entry-spec/latest/recognized-keys.html). Both are validated by `meson test` (`desktop-file-validate`, `appstreamcli validate --no-net --explain`) which Arch's `check()` runs.

### 6.3 AUR PKGBUILD sketch

Per [Arch Meson guidelines](https://manual.archlinux.page/package-guidelines/meson/) (`arch-meson`, `meson compile`, `meson install --destdir`), [Python guidelines](https://wiki.archlinux.org/title/Python_package_guidelines) (applications use the plain program name, pure Python → `arch=(any)`), and the real `gnome-tweaks` PKGBUILD (https://gitlab.archlinux.org/archlinux/packaging/packages/gnome-tweaks/-/raw/main/PKGBUILD: `arch=(any)`, `depends=(… gtk4 hicolor-icon-theme libadwaita … python python-gobject)`, `makedepends=(git meson)`):

```bash
pkgname=hyprland-settings-gui          # -git variant: pkgname=hyprland-settings-gui-git, provides/conflicts the base
pkgver=0.1.0
pkgrel=1
pkgdesc="GTK4/libadwaita settings app for Hyprland's Lua config"
arch=(any)
url="https://github.com/danielbaldwin47/hyprland-settings-gui"
license=(GPL-3.0-or-later)              # TBD by ADR
depends=(python python-gobject gtk4 libadwaita glib2 hicolor-icon-theme)
makedepends=(meson)                     # + blueprint-compiler only if .blp is used
optdepends=('hyprland: the compositor being configured')
source=("$url/archive/v$pkgver.tar.gz")
sha256sums=('…')
build()   { arch-meson "$pkgname-$pkgver" build; meson compile -C build; }
check()   { meson test -C build --print-errorlogs; }
package() { meson install -C build --destdir "$pkgdir"; }
# -git: pkgver() { cd "$_pkgname"; git describe --long --tags | sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g'; }
```

### 6.4 Flatpak manifest sketch and the hyprctl-socket caveats

Facts from [sandbox-permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html), [flatpak-spawn / flatpak-run](https://docs.flatpak.org/en/latest/flatpak-command-reference.html), [python.html](https://docs.flatpak.org/en/latest/python.html), [manifests.html](https://docs.flatpak.org/en/latest/manifests.html), [Hyprland IPC wiki](https://wiki.hypr.land/IPC/):

- `--filesystem=xdg-run/hypr` **is valid**: the docs list `xdg-run/path` = "$XDG_RUNTIME_DIR/path" (examples `xdg-run/gvfsd`, `xdg-run/dconf`). Permissions are static — no `$HYPRLAND_INSTANCE_SIGNATURE` expansion — so grant the parent dir. `XDG_RUNTIME_DIR` is reset by Flatpak but to the same `/run/user/$UID`, so `$XDG_RUNTIME_DIR/hypr/$HIS/.socket.sock` / `.socket2.sock` resolve identically inside.
- `HYPRLAND_INSTANCE_SIGNATURE` is inherited: flatpak-run passes the environment through except a fixed unset list (`PATH LD_* XDG_CONFIG_DIRS XDG_DATA_DIRS SHELL TMP* XDG_RUNTIME_DIR container TZDIR PYTHON* … GDK_BACKEND …`), which does not include it.
- `--filesystem=xdg-config/hypr:create` exposes host `~/.config/hypr` at **both** `~/.config/hypr` and `$XDG_CONFIG_HOME/hypr` (= `~/.var/app/<id>/config/hypr`) inside the sandbox (footnote [3] of the permissions page), so `GLib.get_user_config_dir()/hypr` works natively and sandboxed.
- Running host `hyprctl` needs `flatpak-spawn --host …`, which "requires access to the org.freedesktop.Flatpak D-Bus interface" (`--talk-name=org.freedesktop.Flatpak`); Flathub's linter flags this (`finish-args-flatpak-spawn-access`, "granted on sufficient explanation") because it is arbitrary host execution. **Recommendation:** speak the IPC socket directly from Python (`socket.AF_UNIX`, request text e.g. `j/descriptions`, `[[BATCH]]…`, `keyword …`, `reload`; the wiki warns Hyprland handles connections synchronously and unclosed connections freeze it for up to 5 s) and reserve `flatpak-spawn --host` only for things that must run on the host (e.g. `hyprctl eval` if the socket lacks it — verify in #5, or launching external tools like matugen for the Bridge). Same code path can shell out to `hyprctl` when not sandboxed (`FLATPAK_ID` / `/.flatpak-info` present → sandboxed).
- PyGObject, GTK4, libadwaita and `blueprint-compiler` are all in `org.gnome.Platform`/`Sdk` (gnome-build-meta `elements/sdk-platform.bst`, branch gnome-49); Flathub currently publishes runtime branches 48/49/50 and requires the latest for new submissions.

```yaml
id: io.github.danielbaldwin47.HyprSettings     # placeholder RDNN id
runtime: org.gnome.Platform
runtime-version: '50'                            # latest at submission time; 49 also fine
sdk: org.gnome.Sdk
command: hyprland-settings-gui                   # meson-configured launcher in /app/bin
finish-args:
  - --share=ipc
  - --socket=wayland
  - --socket=fallback-x11
  - --filesystem=xdg-config/hypr:create          # managed dir + hyprland.lua/.conf; visible at ~/.config/hypr inside too
  - --filesystem=xdg-run/hypr                    # $XDG_RUNTIME_DIR/hypr/<HIS>/.socket*.sock (no var expansion allowed)
  # - --talk-name=org.freedesktop.Flatpak        # only if flatpak-spawn --host hyprctl is unavoidable (Flathub linter warns)
modules:
  - name: hyprland-settings-gui
    buildsystem: meson
    sources: [{ type: dir, path: . }]
```
Caveats: `--filesystem=home` is *not* needed if all writes stay under `~/.config/hypr`; but the Importer following `source=` into arbitrary paths (`~/dotfiles/...`) and the Bridge reading other tools' output dirs may need `xdg-config` broadly or a portal file chooser fallback. Backups written next to the config are fine under `xdg-config/hypr`.

### 6.5 How install location affects paths

| Context | launcher | pkgdatadir (modules + `.gresource`) | user config | Hyprland sockets |
|---|---|---|---|---|
| **AUR / `--prefix=/usr`** | `/usr/bin/<name>` | `/usr/share/<project>/{<module>/*.py, <name>.gresource}`; launcher does `sys.path.insert(1, pkgdatadir)` | `~/.config/hypr` (`GLib.get_user_config_dir()`) | `$XDG_RUNTIME_DIR/hypr/$HIS/.socket.sock` |
| **Flatpak / `--prefix=/app`** | `/app/bin/<name>` | `/app/share/<project>/…` (baked at build time by `configure_file`, so the same launcher works) | `$XDG_CONFIG_HOME` = `~/.var/app/<id>/config`; with `xdg-config/hypr:create` the host dir appears at both paths; nothing else in `$HOME` visible | same path via `xdg-run/hypr`; `HYPRLAND_INSTANCE_SIGNATURE` inherited |
| **From source (`python -m <module>`)** | none; `@pkgdatadir@` never substituted | modules import from cwd; `.gresource` not compiled → `Gio.Resource.load` and `@Gtk.Template(resource_path=…)` fail unless handled | same | same |

Recommended `paths.py`: resolve `datadir` in order (1) `HYPRSETTINGS_DATADIR` env override, (2) meson-generated `config.py` (absent in a checkout → ImportError), (3) repo-relative `Path(__file__).parents[1]/data`; if no compiled `.gresource` exists, load `.ui` from files (`Gtk.Template(filename=…)`/`Builder.add_from_file`) or compile with `glib-compile-resources` into a temp dir at start-up. Keep Hyprland paths install-independent: `Path(GLib.get_user_config_dir())/'hypr'`; socket = `Path(os.environ['XDG_RUNTIME_DIR'])/'hypr'/os.environ['HYPRLAND_INSTANCE_SIGNATURE']/'.socket.sock'`. Schema data files (`descriptions` cache, curated overlay JSON, Lua stubs) ship in the gresource or under `pkgdatadir/schema/` and are located through the same `datadir` resolver; user-mutable state (backups, undo journal) goes to `GLib.get_user_data_dir()/<project>/` and `GLib.get_user_state_dir()`, which map to `~/.var/app/<id>/…` inside Flatpak automatically.

---

## Open questions handed to other tickets

- #5 / #15: whether every apply can go over the socket (`keyword`, `reload`, `eval`) so Flatpak never needs `flatpak-spawn --host`.
- #12: Hyprland's looser bind validity vs GNOME's rules; `bindm`/mouse capture UX; how to render Hyprland key names not present as GDK keyvals (`code:N`).
- #13: whether the monitor canvas is `DrawingArea` (simplest, Monique/HyprMod) or a snapshot-based custom widget (needed only if per-monitor child widgets should live on the canvas).
- #16: whether to adopt Blueprint for the static shell (reversible; default no).
