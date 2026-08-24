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
  'schema/hyprland-0.56.2.json',
  'schema/overlay.json',
  'schema/tasks.json',
)

install_data(
  schema_files,
  install_dir: get_option('datadir') / 'hyprtweaker' / 'schema',
)
