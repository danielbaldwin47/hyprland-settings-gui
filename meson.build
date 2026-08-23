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

subdir('src')
subdir('data')
subdir('tests')

# Dev loop (ADR-0011):
#   meson setup build && meson devenv -C build python3 -m hyprtweaker
devenv = environment()
devenv.prepend('PYTHONPATH', meson.current_source_dir() / 'src')
meson.add_devenv(devenv)
