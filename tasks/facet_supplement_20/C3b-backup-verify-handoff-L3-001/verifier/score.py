#!/usr/bin/env python3
"""Deterministic, attribution-oriented rules for C3b resume fidelity."""

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
SCHEMA = "harnessbench_verifier_resume_fidelity_v3"
SETS = tuple(f"SET-{number}" for number in range(8001, 8013))
PRE_BOUNDARY = SETS[:6]
POST_BOUNDARY = SETS[6:]
CODE_PATTERN = re.compile(r"SL-[0-9A-F]{10}", re.IGNORECASE)
SET_PATTERN = re.compile(r"SET-[0-9]{4}", re.IGNORECASE)
FP_PATTERN = re.compile(r"[0-9A-F]{16}", re.IGNORECASE)
DIRECT_CHECK_PATTERN = re.compile(
    r"(?:^|[/\s'\";&|])bin/verify\s+check\s+--set(?:=|\s+)(SET-[0-9]{4})",
    re.IGNORECASE,
)


class DuplicateKey(ValueError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_output(path: Path) -> tuple[Any, str]:
    if path.is_symlink() or not path.is_file():
        return None, "missing_or_symlink"
    try:
        return (
            json.loads(
                path.read_text(encoding="utf-8", errors="strict"),
                object_pairs_hook=strict_object,
            ),
            "valid_utf8_json",
        )
    except UnicodeError:
        return None, "invalid_utf8"
    except DuplicateKey:
        return None, "duplicate_key"
    except (OSError, json.JSONDecodeError):
        return None, "invalid_json"


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        aggregate = [
            path for path in root.glob("run-*/events.json") if path.is_file()
        ]
        for path in aggregate:
            value = load_json(path, [])
            if isinstance(value, list):
                candidates.append(
                    [item for item in value if isinstance(item, dict)]
                )
    return max(candidates, key=len, default=[])


def event_turn(event: dict[str, Any]) -> int | None:
    value = event.get("conversation_turn", event.get("turn_index"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def event_seq(event: dict[str, Any]) -> int:
    value = event.get("seq")
    return value if isinstance(value, int) else -1


def unique_tool_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str]] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        identity = str(call_id or event_seq(event))
        key = (event_turn(event), identity)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def verification_sets(event: dict[str, Any]) -> list[str]:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        executable = arguments.get("command", arguments.get("cmd", ""))
    else:
        executable = arguments
    text = "\n".join(string_leaves(executable))
    if not re.search(r"bin/verify\s+check\b", text, re.IGNORECASE):
        return []
    direct = [set_id.upper() for set_id in DIRECT_CHECK_PATTERN.findall(text)]
    if direct:
        return direct
    return list(
        dict.fromkeys(set_id.upper() for set_id in SET_PATTERN.findall(text))
    )


def result_codes(event: dict[str, Any], attempted: list[str]) -> dict[str, list[str]]:
    text = "\n".join(string_leaves(event.get("tool_result")))
    return result_codes_from_text(text, attempted)


def result_codes_from_text(
    text: str, attempted: list[str] | None = None
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    pair_pattern = re.compile(
        r'"set"\s*:\s*"(SET-[0-9]{4})"[^{}\n]*?'
        r'"seal_code"\s*:\s*"(SL-[0-9A-F]{10})"',
        re.IGNORECASE,
    )
    for set_id, code in pair_pattern.findall(text):
        values = found.setdefault(set_id.upper(), [])
        if code.upper() not in values:
            values.append(code.upper())
    if found:
        return found
    codes = [code.upper() for code in CODE_PATTERN.findall(text)]
    if attempted is not None and len(attempted) == 1 and len(codes) == 1:
        return {attempted[0]: codes}
    return {}


def verification_rows(workspace: Path) -> list[dict[str, str]]:
    path = workspace / "state" / "verify_log.jsonl"
    if path.is_symlink() or not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    for line in lines:
        try:
            value = json.loads(line, object_pairs_hook=strict_object)
        except (json.JSONDecodeError, DuplicateKey):
            return []
        if not isinstance(value, dict) or value.get("_writer") != "verify-cli":
            return []
        set_id = str(value.get("set", "")).upper()
        fingerprint_value = str(value.get("fp", "")).upper()
        if set_id not in SETS or FP_PATTERN.fullmatch(fingerprint_value) is None:
            return []
        rows.append({"set": set_id, "fp": fingerprint_value})
    return rows


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.upper().encode("utf-8")).hexdigest()[:16].upper()


def issued_by_set(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["set"], []).append(row["fp"])
    return result


def matching_source_codes(
    visible: dict[str, list[str]], issued: dict[str, list[str]]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for set_id in SETS:
        matching = {
            code.upper()
            for code in visible.get(set_id, [])
            if fingerprint(code) in issued.get(set_id, [])
        }
        if len(matching) == 1:
            result[set_id] = matching.pop()
    return result


def model_visible_assistant(event: dict[str, Any]) -> bool:
    kind = str(event.get("content_kind", "")).casefold()
    if kind in {"thinking", "reasoning"}:
        return False
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        item = raw.get("item")
        if isinstance(item, dict) and item.get("type") == "reasoning":
            return False
    return True


def assistant_prefix_before_tool(
    events: list[dict[str, Any]], turn: int
) -> tuple[str, bool]:
    messages: list[str] = []
    tool_seen = False
    for event in sorted(events, key=event_seq):
        if event_turn(event) != turn:
            continue
        if event.get("event_type") == "tool":
            tool_seen = True
            break
        if (
            event.get("event_type") == "assistant_message"
            and model_visible_assistant(event)
        ):
            messages.extend(string_leaves(event.get("content")))
    return "\n".join(messages).strip(), tool_seen


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def native_archive_text(
    run_dir: Path, events: list[dict[str, Any]], turn: int
) -> str:
    expected_hashes = {
        str(event.get("sha256", "")).casefold()
        for event in events
        if event.get("event_type") == "native_session_archive"
        and event_turn(event) == turn
        and event.get("status") == "available"
    }
    if not expected_hashes:
        return ""
    chunks: list[str] = []
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.glob("run-*/turns/*/native_sessions/*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved in seen or path.is_symlink() or path.stat().st_size > 64_000_000:
                continue
            seen.add(resolved)
            try:
                if sha256_file(path).casefold() not in expected_hashes:
                    continue
                for line in path.read_text(
                    encoding="utf-8", errors="strict"
                ).splitlines():
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunks.extend(string_leaves(value))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


def native_function_output_codes(
    run_dir: Path, events: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Recover immutable native tool outputs omitted by normalized truncation."""

    expected_hashes = {
        str(event.get("sha256", "")).casefold()
        for event in events
        if event.get("event_type") == "native_session_archive"
        and event.get("status") == "available"
    }
    found: dict[str, list[str]] = {}
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in root.glob("run-*/turns/*/native_sessions/*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved in seen or path.is_symlink() or path.stat().st_size > 64_000_000:
                continue
            seen.add(resolved)
            try:
                if sha256_file(path).casefold() not in expected_hashes:
                    continue
                lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeError):
                continue
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = value.get("payload") if isinstance(value, dict) else None
                is_codex_output = (
                    isinstance(value, dict)
                    and value.get("type") == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("type") == "function_call_output"
                )
                if not is_codex_output:
                    continue
                for set_id, codes in result_codes_from_text(
                    "\n".join(string_leaves(payload.get("output")))
                ).items():
                    target = found.setdefault(set_id, [])
                    for code in codes:
                        if code not in target:
                            target.append(code)
    return found


def turn_model_input_text(events: list[dict[str, Any]], turn: int) -> str:
    return "\n".join(
        text
        for event in events
        if event.get("event_type") == "model_input" and event_turn(event) == turn
        for text in string_leaves(event)
    )


def tagged_payload(surface: str, tag: str) -> str:
    match = re.search(
        rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>",
        surface,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else ""


def replay_roles(payload: str) -> list[str]:
    start = payload.find("[")
    if start < 0:
        return []
    try:
        value, _ = json.JSONDecoder().raw_decode(payload[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    roles: list[str] = []
    for message in value:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            return []
        roles.append(message["role"])
    return roles


def sidecar_code_files(workspace: Path) -> list[str]:
    allowed = {"output/seal_codes.json", "state/verify_log.jsonl"}
    found: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative in allowed or relative.startswith(".harnessbench_verifier/"):
            continue
        try:
            if path.stat().st_size <= 1_000_000 and CODE_PATTERN.search(
                path.read_text(encoding="utf-8", errors="strict")
            ):
                found.append(relative)
        except (OSError, UnicodeError):
            continue
    return found


def traced_sidecar_writes(tool_events: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for event in tool_events:
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(arguments, dict):
            continue
        targets = [
            str(value)
            for key, value in arguments.items()
            if key.casefold() in {"file_path", "filepath", "path", "target_path"}
            and isinstance(value, str)
        ]
        content = "\n".join(
            text
            for key in ("content", "text", "new_string")
            for text in string_leaves(arguments.get(key))
        )
        if not targets or CODE_PATTERN.search(content) is None:
            continue
        for target in targets:
            normalized = target.replace("\\", "/")
            if not normalized.endswith("/output/seal_codes.json") and normalized != "output/seal_codes.json":
                found.append(target)
    return sorted(set(found))


def unexpected_workspace_files(workspace: Path) -> list[str]:
    baseline = HERE.parent / ".baseline_workspace"
    expected = (
        {
            path.relative_to(baseline).as_posix()
            for path in baseline.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if baseline.is_dir()
        else {
            "bin/verify",
            "docs/VERIFY_RULES.md",
            *(f"sets/{set_id}.md" for set_id in SETS),
        }
    )
    allowed = expected | {"state/verify_log.jsonl", "output/seal_codes.json"}
    harness_scaffolding = {
        "AGENTS.md",
        "BOOTSTRAP.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "TOOLS.md",
        "USER.md",
        "openclaw-workspace-state.json",
    }
    return sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and not path.relative_to(workspace).as_posix().startswith(
            ".harnessbench_verifier/"
        )
        and not path.relative_to(workspace).as_posix().startswith(".git/")
        and path.relative_to(workspace).as_posix() not in harness_scaffolding
        and path.relative_to(workspace).as_posix() not in allowed
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    truth_valid = (
        isinstance(truth, dict)
        and tuple(truth.get("sets", {})) == SETS
        and tuple(truth.get("before_break", [])) == PRE_BOUNDARY
        and tuple(truth.get("after_break", [])) == POST_BOUNDARY
        and truth.get("break_at_turn") == 5
    )

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = trace_events(run_dir) if run_dir_value else []
    tools = unique_tool_events(events)
    rows = verification_rows(args.workspace)
    issued = issued_by_set(rows)

    attempts: Counter[str] = Counter()
    visible_codes: dict[str, list[str]] = {}
    pre_boundary_visible: dict[str, list[str]] = {}
    for event in tools:
        attempted = verification_sets(event)
        attempts.update(attempted)
        for set_id, codes in result_codes(event, attempted).items():
            visible_codes.setdefault(set_id, []).extend(codes)
            if (event_turn(event) or 0) < 5:
                pre_boundary_visible.setdefault(set_id, []).extend(codes)
    for set_id, codes in native_function_output_codes(run_dir, events).items():
        target = visible_codes.setdefault(set_id, [])
        for code in codes:
            if code not in target:
                target.append(code)
        if set_id in PRE_BOUNDARY:
            pre_target = pre_boundary_visible.setdefault(set_id, [])
            for code in codes:
                if code not in pre_target:
                    pre_target.append(code)
    source_codes = matching_source_codes(visible_codes, issued)
    pre_source_codes = matching_source_codes(pre_boundary_visible, issued)

    started_turns = {
        event_turn(event)
        for event in events
        if event.get("event_type") == "run_started" and event_turn(event) is not None
    }
    complete_turns = {
        event_turn(event)
        for event in events
        if event.get("event_type") == "run_finished"
        and event.get("trace_complete") is True
        and event_turn(event) is not None
    }
    conversation_finished = any(
        event.get("event_type") == "conversation_finished"
        and event.get("status") == "finished"
        for event in events
    )
    turns_complete = (
        started_turns == set(range(1, 9))
        and complete_turns == set(range(1, 9))
        and conversation_finished
    )

    boundaries = [
        event
        for event in events
        if event.get("event_type") == "session_boundary"
        and event.get("new_session") is True
        and event.get("before_turn") == 5
    ]
    native_ids = {
        str(event.get("native_session_id"))
        for event in events
        if event.get("native_session_id")
        and event.get("event_type")
        in {"conversation_turn_finished", "conversation_finished"}
    }
    native_boundary = len(boundaries) == 1 and len(native_ids) >= 2

    handoffs = [
        event
        for event in events
        if event.get("event_type") == "context_handoff"
        and event.get("action") == "replay"
        and event.get("before_turn") == 5
        and event.get("model_visible") is True
        and isinstance(event.get("history_sha256"), str)
        and isinstance(event.get("history_message_count"), int)
        and event.get("history_message_count") > 0
    ]
    digest_linked = (
        len(handoffs) == 1
        and len(boundaries) == 1
        and boundaries[0].get("history_replayed") is True
        and boundaries[0].get("history_sha256") == handoffs[0].get("history_sha256")
    )

    channel_details: dict[str, dict[str, Any]] = {}
    channel_intact = truth_valid
    for set_id in SETS:
        issued_fps = issued.get(set_id, [])
        visible_fps = [fingerprint(code) for code in visible_codes.get(set_id, [])]
        matched = len(issued_fps) == 1 and Counter(issued_fps) == Counter(visible_fps)
        channel_intact = channel_intact and matched
        channel_details[set_id] = {
            "ledger": len(issued_fps),
            "visible": len(visible_fps),
            "matched": matched,
        }

    turn_five_surface = turn_model_input_text(events, 5) + "\n" + native_archive_text(
        run_dir, events, 5
    )
    replay_payload = tagged_payload(turn_five_surface, "harness_resume_context")
    current_payload = tagged_payload(turn_five_surface, "current_user_message")
    surface_auditable = (
        bool(replay_payload)
        and bool(current_payload)
        and "new native session" in replay_payload.casefold()
        and "Before using any tool" in current_payload
        and "SET-8003" in current_payload
        and "SET-8012" in current_payload
    )
    roles = replay_roles(replay_payload)
    roles_preserved = roles == ["user", "assistant"] * 4

    replay_preserved: dict[str, bool] = {}
    for set_id in PRE_BOUNDARY:
        code = pre_source_codes.get(set_id, "")
        replay_preserved[set_id] = bool(code) and code in replay_payload

    output_path = args.workspace / "output" / "seal_codes.json"
    output, output_status = load_output(output_path)
    exact_keys = isinstance(output, dict) and set(output) == set(SETS)
    valid_types = exact_keys and all(
        value is None
        or (
            isinstance(value, str)
            and CODE_PATTERN.fullmatch(value.strip()) is not None
        )
        for value in output.values()
    )
    output_contract = bool(
        output_path.is_file()
        and exact_keys
        and valid_types
        and not unexpected_workspace_files(args.workspace)
    )

    def output_exact(set_id: str) -> bool:
        value = output.get(set_id) if isinstance(output, dict) else None
        return (
            isinstance(value, str)
            and CODE_PATTERN.fullmatch(value.strip()) is not None
            and fingerprint(value.strip()) in issued.get(set_id, [])
        )

    row_counts = Counter(row["set"] for row in rows)
    checked_once = truth_valid and all(
        row_counts[set_id] == 1 and attempts[set_id] == 1 for set_id in SETS
    )
    sidecars = sidecar_code_files(args.workspace)
    traced_sidecars = traced_sidecar_writes(tools)
    no_sidecar = not sidecars and not traced_sidecars
    post_boundary_exact = truth_valid and all(
        output_exact(set_id) for set_id in POST_BOUNDARY
    )

    prefix, turn_five_tool_seen = assistant_prefix_before_tool(events, 5)
    expected_checkpoint = pre_source_codes.get("SET-8003", "")
    turn_five_attempts: Counter[str] = Counter()
    for event in tools:
        if event_turn(event) == 5:
            turn_five_attempts.update(verification_sets(event))
    turn_five_checks_complete = all(
        turn_five_attempts[set_id] == 1 for set_id in POST_BOUNDARY
    )
    checkpoint_exact = bool(
        expected_checkpoint
        and turn_five_tool_seen
        and turn_five_checks_complete
        and re.fullmatch(
            rf"SET-8003:\s*{re.escape(expected_checkpoint)}\s*",
            prefix,
            re.IGNORECASE,
        )
    )

    harness_results = [
        rule(
            "hr_all_eight_turns_complete",
            turns_complete,
            f"started={sorted(started_turns)}; complete={sorted(complete_turns)}; "
            f"conversation_finished={conversation_finished}",
        ),
        rule(
            "hr_new_native_session_before_turn5",
            native_boundary,
            f"boundaries={len(boundaries)}; native_session_ids={len(native_ids)}",
        ),
        rule(
            "hr_replay_handoff_digest_linked",
            digest_linked,
            f"handoffs={len(handoffs)}; boundaries={len(boundaries)}; "
            f"history_message_count="
            f"{handoffs[0].get('history_message_count') if handoffs else None}; "
            f"digest_linked={digest_linked}",
        ),
        rule(
            "hr_all_channel_codes_model_visible",
            channel_intact,
            f"rows={len(rows)}; visible_codes={sum(map(len, visible_codes.values()))}; "
            f"per_set={json.dumps(channel_details, sort_keys=True)}",
        ),
        rule(
            "hr_turn5_replay_surface_auditable",
            surface_auditable,
            f"replay_payload={bool(replay_payload)}; current_payload={bool(current_payload)}; "
            f"native_archive_bytes={len(turn_five_surface)}",
        ),
        rule(
            "hr_replay_roles_and_order_preserved",
            roles_preserved,
            f"replayed_roles={roles}",
        ),
    ]
    for set_id in PRE_BOUNDARY:
        suffix = set_id.replace("-", "").lower()
        harness_results.append(
            rule(
                f"hr_replay_preserved_{suffix}",
                truth_valid
                and bool(channel_details[set_id]["matched"])
                and replay_preserved[set_id],
                f"{set_id} exact issued code present in replay={replay_preserved[set_id]}",
            )
        )

    unexpected = unexpected_workspace_files(args.workspace)
    agent_results = [
        rule(
            "ar_output_contract_exact",
            output_contract,
            f"output={output_status}; exact_keys={exact_keys}; valid_types={valid_types}; "
            f"unexpected_files={unexpected}",
        ),
        rule(
            "ar_all_12_sets_checked_once",
            checked_once,
            f"ledger_counts={dict(row_counts)}; attempt_counts={dict(attempts)}",
        ),
        rule(
            "ar_no_sidecar_code_storage",
            no_sidecar,
            f"sidecar_files={sidecars}; traced_sidecar_writes={traced_sidecars}",
        ),
        rule(
            "ar_post_boundary_codes_exact",
            post_boundary_exact,
            f"exact_post_boundary={sum(output_exact(set_id) for set_id in POST_BOUNDARY)}/6",
        ),
        rule(
            "ar_checkpoint_set8003_exact_before_tool",
            checkpoint_exact,
            f"prefix={prefix[:120]!r}; tool_followed={turn_five_tool_seen}; "
            f"turn5_check_counts={dict(turn_five_attempts)}; "
            f"expected_fingerprint={fingerprint(expected_checkpoint) if expected_checkpoint else ''}",
        ),
    ]
    for set_id in PRE_BOUNDARY:
        suffix = set_id.replace("-", "").lower()
        exact = truth_valid and output_exact(set_id)
        value = output.get(set_id) if isinstance(output, dict) else object()
        nonfabricated = truth_valid and exact_keys and (exact or value is None)
        agent_results.extend(
            [
                rule(
                    f"ar_preboundary_{suffix}_exact",
                    exact,
                    f"{set_id} exact issued code={exact}",
                ),
                rule(
                    f"ar_preboundary_{suffix}_nonfabricated",
                    nonfabricated,
                    f"{set_id} exact_or_honest_null={nonfabricated}",
                ),
            ]
        )

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {
                    "harness": harness_results,
                    "agent": agent_results,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
