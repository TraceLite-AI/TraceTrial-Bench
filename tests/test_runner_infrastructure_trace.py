from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harnessbench.runner import _write_container_infrastructure_trace


class ContainerInfrastructureTraceTests(unittest.TestCase):
    def test_trace_writes_survive_concurrent_working_directory_change(self) -> None:
        original_cwd = Path.cwd()
        original_mkdir = Path.mkdir
        with tempfile.TemporaryDirectory() as root_name, tempfile.TemporaryDirectory() as rival_name:
            root = Path(root_name)
            rival = Path(rival_name)
            os.chdir(root)

            def mkdir_then_move(path: Path, *args: object, **kwargs: object) -> None:
                original_mkdir(path, *args, **kwargs)
                os.chdir(rival)

            try:
                with patch.object(Path, "mkdir", new=mkdir_then_move):
                    summary = _write_container_infrastructure_trace(
                        layout=SimpleNamespace(artifacts_dir=Path("artifacts")),
                        harness="codex",
                        model="test-model",
                        error_type="container_runtime",
                        error_message="synthetic failure",
                    )
            finally:
                os.chdir(original_cwd)

            events_path = Path(summary["events_path"])
            summary_path = Path(summary["summary_path"])
            self.assertTrue(events_path.is_absolute())
            self.assertTrue(summary_path.is_absolute())
            self.assertTrue(events_path.exists())
            self.assertTrue(summary_path.exists())


if __name__ == "__main__":
    unittest.main()
