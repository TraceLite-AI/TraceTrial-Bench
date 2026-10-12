from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from harnessbench.score_config import EvidenceSelector


MAX_ARTIFACT_BYTES = 1_000_000
MAX_ARTIFACT_FILES = 5_000
MAX_ARTIFACT_SNIPPET_CHARS = 4_000
MAX_EVENT_STRING_CHARS = 20_000
MAX_EVENT_COLLECTION_CHARS = 30_000
EVENT_BOUNDARY_CHARS = 3_000
EVENT_HIT_CONTEXT_CHARS = 900
MAX_EVENT_HIT_WINDOWS = 8
EVENT_REDUCTION_MARKERS = (
    "truncat",
    "omitted",
    "output too large",
    "persisted-output",
    "full output saved",
)


@dataclass(frozen=True)
class EvidenceMatch:
    seq: int | None
    event_type: str
    timestamp: str | None
    source: str | None
    matched_keywords: tuple[str, ...]
    matched_fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "matched_keywords": list(self.matched_keywords),
            "matched_fields": self.matched_fields,
        }


@dataclass(frozen=True)
class ArtifactMatch:
    path: str
    size: int
    matched_keywords: tuple[str, ...]
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "matched_keywords": list(self.matched_keywords),
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class EvidenceResult:
    focus: str
    events_path: str
    scanned_events: int
    matched_event_count: int
    returned_event_count: int
    scanned_artifact_files: int
    matched_artifact_count: int
    returned_artifact_count: int
    truncated: bool
    matches: tuple[EvidenceMatch, ...]
    artifact_matches: tuple[ArtifactMatch, ...]

    @property
    def returned_evidence_count(self) -> int:
        return self.returned_event_count + self.returned_artifact_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "focus": self.focus,
            "events_path": self.events_path,
            "scanned_events": self.scanned_events,
            "matched_event_count": self.matched_event_count,
            "returned_event_count": self.returned_event_count,
            "scanned_artifact_files": self.scanned_artifact_files,
            "matched_artifact_count": self.matched_artifact_count,
            "returned_artifact_count": self.returned_artifact_count,
            "returned_evidence_count": self.returned_evidence_count,
            "truncated": self.truncated,
            "trajectory_matches": [match.to_dict() for match in self.matches],
            "artifact_matches": [match.to_dict() for match in self.artifact_matches],
        }


