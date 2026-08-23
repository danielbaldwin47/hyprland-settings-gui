# Sources are listed explicitly, per subpackage. Meson discourages globbing:
# an unlisted file is a build error at review time rather than a module that
# silently fails to install.
package_sources = {
  'hyprtweaker': [
    '__init__.py',
    '__main__.py',
    'application.py',
    'main.py',
  ],
  'hyprtweaker/engine': ['__init__.py'],
  'hyprtweaker/engine/importer': ['__init__.py'],
  'hyprtweaker/engine/ipc': ['__init__.py'],
  'hyprtweaker/engine/model': ['__init__.py'],
  'hyprtweaker/engine/schema': ['__init__.py'],
  'hyprtweaker/engine/state': ['__init__.py'],
  'hyprtweaker/engine/writer': ['__init__.py'],
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
