from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Sequence


_CONTAINMENT_FAILURE_EXIT_CODE = 190
_RESOURCE_LIMIT_EXIT_CODE = 191
_RESOURCE_MEASUREMENT_FAILURE_EXIT_CODE = 192
_OUTPUT_LIMIT_EXIT_CODE = 193
_DEFAULT_MAX_OUTPUT_BYTES = 16_000_000
_OUTPUT_READ_CHUNK_BYTES = 64 * 1024
# A short interval matters on Darwin, where there is no supported subreaper and
# NOTE_TRACK has been unsupported by the kernel for many years.  A native table
# scan takes well under a millisecond on the formal host; this interval closes
# the practical fork/setsid/reparent window without inspecting process memory.
_MONITOR_INTERVAL_SECONDS = 0.0005
_RSS_SAMPLE_INTERVAL_SECONDS = 0.01
_SANDBOX_TAG_SCAN_INTERVAL_SECONDS = 0.01
_SANDBOX_APPLY_CAPABILITY: bool | None = None
_SANDBOX_APPLY_LOCK = threading.Lock()
_SANDBOX_TAG_QUERY_TIMEOUT_SECONDS = 1.0
_SANDBOX_TAG_QUERY_INTERVAL_SECONDS = 0.005
_FORMAL_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_FORMAL_STAGING_ENV_KEYS = (
    "HOME",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "OPENCLAW_STATE_DIR",
    "HERMES_HOME",
    "OPENCLAW_WORKSPACE_DIR",
)
_STOP_BEFORE_EXEC = (
    "import os,signal,sys\n"
    "os.kill(os.getpid(), signal.SIGSTOP)\n"
    "try:\n"
    " os.execvpe(sys.argv[1], sys.argv[1:], os.environ)\n"
    "except OSError as exc:\n"
    " print(f'exec failed: {exc}', file=sys.stderr, flush=True)\n"
    " raise SystemExit(127)\n"
)
_DARWIN_INNER_STOP_BEFORE_EXEC = (
    'kill -s STOP "$$" || exit 125\n'
    'exec "$@"\n'
)
_DARWIN_INNER_WRAPPER = "/bin/sh"


def _is_formal_sandbox_prefix(command_prefix: Sequence[str]) -> bool:
    """Recognize the one prefix covered by the adversarial containment proof."""
    prefix = list(command_prefix)
    return (
        len(prefix) == 3
        and Path(prefix[0]) == _FORMAL_SANDBOX_EXEC
        and prefix[1] == "-f"
    )


def _sha256_descriptor(descriptor: int) -> bytes:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.digest()


def _formal_staging_roots(
    *, cwd: Path, process_env: dict[str, str]
) -> tuple[Path, ...]:
    """Return the target-writable/public roots that must be clean at launch."""
    candidates: list[tuple[str, Path]] = [("cwd", cwd)]
    for key in _FORMAL_STAGING_ENV_KEYS:
        value = process_env.get(key)
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            raise RuntimeError(f"formal containment {key} must be an absolute path")
        candidates.append((key, candidate))

    resolved: list[Path] = []
    broad_roots = {
        Path("/"),
        Path("/Users"),
        Path("/private"),
        Path("/private/tmp"),
        Path("/tmp"),
        Path("/var"),
        Path("/Library"),
        Path("/System"),
        Path("/usr"),
        Path("/bin"),
    }
    try:
        broad_roots.add(Path.home().resolve())
    except OSError:
        pass
    for label, candidate in candidates:
        try:
            root = candidate.resolve(strict=False)
        except OSError as exc:
            raise RuntimeError(
                f"cannot resolve formal containment staging root {label}"
            ) from exc
        if root in broad_roots:
            raise RuntimeError(
                f"formal containment staging root {label} is too broad to audit"
            )
        if not root.exists():
            continue
        try:
            info = root.lstat()
        except OSError as exc:
            raise RuntimeError(
                f"cannot inspect formal containment staging root {label}"
            ) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(
                f"formal containment staging root {label} is not a directory"
            )
        resolved.append(root)

    # Scan an ancestor only once when, for example, HOME and all XDG roots are
    # nested below the same fresh state directory.
    minimal: list[Path] = []
    for candidate in sorted(set(resolved), key=lambda item: len(item.parts)):
        if any(
            candidate == existing or candidate.is_relative_to(existing)
            for existing in minimal
        ):
            continue
        minimal.append(candidate)
    return tuple(minimal)


def _reject_pre_staged_sandbox_exec(*, cwd: Path, process_env: dict[str, str]) -> None:
    """Fail before exec if a formal lane already contains sandbox-exec bytes.

    The merged profile prevents the target from reading or executing the
    official binary.  This audit closes the complementary pre-staging route in
    the public workspace and fresh state roots without reading any process
    environment or command line.
    """
    try:
        official_descriptor = os.open(
            _FORMAL_SANDBOX_EXEC,
            os.O_RDONLY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise RuntimeError("cannot audit the formal sandbox-exec binary") from exc
    try:
        official_info = os.fstat(official_descriptor)
        if not stat.S_ISREG(official_info.st_mode):
            raise RuntimeError("formal sandbox-exec is not a regular file")
        official_digest = _sha256_descriptor(official_descriptor)
    finally:
        os.close(official_descriptor)

    official_identity = (official_info.st_dev, official_info.st_ino)

    def walk_error(error: OSError) -> None:
        raise RuntimeError("cannot audit a formal containment staging root") from error

    for root in _formal_staging_roots(cwd=cwd, process_env=process_env):
        for directory, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=walk_error,
        ):
            directory_path = Path(directory)
            # os.walk does not follow directory symlinks in this mode.  Seatbelt
            # resolves their targets too, so reject an alias to the official
            # binary and leave unrelated aliases to the formal path policy.
            for name in [*directory_names, *file_names]:
                candidate = directory_path / name
                try:
                    candidate_info = candidate.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError(
                        "cannot inspect a formal containment staging entry"
                    ) from exc

                if stat.S_ISLNK(candidate_info.st_mode):
                    try:
                        target_info = candidate.stat()
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise RuntimeError(
                            "cannot inspect a formal containment staging symlink"
                        ) from exc
                    if (target_info.st_dev, target_info.st_ino) == official_identity:
                        raise RuntimeError(
                            "formal containment staging contains a sandbox-exec alias"
                        )
                    continue
                if not stat.S_ISREG(candidate_info.st_mode):
                    continue
                if (candidate_info.st_dev, candidate_info.st_ino) == official_identity:
                    raise RuntimeError(
                        "formal containment staging contains sandbox-exec"
                    )
                if candidate_info.st_size != official_info.st_size:
                    continue

                try:
                    descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError(
                        "cannot audit a possible staged sandbox-exec clone"
                    ) from exc
                try:
                    opened_info = os.fstat(descriptor)
                    if not stat.S_ISREG(opened_info.st_mode):
                        continue
                    if opened_info.st_size != official_info.st_size:
                        continue
                    if _sha256_descriptor(descriptor) == official_digest:
                        raise RuntimeError(
                            "formal containment staging contains a sandbox-exec clone"
                        )
                finally:
                    os.close(descriptor)


