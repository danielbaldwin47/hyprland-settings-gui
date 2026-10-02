"""Bridge setup in the Migration wizard's back-up step (#187, ADR-0009, ADR-0006).

ADR-0009 §Back up, bridge, static gate: "detected tools offered per-tool, each flip behind
explicit confirmation". The flow is driven headless here, as `test_migration_flow.py` drives
the rest of the wizard: which tools are offered, that a consent is the only way a tool's
config is written, that the write waits for Switch, that the static gate judges the tree with
the tool's line in it, and that Roll back puts every tool file back.

Fixture configs are #166's (`test_bridge_wire.py`), under this test's own home (#233's fence:
`ConfigPaths.default()` is inside `tmp_path`). No tool runs: a tool is "installed" by a stub
on the fenced tool path that is never executed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from _support import SAMPLE_APP_VERSION, sample_schema
from test_bridge_wire import DMS_COLORS, MATUGEN_CONFIG, WALLUST_CONFIG, put
from test_migration_flow import CONF, FakeClient, run

from hyprtweaker.engine.bridge import REGISTRY
from hyprtweaker.engine.bridge.wire import WireConsent
from hyprtweaker.engine.migration import bridge_setup
from hyprtweaker.engine.migration import flow as flow_module
from hyprtweaker.engine.migration import sentinel as sentinels
from hyprtweaker.engine.migration.bridge_setup import CannotSetUp, Offer, SetUp
from hyprtweaker.engine.migration.flow import MigrationFlow
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import Manifest

MATUGEN_WAITING = (
    '-- require("hyprtweaker/bridge/matugen")  -- waiting for matugen\'s first run'
)
DMS_ACTIVE = 'require("dms.colors")'


@pytest.fixture
def paths() -> ConfigPaths:
    """The fenced home's real layout: `~/.config/hypr` holding a `hyprland.conf`."""
    config = ConfigPaths.default()
    config.hypr_dir.mkdir(parents=True, exist_ok=True)
    config.hyprland_conf.write_text(CONF, encoding="utf-8")
    return config


@pytest.fixture
def two_tools(paths: ConfigPaths, stub_tool: Callable[..., Path]) -> ConfigPaths:
    """matugen and wallust installed (stubs never run), each with its own config."""
    put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    put(paths.config_home / "wallust/wallust.toml", WALLUST_CONFIG)
    stub_tool("matugen", "exit 99")
    stub_tool("wallust", "exit 99")
    return paths


class Gate:
    """Hyprland's static gate, stood in for: records each staged Entrypoint it judged."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.files: list[set[str]] = []
        self.returncode = 0

    def __call__(self, entrypoint: Path, runtime: Path) -> subprocess.CompletedProcess[str]:
        self.seen.append(entrypoint.read_text(encoding="utf-8"))
        root = entrypoint.parent
        self.files.append({str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()})
        return subprocess.CompletedProcess(
            [], self.returncode, "", "rejected" * self.returncode
        )


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    fake = Gate()
    monkeypatch.setattr(flow_module, "_verify_config", fake)
    monkeypatch.setattr(flow_module, "_hyprland_installed", lambda: True)
    return fake


def flow_for(paths: ConfigPaths, client: FakeClient | None = None) -> MigrationFlow:
    flow = MigrationFlow(
        paths=paths,
        schema=sample_schema(),
        app_version=SAMPLE_APP_VERSION,
        client=client,
    )
    flow.build_preview()
    flow.back_up()
    return flow


def offer(flow: MigrationFlow, tool: str) -> Offer:
    (found,) = [each for each in flow.bridge_offers() if each.tool == tool]
    assert isinstance(found, Offer), found
    return found


def tool_files(paths: ConfigPaths) -> dict[str, bytes]:
    """Every file outside the hypr dir and the state dir: the tools' own."""
    root = paths.config_home
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_relative_to(paths.hypr_dir)
    }


