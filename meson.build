project(
  'hyprtweaker',
  version: '0.1.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0',
)

# `modules: ['gi']` is the app's one hard runtime dependency expressed as a
# build-time check, so `meson setup` fails on a box that could never run the
# app. Deliberately not a pkg-config dependency() on gtk4/libadwaita: those
# check for development files, which a machine that runs the app fine may not
# have installed.
python = import('python').find_installation('python3', modules: ['gi'])

# The PyGObject floor, checked by capability rather than by version string: live apply
# runs coroutines on the GTK main loop through `gi.events`, which PyGObject 3.50 added.
# One number: MINIMUM_PYGOBJECT in src/hyprtweaker/ui/shell/runtime.py and the
# PKGBUILD's python-gobject pin say the same (tests/unit/test_packaging.py).
if run_command(python, '-c', 'import gi.events', check: false).returncode() != 0
  error('PyGObject >= 3.50 is required (`import gi.events` failed for ' + python.full_path() + '). Update python-gobject (python3-gi) and configure again.')
endif

# The libadwaita floor, by capability for the same reason: Adw.WrapBox arrived in 1.7.
# MINIMUM_LIBADWAITA in src/hyprtweaker/ui/shell/runtime.py and the PKGBUILD say the same.
# A typelib lookup only, so configuring never starts GTK or reaches a display.
adw_check = 'import gi; from gi._gi import Repository; r = Repository.get_default(); r.require("Adw", "1", False); raise SystemExit(r.find_by_name("Adw", "WrapBox") is None)'
if run_command(python, '-c', adw_check, check: false).returncode() != 0
  error('libadwaita >= 1.7 is required (Adw.WrapBox is missing for ' + python.full_path() + '). Update libadwaita (gir1.2-adw-1) and configure again.')
endif

subdir('src')
subdir('data')
subdir('tests')

# Dev loop (ADR-0011):
#   meson setup build && meson devenv -C build python3 -m hyprtweaker
devenv = environment()
devenv.prepend('PYTHONPATH', meson.current_source_dir() / 'src')
meson.add_devenv(devenv)
