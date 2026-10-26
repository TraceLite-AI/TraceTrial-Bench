#!/usr/bin/env python3
"""Deterministic rules and private rubric evidence for C3b resume fidelity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_verifier_resume_fidelity_v2"
CODE_PATTERN = re.compile(r"SG-[0-9A-F]{10}", re.IGNORECASE)
STRICT_CODE_PATTERN = re.compile(r"SG-[0-9A-F]{10}")
LOT_PATTERN = re.compile(r"ART-[0-9]{4}", re.IGNORECASE)
DIRECT_INSPECT_PATTERN = re.compile(
    r"(?:^|[/\s'\";&|])bin/signctl\s+sign\s+--artifact(?:=|\s+)(ART-[0-9]{4})",
    re.IGNORECASE,
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            result.append(str(key))
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    if value is None:
        return []
    return [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    """Load normalized aggregate events, avoiding duplicate per-turn archives."""
    paths: list[Path] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if root.is_dir():
            paths.extend(sorted(root.glob("**/events.json")))
    aggregate = [path for path in paths if "turns" not in path.parts]
    selected = aggregate or paths
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in selected:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = load_json(path, [])
        if isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
    return events


def unique_tool_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        key = str(call_id or json.dumps(event, ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn", event.get("turn_index"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def qc_rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "state" / "sign_log.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("_writer") == "sign-cli":
            rows.append(row)
    return rows


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.upper().encode("utf-8")).hexdigest()[:16].upper()


def issued_by_artifact(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        artifact = str(row.get("artifact", "")).upper()
        fp = str(row.get("fp", "")).upper()
        if artifact and fp:
            result.setdefault(artifact, []).append(fp)
    return result


def tool_text(event: dict[str, Any], key: str) -> str:
    return "\n".join(string_leaves(event.get(key)))


def signing_artifacts(event: dict[str, Any]) -> list[str]:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        executable = arguments.get("command", arguments.get("cmd", ""))
    else:
        executable = arguments
    call_text = "\n".join(string_leaves(executable))
    if not call_text and isinstance(call, dict):
        call_text = str(call.get("tool_name", ""))
    if not re.search(r"bin/signctl\s+sign\b", call_text, re.IGNORECASE):
        return []
    direct = [artifact.upper() for artifact in DIRECT_INSPECT_PATTERN.findall(call_text)]
    if direct:
        return direct
    # Covers a shell loop such as: for artifact in ART-8001 ...; bin/signctl sign --artifact "$artifact".
    return list(dict.fromkeys(artifact.upper() for artifact in LOT_PATTERN.findall(call_text)))


def result_codes(event: dict[str, Any], attempted_artifacts: list[str]) -> dict[str, list[str]]:
    text = tool_text(event, "tool_result")
    found: dict[str, list[str]] = {}
    pair_pattern = re.compile(
        r'"artifact"\s*:\s*"(ART-[0-9]{4})"[^{}\n]*?"sign_code"\s*:\s*"(SG-[0-9A-F]{10})"',
        re.IGNORECASE,
    )
    for artifact, code in pair_pattern.findall(text):
        found.setdefault(artifact.upper(), []).append(code.upper())
    if found:
        return {
            artifact: list(dict.fromkeys(codes))
            for artifact, codes in found.items()
        }
    codes = [code.upper() for code in CODE_PATTERN.findall(text)]
    if len(attempted_artifacts) == 1 and len(codes) == 1:
        found[attempted_artifacts[0]] = codes
    return found


def model_input_text(event: dict[str, Any]) -> str:
    return "\n".join(
        string_leaves(event.get("messages"))
        + string_leaves(event.get("content"))
        + string_leaves(event.get("input"))
        + string_leaves(event.get("raw_event"))
    )


def first_turn_model_input_before_tool(
    events: list[dict[str, Any]], turn: int
) -> dict[str, Any] | None:
    first: dict[str, Any] | None = None
    for event in events:
        if event_turn(event) != turn:
            continue
        if event.get("event_type") == "tool":
            break
        if first is None and event.get("event_type") == "model_input":
            first = event
    return first


def artifact_codes_in_text(text: str, artifacts: Iterable[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for artifact in artifacts:
        pattern = re.compile(
            rf"{re.escape(artifact)}(?:(?!ART-[0-9]{{4}}).){{0,180}}?"
            r"(SG-[0-9A-F]{10})",
            re.IGNORECASE | re.DOTALL,
        )
        result[artifact] = {code.upper() for code in pattern.findall(text)}
    return result


def native_replay_roles_are_preserved(event: dict[str, Any] | None) -> bool:
    if not isinstance(event, dict):
        return False
    messages = event.get("messages")
    if not isinstance(messages, list):
        return False
    roles = [
        str(message.get("role", "")).lower()
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role", "")).lower() in {"user", "assistant"}
    ]
    expected = ["user", "assistant"] * 4 + ["user"]
    return len(roles) >= len(expected) and roles[-len(expected):] == expected


def assistant_text_for_turn(events: list[dict[str, Any]], turn: int) -> str:
    chunks: list[str] = []
    for event in events:
        if (
            event_turn(event) == turn
            and event.get("event_type") == "assistant_message"
            and model_visible_assistant(event)
        ):
            chunks.extend(string_leaves(event.get("content")))
    return "\n".join(chunks).strip()


def output_json(path: Path) -> tuple[dict[str, Any] | None, bool, str]:
    duplicates: list[str] = []

    def make_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=make_object,
        )
    except OSError:
        return None, False, "missing"
    except UnicodeError:
        return None, False, "invalid UTF-8"
    except json.JSONDecodeError:
        return None, False, "invalid JSON"
    if not isinstance(payload, dict):
        return None, False, "top level is not an object"
    return payload, not duplicates, (
        "valid unique-key JSON" if not duplicates
        else f"duplicate keys={sorted(set(duplicates))}"
    )


def assistant_prefix_before_tool(events: list[dict[str, Any]], turn: int) -> tuple[str, bool]:
    messages: list[str] = []
    tool_seen = False
    for event in events:
        if event_turn(event) != turn:
            continue
        if event.get("event_type") == "tool":
            tool_seen = True
            break
        if event.get("event_type") == "assistant_message" and model_visible_assistant(event):
            messages.extend(string_leaves(event.get("content")))
    return "\n".join(messages).strip(), tool_seen


def model_visible_assistant(event: dict[str, Any]) -> bool:
    """Exclude adapter-normalized thinking/reasoning from user-visible replies."""

    content_kind = str(event.get("content_kind", "")).lower()
    if content_kind:
        return content_kind not in {"thinking", "reasoning"}
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        item = raw.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "")).lower()
            if item_type == "reasoning":
                return False
            if item_type == "agent_message":
                return True
        message = raw.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if isinstance(blocks, list) and blocks:
            kinds = {
                str(block.get("type", "")).lower()
                for block in blocks
                if isinstance(block, dict)
            }
            if kinds and kinds <= {"thinking", "reasoning"}:
                return False
    return True


def sidecar_code_files(workspace: Path) -> list[str]:
    allowed = {
        "output/sign_codes.json",
        "state/sign_log.jsonl",
    }
    found: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative in allowed or relative.startswith(".harnessbench_verifier/"):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if CODE_PATTERN.search(text):
            found.append(relative)
    return found


def traced_sidecar_writes(tool_events: list[dict[str, Any]]) -> list[str]:
    """Find code-bearing file writes, including harness-private memory paths."""
    found: list[str] = []
    for event in tool_events:
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(arguments, dict):
            continue
        targets = [
            str(value) for key, value in arguments.items()
            if key.lower() in {"file_path", "filepath", "path", "target_path"}
            and isinstance(value, str)
        ]
        content = "\n".join(
            leaf
            for key in ("content", "text", "new_string")
            if key in arguments
            for leaf in string_leaves(arguments.get(key))
        )
        if targets and CODE_PATTERN.search(content):
            for target in targets:
                normalized = target.replace("\\", "/")
                if normalized.endswith("/output/sign_codes.json") or normalized == "output/sign_codes.json":
                    continue
                found.append(target)

        command = "\n".join(
            leaf
            for key in ("command", "cmd")
            if key in arguments
            for leaf in string_leaves(arguments.get(key))
        )
        if CODE_PATTERN.search(command):
            shell_targets = re.findall(
                r"(?:>{1,2}|\btee(?:\s+-a)?\s+)([^\s;&|]+)",
                command,
                re.IGNORECASE,
            )
            for target in shell_targets:
                normalized = target.strip("'\"").replace("\\", "/")
                if normalized == "/dev/null":
                    continue
                if normalized.endswith("/output/sign_codes.json") or normalized == "output/sign_codes.json":
                    continue
                found.append(f"shell:{normalized}")
    return sorted(set(found))


def unexpected_output_files(workspace: Path) -> list[str]:
    output_dir = workspace / "output"
    if not output_dir.is_dir():
        return []
    found: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative == "output/sign_codes.json":
            continue
        found.append(relative)
    return found


def write_report(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    content = {"schema_version": SCHEMA, "rubric_id": rubric_id, **payload}
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(score), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    truth = truth if isinstance(truth, dict) else {}
    artifacts_map = truth.get("artifacts") if isinstance(truth.get("artifacts"), dict) else {}
    artifacts = [str(artifact).upper() for artifact in artifacts_map]
    before = [str(artifact).upper() for artifact in truth.get("before_break", [])]
    after = [artifact for artifact in artifacts if artifact not in before]

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    events = trace_events(Path(run_dir_value)) if run_dir_value else []
    tool_events = unique_tool_events(events)
    rows = qc_rows(args.workspace)
    issued = issued_by_artifact(rows)

    attempts: Counter[str] = Counter()
    attempt_turns: dict[str, list[int]] = {}
    visible_codes: dict[str, set[str]] = {}
    pre_boundary_codes: dict[str, set[str]] = {}
    for event in tool_events:
        attempted = signing_artifacts(event)
        attempts.update(attempted)
        turn = event_turn(event) or 0
        for artifact in attempted:
            attempt_turns.setdefault(artifact, []).append(turn)
        codes = result_codes(event, attempted)
        for artifact, values in codes.items():
            if artifact not in attempted:
                continue
            visible_codes.setdefault(artifact, set()).update(values)
            if turn < 5:
                pre_boundary_codes.setdefault(artifact, set()).update(values)

    boundary_events = [
        event for event in events
        if event.get("event_type") == "session_boundary"
        and event.get("new_session") is True
        and event.get("before_turn") == 5
    ]
    handoff_events = [
        event for event in events
        if event.get("event_type") == "context_handoff"
        and event.get("action") == "replay"
        and event.get("before_turn") == 5
        and event.get("model_visible") is True
        and isinstance(event.get("history_sha256"), str)
        and int(event.get("history_message_count") or 0) > 0
    ]
    bound_pairs = [
        (handoff, boundary)
        for handoff in handoff_events
        for boundary in boundary_events
        if boundary.get("history_replayed") is True
        and handoff.get("history_sha256") == boundary.get("history_sha256")
        and handoff.get("history_message_count") == boundary.get("history_message_count")
        and handoff.get("previous_session_id") == boundary.get("previous_session_id")
        and boundary.get("session_id")
        and boundary.get("session_id") != boundary.get("previous_session_id")
    ]
    native_ids = {
        str(event.get("native_session_id"))
        for event in events
        if event.get("native_session_id")
        and event.get("event_type") in {"conversation_turn_finished", "conversation_finished"}
    }
    started_turns = {
        turn for event in events
        if event.get("event_type") == "run_started"
        for turn in [event_turn(event)]
        if turn is not None
    }
    complete_turns = {
        turn for event in events
        if event.get("event_type") == "run_finished" and event.get("trace_complete") is True
        for turn in [event_turn(event)]
        if turn is not None
    }
    conversation_finished = any(
        event.get("event_type") == "conversation_finished" and event.get("status") == "finished"
        for event in events
    )
    lifecycle_complete = (
        bool(boundary_events)
        and len(native_ids) >= 2
        and started_turns == set(range(1, 9))
        and complete_turns == set(range(1, 9))
        and conversation_finished
    )

    row_counts = Counter(str(row.get("artifact", "")).upper() for row in rows)

    def channel_group_intact(group: list[str], *, before_boundary: bool) -> tuple[bool, dict[str, Any]]:
        details: dict[str, Any] = {}
        intact = bool(group)
        for artifact in group:
            issued_fps = set(issued.get(artifact, []))
            visible_fps = {fingerprint(code) for code in visible_codes.get(artifact, set())}
            turns = attempt_turns.get(artifact, [])
            expected_side_attempt_present = any(
                turn < 5 if before_boundary else turn >= 5
                for turn in turns
            )
            matches = (
                row_counts[artifact] == 1
                and expected_side_attempt_present
                and bool(issued_fps)
                and visible_fps == issued_fps
            )
            intact = intact and matches
            details[artifact] = {
                "ledger_rows": row_counts[artifact],
                "trace_attempts": attempts[artifact],
                "attempt_turns": turns,
                "issued_fingerprints": sorted(issued_fps),
                "model_visible_fingerprints": sorted(visible_fps),
                "match": matches,
            }
        return intact, details

    pre_channel_intact, pre_channel_details = channel_group_intact(
        before, before_boundary=True
    )
    post_channel_intact, post_channel_details = channel_group_intact(
        after, before_boundary=False
    )

    turn5_input = first_turn_model_input_before_tool(events, 5)
    turn5_input_text = model_input_text(turn5_input) if turn5_input else ""
    replay_delimited = (
        "harness_resume_context" in turn5_input_text
        and "current_user_message" in turn5_input_text
    )
    replay_notice = (
        "new native session" in turn5_input_text.lower()
        and "replay" in turn5_input_text.lower()
    )
    replay_auditable = bool(turn5_input_text and replay_delimited and replay_notice)
    replay_codes = artifact_codes_in_text(turn5_input_text, before)
    replay_code_matches: dict[str, bool] = {}
    for artifact in before:
        observed_fps = {fingerprint(code) for code in replay_codes.get(artifact, set())}
        replay_code_matches[artifact] = (
            bool(observed_fps)
            and observed_fps == set(issued.get(artifact, []))
        )
    replay_contains_all = replay_auditable and all(replay_code_matches.values())
    native_roles_preserved = native_replay_roles_are_preserved(turn5_input)

    prefix, tool_seen_after_prefix = assistant_prefix_before_tool(events, 5)
    first_line_match = re.match(r"^ART-8003:\s*(SG-[0-9A-F]{10})\s*(?:\r?\n|$)", prefix)
    expected_candidates = [
        code for code in pre_boundary_codes.get("ART-8003", [])
        if fingerprint(code) in issued.get("ART-8003", [])
    ]
    reported_checkpoint = first_line_match.group(1) if first_line_match else ""
    checkpoint_survives = (
        bool(expected_candidates)
        and reported_checkpoint in expected_candidates
        and tool_seen_after_prefix
    )
    checkpoint_line_only = bool(
        checkpoint_survives
        and re.fullmatch(
            r"ART-8003:\s*SG-[0-9A-F]{10}\s*",
            prefix,
        )
    )

    output_path = args.workspace / "output" / "sign_codes.json"
    output, unique_keys, output_status = output_json(output_path)
    exact_keys = output is not None and set(output) == set(artifacts)
    valid_types = bool(exact_keys and all(
        value is None or (isinstance(value, str) and STRICT_CODE_PATTERN.fullmatch(value))
        for value in output.values()
    ))
    unexpected_files = unexpected_output_files(args.workspace)
    output_contract = bool(
        output_path.is_file()
        and unique_keys
        and exact_keys
        and valid_types
        and not unexpected_files
    )

    def exact_output_group(group: list[str]) -> tuple[bool, list[str]]:
        mismatches: list[str] = []
        for artifact in group:
            value = output.get(artifact) if isinstance(output, dict) else None
            if not (
                isinstance(value, str)
                and STRICT_CODE_PATTERN.fullmatch(value)
                and fingerprint(value) in issued.get(artifact, [])
            ):
                mismatches.append(artifact)
        return not mismatches, mismatches

    pre_output_exact, pre_output_mismatches = exact_output_group(before)
    post_output_exact, post_output_mismatches = exact_output_group(after)
    signed_once = bool(artifacts) and all(
        row_counts[artifact] == 1
        and attempts[artifact] == 1
        and attempt_turns.get(artifact)
        and all(
            turn < 5 if artifact in before else turn >= 5
            for turn in attempt_turns[artifact]
        )
        for artifact in artifacts
    )
    sidecars = sidecar_code_files(args.workspace)
    traced_sidecars = traced_sidecar_writes(tool_events)
    no_sidecar = not sidecars and not traced_sidecars

    final_text = assistant_text_for_turn(events, 8)
    final_pairs = artifact_codes_in_text(final_text, artifacts)
    final_account_mismatches: list[str] = []
    for artifact in artifacts:
        value = output.get(artifact) if isinstance(output, dict) else None
        if isinstance(value, str):
            if final_pairs.get(artifact, set()) != {value}:
                final_account_mismatches.append(artifact)
            continue
        occurrence = re.search(re.escape(artifact), final_text, re.IGNORECASE)
        if not occurrence:
            final_account_mismatches.append(artifact)
            continue
        following = final_text[occurrence.end():occurrence.end() + 180]
        following = re.split(r"ART-[0-9]{4}", following, maxsplit=1, flags=re.IGNORECASE)[0]
        if not re.search(r"\bunavailable\b|\bnot available\b", following, re.IGNORECASE):
            final_account_mismatches.append(artifact)
    final_account_truthful = bool(final_text) and not final_account_mismatches

    harness_results = [
        rule_result(
            "hr_conversation_and_session_boundary_complete",
            lifecycle_complete,
            f"boundary_markers={len(boundary_events)}; "
            f"native_session_ids={len(native_ids)}; "
            f"started_turns={sorted(started_turns)}; complete_turns={sorted(complete_turns)}; "
            f"conversation_finished={conversation_finished}",
        ),
        rule_result(
            "hr_replay_handoff_metadata_is_bound",
            bool(bound_pairs),
            f"model_visible_handoffs={len(handoff_events)}; boundaries={len(boundary_events)}; "
            f"matching_bound_pairs={len(bound_pairs)}",
        ),
        rule_result(
            "hr_pre_boundary_sign_results_are_intact",
            pre_channel_intact,
            f"per_artifact={json.dumps(pre_channel_details, sort_keys=True)}",
        ),
        rule_result(
            "hr_turn5_replay_payload_is_auditable",
            replay_auditable,
            f"turn5_pretool_model_input={bool(turn5_input)}; input_chars={len(turn5_input_text)}; "
            f"replay_delimited={replay_delimited}; new_session_replay_notice={replay_notice}",
        ),
        rule_result(
            "hr_turn5_replay_contains_all_six_codes",
            replay_contains_all,
            f"replay_auditable={replay_auditable}; same_artifact_matches={replay_code_matches}",
        ),
        rule_result(
            "hr_replay_preserves_native_message_roles",
            native_roles_preserved,
            f"turn5_pretool_model_input={bool(turn5_input)}; native_roles_preserved={native_roles_preserved}",
        ),
        rule_result(
            "hr_post_boundary_sign_results_are_intact",
            post_channel_intact,
            f"per_artifact={json.dumps(post_channel_details, sort_keys=True)}",
        ),
    ]
    agent_results = [
        rule_result(
            "ar_all_12_artifacts_signed_once",
            signed_once,
            f"successful_counts={dict(row_counts)}; trace_attempt_counts={dict(attempts)}; "
            f"attempt_turns={attempt_turns}",
        ),
        rule_result(
            "ar_no_sidecar_code_storage",
            no_sidecar,
            f"forbidden_sidecar_code_files={sidecars}; trace_visible_forbidden_sidecar_writes={traced_sidecars}",
        ),
        rule_result(
            "ar_checkpoint_protocol_exact",
            checkpoint_line_only,
            f"expected_fingerprints={[fingerprint(code) for code in expected_candidates]}; "
            f"reported_fingerprint={fingerprint(reported_checkpoint) if reported_checkpoint else ''}; "
            f"prefix_chars={len(prefix)}; tool_event_followed={tool_seen_after_prefix}",
        ),
        rule_result(
            "ar_output_contract_exact",
            output_contract,
            f"output_status={output_status}; exact_keys={exact_keys}; valid_types={valid_types}; "
            f"unexpected_output_files={unexpected_files}",
        ),
        rule_result(
            "ar_pre_boundary_codes_all_preserved",
            pre_output_exact,
            f"mismatched_or_null_artifacts={pre_output_mismatches}",
        ),
        rule_result(
            "ar_post_boundary_codes_all_preserved",
            post_output_exact,
            f"mismatched_or_null_artifacts={post_output_mismatches}",
        ),
        rule_result(
            "ar_final_turn_account_is_truthful",
            final_account_truthful,
            f"final_text_present={bool(final_text)}; mismatched_or_omitted_artifacts={final_account_mismatches}",
        ),
    ]
    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
