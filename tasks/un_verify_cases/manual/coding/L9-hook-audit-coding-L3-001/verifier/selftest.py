#!/usr/bin/env python3
"""Focused regression checks for the L9 deterministic verifier."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("l9_score_selftest", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def main() -> int:
    canonical = "change=CHG-4202 event=ToolAudit observed=execution marker received"
    event = {
        "tool_result": {
            "content": canonical,
            "content_blocks": [{"content": [{"text": canonical}]}],
        }
    }
    assert SCORE.result_text(event) == canonical
    assert SCORE.HOOK_ROW.findall(SCORE.result_text(event)) == ["CHG-4202"]

    with tempfile.TemporaryDirectory(prefix="l9-verifier-selftest-") as root:
        path = Path(root) / "duplicate.jsonl"
        path.write_text('{"record_type":"change","record_type":"change"}\n', encoding="utf-8")
        _, errors = SCORE.parse_jsonl(path)
        assert any("duplicate_key:record_type" in error for error in errors)

    scenario = json.loads((HERE.parent / "simulator/scenario.json").read_text(encoding="utf-8"))
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    scenario_by_id = {row["change_id"]: row for row in scenario["changes"]}
    truth_by_id = {row["change_id"]: row for row in truth["changes"]}
    for change, marker in SCORE.TIMEOUT_MARKERS.items():
        command = scenario_by_id[change]["command"]
        assert command.startswith("timeout --verbose --signal=TERM 3s bash -c ")
        assert marker in command
        assert truth_by_id[change]["outcome"] == "timed_out"
        assert truth_by_id[change]["exit_code"] == 124

    assert not SCORE.strict_summary_type({
        "record_type": "summary",
        "applied": True,
        "failed": 2,
        "timed_out": 2,
        "unsuccessful": 4,
        "hook_reconciliation": {"recorded_once": 4, "duplicate": 1, "missing": 1},
        "conclusion": "invalid bool count",
    })
    print("L9 verifier selftest: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
