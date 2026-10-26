from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracetrial_bench.trace_report import generate_trace_report


class TraceReportTokenTests(unittest.TestCase):
    def test_report_includes_aggregate_tokens_cache_hit_rate_and_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "events.json").write_text("[]", encoding="utf-8")
            result = {
                "task_id": "token-report",
                "metrics": {
                    "step_count": 2,
                    "timing_metrics": {
                        "ttft_ms": 250.0,
                        "output_throughput_tokens_per_sec": 20.0,
                        "end_to_end_duration_ms": 5_000.0,
                    },
                    "token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_input_tokens": 80,
                        "total_tokens": 120,
                        "cache_hit_rate": 0.8,
                    },
                    "total_cost": {
                        "currency": "USD",
                        "amount": "0.001",
                    },
                },
                "artifacts": {"trace_events": "events.json"},
            }

            report_path = generate_trace_report(run_dir, result=result)
            html = report_path.read_text(encoding="utf-8")
            persisted = json.loads((run_dir / "result.json").read_text())

        self.assertIn("Total input tokens", html)
        self.assertIn("Model steps", html)
        self.assertIn("Total runtime", html)
        self.assertIn("TTFT", html)
        self.assertIn("Output throughput", html)
        self.assertIn("Total output tokens", html)
        self.assertIn("Total cached tokens", html)
        self.assertIn("Cache hit rate", html)
        self.assertIn("Total cost", html)
        self.assertEqual(persisted["metrics"]["token_usage"]["total_tokens"], 120)
        self.assertEqual(persisted["metrics"]["step_count"], 2)
        self.assertEqual(
            persisted["metrics"]["timing_metrics"]["end_to_end_duration_ms"],
            5_000.0,
        )
        self.assertEqual(persisted["metrics"]["total_cost"]["amount"], "0.001")

    def test_opt_in_report_archives_task_tool_transport_without_rewriting_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            events = [{"seq": 1, "event_type": "context_compaction"}]
            (run_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
            evidence = run_dir / "artifacts" / "private_evidence" / "business_simulator"
            evidence.mkdir(parents=True)
            ledger = evidence / "ledger.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "committed": True,
                        "lifecycle": {"seq": 7},
                        "request": {"tool": "web_search", "arguments": {"query_id": "Q01"}},
                        "response": {
                            "ok": True,
                            "query_id": "Q01",
                            "page": 1,
                            "payload_octets": 12,
                            "_task_tool_content": "complete result",
                        },
                        "request_sha256": "request-digest",
                        "effect": {"response_sha256": "response-digest"},
                        "record_sha256": "record-digest",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "config.resolved.json").write_text(
                json.dumps(
                    {
                        "execution_capabilities": {
                            "required": ["task_tool_transport_report_v1"]
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = {
                "task_id": "transport-report",
                "artifacts": {
                    "trace_events": "events.json",
                    "business_simulator_evidence": {
                        "ledger": "artifacts/private_evidence/business_simulator/ledger.jsonl"
                    },
                },
            }

            html = generate_trace_report(run_dir, result=result).read_text(encoding="utf-8")

        self.assertIn("Task-tool transport archive", html)
        self.assertIn("complete result", html)
        self.assertIn('"record_count":1', html)
        self.assertIn('"events":[{"seq":1,"event_type":"context_compaction"}]', html)


if __name__ == "__main__":
    unittest.main()
