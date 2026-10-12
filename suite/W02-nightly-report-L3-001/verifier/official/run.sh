#!/usr/bin/env bash
python3 "$(dirname "$0")/score.py" --workspace "${1:-.}"
