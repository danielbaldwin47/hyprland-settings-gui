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
    'session.py',
  ],
  'hyprtweaker/engine': [
    '__init__.py',
    'dispatchers.py',
    'paths.py',
  ],
  'hyprtweaker/engine/apply': [
    '__init__.py',
    'applier.py',
    'foreign.py',
    'ownership.py',
    'preview.py',
    'queue.py',
    'recovery.py',
    'reread.py',
    'restore.py',
    'result.py',
    'transaction.py',
    'undo.py',
  ],
  'hyprtweaker/engine/importer': [
    '__init__.py',
    'binds.py',
    'dispatchers.py',
    'hyprlang.py',
    'keysyms.py',
    'keywords.py',
    'loss.py',
    'mapping.py',
    'monitors.py',
    'rules.py',
    'scalars.py',
  ],
  'hyprtweaker/engine/importer/lua': [
    '__init__.py',
    'mapping.py',
    # Not Python, but it is package data the importer cannot run without: installing the
    # module and leaving this behind is an ImportError you only see from an install.
    'runner.lua',
    'sandbox.py',
    'scripts.py',
  ],
  'hyprtweaker/engine/ipc': [
    '__init__.py',
    'commands.py',
    'errors.py',
    'events.py',
    'instance.py',
  ],
  'hyprtweaker/engine/migration': [
    '__init__.py',
    'backup.py',
    'detect.py',
    'export.py',
    'flow.py',
    'sentinel.py',
  ],
  'hyprtweaker/engine/model': [
    '__init__.py',
    'entities.py',
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
    'journal.py',
    'manifest.py',
  ],
  'hyprtweaker/engine/writer': [
    '__init__.py',
    'binds.py',
    'lua.py',
    'modules.py',
    'syntax.py',
    'writer.py',
  ],
  'hyprtweaker/ui': ['__init__.py'],
  'hyprtweaker/ui/dialogs': [
    '__init__.py',
    'errors.py',
    'migration.py',
  ],
  'hyprtweaker/ui/pages': [
    '__init__.py',
    'config.py',
    'plan.py',
  ],
  'hyprtweaker/ui/rows': [
    '__init__.py',
    'chrome.py',
    'factory.py',
    'gesture.py',
    'state.py',
  ],
  'hyprtweaker/ui/shell': [
    '__init__.py',
    'runtime.py',
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