def matugen_wired(paths: ConfigPaths) -> str:
    home = paths.config_home.parent
    return MATUGEN_CONFIG.replace(
        "input_path = '~/.config/matugen/templates/hyprland-colors.lua'",
        f"input_path = '{home}/.config/matugen/templates/hyprtweaker-hyprland.lua'",
    ).replace(
        "output_path = '~/.config/hypr/colors.lua'",
        f"output_path = '{home}/.config/hypr/hyprtweaker/bridge/matugen.lua'",
    )


def bridges(paths: ConfigPaths) -> list[str]:
    manifest = Manifest.load(paths.manifest, app_version="x", schema_version="y")
    return [entry.tool for entry in manifest.bridges]


class TestTheOffer:
    def test_two_installed_tools_are_both_offered_with_their_plans(
        self, two_tools: ConfigPaths
    ) -> None:
        flow = flow_for(two_tools, FakeClient())

        offers = flow.bridge_offers()

        assert [(type(each).__name__, each.tool) for each in offers] == [
            ("Offer", "matugen"),
            ("Offer", "wallust"),
        ]
        matugen = offers[0]
        assert isinstance(matugen, Offer)
        assert [(edit.shown, edit.change) for edit in matugen.plan.files] == [
            ("~/.config/matugen/templates/hyprtweaker-hyprland.lua", "new"),
            ("~/.config/matugen/config.toml", "changed"),
        ]

    def test_a_tool_that_is_only_configured_is_not_offered(
        self, paths: ConfigPaths, stub_tool: Callable[..., Path]
    ) -> None:
        """Its config is here, its program is not: there is nothing to keep working."""
        put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
        stub_tool("wallust", "exit 99")

        offers = flow_for(paths, FakeClient()).bridge_offers()

        assert [each.tool for each in offers] == ["wallust"]

    def test_nothing_installed_offers_nothing(self, paths: ConfigPaths) -> None:
        put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)

        assert flow_for(paths, FakeClient()).bridge_offers() == ()

    def test_without_an_ipc_socket_nothing_is_offered(self, two_tools: ConfigPaths) -> None:
        """ADR-0009: without a live session the wizard runs Detect/Preview only."""
        flow = flow_for(two_tools, client=None)
        before = tool_files(two_tools)

        offers = flow.bridge_offers()
        run(flow.switch())

        assert offers == ()
        assert tool_files(two_tools) == before
        assert bridges(two_tools) == []

    def test_an_already_wired_tool_is_listed_as_set_up_with_no_offer(
        self, two_tools: ConfigPaths
    ) -> None:
        manifest = Manifest(app_version="x", schema_version="y").add_bridge(
            REGISTRY["matugen"], present=()
        )
        two_tools.manifest.parent.mkdir(parents=True, exist_ok=True)
        two_tools.manifest.write_text(manifest.render(), encoding="utf-8")

        offers = flow_for(two_tools, FakeClient()).bridge_offers()

        assert offers[0] == SetUp("matugen", "matugen")
        assert isinstance(offers[1], Offer)

    def test_a_version_that_cannot_be_bridged_says_why_and_offers_nothing(
        self, paths: ConfigPaths, stub_tool: Callable[..., Path]
    ) -> None:
        put(paths.hypr_dir / "dms/hypr-colors.conf", "$primary = rgb(8fcdff)\n")
        stub_tool("dms", "exit 99")

        offers = flow_for(paths, FakeClient()).bridge_offers()

        assert offers == (
            CannotSetUp(
                "dms",
                "DMS",
                "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer.",
            ),
        )


