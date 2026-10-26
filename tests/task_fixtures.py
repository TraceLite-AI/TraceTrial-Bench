"""Resolve regression fixtures against the final published task collection."""

from pathlib import Path
from unittest import SkipTest

from tracetrial_bench.task_names import TASK_ID_RENAMES


ROOT = Path(__file__).resolve().parents[1]
RETIRED_TASK_IDS = {
    "C2c-compaction-detail-loss-L3-001",
    "G2b-exec-approval-broken-L3-001",
    "L7-crossregion-archive-L3-001",
    "M-E4c-integration-L3-001",
    "M-G6b-vendormsg-L3-001",
    "M-T5d-copyreview-L3-001",
    "T2d-coercion-advice-L3-001",
}


def paper_task(task_id: str) -> Path:
    if task_id in RETIRED_TASK_IDS:
        raise SkipTest(f"Historical fixture {task_id} is outside the final 95-task release")
    task_id = TASK_ID_RENAMES.get(task_id, task_id)
    path = ROOT / "Task" / task_id
    if not (path / "task.yaml").is_file():
        raise FileNotFoundError(f"Published test fixture is missing: {path}")
    return path
