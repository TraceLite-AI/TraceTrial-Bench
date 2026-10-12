#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
exec "${PYTHON:-python3}" "${SCRIPT_DIR}/score.py" --private-dir "${PRIVATE_DIR}"
