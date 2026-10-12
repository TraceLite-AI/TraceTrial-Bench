from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from harness_tracer import SCHEMA_VERSION
from harness_tracer.timeutils import utc_now_iso


class EventWriter:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        redact_values: tuple[str, ...] = (),
        flush_interval: int = 1,
    ) -> None:
        if flush_interval < 1:
            raise ValueError("event writer flush_interval must be positive")
        self.path = Path(path)
        self.run_id = run_id
        self.seq = 0
        self.events: list[dict[str, Any]] = []
        self.flush_interval = flush_interval
        self.redact_values = tuple(
            sorted(
                {value for value in redact_values if isinstance(value, str) and value},
                key=len,
                reverse=True,
            )
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False

    def write(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.seq += 1
        event = {
            "schema_version": SCHEMA_VERSION,
            "seq": self.seq,
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "event_type": event_type,
        }
        event.update(_redact_value(payload, self.redact_values))
        self.events.append(event)
        self._checkpoint()
        return event

    def write_many(
        self, values: Iterable[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Append a batch while flushing once, preserving normal event shape."""
        appended: list[dict[str, Any]] = []
        for event_type, payload in values:
            self.seq += 1
            event = {
                "schema_version": SCHEMA_VERSION,
                "seq": self.seq,
                "timestamp": utc_now_iso(),
                "run_id": self.run_id,
                "event_type": event_type,
            }
            event.update(_redact_value(payload, self.redact_values))
            self.events.append(event)
            appended.append(event)
        if appended:
            self._flush()
        return appended

    def insert_many(
        self,
        index: int,
        values: Iterable[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Insert normalized events and resequence the trace contiguously."""

        if index < 0 or index > len(self.events):
            raise IndexError("event insertion index is out of range")
        inserted = [
            {
                "schema_version": SCHEMA_VERSION,
                "seq": 0,
                "timestamp": utc_now_iso(),
                "run_id": self.run_id,
                "event_type": event_type,
                **_redact_value(payload, self.redact_values),
            }
            for event_type, payload in values
        ]
        if not inserted:
            return []
        self.events[index:index] = inserted
        for sequence, event in enumerate(self.events, start=1):
            event["seq"] = sequence
        self.seq = len(self.events)
        self._flush()
        return inserted

    def close(self) -> None:
        if not self._closed:
            self._flush()
            self._closed = True

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _checkpoint(self) -> None:
        if self.seq % self.flush_interval == 0:
            self._flush()

    def __enter__(self) -> "EventWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def _redact_value(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "<redacted>")
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, secrets) for item in value)
    if isinstance(value, dict):
        return {
            _redact_value(key, secrets) if isinstance(key, str) else key: _redact_value(
                item, secrets
            )
            for key, item in value.items()
        }
    return value
