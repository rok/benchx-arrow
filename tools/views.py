#!/usr/bin/env python3
"""Derive the page's view payloads from the tables marts.py wrote.

The page is static and must not depend on a query engine to render its fixed
views, so every number it shows is computed here, by chDB over the Parquet
tables, and written as small JSON documents under OUT/views:

    landing.json          stats, recent commits, movers (regressed / improved),
                          noisiest series, suite wall time on the latest commit
    series.json           one entry per series: the search index and the KPI tiles
    sparks.json           every series' last SPARK_POINTS points, for the front page's
                          grid of small charts, with the shared commit axis
    history/<suite>.json  every series of one suite as parallel arrays, plus the
                          suite's commit axis (history charts)
    commits/<sha>.json    one commit: its nodes' status and every change against
                          the previous measured point, largest first

"Regressed" and "improved" follow the quantity's direction: for lower-is-better,
a positive relative change is a regression. The changes are the heuristic from
marts.py (estimate against the previous measured point of the series), not
verdicts; the noise of the series is carried beside every change so a reader can
weigh it. A change counts as notable in the per-commit counts when it exceeds
NOTABLE (5 %) in magnitude.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import chdb

NOTABLE = 0.05
RECENT_COMMITS = 30
MOVERS = 15
NOISIEST = 10
SPARK_POINTS = 60


def rows(sql: str) -> list[dict]:
    """One chDB query, as a list of dicts (JSONEachRow)."""
    text = chdb.query(sql, "JSONEachRow").bytes().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def parquet(path: Path) -> str:
    return f"file('{path}', Parquet)"


def dump(doc, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n")


def signed(direction: str, rel_change) -> str | None:
    """'regressed' or 'improved' for a change, by the quantity's direction."""
    if rel_change is None:
        return None
    worse = rel_change > 0 if direction != "higher-is-better" else rel_change < 0
    return "regressed" if worse else "improved"


