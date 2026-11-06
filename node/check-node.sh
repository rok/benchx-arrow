#!/usr/bin/env bash
# Preflight for a benchmark node: check that everything a tick needs is in place
# before the timer runs one unattended. Changes nothing; exits non-zero when a
# required check fails.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
# node.env holds this node's defaults; a variable set on the command line
# (GIT_PUSH=0 node/bench-node.sh) wins over it.
OVERRIDES=$(for v in NODE_NAME BX PYTHON SUITE_FILTER LOOKBACK REPETITIONS CASE_TIMEOUT BUILD_JOBS GIT_PUSH; do [[ -v $v ]] && printf '%s=%q\n' "$v" "${!v}"; done)
[[ -f $HERE/node.env ]] && source "$HERE/node.env"
eval "$OVERRIDES"
GIT_PUSH=${GIT_PUSH:-1}
source "$ROOT/provider/clone-lib.sh"
BX=${BX:-$WORK/venv/bin/bx}
PYTHON=${PYTHON:-$WORK/venv/bin/python}
UNIT=benchx-$PROJECT
fail=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=1; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }

echo "project $PROJECT: $UPSTREAM_URL ($UPSTREAM_BRANCH)"
[[ -f $HERE/node.env ]] && ok "node/node.env" || warn "no node/node.env; using defaults (cp node/node.env.example node/node.env)"
echo "node ${NODE_NAME:-$(hostname -s)}, work dir $WORK"
[[ -x $BX ]] && ok "bx: $("$BX" --version 2>/dev/null || echo "$BX")" || bad "no bx at $BX (set BX in node/node.env)"
"$PYTHON" -c 'import chdb, pyarrow' 2>/dev/null && ok "python: $PYTHON" || bad "$PYTHON cannot import chdb and pyarrow (set PYTHON in node/node.env)"
for tool in git python3 flock; do command -v "$tool" >/dev/null && ok "$tool" || bad "$tool not found"; done
for tool in cmake ninja c++; do command -v "$tool" >/dev/null && ok "$tool (provider)" || warn "$tool not found (the Arrow provider needs it)"; done
command -v ccache >/dev/null && ok "ccache" || warn "no ccache: every build after a checkout is slower"
git ls-remote --exit-code -q "$UPSTREAM_URL" "refs/heads/$UPSTREAM_BRANCH" >/dev/null && ok "upstream reachable" || bad "cannot reach $UPSTREAM_URL $UPSTREAM_BRANCH"
if [[ $GIT_PUSH == 1 ]]; then
  git -C "$ROOT" push --dry-run -q 2>/dev/null && ok "this repository can push" || bad "git push --dry-run fails: set up credentials, or GIT_PUSH=0 for local runs"
  git -C "$ROOT" config user.email >/dev/null && ok "git identity: $(git -C "$ROOT" config user.name) <$(git -C "$ROOT" config user.email)>" || bad "no git user.email for the node's commits"
else
  warn "GIT_PUSH=0: results stay local"
fi
gov=/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
[[ -r $gov ]] && { [[ $(cat "$gov") == performance ]] && ok "CPU governor: performance" || warn "CPU governor: $(cat "$gov") (fixed 'performance' gives steadier numbers)"; }
systemctl --user is-enabled "$UNIT.timer" >/dev/null 2>&1 && ok "timer $UNIT.timer enabled" || warn "timer $UNIT.timer not enabled (node/install-units.sh --enable)"
if [[ -n $(git -C "$ROOT" status --porcelain -- runs) ]]; then
  warn "runs/ has uncommitted changes (a GIT_PUSH=0 tick?): the timer will treat those commits as measured and never push them; commit them or remove them (git clean -fd runs/)"
fi
exit $fail
