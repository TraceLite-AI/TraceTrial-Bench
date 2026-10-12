from __future__ import annotations

from pathlib import Path

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


class GenericProcessAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        if self.name == "fake":
            return ["fake", "--model", model, "--message", input_query]
        executable = harness_path or self.name
        command = [executable]
        if self.name == "opencode":
            command += [
                "run",
                input_query,
                "--dir",
                ".",
                "--format",
                "json",
                "-m",
                model,
                "--dangerously-skip-permissions",
            ]
        elif self.name == "nanobot":
            command += ["agent", "--message", input_query]
        else:
            command += ["--model", model, "--message", input_query]
        for key, value in sorted(extra_args.items()):
            command.extend([f"--{key}", value])
        return command

    def consume(
        self,
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        run_dir: Path,
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        if self.name == "fake":
            prompt = _extract_arg(process.args, "--message") or ""
            writer.write("assistant_message", content=f"fake response to: {prompt}", message_id="fake-message")
            return AdapterResult(structured_trace_status="available")

        for index, line in enumerate(_split_lines(process.stdout), start=1):
            writer.write(
                "raw_stdout",
                text=line,
                stream_seq=index,
                parseable=False,
                parse_error=None,
            )
        for index, line in enumerate(_split_lines(process.stderr), start=1):
            writer.write(
                "raw_stderr",
                text=line,
                stream_seq=index,
                severity="error" if process.returncode else "info",
            )
        return AdapterResult(structured_trace_status="unavailable")


def _split_lines(value: str) -> list[str]:
    return value.splitlines() if value else []


def _extract_arg(args: list[str], flag: str) -> str | None:
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return None