def build_views(data: Path) -> dict:
    data = data.resolve()
    out = data / "views"
    commits_t, changes_t, latest_t, points_t = (parquet(data / f"{n}.parquet") for n in ("commits", "changes", "latest", "points"))
    counts = {}

    commits = rows(f"""
        SELECT sha, short, parent, subject, toString(committer_date) AS committer_date, node, state, reason,
               toString(started_at) AS started_at, toString(finished_at) AS finished_at,
               suites_ok, suites_failed, results, commit_order
        FROM {commits_t} ORDER BY commit_order DESC, node""")
    notable = {(r["sha"], r["node"]): r for r in rows(f"""
        SELECT sha, node,
               countIf(abs(rel_change) > {NOTABLE} AND rel_change > 0) AS up,
               countIf(abs(rel_change) > {NOTABLE} AND rel_change < 0) AS down,
               count() AS compared
        FROM {changes_t} WHERE direction != 'higher-is-better' GROUP BY sha, node""")}
    for c in commits:
        n = notable.get((c["sha"], c["node"]), {})
        c["regressed"], c["improved"], c["compared"] = n.get("up", 0), n.get("down", 0), n.get("compared", 0)

    latest = rows(f"""
        SELECT suite, case, params, quantity, unit, direction, node, sha, commit_order, first_order,
               toString(committer_date) AS committer_date, points, estimate, vs_prev, vs_10, vs_100, noise, cv, noise_rank
        FROM {latest_t} ORDER BY suite, case, params, quantity, node""")
    counts["series"] = len(latest)
    dump(latest, out / "series.json")

    # Movers: the changes on each node's latest measured commit, largest first.
    movers = rows(f"""
        SELECT ch.suite AS suite, ch.case AS case, ch.params AS params, ch.quantity AS quantity, ch.unit AS unit,
               ch.direction AS direction, ch.node AS node, ch.sha AS sha, substring(ch.sha, 1, 12) AS short,
               ch.estimate AS estimate, ch.prev_estimate AS prev_estimate, ch.rel_change AS rel_change,
               ch.prev_is_parent AS prev_is_parent, ch.gap AS gap, lt.noise AS noise, lt.points AS points
        FROM {changes_t} AS ch
        INNER JOIN (SELECT node, max(commit_order) AS commit_order FROM {changes_t} GROUP BY node) AS top
            ON ch.node = top.node AND ch.commit_order = top.commit_order
        LEFT JOIN {latest_t} AS lt
            ON ch.suite = lt.suite AND ch.case = lt.case AND ch.params = lt.params
               AND ch.quantity = lt.quantity AND ch.node = lt.node
        WHERE ch.rel_change IS NOT NULL
        ORDER BY abs(ch.rel_change) DESC""")
    regressed = [m for m in movers if signed(m["direction"], m["rel_change"]) == "regressed"][:MOVERS]
    improved = [m for m in movers if signed(m["direction"], m["rel_change"]) == "improved"][:MOVERS]

    noisiest = rows(f"""
        SELECT suite, case, params, quantity, node, noise, cv, points, noise_rank
        FROM {latest_t} WHERE noise IS NOT NULL AND points >= 3
        ORDER BY noise DESC LIMIT {NOISIEST}""")

    suite_time = rows(f"""
        SELECT suite, node, sha, substring(sha, 1, 12) AS short, count() AS cases,
               countIf(status != 'success') AS failed,
               dateDiff('second', min(started_at), max(started_at)) AS seconds
        FROM {points_t} AS p
        INNER JOIN (SELECT node, max(commit_order) AS commit_order FROM {points_t} GROUP BY node) AS top
            ON p.node = top.node AND p.commit_order = top.commit_order
        GROUP BY suite, node, sha ORDER BY seconds DESC""")

    nodes = sorted({c["node"] for c in commits})
    newest = commits[0] if commits else None
    totals = rows(f"SELECT count() AS results, uniqExact(sha) AS commits FROM {points_t}")[0] if commits else {"results": 0, "commits": 0}
    landing = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "notable": NOTABLE,
        "stats": {"commits": totals["commits"], "results": totals["results"], "series": len(latest), "nodes": nodes,
                  "latest": newest},
        "recent_commits": commits[:RECENT_COMMITS],
        "movers": {"regressed": regressed, "improved": improved},
        "noisiest": noisiest,
        "suite_time": suite_time,
    }
    dump(landing, out / "landing.json")
    counts["recent_commits"] = len(landing["recent_commits"])

    # Sparks: the last SPARK_POINTS points of every series, for the front page grid.
    spark_axis = rows(f"""
        SELECT commit_order, s AS sha, substring(s, 1, 12) AS short, date
        FROM (SELECT commit_order, any(sha) AS s, toString(any(committer_date)) AS date
              FROM {points_t} GROUP BY commit_order)
        ORDER BY commit_order DESC LIMIT {SPARK_POINTS}""")
    spark_axis.reverse()
    spark_series = rows(f"""
        SELECT p.suite AS suite, p.case AS case, p.params AS params, p.quantity AS quantity, p.unit AS unit,
               p.direction AS direction, p.node AS node, p.orders AS orders, p.estimates AS estimates,
               lt.estimate AS latest, lt.vs_prev AS vs_prev, lt.noise AS noise, lt.points AS points, lt.sha AS sha
        FROM (
            SELECT suite, case, params, quantity, unit, direction, node,
                   groupArray(commit_order) AS orders, groupArray(estimate) AS estimates
            FROM (SELECT * FROM {points_t} WHERE status = 'success' AND estimate IS NOT NULL
                  ORDER BY commit_order DESC LIMIT {SPARK_POINTS} BY suite, case, params, quantity, node)
            GROUP BY suite, case, params, quantity, unit, direction, node
        ) AS p
        LEFT JOIN {latest_t} AS lt
            ON p.suite = lt.suite AND p.case = lt.case AND p.params = lt.params
               AND p.quantity = lt.quantity AND p.node = lt.node
        ORDER BY suite, case, params, quantity, node""")
    for r in spark_series:  # groupArray after ORDER BY DESC: put them back in commit order
        pairs = sorted(zip(r["orders"], r["estimates"]))
        r["orders"], r["estimates"] = [o for o, _ in pairs], [e for _, e in pairs]
    dump({"axis": spark_axis, "series": spark_series}, out / "sparks.json")
    counts["sparks"] = len(spark_series)

    # History per suite: parallel arrays per series, and the suite's commit axis.
    suites = [r["suite"] for r in rows(f"SELECT DISTINCT suite FROM {points_t} ORDER BY suite")]
    for stale in (out / "history").glob("*.json"):
        stale.unlink()
    for suite in suites:
        axis = rows(f"""
            SELECT p.commit_order AS commit_order, p.sha AS sha, substring(p.sha, 1, 12) AS short,
                   toString(any(p.committer_date)) AS date, any(c.subject) AS subject
            FROM {points_t} AS p LEFT JOIN {commits_t} AS c ON p.sha = c.sha AND p.node = c.node
            WHERE p.suite = '{suite}' GROUP BY commit_order, sha ORDER BY commit_order""")
        series = {}
        for r in rows(f"""
            SELECT case, params, quantity, unit, direction, node,
                   groupArray(commit_order) AS orders, groupArray(estimate) AS estimates,
                   groupArray(min) AS mins, groupArray(max) AS maxs, groupArray(n) AS ns, groupArray(status) AS statuses
            FROM (SELECT * FROM {points_t} WHERE suite = '{suite}' ORDER BY commit_order)
            GROUP BY case, params, quantity, unit, direction, node ORDER BY case, params, quantity, node"""):
            key = "|".join([r["case"], r["params"], r["quantity"], r["node"]])
            series[key] = r
        dump({"suite": suite, "commits": axis, "series": series}, out / "history" / f"{suite}.json")
    counts["history_suites"] = len(suites)

    # One document per commit: its nodes and every change, largest first.
    for stale in (out / "commits").glob("*.json"):
        stale.unlink()
    by_sha = {}
    for c in commits:
        by_sha.setdefault(c["sha"], []).append(c)
    changes_by_sha = {}
    for ch in rows(f"""
        SELECT ch.sha AS sha, ch.suite AS suite, ch.case AS case, ch.params AS params, ch.quantity AS quantity,
               ch.unit AS unit, ch.direction AS direction, ch.node AS node, ch.estimate AS estimate,
               ch.prev_estimate AS prev_estimate, ch.rel_change AS rel_change, ch.prev_sha AS prev_sha,
               ch.prev_is_parent AS prev_is_parent, ch.gap AS gap, lt.noise AS noise
        FROM {changes_t} AS ch
        LEFT JOIN {latest_t} AS lt
            ON ch.suite = lt.suite AND ch.case = lt.case AND ch.params = lt.params
               AND ch.quantity = lt.quantity AND ch.node = lt.node
        ORDER BY ch.sha, abs(ch.rel_change) DESC"""):
        sha = ch.pop("sha")
        changes_by_sha.setdefault(sha, []).append(ch)
    for sha, nodes_of in by_sha.items():
        head = nodes_of[0]
        dump({"sha": sha, "short": head["short"], "subject": head["subject"], "committer_date": head["committer_date"],
              "parent": head["parent"], "commit_order": head["commit_order"], "nodes": nodes_of,
              "changes": changes_by_sha.get(sha, [])}, out / "commits" / f"{sha}.json")
    counts["commit_pages"] = len(by_sha)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="site/data, holding marts.py's tables")
    args = ap.parse_args(argv)
    print(json.dumps(build_views(args.data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
