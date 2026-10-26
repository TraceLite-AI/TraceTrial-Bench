from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from harness_tracer.adapters.claudecode import ClaudeCodeAdapter
from harness_tracer.adapters.codex import CodexAdapter
from harness_tracer.adapters.hermes import _consume_native_trace
from harness_tracer.native_sessions import archive_claudecode_session
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter
from tracetrial_bench.conversation_runner import _import_turn_events


class NativeSessionArchiveTests(unittest.TestCase):
    def test_claudecode_archives_bound_session_and_preserves_raw_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            project = state / "projects" / "workspace"
            project.mkdir(parents=True)
            compact = {
                "type": "system",
                "subtype": "compact_boundary",
                "session_id": "session-1",
                "id": "compact-1",
                "summary": "kept detail 4096",
            }
            (project / "session-1.jsonl").write_text(
                json.dumps(compact) + "\n", encoding="utf-8"
            )
            stdout = "\n".join(
                json.dumps(record)
                for record in (
                    {
                        "type": "system",
                        "subtype": "init",
                        "session_id": "session-1",
                        "model": "model-1",
                        "tools": [],
                    },
                    compact,
                )
            )
            writer = EventWriter(root / "events.json", run_id="run-1")
            result = ClaudeCodeAdapter().consume(
                process=CompletedProcessData(
                    args=["claude", "-p", "prompt", "--model", "model-1"],
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                ),
                writer=writer,
                run_dir=root,
                allowed_state_dir=str(state),
            )
            writer.close()

            events = writer.events
            compactions = [
                event for event in events if event["event_type"] == "context_compaction"
            ]
            archives = [
                event
                for event in events
                if event["event_type"] == "native_session_archive"
            ]
            self.assertEqual(len(compactions), 1)
            self.assertEqual(compactions[0]["summary"], "kept detail 4096")
            self.assertTrue(
                any(
                    event["event_type"] == "raw_stdout"
                    and event.get("raw_event", {}).get("subtype") == "compact_boundary"
                    for event in events
                )
            )
            self.assertEqual(archives[0]["status"], "available")
            self.assertTrue(
                (root / "native_sessions" / "claudecode-session.trace.jsonl").is_file()
            )
            self.assertTrue(result.trace_complete)

    def test_codex_archives_rollout_and_normalizes_native_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            sessions = state / "sessions" / "2026" / "08" / "10"
            sessions.mkdir(parents=True)
            records = [
                {
                    "type": "session_meta",
                    "payload": {"id": "thread-1"},
                },
                {
                    "type": "compacted",
                    "id": "compact-1",
                    "summary": "detail survived",
                },
            ]
            (sessions / "rollout-test.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            writer = EventWriter(root / "events.json", run_id="run-1")
            result = CodexAdapter().consume(
                process=CompletedProcessData(
                    args=["codex", "exec"],
                    returncode=0,
                    stdout=json.dumps(
                        {"type": "thread.started", "thread_id": "thread-1"}
                    ),
                    stderr="",
                ),
                writer=writer,
                run_dir=root,
                allowed_state_dir=str(state),
            )
            writer.close()

            compaction = next(
                event
                for event in writer.events
                if event["event_type"] == "context_compaction"
            )
            self.assertEqual(compaction["native_event_type"], "compacted")
            self.assertEqual(compaction["summary"], "detail survived")
            self.assertTrue(
                (root / "native_sessions" / "codex-session.trace.jsonl").is_file()
            )
            self.assertTrue(result.trace_complete)

    def test_codex_native_child_mode_selects_latest_parent_and_discovers_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            sessions = state / "sessions" / "2026" / "08" / "17"
            sessions.mkdir(parents=True)

            old_parent = sessions / "rollout-parent-old.jsonl"
            old_parent.write_text(
                json.dumps(
                    {"type": "session_meta", "payload": {"id": "parent-1"}}
                )
                + "\n",
                encoding="utf-8",
            )
            parent_records = [
                {"type": "session_meta", "payload": {"id": "parent-1"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "old prompt"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "old-exec",
                        "arguments": json.dumps({"cmd": "old command"}),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-old"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "old-exec",
                        "output": "old output",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-old"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "current prompt"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "current-exec",
                        "arguments": json.dumps({"cmd": "current command"}),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-current-exec"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "current-exec",
                        "output": "current output",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-current-exec"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "call_id": "spawn-1",
                        "arguments": json.dumps(
                            {"task_name": "priority_fixity", "message": "measure"}
                        ),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-parent"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "spawn-1",
                        "output": json.dumps({"task_name": "/root/priority_fixity"}),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-parent"
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-parent"},
                },
            ]
            new_parent = sessions / "rollout-parent-new.jsonl"
            new_parent.write_text(
                "".join(json.dumps(record) + "\n" for record in parent_records),
                encoding="utf-8",
            )
            os.utime(old_parent, ns=(1_000_000_000, 1_000_000_000))
            os.utime(new_parent, ns=(2_000_000_000, 2_000_000_000))

            child_records = [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "parent-1",
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "parent_thread_id": "parent-1",
                                    "depth": 1,
                                    "agent_path": "/root/priority_fixity",
                                }
                            }
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "child-exec-1",
                        "arguments": json.dumps({"cmd": "sha256sum archive/a.dat"}),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-child"
                        },
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "child-exec-1",
                        "output": "abc  archive/a.dat",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": "turn-child"
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-child"},
                },
            ]
            (sessions / "rollout-child.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in child_records),
                encoding="utf-8",
            )

            adapter = CodexAdapter()
            adapter.build_command(
                model="model-1",
                input_query="prompt",
                run_dir=root,
                harness_path=None,
                extra_args={"harnessbench-native-child-delegation": "true"},
            )
            writer = EventWriter(root / "events.json", run_id="run-1")
            adapter.consume(
                process=CompletedProcessData(
                    args=["codex", "exec"],
                    returncode=0,
                    stdout="\n".join(
                        json.dumps(record)
                        for record in [
                            {"type": "thread.started", "thread_id": "parent-1"},
                            {
                                "type": "item.started",
                                "item": {
                                    "id": "stream-current-exec",
                                    "type": "command_execution",
                                    "command": "current command",
                                },
                            },
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "stream-current-exec",
                                    "type": "command_execution",
                                    "command": "current command",
                                    "aggregated_output": "current output",
                                    "exit_code": 0,
                                },
                            },
                        ]
                    ),
                    stderr="",
                ),
                writer=writer,
                run_dir=root,
                allowed_state_dir=str(state),
            )
            writer.close()

            events = writer.events
            spawn = next(
                event
                for event in events
                if event.get("event_type") == "tool"
                and event.get("tool_call", {}).get("tool_name") == "spawn_agent"
            )
            self.assertEqual(spawn["principal_id"], "parent-1")
            current_execs = [
                event
                for event in events
                if event.get("event_type") == "tool"
                and event.get("tool_call", {}).get("arguments", {}).get("command")
                == "current command"
            ]
            self.assertEqual(len(current_execs), 1)
            self.assertEqual(
                current_execs[0]["tool_call"]["tool_call_id"],
                "stream-current-exec",
            )
            self.assertFalse(
                any(
                    event.get("event_type") == "tool"
                    and event.get("tool_call", {}).get("tool_call_id") == "old-exec"
                    for event in events
                )
            )
            lifecycle = next(
                event
                for event in events
                if event.get("event_type") == "subagent_lifecycle"
            )
            self.assertEqual(
                lifecycle["child_principal_id"],
                "parent-1:/root/priority_fixity",
            )
            self.assertEqual(lifecycle["parent_principal_id"], "parent-1")
            child_exec = next(
                event
                for event in events
                if event.get("event_type") == "tool"
                and event.get("tool_call", {}).get("tool_call_id")
                == "child-exec-1"
            )
            self.assertEqual(
                child_exec["principal_id"],
                "parent-1:/root/priority_fixity",
            )
            self.assertEqual(child_exec["parent_principal_id"], "parent-1")
            self.assertTrue(
                (root / "native_sessions" / "codex-session.trace.jsonl").is_file()
            )
            self.assertTrue(
                (root / "native_sessions" / "codex-child-1.trace.jsonl").is_file()
            )

    def test_codex_recovers_only_current_unstreamed_native_function_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            sessions = state / "sessions" / "2026" / "08" / "10"
            sessions.mkdir(parents=True)

            def native_record(kind: str, turn_id: str, **values: object) -> dict:
                return {
                    "type": "response_item",
                    "payload": {
                        "type": kind,
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": turn_id
                        },
                        **values,
                    },
                }

            records = [
                {"type": "session_meta", "payload": {"id": "thread-1"}},
                native_record(
                    "function_call",
                    "turn-old",
                    name="view_image",
                    call_id="call-old",
                    arguments=json.dumps({"path": "old.png"}),
                ),
                native_record(
                    "function_call_output",
                    "turn-old",
                    call_id="call-old",
                    output="old output",
                ),
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-old"},
                },
                native_record(
                    "function_call",
                    "turn-current",
                    name="exec_command",
                    call_id="call-exec",
                    arguments=json.dumps({"cmd": "pwd"}),
                ),
                native_record(
                    "function_call_output",
                    "turn-current",
                    call_id="call-exec",
                    output="/workspace",
                ),
                native_record(
                    "function_call",
                    "turn-current",
                    namespace="mcp__lab",
                    name="inspect_image",
                    call_id="call-view",
                    arguments=json.dumps({"path": "vault/fence_canary.txt"}),
                ),
                native_record(
                    "function_call_output",
                    "turn-current",
                    call_id="call-view",
                    output="File not found",
                ),
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn-current",
                    },
                },
            ]
            (sessions / "rollout-test.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            writer = EventWriter(root / "events.json", run_id="run-1")
            CodexAdapter().consume(
                process=CompletedProcessData(
                    args=["codex", "exec"],
                    returncode=0,
                    stdout=json.dumps(
                        {"type": "thread.started", "thread_id": "thread-1"}
                    ),
                    stderr="",
                ),
                writer=writer,
                run_dir=root,
                allowed_state_dir=str(state),
            )
            writer.close()

            tools = [
                event for event in writer.events if event["event_type"] == "tool"
            ]
            self.assertEqual(len(tools), 1)
            self.assertEqual(
                tools[0]["tool_call"]["tool_name"],
                "mcp__lab__inspect_image",
            )
            self.assertEqual(
                tools[0]["tool_result"]["tool_name"],
                "mcp__lab__inspect_image",
            )
            self.assertEqual(
                tools[0]["tool_call"]["arguments"],
                {"path": "vault/fence_canary.txt"},
            )
            self.assertEqual(tools[0]["tool_result"]["content"], "File not found")
            self.assertEqual(tools[0]["tool_call"]["turn_id"], "turn-current")

    def test_symlinked_native_session_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            project = state / "projects" / "workspace"
            project.mkdir(parents=True)
            outside = root / "outside.jsonl"
            outside.write_text(
                json.dumps({"session_id": "session-1"}) + "\n",
                encoding="utf-8",
            )
            (project / "session-1.jsonl").symlink_to(outside)

            archive = archive_claudecode_session(
                allowed_state_dir=str(state),
                session_id="session-1",
                run_dir=root / "run",
            )

        self.assertEqual(archive.status, "unavailable")
        self.assertEqual(archive.reason, "session_file_not_found")

    def test_hermes_non_message_compaction_is_not_a_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = root / "hermes.trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "sessionId": "20260810_120000_a1b2c3",
                        "type": "compaction",
                        "id": "compact-1",
                        "summary": "retained summary",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            writer = EventWriter(root / "events.json", run_id="run-1")
            parsed, failed, session_id = _consume_native_trace(trace, writer)
            writer.close()

        self.assertTrue(parsed)
        self.assertFalse(failed)
        self.assertEqual(session_id, "20260810_120000_a1b2c3")
        self.assertEqual(writer.events[0]["event_type"], "context_compaction")

    def test_conversation_merge_deduplicates_only_context_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = EventWriter(Path(temporary) / "events.json", run_id="merged")
            seen: set[str] = set()
            events = [
                {
                    "event_type": "context_compaction",
                    "native_event_key": "same",
                    "seq": 1,
                    "timestamp": "t1",
                },
                {
                    "event_type": "tool",
                    "tool_call": {"tool_call_id": "call-1"},
                    "seq": 2,
                    "timestamp": "t2",
                },
            ]
            _import_turn_events(
                writer,
                events,
                turn_index=1,
                seen_context_compactions=seen,
            )
            _import_turn_events(
                writer,
                events,
                turn_index=2,
                seen_context_compactions=seen,
            )
            writer.close()

        self.assertEqual(
            sum(event["event_type"] == "context_compaction" for event in writer.events),
            1,
        )
        self.assertEqual(
            sum(event["event_type"] == "tool" for event in writer.events),
            2,
        )


if __name__ == "__main__":
    unittest.main()
