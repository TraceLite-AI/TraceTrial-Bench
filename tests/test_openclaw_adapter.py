from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_tracer.adapters.openclaw import OpenClawAdapter
from harness_tracer.process import CompletedProcessData
from harness_tracer.runner import RunConfig, TraceRunner
from harness_tracer.writer import EventWriter


class OpenClawAdapterCommandTests(unittest.TestCase):
    def _command(self, extra_args: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary:
            return OpenClawAdapter().build_command(
                model="test-provider/test-model",
                input_query="delegate one task",
                run_dir=Path(temporary),
                harness_path=None,
                extra_args=extra_args,
            )

    def test_default_command_keeps_embedded_local_mode(self) -> None:
        self.assertIn("--local", self._command({}))

    def test_managed_gateway_mode_omits_local_and_private_arg(self) -> None:
        command = self._command({"harnessbench-gateway": "true"})

        self.assertNotIn("--local", command)
        self.assertNotIn("--harnessbench-gateway", command)

    def test_thinking_extra_arg_reaches_native_cli(self) -> None:
        command = self._command({"thinking": "high"})

        self.assertIn("--thinking", command)
        self.assertIn("high", command)


class OpenClawAdapterGatewayResultTests(unittest.TestCase):
    prompt = "delegate one task"

    def _record(self, role: str, content: object, **message: object) -> dict:
        return {
            "type": "message",
            "message": {"role": role, "content": content, **message},
        }

    def _initial_records(self) -> list[dict]:
        return [
            self._record("user", self.prompt),
            self._record(
                "assistant",
                [
                    {
                        "type": "toolCall",
                        "id": "spawn-1",
                        "name": "sessions_spawn",
                        "arguments": {"task": "Return CHILD_OK"},
                    }
                ],
            ),
            self._record(
                "toolResult",
                [{"type": "text", "text": "accepted"}],
                toolCallId="spawn-1",
                toolName="sessions_spawn",
                details={"childSessionKey": "agent:main:subagent:child-1"},
            ),
            self._record(
                "assistant",
                [
                    {
                        "type": "toolCall",
                        "id": "yield-1",
                        "name": "sessions_yield",
                        "arguments": {},
                    }
                ],
            ),
            self._record(
                "toolResult",
                [{"type": "text", "text": '{"status":"yielded"}'}],
                toolCallId="yield-1",
                toolName="sessions_yield",
            ),
        ]

    def _gateway_stdout(self, session_file: Path, *, yielded: bool = True) -> str:
        liveness = (
            {"yielded": True, "livenessState": "paused"}
            if yielded
            else {}
        )
        return json.dumps(
            {
                "runId": "gateway-run-1",
                "status": "ok",
                "result": {
                    "payloads": [],
                    "meta": {
                        **liveness,
                        "finalPromptText": self.prompt,
                        "agentMeta": {
                            "sessionId": "session-1",
                            "sessionFile": str(session_file),
                            "provider": "test-provider",
                            "model": "test-model",
                            "usage": {
                                "input": 10,
                                "cacheRead": 4,
                                "cacheWrite": 2,
                                "output": 6,
                            },
                        },
                        "systemPromptReport": {
                            "sessionKey": "agent:main:test"
                        },
                    },
                },
            }
        )

    def test_session_yield_waits_even_when_gateway_metadata_is_not_paused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            session_file = state / "session.jsonl"
            records = self._initial_records()
            session_file.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            sleep_calls = 0

            def append_completion(_: float) -> None:
                nonlocal sleep_calls
                sleep_calls += 1
                if sleep_calls > 2:
                    return
                if sleep_calls == 2:
                    output_result = self._record(
                        "toolResult",
                        [{"type": "text", "text": "wrote output"}],
                        toolCallId="parent-write-1",
                        toolName="write",
                    )
                    with session_file.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(output_result) + "\n")
                    return
                child_session = state / "child-1.jsonl"
                child_session.write_text(
                    "\n".join(
                        json.dumps(record)
                        for record in [
                            self._record(
                                "assistant",
                                [{
                                    "type": "toolCall",
                                    "id": "child-exec-1",
                                    "name": "exec",
                                    "arguments": {"command": "bash -c 'echo child'"},
                                }],
                            ),
                            self._record(
                                "toolResult",
                                [{"type": "text", "text": "Exec denied: approval required"}],
                                toolCallId="child-exec-1",
                                toolName="exec",
                                isError=True,
                            ),
                            self._record(
                                "assistant",
                                [{"type": "text", "text": "CHILD_OK"}],
                                id="child-final-1",
                            ),
                        ]
                    ) + "\n",
                    encoding="utf-8",
                )
                (state / "child-1.trajectory.jsonl").write_text(
                    json.dumps({
                        "type": "session.started",
                        "sessionKey": "agent:main:subagent:child-1",
                    }) + "\n",
                    encoding="utf-8",
                )
                completion = self._record(
                    "assistant",
                    [{"type": "text", "text": "PARENT_OK: CHILD_OK"}],
                )
                output_write = self._record(
                    "assistant",
                    [{
                        "type": "toolCall",
                        "id": "parent-write-1",
                        "name": "write",
                        "arguments": {
                            "path": "output/handoff_report.json",
                            "content": "{}",
                        },
                    }],
                )
                with session_file.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(completion) + "\n")
                    stream.write(json.dumps(output_write) + "\n")

            events_path = root / "events.json"
            writer = EventWriter(events_path, run_id="test-run")
            adapter = OpenClawAdapter(
                completion_poll_interval=0,
                sleep=append_completion,
            )
            adapter.build_command(
                model="test-provider/test-model",
                input_query=self.prompt,
                run_dir=root,
                harness_path=None,
                extra_args={"harnessbench-native-child-delegation": "true"},
            )
            try:
                result = adapter.consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=self._gateway_stdout(session_file, yielded=False),
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                    allowed_state_dir=str(state),
                    completion_timeout=1,
                    managed_gateway=True,
                )
            finally:
                writer.close()
            events = json.loads(events_path.read_text(encoding="utf-8"))

        self.assertEqual(sleep_calls, 2)
        self.assertEqual(result.status, "finished")
        self.assertTrue(result.trace_complete)
        self.assertTrue(
            any(
                event.get("event_type") == "assistant_message"
                and event.get("content") == "PARENT_OK: CHILD_OK"
                for event in events
            )
        )
        self.assertEqual(
            [
                event["tool_call"]["tool_name"]
                for event in events
                if event.get("event_type") == "tool"
            ],
            ["sessions_spawn", "sessions_yield", "exec", "write"],
        )
        main_tools = [
            event
            for event in events
            if event.get("event_type") == "tool"
            and event.get("principal_role") == "main"
        ]
        self.assertEqual(len(main_tools), 3)
        self.assertTrue(
            all(
                event.get("principal_id") == "agent:main:test"
                for event in main_tools
            )
        )
        child = [
            event for event in events
            if event.get("event_type") == "tool"
            and event.get("principal_role") == "child"
        ]
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0]["principal_id"], "agent:main:subagent:child-1")
        self.assertEqual(child[0]["parent_principal_id"], "agent:main:test")
        completions = [
            event
            for event in events
            if event.get("event_type") == "task_completion"
        ]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["content"], "CHILD_OK")
        self.assertTrue(completions[0]["parent_reactivated"])
        self.assertEqual(
            completions[0]["principal_id"],
            "agent:main:subagent:child-1",
        )
        output_write_event = next(
            event
            for event in events
            if event.get("event_type") == "tool"
            and event.get("tool_call", {}).get("tool_name") == "write"
        )
        self.assertLess(completions[0]["seq"], output_write_event["seq"])
        usage = next(
            event
            for event in events
            if event.get("source") == "openclaw.agent_meta"
        )
        self.assertEqual(usage["usage_scope"], "run")
        self.assertEqual(usage["usage"]["input_tokens"], 16)
        self.assertEqual(usage["usage"]["cached_input_tokens"], 4)
        self.assertEqual(usage["usage"]["cache_write_input_tokens"], 2)

    def test_yielded_gateway_result_fails_when_parent_never_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            session_file = state / "session.jsonl"
            session_file.write_text(
                "\n".join(
                    json.dumps(record) for record in self._initial_records()
                )
                + "\n",
                encoding="utf-8",
            )
            clock = iter((0.0, 1.0))
            writer = EventWriter(root / "events.json", run_id="test-run")
            try:
                result = OpenClawAdapter(
                    completion_poll_interval=0,
                    monotonic=lambda: next(clock),
                    sleep=lambda _: None,
                ).consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=self._gateway_stdout(session_file),
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                    allowed_state_dir=str(state),
                    completion_timeout=0.5,
                    managed_gateway=True,
                )
            finally:
                writer.close()

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.error_type, "openclaw_subagent_completion_timeout"
        )
        self.assertFalse(result.trace_complete)

    def test_direct_gateway_answer_without_tools_has_complete_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            session_file = state / "session.jsonl"
            records = [
                self._record("user", self.prompt),
                self._record(
                    "assistant",
                    [{"type": "text", "text": "DIRECT_OK"}],
                ),
            ]
            session_file.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            stdout = json.dumps(
                {
                    "runId": "gateway-run-direct",
                    "status": "ok",
                    "result": {
                        "payloads": [],
                        "meta": {
                            "finalPromptText": self.prompt,
                            "agentMeta": {
                                "sessionId": "session-direct",
                                "sessionFile": str(session_file),
                            },
                            "systemPromptReport": {
                                "sessionKey": "agent:main:direct"
                            },
                        },
                    },
                }
            )
            events_path = root / "events.json"
            writer = EventWriter(events_path, run_id="test-run")
            try:
                result = OpenClawAdapter().consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                    allowed_state_dir=str(state),
                    managed_gateway=True,
                )
            finally:
                writer.close()
            events = json.loads(events_path.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "finished")
        self.assertEqual(result.structured_trace_status, "available")
        self.assertTrue(result.trace_complete)
        self.assertTrue(
            any(
                event.get("event_type") == "assistant_message"
                and event.get("content") == "DIRECT_OK"
                for event in events
            )
        )

    def test_local_yielded_payload_does_not_wait_for_gateway_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            session_file = state / "session.jsonl"
            session_file.write_text(
                "\n".join(
                    json.dumps(record) for record in self._initial_records()
                )
                + "\n",
                encoding="utf-8",
            )
            writer = EventWriter(root / "events.json", run_id="test-run")
            try:
                result = OpenClawAdapter(
                    sleep=lambda _: self.fail("local mode must not poll"),
                ).consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=self._gateway_stdout(session_file),
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                    allowed_state_dir=str(state),
                    completion_timeout=0,
                    managed_gateway=False,
                )
            finally:
                writer.close()

        self.assertEqual(result.status, "finished")
        self.assertIsNone(result.error_type)


