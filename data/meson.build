# The Schema ships as data, not as Python: it is read at runtime from the install prefix
# (or from a checkout during `meson devenv`), never generated on the user's machine
# (ADR-0011). `hyprtweaker.engine.schema.resolve.schema_dir()` is the lookup that has to
# agree with this install path.
#
# Listed explicitly for the same reason src/meson.build lists sources explicitly: meson
# says nothing about a file no list names, and a schema missing from the install is an
# app that starts with no options at all. tests/unit/test_schema_data_installed.py diffs
# these names against what is on disk.
schema_files = files(
  'schema/hyprland-0.56.1.json',
  'schema/hyprland-0.56.2.json',
  'schema/overlay.json',
  'schema/tasks.json',
)

install_data(
  schema_files,
  install_dir: get_option('datadir') / 'hyprtweaker' / 'schema',
)

# The desktop entry, metainfo and icons, all named by the App id (ADR-0019) so the
# launcher, the software centre and the window's icon find one another. One
# install_data() call each, because each lands in its own directory;
# tests/unit/test_packaging.py checks that these four are the files named here.
desktop_file = files('io.github.danielbaldwin47.Hyprtweaker.desktop')
metainfo_file = files('io.github.danielbaldwin47.Hyprtweaker.metainfo.xml')

install_data(desktop_file, install_dir: get_option('datadir') / 'applications')
install_data(metainfo_file, install_dir: get_option('datadir') / 'metainfo')
install_data(
  'icons/hicolor/scalable/apps/io.github.danielbaldwin47.Hyprtweaker.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'icons/hicolor/symbolic/apps/io.github.danielbaldwin47.Hyprtweaker-symbolic.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

# Both validators run under `meson test`, so a PKGBUILD check() refuses a desktop entry
# or metainfo a launcher or software centre would reject. Same contract as
# tests/meson.build: with -Dtests=enabled a missing validator fails `meson setup`, so
# "the validator was not found" can never read as "the file is valid".
tests_opt = get_option('tests')
desktop_file_validate = find_program('desktop-file-validate', required: tests_opt)
appstreamcli = find_program('appstreamcli', required: tests_opt)

if desktop_file_validate.found()
  test('validate-desktop', desktop_file_validate, args: [desktop_file])
elif not tests_opt.disabled()
  warning('desktop-file-validate not found: `meson test` will not validate the desktop entry.')
endif

if appstreamcli.found()
  test(
    'validate-metainfo',
    appstreamcli,
    args: ['validate', '--no-net', '--explain', metainfo_file],
  )
elif not tests_opt.disabled()
  warning('appstreamcli not found: `meson test` will not validate the metainfo.')
endif
