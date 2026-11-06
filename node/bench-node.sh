#!/usr/bin/env bash
# One tick of a benchx-arrow node: measure one Arrow commit, file it as Parquet
# under runs/<sha>/<node>/, commit, push. The raw documents stay on the node, in
# $WORK/raw. Run it from a timer; it exits at once when nothing is pending or
# another tick holds the lock.
#
# Which commit: the first entry in queue/ this node has not measured, else the
# newest commit on the upstream branch (project.json; first-parent, LOOKBACK
# deep) it has not measured. Nothing else decides what runs; the repository is
# the state. run.parquet is written last, so an interrupted tick does not count
# as measured: the next tick clears what it left in $WORK/raw and retries.
#
# Coverage: each tick takes the newest unmeasured commit, so a node faster than
# the branch measures every commit and a slower one skips some; queue/ backfills.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
# node.env holds this node's defaults; a variable set on the command line
# (GIT_PUSH=0 node/bench-node.sh) wins over it.
OVERRIDES=$(for v in NODE_NAME BX PYTHON SUITE_FILTER LOOKBACK REPETITIONS CASE_TIMEOUT BUILD_JOBS GIT_PUSH; do [[ -v $v ]] && printf '%s=%q\n' "$v" "${!v}"; done)
[[ -f $HERE/node.env ]] && source "$HERE/node.env"
eval "$OVERRIDES"
NODE_NAME=${NODE_NAME:-$(hostname -s)}
SUITE_FILTER=${SUITE_FILTER:-.}
LOOKBACK=${LOOKBACK:-50}
REPETITIONS=${REPETITIONS:-3}
CASE_TIMEOUT=${CASE_TIMEOUT:-600}
GIT_PUSH=${GIT_PUSH:-1}
ONLY=${1:-}   # optional: a commit to measure now, queue and main ignored
source "$ROOT/provider/clone-lib.sh"
BX=${BX:-$WORK/venv/bin/bx}
PYTHON=${PYTHON:-$WORK/venv/bin/python}

mkdir -p "$WORK"
exec 9>"$WORK/node.lock"
flock -n 9 || { echo "bench-node: another tick is running" >&2; exit 0; }
cd "$ROOT"
[[ -x $BX ]] || { echo "bench-node: no bx at $BX (see node/node.env.example)" >&2; exit 2; }

if [[ $GIT_PUSH == 1 ]]; then git pull -q --ff-only; fi
ensure_clone
fetch_main

# ---- pick a commit -----------------------------------------------------------
measured() {  # measured SHA: this node filed a finished (or failed) run for it
  [[ -f $ROOT/runs/$1/$NODE_NAME/run.parquet ]]
}
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
    measured "$sha" && continue
    echo "$sha queue/$name"; return
  done
  for sha in $(git -C "$CLONE" rev-list --first-parent -n "$LOOKBACK" "origin/$UPSTREAM_BRANCH"); do
    measured "$sha" || { echo "$sha $UPSTREAM_BRANCH"; return; }
  done
}
read -r SHA REASON < <(pick) || true
[[ -n ${SHA:-} ]] || { echo "bench-node: nothing to measure" >&2; exit 0; }

RUN=$WORK/raw/$SHA/$NODE_NAME
[[ -d $RUN ]] && { echo "bench-node: clearing an earlier attempt in $RUN" >&2; rm -rf "$RUN"; }
mkdir -p "$RUN"
exec > >(tee -a "$RUN/log.txt") 2>&1
echo "== $(date -u +%FT%TZ) node=$NODE_NAME commit=$SHA reason=$REASON"
STARTED=$(date -u +%FT%TZ)

git -C "$CLONE" show -s --format='%H%n%P%n%aI%n%cI%n%s' "$SHA" | python3 -c '
import json, sys
sha, parents, author_date, committer_date, subject = sys.stdin.read().split("\n", 4)
print(json.dumps({"sha": sha, "parents": parents.split(), "author_date": author_date,
                  "committer_date": committer_date, "subject": subject.rstrip("\n"),
                  "branch": sys.argv[1]}, indent=1))' "$UPSTREAM_BRANCH" >"$RUN/commit.json"

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
  ABORTED=build
