#!/usr/bin/env bash
# PROTOTYPE — start a nested Hyprland running the Lua config engine, inside the
# current session. Safe sandbox: separate instance signature, separate socket,
# the host session (still hyprlang) is untouched.
HERE="$(cd "$(dirname "$0")" && pwd)"
unset HYPRLAND_INSTANCE_SIGNATURE
export WAYLAND_DISPLAY="${HOST_WAYLAND_DISPLAY:-wayland-1}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec Hyprland -c "$HERE/hyprland.lua"
