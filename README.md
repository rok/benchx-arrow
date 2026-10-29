# benchx-arrow

A prototype of continuous benchmarking for [Apache Arrow](https://github.com/apache/arrow)
C++ with [benchx](https://github.com/benchx-dev/benchx): every commit on
`main`, measured on a dedicated machine, with the results filed here as benchx
documents.

## Shape

```
apache/arrow main ◀─ git fetch (timer) ─ benchmark node ─ git push ─▶ this repository
                                         provider → bx bench            runs/<sha>/<node>/   orders, results, log
                                                                        queue/<sha>          requests: backfill, re-run
                                                                                 │  on: push
                                                                                 ▼  hosted job, stateless
                                                                        bx ingest → store.parquet (cache + artifact)
                                                                        summary in the job page
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

This is the operational shape asv users run (cron, results committed, static
publish), with what asv lacks: explicit skipping and backfill, machine identity
and ancestry recorded with every run, dirty trees recorded instead of ignored,
and noise-aware verdicts once benchx's history mode exists.

## Layout

```
.benchx/provider.json        names the provider for `bx`
provider/arrow-provider.sh   the Arrow C++ target provider (describe | prepare)
provider/clone-lib.sh        the shared clone, fetch and resolve helpers
node/bench-node.sh           one tick: pick, build, measure, file, push
node/node.env.example        the node's settings; copy to node/node.env
node/benchx-arrow.{service,timer}   systemd user units for the tick
queue/                       requests (empty files named after commits)
runs/                        the documents (see runs/README.md)
tools/build-site.sh          ingest runs/ into a store, then derive site/data (local or CI)
tools/marts.py               the derived tables, in chDB SQL (documented at the top of the file)
tools/test_marts.py          the tables' arithmetic on synthetic points
site/                        the static page; site/data/ is generated and git-ignored
.github/workflows/publish.yml   build the store and the tables, deploy site/ to Pages
```

## The derived tables

`site/data/`, rebuilt by every publish and by `tools/build-site.sh` locally
(needs `bx`, and a python with `chdb` and `pyarrow`: `BX=… PYTHON=… tools/build-site.sh`).

| table | one row per | for |
|---|---|---|
| `commits.parquet` | measured commit × node | landing stats, recent commits, status |
| `points/<suite>.parquet` | result, sorted by commit order | history charts, variant and node breakdowns |
| `changes.parquet` | point with a predecessor in its series | commit-against-previous tables |
| `latest.parquet` | series (suite, case, params, quantity, node) | KPI tiles, search, noise ranks |
| `movers.parquet` | the largest changes per commit × node | regressed and improved lists |
| `manifest.json` | the build | counts, latest commit, table list, definitions |

`rel_change` is estimate over the previous measured point of the same series,
minus one, with `prev_is_parent` and `gap` saying whether that point is the
commit's parent. It is a heuristic until benchx's comparator history mode
produces verdicts with noise. `noise` is the median absolute consecutive change
over a series' last 21 points.

GitHub Pages deploys only when the repository variable `PAGES_ENABLED` is
`true` (`gh variable set PAGES_ENABLED --body true`), after Pages is enabled
in the repository settings with GitHub Actions as the source.

## Setting up a node

```console
$ git clone https://github.com/rok/benchx-arrow ~/benchx-arrow && cd ~/benchx-arrow
$ cp node/node.env.example node/node.env && $EDITOR node/node.env
$ uv venv ~/.benchx-arrow/venv && uv pip install --python ~/.benchx-arrow/venv/bin/python \
      -e /path/to/benchx/prototype          # or pip install -e in any environment, and set BX=
$ GIT_PUSH=0 node/bench-node.sh             # a first tick, kept local: builds Arrow, measures, files under runs/
$ mkdir -p ~/.config/systemd/user && cp node/benchx-arrow.* ~/.config/systemd/user/
$ systemctl --user daemon-reload && systemctl --user enable --now benchx-arrow.timer
$ loginctl enable-linger $USER              # keep the timer running without a login session
```

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
- The summary is a table of the latest results. Charts and per-commit reports
  come after history mode.
- The node and the publish job must run the same benchx (`BENCHX_REPO` and
  `BENCHX_REF` in `publish.yml`); a result written under a newer schema is
  rejected by an older store as malformed.

Measured on the first run (two binaries, 94 results, one repetition): 2.3 MB of
documents on disk, 75 KB compressed, so a full Arrow C++ run of about a
thousand results costs on the order of a megabyte of repository per commit.
