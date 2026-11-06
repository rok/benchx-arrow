#!/usr/bin/env python3
"""File one tick's run.parquet from what it left in its raw directory.

The node calls this after a tick: commit.json, status.json, log.txt and, when the
build failed, build.log become one row, the only part of them the repository keeps.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import marts  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw", required=True, type=Path, help="the tick's raw directory")
    ap.add_argument("--out", required=True, type=Path, help="runs/<sha>/<node>")
    ap.add_argument("--ingest-problems", type=int, default=0, help="suite directories the ingest rejected (shown on the page)")
    args = ap.parse_args(argv)
    log = "".join((args.raw / name).read_text(errors="replace") for name in ("log.txt", "build.log") if (args.raw / name).is_file())
    marts.write_run(args.out, json.loads((args.raw / "commit.json").read_text()), json.loads((args.raw / "status.json").read_text()),
                    log, args.ingest_problems)
    return 0


if __name__ == "__main__":
    sys.exit(main())