class TestConsentIsTheOnlyWayIn:
    def test_confirming_one_of_two_wires_only_that_one_at_switch(
        self, two_tools: ConfigPaths
    ) -> None:
        wallust_before = (two_tools.config_home / "wallust/wallust.toml").read_bytes()
        flow = flow_for(two_tools, FakeClient())
        matugen = offer(flow, "matugen")
        flow.consent(WireConsent(matugen.plan))
        assert (two_tools.config_home / "matugen/config.toml").read_text() == MATUGEN_CONFIG

        result = run(flow.switch())

        assert result.ok
        config = two_tools.config_home / "matugen/config.toml"
        assert config.read_text(encoding="utf-8") == matugen_wired(two_tools)
        assert (two_tools.config_home / "wallust/wallust.toml").read_bytes() == wallust_before
        assert bridges(two_tools) == ["matugen"]
        assert MATUGEN_WAITING in two_tools.entrypoint.read_text(encoding="utf-8").splitlines()
        assert result.bridges == (
            "matugen is set up. Its colors load from matugen's next run.",
        )

    def test_skipping_every_tool_leaves_every_tool_config_byte_identical(
        self, two_tools: ConfigPaths
    ) -> None:
        before = tool_files(two_tools)
        flow = flow_for(two_tools, FakeClient())
        flow.bridge_offers()

        result = run(flow.switch())
        flow.keep()

        assert result.ok
        assert tool_files(two_tools) == before
        assert bridges(two_tools) == []
        assert "matugen" not in two_tools.entrypoint.read_text(encoding="utf-8")

    def test_a_withdrawn_consent_writes_nothing(self, two_tools: ConfigPaths) -> None:
        before = tool_files(two_tools)
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))

        flow.withdraw("matugen")
        run(flow.switch())

        assert tool_files(two_tools) == before

    def test_a_gate_that_rejects_the_tree_leaves_every_tool_config_untouched(
        self, two_tools: ConfigPaths, gate: Gate
    ) -> None:
        before = tool_files(two_tools)
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))
        gate.returncode = 1

        verdict = flow.stage_and_gate()

        assert verdict.blocks
        assert tool_files(two_tools) == before
        assert bridges(two_tools) == []

    def test_consenting_writes_nothing_until_the_switch(
        self, two_tools: ConfigPaths, gate: Gate
    ) -> None:
        """Confirm in the back-up step, write at Switch: a cancel before then costs nothing."""
        before = tool_files(two_tools)
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))

        flow.stage_and_gate()

        assert tool_files(two_tools) == before
        assert not two_tools.entrypoint.exists()

    def test_a_setup_that_fails_at_switch_leaves_the_migration_standing(
        self, two_tools: ConfigPaths
    ) -> None:
        """The user changed matugen's config after confirming: it is not written over, its
        line goes, and the switch itself is not rolled back for it."""
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))
        edited = MATUGEN_CONFIG + "\n# edited after the confirm\n"
        put(two_tools.config_home / "matugen/config.toml", edited)

        result = run(flow.switch())

        assert result.ok
        assert (two_tools.config_home / "matugen/config.toml").read_text() == edited
        assert bridges(two_tools) == []
        assert "matugen" not in two_tools.entrypoint.read_text(encoding="utf-8")
        assert result.bridges == (
            "matugen was not set up: ~/.config/matugen/config.toml changed after the changes "
            "were shown, so nothing was changed. Review them again. You can set it up on the "
            "Theming page.",
        )


class TestTheStagedTree:
    def test_the_gate_judges_the_tree_with_a_waiting_tools_line_in_require_order(
        self, two_tools: ConfigPaths, gate: Gate
    ) -> None:
        """matugen has not run, so there is no file to require yet: its line is there,
        commented as waiting (settled S4), after the generated Modules."""
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))

        verdict = flow.stage_and_gate()

        assert verdict.ran and verdict.ok
        (staged,) = gate.seen
        lines = staged.splitlines()
        requires = [
            i for i, line in enumerate(lines) if line.startswith('require("hyprtweaker/')
        ]
        assert MATUGEN_WAITING in lines
        assert requires and lines.index(MATUGEN_WAITING) > max(requires)

    def test_a_tool_whose_file_is_there_is_required_and_its_file_is_judged_with_it(
        self, paths: ConfigPaths, stub_tool: Callable[..., Path], gate: Gate
    ) -> None:
        put(paths.hypr_dir / "dms/colors.lua", DMS_COLORS)
        stub_tool("dms", "exit 99")
        flow = flow_for(paths, FakeClient())
        flow.consent(WireConsent(offer(flow, "dms").plan))

        flow.stage_and_gate()

        (staged,) = gate.seen
        assert DMS_ACTIVE in staged.splitlines()
        assert "dms/colors.lua" in gate.files[0]

    def test_without_consent_the_staged_tree_has_no_tool_line(
        self, two_tools: ConfigPaths, gate: Gate
    ) -> None:
        flow = flow_for(two_tools, FakeClient())
        flow.bridge_offers()

        flow.stage_and_gate()

        assert "bridge" not in gate.seen[0]


