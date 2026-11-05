#!/usr/bin/env bash
# The benchx target provider for Apache Arrow C++ (benchx system-decomposition.md §3.4).
#
# One JSON request on stdin (schemas/target-provider-request/0.1.0), one target
# description on stdout (schemas/target-description/0.1.0). Everything else,
# above all the build's output, goes to stderr.
#
#   describe  report the built target for source_ref; build nothing; exit 3 if
#             the build directory is not at that commit
#   prepare   fetch, check out, configure and build (incrementally), then report
#
# source_ref is a commit (sha, tag, or ref known to origin) or WORKSPACE, the
# commit the source tree is currently at. The only configuration is `cpp`: a
# Release build of the C++ micro-benchmarks, shared libraries only.
#
# One source tree and one build directory are reused across commits, so a
# build after a small commit takes minutes, not the half hour of a fresh one.
# `how_built` records the exact commands run; benchx never runs any of them.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
[[ -f $ROOT/node/node.env ]] && source "$ROOT/node/node.env"
BUILD_JOBS=${BUILD_JOBS:-$(nproc)}
source "$HERE/clone-lib.sh"

request=$(cat)
field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1]) or "")' "$1" <<<"$request"; }
operation=$(field operation)
source_ref=$(field source_ref)
configuration=$(field configuration)
configuration=${configuration:-cpp}
[[ $configuration == cpp ]] || { echo "provider: unknown configuration '$configuration' (only: cpp)" >&2; exit 2; }

SRC=$WORK/src/$configuration
BUILD=$WORK/build/$configuration
SIDECAR=$BUILD/.benchx-target.json

# The build definition. Mirrors `archery benchmark` (release, benchmarks on,
# tests off) with the components Arrow's benchmark CI enables. Edit here; the
# text lands in every result's how_built.
CMAKE_FLAGS=(
  -GNinja
  -DCMAKE_BUILD_TYPE=Release
  -DARROW_BUILD_BENCHMARKS=ON
  -DARROW_BUILD_TESTS=OFF
  -DARROW_BUILD_SHARED=ON
  -DARROW_BUILD_STATIC=OFF
  -DARROW_ACERO=ON
  -DARROW_COMPUTE=ON
  -DARROW_CSV=ON
  -DARROW_DATASET=ON
  -DARROW_FILESYSTEM=ON
  -DARROW_IPC=ON
  -DARROW_JSON=ON
  -DARROW_PARQUET=ON
  -DARROW_WITH_BROTLI=ON
  -DARROW_WITH_LZ4=ON
  -DARROW_WITH_SNAPPY=ON
  -DARROW_WITH_ZLIB=ON
  -DARROW_WITH_ZSTD=ON
  -DARROW_DEPENDENCY_SOURCE=AUTO
)
if command -v ccache >/dev/null; then
  CMAKE_FLAGS+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi

current_commit() { [[ -d $SRC/.git || -f $SRC/.git ]] && git -C "$SRC" rev-parse HEAD; }

describe() {
  local want=$1 have
  have=$(current_commit || true)
  if [[ -z $have ]]; then echo "provider: nothing is checked out at $SRC; use prepare" >&2; exit 3; fi
  if [[ $have != "$want" ]]; then echo "provider: $SRC is at ${have:0:12}, not ${want:0:12}; use prepare" >&2; exit 3; fi
  if [[ ! -f $SIDECAR ]]; then echo "provider: $BUILD has no description; use prepare" >&2; exit 3; fi
  if [[ -n $(git -C "$SRC" status --porcelain --untracked-files=no) ]]; then
    echo "provider: $SRC has uncommitted changes" >&2; exit 4
  fi
  cat "$SIDECAR"
}

if [[ $operation == describe ]]; then
  if [[ $source_ref == WORKSPACE ]]; then sha=$(current_commit || true); [[ -n $sha ]] || { echo "provider: no workspace yet" >&2; exit 3; }
  else sha=$(git -C "$CLONE" rev-parse --verify -q "$source_ref^{commit}" 2>/dev/null) || { echo "provider: unknown commit $source_ref" >&2; exit 3; }
  fi
  describe "$sha"; exit 0
fi

# ---- prepare -----------------------------------------------------------------
HOW=""
step() {  # step <command...>: record it in how_built, show it, run it (output to stderr)
  local line="$*"
  HOW+="$line"$'\n'
  printf '\033[2m  provider$ %s\033[0m\n' "$line" >&2
  "$@" >&2
}

ensure_clone
if [[ $source_ref == WORKSPACE ]]; then
  sha=$(current_commit || true); [[ -n $sha ]] || { echo "provider: no workspace yet; prepare a commit first" >&2; exit 3; }
else
  fetch_main
  sha=$(resolve_commit "$source_ref") || { echo "provider: unknown commit $source_ref" >&2; exit 3; }
fi

if [[ ! -e $SRC/.git ]]; then
  mkdir -p "$(dirname "$SRC")"
  step git -C "$CLONE" worktree add --detach "$SRC" "$sha"
else
  if [[ -n $(git -C "$SRC" status --porcelain --untracked-files=no) ]]; then
    echo "provider: $SRC has uncommitted changes; refusing to check out ${sha:0:12} over them" >&2; exit 4
  fi
  step git -C "$SRC" checkout -q --detach "$sha"
fi
step git -C "$SRC" submodule update -q --init --depth 1
step cmake -S "$SRC/cpp" -B "$BUILD" "${CMAKE_FLAGS[@]}"
step cmake --build "$BUILD" -j "$BUILD_JOBS"

BUILD=$BUILD SRC=$SRC SHA=$sha HOW=$HOW UPSTREAM_URL=$UPSTREAM_URL CONFIGURATION=$configuration \
python3 - >"$SIDECAR" <<'PY'
import json, os
how = f"# {os.environ['UPSTREAM_URL']} at {os.environ['SHA']} (incremental tree and build directory, reused across commits)\n" + os.environ["HOW"].rstrip("\n")
print(json.dumps({
    "schema_version": "benchx/target-description/0.1.0",
    "target": {"kind": "build", "path": os.environ["BUILD"],
               "source": {"uri": os.environ["UPSTREAM_URL"], "type": "git"},
               "source_dir": os.environ["SRC"]},
    "how_built": how,
    "activation": "export ARROW_TEST_DATA=" + os.path.join(os.environ["SRC"], "testing", "data")
                  + " PARQUET_TEST_DATA=" + os.path.join(os.environ["SRC"], "cpp", "submodules", "parquet-testing", "data"),
    "shell": "bash",
    "labels": {"configuration": os.environ["CONFIGURATION"]},
    "provider": {"name": "benchx-arrow/provider/arrow-provider.sh", "version": "1"},
}, indent=1))
PY
describe "$sha"
