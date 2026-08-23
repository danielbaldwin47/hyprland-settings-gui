project(
  'hyprtweaker',
  version: '0.1.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0',
)

python = import('python').find_installation('python3')

subdir('src')
subdir('tests')

# Dev loop (ADR-0011):
#   meson setup build && meson devenv -C build python3 -m hyprtweaker
devenv = environment()
devenv.prepend('PYTHONPATH', meson.current_source_dir() / 'src')
meson.add_devenv(devenv)