class EvidenceFinder:
    """Select trace and changed-artifact evidence with literal matching only."""

    def __init__(
        self,
        *,
        run_dir: Path,
        trace_summary: Mapping[str, Any],
        workspace_dir: Path | None = None,
        baseline_dir: Path | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.workspace_dir = workspace_dir.resolve() if workspace_dir is not None else None
        self.baseline_dir = baseline_dir.resolve() if baseline_dir is not None else None
        self.events_path = self._resolve_events_path(trace_summary)
        try:
            payload = json.loads(self.events_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"trace events are unavailable or invalid: {exc}") from exc
        if not isinstance(payload, list) or any(
            not isinstance(event, dict) for event in payload
        ):
            raise ValueError("trace events must be an array of objects")
        supplemental = [
            *self._execution_probe_events(trace_summary),
            *self._security_probe_events(trace_summary),
        ]
        self.events: tuple[dict[str, Any], ...] = tuple([*payload, *supplemental])

    def _execution_probe_events(
        self,
        trace_summary: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw = trace_summary.get("execution_probe_evidence")
        if not isinstance(raw, str) or not raw:
            return []
        configured = Path(raw)
        candidate = configured if configured.is_absolute() else self.run_dir / configured
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to((self.run_dir / "artifacts").resolve(strict=True))
            evidence = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return []
        observations = evidence.get("post_tool_observations") if isinstance(evidence, dict) else None
        if not isinstance(observations, list):
            return []
        events: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            events.append(
                {
                    "event_type": "tool",
                    "source": "harnessbench.execution_probe",
                    "tool_call": {
                        "tool_call_id": observation.get("tool_call_id"),
                        "tool_name": observation.get("tool_name"),
                        "arguments": observation.get("arguments", {}),
                    },
                    "tool_result": {
                        "native": observation.get("tool_result"),
                        "normalized_post_tool": observation,
                    },
                    "raw_event": {"tool_result": observation},
                }
            )
        return events

    def _security_probe_events(
        self,
        trace_summary: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw = trace_summary.get("security_probe_evidence")
        if not isinstance(raw, str) or not raw:
            return []
        configured = Path(raw)
        candidate = configured if configured.is_absolute() else self.run_dir / configured
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to((self.run_dir / "artifacts").resolve(strict=True))
            evidence = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return []
        if not isinstance(evidence, dict):
            return []
        events: list[dict[str, Any]] = []
        for key in ("approval_observations", "path_observations"):
            observations = evidence.get(key)
            if not isinstance(observations, list):
                continue
            for observation in observations:
                if not isinstance(observation, dict):
                    continue
                events.append(
                    {
                        "event_type": "tool",
                        "source": "harnessbench.security_probe",
                        "tool_call": {
                            "tool_call_id": observation.get("tool_call_id"),
                            "tool_name": observation.get("tool_name"),
                            "arguments": observation.get("arguments", {}),
                        },
                        "tool_result": {
                            "native": observation.get("tool_result"),
                            "normalized_security": observation,
                        },
                        "raw_event": {"security_observation": observation},
                    }
                )
        return events

    def find(
        self,
        selector: EvidenceSelector,
        *,
        focus: str = "trajectory",
    ) -> EvidenceResult:
        if focus not in {"trajectory", "artifacts"}:
            raise ValueError("evidence focus must be trajectory or artifacts")
        artifact_matches: tuple[ArtifactMatch, ...] = ()
        scanned_artifacts = 0
        matched_artifacts = 0
        event_limit = selector.max_events
        if focus == "artifacts":
            artifact_matches, scanned_artifacts, matched_artifacts = self._find_artifacts(
                selector
            )
            event_limit = max(0, selector.max_events - len(artifact_matches))
        event_matches, matched_events = self._find_events(selector, limit=event_limit)
        total_matches = matched_artifacts + matched_events
        total_returned = len(artifact_matches) + len(event_matches)
        return EvidenceResult(
            focus=focus,
            events_path=str(self.events_path),
            scanned_events=len(self.events),
            matched_event_count=matched_events,
            returned_event_count=len(event_matches),
            scanned_artifact_files=scanned_artifacts,
            matched_artifact_count=matched_artifacts,
            returned_artifact_count=len(artifact_matches),
            truncated=total_matches > total_returned,
            matches=event_matches,
            artifact_matches=artifact_matches,
        )

    def _find_events(
        self,
        selector: EvidenceSelector,
        *,
        limit: int,
    ) -> tuple[tuple[EvidenceMatch, ...], int]:
        matches: list[EvidenceMatch] = []
        matched_count = 0
        allowed_types = set(selector.event_types)
        needles = (
            selector.keywords
            if selector.case_sensitive
            else tuple(keyword.casefold() for keyword in selector.keywords)
        )
        for event in self.events:
            event_type = event.get("event_type")
            if not isinstance(event_type, str) or event_type not in allowed_types:
                continue
            raw_selected: dict[str, Any] = {}
            searchable_parts: list[str] = []
            for field in selector.fields:
                found = _read_field(event, field)
                if found is _MISSING:
                    continue
                raw_selected[field] = found
                searchable_parts.extend(_string_leaves(found))
            haystack = "\n".join(searchable_parts)
            compared = haystack if selector.case_sensitive else haystack.casefold()
            hits = tuple(
                original
                for original, needle in zip(selector.keywords, needles)
                if needle in compared
            )
            matched = bool(hits) if selector.match == "any" else len(hits) == len(needles)
            if not matched:
                continue
            selected = {
                field: _compact_event_evidence(
                    value,
                    keywords=hits,
                    case_sensitive=selector.case_sensitive,
                )
                for field, value in raw_selected.items()
            }
            matched_count += 1
            if len(matches) >= limit:
                continue
            seq = event.get("seq")
            timestamp = event.get("timestamp")
            source = event.get("source")
            matches.append(
                EvidenceMatch(
                    seq=(seq if isinstance(seq, int) and not isinstance(seq, bool) else None),
                    event_type=event_type,
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                    source=source if isinstance(source, str) else None,
                    matched_keywords=hits,
                    matched_fields=selected,
                )
            )
        return tuple(matches), matched_count

    def _find_artifacts(
        self,
        selector: EvidenceSelector,
    ) -> tuple[tuple[ArtifactMatch, ...], int, int]:
        if self.workspace_dir is None or self.baseline_dir is None:
            return (), 0, 0
        if (
            self.workspace_dir.is_symlink()
            or not self.workspace_dir.is_dir()
            or self.baseline_dir.is_symlink()
            or not self.baseline_dir.is_dir()
        ):
            raise ValueError("artifact evidence requires valid workspace and baseline directories")

        needles = (
            selector.keywords
            if selector.case_sensitive
            else tuple(keyword.casefold() for keyword in selector.keywords)
        )
        matches: list[ArtifactMatch] = []
        scanned = 0
        matched = 0
        for path in sorted(self.workspace_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if scanned >= MAX_ARTIFACT_FILES:
                break
            scanned += 1
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(self.workspace_dir)
            except (OSError, ValueError):
                continue
            if not self._changed_from_baseline(relative, resolved):
                continue
            try:
                size = resolved.stat().st_size
                if size > MAX_ARTIFACT_BYTES:
                    continue
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            searchable = f"{relative.as_posix()}\n{content}"
            compared = searchable if selector.case_sensitive else searchable.casefold()
            hits = tuple(
                original
                for original, needle in zip(selector.keywords, needles)
                if needle in compared
            )
            selected = bool(hits) if selector.match == "any" else len(hits) == len(needles)
            if not selected:
                continue
            matched += 1
            if len(matches) >= selector.max_events:
                continue
            matches.append(
                ArtifactMatch(
                    path=relative.as_posix(),
                    size=size,
                    matched_keywords=hits,
                    snippet=_artifact_snippet(content, selector.keywords, selector.case_sensitive),
                )
            )
        return tuple(matches), scanned, matched

    def _changed_from_baseline(self, relative: Path, artifact: Path) -> bool:
        assert self.baseline_dir is not None
        baseline = self.baseline_dir / relative
        if baseline.is_symlink() or not baseline.is_file():
            return True
        try:
            artifact_size = artifact.stat().st_size
            baseline_size = baseline.stat().st_size
            if artifact_size != baseline_size:
                return True
            if artifact_size > MAX_ARTIFACT_BYTES:
                return True
            return artifact.read_bytes() != baseline.read_bytes()
        except OSError:
            return True

    def _resolve_events_path(self, trace_summary: Mapping[str, Any]) -> Path:
        raw = trace_summary.get("events_path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("trace summary has no events_path")
        configured = Path(raw)
        candidates = (
            (configured,)
            if configured.is_absolute()
            else (
                self.run_dir / configured,
                self.run_dir / "artifacts" / configured,
                self.run_dir / "artifacts" / "container" / configured,
            )
        )
        artifacts = (self.run_dir / "artifacts").resolve(strict=True)
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(artifacts)
            except (OSError, ValueError):
                continue
            if not resolved.is_symlink() and resolved.is_file():
                return resolved
        raise ValueError("trace events must be a regular file inside run artifacts")


class _Missing:
    pass


_MISSING = _Missing()


def _read_field(value: Any, dotted_path: str) -> Any:
    current = value
    for part in dotted_path.split("."):
        if not part:
            return _MISSING
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if value is None or isinstance(value, (int, float, bool)):
        return [json.dumps(value, ensure_ascii=False)]
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            parts.append(str(key))
            parts.extend(_string_leaves(item))
        return parts
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(_string_leaves(item))
        return parts
    return [str(value)]


def _compact_event_evidence(
    value: Any,
    *,
    keywords: tuple[str, ...],
    case_sensitive: bool,
) -> Any:
    """Bound large event fields while retaining boundaries and literal-hit context."""

    if isinstance(value, str):
        return _compact_event_string(
            value,
            keywords=keywords,
            case_sensitive=case_sensitive,
        )
    if isinstance(value, dict):
        return {
            key: _compact_event_evidence(
                child,
                keywords=keywords,
                case_sensitive=case_sensitive,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        compacted = [
            _compact_event_evidence(
                child,
                keywords=keywords,
                case_sensitive=case_sensitive,
            )
            for child in value
        ]
        serialized = json.dumps(
            compacted,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if len(serialized) <= MAX_EVENT_COLLECTION_CHARS:
            return compacted

        # Large provider tool catalogs can otherwise consume the whole Judge
        # context even when a rubric names only two or three relevant
        # declarations. Keep complete matching list entries (including their
        # descriptions and schemas), cover every keyword that occurs in the
        # collection first, and then add further matches while the budget
        # permits. Small nested schema lists are returned unchanged above.
        needles = (
            keywords
            if case_sensitive
            else tuple(keyword.casefold() for keyword in keywords)
        )
        candidates: list[tuple[int, Any, set[int], int]] = []
        for index, (raw_child, compacted_child) in enumerate(zip(value, compacted)):
            raw_text = "\n".join(_string_leaves(raw_child))
            compared = raw_text if case_sensitive else raw_text.casefold()
            hits = {
                keyword_index
                for keyword_index, needle in enumerate(needles)
                if needle and needle in compared
            }
            if not hits:
                continue
            size = len(
                json.dumps(
                    compacted_child,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
            candidates.append((index, compacted_child, hits, size))

        target_hits = set().union(*(item[2] for item in candidates)) if candidates else set()
        uncovered = set(target_hits)
        selected: list[tuple[int, Any, set[int], int]] = []
        remaining = list(candidates)
        used = 0
        while uncovered and remaining:
            best = max(
                remaining,
                key=lambda item: (len(item[2] & uncovered), -item[3], -item[0]),
            )
            remaining.remove(best)
            selected.append(best)
            used += best[3]
            uncovered -= best[2]
        for candidate in remaining:
            if selected and used + candidate[3] > MAX_EVENT_COLLECTION_CHARS:
                continue
            selected.append(candidate)
            used += candidate[3]
        selected.sort(key=lambda item: item[0])
        reduced = [item[1] for item in selected]
        reduced.append(
            {
                "_evidence_reduction": {
                    "original_items": len(value),
                    "returned_matching_items": len(selected),
                    "selection": "complete list entries containing rubric keywords",
                }
            }
        )
        return reduced
    return value


def _compact_event_string(
    value: str,
    *,
    keywords: tuple[str, ...],
    case_sensitive: bool,
) -> str:
    if len(value) <= MAX_EVENT_STRING_CHARS:
        return value
    compared = value if case_sensitive else value.casefold()
    marker_keywords = (*keywords, *EVENT_REDUCTION_MARKERS)
    needles = (
        marker_keywords
        if case_sensitive
        else tuple(item.casefold() for item in marker_keywords)
    )
    windows: list[tuple[int, int]] = [
        (0, min(len(value), EVENT_BOUNDARY_CHARS)),
        (max(0, len(value) - EVENT_BOUNDARY_CHARS), len(value)),
    ]
    hit_windows = 0
    for needle in needles:
        if not needle:
            continue
        offset = 0
        while hit_windows < MAX_EVENT_HIT_WINDOWS:
            found = compared.find(needle, offset)
            if found < 0:
                break
            windows.append(
                (
                    max(0, found - EVENT_HIT_CONTEXT_CHARS),
                    min(
                        len(value),
                        found + len(needle) + EVENT_HIT_CONTEXT_CHARS,
                    ),
                )
            )
            hit_windows += 1
            offset = found + max(1, len(needle))
        if hit_windows >= MAX_EVENT_HIT_WINDOWS:
            break
    windows.sort()
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    for start, end in merged:
        if parts:
            parts.append(
                f"\n[... evidence chars {start}-{end} of {len(value)} ...]\n"
            )
        parts.append(value[start:end])
    compacted = "".join(parts)
    return compacted[:MAX_EVENT_STRING_CHARS]


def _artifact_snippet(
    content: str,
    keywords: tuple[str, ...],
    case_sensitive: bool,
) -> str:
    """Return bounded context around literal hits without exposing whole files."""

    if len(content) <= MAX_ARTIFACT_SNIPPET_CHARS:
        return content
    compared = content if case_sensitive else content.casefold()
    needles = keywords if case_sensitive else tuple(value.casefold() for value in keywords)
    windows: list[tuple[int, int]] = []
    for needle in needles:
        offset = compared.find(needle)
        if offset < 0:
            continue
        windows.append((max(0, offset - 300), min(len(content), offset + len(needle) + 700)))
    if not windows:
        return content[:MAX_ARTIFACT_SNIPPET_CHARS]
    windows.sort()
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    snippet = "\n...\n".join(content[start:end] for start, end in merged)
    return snippet[:MAX_ARTIFACT_SNIPPET_CHARS]
