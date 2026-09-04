# ADR-0019: Packaging, install & app theming

**Status:** accepted — 2026-08-22

Read by the agent about to change packaging, install & app theming, before the first edit; the Status line says what is on `main` now.

## Context

ADR-0011 made meson the canonical build but left distribution "an open map item". Research #6 §6 collected the facts: the GNOME Builder Python template layout (modules under `pkgdatadir`, generated launcher, desktop/metainfo validation in `meson test`), a ready PKGBUILD sketch, and a Flatpak manifest sketch with the sandbox caveats (`--filesystem=xdg-run/hypr` is valid; host-tool launches need `flatpak-spawn --host`). ADR-0004 is me-first, publishable later; the dev machine is Arch-based (CachyOS). Open were the install story, whether Flatpak ships, app identity (id, desktop entry, icon), the license, schema shipping at package time, and whether the app exposes its own light/dark preference — plus where app preferences live at all (the ADR-0013 Advanced switch and the #7 View choice were "an app preference" with no home).

## Decision

### Install: repo PKGBUILD now, AUR soon after, Flatpak deferred

- **Meson stays the canonical build** (ADR-0011); the dev loop stays `meson devenv` / `python -m hyprtweaker` from a checkout.
- **v1 installs via a repo-local PKGBUILD** (`packaging/PKGBUILD`, `-git`-style pkgver, `makepkg -si`): pacman tracks the files, uninstall is clean, no `sudo meson install` orphans. The research §6.3 sketch is the basis, with `pkgname=hyprtweaker`.
- **AUR publication follows quickly** — once the app is built and has had real use, not at some distant publishable-later horizon. The `-git` package goes up first; a versioned package when releases exist.
- **No Flatpak in v1.** The Bridge launches host tools (matugen, wallust), ADR-0016 opens the user's editor at file:line, and the Importer follows `source=` into arbitrary paths — all fight the sandbox. Revisited when AUR publication happens; research §6.4's manifest sketch is the recipe if it ever ships.

### Identity

- Binary/launcher, pkgname, App dir, module: **`hyprtweaker`**.
- App id (desktop entry file, icon name, future Flatpak id): **`io.github.danielbaldwin47.Hyprtweaker`**.
- Desktop entry: `Name=Hyprtweaker`, `Categories=Settings;`, `Keywords=Hyprland;Wayland;compositor;`, `Icon=<appid>`, `StartupNotify=true`; metainfo alongside; both validated by the template's `meson test`, which the PKGBUILD's `check()` runs.
- Icon: a **self-drawn simple SVG** (scalable + symbolic), placeholder quality acceptable under ADR-0004. The repo name `hyprland-settings-gui` is unaffected.
- License: **GPL-3.0-or-later**, matching the prior art the design borrows from (HyprMod, hyprsettings).

### Schemas ship, never regenerate at install

Generated schemas are repo-committed artifacts produced by the Release check protocol (ADR-0012); the package build just installs the `data/schema/` files — no schema generation in `build()` (it needs a live Hyprland) and no first-run generation (rejected in ADR-0011). The CI overlay completeness test is the freshness gate, not packaging.

### App theming: System / Light / Dark preference

The app follows the system style by default but exposes an explicit **System / Light / Dark** row in the preferences (hamburger menu), applied via `Adw.StyleManager.set_color_scheme`. Hyprland users often run without the portal/desktop machinery that makes system style detection reliable, so the override earns its place; default is System.

Custom-drawn surfaces must stay legible on both grounds: the Arrangement canvas and value swatches derive their colors from the widget style context (`get_color()`, the style manager's `dark` property) rather than hardcoding, alpha swatches sit on a checkerboard, and both themes are part of the visual test pass.

### Prefs file

App preferences — the theme override, the #7 View choice, the ADR-0013 Advanced switch, "remember my choice" answers (ADR-0014) — persist as the **Prefs file**: plain JSON at `$XDG_STATE_HOME/hyprtweaker/prefs.json`, beside the Snapshots and Journal (ADR-0005). **No GSettings**: it drags in a dconf daemon dependency, and without the daemon the memory backend silently drops every preference — a bad failure mode on minimal Hyprland setups.

## Consequences

- `packaging/PKGBUILD` lives in-repo and is exercised by the developer's own installs before AUR; the AUR `-git` package is near-term roadmap, not fog.
- The reverse-DNS app id is fixed now, so the desktop file, icon theme name, and any future Flatpak id never churn.
- The gschema half of the GNOME Builder template is dropped (no GSettings); the template's desktop/metainfo validation stays.
- The engine gains a tiny prefs store (read/write JSON with defaults); UI preferences never touch the Hyprland config model or its Journal.
- Canvas/swatch theme-derivation is a spec requirement on the drawing code, testable by running the visual pass in both schemes.

## Alternatives considered

- **`sudo meson install`** — rejected: untracked files, dirty uninstall, invisible to pacman.
- **AUR from day one** — rejected: publishing before the app has been used at all inverts ADR-0004; "soon after built and tested" is the actual bar.
- **Flatpak in v1** — rejected: three shipped features (Bridge host tools, open-at-line, `source=`-following import) each need sandbox escapes or portals; the me-first audience is on Arch. Deferred to the AUR moment, not ruled out forever.
- **GSettings for app prefs** — rejected: dconf dependency, silent memory-backend fallback; a JSON file next to the app's other state is boring and reliable.
- **Schema generation at package or first run** — rejected (re-affirming ADR-0011): needs a running Hyprland of the right version; shipped schemas plus the ADR-0012 supplement path already cover drift.
- **Commissioned/iconic branding for v1** — rejected: me-first; a placeholder SVG unblocks the desktop entry.
