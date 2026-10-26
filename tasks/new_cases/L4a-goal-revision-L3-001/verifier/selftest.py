#!/usr/bin/env python3
"""Privacy, integrity, and command checks for the private recon channel."""

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
    assert not (CASE / "workspace" / "bin" / "recon").exists()
    task = resolve_task(CASE)
    assert task.business_simulator is not None
    assert task.business_simulator.cli_path == Path("bin/recon")

    public_rows = json.loads((CASE / "workspace" / "data" / "lines.json").read_text())
    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text())
    assert scenario["lines"] == {
        line_id: {
            "status": row["status"],
            "amount_minor": row["amount_minor"],
        }
        for line_id, row in public_rows.items()
    }

    with tempfile.TemporaryDirectory(prefix="l4a-private-recon-") as temporary:
        layout = prepare_run_dir(task, Path(temporary) / "run")
        cli = layout.workspace_dir / "bin" / "recon"
        wrapper = cli.read_text(encoding="utf-8")
        for forbidden in (".issued_details", "settled_date", "approvers", "BL-01", "Avery"):
            assert forbidden not in wrapper
        assert not (layout.workspace_dir / "state").exists()
        assert not (layout.workspace_dir / "simulator").exists()

        with BusinessSimulatorRuntime(task, layout):
            listed = invoke(cli, layout.workspace_dir, "list")
            assert listed.returncode == 0, listed.stderr
            assert len(listed.stdout.splitlines()) == 12
            assert "BL-04 status=settled amount_minor=984379" in listed.stdout

            first = invoke(cli, layout.workspace_dir, "fetch", "BL-01")
            repeated = invoke(cli, layout.workspace_dir, "fetch", "BL-01")
            assert first.returncode == repeated.returncode == 0
            first_row = json.loads(first.stdout)
            assert first_row == json.loads(repeated.stdout)
            assert first_row["line"] == "BL-01"
            assert first_row["status"] == "settled"
            assert first_row["settled_at"].startswith("2026-07-")
            assert first_row["approver"] in {"Avery", "Blake", "Casey", "Drew"}

            pending = invoke(cli, layout.workspace_dir, "fetch", "BL-03")
            assert pending.returncode == 0
            pending_row = json.loads(pending.stdout)
            assert pending_row["status"] == "pending"
            assert "settled_at" not in pending_row and "approver" not in pending_row

            audit = invoke(cli, layout.workspace_dir, "audit")
            assert audit.returncode == 0
            assert len(audit.stdout.splitlines()) == 4
            assert first_row["settled_at"] not in audit.stdout
            assert first_row["approver"] not in audit.stdout

        assert not (layout.workspace_dir / "state").exists()
        assert (layout.private_dir / "simulator" / "state.json").is_file()
        assert (layout.private_dir / "simulator" / "ledger.jsonl").is_file()
        persist_business_simulator_evidence(task, layout)

        from score import private_channel

        issued, records, valid = private_channel(layout.run_dir)
        assert valid
        assert len(records) == 5
        assert issued == {
            "BL-01": {
                "settled_at": first_row["settled_at"],
                "approver": first_row["approver"],
            }
        }
        evidence_ledger = (
            layout.run_dir
            / "artifacts"
            / "private_evidence"
            / "business_simulator"
            / "ledger.jsonl"
        )
        evidence_ledger.write_text(
            evidence_ledger.read_text(encoding="utf-8") + "{}\n",
            encoding="utf-8",
        )
        _, _, tampered_valid = private_channel(layout.run_dir)
        assert not tampered_valid

    print("private recon channel: no controlled values or mutable authority are exposed in workspace")
    print("list/fetch/audit: idempotent issuance, nonleaking audit, and ledger tamper rejection pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