class TestRollBack:
    def test_rolling_back_restores_each_wired_tools_bytes_and_removes_its_entry(
        self, two_tools: ConfigPaths
    ) -> None:
        before = tool_files(two_tools)
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))
        flow.consent(WireConsent(offer(flow, "wallust").plan))
        run(flow.switch())
        assert tool_files(two_tools) != before

        flow.roll_back()

        assert tool_files(two_tools) == before
        assert bridges(two_tools) == []
        assert flow.rollback_notes == ()

    def test_a_relaunched_app_rolls_back_the_tools_the_sentinel_names(
        self, two_tools: ConfigPaths
    ) -> None:
        before = tool_files(two_tools)
        first = flow_for(two_tools, FakeClient())
        first.consent(WireConsent(offer(first, "matugen").plan))
        run(first.switch())
        # The app dies here; only the marker on disk remembers what was wired.

        relaunched = MigrationFlow(
            paths=two_tools,
            schema=sample_schema(),
            app_version=SAMPLE_APP_VERSION,
            client=FakeClient(),
        )
        pending = relaunched.pending_switch()
        assert pending is not None and pending.bridge_tools == ("matugen",)
        relaunched.roll_back(pending)

        assert tool_files(two_tools) == before
        assert bridges(two_tools) == []

    def test_a_tool_config_edited_since_setup_is_left_and_the_rollback_says_so(
        self, two_tools: ConfigPaths
    ) -> None:
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))
        run(flow.switch())
        config = two_tools.config_home / "matugen/config.toml"
        edited = config.read_text(encoding="utf-8") + "# the user's edit\n"
        config.write_text(edited, encoding="utf-8")

        flow.roll_back()

        assert config.read_text(encoding="utf-8") == edited
        assert not two_tools.entrypoint.exists()
        assert bridges(two_tools) == []
        assert flow.rollback_notes == (
            "~/.config/matugen/config.toml changed after matugen was set up, so it was left "
            "as it is. The copy from before setup is in "
            "~/.local/state/hyprtweaker/bridge-backups/.",
        )


class TestTheOrder:
    def test_the_sentinel_names_the_tools_and_the_entrypoint_has_their_line_before_any_write(
        self, two_tools: ConfigPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sentinel first, so a crash mid-wire still rolls the tool back; the Entrypoint
        line next, so noctalia never finds its template on without its `require` and appends
        one of its own to `hyprland.lua` (#166 Left open)."""
        seen: list[tuple[tuple[str, ...], bool]] = []
        real = bridge_setup.wire

        def watched(plan, consent, **kwargs):  # type: ignore[no-untyped-def]
            marker = sentinels.read(two_tools)
            text = two_tools.entrypoint.read_text(encoding="utf-8")
            seen.append((marker.bridge_tools if marker else (), MATUGEN_WAITING in text))
            return real(plan, consent, **kwargs)

        monkeypatch.setattr(bridge_setup, "wire", watched)
        flow = flow_for(two_tools, FakeClient())
        flow.consent(WireConsent(offer(flow, "matugen").plan))

        run(flow.switch())

        assert seen == [(("matugen",), True)]
