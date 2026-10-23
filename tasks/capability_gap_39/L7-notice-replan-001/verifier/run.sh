#!/bin/sh
set -eu
WORKSPACE_ROOT="${HARNESSBENCH_WORKSPACE:-${WORKSPACE_PATH:-${1:-/workspace}}}"
exec python3 "$(dirname "$0")/score.py" --workspace "$WORKSPACE_ROOT"
