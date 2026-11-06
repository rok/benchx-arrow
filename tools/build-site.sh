#!/usr/bin/env bash
# Build the derived layer: derive the dashboard's tables under site/data from the
# Parquet files under runs/, and copy project.json beside them for the page. The
# same script runs in the publish workflow and on a laptop. Nothing it writes is
# committed.
#
#   PYTHON    a python with tools/requirements.txt
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}

"$PYTHON" "$ROOT/tools/marts.py" --runs "$ROOT/runs" --out "$ROOT/site/data"
cp "$ROOT/project.json" "$ROOT/site/data/project.json"
"$PYTHON" "$ROOT/tools/views.py" --data "$ROOT/site/data"
