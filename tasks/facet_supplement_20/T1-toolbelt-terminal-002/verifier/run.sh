#!/bin/sh
set -eu
workspace="${HARNESSBENCH_WORKSPACE:-${WORKSPACE_PATH:-${1:-/workspace}}}"
exec python3 "$(dirname "$0")/score.py" --workspace "$workspace"
