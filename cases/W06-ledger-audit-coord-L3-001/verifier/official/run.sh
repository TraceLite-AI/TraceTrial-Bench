#!/usr/bin/env bash
# Standard verifier entrypoint. Grades the case workspace; optional process evidence is
# supplied via env vars (set by the runner / when grading committed evidence):
#   HARNESSBENCH_PARENT_SESSION_FILE  -> parent session jsonl (spawn/yield/clean-conduct)
#   HARNESSBENCH_RUN_META             -> run_meta.json (exit_code, elapsed, child_session_created, warned)
# With none set, run.sh grades the INITIAL workspace (baseline-fail check).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
python3 "${SCRIPT_DIR}/score.py" --workspace "${TASK_DIR}/workspace"
