# Shared by the provider and the node tick: where Arrow's clone lives and how it
# is kept fresh. Sourced, not executed. Expects WORK, ARROW_URL, ARROW_REFERENCE.
CLONE=$WORK/arrow

ensure_clone() {
  if [[ ! -d $CLONE/.git ]]; then
    mkdir -p "$WORK"
    local ref=()
    [[ -n ${ARROW_REFERENCE:-} ]] && ref=(--reference-if-able "$ARROW_REFERENCE")
    echo "clone: $ARROW_URL -> $CLONE" >&2
    git clone -q --no-checkout "${ref[@]}" "$ARROW_URL" "$CLONE" >&2
  fi
}

fetch_main() {
  git -C "$CLONE" fetch -q origin "+refs/heads/main:refs/remotes/origin/main" >&2
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
