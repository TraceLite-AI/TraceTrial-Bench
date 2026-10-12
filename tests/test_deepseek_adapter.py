from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_tracer.adapters import adapter_for
from harness_tracer.adapters.deepseek import DeepSeekAdapter
from harness_tracer.writer import EventWriter


def _event(
    session_id: str,
    event_type: str,
    seq: int,
    data: dict[str, object],
    *,
    surface_op: object | None = None,
) -> dict[str, object]:
    native: dict[str, object] = {
        "type": event_type,
        "seq": seq,
        "time": 1_000 + seq,
        "data": data,
    }
    if surface_op is not None:
        native["surfaceOp"] = surface_op
    return {
        "method": "session.event",
        "params": {"sessionId": session_id, "event": native},
    }


class DeepSeekAdapterTests(unittest.TestCase):
    def _consume(
        self,
        payload: dict[str, object],
        *,
        continuable_subagents: bool = False,
    ) -> tuple[object, list[dict[str, object]], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        writer = EventWriter(root / "events.json", run_id="run-deepseek")
        adapter = DeepSeekAdapter()
        if continuable_subagents:
            adapter.build_command(
                model="deepseek-v4-flash",
                input_query="prompt",
                run_dir=root,
                harness_path=None,
                extra_args={
                    "harnessbench-deepseek-continuable-subagents": "true"
                },
            )
        result = adapter.consume_result(
            payload,
            writer=writer,
            run_dir=root,
        )
        writer.close()
        return result, writer.events, root

    def test_maps_official_sdk_notifications_to_common_trace(self) -> None:
        root_session = "session-root"
        child_session = "session-child"
        notifications = [
            {
                "method": "subagent.started",
                "params": {
                    "parentSessionId": root_session,
                    "childSessionId": child_session,
                },
            },
            _event(root_session, "turn/start", 0, {"turn": 1}),
            _event(
                root_session,
                "user/message",
                1,
                {
                    "id": "message-user",
                    "role": "user",
                    "content": [{"type": "text", "text": "inspect"}],
                    "source": {"kind": "user"},
                },
                surface_op="append",
            ),
            _event(root_session, "step/start", 2, {"turn": 1, "step": 1}),
            _event(
                root_session,
                "request/header",
                3,
                {
                    "reason": "initial",
                    "header": {
                        "config": {
                            "provider": "deepseek-official",
                            "model": "deepseek-v4-flash",
                            "reasoningEffort": "max",
                        },
                        "adapterDefaults": {"reasoningEffort": "max"},
                        "system": "system prompt",
                        "tools": [
                            {
                                "name": "bash",
                                "description": "Run bash.",
                                "parameters": {"type": "object"},
                            }
                        ],
                    },
                },
            ),
            _event(
                root_session,
                "assistant/chunk",
                4,
                {
                    "turn": 1,
                    "step": 1,
                    "chunk": {"type": "reasoning-delta", "text": "think"},
                },
            ),
            _event(
                root_session,
                "assistant/chunk",
                5,
                {
                    "turn": 1,
                    "step": 1,
                    "chunk": {"type": "text-delta", "text": "working"},
                },
            ),
            _event(
                root_session,
                "assistant/chunk",
                6,
                {
                    "turn": 1,
                    "step": 1,
                    "chunk": {"type": "finish", "reason": {"kind": "tool-calls"}},
                },
            ),
            _event(
                root_session,
                "assistant/message",
                7,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "id": "message-assistant",
                        "role": "assistant",
                        "content": [
                            {"type": "reasoning", "text": "think"},
                            {"type": "text", "text": "working"},
                            {
                                "type": "tool-call",
                                "id": "call-1",
                                "name": "bash",
                                "arguments": '{"command":"pwd"}',
                            },
                        ],
                        "source": {
                            "kind": "model",
                            "provider": "deepseek-official",
                            "model": "deepseek-v4-flash",
                        },
                    },
                    "usage": {
                        "inputTokens": 12,
                        "cacheReadTokens": 3,
                        "cacheWriteTokens": 2,
                        "outputTokens": 8,
                        "reasoningTokens": 4,
                    },
                },
                surface_op="append",
            ),
            _event(
                root_session,
                "tool/call",
                8,
                {
                    "turn": 1,
                    "step": 1,
                    "callId": "call-1",
                    "name": "bash",
                    "arguments": '{"command":"pwd"}',
                },
            ),
            _event(
                root_session,
                "tool/result",
                9,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "id": "message-tool",
                        "role": "user",
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "call-1",
                                "content": [
                                    {"type": "text", "text": "/workspace"}
                                ],
                                "isError": False,
                            }
                        ],
                        "source": {"kind": "tool", "callId": "call-1"},
                    },
                    "meta": {"exitCode": 0},
                },
                surface_op="append",
            ),
            _event(
                root_session,
                "compaction/summary",
                10,
                {
                    "compactionId": "compact-1",
                    "summary": [{"type": "text", "text": "kept facts"}],
                    "shadowedRange": {"start": 1, "end": 9},
                    "shadowedSeqs": [1, 7, 9],
                    "shadowedTokenCount": 99,
                    "provider": "deepseek-official",
                    "model": "deepseek-v4-flash",
                },
            ),
            _event(
                root_session,
                "approval/asked",
                11,
                {"id": "approval-1", "toolName": "bash"},
            ),
            _event(
                child_session,
                "tool/call",
                0,
                {
                    "turn": 1,
                    "step": 1,
                    "callId": "child-call",
                    "name": "bash",
                    "arguments": '{"command":"ls"}',
                },
            ),
            _event(
                child_session,
                "tool/result",
                1,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "child-call",
                                "content": [{"type": "text", "text": "README.md"}],
                            }
                        ],
                    },
                },
                surface_op="append",
            ),
            _event(
                root_session,
                "turn/end",
                12,
                {"turn": 1, "reason": {"kind": "completed"}},
            ),
            {
                "method": "subagent.finished",
                "params": {
                    "provider": "spawn",
                    "agentId": child_session,
                    "parentSessionId": root_session,
                    "childSessionId": child_session,
                    "status": "ok",
                    "stopReason": {"kind": "completed"},
                    "lastAssistantMessage": [
                        {"type": "text", "text": "child done"}
                    ],
                },
            },
        ]

        result, events, run_dir = self._consume(
            {
                "sessionId": root_session,
                "finalResponse": "done",
                "finishReason": {"kind": "completed"},
                "notifications": notifications,
            }
        )

        self.assertIsInstance(adapter_for("dsh"), DeepSeekAdapter)
        self.assertEqual(result.status, "finished")
        self.assertTrue(result.trace_complete)
        self.assertEqual(result.structured_trace_status, "available")

        model_input = next(
            event for event in events if event["event_type"] == "model_input"
        )
        self.assertEqual(model_input["model"], "deepseek-v4-flash")
        self.assertEqual(model_input["system"], "system prompt")
        self.assertEqual(model_input["messages"][0]["id"], "message-user")
        self.assertEqual(model_input["tools"][0]["name"], "bash")

        assistants = [
            event for event in events if event["event_type"] == "assistant_message"
        ]
        self.assertEqual(
            [(event["content_kind"], event["content"]) for event in assistants],
            [("thinking", "think"), ("text", "working")],
        )
        self.assertEqual(assistants[0]["finish_reason"], "tool-calls")

        usage = next(event for event in events if event["event_type"] == "token_usage")
        self.assertEqual(usage["usage_scope"], "call")
        self.assertEqual(
            usage["usage"],
            {
                "input_tokens": 17,
                "cached_input_tokens": 3,
                "cache_write_input_tokens": 2,
                "cache_write_tokens": 2,
                "output_tokens": 8,
                "reasoning_output_tokens": 4,
            },
        )

        tools = [event for event in events if event["event_type"] == "tool"]
        root_tool = next(
            event
            for event in tools
            if event["tool_call"]["tool_call_id"] == "call-1"
        )
        self.assertEqual(root_tool["tool_call"]["arguments"], {"command": "pwd"})
        self.assertEqual(root_tool["tool_result"]["content"], "/workspace")
        self.assertEqual(root_tool["tool_result"]["metadata"], {"exitCode": 0})
        child_tool = next(
            event
            for event in tools
            if event["tool_call"]["tool_call_id"] == "child-call"
        )
        self.assertEqual(child_tool["principal_id"], child_session)
        self.assertEqual(child_tool["parent_principal_id"], root_session)
        self.assertEqual(child_tool["principal_role"], "child")

        compaction = next(
            event for event in events if event["event_type"] == "context_compaction"
        )
        self.assertEqual(compaction["summary"], "kept facts")
        self.assertTrue(compaction["notice_visible_to_model"])
        self.assertTrue(
            any(event["event_type"] == "policy_event" for event in events)
        )
        lifecycle = [
            event for event in events if event["event_type"] == "subagent_lifecycle"
        ]
        self.assertEqual([event["action"] for event in lifecycle], ["started", "finished"])
        self.assertFalse(
            any(event.get("boundary_kind") == "parent_turn_end" for event in events)
        )
        self.assertFalse(
            any(event.get("event_type") == "task_completion" for event in events)
        )

        archive = run_dir / "native_sessions" / "deepseek-session.trace.jsonl"
        self.assertTrue(archive.is_file())
        archived = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual(archived, notifications)

    def test_continuable_child_has_return_boundary_and_pushed_settlement(self) -> None:
        root_session = "session-root"
        child_session = "session-child"
        notifications = [
            _event(
                root_session,
                "tool/call",
                1,
                {
                    "turn": 1,
                    "step": 1,
                    "callId": "call-subagent",
                    "name": "subagent",
                    "arguments": json.dumps(
                        {
                            "description": "ledger-auditor",
                            "prompt": "run the audit",
                        }
                    ),
                },
            ),
            _event(
                root_session,
                "tool/result",
                2,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "call-subagent",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": f"started subagent {child_session}",
                                    }
                                ],
                            }
                        ],
                    },
                },
                surface_op="append",
            ),
            {
                "method": "subagent.started",
                "params": {
                    "provider": "spawn",
                    "parentSessionId": root_session,
                    "childSessionId": child_session,
                },
            },
            _event(
                root_session,
                "turn/end",
                3,
                {"turn": 1, "reason": {"kind": "completed"}},
            ),
            _event(
                child_session,
                "tool/call",
                1,
                {
                    "turn": 1,
                    "step": 1,
                    "callId": "child-call",
                    "name": "bash",
                    "arguments": '{"command":"sh tools/run_ledger_audit.sh"}',
                },
            ),
            _event(
                child_session,
                "tool/result",
                2,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "child-call",
                                "content": [{"type": "text", "text": "ok"}],
                            }
                        ],
                    },
                },
                surface_op="append",
            ),
            {
                "method": "subagent.finished",
                "params": {
                    "provider": "spawn",
                    "parentSessionId": root_session,
                    "childSessionId": child_session,
                    "status": "ok",
                    "stopReason": {"kind": "completed"},
                },
            },
            _event(
                root_session,
                "user/message",
                4,
                {
                    "id": "settlement-notice",
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Background subagent {child_session} finished and "
                                "will do no further work unless you send it more."
                            ),
                        },
                        {"type": "text", "text": "Its closing message:"},
                        {
                            "type": "text",
                            "text": "CHILD_AUDIT_COMPLETE {\"sum\":\"10.50\"}",
                        },
                    ],
                    "source": {
                        "kind": "subagent-settled",
                        "form": "notice",
                        "summary": f"Background subagent {child_session} finished.",
                        "senderSessionId": child_session,
                    },
                },
                surface_op="append",
            ),
            _event(
                root_session,
                "turn/end",
                5,
                {"turn": 2, "reason": {"kind": "completed"}},
            ),
        ]

        result, events, _run_dir = self._consume(
            {
                "sessionId": root_session,
                "finishReason": {"kind": "completed"},
                "notifications": notifications,
            },
            continuable_subagents=True,
        )

        self.assertEqual(result.status, "finished")
        boundary = next(
            event
            for event in events
            if event.get("action") == "returned"
            and event.get("boundary_kind") == "parent_turn_end"
        )
        completion = next(
            event for event in events if event["event_type"] == "task_completion"
        )
        self.assertLess(boundary["seq"], completion["seq"])
        self.assertEqual(boundary["pending_child_ids"], [child_session])
        self.assertEqual(completion["principal_id"], child_session)
        self.assertEqual(completion["parent_principal_id"], root_session)
        self.assertTrue(completion["parent_reactivated"])
        self.assertEqual(
            completion["content"],
            'CHILD_AUDIT_COMPLETE {"sum":"10.50"}',
        )
        self.assertEqual(
            sum(event.get("action") == "returned" for event in events), 1
        )

    def test_native_turn_error_is_failed_but_structured(self) -> None:
        result, events, _run_dir = self._consume(
            {
                "sessionId": "session-error",
                "notifications": [
                    _event(
                        "session-error",
                        "turn/end",
                        0,
                        {
                            "turn": 1,
                            "reason": {
                                "kind": "error",
                                "error": {
                                    "message": "provider failed",
                                    "code": "TRANSPORT",
                                },
                            },
                        },
                    )
                ],
            }
        )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.trace_complete)
        self.assertEqual(result.structured_trace_status, "available")
        self.assertEqual(result.error_type, "deepseek_turn_error")
        self.assertEqual(result.error_message, "provider failed")
        failure = next(event for event in events if event["event_type"] == "run_failed")
        self.assertEqual(failure["error_type"], "deepseek_turn_error")

    def test_incremental_turns_reconstruct_the_persistent_model_surface(self) -> None:
        session_id = "session-resumed"
        adapter = DeepSeekAdapter()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "first").mkdir()
        (root / "second").mkdir()

        first_writer = EventWriter(root / "first.json", run_id="run-first")
        adapter.consume_result(
            {
                "sessionId": session_id,
                "finishReason": {"kind": "completed"},
                "notifications": [
                    _event(
                        session_id,
                        "user/message",
                        1,
                        {
                            "id": "user-first",
                            "role": "user",
                            "content": [{"type": "text", "text": "first"}],
                        },
                        surface_op="append",
                    ),
                    _event(
                        session_id,
                        "request/header",
                        2,
                        {
                            "reason": "initial",
                            "header": {
                                "config": {
                                    "provider": "deepseek-official",
                                    "model": "deepseek-v4-flash",
                                },
                                "tools": [],
                            },
                        },
                    ),
                    _event(
                        session_id,
                        "assistant/message",
                        3,
                        {
                            "turn": 1,
                            "step": 1,
                            "message": {
                                "id": "assistant-first",
                                "role": "assistant",
                                "content": [{"type": "text", "text": "answer"}],
                            },
                        },
                        surface_op="append",
                    ),
                    _event(
                        session_id,
                        "turn/end",
                        4,
                        {"turn": 1, "reason": {"kind": "completed"}},
                    ),
                ],
            },
            writer=first_writer,
            run_dir=root / "first",
        )
        first_writer.close()

        second_writer = EventWriter(root / "second.json", run_id="run-second")
        adapter.consume_result(
            {
                "sessionId": session_id,
                "finishReason": {"kind": "completed"},
                "notifications": [
                    _event(
                        session_id,
                        "user/message",
                        5,
                        {
                            "id": "user-second",
                            "role": "user",
                            "content": [{"type": "text", "text": "second"}],
                        },
                        surface_op="append",
                    ),
                    _event(
                        session_id,
                        "assistant/chunk",
                        6,
                        {
                            "turn": 2,
                            "step": 1,
                            "chunk": {"type": "text-delta", "text": "next"},
                        },
                    ),
                    _event(
                        session_id,
                        "turn/end",
                        7,
                        {"turn": 2, "reason": {"kind": "completed"}},
                    ),
                ],
            },
            writer=second_writer,
            run_dir=root / "second",
        )
        second_writer.close()

        model_input = next(
            event
            for event in second_writer.events
            if event["event_type"] == "model_input"
        )
        self.assertEqual(
            [message["id"] for message in model_input["messages"]],
            ["user-first", "assistant-first", "user-second"],
        )

    def test_surface_replacement_and_empty_usage_message_match_runtime_projection(self) -> None:
        session_id = "session-compacted"
        notifications = [
            _event(
                session_id,
                "user/message",
                1,
                {
                    "id": "user-before",
                    "role": "user",
                    "content": [{"type": "text", "text": "before"}],
                },
                surface_op="append",
            ),
            _event(
                session_id,
                "assistant/message",
                2,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "id": "usage-only",
                        "role": "assistant",
                        "content": [],
                    },
                    "usage": {"inputTokens": 4, "outputTokens": 0},
                },
                surface_op="append",
            ),
            _event(
                session_id,
                "user/message",
                3,
                {
                    "id": "user-later",
                    "role": "user",
                    "content": [{"type": "text", "text": "later"}],
                },
                surface_op="append",
            ),
            _event(
                session_id,
                "request/header",
                4,
                {
                    "reason": "initial",
                    "header": {
                        "config": {
                            "provider": "deepseek-official",
                            "model": "deepseek-v4-flash",
                        },
                        "tools": [],
                    },
                },
            ),
            _event(
                session_id,
                "assistant/chunk",
                5,
                {
                    "turn": 1,
                    "step": 1,
                    "chunk": {"type": "text-delta", "text": "first call"},
                },
            ),
            _event(
                session_id,
                "compaction/summary",
                6,
                {
                    "compactionId": "compact-surface",
                    "summary": [{"type": "text", "text": "summary"}],
                    "shadowedRange": {"start": 1, "end": 3},
                    "shadowedSeqs": [1, 2, 3],
                    "shadowedTokenCount": 20,
                    "provider": "deepseek-official",
                    "model": "deepseek-v4-flash",
                    "usage": {"inputTokens": 7, "outputTokens": 2},
                },
            ),
            _event(
                session_id,
                "user/message",
                7,
                {
                    "id": "compaction-checkpoint",
                    "role": "user",
                    "content": [{"type": "text", "text": "summary"}],
                },
                surface_op={"op": "replace", "start": 1, "end": 3},
            ),
            _event(
                session_id,
                "assistant/chunk",
                8,
                {
                    "turn": 1,
                    "step": 2,
                    "chunk": {"type": "text-delta", "text": "second call"},
                },
            ),
            _event(
                session_id,
                "turn/end",
                9,
                {"turn": 1, "reason": {"kind": "completed"}},
            ),
        ]

        result, events, _run_dir = self._consume(
            {
                "sessionId": session_id,
                "finishReason": {"kind": "completed"},
                "notifications": notifications,
            }
        )

        self.assertEqual(result.status, "finished")
        model_inputs = [
            event for event in events if event["event_type"] == "model_input"
        ]
        self.assertEqual(
            [message["id"] for message in model_inputs[0]["messages"]],
            ["user-before", "user-later"],
        )
        self.assertEqual(
            [message["id"] for message in model_inputs[1]["messages"]],
            ["compaction-checkpoint"],
        )
        compaction_usage = next(
            event
            for event in events
            if event.get("source") == "deepseek.compaction_summary"
        )
        self.assertEqual(compaction_usage["usage_scope"], "internal")
        self.assertEqual(compaction_usage["usage"]["input_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
