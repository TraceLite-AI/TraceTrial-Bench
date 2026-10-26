#!/usr/bin/env python3
"""Deterministic positive and mutation tests for the L4a coding verifier."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent
VERIFIER = Path(__file__).resolve().parent


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def score(workspace: Path, run_dir: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    result = subprocess.run(
        [sys.executable, str(VERIFIER / "score.py"), "--workspace", str(workspace)],
        cwd=CASE,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return {
        item["id"]: item["score"]
        for layer in ("harness", "agent")
        for item in payload["rule_results"][layer]
    }


def main() -> int:
    score_source = (VERIFIER / "score.py").read_text(encoding="utf-8")
    compile(score_source, str(VERIFIER / "score.py"), "exec")

    yaml_ids: set[str] = set()
    for name in ("harness_score.yaml", "agent_score.yaml"):
        text = (VERIFIER / name).read_text(encoding="utf-8")
        assert "type: rubric" not in text
        assert "N/A" not in text
        ids = re.findall(r"^\s*- id:\s*([a-z0-9_]+)\s*$", text, re.MULTILINE)
        rule_types = re.findall(r"^\s+type:\s*(\w+)\s*$", text, re.MULTILINE)
        assert ids and len(ids) == len(rule_types) and set(rule_types) == {"rule"}
        yaml_ids.update(ids)

    truth = json.loads((VERIFIER / "truth.json").read_text(encoding="utf-8"))
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
    from score import EXPECTED_TURN_SHA256, REVISION_PROMPTS

    assert tuple(hashlib.sha256(turn.encode("utf-8")).hexdigest() for turn in turns) == EXPECTED_TURN_SHA256
    assert {turn: turns[turn - 1] for turn in (3, 5, 7)} == REVISION_PROMPTS
    released = truth["released"]
    reviewers = ["Avery", "Blake", "Casey", "Drew"]

    with tempfile.TemporaryDirectory(prefix="l4a-modulereport-selftest-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        shutil.copytree(CASE / "workspace", workspace)
        run_dir = root / "run"

        issued = {
            module_id: {
                "released_at": f"2026-07-{10 + index:02d}",
                "reviewer": reviewers[index % len(reviewers)],
            }
            for index, module_id in enumerate(released)
        }
        write_json(workspace / "state" / ".issued_details.json", issued)
        report = {
            module_id: {
                "module": module_id,
                "loc_count": truth["loc_count"][module_id],
                "released_at": issued[module_id]["released_at"],
            }
            for module_id in released
        }
        write_json(workspace / "output" / "module_report.json", report)
        (workspace / "output" / "scope_notes.md").write_text(
            "# Scope Notes\n\n"
            "Final scope: released modules only.\n\n"
            "## Final field set\n"
            "Each entry contains exactly three fields:\n"
            "- `module`\n"
            "- `loc_count`\n"
            "- `released_at`\n"
            "The `reviewer` field is removed and superseded.\n\n"
            "## Explicit Revisions (3)\n\n"
            "1. Narrowed the scope to released modules only and removed blocked modules.\n"
            "2. Added `reviewer` to every entry.\n"
            "3. Revoked and removed `reviewer`; replaced it with `released_at`.\n",
            encoding="utf-8",
        )

        events: list[dict[str, Any]] = [{"seq": 1, "event_type": "conversation_started"}]
        seq = 2
        history: list[str] = []
        for turn, prompt in enumerate(turns, start=1):
            history.append(prompt)
            events.append(
                {"seq": seq, "event_type": "conversation_turn_started", "turn_index": turn, "input": prompt}
            )
            seq += 1
            events.append(
                {
                    "seq": seq,
                    "event_type": "model_input",
                    "conversation_turn": turn,
                    "messages": [{"role": "user", "content": value} for value in history],
                }
            )
            seq += 1
            if turn == 4:
                for module_id in released:
                    row = {"module": module_id, "status": "released", **issued[module_id]}
                    events.append(
                        {
                            "seq": seq,
                            "event_type": "tool",
                            "conversation_turn": turn,
                            "tool_call": {
                                "tool_name": "exec_command",
                                "tool_call_id": f"fetch-{module_id}",
                                "arguments": {"command": f"bin/modctl fetch {module_id}"},
                            },
                            "tool_result": {"content": json.dumps(row)},
                        }
                    )
                    seq += 1
            if turn in (3, 5, 7):
                events.append(
                    {
                        "seq": seq,
                        "event_type": "tool",
                        "conversation_turn": turn,
                        "tool_call": {
                            "tool_name": "write",
                            "tool_call_id": f"write-{turn}",
                            "arguments": {"file_path": "output/module_report.json", "content": "{}"},
                        },
                        "tool_result": {"content": "ok"},
                    }
                )
                seq += 1
            if turn == 6:
                events.append(
                    {
                        "seq": seq,
                        "event_type": "assistant_message",
                        "conversation_turn": turn,
                        "content": "MOD-04 has the largest loc_count: 984379.",
                    }
                )
                seq += 1
            events.append({"seq": seq, "event_type": "conversation_turn_finished", "turn_index": turn})
            seq += 1
        events.append({"seq": seq, "event_type": "conversation_finished"})
        events.append({"seq": seq + 1, "event_type": "run_finished"})

        trace_dir = run_dir / "artifacts" / "container" / "trace" / "synthetic"
        write_json(trace_dir / "events.json", events)
        write_json(
            trace_dir / "summary.json",
            {
                "trace_complete": True,
                "conversation": {
                    "turns_completed": 10,
                    "native_session_ids": ["synthetic-session"],
                    "new_session_before_turns": [],
                },
            },
        )
        provider_path = run_dir / "artifacts" / "container" / "provider_requests" / "events.jsonl"
        provider_path.parent.mkdir(parents=True, exist_ok=True)
        provider_path.write_text(
            json.dumps(
                {
                    "model_visible": True,
                    "tool_inventory": {
                        "complete": True,
                        "request_body_valid": True,
                        "tools": [{"name": "update_plan"}, {"name": "exec_command"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        perfect = score(workspace, run_dir)
        assert set(perfect) == yaml_ids, (set(perfect), yaml_ids)
        assert set(perfect.values()) == {1}, perfect

        # Stale-field injection must affect only the schema/revocation component, not scope or values.
        stale = json.loads((workspace / "output" / "module_report.json").read_text(encoding="utf-8"))
        stale[released[0]]["reviewer"] = issued[released[0]]["reviewer"]
        write_json(workspace / "output" / "module_report.json", stale)
        mutated = score(workspace, run_dir)
        assert mutated["ar_final_schema_exact_and_reviewer_absent"] == 0, mutated
        assert mutated["ar_final_scope_is_latest_released_only"] == 1, mutated
        assert mutated["ar_final_values_exact_and_authoritative"] == 1, mutated

        # Opaque model inputs must charge the two harness context metrics without invalidating lifecycle.
        for event in events:
            if event.get("event_type") == "model_input":
                event["messages"] = None
        write_json(trace_dir / "events.json", events)
        opaque = score(workspace, run_dir)
        assert opaque["hr_each_revision_input_readable_by_model"] == 0, opaque
        assert opaque["hr_ordered_goal_history_visible_at_revocation"] == 0, opaque
        assert opaque["hr_trace_lifecycle_and_all_turns_complete"] == 1, opaque

    print("verifier selftest: perfect synthetic trace scores every declared item 1")
    print("mutation selftest: stale reviewer and opaque model input fail only their attributed checks")
    print(f"declared binary metrics: {len(yaml_ids)} (no rubric or N/A path)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
