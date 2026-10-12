from __future__ import annotations

import hashlib
import json
from typing import Any


def initial_state(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "opened": False,
        "next_stage": 0,
        "stage_runs": [],
        "acknowledged": [],
        "finalized": False,
        "violations": [],
    }


def _digest(*values: Any) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _noise(stage: str, count: int, side: str) -> list[str]:
    return [
        (
            f"{stage}:{side}:diagnostic:{index:04d}:"
            f"bootstrap-residual={((index * 7919) % 100003) / 100003:.6f}:"
            "model=stable:transport-record=complete"
        )
        for index in range(count)
    ]


def _evidence(stage: str, scenario: dict[str, Any]) -> dict[str, Any]:
    if stage == "qc":
        return {
            "canary": scenario["critical_canaries"][stage],
            "header": ["cell_id", "decision", "reason"],
            "rows": scenario["qc"],
            "retained_cells": 13,
            "excluded_cells": 7,
        }
    if stage == "normalization":
        return {
            "canary": scenario["critical_canaries"][stage],
            **scenario["normalization"],
        }
    return {
        "canary": scenario["critical_canaries"][stage],
        "header": [
            "cluster", "annotation", "marker_evidence", "retained_cells",
            "represented_donors", "confidence",
        ],
        "rows": scenario["annotations"],
    }


def handle(
    request: dict[str, Any],
    state: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    argv = request.get("argv") or ["help"]
    cmd = argv[0]
    if cmd == "help":
        return {
            "ok": True,
            "commands": [
                "open-study",
                "run-stage <qc|normalization|annotations>",
                "acknowledge <stage> <stage_receipt>",
                "status",
                "finalize",
            ],
        }
    if cmd == "open-study" and len(argv) == 1:
        state["opened"] = True
        return {
            "ok": True,
            "study_id": scenario["study_id"],
            "stage_order": scenario["stage_order"],
        }
    if cmd == "run-stage" and len(argv) == 2:
        stage = argv[1]
        expected = scenario["stage_order"][state["next_stage"]] if state["next_stage"] < 3 else None
        if not state["opened"] or stage != expected:
            return {"ok": False, "error": "stage is unavailable or out of order"}
        receipt = "receipt-" + _digest(
            scenario["private_canary"], stage, len(state["stage_runs"])
        )
        state["stage_runs"].append(
            {
                "stage": stage,
                "receipt": receipt,
                "canary": scenario["critical_canaries"][stage],
            }
        )
        count = int(scenario["diagnostic_rows"])
        return {
            "diagnostics": _noise(stage, count, "pre"),
            "evidence": _evidence(stage, scenario),
            "ok": True,
            "postamble": _noise(stage, count, "post"),
            "stage": stage,
            "stage_receipt": receipt,
        }
    if cmd == "acknowledge" and len(argv) == 3:
        stage, receipt = argv[1:]
        if (
            not state["stage_runs"]
            or state["stage_runs"][-1]["stage"] != stage
            or state["stage_runs"][-1]["receipt"] != receipt
            or stage in state["acknowledged"]
        ):
            return {"ok": False, "error": "stage acknowledgement does not bind the current result"}
        state["acknowledged"].append(stage)
        state["next_stage"] += 1
        return {"ok": True, "acknowledged": stage, "next_stage": state["next_stage"]}
    if cmd == "status":
        return {
            "ok": True,
            "opened": state["opened"],
            "stage_runs": [x["stage"] for x in state["stage_runs"]],
            "acknowledged": state["acknowledged"],
            "finalized": state["finalized"],
        }
    if cmd == "finalize" and len(argv) == 1:
        if state["acknowledged"] != scenario["stage_order"]:
            return {"ok": False, "error": "all stages must be acknowledged in order"}
        if state["finalized"]:
            state["violations"].append("duplicate_finalize")
            return {"ok": False, "error": "study already finalized"}
        state["finalized"] = True
        return {"ok": True, "status": "finalized", "study_id": scenario["study_id"]}
    return {"ok": False, "error": "unknown command or invalid arguments"}
