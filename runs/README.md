# runs/

The system of record: one directory per measured commit and node, Parquet only.

```
runs/<sha>/<node>/
  run.parquet        one row: sha, parents, dates, subject, state, reason, timings,
                     suite counts, the tick's log
  results.parquet    a benchx store of this run, every result with its full document;
                     absent when nothing was measured (a failed build)
```

Everything else in this repository is derived from these files and can be
regenerated. Raw documents and harness output stay on the node, in `$WORK/raw`.
