from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracetrial_bench.conversation_runner import ConversationRunConfig, ConversationTraceRunner
from tracetrial_bench.deepseek_runner import (
    DeepSeekSdkBridge,
    DeepSeekBridgeTimeout,
    DeepSeekTraceRunner,
    patch_root_summary_containment,
)
from harness_tracer.runner import RunConfig


def _notification(
    session_id: str,
    event_type: str,
    seq: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "method": "session.event",
        "params": {
            "sessionId": session_id,
            "event": {
                "type": event_type,
                "seq": seq,
                "time": 1_000 + seq,
                "data": data,
                "surfaceOp": "append",
            },
        },
    }


def _successful_result(session_id: str, answer: str) -> dict[str, Any]:
    finish = {"kind": "completed"}
    return {
        "sessionId": session_id,
        "finalResponse": answer,
        "finishReason": finish,
        "notifications": [
            _notification(session_id, "turn/start", 0, {"turn": 1}),
            _notification(
                session_id,
                "assistant/message",
                1,
                {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "id": f"message-{answer}",
                        "role": "assistant",
                        "content": [{"type": "text", "text": answer}],
                    },
                    "usage": {"inputTokens": 2, "outputTokens": 1},
                },
            ),
            _notification(
                session_id,
                "turn/end",
                2,
                {"turn": 1, "reason": finish},
            ),
        ],
    }


def _containment_receipt(*, close_valid: bool = True) -> dict[str, object]:
    return {
        "schema_version": "harness-process-containment-v3",
        "valid": close_valid,
        "descendant_cleanup_valid": close_valid,
        "tracked_descendant_count": 2,
        "tracked_identity_sha256": "f" * 64,
        "residual_process_count": 0 if close_valid else 1,
        "tracking_failed": not close_valid,
        "pipe_drain_failed": False,
        "peak_rss_bytes": 1024,
        "rss_sample_count": 3,
        "rss_measurement_failed": False,
        "rss_limit_bytes": None,
        "rss_limit_exceeded": False,
        "sandbox_tag_scan_failed": False,
        "output_bytes_captured": 12,
        "output_limit_bytes": 4096,
        "output_limit_exceeded": False,
        "lifecycle_shutdown_valid": close_valid,
        "raw_pids_persisted": False,
        "raw_paths_persisted": False,
    }


class _FakeBridge:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        close_valid: bool = True,
        **kwargs: Any,
    ) -> None:
        self.launch = kwargs
        self.fail_on_call = fail_on_call
        self.close_valid = close_valid
        self.calls: list[tuple[str, str, int | None, bool]] = []
        self.receipt: dict[str, object] | None = None

    def run(
        self,
        input_query: str,
        *,
        session_id: str,
        timeout: int | None,
        wait_for_continuable_children: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            (input_query, session_id, timeout, wait_for_continuable_children)
        )
        if self.fail_on_call == len(self.calls):
            raise DeepSeekBridgeTimeout(
                "DeepSeek Harness turn timed out",
                notifications=[
                    _notification(session_id, "turn/start", 0, {"turn": 1})
                ],
            )
        return _successful_result(session_id, f"answer-{len(self.calls)}")

    def close(self) -> dict[str, object]:
        self.receipt = _containment_receipt(close_valid=self.close_valid)
        return self.receipt


