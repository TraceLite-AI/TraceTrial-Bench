from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracetrial_bench.conversation_runner import (
    ConversationRunConfig,
    ConversationSimulator,
    ConversationTraceRunner,
    _turn_extra_args,
)
from tracetrial_bench.runner import BenchmarkRunConfig, BenchmarkRunner
from tracetrial_bench.task_resolver import read_fixed_turns, resolve_task
from harness_tracer.adapters.codex import CodexAdapter


class _SessionTraceRunner:
    """Small native-session double used to exercise conversation boundaries."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.session_count = 0

    def run(self, config: object) -> dict[str, object]:
        extra_args = dict(getattr(config, "extra_args"))
        resumed = extra_args.get("harnessbench-session-id")
        if resumed:
            session_id = resumed
        else:
            self.session_count += 1
            session_id = f"native-session-{self.session_count}"
        self.calls.append(
            {
                "input": str(getattr(config, "input_query")),
                "session_id": session_id,
                "resumed": resumed or "",
            }
        )
        output_dir = Path(getattr(config, "output_dir"))
        turn_dir = output_dir / f"stub-{len(self.calls)}"
        turn_dir.mkdir(parents=True)
        events_path = turn_dir / "events.json"
        summary_path = turn_dir / "summary.json"
        events_path.write_text(
            json.dumps(
                [
                    {
                        "event_type": "run_started",
                        "session_id": session_id,
                    },
                    {
                        "event_type": "assistant_message",
                        "session_id": session_id,
                        "content": f"answer {len(self.calls)}",
                    },
                ]
            ),
            encoding="utf-8",
        )
        summary = {
            "status": "finished",
            "trace_complete": True,
            "structured_trace_status": "available",
            "events_path": str(events_path),
            "summary_path": str(summary_path),
            "final_answer": f"answer {len(self.calls)}",
            "argv": [],
            "environment": {},
            "exit_code": 0,
            "timed_out": False,
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return summary


class _CumulativeSessionTraceRunner:
    """Return full native history on resume, as Hermes-style exports do."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, config: object) -> dict[str, object]:
        self.calls += 1
        output_dir = Path(getattr(config, "output_dir"))
        turn_dir = output_dir / f"cumulative-{self.calls}"
        turn_dir.mkdir(parents=True)
        events_path = turn_dir / "events.json"
        summary_path = turn_dir / "summary.json"
        events: list[dict[str, object]] = []
        for index in range(1, self.calls + 1):
            events.extend(
                [
                    {
                        "event_type": "tool",
                        "tool_call": {
                            "tool_call_id": f"call-{index}",
                            "tool_name": "delegate_task",
                            "arguments": {"angle": f"A{index}"},
                        },
                        "tool_result": {"content": f"result-{index}"},
                    },
                    {
                        "event_type": "assistant_message",
                        "message_id": f"message-{index}",
                        "content": f"answer-{index}",
                    },
                ]
            )
        events_path.write_text(json.dumps(events), encoding="utf-8")
        summary = {
            "status": "finished",
            "trace_complete": True,
            "structured_trace_status": "available",
            "events_path": str(events_path),
            "summary_path": str(summary_path),
            "final_answer": f"answer-{self.calls}",
            "argv": [],
            "environment": {},
            "exit_code": 0,
            "timed_out": False,
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return summary


class _CollidingToolIdTraceRunner:
    """Mimic adapters that restart fallback item numbering every turn."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, config: object) -> dict[str, object]:
        self.calls += 1
        output_dir = Path(getattr(config, "output_dir"))
        turn_dir = output_dir / f"collision-{self.calls}"
        turn_dir.mkdir(parents=True)
        events_path = turn_dir / "events.json"
        summary_path = turn_dir / "summary.json"
        events_path.write_text(
            json.dumps(
                [
                    {
                        "event_type": "tool",
                        "tool_call": {
                            "tool_call_id": "item-1",
                            "tool_name": "exec_command",
                            "arguments": {"command": f"turn-{self.calls}"},
                        },
                        "tool_result": {
                            "tool_call_id": "item-1",
                            "content": f"result-{self.calls}",
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )
        summary = {
            "status": "finished",
            "trace_complete": True,
            "structured_trace_status": "available",
            "events_path": str(events_path),
            "summary_path": str(summary_path),
            "final_answer": f"answer-{self.calls}",
            "argv": [],
            "environment": {},
            "exit_code": 0,
            "timed_out": False,
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        return summary


class ConversationTaskResolutionTests(unittest.TestCase):
    def _task(self, root: Path, profile: dict, *, turns: object | None = None) -> Path:
        task = root / "task"
        (task / "workspace").mkdir(parents=True)
        (task / "runtime").mkdir()
        (task / "prompt.md").write_text("initial", encoding="utf-8")
        (task / "task.yaml").write_text(
            "id: conversation-test\n"
            "budget:\n"
            "  max_turns: 4\n"
            "execution:\n"
            "  profile: runtime/execution.json\n",
            encoding="utf-8",
        )
        (task / "runtime" / "execution.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )
        if turns is not None:
            (task / "turns.json").write_text(json.dumps(turns), encoding="utf-8")
        return task

    def test_missing_conversation_declaration_preserves_single_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = resolve_task(self._task(Path(temporary), {}))

        self.assertEqual(task.conversation.mode, "single_turn")

    def test_multi_turn_profile_loads_turns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = resolve_task(
                self._task(
                    Path(temporary),
                    {"conversation": "multi_turn"},
                    turns={"turns": ["first", "second"]},
                )
            )
            turns = read_fixed_turns(task.conversation)

        self.assertEqual(task.conversation.mode, "fixed_turns")
        self.assertEqual(turns, ["first", "second"])
        self.assertEqual(task.conversation.new_session_before_turns, ())

    def test_fixed_turn_profile_loads_explicit_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = resolve_task(
                self._task(
                    Path(temporary),
                    {
                        "conversation": {
                            "mode": "fixed_turns",
                            "turns": "turns.json",
                            "new_session_before_turns": [3],
                            "new_session_context": "replay",
                        }
                    },
                    turns={"turns": ["first", "second", "third"]},
                )
            )

        self.assertEqual(task.conversation.new_session_before_turns, (3,))
        self.assertEqual(task.conversation.new_session_context, "replay")

    def test_replay_context_requires_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = self._task(
                Path(temporary),
                {
                    "conversation": {
                        "mode": "fixed_turns",
                        "new_session_context": "replay",
                    }
                },
                turns={"turns": ["first", "second"]},
            )
            with self.assertRaisesRegex(ValueError, "requires new_session"):
                resolve_task(task)

    def test_session_boundary_rejects_turn_one_and_out_of_range(self) -> None:
        for boundary in ([1], [4]):
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as temporary:
                    task = self._task(
                        Path(temporary),
                        {
                            "conversation": {
                                "mode": "fixed_turns",
                                "new_session_before_turns": boundary,
                            }
                        },
                        turns={"turns": ["first", "second", "third"]},
                    )
                    with self.assertRaisesRegex(ValueError, "between 2"):
                        resolve_task(task)

    def test_fixed_turns_must_fit_task_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = self._task(
                Path(temporary),
                {"conversation": "multi_turn"},
                turns={"turns": ["1", "2", "3", "4", "5"]},
            )
            with self.assertRaisesRegex(ValueError, "exceeding budget"):
                resolve_task(task)

    def test_simulator_profile_resolves_private_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = self._task(
                Path(temporary),
                {
                    "conversation": {
                        "mode": "simulator",
                        "entrypoint": "simulator/next_turn.py",
                        "max_turns": 3,
                    }
                },
            )
            (task_path / "simulator").mkdir()
            entrypoint = task_path / "simulator" / "next_turn.py"
            entrypoint.write_text("print('{}')\n", encoding="utf-8")

            task = resolve_task(task_path)

        self.assertEqual(task.conversation.mode, "simulator")
        self.assertEqual(task.conversation.max_turns, 3)
        self.assertEqual(task.conversation.simulator_entrypoint, entrypoint.resolve())


class ConversationTraceRunnerTests(unittest.TestCase):
    def test_fixed_turns_run_after_each_agent_completion_and_merge_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()

            summary = ConversationTraceRunner().run(
                ConversationRunConfig(
                    task_id="fixed-test",
                    harness="fake",
                    model="fake-model",
                    initial_messages=("first", "second", "final"),
                    output_dir=output,
                    max_turns=3,
                    cwd=workspace,
                )
            )
            events = json.loads(Path(summary["events_path"]).read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], "finished")
        self.assertEqual(summary["conversation"]["turns_completed"], 3)
        self.assertEqual(summary["final_answer"], "fake response to: final")
        self.assertEqual(
            [
                event["input"]
                for event in events
                if event["event_type"] == "conversation_turn_started"
            ],
            ["first", "second", "final"],
        )

    def test_simulator_receives_trace_history_and_generates_next_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            simulator_path = root / "simulator.py"
            simulator_path.write_text(
                "import json, sys\n"
                "request = json.load(sys.stdin)\n"
                "assistant = [m['content'] for m in request['history'] "
                "if m['role'] == 'assistant']\n"
                "assert assistant\n"
                "assert request['trace']['events']\n"
                "done = request['turn_index'] >= 2\n"
                "print(json.dumps({'done': done, 'message': None if done else 'follow-up'}))\n",
                encoding="utf-8",
            )

            summary = ConversationTraceRunner().run(
                ConversationRunConfig(
                    task_id="simulator-test",
                    harness="fake",
                    model="fake-model",
                    initial_messages=("initial",),
                    output_dir=output,
                    max_turns=4,
                    cwd=workspace,
                    simulator=ConversationSimulator(simulator_path),
                )
            )

        self.assertEqual(summary["status"], "finished")
        self.assertEqual(summary["conversation"]["turns_completed"], 2)
        self.assertEqual(summary["final_answer"], "fake response to: follow-up")

    def test_explicit_boundary_starts_one_new_native_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            trace_runner = _SessionTraceRunner()

            summary = ConversationTraceRunner(trace_runner=trace_runner).run(
                ConversationRunConfig(
                    task_id="session-boundary-test",
                    harness="codex",
                    model="fake-model",
                    initial_messages=("one", "two", "three", "four"),
                    output_dir=output,
                    max_turns=4,
                    new_session_before_turns=(3,),
                    cwd=workspace,
                )
            )
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )

        self.assertEqual(
            [call["resumed"] for call in trace_runner.calls],
            ["", "native-session-1", "", "native-session-2"],
        )
        self.assertEqual(
            summary["conversation"]["native_session_ids"],
            ["native-session-1", "native-session-2"],
        )
        self.assertEqual(summary["conversation"]["new_session_before_turns"], [3])
        boundaries = [
            event for event in events if event["event_type"] == "session_boundary"
        ]
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["before_turn"], 3)
        self.assertEqual(boundaries[0]["previous_session_id"], "native-session-1")
        self.assertEqual(boundaries[0]["session_id"], "native-session-2")
        self.assertTrue(boundaries[0]["new_session"])
        self.assertFalse(boundaries[0]["history_replayed"])

    def test_default_fixed_turns_never_create_a_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            trace_runner = _SessionTraceRunner()

            summary = ConversationTraceRunner(trace_runner=trace_runner).run(
                ConversationRunConfig(
                    task_id="ordinary-fixed-test",
                    harness="codex",
                    model="fake-model",
                    initial_messages=("one", "two", "three"),
                    output_dir=output,
                    max_turns=3,
                    cwd=workspace,
                )
            )
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )

        self.assertEqual(
            [call["resumed"] for call in trace_runner.calls],
            ["", "native-session-1", "native-session-1"],
        )
        self.assertFalse(
            any(event["event_type"] == "session_boundary" for event in events)
        )

    def test_explicit_boundary_can_replay_visible_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()
            trace_runner = _SessionTraceRunner()

            summary = ConversationTraceRunner(trace_runner=trace_runner).run(
                ConversationRunConfig(
                    task_id="session-replay-test",
                    harness="codex",
                    model="fake-model",
                    initial_messages=("one", "two", "three"),
                    output_dir=output,
                    max_turns=3,
                    new_session_before_turns=(3,),
                    new_session_context="replay",
                    cwd=workspace,
                )
            )
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )

        replayed_input = trace_runner.calls[2]["input"]
        self.assertIn("<harness_resume_context>", replayed_input)
        self.assertIn('"role":"user","content":"one"', replayed_input)
        self.assertIn('"role":"assistant","content":"answer 2"', replayed_input)
        self.assertIn("<current_user_message>\nthree", replayed_input)
        handoffs = [
            event for event in events if event["event_type"] == "context_handoff"
        ]
        self.assertEqual(len(handoffs), 1)
        self.assertTrue(handoffs[0]["model_visible"])
        self.assertEqual(handoffs[0]["history_message_count"], 4)
        boundaries = [
            event for event in events if event["event_type"] == "session_boundary"
        ]
        self.assertTrue(boundaries[0]["history_replayed"])
        self.assertEqual(
            boundaries[0]["history_sha256"], handoffs[0]["history_sha256"]
        )
        self.assertEqual(summary["conversation"]["new_session_context"], "replay")

    def test_resumed_full_session_export_deduplicates_native_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()

            summary = ConversationTraceRunner(
                trace_runner=_CumulativeSessionTraceRunner()
            ).run(
                ConversationRunConfig(
                    task_id="cumulative-session-test",
                    harness="hermes",
                    model="fake-model",
                    initial_messages=("one", "two", "three"),
                    output_dir=output,
                    max_turns=3,
                    cwd=workspace,
                )
            )
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )

        tool_ids = [
            event["tool_call"]["tool_call_id"]
            for event in events
            if event["event_type"] == "tool"
        ]
        message_ids = [
            event["message_id"]
            for event in events
            if event["event_type"] == "assistant_message"
        ]
        self.assertEqual(tool_ids, ["call-1", "call-2", "call-3"])
        self.assertEqual(message_ids, ["message-1", "message-2", "message-3"])

    def test_reused_fallback_tool_ids_are_namespaced_by_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            output.mkdir()

            summary = ConversationTraceRunner(
                trace_runner=_CollidingToolIdTraceRunner()
            ).run(
                ConversationRunConfig(
                    task_id="tool-id-collision-test",
                    harness="codex",
                    model="fake-model",
                    initial_messages=("one", "two", "three"),
                    output_dir=output,
                    max_turns=3,
                    cwd=workspace,
                )
            )
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )

        tools = [event for event in events if event["event_type"] == "tool"]
        tool_ids = [event["tool_call"]["tool_call_id"] for event in tools]
        result_ids = [event["tool_result"]["tool_call_id"] for event in tools]
        self.assertEqual(
            tool_ids,
            [
                "item-1",
                "item-1@conversation-turn-2",
                "item-1@conversation-turn-3",
            ],
        )
        self.assertEqual(result_ids, tool_ids)
        self.assertEqual(tools[1]["tool_call"]["native_tool_call_id"], "item-1")

    def test_default_benchmark_runner_selects_fixed_turn_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_path = root / "task"
            (task_path / "workspace").mkdir(parents=True)
            (task_path / "runtime").mkdir()
            (task_path / "verifier").mkdir()
            (task_path / "prompt.md").write_text("first", encoding="utf-8")
            (task_path / "task.yaml").write_text(
                "id: runner-fixed-test\n"
                "budget:\n"
                "  max_turns: 3\n"
                "verifier:\n"
                "  entrypoint: verifier/run.sh\n"
                "execution:\n"
                "  profile: runtime/execution.json\n",
                encoding="utf-8",
            )
            (task_path / "runtime" / "execution.json").write_text(
                json.dumps({"conversation": "multi_turn"}), encoding="utf-8"
            )
            (task_path / "turns.json").write_text(
                json.dumps({"turns": ["first", "final"]}), encoding="utf-8"
            )
            (task_path / "verifier" / "run.sh").write_text(
                "#!/bin/sh\nprintf '{\"pass\": true, \"score\": 1.0}\\n'\n",
                encoding="utf-8",
            )

            result = BenchmarkRunner().run(
                BenchmarkRunConfig(
                    task=task_path,
                    harness="fake",
                    model="fake-model",
                    mode="local",
                    output_dir=root / "runs",
                )
            )
            run_dir = Path(result["result_path"]).parent
            trace_summary_path = Path(result["artifacts"]["trace_summary"])
            if not trace_summary_path.is_absolute():
                trace_summary_path = run_dir / trace_summary_path
            trace_summary = json.loads(
                trace_summary_path.read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(trace_summary["conversation"]["turns_completed"], 2)
        self.assertEqual(trace_summary["final_answer"], "fake response to: final")


class ConversationAdapterCommandTests(unittest.TestCase):
    def test_openclaw_new_conversation_segment_gets_a_distinct_session_key(self) -> None:
        first = _turn_extra_args(
            harness="openclaw",
            base={},
            conversation_id="segment-one",
            native_session_id=None,
            remaining_timeout=60,
        )
        second = _turn_extra_args(
            harness="openclaw",
            base={},
            conversation_id="segment-two",
            native_session_id=None,
            remaining_timeout=60,
        )

        self.assertEqual(first["session-key"], "agent:main:segment-one")
        self.assertEqual(second["session-key"], "agent:main:segment-two")
        self.assertNotEqual(first["session-key"], second["session-key"])

    def test_codex_initial_conversation_is_persisted_and_followup_resumes(self) -> None:
        adapter = CodexAdapter()
        initial = adapter.build_command(
            model="model-1",
            input_query="initial",
            run_dir=Path("run-1"),
            harness_path=None,
            extra_args={"harnessbench-conversation": "true"},
        )
        followup = adapter.build_command(
            model="model-1",
            input_query="follow up",
            run_dir=Path("run-2"),
            harness_path=None,
            extra_args={
                "harnessbench-conversation": "true",
                "harnessbench-session-id": "thread-1",
            },
        )

        self.assertNotIn("--ephemeral", initial)
        self.assertEqual(followup[0:3], ["codex", "exec", "resume"])
        self.assertIn("thread-1", followup)
        self.assertNotIn("--harnessbench-session-id", followup)


if __name__ == "__main__":
    unittest.main()
