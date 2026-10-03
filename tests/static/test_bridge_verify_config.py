"""`Hyprland --verify-config` over an Entrypoint carrying Bridge lines (#163, ADR-0006).

The unit tier proves which lines the Writer emits; only Hyprland can say it loads them --
a native-path `require("dms.colors")`, noctalia's `require("noctalia").apply_theme()`, the
app-owned matugen template's output, and the commented lines of a gated or waiting bridge.
The tool modules are fixtures written into the test's own directory, shaped as #167 rendered
them; no tool runs.

Same isolation as `test_writer_verify_config.py`, plus `HOME` and `XDG_CONFIG_HOME` pointed
into the test directory, so the verify run reads nothing of the owner's.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.bridge import (  # noqa: E402
    DMS,
    MATUGEN,
    NOCTALIA,
    WALLUST,
    ChosenSource,
    PresetColors,
    Wallpaper,
    bridge_states_for,
)
from hyprtweaker.engine.model import ConfigModel  # noqa: E402
from hyprtweaker.engine.paths import ConfigPaths  # noqa: E402
from hyprtweaker.engine.schema import load_schema  # noqa: E402
from hyprtweaker.engine.state import Manifest  # noqa: E402
from hyprtweaker.engine.writer import Writer  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("Hyprland") is None, reason="no Hyprland binary on this machine"
)

MATUGEN_OUTPUT = (
    MATUGEN.template_pack.templates[0]  # type: ignore[union-attr]
    .text.replace("{{image}}", "/w/sea.png")
    .replace(
        "<* for name, value in colors *>\n"
        '    {{name}} = "0xff{{value.default.hex_stripped}}",\n'
        "<* endfor *>\n",
        '    primary = "0xff8fcdff",\n'
        '    outline_variant = "0xff40484f",\n'
        '    error = "0xffffb4ab",\n',
    )
)
"""The app-owned matugen template as matugen renders it (#167 § matugen, three roles)."""

NOCTALIA_OUTPUT = """\
local primary = "rgb(b0c6ff)"
local surface = "rgb(121318)"
local secondary = "rgb(bfc6dc)"
local function apply_theme()
    hl.config({
        general = { col = { active_border = primary, inactive_border = surface } },
        group = { col = { border_active = secondary, border_inactive = surface } },
    })
end
return { colors = { primary = primary }, apply_theme = apply_theme }
"""

DMS_OUTPUT = """\
hl.config({
    general = { col = { active_border = "rgb(8fcdff)", inactive_border = "rgb(40484f)" } },
    group = { groupbar = { col = { active = "rgb(8fcdff)" } } },
})
"""


def verify(root: Path, entrypoint: Path) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY")
    }
    runtime = root / "run"
    runtime.mkdir(exist_ok=True)
    environment.update(XDG_RUNTIME_DIR=str(runtime), HOME=str(root), XDG_CONFIG_HOME=str(root))
    return subprocess.run(
        ["Hyprland", "--verify-config", "-c", str(entrypoint)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )


def write_with_bridges(root: Path, source: ChosenSource) -> ConfigPaths:
    """A small model plus matugen, noctalia and DMS output, wallust set up but not yet run."""
    paths = ConfigPaths.rooted_at(root)
    for relpath, text in (
        ("hyprtweaker/bridge/matugen.lua", MATUGEN_OUTPUT),
        ("noctalia.lua", NOCTALIA_OUTPUT),
        ("dms/colors.lua", DMS_OUTPUT),
    ):
        (paths.hypr_dir / relpath).parent.mkdir(parents=True, exist_ok=True)
        (paths.hypr_dir / relpath).write_text(text, encoding="utf-8")
    present = {"hyprtweaker/bridge/matugen.lua", "noctalia.lua", "dms/colors.lua"}

    model = ConfigModel(load_schema("0.56.2", ROOT / "data" / "schema"))
    model.set("general:border_size", 3)
    manifest = Manifest(app_version="x", schema_version="0.56.2")
    for spec in (MATUGEN, WALLUST, NOCTALIA, DMS):
        manifest = manifest.add_bridge(spec, present=present)
    writer = Writer(paths, app_version="0.0.0-test")
    writer.record_bridges(model, bridge_states_for(source, manifest.bridges, present=present))
    writer.write(model)
    return paths


@pytest.mark.parametrize("source", [Wallpaper("matugen"), PresetColors()])
def test_an_entrypoint_with_bridge_lines_is_a_config_hyprland_accepts(
    tmp_path: Path, source: ChosenSource
) -> None:
    paths = write_with_bridges(tmp_path, source)
    text = paths.entrypoint.read_text(encoding="utf-8")
    loading = [line for line in text.splitlines() if line.startswith("require(")]
    if isinstance(source, Wallpaper):
        assert loading[-3:] == [
            'require("noctalia").apply_theme()',
            'require("dms.colors")',
            'require("hyprtweaker/bridge/matugen")',
        ]
    else:
        assert not [line for line in loading if "bridge" in line or "noctalia" in line]

    result = verify(tmp_path, paths.entrypoint)

    assert result.returncode == 0, f"{text}\n{result.stdout}\n{result.stderr}"
    assert "config ok" in result.stdout


def test_requiring_a_file_a_tool_has_not_written_is_what_the_waiting_line_avoids(
    tmp_path: Path,
) -> None:
    """The negative control for S4: the require the waiting line comments out would fail."""
    paths = write_with_bridges(tmp_path, Wallpaper("matugen"))
    entrypoint = paths.entrypoint
    entrypoint.write_text(
        entrypoint.read_text(encoding="utf-8") + 'require("hyprtweaker/bridge/wallust")\n',
        encoding="utf-8",
    )

    assert verify(tmp_path, entrypoint).returncode != 0


def test_a_native_path_module_is_really_loaded(tmp_path: Path) -> None:
    """Guards the acceptance above: a bad colour in DMS's file fails the run, so "config ok"
    did not come from Hyprland skipping the require."""
    paths = write_with_bridges(tmp_path, Wallpaper("matugen"))
    colors = paths.hypr_dir / "dms" / "colors.lua"
    colors.write_text(DMS_OUTPUT.replace('"rgb(8fcdff)"', '"notacolor"', 1), encoding="utf-8")

    assert verify(tmp_path, paths.entrypoint).returncode != 0


def test_the_wizard_s_gate_accepts_a_tree_with_a_waiting_and_an_active_tool(
    stub_tool: Callable[..., Path],
) -> None:
    """#187: the Migration wizard's static gate runs on the staged tree with each consented
    tool's line in it -- matugen waiting for its first run, DMS loading the file it wrote."""
    from hyprtweaker.engine.bridge.wire import WireConsent
    from hyprtweaker.engine.migration.bridge_setup import Offer
    from hyprtweaker.engine.migration.flow import MigrationFlow

    paths = ConfigPaths.default()
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    paths.hyprland_conf.write_text("general {\n    gaps_in = 5\n}\n", encoding="utf-8")
    (paths.hypr_dir / "dms").mkdir()
    (paths.hypr_dir / "dms/colors.lua").write_text(DMS_OUTPUT, encoding="utf-8")
    (paths.config_home / "matugen").mkdir()
    (paths.config_home / "matugen/config.toml").write_text("[config]\n", encoding="utf-8")
    stub_tool("dms", "exit 99")
    stub_tool("matugen", "exit 99")

    class Live:
        """A client is all the offer asks for; nothing here is switched."""

    flow = MigrationFlow(
        paths=paths,
        schema=load_schema("0.56.2", ROOT / "data" / "schema"),
        app_version="0.0.0-test",
        client=Live(),  # type: ignore[arg-type]
    )
    flow.build_preview()
    flow.back_up()
    for offer in flow.bridge_offers():
        assert isinstance(offer, Offer), offer
        flow.consent(WireConsent(offer.plan))

    verdict = flow.stage_and_gate()

    assert sorted(consent.plan.tool for consent in flow.consents) == ["dms", "matugen"]
    assert verdict.ran
    assert verdict.ok, verdict.output
