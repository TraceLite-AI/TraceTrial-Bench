from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_tracer.adapters import adapter_for
from harness_tracer.process import CompletedProcessData
from harness_tracer.runner import RunConfig, TraceRunner
from harness_tracer.writer import EventWriter


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_followup_command_resumes_the_native_session(self) -> None:
        command = adapter_for("claudecode").build_command(
            model="model-1",
            input_query="follow up",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={
                "harnessbench-conversation": "true",
                "harnessbench-session-id": "session-1",
            },
        )

        self.assertIn("--resume", command)
        self.assertIn("session-1", command)
        self.assertNotIn("--harnessbench-conversation", command)

    def test_effort_extra_arg_reaches_native_cli(self) -> None:
        command = adapter_for("claudecode").build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={"effort": "high"},
        )

        self.assertEqual(command[-2:], ["--effort", "high"])

    def test_partial_native_events_are_enabled_only_for_return_boundary_probe(self) -> None:
        adapter = adapter_for("claudecode")
        ordinary = adapter.build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={},
        )
        boundary_probe = adapter.build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={"harnessbench-native-child-return-boundary": "true"},
        )

        self.assertNotIn("--include-partial-messages", ordinary)
        self.assertIn("--include-partial-messages", boundary_probe)
        self.assertNotIn("--harnessbench-native-child-return-boundary", boundary_probe)

    def test_approval_probe_allows_only_declared_artifact_tools(self) -> None:
        command = adapter_for("claudecode").build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={
                "harnessbench-approval-policy": "ask_always",
                "harnessbench-approval-artifact-tools": "Read,Write,Task",
            },
        )

        self.assertIn("--permission-mode", command)
        self.assertIn("--allowedTools", command)
        self.assertIn("Write", command)
        self.assertNotIn("Bash", command[command.index("--allowedTools") + 1 :])

    def test_user_rejected_meta_emits_typed_policy_event(self) -> None:
        result = _tool_result("call-1", "This command requires approval", is_error=True)
        result["tool_result_meta"] = [
            {"id": "call-1", "non_execution_kind": "user-rejected"}
        ]
        _, events = self._consume(
            _json_lines(
                [
                    _assistant_block(
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Bash",
                            "input": {"command": "printf safe"},
                        },
                        message_id="message-tool",
                    ),
                    result,
                ]
            )
        )

        policy = next(
            event for event in events if event["event_type"] == "policy_event"
        )
        self.assertEqual(policy["tool_call_id"], "call-1")
        self.assertEqual(policy["decision"], "blocked")
        self.assertFalse(policy["execution_started"])

    def test_maps_stream_json_and_deduplicates_tool_events(self) -> None:
        records = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "session-1",
                "model": "model-1",
                "tools": ["Bash"],
                "claude_code_version": "2.1.220",
                "cwd": "/workspace",
            },
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens_delta": 2,
            },
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens_delta": 3,
            },
            _assistant_block(
                {"type": "thinking", "thinking": "Inspect the workspace."},
                message_id="message-thinking",
            ),
            _assistant_block(
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
                message_id="message-tool",
            ),
            _assistant_block(
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
                message_id="message-tool",
            ),
            _tool_result("call-1", "ok"),
            _tool_result("call-1", "ok"),
            _assistant_block(
                {"type": "text", "text": "Done."},
                message_id="message-final",
            ),
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "session-1",
                "result": "Done.",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 2,
                    "output_tokens": 6,
                },
            },
        ]

        result, events = self._consume(_json_lines(records))

        self.assertEqual(result.structured_trace_status, "available")
        self.assertTrue(result.trace_complete)
        self.assertEqual(
            [event["event_type"] for event in events].count("run_started"),
            1,
        )
        self.assertEqual(
            [event["event_type"] for event in events].count("model_input"),
            1,
        )
        tool_events = [event for event in events if event["event_type"] == "tool"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0]["tool_call"]["tool_name"], "Bash")
        self.assertEqual(tool_events[0]["tool_result"]["content"], "ok")
        self.assertFalse(tool_events[0]["tool_result"]["error"])
        assistant_events = [
            event for event in events if event["event_type"] == "assistant_message"
        ]
        self.assertEqual(
            [(event["content_kind"], event["content"]) for event in assistant_events],
            [
                ("thinking", "Inspect the workspace."),
                ("text", "Done."),
            ],
        )
        reasoning_usage = next(
            event
            for event in events
            if event.get("source") == "claudecode.thinking_tokens"
        )
        self.assertEqual(
            reasoning_usage["usage"]["reasoning_output_tokens"],
            5,
        )
        result_usage = next(
            event
            for event in events
            if event.get("source") == "claudecode.result"
        )
        self.assertEqual(result_usage["usage_scope"], "run")
        self.assertEqual(
            result_usage["usage"],
            {
                "input_tokens": 16,
                "cached_input_tokens": 4,
                "output_tokens": 6,
                "cache_creation_input_tokens": 2,
                "cache_write_input_tokens": 2,
            },
        )
        self.assertFalse(
            any(event["event_type"] == "raw_stdout" for event in events)
        )

    def test_preserves_per_call_usage_and_result_aggregate(self) -> None:
        first = _assistant_block(
            {"type": "text", "text": "First."}, message_id="message-1"
        )
        first["message"]["usage"] = {
            "input_tokens": 10,
            "cache_read_input_tokens": 4,
            "output_tokens": 2,
        }
        second = _assistant_block(
            {"type": "text", "text": "Second."}, message_id="message-2"
        )
        second["message"]["usage"] = {
            "input_tokens": 20,
            "cache_read_input_tokens": 5,
            "output_tokens": 3,
        }
        result, events = self._consume(
            _json_lines(
                [
                    first,
                    second,
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "Second.",
                        "usage": {"input_tokens": 999, "output_tokens": 999},
                    },
                ]
            )
        )

        self.assertEqual(result.status, "finished")
        usages = [
            event
            for event in events
            if event.get("event_type") == "token_usage"
            and event.get("source") == "claudecode.assistant"
        ]
        self.assertEqual(len(usages), 2)
        self.assertTrue(all(event["usage_scope"] == "call" for event in usages))
        self.assertEqual(usages[0]["usage"]["input_tokens"], 14)
        self.assertEqual(usages[1]["usage"]["input_tokens"], 25)
        result_usage = next(
            event
            for event in events
            if event.get("source") == "claudecode.result"
        )
        self.assertEqual(result_usage["usage_scope"], "run")
        self.assertTrue(result_usage["usage_aggregate"])
        self.assertEqual(result_usage["usage"]["input_tokens"], 999)

    def test_prefers_full_run_model_usage_over_terminal_call_usage(self) -> None:
        result, events = self._consume(
            _json_lines(
                [
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "Done.",
                        "usage": {"input_tokens": 999, "output_tokens": 999},
                        "modelUsage": {
                            "model-a": {
                                "inputTokens": 10,
                                "cacheReadInputTokens": 4,
                                "cacheCreationInputTokens": 2,
                                "outputTokens": 3,
                            },
                            "model-b": {
                                "inputTokens": 20,
                                "cacheReadInputTokens": 5,
                                "cacheCreationInputTokens": 0,
                                "outputTokens": 7,
                            },
                        },
                    }
                ]
            )
        )

        self.assertEqual(result.status, "finished")
        result_usage = next(
            event
            for event in events
            if event.get("source") == "claudecode.result"
        )
        self.assertEqual(result_usage["native_usage_source"], "modelUsage")
        self.assertEqual(
            result_usage["usage"],
            {
                "input_tokens": 41,
                "cached_input_tokens": 9,
                "output_tokens": 10,
                "cache_creation_input_tokens": 2,
                "cache_write_input_tokens": 2,
            },
        )

    def test_stream_line_observation_offsets_are_preserved(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "session-1",
                    "model": "model-1",
                    "tools": [],
                },
                _assistant_block(
                    {"type": "text", "text": "Done."},
                    message_id="message-final",
                ),
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Done.",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                    },
                },
            ]
        )

        result, events = self._consume(
            stdout,
            stdout_line_observed_ns=(
                1_000_000_000,
                1_250_000_000,
                2_250_000_000,
            ),
        )

        self.assertEqual(result.structured_trace_status, "available")
        model_input = next(
            event for event in events if event.get("event_type") == "model_input"
        )
        assistant = next(
            event
            for event in events
            if event.get("event_type") == "assistant_message"
        )
        usage = next(
            event for event in events if event.get("event_type") == "token_usage"
        )
        self.assertEqual(model_input["stream_observed_offset_ms"], 0.0)
        self.assertEqual(assistant["stream_observed_offset_ms"], 250.0)
        self.assertEqual(usage["stream_observed_offset_ms"], 1250.0)

    def test_malformed_json_preserves_raw_event_and_degrades_status(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "session-1",
                    "model": "model-1",
                    "tools": [],
                }
            ]
        ) + "\n{not-json"

        result, events = self._consume(stdout)

        self.assertEqual(result.structured_trace_status, "unavailable")
        self.assertTrue(result.trace_complete)
        raw_events = [
            event for event in events if event["event_type"] == "raw_stdout"
        ]
        self.assertEqual(len(raw_events), 1)
        self.assertFalse(raw_events[0]["parseable"])
        self.assertIn("JSON parse error", raw_events[0]["parse_error"])

    def test_retains_unmatched_tool_call_and_result(self) -> None:
        records = [
            _assistant_block(
                {
                    "type": "tool_use",
                    "id": "pending-call",
                    "name": "Read",
                    "input": {"file_path": "README.md"},
                },
                message_id="message-pending",
            ),
            _tool_result("orphan-result", "missing call", is_error=True),
        ]

        result, events = self._consume(_json_lines(records))

        self.assertEqual(result.structured_trace_status, "available")
        tool_events = [event for event in events if event["event_type"] == "tool"]
        self.assertEqual(len(tool_events), 2)
        by_id = {
            event["tool_call"]["tool_call_id"]: event for event in tool_events
        }
        self.assertIsNone(by_id["pending-call"]["tool_result"])
        self.assertEqual(
            by_id["orphan-result"]["tool_call"]["tool_name"],
            "unknown",
        )
        self.assertTrue(by_id["orphan-result"]["tool_result"]["error"])

    def test_preserves_forwarded_subagent_principal_lineage(self) -> None:
        child_call = _assistant_block(
            {
                "type": "tool_use",
                "id": "child-call",
                "name": "Bash",
                "input": {"command": "sha256sum archive/a.dat"},
            },
            message_id="message-child",
        )
        child_call["session_id"] = "session-1"
        child_call["parent_tool_use_id"] = "agent-spawn-call"
        child_result = _tool_result("child-call", "abc")
        child_result["session_id"] = "session-1"
        child_result["parent_tool_use_id"] = "agent-spawn-call"

        _result, events = self._consume(_json_lines([child_call, child_result]))

        tool_event = next(event for event in events if event["event_type"] == "tool")
        self.assertEqual(
            tool_event["principal_id"],
            "claudecode-child:agent-spawn-call",
        )
        self.assertEqual(tool_event["parent_principal_id"], "session-1")
        self.assertEqual(tool_event["parent_tool_use_id"], "agent-spawn-call")
        self.assertEqual(tool_event["principal_role"], "child")

    def test_error_result_marks_adapter_failed(self) -> None:
        result, _events = self._consume(
            _json_lines(
                [
                    {
                        "type": "result",
                        "subtype": "error",
                        "is_error": True,
                        "result": "provider request failed",
                    }
                ]
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_type, "claudecode_result_error")
        self.assertEqual(result.error_message, "provider request failed")
        self.assertFalse(result.trace_complete)
        self.assertEqual(result.structured_trace_status, "available")

    def test_later_successful_root_result_clears_intermediate_error(self) -> None:
        result, _events = self._consume(
            _json_lines(
                [
                    {
                        "type": "result",
                        "subtype": "error",
                        "is_error": True,
                        "result": "child turn failed",
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "root deliverable completed",
                    },
                ]
            )
        )

        self.assertEqual(result.status, "finished")
        self.assertIsNone(result.error_type)
        self.assertIsNone(result.error_message)
        self.assertTrue(result.trace_complete)

    def test_task_scoped_completed_response_format_diagnostic_is_soft(self) -> None:
        diagnostic = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "terminal_reason": "completed",
            "errors": [
                "[ede_diagnostic] provider compatibility check",
                "This response_format type is unavailable now",
            ],
        }
        stdout = _json_lines([diagnostic])

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout, returncode=1)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                    extra_args={
                        "harnessbench-approval-claude-diagnostic-soft-exit": "true"
                    },
                )
            )
            events = json.loads(
                Path(summary["events_path"]).read_text(encoding="utf-8")
            )

        self.assertEqual(summary["status"], "finished")
        self.assertTrue(summary["trace_complete"])
        self.assertEqual(summary["exit_code"], 1)
        self.assertTrue(
            any(
                event.get("event_type") == "native_diagnostic"
                and event.get("diagnostic_type")
                == "completed_tool_turn_ede_diagnostic"
                for event in events
            )
        )

    def test_task_scoped_completed_permission_denial_ede_is_soft(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "terminal_reason": "completed",
                    "errors": [
                        "[ede_diagnostic] result_type=assistant "
                        "last_content_type=none stop_reason=end_turn"
                    ],
                    "permission_denials": [
                        {"tool_name": "Bash", "tool_use_id": "call-1"}
                    ],
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout, returncode=1)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                    extra_args={
                        "harnessbench-approval-claude-diagnostic-soft-exit": "true"
                    },
                )
            )

        self.assertEqual(summary["status"], "finished")
        self.assertTrue(summary["trace_complete"])

    def test_task_scoped_completed_ede_only_final_turn_is_soft(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "terminal_reason": "completed",
                    "errors": [
                        "[ede_diagnostic] result_type=assistant "
                        "last_content_type=none stop_reason=end_turn"
                    ],
                    "permission_denials": [],
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout, returncode=1)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                    extra_args={
                        "harnessbench-approval-claude-diagnostic-soft-exit": "true"
                    },
                )
            )

        self.assertEqual(summary["status"], "finished")
        self.assertTrue(summary["trace_complete"])

    def test_task_scoped_timeout_diagnostic_is_not_soft(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "terminal_reason": "completed",
                    "errors": [
                        "[ede_diagnostic] result_type=assistant stop_reason=end_turn",
                        "provider timeout while completing request",
                    ],
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout, returncode=1)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                    extra_args={
                        "harnessbench-approval-claude-diagnostic-soft-exit": "true"
                    },
                )
            )

        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["trace_complete"])

    def test_completed_response_format_diagnostic_is_not_globally_soft(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "terminal_reason": "completed",
                    "errors": [
                        "[ede_diagnostic] provider compatibility check",
                        "This response_format type is unavailable now",
                    ],
                }
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout, returncode=1)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                )
            )

        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["trace_complete"])

    def test_claude_alias_uses_structured_adapter_and_existing_command(self) -> None:
        adapter = adapter_for("claude")

        command = adapter.build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={"debug": "true"},
        )

        self.assertEqual(type(adapter).__name__, "ClaudeCodeAdapter")
        self.assertEqual(command[0], "claude")
        self.assertEqual(command[1:3], ["-p", "prompt"])
        self.assertIn("stream-json", command)
        self.assertEqual(command[-2:], ["--debug", "true"])

    def test_trace_runner_writes_one_claudecode_run_started_event(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "session-1",
                    "model": "model-1",
                    "tools": [],
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Done.",
                },
            ]
        )
        process_runner = _StaticProcessRunner(stdout)

        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "trace"
            summary = TraceRunner(process_runner=process_runner).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=output_dir,
                )
            )
            events = json.loads(
                Path(summary["events_path"]).read_text(encoding="utf-8")
            )

        self.assertEqual(
            [event["event_type"] for event in events].count("run_started"),
            1,
        )

    def test_task_scoped_native_child_notifications_are_typed(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": [],
                },
                {
                    "type": "system",
                    "subtype": "task_started",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "task_type": "local_agent",
                    "tool_use_id": "call-1",
                    "prompt": "run audit",
                },
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "tool_use_id": "call-1",
                    "status": "completed",
                    "summary": "CHILD_AUDIT_COMPLETE {\"sum\":\"1.00\"}",
                },
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": [],
                },
            ]
        )

        _, events = self._consume(stdout, native_child_delegation=True)

        self.assertEqual(
            [event["event_type"] for event in events].count("run_started"), 1
        )
        lifecycle = next(
            event for event in events if event["event_type"] == "subagent_lifecycle"
        )
        completion = next(
            event for event in events if event["event_type"] == "task_completion"
        )
        self.assertEqual(lifecycle["principal_id"], "claudecode-child:call-1")
        self.assertEqual(completion["principal_id"], lifecycle["principal_id"])
        self.assertEqual(completion["parent_principal_id"], "parent-1")
        self.assertTrue(completion["parent_reactivated"])

    def test_task_scoped_native_parent_end_turn_precedes_child_notification(self) -> None:
        completion_text = 'CHILD_AUDIT_COMPLETE {"sum":"1.00"}'
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": ["Agent"],
                },
                {
                    "type": "assistant",
                    "session_id": "parent-1",
                    "uuid": "uuid-agent-call",
                    "message": {
                        "id": "message-agent-call",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-1",
                                "name": "Agent",
                                "input": {
                                    "description": "ledger-auditor",
                                    "prompt": "run audit",
                                    "run_in_background": True,
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_started",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "task_type": "local_agent",
                    "tool_use_id": "call-1",
                    "prompt": "run audit",
                },
                {
                    "type": "user",
                    "session_id": "parent-1",
                    "uuid": "uuid-agent-result",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "content": "Async agent launched successfully. agentId: task-1",
                            }
                        ],
                    },
                },
                {
                    "type": "assistant",
                    "session_id": "parent-1",
                    "uuid": "uuid-parent-wait",
                    "message": {
                        "id": "message-parent-wait",
                        "role": "assistant",
                        "model": "model-1",
                        "stop_reason": None,
                        "content": [{"type": "text", "text": "Waiting."}],
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "tool_use_id": "call-1",
                    "status": "completed",
                    "summary": completion_text,
                },
            ]
        )
        archive_records = [
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "uuid": "archive-agent-call",
                "message": {
                    "id": "message-agent-call",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Agent",
                            "input": {
                                "description": "ledger-auditor",
                                "prompt": "run audit",
                                "run_in_background": True,
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            },
            {
                "type": "user",
                "parent_tool_use_id": None,
                "uuid": "archive-agent-result",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": "Async agent launched successfully.",
                        }
                    ],
                },
                "tool_use_result": {
                    "agentId": "task-1",
                    "isAsync": True,
                    "status": "async_launched",
                },
            },
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "uuid": "archive-parent-wait",
                "timestamp": "2026-08-17T00:00:00Z",
                "message": {
                    "id": "message-parent-wait",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Waiting."}],
                    "stop_reason": "end_turn",
                },
            },
        ]

        _, events = self._consume(
            stdout,
            native_child_delegation=True,
            native_child_return_boundary=True,
            native_session_records=archive_records,
        )

        dispatch = next(
            event
            for event in events
            if event.get("event_type") == "tool"
            and event.get("tool_call", {}).get("tool_call_id") == "call-1"
        )
        boundary = next(
            event
            for event in events
            if event.get("boundary_kind") == "parent_turn_end"
        )
        completion = next(
            event for event in events if event["event_type"] == "task_completion"
        )
        self.assertLess(dispatch["seq"], boundary["seq"])
        self.assertLess(boundary["seq"], completion["seq"])
        self.assertEqual(boundary["native_event_type"], "assistant.end_turn")
        self.assertEqual(
            boundary["raw_event"]["message"]["stop_reason"], "end_turn"
        )
        self.assertEqual(boundary["pending_child_ids"], ["claudecode-child:call-1"])
        self.assertEqual(
            sum(event.get("boundary_kind") == "parent_turn_end" for event in events),
            1,
        )

    def test_native_parent_boundary_projection_is_off_by_default(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": [],
                }
            ]
        )
        archive_records = [
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {
                    "id": "message-parent-wait",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Waiting."}],
                    "stop_reason": "end_turn",
                },
            }
        ]

        _, events = self._consume(
            stdout,
            native_child_delegation=True,
            native_session_records=archive_records,
        )

        self.assertFalse(
            any(event.get("boundary_kind") == "parent_turn_end" for event in events)
        )

    def test_empty_completed_tool_turn_result_is_a_pre_notification_boundary(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": ["Agent"],
                },
                {
                    "type": "assistant",
                    "session_id": "parent-1",
                    "message": {
                        "id": "message-agent-call",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-1",
                                "name": "Agent",
                                "input": {
                                    "description": "ledger-auditor",
                                    "prompt": "run audit",
                                    "run_in_background": True,
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_started",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "task_type": "local_agent",
                    "tool_use_id": "call-1",
                },
                {
                    "type": "user",
                    "session_id": "parent-1",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "content": "Async agent launched successfully. agentId: task-1",
                            }
                        ]
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "tool_use_id": "call-1",
                    "status": "completed",
                    "summary": "CHILD_AUDIT_COMPLETE {}",
                },
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "session_id": "parent-1",
                    "terminal_reason": "completed",
                    "stop_reason": "end_turn",
                    "errors": [
                        "[ede_diagnostic] result_type=assistant "
                        "last_content_type=none stop_reason=end_turn"
                    ],
                },
            ]
        )

        _, events = self._consume(
            stdout,
            native_child_delegation=True,
            native_child_return_boundary=True,
        )

        dispatch = next(event for event in events if event["event_type"] == "tool")
        boundary = next(
            event
            for event in events
            if event.get("native_event_type")
            == "result.completed_empty_end_turn"
        )
        completion = next(
            event for event in events if event["event_type"] == "task_completion"
        )
        self.assertLess(dispatch["seq"], boundary["seq"])
        self.assertLess(boundary["seq"], completion["seq"])
        self.assertEqual(boundary["source"], "claudecode.result")
        self.assertEqual(boundary["raw_event"]["stop_reason"], "end_turn")
        self.assertTrue(boundary["observed_after_child_notification"])
        self.assertEqual(boundary["pending_child_ids"], ["claudecode-child:call-1"])

    def test_final_end_turn_after_task_notification_is_not_moved_earlier(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "parent-1",
                    "model": "model-1",
                    "tools": ["Agent"],
                },
                {
                    "type": "assistant",
                    "session_id": "parent-1",
                    "message": {
                        "id": "message-agent-call",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-1",
                                "name": "Agent",
                                "input": {
                                    "description": "ledger-auditor",
                                    "prompt": "run audit",
                                    "run_in_background": True,
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_started",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "task_type": "local_agent",
                    "tool_use_id": "call-1",
                },
                {
                    "type": "user",
                    "session_id": "parent-1",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "content": "Async agent launched successfully. agentId: task-1",
                            }
                        ]
                    },
                },
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "parent-1",
                    "task_id": "task-1",
                    "tool_use_id": "call-1",
                    "status": "completed",
                    "summary": "CHILD_AUDIT_COMPLETE {}",
                },
                {
                    "type": "assistant",
                    "session_id": "parent-1",
                    "message": {
                        "id": "message-final",
                        "stop_reason": None,
                        "content": [{"type": "text", "text": "Done."}],
                    },
                },
            ]
        )
        archive_records = [
            {
                "type": "assistant",
                "message": {
                    "id": "message-agent-call",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Agent",
                            "input": {
                                "run_in_background": True,
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": "Async agent launched successfully. The agent is working in the background.\nagentId: task-1",
                        }
                    ]
                },
                "toolUseResult": {
                    "isAsync": True,
                    "status": "async_launched",
                    "agentId": "task-1",
                },
            },
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": (
                    "<task-notification><tool-use-id>call-1</tool-use-id>"
                    "<status>completed</status></task-notification>"
                ),
            },
            {
                "type": "assistant",
                "message": {
                    "id": "message-final",
                    "content": [{"type": "text", "text": "Done."}],
                    "stop_reason": "end_turn",
                },
            },
        ]

        _, events = self._consume(
            stdout,
            native_child_delegation=True,
            native_child_return_boundary=True,
            native_session_records=archive_records,
        )

        self.assertFalse(
            any(event.get("boundary_kind") == "parent_turn_end" for event in events)
        )

    def test_thinking_is_not_used_as_the_final_answer(self) -> None:
        stdout = _json_lines(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "session-1",
                    "model": "model-1",
                    "tools": [],
                },
                _assistant_block(
                    {"type": "thinking", "thinking": "Internal analysis."},
                    message_id="message-thinking",
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            summary = TraceRunner(
                process_runner=_StaticProcessRunner(stdout)
            ).run(
                RunConfig(
                    harness="claudecode",
                    model="model-1",
                    input_query="prompt",
                    output_dir=Path(temporary) / "trace",
                )
            )
            last_message = Path(summary["summary_path"]).with_name(
                "last_message.txt"
            ).read_text(encoding="utf-8")

        self.assertIsNone(summary["final_answer"])
        self.assertEqual(last_message, "")

    def _consume(
        self,
        stdout: str,
        *,
        stdout_line_observed_ns: tuple[int, ...] = (),
        native_child_delegation: bool = False,
        native_child_return_boundary: bool = False,
        native_session_records: list[dict] | None = None,
    ):
        adapter = adapter_for("claudecode")
        if native_child_delegation or native_child_return_boundary:
            extra_args = {}
            if native_child_delegation:
                extra_args["harnessbench-native-child-delegation"] = "true"
            if native_child_return_boundary:
                extra_args["harnessbench-native-child-return-boundary"] = "true"
            adapter.build_command(
                model="model-1",
                input_query="prompt",
                run_dir=Path("run"),
                harness_path=None,
                extra_args=extra_args,
            )
        process = CompletedProcessData(
            args=[
                "claude",
                "-p",
                "prompt",
                "--output-format",
                "stream-json",
                "--model",
                "model-1",
            ],
            returncode=0,
            stdout=stdout,
            stderr="",
            stdout_line_observed_ns=stdout_line_observed_ns,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "trace"
            run_dir.mkdir()
            allowed_state_dir = None
            if native_session_records is not None:
                state_dir = root / "state" / "projects" / "workspace"
                state_dir.mkdir(parents=True)
                (state_dir / "parent-1.jsonl").write_text(
                    "".join(
                        json.dumps(record) + "\n"
                        for record in native_session_records
                    ),
                    encoding="utf-8",
                )
                allowed_state_dir = str(root / "state")
            writer = EventWriter(run_dir / "events.json", run_id="run-1")
            result = adapter.consume(
                process=process,
                writer=writer,
                run_dir=run_dir,
                allowed_state_dir=allowed_state_dir,
            )
            writer.close()
            return result, list(writer.events)


def _assistant_block(block: dict, *, message_id: str) -> dict:
    return {
        "type": "assistant",
        "uuid": f"uuid-{message_id}",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": "model-1",
            "content": [block],
        },
    }


def _tool_result(call_id: str, content: str, *, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "uuid": f"uuid-{call_id}-{content}",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
    }


def _json_lines(records: list[dict]) -> str:
    return "\n".join(json.dumps(record) for record in records)


class _StaticProcessRunner:
    def __init__(self, stdout: str, *, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode

    def run(self, command: list[str], **_kwargs) -> CompletedProcessData:
        return CompletedProcessData(
            args=command,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr="",
        )


if __name__ == "__main__":
    unittest.main()
