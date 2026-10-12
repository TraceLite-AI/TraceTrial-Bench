from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


@dataclass
class AdapterResult:
    status: str = "finished"
    trace_complete: bool = True
    structured_trace_status: str = "unavailable"
    error_type: str | None = None
    error_message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class HarnessAdapter(Protocol):
    name: str

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        ...

    def consume(
        self,
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        run_dir: Path,
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        ...
