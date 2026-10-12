from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path

from harnessbench.workspace import validate_workspace_for_host_verifier


class GatewayWorkspaceValidationTests(unittest.TestCase):
    def test_undeclared_unix_socket_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            socket_path = workspace / "agent.sock"
            with _bound_unix_socket(socket_path):
                with self.assertRaisesRegex(ValueError, "special file"):
                    validate_workspace_for_host_verifier(workspace)

    def test_exact_declared_unix_socket_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            socket_path = workspace / "simulator.sock"
            (workspace / "answer.txt").write_text("answer\n", encoding="utf-8")
            with _bound_unix_socket(socket_path):
                result = validate_workspace_for_host_verifier(
                    workspace,
                    transient_socket_paths=(socket_path,),
                )

            self.assertEqual(result, {"files": 1, "directories": 1})

    def test_declared_socket_does_not_allow_another_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            declared_path = workspace / "simulator.sock"
            undeclared_path = workspace / "agent.sock"
            with _bound_unix_socket(declared_path), _bound_unix_socket(
                undeclared_path
            ):
                with self.assertRaisesRegex(ValueError, "special file"):
                    validate_workspace_for_host_verifier(
                        workspace,
                        transient_socket_paths=(declared_path,),
                    )

    def test_declared_fifo_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            fifo_path = workspace / "simulator.sock"
            os.mkfifo(fifo_path)

            with self.assertRaisesRegex(ValueError, "special file"):
                validate_workspace_for_host_verifier(
                    workspace,
                    transient_socket_paths=(fifo_path,),
                )

    def test_declared_path_outside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()

            with self.assertRaisesRegex(ValueError, "inside the verifier workspace"):
                validate_workspace_for_host_verifier(
                    workspace,
                    transient_socket_paths=(root / "outside.sock",),
                )

    def test_declared_symbolic_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "target.txt"
            target.write_text("target\n", encoding="utf-8")
            socket_path = workspace / "simulator.sock"
            socket_path.symlink_to(target.name)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                validate_workspace_for_host_verifier(
                    workspace,
                    transient_socket_paths=(socket_path,),
                )


class _bound_unix_socket:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def __enter__(self) -> socket.socket:
        self.server.bind(str(self.path))
        return self.server

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.close()
        self.path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