@dataclass(frozen=True)
class CompletedProcessData:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error_type: str | None = None
    error_message: str | None = None
    containment: dict[str, object] | None = None
    peak_rss_bytes: int | None = None
    rss_limit_bytes: int | None = None
    rss_limit_exceeded: bool = False
    output_bytes_captured: int = 0
    output_limit_bytes: int | None = None
    output_limit_exceeded: bool = False
    stdout_line_observed_ns: tuple[int, ...] = ()


@dataclass(frozen=True)
class _ProcessRecord:
    pid: int
    ppid: int
    process_group: int
    # A PID alone is not an identity: it can be reused while cleanup is in
    # progress.  Linux exposes a boot-relative start tick; Darwin exposes a
    # start timeval.  Both are read without inspecting process environments.
    started: tuple[int, int]
    zombie: bool


@dataclass(frozen=True)
class _ContainmentReport:
    tracked_descendants: tuple[int, ...]
    residual_pids: tuple[int, ...]
    tracking_failed: bool
    tracked_identity_sha256: str | None = None
    drain_failed: bool = False
    peak_rss_bytes: int | None = None
    rss_sample_count: int = 0
    rss_measurement_failed: bool = False
    rss_limit_bytes: int | None = None
    rss_limit_exceeded: bool = False
    sandbox_tag_scan_failed: bool = False
    output_bytes_captured: int = 0
    output_limit_bytes: int | None = None
    output_limit_exceeded: bool = False

    @property
    def valid(self) -> bool:
        return not (self.residual_pids or self.tracking_failed or self.drain_failed)

    @property
    def resource_valid(self) -> bool:
        if self.rss_limit_bytes is None:
            return True
        return bool(
            self.rss_sample_count > 0
            and not self.rss_measurement_failed
            and not self.rss_limit_exceeded
        )

    @property
    def output_valid(self) -> bool:
        return not self.output_limit_exceeded

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema_version": "harness-process-containment-v3",
            "valid": self.valid and self.resource_valid and self.output_valid,
            "descendant_cleanup_valid": self.valid,
            "tracked_descendant_count": len(self.tracked_descendants),
            # Do not persist host PIDs. The hash still binds the in-memory
            # identity set used for cleanup to this receipt.
            "tracked_identity_sha256": self.tracked_identity_sha256,
            "residual_process_count": len(self.residual_pids),
            "tracking_failed": self.tracking_failed,
            "pipe_drain_failed": self.drain_failed,
            "peak_rss_bytes": self.peak_rss_bytes,
            "rss_sample_count": self.rss_sample_count,
            "rss_measurement_failed": self.rss_measurement_failed,
            "rss_limit_bytes": self.rss_limit_bytes,
            "rss_limit_exceeded": self.rss_limit_exceeded,
            "sandbox_tag_scan_failed": self.sandbox_tag_scan_failed,
            "output_bytes_captured": self.output_bytes_captured,
            "output_limit_bytes": self.output_limit_bytes,
            "output_limit_exceeded": self.output_limit_exceeded,
            "raw_pids_persisted": False,
            "raw_paths_persisted": False,
        }


class _ProcessTable:
    """Read only PID, PPID, process-group, state, and start identity.

    In particular, this code never calls ``psutil.Process.environ``, reads
    ``/proc/*/environ``, or invokes ``ps`` with an environment column.  The
    narrow native views make the containment mechanism independently
    auditable and avoid adding a runtime dependency to harness-tracer.
    """

    def __init__(self) -> None:
        if sys.platform == "darwin":
            self._reader = _DarwinProcessTable()
        elif sys.platform.startswith("linux"):
            self._reader = _LinuxProcessTable()
        else:
            raise RuntimeError(
                "process containment is unavailable on this operating system"
            )

    def snapshot(self) -> dict[int, _ProcessRecord]:
        return self._reader.snapshot()

    def record(self, pid: int) -> _ProcessRecord | None:
        return self._reader.record(pid)


class _LinuxProcessTable:
    def snapshot(self) -> dict[int, _ProcessRecord]:
        records: dict[int, _ProcessRecord] = {}
        try:
            entries = list(Path("/proc").iterdir())
        except OSError as exc:
            raise RuntimeError("cannot enumerate the Linux process table") from exc
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            record = self.record(int(entry.name))
            if record is not None:
                records[record.pid] = record
        return records

    def record(self, pid: int) -> _ProcessRecord | None:
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            # The command name is parenthesized and may itself contain spaces
            # or ')', so split after the final closing parenthesis.
            fields = stat_text[stat_text.rfind(")") + 2 :].split()
            state = fields[0]
            ppid = int(fields[1])
            process_group = int(fields[2])
            start_ticks = int(fields[19])
        except (OSError, ValueError, IndexError):
            # Exiting processes routinely disappear between enumeration and
            # stat reads. A subsequent snapshot observes their children.
            return None
        return _ProcessRecord(
            pid=pid,
            ppid=ppid,
            process_group=process_group,
            started=(start_ticks, 0),
            zombie=state == "Z",
        )


class _DarwinProcBSDInfo(ctypes.Structure):
    # struct proc_bsdinfo from <libproc.h>.  Keeping the complete structure is
    # important because the start timeval is at its tail.
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class _DarwinRusageInfoV2(ctypes.Structure):
    """Public ``rusage_info_v2`` layout from <sys/resource.h>."""

    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
    ]


class _DarwinProcessTable:
    _PROC_PIDTBSDINFO = 3
    _SZOMB = 5

    def __init__(self) -> None:
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError as exc:
            raise RuntimeError("cannot load the Darwin process table API") from exc
        library.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.proc_listallpids.restype = ctypes.c_int
        library.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        library.proc_pidinfo.restype = ctypes.c_int
        self._library = library

    def snapshot(self) -> dict[int, _ProcessRecord]:
        # Leave headroom for forks between the sizing and fill calls.  If the
        # buffer is nevertheless filled, retry with a larger buffer rather
        # than silently accepting a truncated containment view.
        capacity = max(256, self._library.proc_listallpids(None, 0) + 256)
        for _attempt in range(3):
            pid_buffer = (ctypes.c_int * capacity)()
            count = self._library.proc_listallpids(
                pid_buffer, ctypes.sizeof(pid_buffer)
            )
            if count < 0:
                raise RuntimeError("cannot enumerate the Darwin process table")
            if count < capacity:
                break
            capacity *= 2
        else:
            raise RuntimeError("Darwin process table changed too quickly to audit")

        records: dict[int, _ProcessRecord] = {}
        for pid in pid_buffer[:count]:
            record = self.record(pid)
            if record is None:
                # Permission-restricted unrelated processes and processes that
                # exit during the snapshot are irrelevant to our known tree.
                continue
            records[pid] = record
        return records

    def record(self, pid: int) -> _ProcessRecord | None:
        if pid <= 0:
            return None
        info = _DarwinProcBSDInfo()
        info_size = ctypes.sizeof(info)
        copied = self._library.proc_pidinfo(
            pid,
            self._PROC_PIDTBSDINFO,
            0,
            ctypes.byref(info),
            info_size,
        )
        if copied != info_size:
            return None
        return _ProcessRecord(
            pid=pid,
            ppid=int(info.pbi_ppid),
            process_group=int(info.pbi_pgid),
            started=(
                int(info.pbi_start_tvsec),
                int(info.pbi_start_tvusec),
            ),
            zombie=int(info.pbi_status) == self._SZOMB,
        )


