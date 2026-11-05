"""The page's views, on synthetic runs and points.

Four commits on one node. "flaky" alternates error and success, so its history
must keep every estimate on its own commit; "broken" never succeeds and must
still be listed, and fail on the newest commit; "fast" is higher-is-better, so
a drop is a regression. A second case builds the views with no results at all,
the state of a fresh template.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import marts  # noqa: E402
import views  # noqa: E402

UTC = dt.timezone.utc
SHAS = [f"{i}" * 40 for i in range(1, 5)]


def point(i, case, estimate, status="success", quantity="wall-time", direction="lower-is-better"):
    sha = SHAS[i - 1]
    return {"sha": sha, "commit_order": i, "committer_date": dt.datetime(2026, 1, i, tzinfo=UTC),
            "parent": SHAS[i - 2] if i > 1 else "0" * 40, "node": "n1", "suite": "s", "case": case, "params": "",
            "quantity": quantity, "unit": "s", "direction": direction, "status": status,
            "reason": "" if status == "success" else "harness.no-output", "estimate": estimate, "mean": estimate,
            "min": estimate, "max": estimate, "n": 1 if estimate is not None else 0,
            "started_at": dt.datetime(2026, 1, i, tzinfo=UTC), "run_key": f"main/{sha}/n1", "ingest_key": f"{sha}:{case}"}


def write_runs(runs: Path, n: int):
    for i in range(1, n + 1):
        d = runs / SHAS[i - 1] / "n1"
        d.mkdir(parents=True)
        (d / "commit.json").write_text(json.dumps({"sha": SHAS[i - 1], "parents": [SHAS[i - 2] if i > 1 else "0" * 40],
                                                   "committer_date": f"2026-01-0{i}T00:00:00+00:00", "subject": f"commit {i}"}))
        (d / "status.json").write_text(json.dumps({"state": "finished", "node": "n1", "suites_ok": 1, "suites_failed": 0,
                                                   "suites": {"s": {"exit": 0, "results": 3}}}))


def build(tmp_path: Path, points: list[dict], n_commits: int = 4) -> Path:
    runs, data = tmp_path / "runs", tmp_path / "data"
    write_runs(runs, n_commits)
    data.mkdir()
    marts.write(marts.read_commits(runs), data / "commits.parquet")
    if points:
        pq.write_table(pa.Table.from_pylist(points), data / "points.parquet")
        glob = marts.parquet(data / "points.parquet")
        pq.write_table(marts.query(marts.CHANGES_SQL.format(points=glob)), data / "changes.parquet")
        pq.write_table(marts.query(marts.LATEST_SQL.format(points=glob)), data / "latest.parquet")
    else:
        for name in ("points", "changes", "latest", "movers"):
            pq.write_table(pa.table({}), data / f"{name}.parquet")
    (data / "manifest.json").write_text(json.dumps({"counts": {"points": len(points)}}))
    views.build_views(data)
    return data / "views"


def load(path: Path):
    return json.loads(path.read_text())


@pytest.fixture
def out(tmp_path):
    pts = [
        point(1, "flaky", None, "error"), point(2, "flaky", 1.0), point(3, "flaky", None, "error"), point(4, "flaky", 1.1),
        *[point(i, "broken", None, "error") for i in range(1, 5)],
        *[point(i, "fast", v, quantity="items-per-second", direction="higher-is-better") for i, v in [(1, 100), (2, 101), (3, 100), (4, 80)]],
    ]
    return build(tmp_path, pts)


def test_history_keeps_each_estimate_on_its_commit(out):
    flaky = load(out / "history" / "s.json")["series"]["flaky||wall-time|n1"]
    assert flaky["orders"] == [1, 2, 3, 4]
    assert flaky["estimates"] == [None, 1.0, None, 1.1]
    assert flaky["statuses"] == ["error", "success", "error", "success"]
    spark = next(s for s in load(out / "sparks.json")["series"] if s["case"] == "flaky")
    assert spark["orders"] == [2, 4] and spark["estimates"] == [1.0, 1.1]


def test_failures_are_listed_not_hidden(out):
    series = {s["case"]: s for s in load(out / "series.json")}
    assert series["broken"]["estimate"] is None and series["broken"]["status"] == "error" and series["broken"]["points"] == 0
    assert series["flaky"]["status"] == "success"
    landing = load(out / "landing.json")
    assert [f["case"] for f in landing["failures"]] == ["broken"]
    assert landing["latest_runs"][0]["failed_results"] == 1
    newest = load(out / "commits" / f"{SHAS[3]}.json")
    assert [f["case"] for f in newest["failures"]] == ["broken"]


def test_counts_follow_the_direction(out):
    newest = load(out / "landing.json")["latest_runs"][0]
    # flaky +10 % (lower is better: regressed); fast -20 % (higher is better: regressed)
    assert newest["regressed"] == 2 and newest["improved"] == 0
    assert {m["case"] for m in load(out / "landing.json")["movers"]["regressed"]} == {"flaky", "fast"}


def test_no_results_still_builds(tmp_path):
    out = build(tmp_path, [], n_commits=2)
    landing = load(out / "landing.json")
    assert landing["stats"]["results"] == 0 and len(landing["recent_commits"]) == 2
    assert load(out / "series.json") == [] and load(out / "sparks.json") == {"axis": [], "series": []}
    assert load(out / "commits" / f"{SHAS[1]}.json")["changes"] == []


def test_last_executed_is_by_finish_time_not_commit_order():
    commits = [  # sorted by commit order, newest first, as build_views has them
        {"sha": "new", "node": "n1", "finished_at": "2026-01-02 10:00:00", "started_at": None},
        {"sha": "old", "node": "n1", "finished_at": "2026-01-03 09:00:00", "started_at": None},  # a later backfill
        {"sha": "new", "node": "n2", "finished_at": None, "started_at": "2026-01-01 08:00:00"},  # still running
    ]
    assert views.newest_commits(commits)["n1"]["sha"] == "new"
    last = views.last_executed(commits)
    assert last["n1"]["sha"] == "old" and last["n2"]["sha"] == "new"
