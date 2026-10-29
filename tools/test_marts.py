"""The derived tables' arithmetic, on a synthetic points table.

marts.py's SQL takes a points file, so the change, latest and movers tables can
be checked without a benchx store: three commits, two series on one node, one
series with a gap and a regression at the end.
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

UTC = dt.timezone.utc


def point(sha, order, parent, case, estimate, status="success"):
    return {"sha": sha, "commit_order": order, "committer_date": dt.datetime(2026, 1, order, tzinfo=UTC), "parent": parent,
            "node": "n1", "suite": "s", "case": case, "params": "", "quantity": "wall-time", "unit": "s",
            "direction": "lower-is-better", "status": status, "reason": "", "estimate": estimate, "mean": estimate,
            "min": estimate, "max": estimate, "n": 1, "started_at": dt.datetime(2026, 1, order, tzinfo=UTC),
            "run_key": f"main/{sha}/n1", "ingest_key": f"{sha}:{case}"}


@pytest.fixture
def tables(tmp_path):
    # commits: c1 <- c2 <- c3 (c3's parent is c2, c2's parent is c1); "quiet" is not measured at c2
    rows = [
        point("c1", 1, "c0", "steady", 1.00), point("c2", 2, "c1", "steady", 1.01), point("c3", 3, "c2", "steady", 1.10),
        point("c1", 1, "c0", "quiet", 2.00), point("c3", 3, "c2", "quiet", 2.02),
        point("c3", 3, "c2", "broken", None, status="error"),
    ]
    points_path = tmp_path / "points.parquet"
    pq.write_table(pa.Table.from_pylist(rows), points_path)
    glob = marts.parquet(points_path)
    changes = marts.query(marts.CHANGES_SQL.format(points=glob))
    changes_path = tmp_path / "changes.parquet"
    pq.write_table(changes, changes_path)
    latest = marts.query(marts.LATEST_SQL.format(points=glob))
    movers = marts.query(marts.MOVERS_SQL.format(changes=marts.parquet(changes_path)))
    return {"changes": changes.to_pylist(), "latest": latest.to_pylist(), "movers": movers.to_pylist()}


def by(rows, **keys):
    out = [r for r in rows if all(r[k] == v for k, v in keys.items())]
    assert len(out) == 1, (keys, out)
    return out[0]


def test_changes_follow_the_series_and_record_gaps(tables):
    ch = tables["changes"]
    assert len(ch) == 3  # steady: c2, c3; quiet: c3; broken has no successful point
    c3 = by(ch, case="steady", sha="c3")
    assert c3["prev_sha"] == "c2" and c3["prev_is_parent"] and c3["gap"] == 1
    assert c3["rel_change"] == pytest.approx(1.10 / 1.01 - 1)
    q3 = by(ch, case="quiet", sha="c3")
    assert q3["prev_sha"] == "c1" and not q3["prev_is_parent"] and q3["gap"] == 2
    assert q3["rel_change"] == pytest.approx(0.01)


def test_latest_summarizes_each_series(tables):
    lt = tables["latest"]
    assert {r["case"] for r in lt} == {"steady", "quiet"}  # the errored series has no point
    s = by(lt, case="steady")
    assert s["sha"] == "c3" and s["points"] == 3 and s["first_order"] == 1
    assert s["estimate"] == pytest.approx(1.10)
    assert s["vs_prev"] == pytest.approx(1.10 / 1.01 - 1)
    assert s["vs_10"] is None and s["vs_100"] is None
    # noise: median of |1.10/1.01-1|, |1.01/1.00-1|
    assert s["noise"] == pytest.approx((abs(1.10 / 1.01 - 1) + 0.01) / 2)
    q = by(lt, case="quiet")
    assert q["noise"] == pytest.approx(0.01)
    assert q["noise_rank"] == 0.0 and s["noise_rank"] == 1.0  # quiet is the quieter of two


def test_movers_rank_changes_per_commit(tables):
    mv = tables["movers"]
    c3 = [r for r in mv if r["sha"] == "c3"]
    assert [r["case"] for r in c3] == ["steady", "quiet"]  # largest |change| first
    assert all(r["rel_change"] is not None for r in mv)


def test_read_commits_orders_by_committer_date(tmp_path):
    for sha, date, parent in [("b" * 40, "2026-02-01T10:00:00+00:00", "a" * 40), ("a" * 40, "2026-01-01T10:00:00+01:00", "9" * 40)]:
        d = tmp_path / sha / "n1"
        d.mkdir(parents=True)
        (d / "commit.json").write_text(json.dumps({"sha": sha, "parents": [parent], "committer_date": date, "author_date": date, "subject": "s"}))
        (d / "status.json").write_text(json.dumps({"state": "finished", "node": "n1", "suites_ok": 2, "suites_failed": 0,
                                                   "suites": {"x": {"exit": 0, "results": 5}}}))
    t = marts.read_commits(tmp_path).to_pylist()
    order = {r["sha"]: r["commit_order"] for r in t}
    assert order["a" * 40] == 1 and order["b" * 40] == 2
    assert by(t, sha="a" * 40)["results"] == 5 and by(t, sha="a" * 40)["parent"] == "9" * 40
