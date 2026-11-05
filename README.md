# benchx-arrow

A prototype of continuous benchmarking for [Apache Arrow](https://github.com/apache/arrow)
C++ with [benchx](https://github.com/benchx-dev/benchx): new commits on `main`,
measured on a dedicated machine, with the results filed here as benchx documents
and published as a dashboard at https://mihevc.org/benchx-arrow/.

**Want this for your own project?** This repository is meant to be copied. Only
the build (`provider/`) and the benchmark discovery (a few lines in
`node/bench-node.sh`) are Arrow's; the project itself is one file,
`project.json`. [PORTING.md](PORTING.md) walks through it, with a checklist.

## Shape

```
apache/arrow main ◀─ git fetch (timer) ─ benchmark node ─ git push ─▶ this repository
                                         provider → bx bench            runs/<sha>/<node>/   orders, results, log
                                                                        queue/<sha>          requests: backfill, re-run
                                                                                 │  on: push
                                                                                 ▼  hosted job, stateless
                                                                        bx ingest → store.parquet (cache + artifact)
                                                                        tools/marts.py → tables, tools/views.py → JSON
                                                                        site/ → GitHub Pages
```

- **The node pushes documents; everything else is derived.** A timer on the
  benchmark machine runs `node/bench-node.sh`: fetch Arrow, pick a commit, have
  the provider build it, run every benchmark binary through `bx bench`, write the
  work orders and result documents under `runs/<sha>/<node>/`, commit, push. No
  scheduler service, no runner registration, no API polling. The state is the
  repository: `ls runs/` says what was measured, `queue/` says what is asked for.
- **Requests are files.** Add `queue/<sha>` to backfill a skipped commit or to
  re-measure one. The node takes queue entries before anything on main and
  removes them with the commit that files the results.
- **The store is a cache, and the dashboard's tables are derived from it.** The
  GitHub Actions job ingests `runs/` into a benchx Parquet store, then
  `tools/marts.py` derives small, flat, per-view tables from it with chDB
  (in process, SQL over Parquet): commits, points per suite, changes, latest,
  movers. They are published to GitHub Pages with the page under `site/`. Delete
  the cache and everything rebuilds from the documents, because benchx's ingest
  is idempotent and the documents are the system of record.
- **The provider builds, benchx measures.** `provider/arrow-provider.sh` speaks
  benchx's target-provider contract: fetch the commit, check it out into one
  reused worktree, configure and build incrementally with CMake, hand back a
  target description whose `how_built` is the exact commands it ran. Nothing in
  benchx builds or tunes anything.

Coverage is newest-first: each tick measures the newest commit on `main` the
node has not measured, so a node faster than the branch measures every commit
and a slower one skips some, which `queue/` backfills.

This is the operational shape asv users run (cron, results committed, static
publish), with what asv lacks: explicit skipping and backfill, machine identity
and ancestry recorded with every run, dirty trees recorded instead of ignored,
and noise-aware verdicts once benchx's history mode exists.

## Layout

```
project.json                 the project: name, title, upstream and branch, links, the pinned benchx
PORTING.md                   how to make this benchmark another repository
.benchx/provider.json        names the provider for `bx`
provider/arrow-provider.sh   the Arrow C++ target provider (describe | prepare)
provider/clone-lib.sh        the shared clone, fetch and resolve helpers
node/bench-node.sh           one tick: pick, build, measure, file, push
node/check-node.sh           preflight for a node: tools, upstream, push access, governor
node/install-units.sh        install the systemd units for this checkout (--enable starts the timer)
node/node.env.example        the node's settings; copy to node/node.env
node/benchx-arrow.{service,timer}   systemd user units for the tick (templates; see install-units.sh)
queue/                       requests (empty files named after commits)
runs/                        the documents (see runs/README.md)
tools/build-site.sh          ingest runs/ into a store, then derive site/data (local or CI)
tools/marts.py               the derived tables, in chDB SQL (documented at the top of the file)
tools/views.py               the page's JSON views, in chDB SQL over those tables
tools/test_marts.py          the tables' arithmetic on synthetic points
tools/test_views.py          the views: alignment with failures, failure lists, direction, an empty start
tools/requirements.txt       the derived layer's dependencies (benchx is pinned in project.json)
site/                        the dashboard (index.html, app.js, style.css); site/data/ is generated and git-ignored
.github/workflows/publish.yml   test, build the store and the tables, deploy site/ to Pages
```

## The derived tables

`site/data/`, rebuilt by every publish and by `tools/build-site.sh` locally
(needs `bx`, and a python with `chdb` and `pyarrow`: `BX=… PYTHON=… tools/build-site.sh`).

| table | one row per | for |
|---|---|---|
| `commits.parquet` | measured commit × node | landing stats, recent commits, status |
| `points.parquet` | result, sorted by suite, case and commit order | history charts, variant and node breakdowns |
| `changes.parquet` | point with a predecessor in its series | commit-against-previous tables |
| `latest.parquet` | series (suite, case, params, quantity, node) | KPI tiles, search, noise ranks |
| `movers.parquet` | the largest changes per commit × node | regressed and improved lists |
| `manifest.json` | the build | counts, latest commit, table list, definitions |

`rel_change` is estimate over the previous measured point of the same series,
minus one, with `prev_is_parent` and `gap` saying whether that point is the
commit's parent. It is a heuristic until benchx's comparator history mode
produces verdicts with noise. `noise` is the median absolute consecutive change
over a series' last 21 points.

`tools/views.py` then writes `site/data/views/`: `landing.json`, `series.json`,
`sparks.json` (the last 60 points of every series, for the front page),
`history/<suite>.json` and `commits/<sha>.json`, the documents the page reads.
Every number on the page is computed there, by chDB; the page runs no engine.

## The dashboard

`site/` is a static page over those views, in the spirit of ClickPy and asv's
web UI. It answers "what did the latest run find?" first:

- **Overview.** The latest run on each node: its commit, how many results
  failed, the largest regressions and improvements against the previous
  measurement, and every benchmark that failed, with benchx's reason. Below it,
  one small history chart per benchmark across every suite (sorted by largest
  change, filterable by suite and name, paged), recent commits, the noisiest
  series and suite wall times.
- **Suite, benchmark and commit pages.** A suite page has one small chart per
  benchmark. A benchmark page has KPI tiles, the history chart (uPlot, with
  failed attempts marked under the axis) and its biggest moves. A commit page
  shows its runs with links to their logs, its failed results, and every change
  with the baseline it was measured against.
- **About.** How to read the numbers, and a pointer to PORTING.md.

Changes are colored by the quantity's direction. A change smaller than twice
the series' noise is drawn muted, because ordinary run-to-run variation would
produce it too. Noise is shown only once a series has three measurements. Light
and dark follow the OS, with a toggle. The title and links come from
`project.json`. Data is fetched with cache-busting query strings
because the Pages CDN caches files for four hours.

To look at it locally after `tools/build-site.sh`: `python3 -m http.server -d site 8765`.

GitHub Pages deploys only when the repository variable `PAGES_ENABLED` is
`true` (`gh variable set PAGES_ENABLED --body true`), after Pages is enabled
in the repository settings with GitHub Actions as the source. The site is at
https://mihevc.org/benchx-arrow/.

## Setting up a node

```console
$ git clone https://github.com/rok/benchx-arrow ~/benchx-arrow && cd ~/benchx-arrow
$ cp node/node.env.example node/node.env && $EDITOR node/node.env
$ git clone https://github.com/rok/benchx ~/src/benchx && git -C ~/src/benchx checkout "$(jq -r .benchx.ref project.json)"
$ uv venv ~/.benchx-arrow/venv && uv pip install --python ~/.benchx-arrow/venv/bin/python -e ~/src/benchx/prototype
$ node/check-node.sh                        # preflight: tools, upstream, push access, governor
$ GIT_PUSH=0 node/bench-node.sh             # a first tick, kept local: builds Arrow, measures, files under runs/
$ git clean -fd runs/                       # drop that local run, or the timer will consider it measured
$ node/install-units.sh --enable            # systemd user units benchx-arrow.*, pointing at this checkout
$ loginctl enable-linger $USER              # keep the timer running without a login session
```

The node commits and pushes as the git identity configured in this checkout,
with whatever credentials `git push` uses there (an SSH deploy key with write
access is the simplest). `check-node.sh` checks both.

The node needs `git`, `cmake`, `ninja`, a C++ compiler, and Arrow's build
dependencies; what the system lacks, Arrow's CMake fetches and builds
(`ARROW_DEPENDENCY_SOURCE=AUTO`). `ccache` is used when present. The first build
takes the better part of an hour; later ones rebuild only what the commit
touched. Treat the machine as a benchmark node: nothing else scheduled on it,
fixed frequency governor, upgrades applied deliberately between runs
(benchx's `benchmark-environments.md` §5.3).

`node/bench-node.sh <commit>` measures one commit now, regardless of queue and main.

## Status

Prototype. Known gaps, in the order they bite:

- `bx bench` has no order-level timeout flag; the runner's default of 600 s per
  order cuts long benchmark binaries short, with the unstarted cases filed as
  `timeout-order` errors. Needs a flag in benchx.
- benchx's Google Benchmark adapter translates wall time and CPU time only.
  Arrow's benchmarks report mostly through `bytes_per_second` and
  `items_per_second` counters, which are not yet quantities.
- No verdicts yet: benchx's comparator has run mode only. History mode
  (this commit against its ancestors, k × noise) is what turns the store into
  alerts; `commit.json` records the ancestry it will need.
- The node and the publish job must run the same benchx (`benchx.ref` in
  `project.json`). A result written under a newer schema is rejected by an
  older store as malformed, and the page shows how many runs were rejected.
- The publish job's cached store is not reconciled with deletions: after
  removing a run to re-measure it, drop the `store-` caches.

Measured on the first run (two binaries, 94 results, one repetition): 2.3 MB of
documents on disk, 75 KB compressed, so a full Arrow C++ run of about a
thousand results costs on the order of a megabyte of repository per commit.
