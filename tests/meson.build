# `meson test` runs the per-commit tiers, so a packaging check() (ADR-0019) and
# a local build exercise the same suite CI does.
#
# pytest is resolved at configure time, and a missing pytest would otherwise
# leave `meson test` with nothing to run -- which exits 0 and looks exactly like
# a passing suite. The `tests` feature option is what keeps that honest:
#
#   auto     (default) -- run them if pytest is here, warn loudly if not
#   enabled            -- pytest is required; configuring fails without it
#   disabled           -- deliberately no tests, no warning
#
# Anything that treats a green `meson test` as evidence (CI, a PKGBUILD check())
# should configure with -Dtests=enabled, so "no tests ran" can never read as pass.
tests_opt = get_option('tests')
pytest = find_program('pytest', 'pytest-3', required: tests_opt)

if not pytest.found()
  if not tests_opt.disabled()
    warning('pytest not found: `meson test` will run no tests. Configure with -Dtests=enabled to require it.')
  endif
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

  # ADR-0011 tier 2: `Hyprland --verify-config` over every written Lua output. No
  # compositor needed, and it skips itself where no Hyprland binary exists -- so it is
  # cheap enough to be a normal part of `meson test` and a PKGBUILD check().
  test(
    'static',
    pytest,
    args: ['-q', meson.current_source_dir() / 'static'],
    env: test_env,
    protocol: 'exitcode',
  )

  # Skips itself when GTK, libadwaita or a display is missing; set
  # HYPRTWEAKER_REQUIRE_UI=1 to make that a failure instead (tests/ui/conftest.py).
  test(
    'ui-smoke',
    pytest,
    args: ['-q', meson.current_source_dir() / 'ui'],
    env: test_env,
    protocol: 'exitcode',
  )
endif
