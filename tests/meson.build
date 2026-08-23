# `meson test` runs the per-commit tiers, so a packaging check() (ADR-0019) and
# a local build exercise the same suite CI does. pytest is optional: a machine
# without it can still build and install the app.
pytest = find_program('pytest', 'pytest-3', required: false)

if pytest.found()
  test_env = environment()
  test_env.prepend('PYTHONPATH', meson.project_source_root() / 'src')

  test(
    'unit',
    pytest,
    args: ['-q', meson.current_source_dir() / 'unit'],
    env: test_env,
    protocol: 'exitcode',
  )

  # Skips itself when GTK, libadwaita or a display is missing (tests/ui/conftest.py).
  test(
    'ui-smoke',
    pytest,
    args: ['-q', meson.current_source_dir() / 'ui'],
    env: test_env,
    protocol: 'exitcode',
  )
endif
