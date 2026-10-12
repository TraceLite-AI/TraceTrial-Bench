from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from harness_tracer.writer import EventWriter


MAX_NATIVE_SESSION_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class NativeSessionArchive:
    status: str
    records: tuple[dict[str, Any], ...] = ()
    path: Path | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CodexChildSession:
    session_id: str
    parent_session_id: str
    agent_path: str | None = None
    source_path: Path | None = None

    @property
    def principal_id(self) -> str:
        if self.session_id != self.parent_session_id:
            return self.session_id
        discriminator = self.agent_path or (
            self.source_path.stem if self.source_path is not None else "child"
        )
        return f"{self.session_id}:{discriminator}"


def archive_claudecode_session(
    *,
    allowed_state_dir: str | None,
    session_id: str | None,
    run_dir: Path,
) -> NativeSessionArchive:
    return _archive_matching_jsonl(
        harness="claudecode",
        allowed_state_dir=allowed_state_dir,
        session_id=session_id,
        run_dir=run_dir,
        destination_name="claudecode-session.trace.jsonl",
        candidates=(f"**/{session_id}.jsonl" if session_id else "",),
    )


def archive_codex_session(
    *,
    allowed_state_dir: str | None,
    session_id: str | None,
    run_dir: Path,
    destination_name: str = "codex-session.trace.jsonl",
    prefer_latest: bool = False,
    exclude_subagents: bool = False,
) -> NativeSessionArchive:
    return _archive_matching_jsonl(
        harness="codex",
        allowed_state_dir=allowed_state_dir,
        session_id=session_id,
        run_dir=run_dir,
        destination_name=destination_name,
        candidates=("sessions/**/*.jsonl",),
        prefer_latest=prefer_latest,
        exclude_codex_subagents=exclude_subagents,
    )


