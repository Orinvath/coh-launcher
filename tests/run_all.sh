#!/usr/bin/env bash
# Runs every test on its own hidden screen (Xvfb). Nothing touches the real desktop, and every process started here
# is stopped by its own process id when the run ends.
set -u
cd "$(dirname "$0")/.."
WORK="$(mktemp -d "${TMPDIR:-/tmp}/coh-tests.XXXXXX")"
XVFB_PID=""
cleanup() {
  [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null && wait "$XVFB_PID" 2>/dev/null
  rm -rf "$WORK" tests/tmp
}
trap cleanup EXIT

Xvfb -screen 0 1920x1080x24 -nolisten tcp -displayfd 3 3>"$WORK/display" >"$WORK/xvfb.log" 2>&1 &
XVFB_PID=$!
for _ in $(seq 1 100); do
  [ -s "$WORK/display" ] && break
  sleep 0.1
done
[ -s "$WORK/display" ] || { echo "The hidden screen did not start."; exit 1; }

export DISPLAY=":$(tr -d '\n' < "$WORK/display")"
unset WAYLAND_DISPLAY
export QT_QPA_PLATFORM=xcb GDK_BACKEND=x11 QT_ENABLE_HIGHDPI_SCALING=0
unset DBUS_SESSION_BUS_ADDRESS
export HOME="$WORK/home"
mkdir -p "$HOME"

status=0
for file in tests/test_*.py; do
  echo "=== $file"
  python3 -m unittest discover -v -s tests -p "$(basename "$file")" 2>&1 | grep -v "^\[page\]\|Failed to connect to the bus\|dbus/bus.cc" || true
  [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
done
[ "$status" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit "$status"
