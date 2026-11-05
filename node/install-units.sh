#!/usr/bin/env bash
# Install the node's systemd user units for this checkout, wherever it lives:
# write benchx-<project>.{service,timer} (the name from project.json) to
# ~/.config/systemd/user with the repository's path filled in, reload, and
# (with --enable) start the timer. Two projects on one node get two timers.
#
#   node/install-units.sh            install, show what would run, do not start
#   node/install-units.sh --enable   install and start the timer now
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PROJECT=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/project.json")
UNIT=benchx-$PROJECT
UNITS=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
mkdir -p "$UNITS"
sed -e "s|@ROOT@|$ROOT|g" -e "s|@PROJECT@|$PROJECT|g" "$HERE/benchx-arrow.service" >"$UNITS/$UNIT.service"
sed -e "s|@PROJECT@|$PROJECT|g" "$HERE/benchx-arrow.timer" >"$UNITS/$UNIT.timer"
systemctl --user daemon-reload
echo "installed $UNITS/$UNIT.{service,timer}, running $ROOT/node/bench-node.sh"
if [[ -n $(git -C "$ROOT" status --porcelain -- runs) ]]; then
  echo "note: runs/ has uncommitted runs; the timer treats them as measured and will not push them. Commit them, or remove them with: git clean -fd runs/" >&2
fi
if [[ ${1:-} == --enable ]]; then
  systemctl --user enable --now "$UNIT.timer"
  systemctl --user list-timers "$UNIT.timer" --no-pager
  echo "keep it running without a login session: loginctl enable-linger $USER"
else
  echo "start it with: node/install-units.sh --enable   (or systemctl --user enable --now $UNIT.timer)"
fi
