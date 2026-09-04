# ADR-0001: Python + GTK4 + libadwaita for the whole app (no Rust rewrite)

**Status:** accepted — 2026-08-19

Read by the agent about to change python + gtk4 + libadwaita for the whole app (no rust rewrite), before the first edit; the Status line says what is on `main` now.

## Context

Options weighed: GTK4/libadwaita in Python or Rust, Qt6/QML, hyprtoolkit. The user asked whether to prototype in Python and rewrite in Rust for distribution.

## Decision

Python (PyGObject) + GTK4 + libadwaita for both engine and UI. No planned Rust rewrite.

## Consequences

- libadwaita's preferences widgets (`Adw.PreferencesPage/Group`, `SwitchRow`, `SpinRow`, `ComboRow`, `EntryRow`, `ExpanderRow`) map directly onto a settings app; fastest path to a polished result and instant live testing on the T480.
- Python GTK apps distribute fine (Flatpak, AUR/PKGBUILD via meson) — a rewrite would double the work for no user-visible gain.
- Keep a hard seam between **engine** (schema, config model, Lua writer, hyprlang importer, Hyprland IPC) and **UI** so either half could be ported independently if ever needed.