class _ResidentMemoryReader:
    """Read one exact process identity's current resident memory."""

    def __init__(self) -> None:
        if sys.platform == "darwin":
            self._reader = _DarwinResidentMemoryReader()
        elif sys.platform.startswith("linux"):
            self._reader = _LinuxResidentMemoryReader()
        else:
            raise RuntimeError(
                "resident-memory enforcement is unavailable on this operating system"
            )

    def bytes_for(self, record: _ProcessRecord) -> int | None:
        return self._reader.bytes_for(record)


class _LinuxResidentMemoryReader:
    def __init__(self) -> None:
        self._page_size = int(os.sysconf("SC_PAGE_SIZE"))

    def bytes_for(self, record: _ProcessRecord) -> int | None:
        try:
            fields = Path(f"/proc/{record.pid}/statm").read_text(
                encoding="ascii"
            ).split()
            resident_pages = int(fields[1])
            stat_text = Path(f"/proc/{record.pid}/stat").read_text(
                encoding="ascii"
            )
            stat_fields = stat_text[stat_text.rfind(")") + 2 :].split()
            observed_start = int(stat_fields[19])
        except (OSError, ValueError, IndexError):
            return None
        if record.started != (observed_start, 0):
            return None
        return max(0, resident_pages * self._page_size)


class _DarwinResidentMemoryReader:
    _RUSAGE_INFO_V2 = 2

    def __init__(self) -> None:
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError as exc:
            raise RuntimeError("cannot load Darwin resident-memory API") from exc
        function = library.proc_pid_rusage
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_DarwinRusageInfoV2),
        ]
        function.restype = ctypes.c_int
        self._function = function
        self._identity = _DarwinProcessTable()

    def bytes_for(self, record: _ProcessRecord) -> int | None:
        info = _DarwinRusageInfoV2()
        if self._function(
            record.pid,
            self._RUSAGE_INFO_V2,
            ctypes.byref(info),
        ) != 0:
            return None
        # Revalidate the PID generation after the resource query. This closes
        # the ordinary PID-reuse path without reading argv or environment.
        current = self._identity.record(record.pid)
        if current is None or current.started != record.started:
            return None
        # Physical footprint includes compressed/I/O-mapped accounting that
        # better reflects host pressure. Fall back to resident size only when
        # the kernel reports no footprint value.
        footprint = int(info.ri_phys_footprint)
        return max(0, footprint or int(info.ri_resident_size))


