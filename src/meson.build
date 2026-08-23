# Sources are listed explicitly, per subpackage, because meson discourages
# globbing. Meson will NOT complain about a source that is missing from this
# list -- it just quietly fails to install it, which is invisible from a
# checkout and shows up as an ImportError from the installed package. So
# tests/unit/test_meson_sources.py diffs these lists against what is on disk;
# add a module here and the test stays green, forget to and it goes red.
package_sources = {
  'hyprtweaker': [
    '__init__.py',
    '__main__.py',
    'application.py',
    'main.py',
  ],
  'hyprtweaker/engine': [
    '__init__.py',
    'paths.py',
  ],
  'hyprtweaker/engine/importer': [
    '__init__.py',
    'hyprlang.py',
    'keywords.py',
  ],
  'hyprtweaker/engine/ipc': [
    '__init__.py',
    'commands.py',
    'errors.py',
    'events.py',
    'instance.py',
  ],
  'hyprtweaker/engine/model': [
    '__init__.py',
    'options.py',
    'values.py',
  ],
  'hyprtweaker/engine/schema': [
    '__init__.py',
    'generated.py',
    'infer.py',
    'overlay.py',
    'resolve.py',
    'sources.py',
    'types.py',
  ],
  'hyprtweaker/engine/state': [
    '__init__.py',
    'manifest.py',
  ],
  'hyprtweaker/engine/writer': [
    '__init__.py',
    'lua.py',
    'modules.py',
    'syntax.py',
    'writer.py',
  ],
  'hyprtweaker/ui': ['__init__.py'],
  'hyprtweaker/ui/dialogs': ['__init__.py'],
  'hyprtweaker/ui/pages': ['__init__.py'],
  'hyprtweaker/ui/rows': ['__init__.py'],
  'hyprtweaker/ui/shell': [
    '__init__.py',
    'window.py',
  ],
}

foreach package, sources : package_sources
  paths = []
  foreach source : sources
    paths += package / source
  endforeach
  python.install_sources(paths, subdir: package)
endforeach

launcher_conf = configuration_data()
launcher_conf.set('PYTHON', python.full_path())

configure_file(
  input: 'hyprtweaker.in',
  output: 'hyprtweaker',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
