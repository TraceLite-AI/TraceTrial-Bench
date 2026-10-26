#!/usr/bin/env python3
"""Mechanically migrate an explicitly supplied legacy task archive to layered scoring.

The legacy rubric and scorer files remain untouched.  New score sheets retain
the existing item ids and criterion text, while score_adapter.py translates the
legacy scorer's deterministic results to harnessbench_rule_results_v1.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


NON_PARSEABLE = {
    "C3b-resume-fidelity-L3-001",
    "L11-longrun-timeout-L3-001",
    "L13-plugin-compliance-L3-001",
    "L4a-goal-revision-L3-001",
    "L7-crossborder-archive-L3-001",
    "L9-hook-audit-trail-L3-001",
    "N1-ambiguous-spec-askuser-L3-001",
    "T1b-duplicate-tool-name-001",
    "T1c-reserved-tool-name-001",
}

NEUTRAL_AGENT_RULE_ID = "agent-layer-not-applicable"

ADAPTER_TEMPLATE = '''#!/usr/bin/env python3
"""Adapt this task's unchanged legacy scorer to the layered rule protocol."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
HARNESS_RULE_IDS = __HARNESS_RULE_IDS__
AGENT_RULE_IDS = __AGENT_RULE_IDS__
NEUTRAL_RULE_ID = "agent-layer-not-applicable"
_MISSING = object()