class DeepSeekTraceRunnerTests(unittest.TestCase):
    def test_task_scoped_root_turn_end_returns_without_sdk_aggregate(self) -> None:
        session_id = "root-session"
        bridge = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
            terminal_event_return=True,
        )
        notifications = _successful_result(session_id, "done")["notifications"]
        result = bridge._terminal_event_result(
            method="run",
            params={"sessionId": session_id},
            notification=notifications[-1],
            notifications=notifications,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["finalResponse"], "done")
        self.assertEqual(result["completionSource"], "native_parent_turn_end")

    def test_bridge_uses_deepseek_specific_output_limit_by_default(self) -> None:
        bridge = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
        )
        self.assertEqual(bridge.max_output_bytes, 64_000_000)

        overridden = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
            max_output_bytes=1234,
        )
        self.assertEqual(overridden.max_output_bytes, 1234)

    def test_bridge_passes_explicit_output_token_limit_to_sdk(self) -> None:
        bridge = DeepSeekSdkBridge(
            environment={"DSH_CORDIS_CONFIG": "/tmp/cordis.yml"},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
            max_output_tokens=128_000,
        )

        with patch.object(bridge, "_start_process"), patch.object(
            bridge,
            "_rpc",
            return_value={"initialized": True},
        ) as rpc:
            bridge._ensure_started(None)

        self.assertEqual(rpc.call_args.args[1]["maxTokens"], 128_000)

    def test_trace_runner_accepts_task_scoped_output_limit_override(self) -> None:
        runner = DeepSeekTraceRunner(output_root=Path("output"))
        bridge = runner._bridge_for(
            RunConfig(
                harness="deepseek",
                model="model-1",
                input_query="test",
                output_dir=Path("output"),
                extra_args={
                    "harnessbench-deepseek-max-output-bytes": "192000000"
                },
            ),
            {},
        )
        self.assertEqual(bridge.max_output_bytes, 192_000_000)

    def test_trace_runner_accepts_task_scoped_output_token_limit_override(self) -> None:
        runner = DeepSeekTraceRunner(output_root=Path("output"))
        bridge = runner._bridge_for(
            RunConfig(
                harness="deepseek",
                model="model-1",
                input_query="test",
                output_dir=Path("output"),
                extra_args={
                    "harnessbench-deepseek-max-output-tokens": "128000"
                },
            ),
            {},
        )
        self.assertEqual(bridge.max_output_tokens, 128_000)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_node_bridge_waits_through_continuable_settlement_only_when_flagged(
        self,
    ) -> None:
        fake_sdk = r"""
export class DeepSeekHarness {
  constructor() {
    this.client = {
      subscribeSessionTree: sessionId => {
        const childId = 'child-1'
        const event = (type, seq, data) => ({
          method: 'session.event',
          params: { sessionId, event: { type, seq, data } },
        })
        const queue = [
          event('agent/inbox/spliced', 1, {
            inserted: [{ id: 'message-1', role: 'user', content: [] }],
          }),
          {
            method: 'subagent.started',
            params: { parentSessionId: sessionId, childSessionId: childId },
          },
          event('turn/end', 2, { turn: 1, reason: { kind: 'completed' } }),
          { method: 'session.status', params: { sessionId, status: 'idle' } },
          event('user/message', 3, {
            id: 'settlement',
            role: 'user',
            content: [
              { type: 'text', text: `Background subagent ${childId} finished.` },
              { type: 'text', text: 'Its closing message:' },
              { type: 'text', text: 'CHILD_AUDIT_COMPLETE {"sum":"1.00"}' },
            ],
            source: {
              kind: 'subagent-settled',
              form: 'notice',
              senderSessionId: childId,
            },
          }),
          {
            method: 'subagent.finished',
            params: { parentSessionId: sessionId, childSessionId: childId },
          },
          { method: 'session.status', params: { sessionId, status: 'running' } },
          event('assistant/message', 4, {
            turn: 2,
            step: 1,
            message: {
              role: 'assistant',
              content: [{ type: 'text', text: 'after settlement' }],
            },
          }),
          event('turn/end', 5, { turn: 2, reason: { kind: 'completed' } }),
          { method: 'session.status', params: { sessionId, status: 'idle' } },
        ]
        return {
          next: async () => queue.shift(),
          close: () => {},
        }
      },
      prompt: async () => 'message-1',
    }
  }
  async start() {}
  async run(_input, options) {
    return {
      sessionId: options.sessionId,
      finalResponse: 'ordinary path',
      events: [],
      notifications: [],
    }
  }
  async close() {}
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "fake-sdk.mjs"
            module.write_text(fake_sdk, encoding="utf-8")
            requests = [
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "runtimeCommand": "unused",
                        "runtimeArgs": [],
                        "cwd": str(root),
                        "env": {},
                        "provider": "test",
                        "model": "test",
                    },
                },
                {
                    "id": 2,
                    "method": "run",
                    "params": {
                        "input": "delegate",
                        "sessionId": "root-1",
                        "waitForContinuableChildren": True,
                    },
                },
                {
                    "id": 3,
                    "method": "run",
                    "params": {"input": "ordinary", "sessionId": "root-2"},
                },
                {"id": 4, "method": "close", "params": {}},
            ]
            completed = subprocess.run(
                ["node", str(Path("tracetrial_bench/deepseek_bridge.mjs").resolve())],
                input="".join(json.dumps(request) + "\n" for request in requests),
                text=True,
                capture_output=True,
                timeout=10,
                env={
                    **os.environ,
                    "HARNESSBENCH_DEEPSEEK_SDK_MODULE": module.as_uri(),
                },
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        continuable = next(
            response
            for response in responses
            if response.get("id") == 2 and response.get("ok") is True
        )["result"]
        ordinary = next(
            response
            for response in responses
            if response.get("id") == 3 and response.get("ok") is True
        )["result"]
        self.assertEqual(continuable["finalResponse"], "after settlement")
        self.assertEqual(continuable["finishReason"], {"kind": "completed"})
        self.assertEqual(
            [
                item["notification"]["params"]["status"]
                for item in responses
                if item.get("id") == 2
                and item.get("event") == "notification"
                and item["notification"]["method"] == "session.status"
                and item["notification"]["params"].get("sessionId") == "root-1"
            ],
            ["idle", "running", "idle"],
        )
        self.assertEqual(ordinary["finalResponse"], "ordinary path")

    def test_rpc_deadline_is_enforced_even_with_buffered_notifications(self) -> None:
        bridge = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
        )
        bridge.process = SimpleNamespace(
            stdin=io.BytesIO(),
            stdout=io.BytesIO(),
        )
        aborted: list[bool] = []
        bridge.abort = lambda: aborted.append(True) or {}

        with self.assertRaises(DeepSeekBridgeTimeout):
            bridge._rpc("run", {}, deadline=time.monotonic() - 1)

        self.assertEqual(aborted, [True])

    def test_rpc_discards_trailing_response_from_completed_request(self) -> None:
        bridge = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
        )
        bridge.request_id = 1
        bridge.process = SimpleNamespace(
            stdin=io.BytesIO(),
            stdout=io.BytesIO(),
        )
        bridge.stdout_buffer.extend(
            b'{"id":1,"ok":true,"result":{"stale":true}}\n'
            b'{"id":2,"ok":true,"result":{"current":true}}\n'
        )

        result = bridge._rpc("run", {}, deadline=time.monotonic() + 1)

        self.assertEqual(result, {"current": True})

    def test_bridge_forwards_task_scoped_continuable_collection_flag(self) -> None:
        bridge = DeepSeekSdkBridge(
            environment={},
            cwd=Path("."),
            model="model-1",
            output_root=Path("output"),
        )
        captured: list[tuple[str, dict[str, Any]]] = []
        bridge._ensure_started = lambda _deadline: None  # type: ignore[method-assign]
        bridge._rpc = (  # type: ignore[method-assign]
            lambda method, params, *, deadline: captured.append((method, params))
            or {"sessionId": params["sessionId"]}
        )

        bridge.run(
            "prompt",
            session_id="session-1",
            timeout=3,
            wait_for_continuable_children=True,
        )

        self.assertEqual(captured[0][0], "run")
        self.assertTrue(captured[0][1]["waitForContinuableChildren"])

    def _runner(
        self,
        root: Path,
        *,
        fail_on_call: int | None = None,
        close_valid: bool = True,
    ) -> tuple[DeepSeekTraceRunner, list[_FakeBridge]]:
        bridges: list[_FakeBridge] = []

        def factory(**kwargs: Any) -> _FakeBridge:
            bridge = _FakeBridge(
                fail_on_call=fail_on_call,
                close_valid=close_valid,
                **kwargs,
            )
            bridges.append(bridge)
            return bridge

        return (
            DeepSeekTraceRunner(output_root=root, bridge_factory=factory),
            bridges,
        )

    def test_conversation_reuses_one_sdk_runtime_and_native_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "output"
            workspace.mkdir()
            runner, bridges = self._runner(root)

            summary = ConversationTraceRunner(trace_runner=runner).run(
                ConversationRunConfig(
                    task_id="deepseek-conversation",
                    harness="deepseek",
                    model="deepseek-v4-flash",
                    initial_messages=("first", "second"),
                    output_dir=output,
                    max_turns=2,
                    cwd=workspace,
                    env={"DSH_CORDIS_CONFIG": "/hb-state/deepseek/cordis.yml"},
                    replace_environment=True,
                )
            )
            receipt = runner.close()
            patch_root_summary_containment(summary, receipt)
            persisted_root = json.loads(
                Path(str(summary["summary_path"])).read_text(encoding="utf-8")
            )
            turn_summary_paths = [
                Path(str(summary["summary_path"])).parent / relative
                for relative in summary["conversation"]["turn_summaries"]
            ]
            persisted_turns = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in turn_summary_paths
            ]

        self.assertEqual(len(bridges), 1)
        self.assertEqual([call[0] for call in bridges[0].calls], ["first", "second"])
        self.assertTrue(all(call[3] is False for call in bridges[0].calls))
        self.assertEqual(bridges[0].calls[0][1], bridges[0].calls[1][1])
        self.assertEqual(summary["conversation"]["native_session_ids"], [bridges[0].calls[0][1]])
        self.assertEqual(summary["final_answer"], "answer-2")
        self.assertEqual(persisted_root["process_containment"]["valid"], True)
        self.assertTrue(
            all(item["process_containment"]["valid"] is True for item in persisted_turns)
        )

    def test_continuable_collection_is_enabled_only_by_private_task_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            runner, bridges = self._runner(root)

            runner.run(
                RunConfig(
                    harness="deepseek",
                    model="deepseek-v4-flash",
                    input_query="delegate",
                    output_dir=root / "output",
                    cwd=workspace,
                    env={"DSH_CORDIS_CONFIG": "/hb-state/deepseek/cordis.yml"},
                    replace_environment=True,
                    extra_args={
                        "harnessbench-deepseek-continuable-subagents": "true"
                    },
                )
            )
            runner.close()

        self.assertEqual(len(bridges), 1)
        self.assertTrue(bridges[0].calls[0][3])

    def test_explicit_boundary_uses_new_session_in_same_sdk_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            runner, bridges = self._runner(root)

            summary = ConversationTraceRunner(trace_runner=runner).run(
                ConversationRunConfig(
                    task_id="deepseek-boundary",
                    harness="deepseek",
                    model="deepseek-v4-flash",
                    initial_messages=("one", "two"),
                    output_dir=root / "output",
                    max_turns=2,
                    new_session_before_turns=(2,),
                    cwd=workspace,
                    env={"DSH_CORDIS_CONFIG": "/hb-state/deepseek/cordis.yml"},
                    replace_environment=True,
                )
            )
            runner.close()

        self.assertEqual(len(bridges), 1)
        self.assertNotEqual(bridges[0].calls[0][1], bridges[0].calls[1][1])
        self.assertEqual(len(summary["conversation"]["native_session_ids"]), 2)

    def test_timeout_keeps_partial_native_trace_and_aligned_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            runner, _bridges = self._runner(root, fail_on_call=1)

            summary = runner.run(
                RunConfig(
                    harness="deepseek",
                    model="deepseek-v4-flash",
                    input_query="wait",
                    output_dir=root / "output",
                    timeout=3,
                    cwd=workspace,
                    env={"DSH_CORDIS_CONFIG": "/hb-state/deepseek/cordis.yml"},
                    replace_environment=True,
                )
            )
            runner.close()
            events = json.loads(
                Path(str(summary["events_path"])).read_text(encoding="utf-8")
            )
            archive = (
                Path(str(summary["events_path"])).parent
                / "native_sessions"
                / "deepseek-session.trace.jsonl"
            )
            archive_lines = archive.read_text(encoding="utf-8").splitlines()

        failure = next(event for event in events if event["event_type"] == "run_failed")
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["timed_out"])
        self.assertEqual(summary["error_type"], "timeout")
        self.assertEqual(failure["error_type"], "timeout")
        self.assertTrue(failure["timed_out"])
        self.assertEqual(failure["exit_code"], 124)
        self.assertEqual(len(archive_lines), 1)

    def test_close_handshake_failure_invalidates_persisted_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            runner, _bridges = self._runner(root, close_valid=False)
            summary = runner.run(
                RunConfig(
                    harness="deepseek",
                    model="deepseek-v4-flash",
                    input_query="finish",
                    output_dir=root / "output",
                    cwd=workspace,
                    env={"DSH_CORDIS_CONFIG": "/hb-state/deepseek/cordis.yml"},
                    replace_environment=True,
                )
            )
            runner.close()
            persisted = json.loads(
                Path(str(summary["summary_path"])).read_text(encoding="utf-8")
            )

        self.assertEqual(persisted["status"], "failed")
        self.assertFalse(persisted["trace_complete"])
        self.assertEqual(persisted["error_type"], "deepseek_sdk_close")


if __name__ == "__main__":
    unittest.main()
