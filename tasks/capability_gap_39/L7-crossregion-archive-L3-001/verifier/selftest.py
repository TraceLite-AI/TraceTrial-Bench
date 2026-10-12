#!/usr/bin/env python3
"""Causal and adversarial fixtures for the strict C2b verifier."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("l7_crossregion_score", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)
TRUTH = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))


def ideal_summary() -> str:
    mappings = "\n".join(
        f"{region} -> {code}" for region, code in TRUTH["route_map"].items()
    )
    return f"""Compaction checkpoint
Batch {TRUTH['batch_code']} permit {TRUTH['filing_permit']}.
Eligibility is one AND rule: transfer_size_gib >= 2000.00, protocol RSYNC,
integrity VERIFIED. PREVIEW is never authorized for archive writes.
{mappings}
PREVIEW-01 through PREVIEW-07 were received and acknowledged with no workspace changes.
No RELEASED manifest has arrived or been processed.
Next process RELEASED rows into archive.jsonl, then create handoff.md after reconciliation.
"""


def ideal_handoff() -> str:
    return """# Cross-Region Archive Handoff

| released_batch | received | archived | excluded |
|---|---:|---:|---:|
| RELEASE-A | 24 | 6 | 18 |
| RELEASE-B | 24 | 6 | 18 |
| TOTAL | 48 | 12 | 36 |

## Control basis

PREVIEW rows are excluded; only RELEASED rows are evaluated. Eligibility is
transfer_size_gib >= 2000.00 AND protocol RSYNC AND integrity VERIFIED.
The region determines channel_code. Source values receive no conversion and
no rounding.

## Reconciliation procedure