def _parse_json_output(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return _MISSING
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value
    return _MISSING


def _find(value: Any, item_id: str, path: str = "root") -> tuple[Any, str]:
    if isinstance(value, dict):
        if item_id in value:
            return value[item_id], f"{path}.{item_id}"
        if value.get("id") == item_id:
            for key in ("score", "value", "pass", "passed", "verdict", "result"):
                if key in value:
                    return value[key], f"{path}.{key}"
        for key, child in value.items():
            found, found_path = _find(child, item_id, f"{path}.{key}")
            if found is not _MISSING:
                return found, found_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found, found_path = _find(child, item_id, f"{path}[{index}]")
            if found is not _MISSING:
                return found, found_path
    return _MISSING, path


def _binary(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(float(value) >= 1.0)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "pass", "passed", "ok", "yes", "1"}:
            return 1
        if normalized in {"false", "fail", "failed", "no", "0", "none", "n/a", "na"}:
            return 0
    if isinstance(value, dict):
        for key in ("score", "value", "pass", "passed", "verdict", "result"):
            if key in value:
                return _binary(value[key])
    return 0


def _result(item_id: str, payload: Any, legacy_error: str) -> dict[str, Any]:
    if item_id == NEUTRAL_RULE_ID:
        return {
            "id": item_id,
            "score": 1,
            "reason": "原题没有独立的 Agent 评分项；该恒真项仅用于满足新格式。",
        }
    value, path = _find(payload, item_id)
    if value is _MISSING:
        detail = legacy_error or "legacy payload did not expose this item id"
        return {"id": item_id, "score": 0, "reason": detail}
    return {
        "id": item_id,
        "score": _binary(value),
        "reason": f"legacy result {path}={json.dumps(value, ensure_ascii=False, default=str)}",
    }


def main() -> int:
    required_legacy_ids = [
        item_id
        for item_id in [*HARNESS_RULE_IDS, *AGENT_RULE_IDS]
        if item_id != NEUTRAL_RULE_ID
    ]
    payload: Any = {}
    if required_legacy_ids:
        completed = subprocess.run(
            [sys.executable, str(HERE / "score.py"), *sys.argv[1:]],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = _parse_json_output(completed.stdout)
        if payload is _MISSING:
            message = (
                f"legacy scorer produced no JSON (exit={completed.returncode}): "
                + (completed.stderr.strip() or completed.stdout.strip() or "no output")[-1000:]
            )
            print(message, file=sys.stderr)
            return completed.returncode or 2
    result = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "harness": [_result(item_id, payload, "") for item_id in HARNESS_RULE_IDS],
            "agent": [_result(item_id, payload, "") for item_id in AGENT_RULE_IDS],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError(f"invalid legacy rubric: {path}")
    return value


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _criterion(item: dict[str, Any], *, registry: dict[str, Any] | None = None) -> str:
    parts = [_text(item.get("criterion", "未提供判据说明"))]
    labels = {
        "evidence": "原证据说明",
        "唤醒门": "唤醒门",
        "verdict_when_defect": "缺陷结论",
        "computed_by": "原计算方式",
        "不算": "不计入情形",
        "dim": "维度",
        "locates": "定位",
        "kind": "类型",
        "axis": "轴",
        "core": "核心项",
        "weight": "原权重",
        "min_score": "原阈值",
        "layer": "原层级",
    }
    for key, label in labels.items():
        if key in item and item[key] is not None:
            parts.append(f"[{label}] {_text(item[key])}")
    anchor = item.get("anchor")
    if anchor:
        parts.append(f"[原证据锚点] {_text(anchor)}")
    if registry is not None:
        registered = _criterion(registry)
        if registered not in parts:
            parts.append(f"[程序判据登记] {registered}")
    return "\n\n".join(part for part in parts if part)


def _rule_layer(item_id: str) -> str:
    return "harness" if item_id.lower().startswith("hr") else "agent"


def _is_program_item(item: dict[str, Any]) -> bool:
    if item.get("type") == "rule":
        return True
    anchor = item.get("anchor")
    return (
        item.get("type") == "rubric"
        and isinstance(anchor, dict)
        and anchor.get("kind") == "none"
    )


def _literal_keywords(item: dict[str, Any]) -> list[str]:
    anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
    raw = " ".join(str(anchor.get(key, "")) for key in ("pattern", "kind_name"))
    tokens = re.findall(r"[A-Za-z0-9_.-]{2,}", raw)
    ignored = {"kind", "none", "true", "false", "window"}
    keywords = [token for token in tokens if token.lower() not in ignored]
    if not keywords:
        kind = str(anchor.get("kind", ""))
        defaults = {
            "error": ["error", "failed", "timeout", "exit"],
            "tool": ["tool", "result", "command", "status"],
            "call_digest": ["command", "result", "path", "state", "status", "error"],
            "result_match": ["result", "status", "output", "error"],
            "event": ["error", "timeout", "failed", "status"],
            "system_prompt": ["system", "tool", "instruction"],
            "turns": ["user", "assistant", "turn"],
            "file_exists": ["file", "path", "output"],
        }
        keywords = defaults.get(kind, ["tool", "result", "status", "error"])
    return list(dict.fromkeys(keywords))[:12]


def _evidence(item: dict[str, Any], layer: str) -> dict[str, Any]:
    if layer == "agent":
        keywords = ["output", ".json", ".md", ".csv", ".txt"]
    else:
        keywords = _literal_keywords(item)
    return {
        "event_types": ["tool", "assistant_message", "run_failed"],
        "fields": [
            "tool_name",
            "tool_call",
            "tool_result",
            "content",
            "raw_event",
            "error",
        ],
        "keywords": keywords,
        "match": "any",
        "case_sensitive": False,
        "max_events": 20,
    }


def _sheet_text(layer: str, items: list[dict[str, Any]]) -> str:
    lines = ["version: score_v1", f"layer: {layer}", "items:"]
    for item in items:
        lines.extend(
            [
                f"  - id: {item['id']}",
                f"    type: {item['type']}",
                "    criterion: " + json.dumps(item["criterion"], ensure_ascii=False),
            ]
        )
        if item["type"] == "rule":
            lines.append("    evidence: null")
        else:
            evidence = item["evidence"]
            lines.extend(
                [
                    "    evidence:",
                    "      event_types: " + json.dumps(evidence["event_types"], ensure_ascii=False),
                    "      fields: " + json.dumps(evidence["fields"], ensure_ascii=False),
                    "      keywords: " + json.dumps(evidence["keywords"], ensure_ascii=False),
                    f"      match: {evidence['match']}",
                    f"      case_sensitive: {str(evidence['case_sensitive']).lower()}",
                    f"      max_events: {evidence['max_events']}",
                ]
            )
    return "\n".join(lines) + "\n"


def _new_item(
    item: dict[str, Any],
    *,
    layer: str,
    item_type: str,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "id": str(item["id"]),
        "type": item_type,
        "criterion": _criterion(item, registry=registry),
    }
    if item_type == "rubric":
        result["evidence"] = _evidence(item, layer)
    return result


def _build_sheets(verifier: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hr_items = _read_yaml(verifier / "rubric_hr.yaml")["items"]
    ar_path = verifier / "rubric_ar.yaml"
    ar_items = _read_yaml(ar_path)["items"] if ar_path.is_file() else []

    if any(not isinstance(item, dict) or not item.get("id") for item in [*hr_items, *ar_items]):
        raise ValueError(f"rubric contains an item without id: {verifier}")

    program_agent = {
        str(item["id"]): item
        for item in hr_items
        if _is_program_item(item) and _rule_layer(str(item["id"])) == "agent"
    }
    harness: list[dict[str, Any]] = []
    for item in hr_items:
        item_id = str(item["id"])
        if _is_program_item(item):
            if _rule_layer(item_id) == "harness":
                harness.append(_new_item(item, layer="harness", item_type="rule"))
            continue
        harness.append(_new_item(item, layer="harness", item_type="rubric"))

    agent: list[dict[str, Any]] = []
    seen_agent: set[str] = set()
    for item in ar_items:
        item_id = str(item["id"])
        registry = program_agent.get(item_id)
        agent.append(
            _new_item(
                item,
                layer="agent",
                item_type="rule" if registry is not None else "rubric",
                registry=registry,
            )
        )
        seen_agent.add(item_id)
    for item_id, item in program_agent.items():
        if item_id not in seen_agent:
            agent.append(_new_item(item, layer="agent", item_type="rule"))

    if not agent:
        agent.append(
            {
                "id": NEUTRAL_AGENT_RULE_ID,
                "type": "rule",
                "criterion": (
                    "原题没有独立的 Agent 规则或 Rubric；此恒真项仅用于满足 "
                    "score_v1 的非空格式要求，不新增评分要求。"
                ),
            }
        )

    all_ids = [item["id"] for item in [*harness, *agent]]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError(f"duplicate ids after migration: {verifier}")
    return harness, agent


def _append_scoring(task_yaml: Path) -> None:
    text = task_yaml.read_text(encoding="utf-8")
    if re.search(r"(?m)^scoring:\s*$", text):
        raise ValueError(f"task already has scoring configuration: {task_yaml}")
    addition = (
        "scoring:\n"
        "  version: layered_100_v1\n"
        "  harness_score: verifier/harness_score.yaml\n"
        "  agent_score: verifier/agent_score.yaml\n"
    )
    task_yaml.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")


def _rewrite_run_sh(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    replaced = text.replace('/score.py"', '/score_adapter.py"')
    if replaced == text:
        raise ValueError(f"run.sh does not invoke score.py: {path}")
    path.write_text(replaced, encoding="utf-8")


def _mark_run_documentation(path: Path) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    for name in sorted(NON_PARSEABLE):
        text = re.sub(rf"(?<!Non_){re.escape(name)}", f"Non_{name}", text)
    path.write_text(text, encoding="utf-8")


def _migrate_case(case_dir: Path) -> tuple[int, int]:
    verifier = case_dir / "verifier"
    harness, agent = _build_sheets(verifier)
    (verifier / "harness_score.yaml").write_text(
        _sheet_text("harness", harness), encoding="utf-8"
    )
    (verifier / "agent_score.yaml").write_text(
        _sheet_text("agent", agent), encoding="utf-8"
    )
    harness_rule_ids = [item["id"] for item in harness if item["type"] == "rule"]
    agent_rule_ids = [item["id"] for item in agent if item["type"] == "rule"]
    adapter = ADAPTER_TEMPLATE.replace(
        "__HARNESS_RULE_IDS__", repr(harness_rule_ids)
    ).replace("__AGENT_RULE_IDS__", repr(agent_rule_ids))
    adapter_path = verifier / "score_adapter.py"
    adapter_path.write_text(adapter, encoding="utf-8")
    adapter_path.chmod(0o755)
    _rewrite_run_sh(verifier / "run.sh")
    _append_scoring(case_dir / "task.yaml")
    return len(harness), len(agent)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="path to an external legacy task archive; not the final Task collection")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()

    existing = {path.name for path in root.iterdir() if path.is_dir()}
    unresolved = sorted(name for name in NON_PARSEABLE if name in existing)
    already_marked = sorted(
        name for name in NON_PARSEABLE if f"Non_{name}" in existing
    )
    if set(unresolved) | set(already_marked) != NON_PARSEABLE:
        missing = sorted(NON_PARSEABLE - set(unresolved) - set(already_marked))
        raise ValueError(f"expected unresolved task directories are missing: {missing}")

    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in NON_PARSEABLE and not path.name.startswith("Non_")
    )
    print(f"parseable={len(candidates)} unresolved={len(NON_PARSEABLE)}")
    if not args.apply:
        for path in candidates:
            harness, agent = _build_sheets(path / "verifier")
            print(f"would migrate {path.name}: harness={len(harness)} agent={len(agent)}")
        for name in unresolved:
            print(f"would rename {name} -> Non_{name}")
        return 0

    for name in unresolved:
        source = root / name
        destination = root / f"Non_{name}"
        if destination.exists():
            raise ValueError(f"rename destination already exists: {destination}")
        source.rename(destination)
        print(f"renamed {name} -> {destination.name}")

    _mark_run_documentation(root / "run.md")

    for path in candidates:
        harness_count, agent_count = _migrate_case(path)
        print(f"migrated {path.name}: harness={harness_count} agent={agent_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
