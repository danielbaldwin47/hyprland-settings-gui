"""The `HARNESS_DRM_CARD` opt-in (#144): what it hands the nested Hyprland, and what it refuses.

The no-op seat is the dangerous half -- unmasked, it reads the developer's real keyboard -- so
the tests pin that it never appears without the sandbox that hides `/dev/input`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from harness import nested
from harness.nested import (
    DRM_CARD_VARIABLE,
    HarnessUnavailable,
    drm_card_problem,
    drm_wrapped,
    render_node_of,
    unavailable_reason,
)


def fake_sysfs(
    root: Path,
    *,
    device: str = "devices/pci0000:00/0000:00:02.0",
    driver: str = "i915",
    render: bool = True,
    connector: str = "HDMI-A-1",
    status: str = "",
) -> Path:
    """A `/sys/class/drm` with `card0`, optionally its render node and one connector.

    `card0/device` is a link to `device`, as in sysfs; the device's `driver` links to
    `driver`. A PCI GPU is the default; `VKMS` below is the layout a runner shows.
    """
    sys_drm = root / "sys-drm"
    target = root / device
    (target / "drm").mkdir(parents=True)
    if render:
        (target / "drm" / "renderD129").mkdir()
    (root / "drivers" / driver).mkdir(parents=True)
    (target / "driver").symlink_to(root / "drivers" / driver)
    (sys_drm / "card0").mkdir(parents=True)
    (sys_drm / "card0" / "device").symlink_to(target)
    if status:
        (sys_drm / f"card0-{connector}").mkdir()
        (sys_drm / f"card0-{connector}" / "status").write_text(status + "\n")
    return sys_drm


#: A `vkms` card as a stock `ubuntu-latest` runner shows it (kernel 6.17, CI run 36957432658):
#: since Linux 6.15 `vkms` sits on the faux bus, its driver is `faux_driver`, and it has
#: no render node.
VKMS = {"device": "devices/faux/vkms", "driver": "faux_driver", "render": False}


@pytest.fixture
def card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A card node that exists, with `bwrap` on the path."""
    node = tmp_path / "card0"
    node.touch()
    monkeypatch.setattr(nested.shutil, "which", lambda name: f"/usr/bin/{name}")
    return node


def test_the_card_is_handed_over_inside_the_sandbox_with_the_noop_seat(
    tmp_path: Path, card: Path
) -> None:
    argv, env = drm_wrapped(["Hyprland", "-c", "x"], {"HOME": "/h"}, card, fake_sysfs(tmp_path))

    assert argv[0] == "bwrap"
    assert argv[-3:] == ["Hyprland", "-c", "x"]
    masked = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--tmpfs"]
    assert masked == ["/dev/dri", "/dev/input"]
    assert "--die-with-parent" in argv
    assert env == {"HOME": "/h", "LIBSEAT_BACKEND": "noop", "AQ_DRM_DEVICES": str(card)}
    # Only this card and its own render node are bound back over the emptied /dev/dri.
    bound = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--dev-bind"]
    assert bound == ["/", str(card), "/dev/dri/renderD129"]


def test_the_launch_environment_carries_no_seat_without_the_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-op seat is only ever added by `drm_wrapped`, next to the sandbox."""
    monkeypatch.delenv(DRM_CARD_VARIABLE, raising=False)
    monkeypatch.delenv("LIBSEAT_BACKEND", raising=False)
    environment = nested.NestedHyprland(tmp_path / "c.lua", home=tmp_path).launch_environment

    assert "LIBSEAT_BACKEND" not in environment


def test_a_vkms_card_is_handed_over_alone(tmp_path: Path) -> None:
    """No render node exists to bind: the card is the only device left in /dev/dri."""
    card = Path("/dev/dri/card0")
    argv, env = drm_wrapped(["Hyprland"], {}, card, fake_sysfs(tmp_path, **VKMS))

    bound = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--dev-bind"]
    assert bound == ["/", "/dev/dri/card0"]
    masked = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--tmpfs"]
    assert masked == ["/dev/dri", "/dev/input"]
    assert env == {"LIBSEAT_BACKEND": "noop", "AQ_DRM_DEVICES": "/dev/dri/card0"}


def test_a_card_with_no_render_node_cannot_be_wrapped(tmp_path: Path) -> None:
    with pytest.raises(HarnessUnavailable, match="no render node"):
        drm_wrapped(
            ["Hyprland"], {}, Path("/dev/dri/card0"), fake_sysfs(tmp_path, render=False)
        )


def test_the_render_node_comes_from_the_same_gpu_as_the_card(tmp_path: Path) -> None:
    assert render_node_of(Path("/dev/dri/card0"), fake_sysfs(tmp_path)) == Path(
        "/dev/dri/renderD129"
    )
    assert (
        render_node_of(Path("/dev/dri/card0"), fake_sysfs(tmp_path / "b", render=False)) is None
    )


def test_a_card_with_no_problem_is_accepted(tmp_path: Path, card: Path) -> None:
    assert drm_card_problem(card, fake_sysfs(tmp_path, status="disconnected")) is None


def test_a_vkms_card_with_its_connector_off_is_accepted_without_a_render_node(
    tmp_path: Path, card: Path
) -> None:
    sys_drm = fake_sysfs(tmp_path, **VKMS, connector="Virtual-2", status="disconnected")

    assert drm_card_problem(card, sys_drm) is None


@pytest.mark.parametrize(
    ("layout", "reason"),
    [
        ({"driver": "nvidia"}, "an NVIDIA card cannot allocate the headless output (#144)"),
        ({"render": False}, "the card has no render node"),
        # Render-node-less and on the faux bus, but not vkms: only vkms is known virtual.
        ({**VKMS, "device": "devices/faux/other"}, "the card has no render node"),
        ({"driver": "hyperv_drm", "render": False}, "the card has no render node"),
        (
            {"status": "connected"},
            "card0-HDMI-A-1 is connected: the nested Hyprland would take over a monitor "
            "the desktop is using",
        ),
        (
            {**VKMS, "connector": "Virtual-2", "status": "connected"},
            "card0-Virtual-2 is connected: the nested Hyprland would take over a monitor "
            "the desktop is using",
        ),
    ],
)
def test_a_card_that_would_break_the_desktop_or_the_output_is_refused(
    tmp_path: Path, card: Path, layout: dict[str, Any], reason: str
) -> None:
    assert drm_card_problem(card, fake_sysfs(tmp_path, **layout)) == reason


def test_a_missing_bwrap_or_node_is_refused(
    tmp_path: Path, card: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys_drm = fake_sysfs(tmp_path)

    assert drm_card_problem(tmp_path / "gone", sys_drm) == "no such device"
    monkeypatch.setattr(nested.shutil, "which", lambda name: None)
    problem = drm_card_problem(card, sys_drm)
    assert problem is not None
    assert "bwrap" in problem


def test_the_skip_reason_names_the_variable_and_the_card(
    card: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-9")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/0")
    monkeypatch.setenv(DRM_CARD_VARIABLE, str(card.parent / "gone"))

    assert unavailable_reason() == f"{DRM_CARD_VARIABLE}={card.parent / 'gone'}: no such device"
