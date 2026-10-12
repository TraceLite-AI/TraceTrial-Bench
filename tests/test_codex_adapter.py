from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path

from harness_tracer.adapters.codex import CodexAdapter
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


class CodexCommandNormalizationTests(unittest.TestCase):
    def test_config_extra_arg_reaches_native_cli(self) -> None:
        command = CodexAdapter().build_command(
            model="model-1",
            input_query="prompt",
            run_dir=Path("run"),
            harness_path=None,
            extra_args={"config": 'model_reasoning_effort="high"'},
        )

        self.assertIn("--config", command)
        self.assertIn('model_reasoning_effort="high"', command)

    def _consume_tool(
        self,
        command: str,
        *,
        item_type: str = "command_execution",
        arguments: dict[str, object] | None = None,
        item_fields: dict[str, object] | None = None,
    ) -> dict:
        item = {
            "id": "item-command-1",
            "type": item_type,
            "command": command,
            "aggregated_output": "ok",
            "exit_code": 0,
            "status": "completed",
        }
        if arguments is not None:
            item["arguments"] = arguments
        if item_fields is not None:
            item.update(item_fields)
        started = dict(item)
        started["aggregated_output"] = ""
        started["exit_code"] = None
        started["status"] = "in_progress"
        stdout = "\n".join(
            json.dumps(payload)
            for payload in (
                {"type": "thread.started", "thread_id": "thread-1"},
                {"type": "item.started", "item": started},
                {"type": "item.completed", "item": item},
            )
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EventWriter(root / "events.json", run_id="run-1")
            try:
                result = CodexAdapter().consume(
                    process=CompletedProcessData(
                        args=["codex", "exec"],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                    ),
                    writer=writer,
                    run_dir=root,
                )
            finally:
                writer.close()
            events = json.loads((root / "events.json").read_text(encoding="utf-8"))

        self.assertTrue(result.trace_complete)
        return next(event for event in events if event.get("event_type") == "tool")

    @staticmethod
    def _executor_wrap(agent_command: str) -> str:
        return shlex.join(["/usr/bin/bash", "-lc", agent_command])

    def test_removes_one_codex_executor_shell_from_direct_command(self) -> None:
        native = self._executor_wrap("bin/ticketctl export")

        event = self._consume_tool(native)

        self.assertEqual(
            event["tool_call"]["arguments"]["command"],
            "bin/ticketctl export",
        )
        self.assertEqual(
            event["raw_event"]["tool_call"]["item"]["command"],
            native,
        )

    def test_agent_authored_shell_wrapper_remains_visible(self) -> None:
        agent_command = shlex.join(
            ["bash", "-lc", "bin/ticketctl export | head -n 1"]
        )

        event = self._consume_tool(self._executor_wrap(agent_command))

        self.assertEqual(
            event["tool_call"]["arguments"]["command"],
            agent_command,
        )

    def test_collaboration_spawn_preserves_native_lineage_arguments(self) -> None:
        event = self._consume_tool(
            "",
            item_type="collab_tool_call",
            item_fields={
                "tool": "spawn_agent",
                "prompt": "measure",
                "sender_thread_id": "parent-1",
                "receiver_thread_ids": ["child-1"],
                "agents_states": {"child-1": {"status": "completed"}},
            },
        )

        self.assertEqual(event["tool_call"]["tool_name"], "collaboration__spawn_agent")
        self.assertEqual(
            event["tool_call"]["arguments"]["sender_thread_id"], "parent-1"
        )
        self.assertEqual(
            event["tool_result"]["structured_output"]["receiver_thread_ids"],
            ["child-1"],
        )
    def test_complex_shell_syntax_is_preserved_exactly(self) -> None:
        agent_command = (
            "for value in 1 2; do printf '%s\\n' \"$value\" | sed -n '1p'; done\n"
            "cat > output/result.txt <<'EOF'\ncomplete\nEOF"
        )

        event = self._consume_tool(self._executor_wrap(agent_command))

        self.assertEqual(
            event["tool_call"]["arguments"]["command"],
            agent_command,
        )

    def test_structured_arguments_are_copied_and_normalized(self) -> None:
        native = self._executor_wrap("bin/ticketctl export")

        event = self._consume_tool(
            native,
            arguments={"command": native, "timeout_ms": 30000},
        )

        self.assertEqual(
            event["tool_call"]["arguments"],
            {"command": "bin/ticketctl export", "timeout_ms": 30000},
        )

    def test_empty_structured_arguments_fall_back_to_native_command(self) -> None:
        native = self._executor_wrap("bin/ticketctl export")

        event = self._consume_tool(native, arguments={})

        self.assertEqual(
            event["tool_call"]["arguments"]["command"],
            "bin/ticketctl export",
        )

    def test_unrecognized_or_malformed_wrapper_is_left_unchanged(self) -> None:
        values = (
            "/bin/bash -lc 'bin/ticketctl export'",
            "/usr/bin/bash -lc 'unterminated",
            "/usr/bin/bash -c 'bin/ticketctl export'",
        )
        for native in values:
            with self.subTest(native=native):
                event = self._consume_tool(native)
                self.assertEqual(
                    event["tool_call"]["arguments"]["command"],
                    native,
                )

    def test_non_command_tool_arguments_are_not_rewritten(self) -> None:
        native = self._executor_wrap("bin/ticketctl export")

        event = self._consume_tool(
            native,
            item_type="mcp_tool_call",
            arguments={"command": native},
        )

        self.assertEqual(
            event["tool_call"]["arguments"]["command"],
            native,
        )

    def test_command_item_uses_provider_visible_exec_name(self) -> None:
        event = self._consume_tool(self._executor_wrap("pwd"))

        self.assertEqual(event["tool_call"]["tool_name"], "exec_command")
        self.assertEqual(event["tool_result"]["tool_name"], "exec_command")

    def test_mcp_items_recover_builtin_and_external_names(self) -> None:
        builtin = self._consume_tool(
            "",
            item_type="mcp_tool_call",
            arguments={},
            item_fields={"server": "codex", "tool": "list_mcp_resources"},
        )
        external = self._consume_tool(
            "",
            item_type="mcp_tool_call",
            arguments={"label": "PQ-4101"},
            item_fields={"server": "harnessbench", "tool": "copper_lens"},
        )

        self.assertEqual(builtin["tool_call"]["tool_name"], "list_mcp_resources")
        self.assertEqual(
            external["tool_call"]["tool_name"],
            "mcp__harnessbench__copper_lens",
        )

    def test_collaboration_item_uses_flat_namespace_name(self) -> None:
        event = self._consume_tool(
            "",
            item_type="collab_tool_call",
            arguments={"task_name": "counter"},
            item_fields={"tool": "spawn_agent"},
        )

        self.assertEqual(
            event["tool_call"]["tool_name"],
            "collaboration__spawn_agent",
        )

    def test_turn_usage_preserves_cache_write_tokens(self) -> None:
        stdout = "\n".join(
            json.dumps(payload)
            for payload in (
                {"type": "thread.started", "thread_id": "thread-1"},
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 3,
                    },
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EventWriter(root / "events.json", run_id="run-1")
            try:
                CodexAdapter().consume(
                    process=CompletedProcessData(
                        args=["codex", "exec"],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                    ),
                    writer=writer,
                    run_dir=root,
                )
            finally:
                writer.close()
            events = json.loads(
                (root / "events.json").read_text(encoding="utf-8")
            )

        usage = next(
            event for event in events if event.get("event_type") == "token_usage"
        )
        self.assertEqual(usage["usage_scope"], "turn")
        self.assertEqual(usage["usage"]["cached_input_tokens"], 80)
        self.assertEqual(usage["usage"]["cache_write_input_tokens"], 5)

    def test_stream_line_observation_offsets_are_preserved(self) -> None:
        stdout = "\n".join(
            json.dumps(payload)
            for payload in (
                {"type": "thread.started", "thread_id": "thread-1"},
                {"type": "turn.started", "turn_id": "turn-1"},
                {
                    "type": "item.completed",
                    "item": {"id": "item-1", "type": "agent_message", "text": "hi"},
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EventWriter(root / "events.json", run_id="run-1")
            try:
                CodexAdapter().consume(
                    process=CompletedProcessData(
                        args=["codex", "exec"],
                        returncode=0,
                        stdout=stdout,
                        stderr="",
                        stdout_line_observed_ns=(
                            1_000_000_000,
                            1_100_000_000,
                            1_350_000_000,
                        ),
                    ),
                    writer=writer,
                    run_dir=root,
                )
            finally:
                writer.close()
            events = json.loads(
                (root / "events.json").read_text(encoding="utf-8")
            )

        model_input = next(
            event for event in events if event.get("event_type") == "model_input"
        )
        assistant = next(
            event
            for event in events
            if event.get("event_type") == "assistant_message"
        )
        self.assertEqual(model_input["stream_observed_offset_ms"], 100.0)
        self.assertEqual(assistant["stream_observed_offset_ms"], 350.0)


if __name__ == "__main__":
    unittest.main()
