#!/usr/bin/env python3
"""Derive the page's view payloads from the tables marts.py wrote.

The page is static and must not depend on a query engine to render its fixed
views, so every number it shows is computed here, by chDB over the Parquet
tables, and written as small JSON documents under OUT/views:

    landing.json          stats, each node's run of its newest commit (its movers
                          and failures) and its most recently finished run,
                          recent commits, noisiest series, suite wall time
    series.json           one entry per series: the search index and the KPI tiles,
                          with the status of its latest attempt; a series that has
                          never succeeded is listed too, with no estimate
    sparks.json           every series' last SPARK_POINTS points, for the front page's
                          grid of small charts, with the shared commit axis
    history/<suite>.json  every series of one suite as parallel arrays (one entry
                          per attempt, aligned: estimates are null where it failed),
                          plus the suite's commit axis (history charts)
    commits/<sha>.json    one commit: its nodes' status, its failed results, and
                          every change against the previous measured point

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
SERIES = "suite, case, params, quantity, node"


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


# The same rule in SQL, for counting.
WORSE = "if(direction = 'higher-is-better', rel_change < 0, rel_change > 0)"


def series_key(r: dict) -> tuple:
    return (r["suite"], r["case"], r["params"], r["quantity"], r["node"])


def build_views(data: Path) -> dict:
    data = data.resolve()
    out = data / "views"
    manifest = json.loads((data / "manifest.json").read_text())
    has_points = manifest["counts"].get("points", 0) > 0
    commits_t, changes_t, latest_t, points_t = (parquet(data / f"{n}.parquet") for n in ("commits", "changes", "latest", "points"))
    counts = {}

    commits = rows(f"""
        SELECT sha, short, parent, subject, toString(committer_date) AS committer_date, node, state, reason,
               toString(started_at) AS started_at, toString(finished_at) AS finished_at,
               suites_ok, suites_failed, results, commit_order
        FROM {commits_t} ORDER BY commit_order DESC, node""")
    notable, failed_results = {}, {}
    if has_points:
        notable = {(r["sha"], r["node"]): r for r in rows(f"""
            SELECT sha, node,
                   countIf(abs(rel_change) > {NOTABLE} AND {WORSE})     AS regressed,
                   countIf(abs(rel_change) > {NOTABLE} AND NOT {WORSE}) AS improved,
                   count() AS compared
            FROM {changes_t} GROUP BY sha, node""")}
        failed_results = {(r["sha"], r["node"]): r["failed"] for r in rows(f"""
            SELECT sha, node, countIf(status != 'success') AS failed FROM {points_t} GROUP BY sha, node""")}
    for c in commits:
        n = notable.get((c["sha"], c["node"]), {})
        c["regressed"], c["improved"], c["compared"] = n.get("regressed", 0), n.get("improved", 0), n.get("compared", 0)
        c["failed_results"] = failed_results.get((c["sha"], c["node"]), 0)

    if not has_points:
        return build_empty(out, commits)

    # The latest attempt of every series, successful or not: a series whose last
    # run failed must not keep looking healthy on its last good value.
    attempts = {series_key(r): r for r in rows(f"""
        SELECT {SERIES}, any(unit) AS unit, any(direction) AS direction,
               argMax(status, commit_order) AS status, argMax(reason, commit_order) AS reason,
               max(commit_order) AS attempt_order, argMax(sha, commit_order) AS attempt_sha
        FROM {points_t} GROUP BY {SERIES}""")}

    latest = rows(f"""
        SELECT suite, case, params, quantity, unit, direction, node, sha, commit_order, first_order,
               toString(committer_date) AS committer_date, points, estimate, vs_prev, vs_10, vs_100, noise, cv, noise_rank
        FROM {latest_t} ORDER BY {SERIES}""")
    seen = set()
    for r in latest:
        a = attempts[series_key(r)]
        r.update(status=a["status"], reason=a["reason"], attempt_order=a["attempt_order"], attempt_sha=a["attempt_sha"])
        seen.add(series_key(r))
    for key, a in attempts.items():  # never succeeded: listed, with nothing to show but the failure
        if key not in seen:
            latest.append({**a, "sha": a["attempt_sha"], "commit_order": a["attempt_order"], "first_order": None,
                           "committer_date": None, "points": 0, "estimate": None, "vs_prev": None, "vs_10": None,
                           "vs_100": None, "noise": None, "cv": None, "noise_rank": None})
    latest.sort(key=series_key)
    counts["series"] = len(latest)
    dump(latest, out / "series.json")

    # Movers: the changes on each node's latest measured commit, largest first.
    movers = rows(f"""
        SELECT ch.suite AS suite, ch.case AS case, ch.params AS params, ch.quantity AS quantity, ch.unit AS unit,
               ch.direction AS direction, ch.node AS node, ch.sha AS sha, substring(ch.sha, 1, 12) AS short,
               ch.estimate AS estimate, ch.prev_estimate AS prev_estimate, ch.rel_change AS rel_change,
               ch.prev_sha AS prev_sha, ch.prev_is_parent AS prev_is_parent, ch.gap AS gap,
               lt.noise AS noise, lt.points AS points
        FROM {changes_t} AS ch
        INNER JOIN (SELECT node, max(commit_order) AS commit_order FROM {changes_t} GROUP BY node) AS top
            ON ch.node = top.node AND ch.commit_order = top.commit_order
        LEFT JOIN {latest_t} AS lt
            ON ch.suite = lt.suite AND ch.case = lt.case AND ch.params = lt.params
               AND ch.quantity = lt.quantity AND ch.node = lt.node
        WHERE ch.rel_change IS NOT NULL
        ORDER BY abs(ch.rel_change) DESC""")
    regressed, improved, per_node = [], [], {}
    for m in movers:  # MOVERS per node and direction, so one node cannot crowd out another
        kind = signed(m["direction"], m["rel_change"])
        k = (m["node"], kind)
        per_node[k] = per_node.get(k, 0) + 1
        if per_node[k] <= MOVERS:
            (regressed if kind == "regressed" else improved).append(m)

    # Failures: every series whose attempt on its node's newest commit did not succeed.
    newest_by_node = newest_commits(commits)
    by_series = {series_key(r): r for r in latest}
    failures = []
    for key, a in sorted(attempts.items()):
        top = newest_by_node.get(key[4])
        if top and a["attempt_sha"] == top["sha"] and a["status"] != "success":
            last_ok = by_series[key]
            failures.append({"suite": key[0], "case": key[1], "params": key[2], "quantity": key[3], "node": key[4],
                             "status": a["status"], "reason": a["reason"], "sha": a["attempt_sha"],
                             "last_success_sha": last_ok["sha"] if last_ok.get("points") else None})

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
    totals = rows(f"SELECT count() AS results, uniqExact(sha) AS commits FROM {points_t}")[0]
    landing = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "notable": NOTABLE,
        "stats": {"commits": totals["commits"], "results": totals["results"], "series": len(latest), "nodes": nodes,
                  "latest": commits[0] if commits else None},
        "latest_runs": list(newest_by_node.values()),
        "last_executed": last_executed(commits),
        "recent_commits": commits[:RECENT_COMMITS],
        "movers": {"regressed": regressed, "improved": improved},
        "failures": failures,
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
               p.direction AS direction, p.node AS node, p.pts AS pts,
               lt.estimate AS latest, lt.vs_prev AS vs_prev, lt.noise AS noise, lt.points AS points, lt.sha AS sha
        FROM (
            SELECT suite, case, params, quantity, unit, direction, node, groupArray((commit_order, estimate)) AS pts
            FROM (SELECT * FROM {points_t} WHERE status = 'success' AND estimate IS NOT NULL
                  ORDER BY commit_order DESC LIMIT {SPARK_POINTS} BY {SERIES})
            GROUP BY suite, case, params, quantity, unit, direction, node
        ) AS p
        LEFT JOIN {latest_t} AS lt
            ON p.suite = lt.suite AND p.case = lt.case AND p.params = lt.params
               AND p.quantity = lt.quantity AND p.node = lt.node
        ORDER BY suite, case, params, quantity, node""")
    for r in spark_series:
        pts = sorted(r.pop("pts"))  # groupArray keeps no order; tuples keep each estimate with its commit
        r["orders"], r["estimates"] = [o for o, _ in pts], [e for _, e in pts]
        a = attempts[series_key(r)]
        r["status"], r["reason"] = a["status"], a["reason"]
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
        # One tuple per attempt: groupArray drops NULLs, so separate arrays of
        # nullable estimates would slide successes onto the commits of failures.
        for r in rows(f"""
            SELECT case, params, quantity, unit, direction, node,
                   groupArray((commit_order, estimate, min, max, n, status, reason)) AS pts
            FROM {points_t} WHERE suite = '{suite}'
            GROUP BY case, params, quantity, unit, direction, node ORDER BY case, params, quantity, node"""):
            pts = sorted(r.pop("pts"), key=lambda p: p[0])
            for name, i in (("orders", 0), ("estimates", 1), ("mins", 2), ("maxs", 3), ("ns", 4), ("statuses", 5), ("reasons", 6)):
                r[name] = [p[i] for p in pts]
            series["|".join([r["case"], r["params"], r["quantity"], r["node"]])] = r
        dump({"suite": suite, "commits": axis, "series": series}, out / "history" / f"{suite}.json")
    counts["history_suites"] = len(suites)

    # One document per commit: its nodes, its failures and every change, largest first.
    changes_by_sha, failures_by_sha = {}, {}
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
        changes_by_sha.setdefault(ch.pop("sha"), []).append(ch)
    for f in rows(f"""
        SELECT sha, suite, case, params, quantity, node, status, reason FROM {points_t}
        WHERE status != 'success' ORDER BY sha, {SERIES}"""):
        failures_by_sha.setdefault(f.pop("sha"), []).append(f)
    counts["commit_pages"] = write_commit_pages(out, commits, changes_by_sha, failures_by_sha)
    return counts


