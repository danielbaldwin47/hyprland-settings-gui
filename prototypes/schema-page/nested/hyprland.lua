-- PROTOTYPE — nested Hyprland used as a sandbox for issue #8.
-- Mirrors the ADR-0002 layout: a thin main file that require()s generated modules.
-- nested wayland backend names its output WAYLAND-1 (not WL-1)
hl.monitor({ output = 'WAYLAND-1', mode = '1250x2400@60', position = '0x0', scale = 1 })

hl.config({
  ['general.border_size'] = 2,
  ['general.gaps_in'] = 4,
  ['general.gaps_out'] = 8,
  ['decoration.rounding'] = 8,
  ['decoration.blur.enabled'] = false,
  ['animations.enabled'] = false,
  ['misc.disable_hyprland_logo'] = true,
  ['misc.disable_splash_rendering'] = true,
  ['misc.force_default_wallpaper'] = 0,
})

-- no binds: under --verify-config hl.dsp.* resolves to nil, so a bind here would
-- fail pre-flight validation even though it is fine at runtime (noted for #8/#15).

-- generated module, rewritten by the app on every apply
pcall(require, 'options')
