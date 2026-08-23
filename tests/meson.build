# `meson test` runs the per-commit tiers, so a packaging check() (ADR-0019) and
# a local build exercise the same suite CI does.
#
# pytest is resolved at configure time. When it is absent meson would otherwise
# define no tests at all and `meson test` would pass vacuously, so say so loudly
# rather than let an empty run look like a green one.
pytest = find_program('pytest', 'pytest-3', required: false)

if not pytest.found()
  warning('pytest not found: `meson test` will run no tests. Install pytest to enable them.')
else
  test_env = environment()
  test_env.prepend('PYTHONPATH', meson.project_source_root() / 'src')

  test(
    'unit',
    pytest,
    args: ['-q', meson.current_source_dir() / 'unit'],
    env: test_env,
    protocol: 'exitcode',
  )

  # Skips itself when GTK, libadwaita or a display is missing; set
  # HYPRTWEAKER_REQUIRE_UI=1 to make that a failure instead (tests/ui/test_smoke.py).
  test(
    'ui-smoke',
    pytest,
    args: ['-q', meson.current_source_dir() / 'ui'],
    env: test_env,
    protocol: 'exitcode',
  )
endif