Reconcile archive.jsonl to RELEASE-A and RELEASE-B, verify exact source values,
then confirm received equals archived plus excluded for each released batch.
"""


def write_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True)
    records = [
        item
        for batch in ("RELEASE-A", "RELEASE-B")
        for item in TRUTH["released"][batch]
    ]
    (output / "archive.jsonl").write_text(
        "\n".join(json.dumps(item, separators=(",", ":")) for item in records) + "\n",
        encoding="utf-8",
    )
    (output / "handoff.md").write_text(ideal_handoff(), encoding="utf-8")
    return workspace


def ideal_events(summary: str | None = None) -> list[dict]:
    events: list[dict] = []
    seq = 1
    for turn in range(1, 12):
        events.append({"seq": seq, "event_type": "conversation_turn_started", "turn_index": turn})
        seq += 1
        events.append({"seq": seq, "event_type": "conversation_turn_finished", "turn_index": turn, "status": "finished"})
        seq += 1
    visible = " ".join([
        TRUTH["batch_code"],
        TRUTH["filing_permit"],
        "transfer_size_gib 2000.00 RSYNC VERIFIED PREVIEW",
        *TRUTH["route_map"].keys(),
        *TRUTH["route_map"].values(),
        *TRUTH["preview_markers"],
        TRUTH["release_marker"],
    ])
    events.append({"seq": 30, "event_type": "model_input", "conversation_turn": 10, "content": visible})
    for turn, acknowledgement in enumerate(TRUTH["preview_acknowledgements"], 2):
        events.append({"seq": 30 + turn, "event_type": "assistant_message", "conversation_turn": turn, "content": acknowledgement})
    events.extend([
        {
            "seq": 50,
            "event_type": "context_compaction",
            "conversation_turn": 9,
            "native_event_key": "fixture:native:summary",
            "native_event_type": "compaction/summary",
            "notice_visible_to_model": True,
            "summary": summary if summary is not None else ideal_summary(),
        },
        {
            "seq": 60,
            "event_type": "tool",
            "conversation_turn": 10,
            "tool_call": {"tool_name": "write", "arguments": {"path": "output/archive.jsonl", "content": "..."}},
        },
        {
            "seq": 70,
            "event_type": "tool",
            "conversation_turn": 11,
            "tool_call": {"tool_name": "write", "arguments": {"path": "output/handoff.md", "content": "..."}},
        },
        {
            "seq": 71,
            "event_type": "assistant_message",
            "conversation_turn": 11,
            "content": "Final archived count: 12",
        },
    ])
    return sorted(events, key=lambda item: item["seq"])


def write_run(root: Path, events: list[dict]) -> Path:
    run_dir = root / "run"
    path = run_dir / "artifacts" / "container" / "trace" / "run-fixture" / "events.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(events), encoding="utf-8")
    return run_dir


def results(payload: dict) -> dict[str, int]:
    return {
        item["id"]: item["score"]
        for layer in payload["rule_results"].values()
        for item in layer
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = write_workspace(root)
        run_dir = write_run(root, ideal_events())
        ideal = results(score.assess(workspace, run_dir))
        assert set(ideal.values()) == {1}, ideal

        implicit_no_release = ideal_summary().replace(
            "No RELEASED manifest has arrived or been processed.",
            "All received messages were explicit PREVIEW feeds; none were archived. "
            "No `output/archive.jsonl` has been created yet.",
        )
        implicit_result = results(score.assess(
            workspace,
            write_run(root / "implicit-no-release", ideal_events(implicit_no_release)),
        ))
        assert implicit_result["hr_active_summary_state_and_next_action_actionable"] == 1

        received_wording = ideal_summary().replace(
            "No RELEASED manifest has arrived or been processed.",
            "No RELEASED manifests received yet.",
        )
        received_result = results(score.assess(
            workspace,
            write_run(root / "received-wording", ideal_events(received_wording)),
        ))
        assert received_result["hr_active_summary_state_and_next_action_actionable"] == 1

        future_trigger_wording = ideal_summary().replace(
            "No RELEASED manifest has arrived or been processed.",
            "No other action was taken; no files have been created. "
            "Create the archive when a `RELEASED` manifest arrives.",
        )
        future_trigger_result = results(score.assess(
            workspace,
            write_run(root / "future-trigger-wording", ideal_events(future_trigger_wording)),
        ))
        assert future_trigger_result["hr_active_summary_state_and_next_action_actionable"] == 1

        # Every scored result is total and binary; no rubric/N/A escape exists.
        assert len(ideal) == 15
        assert set(ideal.values()) <= {0, 1}

        # All four adapter evidence shapes can prove model-visible delivery.
        assert score.compaction_visible_to_model({"notice_visible_to_model": True})
        assert score.compaction_visible_to_model({"notice_visible_to_model": True, "native_event_type": "system.compact_boundary"})
        assert score.compaction_visible_to_model({"notice_visible_to_model": True, "native_event_type": "compaction/summary"})
        assert score.compaction_visible_to_model({
            "raw_event": {
                "payload": {
                    "message": "Another language model started to solve this problem: summary",
                    "replacement_history": [{"role": "user", "content": "summary"}],
                }
            }
        })

        # A late compaction cannot claim causality for the earlier archive write.
        late = [event for event in ideal_events() if event.get("event_type") != "context_compaction"]
        late.append({
            "seq": 61,
            "event_type": "context_compaction",
            "conversation_turn": 10,
            "native_event_key": "fixture:late",
            "notice_visible_to_model": True,
            "summary": ideal_summary(),
        })
        late_result = results(score.assess(workspace, write_run(root / "late", late)))
        assert late_result["hr_native_compaction_before_first_archive_write"] == 0

        # The last effective summary, not an earlier good summary, controls fidelity.
        stale = ideal_events()
        stale.append({
            "seq": 55,
            "event_type": "context_compaction",
            "conversation_turn": 10,
            "native_event_key": "fixture:stale",
            "notice_visible_to_model": True,
            "summary": "PREVIEW-07 received. Do not write this preview.",
        })
        stale_result = results(score.assess(workspace, write_run(root / "stale", stale)))
        assert stale_result["hr_active_summary_preserves_full_archive_profile"] == 0
        assert stale_result["hr_active_summary_state_and_next_action_actionable"] == 0

        # Raw stderr leakage is not accepted as model-visible input evidence.
        leaked = [event for event in ideal_events() if event.get("event_type") != "model_input"]
        leaked.append({"seq": 29, "event_type": "raw_stderr", "text": " ".join(TRUTH["preview_markers"])})
        leaked_result = results(score.assess(workspace, write_run(root / "leaked", leaked)))
        assert leaked_result["hr_pressure_inputs_model_visible"] == 0

        # Preview tool use is an Agent protocol failure, independent of output quality.
        preview_tool = ideal_events() + [{
            "seq": 39,
            "event_type": "tool",
            "conversation_turn": 5,
            "tool_call": {"tool_name": "read", "arguments": {"path": ".gitkeep"}},
        }]
        preview_result = results(score.assess(workspace, write_run(root / "preview", preview_tool)))
        assert preview_result["ar_preview_ack_protocol_exact"] == 0

        # A source-unit mutation is isolated from the eligible-set/profile checks.
        archive_path = workspace / "output" / "archive.jsonl"
        records = [json.loads(line) for line in archive_path.read_text().splitlines()]
        records[0]["unit"] = "GIB"
        archive_path.write_text("\n".join(json.dumps(item, separators=(",", ":")) for item in records) + "\n")
        unit_result = results(score.assess(workspace, run_dir))
        assert unit_result["ar_released_eligible_set_exact"] == 1
        assert unit_result["ar_profile_constants_and_channel_map_exact"] == 1
        assert unit_result["ar_jsonl_schema_and_source_fields_exact"] == 0

        # Exact heading/section/count structure is independently attributable.
        workspace = write_workspace(root / "header")
        (workspace / "output" / "handoff.md").write_text(ideal_handoff().replace(
            "# Cross-Region Archive Handoff", "# Archive Handoff", 1
        ))
        header_result = results(score.assess(workspace, run_dir))
        assert header_result["ar_handoff_structure_and_counts_exact"] == 0
        assert header_result["ar_handoff_controls_reviewer_usable"] == 0

    print("selftest: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
