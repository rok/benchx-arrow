# runs/

The system of record: one directory per measured commit and node.

```
runs/<sha>/<node>/
  commit.json        sha, parents, dates, subject (ancestry for history comparisons)
  status.json        state, reason (main | queue/<name> | argument), timings, per-suite exits
  log.txt            the tick's log
  build.log          only when the build failed: its last lines
  suites/<binary>/   one `bx bench` run: orders/order.json, workorder-*.json,
                     one result document per case and quantity, table.txt
```

Everything else in this repository is derived from these documents and can
be regenerated.
