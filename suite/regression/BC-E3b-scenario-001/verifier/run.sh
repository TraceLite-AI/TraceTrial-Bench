#!/usr/bin/env bash
exec python "$(dirname "$0")/score.py" --workspace "${HARNESSBENCH_WORKSPACE:-workspace}"