def archive_codex_child_session(
    *,
    child: CodexChildSession,
    allowed_state_dir: str | None,
    run_dir: Path,
    destination_name: str,
) -> NativeSessionArchive:
    if not allowed_state_dir or child.source_path is None:
        return NativeSessionArchive(status="unavailable", reason="state_dir_unavailable")
    try:
        state_root = Path(allowed_state_dir).resolve(strict=True)
        source = child.source_path.resolve(strict=True)
        source.relative_to(state_root)
        if source.is_symlink() or not source.is_file():
            raise OSError("invalid child rollout")
        size = source.stat().st_size
        if size <= 0 or size > MAX_NATIVE_SESSION_BYTES:
            return NativeSessionArchive(
                status="unavailable", reason="session_file_too_large"
            )
        records = _read_jsonl(source)
    except (OSError, UnicodeError, ValueError):
        return NativeSessionArchive(status="unavailable", reason="session_file_not_found")
    binding = _codex_child_binding(records, source_path=source)
    if binding is None or binding != child:
        return NativeSessionArchive(status="unavailable", reason="session_binding_changed")
    destination_dir = run_dir / "native_sessions"
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    destination = destination_dir / destination_name
    shutil.copyfile(source, destination)
    payload = destination.read_bytes()
    return NativeSessionArchive(
        status="available",
        records=records,
        path=destination,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def discover_codex_child_sessions(
    *,
    allowed_state_dir: str | None,
    parent_session_id: str | None,
) -> tuple[CodexChildSession, ...]:
    """Return direct native children bound to one Codex parent rollout.

    Codex exec JSON can omit ``spawn_agent`` even though the native child
    rollout is persisted. The child ``session_meta`` carries a typed
    ``thread_spawn.parent_thread_id`` binding, which is stronger than matching
    prose, paths, or task names.
    """

    if not allowed_state_dir or not parent_session_id:
        return ()
    try:
        state_root = Path(allowed_state_dir).resolve(strict=True)
    except OSError:
        return ()
    children: dict[str, CodexChildSession] = {}
    for candidate in state_root.glob("sessions/**/*.jsonl"):
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(state_root)
            if not resolved.is_file():
                continue
            size = resolved.stat().st_size
            if size <= 0 or size > MAX_NATIVE_SESSION_BYTES:
                continue
            records = _read_jsonl(resolved)
        except (OSError, UnicodeError, ValueError):
            continue
        binding = _codex_child_binding(records, source_path=resolved)
        if binding is None or binding.parent_session_id != parent_session_id:
            continue
        children[str(resolved)] = binding
    return tuple(children[key] for key in sorted(children))


def archive_records(
    *,
    harness: str,
    session_id: str | None,
    records: Iterable[dict[str, Any]],
    run_dir: Path,
    destination_name: str,
) -> NativeSessionArchive:
    materialized = tuple(records)
    if not materialized:
        return NativeSessionArchive(status="unavailable", reason="session_records_empty")
    destination_dir = run_dir / "native_sessions"
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    destination = destination_dir / destination_name
    payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in materialized
    ).encode("utf-8")
    if len(payload) > MAX_NATIVE_SESSION_BYTES:
        return NativeSessionArchive(status="unavailable", reason="session_file_too_large")
    destination.write_bytes(payload)
    return NativeSessionArchive(
        status="available",
        records=materialized,
        path=destination,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def write_archive_event(
    writer: EventWriter,
    *,
    harness: str,
    session_id: str | None,
    archive: NativeSessionArchive,
) -> None:
    writer.write(
        "native_session_archive",
        source=f"{harness}.native_session",
        status=archive.status,
        session_id=session_id,
        format="jsonl",
        artifact=(
            archive.path.relative_to(archive.path.parents[1]).as_posix()
            if archive.path is not None
            else None
        ),
        sha256=archive.sha256,
        size_bytes=archive.size_bytes,
        reason=archive.reason,
        redacted=True,
    )


def emit_context_compaction(
    writer: EventWriter,
    *,
    harness: str,
    source: str,
    native_event_type: str,
    record: dict[str, Any],
    session_id: str | None,
    summary: str | None,
    summary_artifact: str | None,
    seen: set[str],
    notice_visible_to_model: bool | None = None,
) -> bool:
    native_event_id = _first_string(
        record.get("id"),
        record.get("uuid"),
        record.get("event_id"),
        (record.get("payload") or {}).get("id")
        if isinstance(record.get("payload"), dict)
        else None,
    )
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    native_event_key = (
        f"{harness}:{session_id}:{native_event_id}"
        if native_event_id
        else f"{harness}:{session_id}:sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    )
    if native_event_key in seen:
        return False
    seen.add(native_event_key)
    writer.write(
        "context_compaction",
        source=source,
        session_id=session_id,
        native_event_type=native_event_type,
        native_event_id=native_event_id,
        native_event_key=native_event_key,
        trigger=_compaction_trigger(record),
        source_timestamp=_first_string(
            record.get("timestamp"),
            record.get("created_at"),
            record.get("createdAt"),
        ),
        summary=summary,
        summary_present=bool(summary),
        summary_artifact=summary_artifact,
        notice_visible_to_model=notice_visible_to_model,
        raw_event=record,
    )
    return True


def extract_summary(record: dict[str, Any]) -> str | None:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    compact_metadata = (
        record.get("compactMetadata")
        if isinstance(record.get("compactMetadata"), dict)
        else {}
    )
    for value in (
        record.get("summary"),
        payload.get("summary"),
        compact_metadata.get("summary"),
        record.get("content"),
        payload.get("content"),
    ):
        text = _content_text(value)
        if text:
            return text
    message = record.get("message")
    if isinstance(message, dict):
        text = _content_text(message.get("content"))
        if text:
            return text
    return None


def _archive_matching_jsonl(
    *,
    harness: str,
    allowed_state_dir: str | None,
    session_id: str | None,
    run_dir: Path,
    destination_name: str,
    candidates: tuple[str, ...],
    prefer_latest: bool = False,
    exclude_codex_subagents: bool = False,
) -> NativeSessionArchive:
    if not allowed_state_dir:
        return NativeSessionArchive(status="unavailable", reason="state_dir_unavailable")
    if not session_id:
        return NativeSessionArchive(status="unavailable", reason="session_id_unavailable")
    try:
        state_root = Path(allowed_state_dir).resolve(strict=True)
    except OSError:
        return NativeSessionArchive(status="unavailable", reason="state_dir_unavailable")
    matches: list[tuple[Path, tuple[dict[str, Any], ...], int]] = []
    visited: set[Path] = set()
    for pattern in candidates:
        if not pattern:
            continue
        for candidate in state_root.glob(pattern):
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(state_root)
                if resolved in visited or not resolved.is_file():
                    continue
                visited.add(resolved)
                stat = resolved.stat()
                size = stat.st_size
                if size <= 0 or size > MAX_NATIVE_SESSION_BYTES:
                    continue
                records = _read_jsonl(resolved)
            except (OSError, UnicodeError, ValueError):
                continue
            if (
                records
                and not (
                    harness == "codex"
                    and exclude_codex_subagents
                    and _codex_child_binding(records, source_path=resolved) is not None
                )
                and (
                (harness == "codex" and _codex_session_id(records) == session_id)
                or (
                    harness != "codex"
                    and (
                        _contains_exact_value(records, session_id)
                        or (harness == "claudecode" and resolved.stem == session_id)
                    )
                )
                )
            ):
                matches.append((resolved, records, stat.st_mtime_ns))
    if len(matches) > 1 and prefer_latest:
        matches = [max(matches, key=lambda match: (match[2], str(match[0])))]
    if len(matches) != 1:
        return NativeSessionArchive(
            status="unavailable",
            reason=("session_file_not_found" if not matches else "session_file_ambiguous"),
        )
    source, records, _mtime_ns = matches[0]
    destination_dir = run_dir / "native_sessions"
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    destination = destination_dir / destination_name
    shutil.copyfile(source, destination)
    payload = destination.read_bytes()
    return NativeSessionArchive(
        status="available",
        records=records,
        path=destination,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("native session record must be an object")
        records.append(value)
    return tuple(records)


def _contains_exact_value(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_value(item, expected) for item in value)
    return False


def _codex_session_id(records: tuple[dict[str, Any], ...]) -> str | None:
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "session_meta" or not isinstance(payload, dict):
            continue
        for key in ("session_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _codex_child_binding(
    records: tuple[dict[str, Any], ...],
    *,
    source_path: Path | None = None,
) -> CodexChildSession | None:
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "session_meta" or not isinstance(payload, dict):
            continue
        session_id = _first_string(payload.get("session_id"), payload.get("id"))
        source = payload.get("source")
        subagent = source.get("subagent") if isinstance(source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if not isinstance(spawn, dict):
            continue
        parent_session_id = _first_string(spawn.get("parent_thread_id"))
        if session_id and parent_session_id:
            return CodexChildSession(
                session_id=session_id,
                parent_session_id=parent_session_id,
                agent_path=_first_string(spawn.get("agent_path")),
                source_path=source_path,
            )
    return None


def _content_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    joined = "\n".join(part for part in parts if part.strip())
    return joined or None


def _compaction_trigger(record: dict[str, Any]) -> str:
    values = [
        record.get("trigger"),
        record.get("reason"),
        (record.get("payload") or {}).get("reason")
        if isinstance(record.get("payload"), dict)
        else None,
    ]
    text = " ".join(str(value).lower() for value in values if value is not None)
    if "manual" in text or "command" in text:
        return "manual"
    if "overflow" in text or "limit" in text:
        return "overflow"
    if "auto" in text or "threshold" in text:
        return "auto"
    return "unknown"


def _first_string(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value), None)