class _DarwinSandboxTag:
    """An inherited, runner-private Seatbelt label for detached descendants.

    Session and parent identities are mutable: an agent process can call
    ``setsid`` and double-fork.  A sandbox restriction, in contrast, is
    inherited by every descendant and cannot be removed.  A unique existing
    file is denied while a sibling control file remains readable; querying both
    decisions identifies this lane without exposing the nonce in argv or env.
    When the Harness already has a formal ``sandbox-exec -f`` prefix, the tag
    rules are merged into that profile so macOS applies Seatbelt exactly once.
    """

    _FILTER_PATH = 1
    _OPERATION = b"file-read-data"

    def __init__(self, *, cwd: Path) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Seatbelt process tagging requires Darwin")
        sandbox_exec = _FORMAL_SANDBOX_EXEC
        if not sandbox_exec.is_file():
            raise RuntimeError("sandbox-exec is unavailable for process tagging")
        try:
            library = ctypes.CDLL("/usr/lib/libsandbox.1.dylib", use_errno=True)
        except OSError as exc:
            raise RuntimeError("cannot load the Darwin sandbox query API") from exc
        check = library.sandbox_check
        check.restype = ctypes.c_int
        # sandbox_check is variadic.  Declaring its fixed arguments and passing
        # an explicit c_char_p for the path avoids ctypes treating bytes as an
        # opaque Python-object pointer.
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self._check = check
        nonce = secrets.token_hex(24)
        self.root = cwd.resolve()
        self.directory = self.root / f".harness-process-containment-{nonce}"
        self.path = self.directory / "tag"
        self.control_path = self.directory / "control"
        self._identities: dict[Path, tuple[int, int]] = {}
        try:
            self.directory.mkdir(mode=0o700)
            for candidate in (self.path, self.control_path):
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                os.close(descriptor)
            for candidate in (self.directory, self.path, self.control_path):
                info = candidate.lstat()
                self._identities[candidate] = (info.st_dev, info.st_ino)
            self._path_argument = ctypes.c_char_p(str(self.path).encode("utf-8"))
            self._control_argument = ctypes.c_char_p(
                str(self.control_path).encode("utf-8")
            )
            if not self._wait_until_paths_queryable():
                raise RuntimeError(
                    "runner cannot query an allowed Seatbelt process-tag path"
                )
            baseline = _DarwinProcessTable().snapshot()
        except BaseException:
            self._remove_unchecked()
            raise
        self._preexisting_denials = {
            pid: record.started
            for pid, record in baseline.items()
            if self._has_tag_decision(pid)
        }
        self.profile = "(version 1)\n(allow default)\n" + self._tag_rules()

    def wrap(
        self,
        invocation: Sequence[str],
        *,
        command_prefix: Sequence[str] = (),
    ) -> list[str]:
        # The inner stopped exec verifies that the tag is active before any
        # untrusted harness byte executes.  Once it resumes, it replaces its
        # argv with `invocation`, so the random path never reaches the agent.
        combined_prefix = self._combined_sandbox_prefix(command_prefix)
        if combined_prefix is None:
            sandbox_prefix = ["/usr/bin/sandbox-exec", "-p", self.profile]
            target_invocation = [*command_prefix, *invocation]
        else:
            sandbox_prefix = combined_prefix
            target_invocation = list(invocation)
        return [
            *sandbox_prefix,
            # The inner wrapper runs after the formal Seatbelt profile is
            # active.  It must not depend on the runner's Python prefix (for
            # example /opt/anaconda3), because reopening that broad tree would
            # expose unrelated tools and packages to the evaluated agent.
            # ``-p`` prevents shell startup hooks/functions from the inherited
            # environment, while the fixed script passes the target only as
            # positional arguments and never interpolates it as shell code.
            _DARWIN_INNER_WRAPPER,
            "-p",
            "-c",
            _DARWIN_INNER_STOP_BEFORE_EXEC,
            "harness-stop-wrapper",
            *target_invocation,
        ]

    def _combined_sandbox_prefix(
        self, command_prefix: Sequence[str]
    ) -> list[str] | None:
        prefix = list(command_prefix)
        if not _is_formal_sandbox_prefix(prefix):
            return None
        source_path = Path(prefix[2])
        if not source_path.is_absolute():
            source_path = self.root / source_path
        descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            source_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(source_info.st_mode)
                or source_info.st_uid != os.getuid()
                or stat.S_IMODE(source_info.st_mode) & 0o022
                or source_info.st_size > 1_048_576
            ):
                raise RuntimeError("formal Seatbelt profile identity is unsafe")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                source_profile = stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        combined_path = self.directory / "combined.sb"
        combined_profile = (
            source_profile.rstrip()
            + "\n"
            + self._tag_rules(combined_profile_path=combined_path)
        )
        output_descriptor = os.open(
            combined_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(output_descriptor, "w", encoding="utf-8") as stream:
                output_descriptor = -1
                stream.write(combined_profile)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if output_descriptor >= 0:
                os.close(output_descriptor)
        info = combined_path.lstat()
        self.combined_profile_path = combined_path
        self._identities[combined_path] = (info.st_dev, info.st_ino)
        return [prefix[0], "-f", str(combined_path)]

    def _tag_rules(self, *, combined_profile_path: Path | None = None) -> str:
        rules = [
            f"(deny file-read-data (literal {json.dumps(str(self.path))}))",
            f"(deny file-write* (subpath {json.dumps(str(self.directory))}))",
            f"(deny file-write* (literal {json.dumps(str(self.directory))}))",
        ]
        if combined_profile_path is not None:
            rules.extend(
                (
                    "(deny file-read* "
                    f"(literal {json.dumps(str(combined_profile_path))}))",
                    "(deny file-read* "
                    f"(literal {json.dumps(str(_FORMAL_SANDBOX_EXEC))}))",
                    "(deny process-exec "
                    f"(literal {json.dumps(str(_FORMAL_SANDBOX_EXEC))}))",
                )
            )
        return "\n".join(rules) + "\n"

    def matches(self, record: _ProcessRecord) -> bool:
        return self._preexisting_denials.get(
            record.pid
        ) != record.started and self._has_tag_decision(record.pid)

    def _has_tag_decision(self, pid: int) -> bool:
        return (
            self._decision(pid, self._path_argument) == 1
            and self._decision(pid, self._control_argument) == 0
        )

    def _wait_until_paths_queryable(self) -> bool:
        """Bridge the short APFS-to-Seatbelt visibility race, fail closed."""
        deadline = time.monotonic() + _SANDBOX_TAG_QUERY_TIMEOUT_SECONDS
        while True:
            if (
                self._decision(os.getpid(), self._path_argument) == 0
                and self._decision(os.getpid(), self._control_argument) == 0
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_SANDBOX_TAG_QUERY_INTERVAL_SECONDS)

    def _decision(self, pid: int, path: ctypes.c_char_p) -> int:
        return int(
            self._check(
                pid,
                self._OPERATION,
                self._FILTER_PATH,
                path,
            )
        )

    def cleanup(self) -> None:
        for candidate, identity in self._identities.items():
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise RuntimeError(
                    "process containment tag was removed by the target"
                ) from exc
            if (info.st_dev, info.st_ino) != identity:
                raise RuntimeError(
                    "process containment tag identity changed during the run"
                )
        self._remove_unchecked()

    def _remove_unchecked(self) -> None:
        for candidate in (
            getattr(self, "path", None),
            getattr(self, "control_path", None),
            getattr(self, "combined_profile_path", None),
        ):
            if isinstance(candidate, Path):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
        directory = getattr(self, "directory", None)
        if isinstance(directory, Path):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass


class _DescendantTracker:
    """Continuously retain descendant identities across setsid/reparenting."""

    def __init__(
        self,
        root_pid: int,
        *,
        sandbox_tag: _DarwinSandboxTag | None = None,
        max_rss_bytes: int | None = None,
    ) -> None:
        self.root_pid = root_pid
        self._sandbox_tag = sandbox_tag
        self._table = _ProcessTable()
        initial = self._table.snapshot()
        root = initial.get(root_pid)
        if root is None or root.zombie:
            raise RuntimeError("could not identify the stopped harness process")
        root_session = _session_id(root.pid)
        if root_session < 0:
            raise RuntimeError("could not identify the stopped harness session")
        self._root_process_group = root.process_group
        self._root_session = root_session
        self._known: dict[int, tuple[int, int]] = {root_pid: root.started}
        self._observed_topology: dict[
            tuple[int, tuple[int, int]], tuple[int, int, int]
        ] = {
            (root.pid, root.started): (
                root.ppid,
                root.process_group,
                root_session,
            )
        }
        self._latest = initial
        self._tracking_failed = False
        self._rss_limit_bytes = max_rss_bytes
        self._rss_reader = (
            _ResidentMemoryReader() if max_rss_bytes is not None else None
        )
        self._peak_rss_bytes = 0
        self._rss_sample_count = 0
        self._rss_measurement_failed = False
        self._rss_limit_exceeded = False
        self._sandbox_tag_scan_failed = False
        self._next_rss_sample = 0.0
        self._next_sandbox_tag_scan = 0.0
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._monitor,
            name=f"harness-process-tree-{root_pid}",
            daemon=True,
        )
        if self._reserve_rss_sample(force=True):
            self._sample_rss(initial, dict(self._known))
        self._thread.start()

    @property
    def rss_limit_exceeded(self) -> bool:
        with self._lock:
            return self._rss_limit_exceeded

    @property
    def rss_measurement_failed(self) -> bool:
        with self._lock:
            return self._rss_measurement_failed

    def _monitor(self) -> None:
        while not self._stop.is_set():
            self._refresh()
            self._stop.wait(_MONITOR_INTERVAL_SECONDS)

    def _refresh(
        self,
        *,
        include_sandbox_tag: bool = False,
        force_rss: bool = False,
    ) -> None:
        # Cleanup calls refresh synchronously while the monitor is live.  A
        # separate scan lock keeps native table reads and closure updates
        # deterministic.
        with self._scan_lock:
            try:
                snapshot = self._table.snapshot()
                if not snapshot:
                    raise RuntimeError("empty process-table snapshot")
            except BaseException:
                with self._lock:
                    self._tracking_failed = True
                return

            with self._lock:
                known = dict(self._known)

            sample_rss = self._reserve_rss_sample(force=force_rss)

            # Numeric process-group IDs can be reused after the original group
            # disappears.  Expand by PGID only while a *previously known exact
            # identity* is still live in both the original group and session.
            # A new, unrelated member can therefore never bootstrap trust in a
            # reused numeric group.  Candidate members must independently be
            # in that same PGID+SID before they are retained.
            group_anchor_live = False
            for pid, identity in known.items():
                current = snapshot.get(pid)
                if (
                    current is None
                    or current.started != identity
                    or current.zombie
                    or current.process_group != self._root_process_group
                ):
                    continue
                session_id = self._exact_session_id(current)
                if session_id == self._root_session:
                    group_anchor_live = True
                    break

            if group_anchor_live:
                for record in snapshot.values():
                    if (
                        record.zombie
                        or record.process_group != self._root_process_group
                        or known.get(record.pid) == record.started
                    ):
                        continue
                    if self._exact_session_id(record) == self._root_session:
                        known[record.pid] = record.started

            # On Darwin the inherited Seatbelt decision is the only durable
            # identity across setsid+double-fork+reparent.  In formal runs scan
            # it on every reserved RSS sample, before that same sample is
            # aggregated, so a newly discovered detached process cannot escape
            # the lane's memory accounting.  Cleanup requests an extra scan.
            scheduled_tag_scan = self._reserve_sandbox_tag_scan(
                force=include_sandbox_tag
            )
            scan_sandbox_tag = self._sandbox_tag is not None and (
                include_sandbox_tag or sample_rss or scheduled_tag_scan
            )
            if scan_sandbox_tag:
                try:
                    for record in snapshot.values():
                        if record.zombie or not self._sandbox_tag.matches(record):
                            continue
                        # Bind the kernel decision to the same PID generation
                        # captured by the process-table scan.
                        current = self._table.record(record.pid)
                        if (
                            current is not None
                            and current.started == record.started
                            and not current.zombie
                        ):
                            known[record.pid] = record.started
                except BaseException:
                    with self._lock:
                        self._tracking_failed = True
                        self._sandbox_tag_scan_failed = True
                        if sample_rss:
                            self._rss_measurement_failed = True
                    return

            changed = True
            while changed:
                changed = False
                trusted_live = {
                    pid
                    for pid, identity in known.items()
                    if (current := snapshot.get(pid)) is not None
                    and current.started == identity
                }
                for record in snapshot.values():
                    if record.ppid not in trusted_live:
                        continue
                    if known.get(record.pid) == record.started:
                        continue
                    known[record.pid] = record.started
                    changed = True

            with self._lock:
                self._known.update(known)
                for pid, identity in known.items():
                    current = snapshot.get(pid)
                    if current is None or current.started != identity:
                        continue
                    self._observed_topology[(pid, identity)] = (
                        current.ppid,
                        current.process_group,
                        _session_id(pid),
                    )
                self._latest = snapshot
            if sample_rss:
                self._sample_rss(snapshot, known)

    def _reserve_rss_sample(self, *, force: bool) -> bool:
        if self._rss_reader is None:
            return False
        now = time.monotonic()
        with self._lock:
            if not force and now < self._next_rss_sample:
                return False
            self._next_rss_sample = now + _RSS_SAMPLE_INTERVAL_SECONDS
        return True

    def _reserve_sandbox_tag_scan(self, *, force: bool) -> bool:
        if self._sandbox_tag is None:
            return False
        now = time.monotonic()
        with self._lock:
            if not force and now < self._next_sandbox_tag_scan:
                return False
            self._next_sandbox_tag_scan = (
                now + _SANDBOX_TAG_SCAN_INTERVAL_SECONDS
            )
        return True

    def _exact_session_id(self, record: _ProcessRecord) -> int | None:
        """Read SID and rebind it to the same process generation."""
        session_id = _session_id(record.pid)
        try:
            current = self._table.record(record.pid)
        except BaseException:
            with self._lock:
                self._tracking_failed = True
            return None
        if (
            current is not None
            and current.started == record.started
            and not current.zombie
        ):
            if session_id < 0:
                with self._lock:
                    self._tracking_failed = True
                return None
            return session_id
        return None

    def _sample_rss(
        self,
        snapshot: dict[int, _ProcessRecord],
        known: dict[int, tuple[int, int]],
    ) -> None:
        reader = self._rss_reader
        if reader is None:
            return
        active = [
            record
            for pid, identity in known.items()
            if (record := snapshot.get(pid)) is not None
            and record.started == identity
            and not record.zombie
        ]
        if not active:
            return
        total = 0
        failed: list[_ProcessRecord] = []
        for record in active:
            observed = reader.bytes_for(record)
            if observed is None:
                failed.append(record)
            else:
                total += observed
        unresolved_failure = False
        if failed:
            # A process can exit between topology and RSS reads. Only a failed
            # read for an identity still live in a fresh table is an invalid
            # resource measurement.
            try:
                current = self._table.snapshot()
            except BaseException:
                unresolved_failure = True
            else:
                unresolved_failure = any(
                    (latest := current.get(record.pid)) is not None
                    and latest.started == record.started
                    and not latest.zombie
                    for record in failed
                )
        with self._lock:
            self._rss_sample_count += 1
            self._peak_rss_bytes = max(self._peak_rss_bytes, total)
            if unresolved_failure:
                self._rss_measurement_failed = True
            if (
                self._rss_limit_bytes is not None
                and total > self._rss_limit_bytes
            ):
                self._rss_limit_exceeded = True

    def terminate_all(self) -> _ContainmentReport:
        # SIGTERM gives well-behaved tools a chance to close files.  SIGKILL is
        # mandatory because agent-authored descendants may ignore SIGTERM.
        for action, grace in ((signal.SIGTERM, 0.25), (signal.SIGKILL, 1.5)):
            self._refresh(include_sandbox_tag=True, force_rss=True)
            self._signal_process_group(action)
            deadline = time.monotonic() + grace
            stable_empty_scans = 0
            required_empty_scans = 10 if action == signal.SIGKILL else 1
            while True:
                self._refresh(include_sandbox_tag=True, force_rss=True)
                active = self._active_records()
                for record in active:
                    self._signal_exact_record(record, action)
                if active:
                    stable_empty_scans = 0
                else:
                    # A single empty process-table view is not a cleanup fixed
                    # point: a just-forked, reparented process can become
                    # visible on the next scan.  Keep the inherited Seatbelt
                    # tag monitor live until several consecutive scans agree.
                    stable_empty_scans += 1
                if (
                    stable_empty_scans >= required_empty_scans
                    or time.monotonic() >= deadline
                ):
                    break
                time.sleep(0.01)

        self._stop.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            with self._lock:
                self._tracking_failed = True
        self._refresh(include_sandbox_tag=True, force_rss=True)
        residual = tuple(record.pid for record in self._active_records())
        with self._lock:
            tracked = tuple(sorted(pid for pid in self._known if pid != self.root_pid))
            failed = self._tracking_failed
            identity_payload = json.dumps(
                sorted(
                    [
                        pid,
                        started[0],
                        started[1],
                        *self._observed_topology.get(
                            (pid, started),
                            (-1, -1, -1),
                        ),
                    ]
                    for pid, started in self._known.items()
                ),
                separators=(",", ":"),
            ).encode("ascii")
            peak_rss = (
                self._peak_rss_bytes if self._rss_limit_bytes is not None else None
            )
            rss_samples = self._rss_sample_count
            rss_failed = self._rss_measurement_failed
            rss_exceeded = self._rss_limit_exceeded
            tag_scan_failed = self._sandbox_tag_scan_failed
        return _ContainmentReport(
            tracked_descendants=tracked,
            residual_pids=tuple(sorted(residual)),
            tracking_failed=failed,
            tracked_identity_sha256=hashlib.sha256(identity_payload).hexdigest(),
            peak_rss_bytes=peak_rss,
            rss_sample_count=rss_samples,
            rss_measurement_failed=rss_failed,
            rss_limit_bytes=self._rss_limit_bytes,
            rss_limit_exceeded=rss_exceeded,
            sandbox_tag_scan_failed=tag_scan_failed,
        )

    def _active_records(self) -> list[_ProcessRecord]:
        with self._lock:
            known = dict(self._known)
            snapshot = dict(self._latest)
        active = []
        for pid, identity in known.items():
            current = snapshot.get(pid)
            if current is None or current.started != identity or current.zombie:
                continue
            active.append(current)
        return active

    def _signal_process_group(self, action: signal.Signals) -> None:
        # A numeric PGID may be reused after its original group disappears.
        # Only address the group while a fresh table still proves that an
        # exact known identity belongs to our new session and root group.
        with self._lock:
            known = dict(self._known)
            snapshot = dict(self._latest)
        live_group_members: list[_ProcessRecord] = []
        for pid, identity in known.items():
            record = snapshot.get(pid)
            if (
                record is None
                or record.started != identity
                or record.process_group != self._root_process_group
                or record.zombie
            ):
                continue
            session_id = self._exact_session_id(record)
            if session_id is None:
                continue
            if session_id == self._root_session:
                live_group_members.append(record)
        if not live_group_members:
            return
        try:
            os.killpg(self._root_process_group, action)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can return EPERM for a process group whose only remaining
            # member is the already-dead session leader.  It is a containment
            # error only if our fresh process-table view still has a live
            # member in that group.
            if live_group_members:
                with self._lock:
                    self._tracking_failed = True

    def _signal_exact_record(
        self, record: _ProcessRecord, action: signal.Signals
    ) -> None:
        """Signal one PID only after rebinding it to the observed generation.

        Detached descendants cannot be addressed through the original process
        group. Re-read their native identity immediately before ``kill`` so a
        stale topology snapshot cannot target a subsequently reused PID.
        """

        try:
            current = self._table.record(record.pid)
        except BaseException:
            with self._lock:
                self._tracking_failed = True
            return
        if (
            current is None
            or current.started != record.started
            or current.zombie
        ):
            return
        try:
            os.kill(record.pid, action)
        except ProcessLookupError:
            pass
        except PermissionError:
            with self._lock:
                self._tracking_failed = True