class OpenClawAdapterToolResultMetadataTests(unittest.TestCase):
    def _consume_native_result(
        self,
        *,
        content: str,
        details: dict[str, object],
        is_error: bool,
    ) -> dict:
        prompt = "run one command"
        records = [
            {
                "type": "message",
                "message": {"role": "user", "content": prompt},
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "exec-1",
                            "name": "exec",
                            "arguments": {"command": "large-command"},
                        }
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "content": [{"type": "text", "text": content}],
                    "toolCallId": "exec-1",
                    "toolName": "exec",
                    "isError": is_error,
                    "details": details,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            session_file = state / "session.jsonl"
            session_file.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            stdout = json.dumps(
                {
                    "runId": "gateway-run-metadata",
                    "status": "ok",
                    "result": {
                        "payloads": [],
                        "meta": {
                            "finalPromptText": prompt,
                            "agentMeta": {
                                "sessionId": "session-metadata",
                                "sessionFile": str(session_file),
                            },
                            "systemPromptReport": {
                                "sessionKey": "agent:main:metadata"
                            },
                        },
                    },
                }
            )
            events_path = root / "events.json"
            writer = EventWriter(events_path, run_id="test-run")
            try:
                result = OpenClawAdapter().consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                    allowed_state_dir=str(state),
                    managed_gateway=True,
                )
            finally:
                writer.close()
            events = json.loads(events_path.read_text(encoding="utf-8"))

        self.assertTrue(result.trace_complete)
        tool_events = [
            event for event in events if event.get("event_type") == "tool"
        ]
        self.assertEqual(len(tool_events), 1)
        return tool_events[0]["tool_result"]

    def test_native_session_preserves_exit_status_and_truncation_metadata(
        self,
    ) -> None:
        result = self._consume_native_result(
            content=(
                "retained tail"
                "[... 136,935 more characters truncated; "
                "rerun with narrower args if needed]"
            ),
            details={
                "status": "completed",
                "exitCode": 0,
                "durationMs": 51,
                "persistedDetailsTruncated": True,
                "originalDetailsBytesAtLeast": 8193,
                "aggregated": "must not be copied",
            },
            is_error=False,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["duration_ms"], 51)
        self.assertFalse(result["error"])
        self.assertIs(result["truncated"], True)
        self.assertEqual(result["omitted_characters"], 136935)
        self.assertIs(result["native_details_truncated"], True)
        self.assertEqual(result["native_details_bytes_at_least"], 8193)
        self.assertNotIn("aggregated", result)

    def test_native_session_preserves_failed_command_provenance(self) -> None:
        result = self._consume_native_result(
            content="command failed",
            details={"status": "failed", "exitCode": 23, "durationMs": "17"},
            is_error=True,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 23)
        self.assertEqual(result["duration_ms"], 17)
        self.assertTrue(result["error"])
        self.assertNotIn("truncated", result)
        self.assertNotIn("omitted_characters", result)

    def test_native_details_truncation_does_not_imply_visible_output_loss(
        self,
    ) -> None:
        result = self._consume_native_result(
            content="complete visible output",
            details={
                "status": "completed",
                "exitCode": 0,
                "persistedDetailsTruncated": True,
                "originalDetailsBytesAtLeast": 9000,
            },
            is_error=False,
        )

        self.assertIs(result["native_details_truncated"], True)
        self.assertNotIn("truncated", result)
        self.assertNotIn("omitted_characters", result)

    def test_trajectory_result_accepts_native_camel_case_metadata(self) -> None:
        payload = {
            "sessionKey": "agent:main:trajectory",
            "runId": "trajectory-run",
            "trajectory": [
                {
                    "type": "tool_call",
                    "tool_call_id": "exec-2",
                    "tool_name": "exec",
                    "arguments": {"command": "large-command"},
                },
                {
                    "type": "tool_result",
                    "tool_call_id": "exec-2",
                    "tool_name": "exec",
                    "content": "tail[... 42 more characters truncated]",
                    "isError": False,
                    "details": {
                        "status": "completed",
                        "exitCode": "0",
                        "durationMs": 5,
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events_path = root / "events.json"
            writer = EventWriter(events_path, run_id="test-run")
            try:
                result = OpenClawAdapter().consume(
                    process=CompletedProcessData(
                        args=[],
                        returncode=0,
                        stdout=json.dumps(payload),
                        stderr="",
                        timed_out=False,
                    ),
                    writer=writer,
                    run_dir=root,
                )
            finally:
                writer.close()
            events = json.loads(events_path.read_text(encoding="utf-8"))

        self.assertTrue(result.trace_complete)
        tool_result = next(
            event["tool_result"]
            for event in events
            if event.get("event_type") == "tool"
        )
        self.assertEqual(tool_result["status"], "completed")
        self.assertEqual(tool_result["exit_code"], 0)
        self.assertEqual(tool_result["duration_ms"], 5)
        self.assertIs(tool_result["truncated"], True)
        self.assertEqual(tool_result["omitted_characters"], 42)


class OpenClawRunnerTimeoutTests(unittest.TestCase):
    def test_managed_gateway_receives_remaining_timeout_without_replaced_env(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        class FakeAdapter:
            name = "openclaw"

            def build_command(self, **kwargs: object) -> list[str]:
                return ["openclaw", "agent"]

            def consume(self, **kwargs: object):
                captured.update(kwargs)
                from harness_tracer.adapters.base import AdapterResult

                return AdapterResult(
                    status="finished",
                    structured_trace_status="available",
                    trace_complete=True,
                )

        class FakeProcessRunner:
            def run(self, command: list[str], **kwargs: object) -> CompletedProcessData:
                return CompletedProcessData(
                    args=command,
                    returncode=0,
                    stdout="{}",
                    stderr="",
                    timed_out=False,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch("harness_tracer.runner.adapter_for", return_value=FakeAdapter()):
                TraceRunner(process_runner=FakeProcessRunner()).run(
                    RunConfig(
                        harness="openclaw",
                        model="test/model",
                        input_query="test",
                        output_dir=root / "output",
                        cwd=root,
                        timeout=7,
                        extra_args={"harnessbench-gateway": "true"},
                        replace_environment=False,
                    )
                )

        self.assertIs(captured["managed_gateway"], True)
        self.assertGreaterEqual(captured["completion_timeout"], 0)
        self.assertLessEqual(captured["completion_timeout"], 7)


if __name__ == "__main__":
    unittest.main()
