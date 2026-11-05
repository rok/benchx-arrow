#!/usr/bin/env bash
# Build the derived layer: ingest every run into a benchx store, then derive the
# dashboard's tables under site/data, and copy project.json beside them for the
# page. The same script runs in the publish workflow and on a laptop. Nothing it
# writes is committed. A suite directory the ingest rejects does not stop the
# build; the count lands in the manifest and the page shows a warning.
#
#   BENCHX_HOME   where the store lives (default: _home/ in the repository)
#   BX, PYTHON    the benchx command and a python with chdb and pyarrow
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export BENCHX_HOME=${BENCHX_HOME:-$ROOT/_home}
BX=${BX:-bx}
PYTHON=${PYTHON:-python3}
mkdir -p "$BENCHX_HOME"

# The store is a cache of runs/, and ingesting only adds. When a suite directory
# it was built from has since been removed or replaced (a run deleted to be
# re-measured), start the store over, so a cached build and a clean one agree.
# ingested.txt lists each directory with a fingerprint of its files.
mapfile -t DIRS < <(cd "$ROOT" && find runs -mindepth 4 -maxdepth 4 -type d -path 'runs/*/*/suites/*' | sort)
fingerprints=$(for dir in "${DIRS[@]}"; do
  echo "$dir $(cd "$ROOT/$dir" && find . -type f -printf '%P %s\n' | sort | sha1sum | cut -c1-16)"
done)
if [[ -f $BENCHX_HOME/store.parquet && -f $BENCHX_HOME/ingested.txt ]] \
   && [[ -n $(comm -23 <(sort "$BENCHX_HOME/ingested.txt") <(sort <<<"$fingerprints")) ]]; then
  echo "runs were removed or replaced since the cached store was built; rebuilding it" >&2
  rm -f "$BENCHX_HOME/store.parquet"
fi

n=0; problems=0
for dir in "${DIRS[@]}"; do
  if ! out=$("$BX" ingest "$dir" 2>&1); then
    echo "ingest problems in $dir:" >&2; echo "$out" | tail -5 >&2; problems=$((problems + 1))
  fi
  n=$((n + 1))
done
printf '%s\n' "$fingerprints" >"$BENCHX_HOME/ingested.txt"
echo "ingested $n suite directories ($problems with problems) into $BENCHX_HOME/store.parquet" >&2

"$PYTHON" "$ROOT/tools/marts.py" --store "$BENCHX_HOME/store.parquet" --runs "$ROOT/runs" --out "$ROOT/site/data" \
  --ingest-problems "$problems"
cp "$ROOT/project.json" "$ROOT/site/data/project.json"
"$PYTHON" "$ROOT/tools/views.py" --data "$ROOT/site/data"