def _session_id(pid: int) -> int:
    """Return a process session ID without persisting a raw process record."""
    try:
        return os.getsid(pid)
    except (OSError, ValueError):
        return -1


class _BoundedOutputCollector:
    """Drain both process pipes while retaining at most one shared byte cap."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        max_bytes: int,
    ) -> None:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("bounded output collection requires two pipes")
        self._streams: dict[str, BinaryIO] = {
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._line_buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._line_observed_ns = {"stdout": [], "stderr": []}
        self._max_bytes = max_bytes
        self._captured_bytes = 0
        self._observed_bytes = 0
        self._errors: list[BaseException] = []
        self._limit_exceeded = threading.Event()
        self._aborting = threading.Event()
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(
                target=self._drain,
                args=(name, stream),
                name=f"harness-{name}-drain",
                daemon=True,
            )
            for name, stream in self._streams.items()
        ]

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded.is_set()

    @property
    def is_alive(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    @property
    def failed(self) -> bool:
        with self._lock:
            return bool(self._errors)

    @property
    def captured_bytes(self) -> int:
        with self._lock:
            return self._captured_bytes

    @property
    def limit_bytes(self) -> int:
        return self._max_bytes

    def text(self, name: str) -> str:
        with self._lock:
            payload = bytes(self._buffers[name])
        return payload.decode("utf-8", errors="replace")

    def line_observed_ns(self, name: str) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._line_observed_ns[name])

    def wait(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    def abort(self) -> None:
        # Closing an fd wakes a reader blocked on a pipe inherited by an
        # otherwise unobservable descendant.  Expected close errors are not
        # treated as collection failures.
        self._aborting.set()
        for stream in self._streams.values():
            try:
                os.close(stream.fileno())
            except (OSError, ValueError):
                pass

    def _drain(self, name: str, stream: BinaryIO) -> None:
        try:
            while chunk := stream.readline(_OUTPUT_READ_CHUNK_BYTES):
                observed_ns = time.monotonic_ns()
                with self._lock:
                    self._observed_bytes += len(chunk)
                    remaining = self._max_bytes - self._captured_bytes
                    if remaining > 0:
                        retained = chunk[:remaining]
                        self._buffers[name].extend(retained)
                        self._captured_bytes += len(retained)
                        self._observe_retained_lines(name, retained, observed_ns)
                    if self._observed_bytes > self._max_bytes:
                        self._limit_exceeded.set()
        except BaseException as exc:
            if not self._aborting.is_set():
                with self._lock:
                    self._errors.append(exc)
        finally:
            observed_ns = time.monotonic_ns()
            with self._lock:
                if self._line_buffers[name]:
                    self._line_observed_ns[name].append(observed_ns)
                    self._line_buffers[name].clear()
            try:
                stream.close()
            except OSError:
                pass

    def _observe_retained_lines(
        self, name: str, payload: bytes, observed_ns: int
    ) -> None:
        line_buffer = self._line_buffers[name]
        start = 0
        while True:
            newline = payload.find(b"\n", start)
            if newline < 0:
                line_buffer.extend(payload[start:])
                return
            line_buffer.extend(payload[start:newline])
            self._line_observed_ns[name].append(observed_ns)
            line_buffer.clear()
            start = newline + 1


def _wait_until_stopped(
    process: subprocess.Popen[bytes], timeout: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            waited_pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
        except ChildProcessError as exc:
            raise RuntimeError("harness wrapper disappeared before tracking") from exc
        if waited_pid == process.pid:
            if os.WIFSTOPPED(status):
                return
            raise RuntimeError("harness wrapper exited before tracking")
        time.sleep(0.005)
    raise RuntimeError("harness wrapper did not stop before exec")


def _kill_untracked_stopped_wrapper(
    process: subprocess.Popen[bytes],
    collector: _BoundedOutputCollector,
) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    collector.wait(2)
    if collector.is_alive:
        collector.abort()
        collector.wait(1)


def _containment_error_message(report: _ContainmentReport) -> str:
    return (
        "process containment failed "
        f"(tracking_failed={str(report.tracking_failed).lower()}, "
        f"drain_failed={str(report.drain_failed).lower()}, "
        f"residual_process_count={len(report.residual_pids)})"
    )


def _resource_error_message(report: _ContainmentReport) -> str:
    return (
        "resident-memory gate failed "
        f"(measurement_failed={str(report.rss_measurement_failed).lower()}, "
        f"limit_exceeded={str(report.rss_limit_exceeded).lower()}, "
        f"peak_rss_bytes={report.peak_rss_bytes}, "
        f"rss_limit_bytes={report.rss_limit_bytes}, "
        f"sample_count={report.rss_sample_count})"
    )


def _output_error_message(report: _ContainmentReport) -> str:
    return (
        "process output gate failed "
        f"(limit_exceeded={str(report.output_limit_exceeded).lower()}, "
        f"captured_bytes={report.output_bytes_captured}, "
        f"output_limit_bytes={report.output_limit_bytes})"
    )


def _darwin_can_apply_sandbox() -> bool:
    global _SANDBOX_APPLY_CAPABILITY
    with _SANDBOX_APPLY_LOCK:
        if _SANDBOX_APPLY_CAPABILITY is not None:
            return _SANDBOX_APPLY_CAPABILITY
        try:
            probe = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    "(version 1) (allow default)",
                    "/usr/bin/true",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            _SANDBOX_APPLY_CAPABILITY = False
        else:
            _SANDBOX_APPLY_CAPABILITY = probe.returncode == 0
        return _SANDBOX_APPLY_CAPABILITY


class ProcessRunner:
    """Run a harness and synchronously collect every observable descendant.

    On Darwin, an exact official ``sandbox-exec -f`` prefix with a replacement
    environment selects the adversarial formal mode: containment rules are
    merged into that single profile and public staging is audited before exec.
    Other prefixes still receive process-table and inherited-tag cleanup as
    defense in depth, but are not the formal nested-sandbox security boundary.
    """

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        command_prefix: Sequence[str] = (),
        replace_env: bool = False,
        max_rss_bytes: int | None = None,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CompletedProcessData:
        original_command = list(command)
        invocation = [*command_prefix, *original_command]
        if not invocation:
            raise ValueError("command must not be empty")
        if max_rss_bytes is not None and (
            isinstance(max_rss_bytes, bool)
            or not isinstance(max_rss_bytes, int)
            or max_rss_bytes <= 0
        ):
            raise ValueError("max_rss_bytes must be a positive integer")
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes <= 0
        ):
            raise ValueError("max_output_bytes must be a positive integer")

        process_env = {} if replace_env else os.environ.copy()
        if env:
            process_env.update(env)

        sandbox_tag: _DarwinSandboxTag | None = None
        if sys.platform == "darwin":
            formal_sandbox = _is_formal_sandbox_prefix(command_prefix)
            if formal_sandbox and not replace_env:
                raise RuntimeError(
                    "formal process containment requires a replacement environment"
                )
            if formal_sandbox:
                _reject_pre_staged_sandbox_exec(
                    cwd=cwd,
                    process_env=process_env,
                )
            if _darwin_can_apply_sandbox():
                sandbox_tag = _DarwinSandboxTag(cwd=cwd)
            elif formal_sandbox:
                raise RuntimeError(
                    "formal process containment tag cannot be applied; "
                    "no harness process was launched"
                )
        try:
            contained_invocation = (
                sandbox_tag.wrap(
                    original_command,
                    command_prefix=command_prefix,
                )
                if sandbox_tag is not None
                else invocation
            )
            return _run_contained_process(
                contained_invocation=contained_invocation,
                original_command=original_command,
                cwd=cwd,
                timeout=timeout,
                process_env=process_env,
                sandbox_tag=sandbox_tag,
                max_rss_bytes=max_rss_bytes,
                max_output_bytes=max_output_bytes,
            )
        finally:
            if sandbox_tag is not None:
                sandbox_tag.cleanup()


def _run_contained_process(
    *,
    contained_invocation: Sequence[str],
    original_command: list[str],
    cwd: Path,
    timeout: int | None,
    process_env: dict[str, str],
    sandbox_tag: _DarwinSandboxTag | None,
    max_rss_bytes: int | None,
    max_output_bytes: int,
) -> CompletedProcessData:
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            _STOP_BEFORE_EXEC,
            *contained_invocation,
        ],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=process_env,
        start_new_session=True,
    )
    collector = _BoundedOutputCollector(process, max_bytes=max_output_bytes)
    collector.start()
    try:
        _wait_until_stopped(process)
        tracker = _DescendantTracker(
            process.pid,
            sandbox_tag=sandbox_tag,
            max_rss_bytes=max_rss_bytes,
        )
    except BaseException:
        _kill_untracked_stopped_wrapper(process, collector)
        raise

    tracker_terminated = False
    try:
        if tracker.rss_limit_exceeded or tracker.rss_measurement_failed:
            report = tracker.terminate_all()
            tracker_terminated = True
            return _resource_failure_before_exec(
                process=process,
                collector=collector,
                original_command=original_command,
                report=report,
            )
        if sandbox_tag is not None:
            # First resume applies the random outer Seatbelt tag; the trusted
            # inner wrapper immediately stops again.  Verify the kernel-visible
            # decisions before permitting any harness byte to exec.
            os.kill(process.pid, signal.SIGCONT)
            _wait_until_stopped(process)
            tagged_root = _DarwinProcessTable().snapshot().get(process.pid)
            if tagged_root is None or not sandbox_tag.matches(tagged_root):
                raise RuntimeError(
                    "process containment tag was not inherited before exec"
                )
            if tracker.rss_limit_exceeded or tracker.rss_measurement_failed:
                report = tracker.terminate_all()
                tracker_terminated = True
                return _resource_failure_before_exec(
                    process=process,
                    collector=collector,
                    original_command=original_command,
                    report=report,
                )
        os.kill(process.pid, signal.SIGCONT)
        deadline = None if timeout is None else time.monotonic() + timeout
        timed_out = False
        while collector.is_alive:
            if collector.limit_exceeded:
                break
            if tracker.rss_limit_exceeded or tracker.rss_measurement_failed:
                break
            if process.poll() is not None:
                # A pipe reader may still be blocked on descriptors inherited
                # by a detached background descendant. Start containment as
                # soon as the actual harness process exits.
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            collector.wait(0.01)

        report = tracker.terminate_all()
        tracker_terminated = True
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            report = replace(report, tracking_failed=True)
        collector.wait(3)
        if collector.is_alive:
            collector.abort()
            collector.wait(1)
        report = replace(
            report,
            drain_failed=collector.is_alive or collector.failed,
            output_bytes_captured=collector.captured_bytes,
            output_limit_bytes=max_output_bytes,
            output_limit_exceeded=collector.limit_exceeded,
        )

        stdout = collector.text("stdout")
        stderr = collector.text("stderr")
        stdout_line_observed_ns = collector.line_observed_ns("stdout")
        if not report.valid:
            message = _containment_error_message(report)
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"HARNESS_PROCESS_CONTAINMENT_FAILURE: {message}\n"
            return CompletedProcessData(
                args=original_command,
                returncode=(None if timed_out else _CONTAINMENT_FAILURE_EXIT_CODE),
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                error_type="process_containment_failure",
                error_message=message,
                containment=report.public_receipt(),
                peak_rss_bytes=report.peak_rss_bytes,
                rss_limit_bytes=report.rss_limit_bytes,
                rss_limit_exceeded=report.rss_limit_exceeded,
                output_bytes_captured=report.output_bytes_captured,
                output_limit_bytes=report.output_limit_bytes,
                output_limit_exceeded=report.output_limit_exceeded,
                stdout_line_observed_ns=stdout_line_observed_ns,
            )

        if not report.resource_valid:
            message = _resource_error_message(report)
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"HARNESS_RESOURCE_GATE_FAILURE: {message}\n"
            return CompletedProcessData(
                args=original_command,
                returncode=(
                    _RESOURCE_MEASUREMENT_FAILURE_EXIT_CODE
                    if report.rss_measurement_failed
                    else _RESOURCE_LIMIT_EXIT_CODE
                ),
                stdout=stdout,
                stderr=stderr,
                error_type=(
                    "resource_measurement_failure"
                    if report.rss_measurement_failed
                    else "memory_limit_exceeded"
                ),
                error_message=message,
                containment=report.public_receipt(),
                peak_rss_bytes=report.peak_rss_bytes,
                rss_limit_bytes=report.rss_limit_bytes,
                rss_limit_exceeded=report.rss_limit_exceeded,
                output_bytes_captured=report.output_bytes_captured,
                output_limit_bytes=report.output_limit_bytes,
                output_limit_exceeded=report.output_limit_exceeded,
                stdout_line_observed_ns=stdout_line_observed_ns,
            )

        if not report.output_valid:
            message = _output_error_message(report)
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"HARNESS_OUTPUT_GATE_FAILURE: {message}\n"
            return CompletedProcessData(
                args=original_command,
                returncode=_OUTPUT_LIMIT_EXIT_CODE,
                stdout=stdout,
                stderr=stderr,
                error_type="output_limit_exceeded",
                error_message=message,
                containment=report.public_receipt(),
                peak_rss_bytes=report.peak_rss_bytes,
                rss_limit_bytes=report.rss_limit_bytes,
                rss_limit_exceeded=report.rss_limit_exceeded,
                output_bytes_captured=report.output_bytes_captured,
                output_limit_bytes=report.output_limit_bytes,
                output_limit_exceeded=report.output_limit_exceeded,
                stdout_line_observed_ns=stdout_line_observed_ns,
            )

        if timed_out:
            return CompletedProcessData(
                args=original_command,
                returncode=None,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                error_type="timeout",
                error_message=f"process timed out after {timeout} seconds",
                containment=report.public_receipt(),
                peak_rss_bytes=report.peak_rss_bytes,
                rss_limit_bytes=report.rss_limit_bytes,
                rss_limit_exceeded=report.rss_limit_exceeded,
                output_bytes_captured=report.output_bytes_captured,
                output_limit_bytes=report.output_limit_bytes,
                output_limit_exceeded=report.output_limit_exceeded,
                stdout_line_observed_ns=stdout_line_observed_ns,
            )
        return CompletedProcessData(
            # Keep runner-private wrappers (notably the Seatbelt profile path)
            # out of adapters and persisted traces.
            args=original_command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            containment=report.public_receipt(),
            peak_rss_bytes=report.peak_rss_bytes,
            rss_limit_bytes=report.rss_limit_bytes,
            rss_limit_exceeded=report.rss_limit_exceeded,
            output_bytes_captured=report.output_bytes_captured,
            output_limit_bytes=report.output_limit_bytes,
            output_limit_exceeded=report.output_limit_exceeded,
            stdout_line_observed_ns=stdout_line_observed_ns,
        )
    except BaseException:
        if not tracker_terminated:
            tracker.terminate_all()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        collector.wait(2)
        if collector.is_alive:
            collector.abort()
            collector.wait(1)
        raise


def _resource_failure_before_exec(
    *,
    process: subprocess.Popen[bytes],
    collector: _BoundedOutputCollector,
    original_command: list[str],
    report: _ContainmentReport,
) -> CompletedProcessData:
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    collector.wait(2)
    if collector.is_alive:
        collector.abort()
        collector.wait(1)
    report = replace(
        report,
        drain_failed=collector.is_alive or collector.failed,
        output_bytes_captured=collector.captured_bytes,
        output_limit_bytes=collector.limit_bytes,
        output_limit_exceeded=collector.limit_exceeded,
    )
    stdout = collector.text("stdout")
    stderr = collector.text("stderr")
    message = _resource_error_message(report)
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    stderr += f"HARNESS_RESOURCE_GATE_FAILURE: {message}\n"
    return CompletedProcessData(
        args=original_command,
        returncode=(
            _RESOURCE_MEASUREMENT_FAILURE_EXIT_CODE
            if report.rss_measurement_failed
            else _RESOURCE_LIMIT_EXIT_CODE
        ),
        stdout=stdout,
        stderr=stderr,
        error_type=(
            "resource_measurement_failure"
            if report.rss_measurement_failed
            else "memory_limit_exceeded"
        ),
        error_message=message,
        containment=report.public_receipt(),
        peak_rss_bytes=report.peak_rss_bytes,
        rss_limit_bytes=report.rss_limit_bytes,
        rss_limit_exceeded=report.rss_limit_exceeded,
        output_bytes_captured=report.output_bytes_captured,
        output_limit_bytes=report.output_limit_bytes,
        output_limit_exceeded=report.output_limit_exceeded,
    )