def newest_commits(commits: list[dict]) -> dict:
    """Per node, its run of the newest commit (commit order, not execution time)."""
    out = {}
    for c in commits:  # already sorted by commit_order descending
        out.setdefault(c["node"], c)
    return out


def last_executed(commits: list[dict]) -> dict:
    """Per node, the run that finished last: a backfill of an older commit, or a
    failed run, is what an operator wants to see first, even though the newest
    commit is what the summary compares."""
    out = {}
    for c in commits:
        when = c["finished_at"] or c["started_at"] or ""
        if c["node"] not in out or when > (out[c["node"]]["finished_at"] or out[c["node"]]["started_at"] or ""):
            out[c["node"]] = c
    return out


def write_commit_pages(out: Path, commits: list[dict], changes_by_sha: dict, failures_by_sha: dict) -> int:
    for stale in (out / "commits").glob("*.json"):
        stale.unlink()
    by_sha = {}
    for c in commits:
        by_sha.setdefault(c["sha"], []).append(c)
    for sha, nodes_of in by_sha.items():
        head = nodes_of[0]
        dump({"sha": sha, "short": head["short"], "subject": head["subject"], "committer_date": head["committer_date"],
              "parent": head["parent"], "commit_order": head["commit_order"], "nodes": nodes_of,
              "failures": failures_by_sha.get(sha, []), "changes": changes_by_sha.get(sha, [])}, out / "commits" / f"{sha}.json")
    return len(by_sha)


def build_empty(out: Path, commits: list[dict]) -> dict:
    """No results yet (a fresh template, or only failed builds): the page still
    loads, shows the runs there are, and says what is missing."""
    for stale in (out / "history").glob("*.json"):
        stale.unlink()
    newest_by_node = newest_commits(commits)
    dump([], out / "series.json")
    dump({"axis": [], "series": []}, out / "sparks.json")
    dump({"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "notable": NOTABLE,
          "stats": {"commits": len({c["sha"] for c in commits}), "results": 0, "series": 0,
                    "nodes": sorted(newest_by_node), "latest": commits[0] if commits else None},
          "latest_runs": list(newest_by_node.values()), "last_executed": last_executed(commits),
          "recent_commits": commits[:RECENT_COMMITS],
          "movers": {"regressed": [], "improved": []}, "failures": [], "noisiest": [], "suite_time": []},
         out / "landing.json")
    return {"series": 0, "recent_commits": min(len(commits), RECENT_COMMITS), "sparks": 0, "history_suites": 0,
            "commit_pages": write_commit_pages(out, commits, {}, {})}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="site/data, holding marts.py's tables")
    args = ap.parse_args(argv)
    print(json.dumps(build_views(args.data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
