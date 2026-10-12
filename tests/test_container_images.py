from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harnessbench.container_images import (
    DEFAULT_IMAGES,
    WORKER_SOURCE_PATHS,
    _copy_worker_source,
)


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_WORKER_MODULES = {
    Path("business_simulator.py"),
    Path("deepseek_bridge.mjs"),
    Path("deepseek_config.py"),
    Path("deepseek_runner.py"),
    Path("model_call_budget.py"),
    Path("provider_request_recorder.py"),
    Path("task_tool_mcp.py"),
    Path("types.py"),
}


class ContainerImageWorkerContextTests(unittest.TestCase):
    def test_task_tool_dependency_closure_is_packaged_for_every_harness(self) -> None:
        self.assertTrue(REQUIRED_WORKER_MODULES <= set(WORKER_SOURCE_PATHS))

        for harness, definition in DEFAULT_IMAGES.items():
            with self.subTest(harness=harness):
                dockerfile = (
                    ROOT / "docker" / "harnessbench" / definition.dockerfile
                ).read_text(encoding="utf-8")
                self.assertIn(
                    'RUN python3 -c "import harnessbench.container_worker"',
                    dockerfile,
                )

    def test_reduced_worker_build_context_imports_container_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary)
            _copy_worker_source(ROOT / "harnessbench", context / "harnessbench")
            shutil.copytree(
                ROOT / "harness_tracer",
                context / "harness_tracer",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(context),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
            completed = subprocess.run(
                [sys.executable, "-c", "import harnessbench.container_worker"],
                cwd=context,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
