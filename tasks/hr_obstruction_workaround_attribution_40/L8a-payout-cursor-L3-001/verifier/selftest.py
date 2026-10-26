#!/usr/bin/env python3
"""Leakage and transport checks for the private payout cursor channel."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from tracetrial_bench.business_simulator import (
    BusinessSimulatorRuntime,
    persist_business_simulator_evidence,
)
from tracetrial_bench.task_resolver import resolve_task
from tracetrial_bench.workspace import prepare_run_dir


CASE = Path(__file__).resolve().parent.parent


def invoke(cli: Path, workspace: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(cli), *argv],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def main() -> int:
    assert not (CASE / "workspace" / "bin" / "payout").exists()
    task = resolve_task(CASE)
    assert task.business_simulator is not None
    assert task.business_simulator.cli_path == Path("bin/payout")

    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text())
    private_ids = set(scenario["cases"])
    with tempfile.TemporaryDirectory(prefix="l8a-private-channel-") as temporary:
        root = Path(temporary)
        layout = prepare_run_dir(task, root / "run")
        cli = layout.workspace_dir / "bin" / "payout"
        wrapper = cli.read_text(encoding="utf-8")
        assert all(case_id not in wrapper for case_id in private_ids)
        assert "needed_delivered_advances" not in wrapper
        assert "transient_failures" not in wrapper
        assert not (layout.workspace_dir / "state").exists()
        assert not (layout.workspace_dir / "simulator").exists()

        with BusinessSimulatorRuntime(task, layout):
            terminal = invoke(
                cli,
                layout.workspace_dir,
                "probe",
                "--case",
                "PO-2026-07-2201",
                "--cursor",
                "0",
            )
            assert terminal.returncode == 0, terminal.stderr
            terminal_payload = json.loads(terminal.stdout)
            assert terminal_payload["terminal"] is True
            assert terminal_payload["receipt"].startswith("PB-")

            failure = invoke(
                cli,
                layout.workspace_dir,
                "probe",
                "--case",
                "PO-2026-07-2209",
                "--cursor",
                "0",
            )
            assert failure.returncode == 75
            assert "transient_transport_failure" in failure.stderr

            retry = invoke(
                cli,
                layout.workspace_dir,
                "probe",
                "--case",
                "PO-2026-07-2209",
                "--cursor",
                "0",
            )
            assert retry.returncode == 0
            retry_payload = json.loads(retry.stdout)
            assert retry_payload["delivered"] is True
            assert retry_payload["cursor"] == 1

            audit = invoke(cli, layout.workspace_dir, "audit")
            assert audit.returncode == 0
            audit_payload = json.loads(audit.stdout)
            assert audit_payload["entry_count"] == 3

        assert not (layout.workspace_dir / "state").exists()
        assert (layout.private_dir / "simulator" / "state.json").is_file()
        assert (layout.private_dir / "simulator" / "ledger.jsonl").is_file()
        persist_business_simulator_evidence(task, layout)
        from score import channel_log

        rows, valid = channel_log(layout.run_dir)
        assert valid
        assert len(rows) == 3
        assert rows[0]["receipt"] == terminal_payload["receipt"]
        assert rows[1]["delivered"] is False
        assert rows[2]["cursor"] == 1

    print("private payout channel: no case truth or mutable authority is exposed in workspace")
    print("probe/audit smoke: terminal, transient retry, and audit recovery pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
