# Shared by the provider and the node tick: the project's settings, where the
# upstream clone lives and how it is kept fresh. Sourced, not executed. Expects
# ROOT (this repository); node/node.env may override UPSTREAM_URL,
# UPSTREAM_REFERENCE and UPSTREAM_BRANCH, which default to project.json, and
# BENCHX_WORK, which defaults to ~/.benchx-<project name>, so two projects on
# one machine keep separate clones, builds and locks.

# project FIELD: one value from project.json, by dotted path (upstream.url).
project() {
  python3 -c 'import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): d = d[k]
print(d)' "$ROOT/project.json" "$1"
}

PROJECT=$(project name)
UPSTREAM_URL=${UPSTREAM_URL:-${ARROW_URL:-$(project upstream.url)}}
UPSTREAM_BRANCH=${UPSTREAM_BRANCH:-$(project upstream.branch)}
UPSTREAM_REFERENCE=${UPSTREAM_REFERENCE:-${ARROW_REFERENCE:-}}
WORK=${BENCHX_WORK:-${BENCHX_ARROW_WORK:-$HOME/.benchx-$PROJECT}}
CLONE=$WORK/$PROJECT

ensure_clone() {
  if [[ ! -d $CLONE/.git ]]; then
    mkdir -p "$WORK"
    local ref=()
    [[ -n $UPSTREAM_REFERENCE ]] && ref=(--reference-if-able "$UPSTREAM_REFERENCE")
    echo "clone: $UPSTREAM_URL -> $CLONE" >&2
    git clone -q --no-checkout "${ref[@]}" "$UPSTREAM_URL" "$CLONE" >&2
  fi
}

fetch_main() {
  git -C "$CLONE" fetch -q origin "+refs/heads/$UPSTREAM_BRANCH:refs/remotes/origin/$UPSTREAM_BRANCH" >&2
}

# resolve_commit REF: the full sha, fetching it from origin when it is unknown locally.
resolve_commit() {
  local sha
  if ! sha=$(git -C "$CLONE" rev-parse --verify -q "$1^{commit}"); then
    git -C "$CLONE" fetch -q origin "$1" >&2 || return 1
    sha=$(git -C "$CLONE" rev-parse --verify -q "$1^{commit}") || return 1
  fi
  echo "$sha"
}
