# Porting this to another project

This repository benchmarks Apache Arrow C++, but only two pieces of it are
specific to Arrow. Copy it, replace those two pieces, set your project in one
file, and you get the same thing for your repository: a machine that measures
new commits, results kept as documents in git, and a dashboard on GitHub Pages.

Plan on an afternoon if your benchmarks already run from the command line. The
expensive part is a quiet machine to measure on; nothing else needs a server.

## What to keep, what to replace

| piece | files | for your project |
|---|---|---|
| project settings | `project.json` | **edit**: name, title, upstream URL and branch, commit link, this repository's URL, the benchx version |
| build | `provider/arrow-provider.sh`, `.benchx/provider.json` | **replace**: how to check out and build one commit |
| benchmark discovery | `node/bench-node.sh`, the `measure` section (marked `Discovery`) | **edit**: which binaries or commands to run, and with which benchx adapter |
| node settings | `node/node.env.example` | edit the defaults; each node keeps its own `node/node.env` |
| the node tick | the rest of `node/bench-node.sh`, `provider/clone-lib.sh` | keep |
| node install and checks | `node/install-units.sh`, `node/check-node.sh`, `node/benchx-arrow.{service,timer}` | keep; the units are installed as `benchx-<name>` and the work directory defaults to `~/.benchx-<name>`, so several projects can share a node |
| derived tables and views | `tools/` | keep |
| dashboard | `site/` | keep; it reads its title and links from `project.json` |
| publish | `.github/workflows/publish.yml` | keep |
| results and requests | `runs/`, `queue/` | **empty** them (keep the READMEs) |

## 1. Copy the repository and clear Arrow's results

Use GitHub's "Use this template" (or clone and push to a new repository), then:

```bash
git rm -r -q runs/*/
git commit -m "start from an empty runs/"
```

## 2. Describe your project in `project.json`

```json
{
 "name": "mylib",
 "title": "mylib benchmarks",
 "description": "mylib's micro-benchmarks on every commit to main.",
 "upstream": {
  "url": "https://github.com/me/mylib.git",
  "branch": "main",
  "commit_url": "https://github.com/me/mylib/commit/{sha}"
 },
 "repository": "https://github.com/me/mylib-bench",
 "benchx": { "repository": "rok/benchx", "ref": "<a benchx commit>" }
}
```

- `name` is the benchx project the results are filed under. Keep it stable:
  changing it starts new series.
- `upstream` is the repository being measured. The node clones it, follows
  `branch` (first parent), and the page links commits with `commit_url`.
- `repository` is this repository. The page uses it to link run logs and
  documents. Leave it empty to hide those links.
- `benchx.ref` pins the benchx the publish job installs. Nodes must run the same
  one, because a result written by a newer benchx is rejected by an older store.

## 3. Write the provider: how to build one commit

benchx never builds anything. It asks a *target provider*, a program named in
`.benchx/provider.json`, for a built tree. The provider reads one JSON request
on stdin and prints one target description on stdout. Everything else, the
build output above all, goes to stderr.

The request (only the fields the Arrow provider reads):

```json
{ "operation": "prepare", "source_ref": "3f1c…", "configuration": "cpp" }
```

- `operation` is `prepare` (fetch, check out, build, then describe) or
  `describe` (report what is built for `source_ref` and build nothing; exit 3
  if the build is not at that commit).
- `source_ref` is a commit, or `WORKSPACE` for whatever is checked out now.

The description it prints:

```json
{
 "schema_version": "benchx/target-description/0.1.0",
 "target": { "kind": "build", "path": "/home/me/.benchx-arrow/build/cpp",
             "source": { "uri": "https://github.com/me/mylib.git", "type": "git" },
             "source_dir": "/home/me/.benchx-arrow/src/cpp" },
 "how_built": "git checkout …\ncmake …\ncmake --build …",
 "shell": "bash",
 "labels": { "configuration": "cpp" },
 "provider": { "name": "mylib-bench/provider.sh", "version": "1" }
}
```

`how_built` is recorded with every result. Put the exact commands in it, so a
result can be reproduced from its document.

Start from `provider/arrow-provider.sh`. In most projects only the build block
changes: the `CMAKE_FLAGS` array and the two `step cmake …` lines. Keep the
parts that make it cheap and safe: one reused worktree and build directory
(incremental builds), refusing to check out over local changes, and the
`step` helper that runs each command and records it in `how_built`.

Try it by hand before going further:

```bash
echo '{"operation":"prepare","source_ref":"main","configuration":"cpp"}' | provider/arrow-provider.sh
```

## 4. Tell the node what to run