else
  rm -f "$RUN/build.log"
  ABORTED=
fi

# ---- measure -----------------------------------------------------------------
OK=0; FAILED=0; SUITES_JSON="{}"
if [[ -z $ABORTED ]]; then
  BUILD=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target"]["path"])' <<<"$DESCRIPTION")
  SRC=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target"]["source_dir"])' <<<"$DESCRIPTION")
  export ARROW_TEST_DATA=$SRC/testing/data PARQUET_TEST_DATA=$SRC/cpp/submodules/parquet-testing/data
  # Discovery: Arrow's benchmark binaries are release/*-benchmark. Another project
  # changes this line and --suite below (see PORTING.md); the adapter is in benchx.conf.json.
  mapfile -t SUITES < <(cd "$BUILD/release" && ls -1 | grep -E -- '-benchmark$' | grep -E -- "$SUITE_FILTER" || true)
  echo "suites: ${#SUITES[@]}"
  if [[ ${#SUITES[@]} == 0 ]]; then
    echo "bench-node: no benchmark binary matches SUITE_FILTER='$SUITE_FILTER'"
    status failed "finished_at=\"$(date -u +%FT%TZ)\"" 'stage="discover"'
    ABORTED=discover
  fi
fi
if [[ -z $ABORTED ]]; then
  for suite in "${SUITES[@]}"; do
    out=$RUN/suites/$suite
    mkdir -p "$out"
    echo "-- $suite"
    if "$BX" bench "$BUILD" --suite "release/$suite" \
         --project "$PROJECT" --run-key "$UPSTREAM_BRANCH/$SHA/$NODE_NAME" --out "$out" \
         --repetitions "$REPETITIONS" --case-timeout "$CASE_TIMEOUT" --no-ingest >"$out/table.txt" 2>&1; then
      rc=0; OK=$((OK + 1))
    else
      rc=$?; FAILED=$((FAILED + 1)); echo "   exit $rc"
    fi
    n=$(ls "$out"/*.json 2>/dev/null | grep -vcE 'workorder-|_load-average\.json$' || true)
    SUITES_JSON=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d[sys.argv[2]]={"exit":int(sys.argv[3]),"results":int(sys.argv[4])}; print(json.dumps(d))' "$SUITES_JSON" "$suite" "$rc" "$n")
  done
  status finished "finished_at=\"$(date -u +%FT%TZ)\"" "suites=$SUITES_JSON" "suites_ok=$OK" "suites_failed=$FAILED"
fi
echo "== done: $OK suites ok, $FAILED failed${ABORTED:+, stopped at $ABORTED}"

# ---- file it -----------------------------------------------------------------
# The repository takes Parquet only: a store of this run and one row about it.
FILED=$ROOT/runs/$SHA/$NODE_NAME
PROBLEMS=0
for dir in "$RUN"/suites/*/; do
  [[ -d $dir ]] || continue
  if ! out=$(BENCHX_HOME=$RUN "$BX" ingest "$dir" 2>&1); then
    echo "ingest problems in $dir:"; echo "$out" | tail -5; PROBLEMS=$((PROBLEMS + 1))
  fi
done
mkdir -p "$FILED"
rm -f "$FILED/results.parquet"
[[ -f $RUN/store.parquet ]] && mv "$RUN/store.parquet" "$FILED/results.parquet"
"$PYTHON" "$ROOT/tools/file_run.py" --raw "$RUN" --out "$FILED" --ingest-problems "$PROBLEMS"

if [[ $GIT_PUSH == 1 ]]; then
  git add "runs/$SHA"
  [[ $REASON == queue/* ]] && git rm -q "$REASON"
  if [[ -n $ABORTED ]]; then msg="$UPSTREAM_BRANCH ${SHA:0:12} on $NODE_NAME: $ABORTED failed"
  else msg="$UPSTREAM_BRANCH ${SHA:0:12} on $NODE_NAME: $OK suites ok, $FAILED failed"; fi
  git commit -q -m "$msg"
  for attempt in 1 2 3 4 5; do
    git push -q && break
    echo "push rejected; rebasing (attempt $attempt)"
    git pull -q --rebase || { git rebase --abort; sleep 10; }
  done
fi
