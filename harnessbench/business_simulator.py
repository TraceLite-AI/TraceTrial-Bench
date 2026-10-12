"""Runner-private, stateful business simulator support.

Tasks opt in with a small ``task.yaml`` block::

    business_simulator:
      version: unix_json_v1
      scenario: simulator/scenario.json
      server: simulator/server.py
      cli: business-cli

``simulator/`` is copied only into the run's private directory.  ``server.py``
is an ordinary Python plugin with two callables::

    def initial_state(scenario):
        return {"items": []}

    def handle(request, state, scenario):
        # request == {"argv": ["create", "item-1"]}
        # Mutate state; mutations commit only when this function returns.
        return {"ok": True}

The runner installs only a generic CLI in the agent workspace.  That CLI sends
one JSON-line request through the runner-owned Unix or authenticated container
transport. Responses are normally rendered as JSON, while a simulator may use
``_transport_stdout`` for ordinary CLI text output. The runner, rather than task
code, owns the durable state and append-only ledger below ``private/simulator/``.
Every v2 ledger row is bound to the run, paired instance, non-secret runtime
configuration, runner authority, derived effect, and lifecycle transition.
After the socket and all accepted handlers drain, the runner writes
``private/simulator/manifest.json`` before the private verifier starts. Its
immutable binding digest deliberately omits the final ledger digest, avoiding
a self-referential hash cycle.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import shutil
import socket
import socketserver
import sys
import threading
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from harness_tracer.summary import build_summary, write_last_message, write_summary
from harness_tracer.timeutils import utc_now_iso
from harness_tracer.writer import EventWriter

from harnessbench.task_tool_mcp import ENDPOINT_SCHEMA
from harnessbench.types import BusinessSimulatorSpec, TaskSpec


SOCKET_FILENAME = ".harnessbench-business-simulator.sock"
LEDGER_FILENAME = "ledger.jsonl"
STATE_FILENAME = "state.json"
INSTANCE_FILENAME = "instance.json"
RELEASE_MANIFEST_FILENAME = "manifest.json"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
SUPPORTED_VERSION = "unix_json_v1"
BUSINESS_ENDPOINT_ENV = "HARNESSBENCH_BUSINESS_SIMULATOR_ENDPOINT"
BUSINESS_ENDPOINT_SCHEMA = "harnessbench_business_simulator_endpoint_v1"
CONTAINER_HOST_ALIAS = "harnessbench-host"
GENESIS_RECORD_SHA256 = "0" * 64
RELEASE_EVIDENCE_SCHEMA = "harnessbench_runner_evidence_v2"
RELEASE_EVIDENCE_OWNER = "harnessbench-runner"
UNSEEDED_INSTANCE_SHA256 = hashlib.sha256(
    b"harnessbench:unseeded-instance:v1"
).hexdigest()
RELEASE_LEDGER_FIELDS = frozenset(
    {
        "seq",
        "task_id",
        "request",
        "request_sha256",
        "response",
        "response_sha256",
        "committed",
        "state_revision",
        "state_sha256",
        "prev_record_sha256",
        "record_sha256",
        "run_id",
        "instance_seed_sha256",
        "configuration_sha256",
        "authority_context",
        "effect",
        "lifecycle",
        "manifest_sha256",
    }
)
RELEASE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_owner",
        "task_id",
        "run_id",
        "seed_sha256",
        "configuration_sha256",
        "authority_digest",
        "effect_digest",
        "lifecycle_digest",
        "ledger_sha256",
        "final_state_sha256",
        "record_count",
        "status",
    }
)
HANDLER_DRAIN_GRACE_SECONDS = 0.25
HANDLER_FORCE_DRAIN_TIMEOUT_SECONDS = 5.0

_CWD_LOCK = threading.Lock()


class BusinessSimulatorStartupError(RuntimeError):
    """Raised when a configured simulator cannot be made ready."""


def install_agent_cli(spec: BusinessSimulatorSpec, workspace_dir: Path) -> Path:
    """Install the generic socket client before the workspace baseline is made."""

    destination = (workspace_dir / spec.cli_path).resolve()
    try:
        destination.relative_to(workspace_dir.resolve())
    except ValueError as exc:
        raise ValueError("business_simulator cli escapes the workspace") from exc
    if destination.exists():
        raise ValueError(
            f"business_simulator cli would overwrite workspace file: {spec.cli_path}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    agent_parent_index = len(spec.cli_path.parts)
    destination.write_text(_cli_source(agent_parent_index), encoding="utf-8")
    destination.chmod(0o755)
    return destination


class BusinessSimulatorRuntime:
    """Own the private service lifecycle for one benchmark run."""

    def __init__(
        self,
        task: TaskSpec,
        layout: Any,
        *,
        instance_seed: str | None = None,
        configuration_context: dict[str, Any] | None = None,
        enable_container_transport: bool = False,
    ) -> None:
        self.task = task
        self.layout = layout
        self.spec = task.business_simulator
        self.instance_seed = instance_seed
        self.configuration_context = copy.deepcopy(configuration_context or {})
        self.enable_container_transport = enable_container_transport
        self.socket_path = layout.workspace_dir / SOCKET_FILENAME
        self.private_simulator_dir = layout.private_dir / "simulator"
        self.state_path = self.private_simulator_dir / STATE_FILENAME
        self.ledger_path = self.private_simulator_dir / LEDGER_FILENAME
        self._server: _ThreadedUnixStreamServer | None = None
        self._thread: threading.Thread | None = None
        self._container_server: _ThreadedTCPStreamServer | None = None
        self._container_thread: threading.Thread | None = None
        self._container_auth_token = secrets.token_urlsafe(32)
        self._state_runtime: _StateRuntime | None = None
        self._release_manifest_written = False
        self._service_started = False

    @property
    def enabled(self) -> bool:
        return self.spec is not None

    def __enter__(self) -> "BusinessSimulatorRuntime":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    @property
    def task_tool_endpoint(self) -> dict[str, Any]:
        """Return the private transport binding consumed by the MCP bridge."""

        if not self._service_started:
            raise RuntimeError("business simulator transport is not ready")
        if self.enable_container_transport:
            server = self._container_server
            if server is None:
                raise RuntimeError("container simulator transport is unavailable")
            return {
                "schema_version": ENDPOINT_SCHEMA,
                "transport": "tcp",
                "host": CONTAINER_HOST_ALIAS,
                "port": int(server.server_address[1]),
                "auth_token": self._container_auth_token,
                "task_id": self.task.task_id,
                "run_id": str(self.layout.run_dir.name),
            }
        return {
            "schema_version": ENDPOINT_SCHEMA,
            "transport": "unix",
            "path": str(self.socket_path),
        }

    @property
    def business_endpoint(self) -> dict[str, Any]:
        """Return the transport binding consumed by the generic business CLI."""

        if not self._service_started:
            raise RuntimeError("business simulator transport is not ready")
        if self.enable_container_transport:
            server = self._container_server
            if server is None:
                raise RuntimeError("container simulator transport is unavailable")
            return {
                "schema_version": BUSINESS_ENDPOINT_SCHEMA,
                "transport": "tcp",
                "host": CONTAINER_HOST_ALIAS,
                "port": int(server.server_address[1]),
                "auth_token": self._container_auth_token,
                "task_id": self.task.task_id,
                "run_id": str(self.layout.run_dir.name),
            }
        return {
            "schema_version": BUSINESS_ENDPOINT_SCHEMA,
            "transport": "unix",
            "path": str(self.socket_path),
        }

    def start(self) -> None:
        if self.spec is None or self._server is not None:
            return
        try:
            if self.spec.version != SUPPORTED_VERSION:
                raise ValueError(f"unsupported simulator version: {self.spec.version}")
            scenario_path = self._private_copy_of(self.spec.scenario_path)
            server_path = self._private_copy_of(self.spec.server_path)
            source_scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenario, instance = materialize_scenario(
                source_scenario,
                task_id=self.task.task_id,
                seed=self.instance_seed,
            )
            _write_json_atomic(scenario_path, scenario)
            plugin = _load_plugin(server_path)
            initial_state = _require_callable(plugin, "initial_state")
            handle = _require_callable(plugin, "handle")
            state = initial_state(copy.deepcopy(scenario))
            _serialized_json(state, "initial simulator state")

            self.private_simulator_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(
                self.private_simulator_dir / INSTANCE_FILENAME,
                instance,
            )
            _write_json_atomic(self.state_path, state)
            self.ledger_path.write_text("", encoding="utf-8")
            self.ledger_path.chmod(0o600)
            run_id = str(self.layout.run_dir.name)
            instance_seed_sha256 = (
                instance.get("seed_sha256") or UNSEEDED_INSTANCE_SHA256
            )
            configuration_sha256 = _sha256_json(
                _simulator_configuration(
                    self.task,
                    context=self.configuration_context,
                )
            )
            authority_context = _authority_context(
                task_id=self.task.task_id,
                run_id=run_id,
            )
            authority_digest = _sha256_json(authority_context)
            manifest_sha256 = release_manifest_binding_sha256(
                {
                    "schema_version": RELEASE_EVIDENCE_SCHEMA,
                    "evidence_owner": RELEASE_EVIDENCE_OWNER,
                    "task_id": self.task.task_id,
                    "run_id": run_id,
                    "seed_sha256": instance_seed_sha256,
                    "configuration_sha256": configuration_sha256,
                    "authority_digest": authority_digest,
                }
            )
            state_runtime = _StateRuntime(
                task_id=self.task.task_id,
                scenario=scenario,
                state=state,
                handle=handle,
                state_path=self.state_path,
                ledger_path=self.ledger_path,
                max_requests=getattr(self.task, "max_tool_calls", None),
                run_id=run_id,
                instance_seed_sha256=instance_seed_sha256,
                configuration_sha256=configuration_sha256,
                authority_context=authority_context,
                manifest_sha256=manifest_sha256,
            )
            self._state_runtime = state_runtime
            self.socket_path.unlink(missing_ok=True)
            self._server = _bind_relative_server(
                self.layout.workspace_dir, state_runtime
            )
            self.socket_path.chmod(0o600)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name=f"harnessbench-simulator-{self.task.task_id}",
                daemon=True,
            )
            self._thread.start()
            if not self._thread.is_alive():
                raise RuntimeError("simulator service thread did not start")
            if self.enable_container_transport:
                self._container_server = _ThreadedTCPStreamServer(
                    ("0.0.0.0", 0),
                    state_runtime,
                    auth_token=self._container_auth_token,
                    task_id=self.task.task_id,
                    run_id=str(self.layout.run_dir.name),
                )
                self._container_thread = threading.Thread(
                    target=self._container_server.serve_forever,
                    name=f"harnessbench-container-simulator-{self.task.task_id}",
                    daemon=True,
                )
                self._container_thread.start()
                if not self._container_thread.is_alive():
                    raise RuntimeError("container simulator service thread did not start")
            self._service_started = True
        except Exception as exc:
            self.stop()
            raise BusinessSimulatorStartupError(
                f"business simulator startup failed ({type(exc).__name__}): {exc}"
            ) from exc

    def stop(self) -> None:
        servers = (
            (self._container_server, self._container_thread),
            (self._server, self._thread),
        )
        self._container_server = None
        self._container_thread = None
        self._server = None
        self._thread = None
        failures: list[str] = []
        for server, thread in servers:
            if server is None:
                continue
            try:
                if thread is not None and thread.is_alive():
                    server.shutdown()
            except Exception as exc:
                failures.append(f"accept loop shutdown failed: {type(exc).__name__}")
            try:
                server.server_close()
            except Exception as exc:
                failures.append(f"listening socket close failed: {type(exc).__name__}")
            try:
                server.drain_handlers()
            except Exception as exc:
                failures.append(f"handler drain failed: {type(exc).__name__}: {exc}")
            if thread is not None and thread.ident is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    failures.append("accept loop thread did not stop")
        self.socket_path.unlink(missing_ok=True)
        if self._state_runtime is not None and not self._release_manifest_written:
            try:
                status = (
                    "complete"
                    if self._service_started and not failures
                    else "incomplete"
                )
                manifest = self._state_runtime.release_manifest(status=status)
                _write_json_atomic(
                    self.private_simulator_dir / RELEASE_MANIFEST_FILENAME,
                    manifest,
                )
                self._release_manifest_written = True
            except Exception as exc:
                failures.append(
                    f"release manifest persistence failed: {type(exc).__name__}: {exc}"
                )
        if failures:
            raise RuntimeError("; ".join(failures))

    def _private_copy_of(self, source: Path) -> Path:
        relative = source.resolve().relative_to(self.task.task_dir.resolve())
        candidate = (self.layout.private_dir / relative).resolve()
        try:
            candidate.relative_to(self.private_simulator_dir.resolve())
        except ValueError as exc:
            raise ValueError("simulator input is not below private/simulator") from exc
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate


class _StateRuntime:
    def __init__(
        self,
        *,
        task_id: str,
        scenario: Any,
        state: Any,
        handle: Callable[[dict[str, Any], Any, Any], Any],
        state_path: Path,
        ledger_path: Path,
        run_id: str,
        instance_seed_sha256: str,
        configuration_sha256: str,
        authority_context: dict[str, Any],
        manifest_sha256: str,
        max_requests: int | None = None,
    ) -> None:
        self.task_id = task_id
        self.scenario = scenario
        self.state = state
        self.handle = handle
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.max_requests = max_requests
        self.run_id = run_id
        self.instance_seed_sha256 = instance_seed_sha256
        self.configuration_sha256 = configuration_sha256
        self.authority_context = copy.deepcopy(authority_context)
        self.manifest_sha256 = manifest_sha256
        self._lock = threading.Lock()
        self._sequence = 0
        self._state_revision = 0
        self._previous_record_sha256 = GENESIS_RECORD_SHA256

    def dispatch(self, request: dict[str, Any]) -> Any:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            if self.max_requests is not None and sequence > self.max_requests:
                response = {
                    "ok": False,
                    "error": (
                        "business simulator request budget exceeded: "
                        f"limit={self.max_requests}"
                    ),
                    "error_type": "BudgetExceeded",
                    "limit": self.max_requests,
                    "request_number": sequence,
                }
                self._append_ledger(
                    sequence=sequence,
                    request=request,
                    response=response,
                    committed=False,
                )
                return response
            candidate_state = copy.deepcopy(self.state)
            committed = False
            try:
                response = self.handle(
                    copy.deepcopy(request),
                    candidate_state,
                    copy.deepcopy(self.scenario),
                )
                if response is None:
                    response = {"ok": True}
                _serialized_json(response, "simulator response")
                _serialized_json(candidate_state, "simulator state")
                _write_json_atomic(self.state_path, candidate_state)
                self.state = candidate_state
                committed = True
            except Exception as exc:
                response = {
                    "ok": False,
                    "error": str(exc) or "simulator command failed",
                    "error_type": type(exc).__name__,
                }
            self._append_ledger(
                sequence=sequence,
                request=request,
                response=response,
                committed=committed,
            )
            return response

    def reject(self, error: str) -> dict[str, Any]:
        request = {"invalid": True}
        with self._lock:
            self._sequence += 1
            response = {"ok": False, "error": error, "error_type": "ProtocolError"}
            self._append_ledger(
                sequence=self._sequence,
                request=request,
                response=response,
                committed=False,
            )
            return response

    def _append_ledger(
        self,
        *,
        sequence: int,
        request: dict[str, Any],
        response: Any,
        committed: bool,
    ) -> None:
        if committed:
            self._state_revision += 1
        record = {
            "seq": sequence,
            "task_id": self.task_id,
            "request": request,
            "request_sha256": _sha256_json(request),
            "response": response,
            "response_sha256": _sha256_json(response),
            "committed": committed,
            "state_revision": self._state_revision,
            "state_sha256": _sha256_json(self.state),
            "prev_record_sha256": self._previous_record_sha256,
            "run_id": self.run_id,
            "instance_seed_sha256": self.instance_seed_sha256,
            "configuration_sha256": self.configuration_sha256,
            "authority_context": copy.deepcopy(self.authority_context),
        }
        record["effect"] = _release_effect(record)
        record["lifecycle"] = _release_lifecycle(record)
        record["manifest_sha256"] = self.manifest_sha256
        record["record_sha256"] = _record_sha256(record)
        with self.ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._previous_record_sha256 = record["record_sha256"]

    def release_manifest(self, *, status: str) -> dict[str, Any]:
        records, parse_errors = _read_ledger_records(self.ledger_path)
        chain = validate_ledger_chain(
            records,
            parse_errors=parse_errors,
            expected_task_id=self.task_id,
        )
        final_state_sha256 = _sha256_json(self.state)
        final_record_state_sha256 = (
            records[-1].get("state_sha256") if records else final_state_sha256
        )
        release_status = status
        if (
            parse_errors
            or not chain["valid"]
            or final_record_state_sha256 != final_state_sha256
        ):
            release_status = "integrity_error"
        manifest = {
            "schema_version": RELEASE_EVIDENCE_SCHEMA,
            "evidence_owner": RELEASE_EVIDENCE_OWNER,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "seed_sha256": self.instance_seed_sha256,
            "configuration_sha256": self.configuration_sha256,
            "authority_digest": _sha256_json(self.authority_context),
            "effect_digest": _sha256_json([record.get("effect") for record in records]),
            "lifecycle_digest": _sha256_json(
                [record.get("lifecycle") for record in records]
            ),
            "ledger_sha256": _sha256_file(self.ledger_path),
            "final_state_sha256": final_state_sha256,
            "record_count": len(records),
            "status": release_status,
        }
        if release_status == "complete":
            validation = validate_release_evidence(
                records,
                self.state,
                manifest,
                ledger_sha256=manifest["ledger_sha256"],
                expected_task_id=self.task_id,
                expected_seed_sha256=self.instance_seed_sha256,
            )
            if not validation["valid"]:
                manifest["status"] = "integrity_error"
        return manifest


class _SimulatorRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: _ThreadedSimulatorServerMixin = self.server  # type: ignore[assignment]
        self.request.settimeout(10)
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except (OSError, TimeoutError):
            return
        if not raw:
            # An accepted connection that never supplied a JSON line is not a
            # simulator action.  In particular, forced drain of a half-open
            # client must not synthesize a ProtocolError ledger record.
            return
        runtime = server.runtime
        if len(raw) > MAX_REQUEST_BYTES:
            response = runtime.reject("request exceeds maximum size")
        else:
            try:
                request = json.loads(raw.decode("utf-8"))
                request = server.authorize_request(request)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                # Transport authentication and health checks are deliberately
                # outside the business ledger. A caller that cannot establish
                # the runner-owned channel has not performed a business action.
                response = {
                    "ok": False,
                    "error": f"invalid transport request: {exc}",
                    "error_type": "TransportError",
                }
            else:
                if request.pop("_harnessbench_health", False) is True:
                    response = {
                        "ok": True,
                        "status": "ready",
                        "task_id": runtime.task_id,
                    }
                else:
                    try:
                        _validate_request(request)
                    except ValueError as exc:
                        response = runtime.reject(f"invalid request: {exc}")
                    else:
                        response = runtime.dispatch(request)
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self.wfile.write(payload.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The authoritative state/ledger commit, if any, has already
            # completed.  A disconnected client cannot roll it back.
            return


class _ThreadedSimulatorServerMixin:
    daemon_threads = True
    block_on_close = False

    def _initialize_simulator_server(
        self,
        runtime: _StateRuntime,
        *,
        auth_token: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.auth_token = auth_token
        self.auth_task_id = task_id
        self.auth_run_id = run_id
        self._handler_condition = threading.Condition()
        self._active_handler_sockets: set[socket.socket] = set()

    def authorize_request(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        if self.auth_token is None:
            return request
        transport = request.pop("_harnessbench_transport", None)
        if not isinstance(transport, dict):
            raise ValueError("transport authentication is missing")
        supplied = transport.get("auth_token")
        if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, self.auth_token
        ):
            raise ValueError("transport authentication failed")
        if transport.get("task_id") != self.auth_task_id:
            raise ValueError("transport task binding failed")
        if transport.get("run_id") != self.auth_run_id:
            raise ValueError("transport run binding failed")
        return request

    def handler_started(self, client: socket.socket) -> None:
        with self._handler_condition:
            self._active_handler_sockets.add(client)

    def handler_finished(self, client: socket.socket) -> None:
        with self._handler_condition:
            self._active_handler_sockets.discard(client)
            self._handler_condition.notify_all()

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        """Own an accepted socket before a worker thread can be delayed.

        ``ThreadingMixIn.process_request`` creates the worker.  Registering in
        the request handler is too late: shutdown can otherwise observe an
        accepted-but-not-yet-started request as an empty handler set and let
        scoring race a subsequent state/ledger mutation.
        """

        self.handler_started(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.handler_finished(request)
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.handler_finished(request)

    @property
    def active_handler_count(self) -> int:
        with self._handler_condition:
            return len(self._active_handler_sockets)

    def drain_handlers(self) -> None:
        """Prove every accepted request is terminal before scoring begins."""

        with self._handler_condition:
            drained = self._handler_condition.wait_for(
                lambda: not self._active_handler_sockets,
                timeout=HANDLER_DRAIN_GRACE_SECONDS,
            )
            active = tuple(self._active_handler_sockets) if not drained else ()
        for client in active:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        with self._handler_condition:
            if not self._handler_condition.wait_for(
                lambda: not self._active_handler_sockets,
                timeout=HANDLER_FORCE_DRAIN_TIMEOUT_SECONDS,
            ):
                raise RuntimeError(
                    f"{len(self._active_handler_sockets)} active request handler(s) "
                    "survived forced socket shutdown"
                )


class _ThreadedUnixStreamServer(
    _ThreadedSimulatorServerMixin,
    socketserver.ThreadingMixIn,
    socketserver.UnixStreamServer,
):
    def __init__(self, address: str, runtime: _StateRuntime) -> None:
        self._initialize_simulator_server(runtime)
        super().__init__(address, _SimulatorRequestHandler)


class _ThreadedTCPStreamServer(
    _ThreadedSimulatorServerMixin,
    socketserver.ThreadingMixIn,
    socketserver.TCPServer,
):
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        runtime: _StateRuntime,
        *,
        auth_token: str,
        task_id: str,
        run_id: str,
    ) -> None:
        self._initialize_simulator_server(
            runtime,
            auth_token=auth_token,
            task_id=task_id,
            run_id=run_id,
        )
        super().__init__(address, _SimulatorRequestHandler)


def _bind_relative_server(
    socket_dir: Path, runtime: _StateRuntime
) -> _ThreadedUnixStreamServer:
    # macOS limits AF_UNIX pathnames to roughly 104 bytes.  Binding and
    # connecting relative to workspace keeps deeply nested output runs valid
    # while making the socket available through the sole Docker bind mount.
    with _CWD_LOCK:
        previous_fd = os.open(".", os.O_RDONLY)
        try:
            os.chdir(socket_dir)
            return _ThreadedUnixStreamServer(SOCKET_FILENAME, runtime)
        finally:
            os.fchdir(previous_fd)
            os.close(previous_fd)


def _load_plugin(path: Path) -> ModuleType:
    module_name = f"_harnessbench_business_simulator_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load simulator plugin: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(path.parent))
        except ValueError:
            pass
    return module


def _require_callable(module: ModuleType, name: str) -> Callable[..., Any]:
    value = getattr(module, name, None)
    if not callable(value):
        raise TypeError(f"simulator server must define callable {name}(...)")
    return value


def _validate_request(request: Any) -> None:
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    argv = request.get("argv")
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ValueError("request.argv must be an array of strings")


def materialize_scenario(
    scenario: Any,
    *,
    task_id: str,
    seed: str | None,
) -> tuple[Any, dict[str, Any]]:
    """Deterministically randomize only opaque per-run protocol values.

    Business semantics, labels, revisions, and document contents remain stable.
    Values below fields such as ``*_key``, ``*_token``, ``nonce``, ``canary``,
    and ``approval_ids`` are aliases derived from the paired task/seed.  The
    same seed therefore gives two Harnesses the same hidden instance while a
    different seed prevents a fixed key sequence from being replayed.
    """

    copied = copy.deepcopy(scenario)
    if seed is None:
        return copied, {
            "schema_version": "business_simulator_instance_v1",
            "task_id": task_id,
            "materialized": False,
            "seed_sha256": None,
            "opaque_value_count": 0,
            "scenario_sha256": _sha256_json(copied),
        }
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("instance seed must be a non-empty string")

    opaque_values: set[str] = set()

    def collect(value: Any, *, protected: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                field = str(key)
                if isinstance(child, str):
                    if protected or _opaque_protocol_field(field):
                        opaque_values.add(child)
                else:
                    collect(
                        child,
                        protected=protected or _opaque_protocol_container(field),
                    )
        elif isinstance(value, list):
            for child in value:
                collect(child, protected=protected)
        elif protected and isinstance(value, str) and value:
            opaque_values.add(value)

    collect(copied)
    aliases = {
        value: "inst-"
        + hashlib.sha256(f"{task_id}\0{seed}\0{value}".encode("utf-8")).hexdigest()[:20]
        for value in sorted(opaque_values)
    }

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return aliases.get(value, value)
        if isinstance(value, list):
            return [replace(child) for child in value]
        if isinstance(value, dict):
            return {
                aliases.get(key, key) if isinstance(key, str) else key: replace(child)
                for key, child in value.items()
            }
        return value

    materialized = replace(copied)
    return materialized, {
        "schema_version": "business_simulator_instance_v1",
        "task_id": task_id,
        "materialized": True,
        "seed_sha256": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
        "opaque_value_count": len(aliases),
        "scenario_sha256": _sha256_json(materialized),
    }


def _opaque_protocol_field(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return bool(
        normalized in {"key", "idempotency", "approval_id", "effect_id"}
        or normalized.endswith("_key")
        or normalized.endswith("_token")
        or "nonce" in normalized
        or "canary" in normalized
    )


def _opaque_protocol_container(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return bool(
        normalized
        in {
            "keys",
            "approval_ids",
            "effect_ids",
            "idempotency_keys",
        }
        or normalized.endswith("_keys")
        or normalized.endswith("_tokens")
    )


def _serialized_json(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} is not JSON serializable") from exc


def _sha256_json(value: Any) -> str:
    serialized = _serialized_json(value, "simulator state")
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _record_sha256(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    return _sha256_json(payload)


def _simulator_configuration(
    task: TaskSpec,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    spec = task.business_simulator
    if spec is None:
        raise ValueError("business simulator configuration is unavailable")

    def relative(path: Path) -> str:
        return str(path.resolve().relative_to(task.task_dir.resolve()))

    configuration = {
        "schema_version": "harnessbench_simulator_configuration_v1",
        "task_id": task.task_id,
        "business_simulator": {
            "version": spec.version,
            "scenario": relative(spec.scenario_path),
            "server": relative(spec.server_path),
            "cli": str(spec.cli_path),
        },
        "budget": {
            "max_turns": task.max_turns,
            "max_tool_calls": task.max_tool_calls,
            "max_wall_time_sec": task.max_wall_time_sec,
        },
        "runtime": copy.deepcopy(context),
    }
    _serialized_json(configuration, "simulator configuration")
    return configuration


def _authority_context(*, task_id: str, run_id: str) -> dict[str, str]:
    return {
        "schema_version": "harnessbench_simulator_authority_v1",
        "owner": RELEASE_EVIDENCE_OWNER,
        "task_id": task_id,
        "run_id": run_id,
        "transport": SUPPORTED_VERSION,
    }


def _release_effect(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_sha256": record["request_sha256"],
        "response_sha256": record["response_sha256"],
        "committed": record["committed"],
        "state_revision": record["state_revision"],
        "state_sha256": record["state_sha256"],
    }


def _release_lifecycle(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": record["seq"],
        "committed": record["committed"],
        "state_revision": record["state_revision"],
    }


def release_manifest_binding_sha256(manifest: dict[str, Any]) -> str:
    """Hash the immutable manifest prelude without a ledger/hash cycle."""

    binding_fields = (
        "schema_version",
        "evidence_owner",
        "task_id",
        "run_id",
        "seed_sha256",
        "configuration_sha256",
        "authority_digest",
    )
    binding = {field: manifest.get(field) for field in binding_fields}
    return _sha256_json(binding)


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    path.chmod(0o600)


def validate_business_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    """Validate a runner-owned endpoint for the generic business CLI."""

    if endpoint.get("schema_version") != BUSINESS_ENDPOINT_SCHEMA:
        raise ValueError("business simulator endpoint has an unsupported schema")
    transport = endpoint.get("transport")
    if transport == "unix":
        if set(endpoint) != {"schema_version", "transport", "path"}:
            raise ValueError("Unix business simulator endpoint has invalid fields")
        if not isinstance(endpoint.get("path"), str) or not endpoint["path"]:
            raise ValueError("Unix business simulator endpoint path is invalid")
        return endpoint
    if transport == "tcp":
        required = {
            "schema_version",
            "transport",
            "host",
            "port",
            "auth_token",
            "task_id",
            "run_id",
        }
        if set(endpoint) != required:
            raise ValueError("TCP business simulator endpoint has invalid fields")
        if not isinstance(endpoint.get("host"), str) or not endpoint["host"]:
            raise ValueError("TCP business simulator endpoint host is invalid")
        if (
            type(endpoint.get("port")) is not int
            or not 1 <= endpoint["port"] <= 65535
        ):
            raise ValueError("TCP business simulator endpoint port is invalid")
        for field in ("auth_token", "task_id", "run_id"):
            if not isinstance(endpoint.get(field), str) or not endpoint[field]:
                raise ValueError(f"TCP business simulator endpoint {field} is invalid")
        return endpoint
    raise ValueError("business simulator endpoint transport is unsupported")


def _authorized_business_request(
    endpoint: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    outbound = dict(request)
    if endpoint["transport"] == "tcp":
        outbound["_harnessbench_transport"] = {
            "auth_token": endpoint["auth_token"],
            "task_id": endpoint["task_id"],
            "run_id": endpoint["run_id"],
        }
    return outbound


def _exchange_business_endpoint(
    endpoint: dict[str, Any], request: dict[str, Any]
) -> Any:
    endpoint = validate_business_endpoint(dict(endpoint))
    payload = json.dumps(
        _authorized_business_request(endpoint, request),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise RuntimeError("business simulator request exceeds the maximum size")
    if endpoint["transport"] == "tcp":
        connection = socket.create_connection(
            (str(endpoint["host"]), int(endpoint["port"])), timeout=10
        )
    else:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(10)
        connection.connect(str(endpoint["path"]))
    try:
        connection.settimeout(300)
        connection.sendall(payload)
        with connection.makefile("rb") as stream:
            response = stream.readline(MAX_RESPONSE_BYTES + 1)
    finally:
        connection.close()
    if not response:
        raise RuntimeError("business simulator transport returned no response")
    if len(response) > MAX_RESPONSE_BYTES:
        raise RuntimeError("business simulator response exceeds the maximum size")
    try:
        return json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("business simulator returned an invalid response") from exc


def probe_business_endpoint(endpoint: dict[str, Any]) -> None:
    """Fail before model execution when the business CLI backend is unreachable."""

    normalized = validate_business_endpoint(dict(endpoint))
    response = _exchange_business_endpoint(
        normalized,
        {"_harnessbench_health": True},
    )
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("business simulator transport health check failed")
    if normalized["transport"] == "tcp" and response.get("task_id") != normalized.get(
        "task_id"
    ):
        raise RuntimeError("business simulator transport health check binding failed")


def _cli_source(agent_parent_index: int) -> str:
    return f'''#!/usr/bin/env python3
import json
import os
import signal
import socket
import sys
import termios
import tty
from pathlib import Path

SOCKET = {SOCKET_FILENAME!r}
ENDPOINT_ENV = {BUSINESS_ENDPOINT_ENV!r}
ENDPOINT_SCHEMA = {BUSINESS_ENDPOINT_SCHEMA!r}
MAX_REQUEST_BYTES = {MAX_REQUEST_BYTES}
MAX_RESPONSE_BYTES = {MAX_RESPONSE_BYTES}


class InteractiveInputTimeout(Exception):
    pass


def _raise_interactive_input_timeout(signum, frame):
    del signum, frame
    raise InteractiveInputTimeout()


def read_interactive_input(mode, timeout_sec):
    previous_handler = None
    if timeout_sec is not None:
        previous_handler = signal.signal(signal.SIGALRM, _raise_interactive_input_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout_sec)
    try:
        if mode in ("canonical", "eof"):
            return sys.stdin.readline()
        if mode == "raw":
            previous_attributes = termios.tcgetattr(0)
            try:
                tty.setraw(0)
                return os.read(0, 1)
            finally:
                termios.tcsetattr(0, termios.TCSADRAIN, previous_attributes)
        raise ValueError("unsupported interactive mode")
    finally:
        if timeout_sec is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)


def load_endpoint(workspace_dir):
    raw = os.environ.get(ENDPOINT_ENV, "")
    if not raw:
        return {{"schema_version": ENDPOINT_SCHEMA, "transport": "unix", "path": SOCKET}}
    try:
        endpoint = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("business simulator endpoint is invalid") from exc
    if not isinstance(endpoint, dict) or endpoint.get("schema_version") != ENDPOINT_SCHEMA:
        raise RuntimeError("business simulator endpoint is invalid")
    if endpoint.get("transport") == "tcp":
        required = {{"schema_version", "transport", "host", "port", "auth_token", "task_id", "run_id"}}
        if set(endpoint) != required:
            raise RuntimeError("business simulator endpoint is invalid")
    elif endpoint.get("transport") == "unix":
        if set(endpoint) != {{"schema_version", "transport", "path"}}:
            raise RuntimeError("business simulator endpoint is invalid")
    else:
        raise RuntimeError("business simulator endpoint is invalid")
    return endpoint


def exchange(endpoint, request, workspace_dir):
    outbound = dict(request)
    if endpoint["transport"] == "tcp":
        outbound["_harnessbench_transport"] = {{
            "auth_token": endpoint["auth_token"],
            "task_id": endpoint["task_id"],
            "run_id": endpoint["run_id"],
        }}
    payload = json.dumps(outbound, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise RuntimeError("business simulator request exceeds maximum size")
    previous_fd = os.open(".", os.O_RDONLY)
    try:
        if endpoint["transport"] == "tcp":
            client = socket.create_connection((endpoint["host"], endpoint["port"]), timeout=30)
        else:
            os.chdir(workspace_dir)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(30)
            client.connect(endpoint["path"])
        with client:
            client.settimeout(300)
            client.sendall(payload)
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_RESPONSE_BYTES + 1)
    finally:
        os.fchdir(previous_fd)
        os.close(previous_fd)
    if not raw:
        raise RuntimeError("business simulator returned no response")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("business simulator response exceeds maximum size")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("business simulator returned an invalid response") from exc


def main():
    workspace_dir = Path(__file__).resolve().parents[{agent_parent_index - 1}]
    invocation_cwd = Path.cwd().resolve()
    try:
        relative_cwd = str(invocation_cwd.relative_to(workspace_dir.resolve())) or "."
    except ValueError:
        relative_cwd = "<outside-workspace>"
    invocation_umask = os.umask(0)
    os.umask(invocation_umask)
    stdin_tty = os.isatty(0)
    stdout_tty = os.isatty(1)
    try:
        terminal_size = os.get_terminal_size(0)
        terminal = {{"columns": terminal_size.columns, "lines": terminal_size.lines}}
    except OSError:
        terminal = None
    request = {{
        "argv": sys.argv[1:] or ["help"],
        "cwd": relative_cwd,
        "umask": f"{{invocation_umask:03o}}",
        "stdin_tty": stdin_tty,
        "stdout_tty": stdout_tty,
        "terminal": terminal,
        "context_env": {{key: value for key, value in os.environ.items() if key.startswith("HBCTX_")}},
    }}
    try:
        endpoint = load_endpoint(workspace_dir)
        response = exchange(endpoint, request, workspace_dir)
    except (OSError, TimeoutError, RuntimeError) as exc:
        print(json.dumps({{"ok": False, "error": "business simulator unavailable: " + str(exc)}}))
        return 2
    if isinstance(response, dict) and "_transport_interactive" in response:
        specification = response.get("_transport_interactive")
        timeout_sec = specification.get("read_timeout_sec") if isinstance(specification, dict) else None
        if (
            not isinstance(specification, dict)
            or not isinstance(specification.get("token"), str)
            or not isinstance(specification.get("steps"), list)
            or (
                timeout_sec is not None
                and (
                    isinstance(timeout_sec, bool)
                    or not isinstance(timeout_sec, (int, float))
                    or timeout_sec <= 0
                    or timeout_sec > 300
                )
            )
            or not stdin_tty
            or not stdout_tty
        ):
            print(json.dumps({{"ok": False, "error": "interactive transport requires a valid PTY"}}))
            return 2
        collected = []
        for step in specification["steps"]:
            if not isinstance(step, dict) or not isinstance(step.get("prompt"), str):
                print(json.dumps({{"ok": False, "error": "invalid interactive step"}}))
                return 2
            mode = step.get("mode")
            sys.stdout.write(step["prompt"])
            sys.stdout.flush()
            if mode not in ("canonical", "raw", "eof"):
                print(json.dumps({{"ok": False, "error": "unsupported interactive mode"}}))
                return 2
            try:
                value = read_interactive_input(mode, timeout_sec)
            except InteractiveInputTimeout:
                print(json.dumps({{
                    "ok": False,
                    "error": "INTERACTIVE_INPUT_TIMEOUT",
                    "prompt": step["prompt"],
                    "timeout_sec": timeout_sec,
                }}))
                return 3
            if mode in ("canonical", "eof"):
                collected.append({{"mode": mode, "value": value.rstrip("\\r\\n"), "eof": value == ""}})
            else:
                collected.append({{"mode": mode, "value": value.decode("utf-8", "replace"), "eof": value == b""}})
                sys.stdout.write("\\r\\n")
                sys.stdout.flush()
        follow_request = dict(request)
        follow_request["argv"] = ["interactive-submit", specification["token"]]
        follow_request["interactive_responses"] = collected
        try:
            response = exchange(endpoint, follow_request, workspace_dir)
        except (OSError, TimeoutError, RuntimeError) as exc:
            print(json.dumps({{"ok": False, "error": "interactive simulator unavailable: " + str(exc)}}))
            return 2
    if isinstance(response, dict) and "_transport_stderr" in response:
        stderr_value = response.get("_transport_stderr")
        if not isinstance(stderr_value, str):
            print(json.dumps({{"ok": False, "error": "invalid native stderr"}}))
            return 2
        sys.stderr.write(stderr_value)
        if stderr_value and not stderr_value.endswith("\\n"):
            sys.stderr.write("\\n")
        sys.stderr.flush()
    raw_stdout = response.get("_transport_stdout") if isinstance(response, dict) else None
    if raw_stdout is not None:
        if not isinstance(raw_stdout, str):
            print(json.dumps({{"ok": False, "error": "invalid native stdout"}}))
            return 2
        sys.stdout.write(raw_stdout)
        sys.stdout.flush()
    else:
        public_response = (
            {{key: value for key, value in response.items() if key != "_transport_stderr"}}
            if isinstance(response, dict)
            else response
        )
        print(json.dumps(public_response, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    if isinstance(response, dict) and "_transport_signal" in response:
        value = response.get("_transport_signal")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 64:
            print(json.dumps({{"ok": False, "error": "invalid native signal"}}), flush=True)
            return 2
        os.kill(os.getpid(), value)
        return 128 + value
    if isinstance(response, dict) and "_transport_exit_code" in response:
        value = response.get("_transport_exit_code")
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
            print(json.dumps({{"ok": False, "error": "invalid native exit code"}}))
            return 2
        return value
    return 1 if isinstance(response, dict) and response.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def write_simulator_infrastructure_trace(
    *,
    layout: Any,
    harness: str,
    model: str,
    prompt: str,
    error: BusinessSimulatorStartupError,
) -> dict[str, Any]:
    """Materialize a normal trace-shaped record for a pre-harness failure."""

    run_id = f"run-infrastructure-{uuid.uuid4().hex}"
    trace_dir = layout.artifacts_dir / "trace" / run_id
    trace_dir.mkdir(parents=True, exist_ok=False)
    events_path = trace_dir / "events.json"
    summary_path = trace_dir / "summary.json"
    last_message_path = trace_dir / "last_message.txt"
    started_at = utc_now_iso()
    started_monotonic = time.monotonic()
    writer = EventWriter(events_path, run_id=run_id)
    error_message = str(error)
    writer.write(
        "run_failed",
        error_type="business_simulator_startup",
        error_message=error_message,
        infrastructure_error=True,
        harness_started=False,
    )
    finished_at = utc_now_iso()
    summary = build_summary(
        writer=writer,
        run_id=run_id,
        run_dir=trace_dir,
        harness=harness,
        model=model,
        input_query=prompt,
        status="infrastructure_error",
        trace_complete=False,
        structured_trace_status="unavailable",
        exit_code=None,
        timed_out=False,
        error_type="business_simulator_startup",
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((time.monotonic() - started_monotonic) * 1000),
        session_id=None,
        cwd=str(layout.agent_dir),
        argv=[],
        environment={},
        summary_path=summary_path,
    )
    writer.close()
    write_summary(summary, summary_path)
    write_last_message(summary, last_message_path)
    return summary


def persist_business_simulator_evidence(
    task: TaskSpec,
    layout: Any,
    *,
    harness: str | None = None,
    model: str | None = None,
) -> dict[str, str] | None:
    """Copy runner-owned evidence after verification and describe its integrity.

    This must be called only after the harness has exited and the socket service
    has stopped.  Until then, the ledger and state remain solely in the private
    tree.  The resulting artifacts are intended for benchmark audit, not agent
    input.
    """

    if task.business_simulator is None:
        return None
    source_dir = layout.private_dir / "simulator"
    evidence_dir = layout.artifacts_dir / "private_evidence" / "business_simulator"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    source_ledger = source_dir / LEDGER_FILENAME
    source_state = source_dir / STATE_FILENAME
    copied_ledger = evidence_dir / LEDGER_FILENAME
    copied_state = evidence_dir / "final_state.json"
    source_instance = source_dir / INSTANCE_FILENAME
    copied_instance = evidence_dir / INSTANCE_FILENAME
    source_release_manifest = source_dir / RELEASE_MANIFEST_FILENAME
    copied_release_manifest = evidence_dir / "release_manifest.json"
    source_scenario = source_dir / "scenario.json"
    copied_scenario = evidence_dir / "scenario.json"

    paths: dict[str, str] = {}
    if source_ledger.is_file():
        shutil.copy2(source_ledger, copied_ledger)
        copied_ledger.chmod(0o600)
        paths["ledger"] = str(copied_ledger)
    if source_state.is_file():
        shutil.copy2(source_state, copied_state)
        copied_state.chmod(0o600)
        paths["final_state"] = str(copied_state)
    if source_instance.is_file():
        shutil.copy2(source_instance, copied_instance)
        copied_instance.chmod(0o600)
        paths["instance"] = str(copied_instance)
    if source_release_manifest.is_file():
        shutil.copy2(source_release_manifest, copied_release_manifest)
        copied_release_manifest.chmod(0o600)
        paths["release_manifest"] = str(copied_release_manifest)
    if source_scenario.is_file():
        shutil.copy2(source_scenario, copied_scenario)
        copied_scenario.chmod(0o600)
        paths["scenario"] = str(copied_scenario)

    records, parse_errors = _read_ledger_records(copied_ledger)
    chain = validate_ledger_chain(
        records,
        parse_errors=parse_errors,
        expected_task_id=task.task_id,
    )
    sequences = [item.get("seq") for item in records]
    integer_sequences = [value for value in sequences if isinstance(value, int)]
    expected_sequences = list(range(1, len(records) + 1))
    state_value: Any = None
    state_parse_error: str | None = None
    if copied_state.is_file():
        try:
            state_value = json.loads(copied_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            state_parse_error = f"{type(exc).__name__}: {exc}"
    canonical_state_sha256 = (
        _sha256_json(state_value)
        if state_parse_error is None and copied_state.is_file()
        else None
    )
    final_ledger_state_sha256 = records[-1].get("state_sha256") if records else None
    final_committed_sequence = next(
        (
            item.get("seq")
            for item in reversed(records)
            if item.get("committed") is True and isinstance(item.get("seq"), int)
        ),
        None,
    )
    state_matches_ledger = (
        canonical_state_sha256 == final_ledger_state_sha256
        if canonical_state_sha256 is not None and final_ledger_state_sha256 is not None
        else None
    )
    evidence_available = copied_ledger.is_file() and copied_state.is_file()
    instance_value: dict[str, Any] | None = None
    instance_parse_error: str | None = None
    if copied_instance.is_file():
        try:
            parsed_instance = json.loads(copied_instance.read_text(encoding="utf-8"))
            if not isinstance(parsed_instance, dict):
                raise ValueError("instance metadata is not a JSON object")
            instance_value = parsed_instance
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            instance_parse_error = f"{type(exc).__name__}: {exc}"
    private_scenario_sha256: str | None = None
    private_scenario_parse_error: str | None = None
    private_scenario = source_scenario
    if private_scenario.is_file():
        try:
            private_scenario_sha256 = _sha256_json(
                json.loads(private_scenario.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            private_scenario_parse_error = f"{type(exc).__name__}: {exc}"
    instance_matches_scenario = (
        instance_value.get("scenario_sha256") == private_scenario_sha256
        if instance_value is not None and private_scenario_sha256 is not None
        else None
    )
    release_manifest_value: dict[str, Any] | None = None
    release_manifest_parse_error: str | None = None
    if copied_release_manifest.is_file():
        try:
            parsed_release_manifest = json.loads(
                copied_release_manifest.read_text(encoding="utf-8")
            )
            if not isinstance(parsed_release_manifest, dict):
                raise ValueError("release manifest is not a JSON object")
            release_manifest_value = parsed_release_manifest
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            release_manifest_parse_error = f"{type(exc).__name__}: {exc}"
    expected_release_seed = None
    if instance_value is not None:
        expected_release_seed = (
            instance_value.get("seed_sha256") or UNSEEDED_INSTANCE_SHA256
        )
    release_validation = {"valid": False, "errors": []}
    if (
        release_manifest_value is not None
        and copied_ledger.is_file()
        and copied_state.is_file()
        and state_parse_error is None
    ):
        release_validation = validate_release_evidence(
            records,
            state_value,
            release_manifest_value,
            ledger_sha256=_sha256_file(copied_ledger),
            expected_task_id=task.task_id,
            expected_seed_sha256=expected_release_seed,
        )
    integrity_valid = bool(
        evidence_available
        and chain["valid"]
        and state_parse_error is None
        and instance_parse_error is None
        and private_scenario_parse_error is None
        and copied_scenario.is_file()
        and private_scenario_sha256 is not None
        and instance_matches_scenario is not False
        and (state_matches_ledger is not False)
        and release_manifest_parse_error is None
        and release_validation["valid"]
    )
    manifest = {
        "schema_version": "business_simulator_evidence_v1",
        "evidence_owner": "harnessbench-runner",
        "task_id": task.task_id,
        "run_id": layout.run_dir.name,
        "harness": harness,
        "model": model,
        "status": (
            "complete"
            if integrity_valid
            else "integrity_error" if evidence_available else "unavailable"
        ),
        "integrity_valid": integrity_valid,
        "ledger": {
            "path": copied_ledger.name if copied_ledger.is_file() else None,
            "sha256": _sha256_file(copied_ledger) if copied_ledger.is_file() else None,
            "record_count": len(records),
            "sequences": sequences,
            "integer_sequences": integer_sequences,
            "expected_sequences": expected_sequences,
            "sequence_contiguous": sequences == expected_sequences,
            "parse_errors": parse_errors,
            "chain_valid": chain["valid"],
            "chain_errors": chain["errors"],
            "genesis_record_sha256": GENESIS_RECORD_SHA256,
            "final_record_sha256": chain["final_record_sha256"],
            "final_state_revision": chain["final_state_revision"],
            "final_committed_sequence": final_committed_sequence,
            "final_record_state_sha256": final_ledger_state_sha256,
        },
        "budget": {
            "max_simulator_requests": getattr(task, "max_tool_calls", None),
            "observed_simulator_requests": len(records),
            "exceeded": (
                len(records) > getattr(task, "max_tool_calls", None)
                if getattr(task, "max_tool_calls", None) is not None
                else False
            ),
        },
        "final_state": {
            "path": copied_state.name if copied_state.is_file() else None,
            "file_sha256": (
                _sha256_file(copied_state) if copied_state.is_file() else None
            ),
            "canonical_sha256": canonical_state_sha256,
            "parse_error": state_parse_error,
            "matches_final_ledger_record": state_matches_ledger,
        },
        "checkpoint": {
            "final_sequence": sequences[-1] if sequences else 0,
            "final_committed_sequence": final_committed_sequence,
            "final_state_revision": chain["final_state_revision"],
            "final_record_sha256": chain["final_record_sha256"],
            "state_sha256": canonical_state_sha256,
        },
        "instance": {
            "path": copied_instance.name if copied_instance.is_file() else None,
            "file_sha256": (
                _sha256_file(copied_instance) if copied_instance.is_file() else None
            ),
            "parse_error": instance_parse_error,
            "materialized": (
                instance_value.get("materialized") if instance_value else None
            ),
            "seed_sha256": (
                instance_value.get("seed_sha256") if instance_value else None
            ),
            "opaque_value_count": (
                instance_value.get("opaque_value_count") if instance_value else None
            ),
            "scenario_sha256": (
                instance_value.get("scenario_sha256") if instance_value else None
            ),
            "private_scenario_sha256": private_scenario_sha256,
            "private_scenario_parse_error": private_scenario_parse_error,
            "matches_private_scenario": instance_matches_scenario,
        },
        "scenario": {
            "path": copied_scenario.name if copied_scenario.is_file() else None,
            "file_sha256": (
                _sha256_file(copied_scenario) if copied_scenario.is_file() else None
            ),
            "canonical_sha256": private_scenario_sha256,
            "parse_error": private_scenario_parse_error,
        },
        "release_manifest": {
            "path": (
                copied_release_manifest.name
                if copied_release_manifest.is_file()
                else None
            ),
            "file_sha256": (
                _sha256_file(copied_release_manifest)
                if copied_release_manifest.is_file()
                else None
            ),
            "parse_error": release_manifest_parse_error,
            "schema_version": (
                release_manifest_value.get("schema_version")
                if release_manifest_value
                else None
            ),
            "status": (
                release_manifest_value.get("status") if release_manifest_value else None
            ),
            "valid": release_validation["valid"],
            "errors": release_validation["errors"],
        },
    }
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    paths["manifest"] = str(manifest_path)
    return paths


def _read_ledger_records(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not path.is_file():
        return records, errors
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line": line_number, "error": exc.msg})
            continue
        if not isinstance(value, dict):
            errors.append({"line": line_number, "error": "record is not a JSON object"})
            continue
        records.append(value)
    return records, errors


def validate_ledger_chain(
    records: list[dict[str, Any]],
    *,
    parse_errors: list[dict[str, Any]] | None = None,
    expected_task_id: str | None = None,
) -> dict[str, Any]:
    """Recompute all ledger hashes, revisions, and links for independent audit."""

    errors: list[dict[str, Any]] = list(parse_errors or [])
    previous_hash = GENESIS_RECORD_SHA256
    expected_revision = 0
    for position, record in enumerate(records, start=1):
        sequence = record.get("seq")
        if sequence != position:
            errors.append(
                {
                    "seq": sequence,
                    "field": "seq",
                    "expected": position,
                    "actual": sequence,
                }
            )
        if expected_task_id is not None and record.get("task_id") != expected_task_id:
            errors.append(
                {
                    "seq": sequence,
                    "field": "task_id",
                    "expected": expected_task_id,
                    "actual": record.get("task_id"),
                }
            )
        committed = record.get("committed")
        if not isinstance(committed, bool):
            errors.append(
                {"seq": sequence, "field": "committed", "error": "expected boolean"}
            )
            committed = False
        if committed:
            expected_revision += 1
        if record.get("state_revision") != expected_revision:
            errors.append(
                {
                    "seq": sequence,
                    "field": "state_revision",
                    "expected": expected_revision,
                    "actual": record.get("state_revision"),
                }
            )
        expected_request_hash = _sha256_json(record.get("request"))
        if record.get("request_sha256") != expected_request_hash:
            errors.append(
                {
                    "seq": sequence,
                    "field": "request_sha256",
                    "expected": expected_request_hash,
                }
            )
        expected_response_hash = _sha256_json(record.get("response"))
        if record.get("response_sha256") != expected_response_hash:
            errors.append(
                {
                    "seq": sequence,
                    "field": "response_sha256",
                    "expected": expected_response_hash,
                }
            )
        if record.get("prev_record_sha256") != previous_hash:
            errors.append(
                {
                    "seq": sequence,
                    "field": "prev_record_sha256",
                    "expected": previous_hash,
                }
            )
        expected_record_hash = _record_sha256(record)
        if record.get("record_sha256") != expected_record_hash:
            errors.append(
                {
                    "seq": sequence,
                    "field": "record_sha256",
                    "expected": expected_record_hash,
                }
            )
        actual_record_hash = record.get("record_sha256")
        previous_hash = (
            actual_record_hash if isinstance(actual_record_hash, str) else ""
        )
    return {
        "valid": not errors,
        "errors": errors,
        "final_state_revision": expected_revision,
        "final_record_sha256": previous_hash if records else GENESIS_RECORD_SHA256,
    }


def validate_release_evidence(
    records: list[dict[str, Any]],
    state: Any,
    manifest: Any,
    *,
    ledger_sha256: str,
    expected_task_id: str,
    expected_seed_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate v2 run bindings in addition to the ordinary hash chain."""

    errors: list[dict[str, Any]] = []

    def error(field: str, detail: str, *, seq: Any = None) -> None:
        item: dict[str, Any] = {"field": field, "error": detail}
        if seq is not None:
            item["seq"] = seq
        errors.append(item)

    if not isinstance(manifest, dict):
        error("manifest", "expected JSON object")
        return {"valid": False, "errors": errors}
    if set(manifest) != RELEASE_MANIFEST_FIELDS:
        error(
            "manifest_fields",
            f"expected={sorted(RELEASE_MANIFEST_FIELDS)} actual={sorted(manifest)}",
        )
    if manifest.get("schema_version") != RELEASE_EVIDENCE_SCHEMA:
        error("schema_version", "unexpected release evidence schema")
    if manifest.get("evidence_owner") != RELEASE_EVIDENCE_OWNER:
        error("evidence_owner", "release evidence is not runner-owned")
    if manifest.get("task_id") != expected_task_id:
        error("task_id", "manifest task scope mismatch")
    if manifest.get("status") != "complete":
        error("status", "release evidence is not complete")
    for field in (
        "seed_sha256",
        "configuration_sha256",
        "authority_digest",
        "effect_digest",
        "lifecycle_digest",
        "ledger_sha256",
        "final_state_sha256",
    ):
        if not _is_sha256(manifest.get(field)):
            error(field, "expected lowercase SHA-256")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        error("run_id", "expected non-empty string")
        run_id = ""
    if (
        type(manifest.get("record_count")) is not int
        or manifest.get("record_count") < 0
    ):
        error("record_count", "expected non-negative integer")
    if (
        expected_seed_sha256 is not None
        and manifest.get("seed_sha256") != expected_seed_sha256
    ):
        error("seed_sha256", "manifest does not match instance metadata")

    chain = validate_ledger_chain(records, expected_task_id=expected_task_id)
    errors.extend(chain["errors"])
    expected_authority = _authority_context(
        task_id=expected_task_id,
        run_id=run_id,
    )
    expected_authority_digest = _sha256_json(expected_authority)
    if manifest.get("authority_digest") != expected_authority_digest:
        error("authority_digest", "authority context digest mismatch")
    expected_manifest_sha256 = release_manifest_binding_sha256(manifest)

    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            error("record", "expected JSON object", seq=position)
            continue
        sequence = record.get("seq", position)
        if set(record) != RELEASE_LEDGER_FIELDS:
            error("ledger_fields", "release ledger schema mismatch", seq=sequence)
        if record.get("run_id") != run_id:
            error("run_id", "row is bound to a different run", seq=sequence)
        if record.get("instance_seed_sha256") != manifest.get("seed_sha256"):
            error("instance_seed_sha256", "row seed binding mismatch", seq=sequence)
        if record.get("configuration_sha256") != manifest.get("configuration_sha256"):
            error("configuration_sha256", "row configuration mismatch", seq=sequence)
        if record.get("authority_context") != expected_authority:
            error("authority_context", "row authority mismatch", seq=sequence)
        try:
            expected_effect = _release_effect(record)
            expected_lifecycle = _release_lifecycle(record)
        except (KeyError, TypeError):
            error("derived_evidence", "core row fields unavailable", seq=sequence)
        else:
            if record.get("effect") != expected_effect:
                error("effect", "row effect is not derivable", seq=sequence)
            if record.get("lifecycle") != expected_lifecycle:
                error("lifecycle", "row lifecycle is not derivable", seq=sequence)
        if record.get("manifest_sha256") != expected_manifest_sha256:
            error("manifest_sha256", "row manifest binding mismatch", seq=sequence)

    if manifest.get("effect_digest") != _sha256_json(
        [record.get("effect") for record in records]
    ):
        error("effect_digest", "aggregate effect digest mismatch")
    if manifest.get("lifecycle_digest") != _sha256_json(
        [record.get("lifecycle") for record in records]
    ):
        error("lifecycle_digest", "aggregate lifecycle digest mismatch")
    if manifest.get("ledger_sha256") != ledger_sha256:
        error("ledger_sha256", "ledger file digest mismatch")
    if manifest.get("final_state_sha256") != _sha256_json(state):
        error("final_state_sha256", "final state digest mismatch")
    if manifest.get("record_count") != len(records):
        error("record_count", "ledger record count mismatch")
    if records and records[-1].get("state_sha256") != _sha256_json(state):
        error("state_sha256", "ledger tail does not match final state")
    return {"valid": not errors, "errors": errors}


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
