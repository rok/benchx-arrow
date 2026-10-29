# queue/

A request to measure a commit: an empty file named after the commit (a full
sha, or anything `git rev-parse` on apache/arrow resolves). A node takes
queue entries before anything on main, removes the entry in the commit that
files its results, and skips entries it has already measured.

Use it to backfill a commit the node skipped, to re-measure one (remove its
`runs/<sha>/<node>/` first), or to narrow a change to its commit.
