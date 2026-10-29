#!/usr/bin/env bash
# One tick of a benchx-arrow node: measure one Arrow commit, file the documents
# under runs/<sha>/<node>/, commit, push. Run it from a timer; it exits at once
# when nothing is pending or another tick holds the lock.
#
# Which commit: the first entry in queue/ this node has not measured, else the
# newest commit on apache/arrow main (first-parent, LOOKBACK deep) it has not
# measured. Nothing else decides what runs; the repository is the state.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
[[ -f $HERE/node.env ]] && source "$HERE/node.env"
NODE_NAME=${NODE_NAME:-$(hostname -s)}
WORK=${BENCHX_ARROW_WORK:-$HOME/.benchx-arrow}
BX=${BX:-$WORK/venv/bin/bx}
ARROW_URL=${ARROW_URL:-https://github.com/apache/arrow.git}
ARROW_REFERENCE=${ARROW_REFERENCE:-}
SUITE_FILTER=${SUITE_FILTER:-.}
LOOKBACK=${LOOKBACK:-50}
REPETITIONS=${REPETITIONS:-3}
CASE_TIMEOUT=${CASE_TIMEOUT:-600}
GIT_PUSH=${GIT_PUSH:-1}
ONLY=${1:-}   # optional: a commit to measure now, queue and main ignored
source "$ROOT/provider/clone-lib.sh"

mkdir -p "$WORK"
exec 9>"$WORK/node.lock"
flock -n 9 || { echo "bench-node: another tick is running" >&2; exit 0; }
cd "$ROOT"
[[ -x $BX ]] || { echo "bench-node: no bx at $BX (see node/node.env.example)" >&2; exit 2; }

if [[ $GIT_PUSH == 1 ]]; then git pull -q --ff-only; fi
ensure_clone
fetch_main

# ---- pick a commit -----------------------------------------------------------
pick() {
  local f name sha
  if [[ -n $ONLY ]]; then
    sha=$(resolve_commit "$ONLY") || { echo "bench-node: unknown commit $ONLY" >&2; exit 2; }
    echo "$sha argument"; return
  fi
  for f in "$ROOT"/queue/*; do
    [[ -f $f ]] || continue
    name=$(basename "$f")
    [[ $name == README.md ]] && continue
    sha=$(resolve_commit "$name") || { echo "bench-node: queue/$name is not a commit; skipping" >&2; continue; }
    [[ -d $ROOT/runs/$sha/$NODE_NAME ]] && continue
    echo "$sha queue/$name"; return
  done
  for sha in $(git -C "$CLONE" rev-list --first-parent -n "$LOOKBACK" origin/main); do
    [[ -d $ROOT/runs/$sha/$NODE_NAME ]] || { echo "$sha main"; return; }
  done
}
read -r SHA REASON < <(pick) || true
[[ -n ${SHA:-} ]] || { echo "bench-node: nothing to measure" >&2; exit 0; }

RUN=$ROOT/runs/$SHA/$NODE_NAME
mkdir -p "$RUN"
exec > >(tee -a "$RUN/log.txt") 2>&1
echo "== $(date -u +%FT%TZ) node=$NODE_NAME commit=$SHA reason=$REASON"
STARTED=$(date -u +%FT%TZ)

git -C "$CLONE" show -s --format='%H%n%P%n%aI%n%cI%n%s' "$SHA" | python3 -c '
import json, sys
sha, parents, author_date, committer_date, subject = sys.stdin.read().split("\n", 4)
print(json.dumps({"sha": sha, "parents": parents.split(), "author_date": author_date,
                  "committer_date": committer_date, "subject": subject.rstrip("\n"),
                  "branch": "main"}, indent=1))' >"$RUN/commit.json"

status() {  # status STATE [extra json fields...]
  python3 - "$RUN/status.json" "$@" <<'PY'
import json, sys
path, state, *rest = sys.argv[1:]
try: doc = json.load(open(path))
except Exception: doc = {}
doc["state"] = state
for item in rest:
    k, v = item.split("=", 1)
    doc[k] = json.loads(v)
json.dump(doc, open(path, "w"), indent=1)
PY
}
status running "node=\"$NODE_NAME\"" "commit=\"$SHA\"" "reason=\"$REASON\"" "started_at=\"$STARTED\""

# ---- build -------------------------------------------------------------------
if ! DESCRIPTION=$("$BX" target prepare "@cpp=$SHA" 2> >(tee "$RUN/build.log" >&2)); then
  echo "bench-node: the provider failed; keeping the tail of build.log"
  tail -n 300 "$RUN/build.log" >"$RUN/build.log.tail" && mv "$RUN/build.log.tail" "$RUN/build.log"
  status failed "finished_at=\"$(date -u +%FT%TZ)\"" 'stage="build"'
  FAILED_BUILD=1
else
  rm -f "$RUN/build.log"
  FAILED_BUILD=0
fi

# ---- measure -----------------------------------------------------------------
OK=0; FAILED=0; SUITES_JSON="{}"
if [[ $FAILED_BUILD == 0 ]]; then
  BUILD=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target"]["path"])' <<<"$DESCRIPTION")
  SRC=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target"]["source_dir"])' <<<"$DESCRIPTION")
  export ARROW_TEST_DATA=$SRC/testing/data PARQUET_TEST_DATA=$SRC/cpp/submodules/parquet-testing/data
  mapfile -t SUITES < <(cd "$BUILD/release" && ls -1 | grep -E -- '-benchmark$' | grep -E -- "$SUITE_FILTER" || true)
  echo "suites: ${#SUITES[@]}"
  for suite in "${SUITES[@]}"; do
    out=$RUN/suites/$suite
    mkdir -p "$out"
    echo "-- $suite"
    if "$BX" bench "$BUILD" --adapter google-benchmark --suite "release/$suite" \
         --project arrow --run-key "main/$SHA/$NODE_NAME" --out "$out" \
         --repetitions "$REPETITIONS" --case-timeout "$CASE_TIMEOUT" --no-ingest >"$out/table.txt" 2>&1; then
      rc=0; OK=$((OK + 1))
    else
      rc=$?; FAILED=$((FAILED + 1)); echo "   exit $rc"
    fi
    n=$(ls "$out"/*.json 2>/dev/null | grep -vc workorder- || true)
    SUITES_JSON=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d[sys.argv[2]]={"exit":int(sys.argv[3]),"results":int(sys.argv[4])}; print(json.dumps(d))' "$SUITES_JSON" "$suite" "$rc" "$n")
  done
  status finished "finished_at=\"$(date -u +%FT%TZ)\"" "suites=$SUITES_JSON" "suites_ok=$OK" "suites_failed=$FAILED"
fi
echo "== done: $OK suites ok, $FAILED failed, build failed: $FAILED_BUILD"

# ---- file it -----------------------------------------------------------------
if [[ $GIT_PUSH == 1 ]]; then
  git add "runs/$SHA"
  [[ $REASON == queue/* ]] && git rm -q "$REASON"
  if [[ $FAILED_BUILD == 1 ]]; then msg="main ${SHA:0:12} on $NODE_NAME: build failed"
  else msg="main ${SHA:0:12} on $NODE_NAME: $OK suites ok, $FAILED failed"; fi
  git commit -q -m "$msg"
  for attempt in 1 2 3 4 5; do
    git push -q && break
    echo "push rejected; rebasing (attempt $attempt)"
    git pull -q --rebase || { git rebase --abort; sleep 10; }
  done
fi
