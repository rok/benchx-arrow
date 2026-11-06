"""The tables, on a store the pinned benchx wrote.

The other tests start from the tables, so they would not notice a new benchx
changing its results. testdata/results.parquet is one case as a node files it: a
wall time and the load-average condition beside it. Regenerate it when the pin
in requirements.txt moves: `bx ingest` one `bx bench` directory and copy its
store.parquet here. The version check fails until that is done.
"""
import importlib.metadata
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
import marts  # noqa: E402

RESULTS = Path(__file__).parent / "testdata" / "results.parquet"
SHA = "a99464f9c625c3f85cade9d7d50eb25297ca1f8d"


def test_fixture_comes_from_the_pinned_benchx():
    runners = {row["provenance"]["runner"]["version"] for row in pq.read_table(RESULTS, columns=["provenance"]).to_pylist()}
    assert runners == {importlib.metadata.version("benchx")}


def test_points_read_a_store_of_the_pinned_benchx(tmp_path):
    marts.write_run(tmp_path / "runs" / SHA / "desko",
                    {"sha": SHA, "parents": ["0" * 40], "subject": "s", "committer_date": "2026-01-01T00:00:00+00:00"},
                    {"state": "finished", "node": "desko"})
    marts.write(marts.read_commits(tmp_path / "runs"), tmp_path / "commits.parquet")

    points = marts.query(marts.POINTS_SQL.format(store=marts.parquet(RESULTS), commits=marts.parquet(tmp_path / "commits.parquet")))

    assert points.num_rows == 1  # the load-average is a condition, not a benchmark
    row = points.to_pylist()[0]
    assert (row["sha"], row["node"], row["suite"], row["case"]) == (SHA, "desko", "arrow-bit-util-benchmark", "SetBitsTo/2")
    assert (row["quantity"], row["unit"], row["direction"], row["status"]) == ("wall-time", "s", "lower-is-better", "success")
    assert row["n"] == 1 and 0 < row["min"] <= row["estimate"] <= row["max"]


def test_a_filed_run_is_read_back_with_its_log(tmp_path):
    marts.write_run(tmp_path / SHA / "n1", {"sha": SHA, "parents": []}, {"state": "failed"}, "the build broke", ingest_problems=2)
    run = marts.read_runs(tmp_path)[0]
    assert (run["node"], run["state"], run["log"], run["ingest_problems"]) == ("n1", "failed", "the build broke", 2)
