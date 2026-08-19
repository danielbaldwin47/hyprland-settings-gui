#!/usr/bin/env bash
# PROTOTYPE — screenshot the page inside the nested Hyprland (tall output, so a
# whole section fits in one frame). Usage: shot.sh <out.png> <args to app.py...>
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$1"; shift
: "${NESTED_SIG:?set NESTED_SIG to the nested instance signature}"
: "${NESTED_DISPLAY:=wayland-2}"

WAYLAND_DISPLAY="$NESTED_DISPLAY" HYPRLAND_INSTANCE_SIGNATURE="$NESTED_SIG" \
  python3 "$HERE/app.py" "$@" >"$HERE/out/app.log" 2>&1 &
APP=$!
sleep 6
WAYLAND_DISPLAY="$NESTED_DISPLAY" HYPRLAND_INSTANCE_SIGNATURE="$NESTED_SIG" grim "$OUT"
kill $APP 2>/dev/null
wait $APP 2>/dev/null
echo "wrote $OUT"
