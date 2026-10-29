#!/usr/bin/env bash
# Build the derived layer: ingest every run into a benchx store, then derive the
# dashboard's tables under site/data. The same script runs in the publish
# workflow and on a laptop. Nothing it writes is committed.
#
#   BENCHX_HOME   where the store lives (default: _home/ in the repository)
#   BX, PYTHON    the benchx command and a python with chdb and pyarrow
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export BENCHX_HOME=${BENCHX_HOME:-$ROOT/_home}
BX=${BX:-bx}
PYTHON=${PYTHON:-python3}
mkdir -p "$BENCHX_HOME"

n=0; problems=0
while IFS= read -r dir; do
  if ! out=$("$BX" ingest "$dir" 2>&1); then
    echo "ingest problems in $dir:" >&2; echo "$out" | tail -5 >&2; problems=$((problems + 1))
  fi
  n=$((n + 1))
done < <(cd "$ROOT" && find runs -type d -path '*/suites/*' -prune | sort)
echo "ingested $n suite directories ($problems with problems) into $BENCHX_HOME/store.parquet" >&2

"$PYTHON" "$ROOT/tools/marts.py" --store "$BENCHX_HOME/store.parquet" --runs "$ROOT/runs" --out "$ROOT/site/data"