In `node/bench-node.sh`, the `measure` section finds the benchmark binaries
and runs each one through `bx bench`:

```bash
# Discovery: Arrow's benchmark binaries are release/*-benchmark. …
mapfile -t SUITES < <(cd "$BUILD/release" && ls -1 | grep -E -- '-benchmark$' | grep -E -- "$SUITE_FILTER" || true)
…
"$BX" bench "$BUILD" --adapter google-benchmark --suite "release/$suite" …
```

Change the `ls | grep` to list your benchmark executables relative to the
build directory. If they are not Google Benchmark binaries, change `--adapter`
(`bx bench --help` lists the adapters your benchx has). Each suite becomes a
directory under `runs/<sha>/<node>/suites/` and a suite on the page.

If your benchmarks need environment variables or test data, export them just
above that line, the way Arrow's `ARROW_TEST_DATA` is.

## 5. Set up a node and run one tick by hand

```bash
cp node/node.env.example node/node.env    # NODE_NAME, BX, SUITE_FILTER, REPETITIONS…
node/check-node.sh                        # preflight: tools, upstream, push access, CPU governor
GIT_PUSH=0 node/bench-node.sh             # one tick: build, measure, file under runs/, no push
```

Narrow `SUITE_FILTER` to one fast suite for this first run. Then look at what
it filed: `runs/<sha>/<node>/status.json` and `log.txt`, and one result
document under `suites/`. If you have the derived layer's dependencies
(`pip install -r tools/requirements.txt` plus benchx), you can see the
dashboard before pushing anything:

```bash
BX=… PYTHON=… tools/build-site.sh && python3 -m http.server -d site 8765
```

That run stays local: it is not committed, and it measured only the suites
your filter let through. Remove it before handing over to the timer.
Otherwise the timer sees the commit as measured and never pushes it. Then
widen `SUITE_FILTER` and let the timer take over:

```bash
git clean -fd runs/                       # drop the local smoke-test run
node/install-units.sh --enable            # installs benchx-<name>.{service,timer} and starts the timer
loginctl enable-linger $USER              # keep it running without a login session
```

`check-node.sh` and `install-units.sh` both warn when `runs/` has
uncommitted runs.

## 6. Publish

1. In the repository settings, enable Pages with GitHub Actions as the source.
2. `gh variable set PAGES_ENABLED --body true`
3. Push. The `publish` workflow tests the derived layer, ingests `runs/`,
   builds the tables and views, and deploys `site/`.

Until the first result arrives, the page says it is waiting for one and shows
these steps. If a run is filed but every benchmark failed (a broken build, say),
it lists the runs instead, and each one links to its log.

## Checklist

- [ ] `runs/` emptied, `queue/` has only its README
- [ ] `project.json`: name, title, upstream (url, branch, commit_url), repository, benchx ref
- [ ] provider: builds one commit by hand and prints a description
- [ ] `.benchx/provider.json` names the provider
- [ ] discovery and adapter in `node/bench-node.sh` find your benchmarks
- [ ] `node/check-node.sh` passes on the benchmark machine
- [ ] one tick with `GIT_PUSH=0` filed a run that looks right on a local build of the page
- [ ] the node's git credentials can push to this repository
- [ ] Pages enabled, `PAGES_ENABLED=true`
- [ ] the timer is enabled, and the machine is quiet: nothing else scheduled, a fixed CPU governor

## Things that bite

- **The benchx versions must match.** The node writes documents with its
  benchx, and the publish job reads them with the benchx `project.json` pins.
  Upgrade both together. The page shows a warning when the ingest rejects runs.
- **A node is a machine, not a name.** Results from different hardware are
  different series. When a machine is replaced, give the new one a new
  `NODE_NAME`. The name is what runs are filed under and what the page shows.
  The node writes it into every result's run key, and the tables take it from
  there rather than from the hostname benchx records.
- **Commit order is committer date.** The tables order commits on the branch
  by committer date, which follows ancestry on a branch that only takes
  squash or rebase merges (Arrow's). With merge commits or skewed clocks,
  "previous measurement" can be a commit that is not an ancestor. Change
  `read_commits` in `tools/marts.py` to order by first-parent position.
  `commit.json` records the parents it needs.
- **Coverage.** Each tick measures the newest unmeasured commit on the branch.
  A node slower than the branch skips commits; put the ones you care about in
  `queue/` (an empty file named after the commit).
- **Re-measuring.** Delete `runs/<sha>/<node>/` and add `queue/<sha>`. The
  publish job's cached store keeps the old results until its cache is
  dropped. Re-run the workflow after deleting the `store-` caches under
  Actions → Caches.
