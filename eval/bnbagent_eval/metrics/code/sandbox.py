"""Run untrusted code in a confined subprocess (``code.tests_pass``, CLI syntax checks).

Never enabled on the public service (the metrics using it set ``service_allowed=False``).

**subprocess runtime (default, Linux only).** ``unshare`` creates user, network, mount and PID
namespaces (unprivileged; needs ``kernel.unprivileged_userns_clone`` and no AppArmor userns
restriction), then ``_launcher.py``:

- *network*: a fresh network namespace with no interfaces up (TCP, UDP, DNS all fail), plus a
  Landlock rule denying TCP bind/connect (Landlock ABI >= 4) and abstract-unix-socket scoping
  (ABI >= 6);
- *filesystem*: Landlock allows read/execute on system paths (``/usr``, ``/lib*``, ``/bin``, the
  Python prefix) and read/write only in the temporary work directory. ``/tmp``, ``/run``,
  ``/dev/shm`` and ``/var/tmp`` are hidden behind empty tmpfs mounts, so pathname unix sockets
  there (``docker.sock``, tmux, ssh-agent) cannot be reached. Mount changes, ptrace and signals
  to processes outside the sandbox are refused (Landlock), ``no_new_privs`` is set and the
  capability bounding set is emptied;
- *resources*: ``setrlimit`` CPU time, address space (``memory_mb``), file size, open files,
  processes and no core dumps; a wall-clock timeout kills the whole PID namespace;
- *environment*: scrubbed (``PATH``, ``LANG``, ``HOME``/``TMPDIR`` = work dir); stdout and stderr
  are read through capped buffers; arguments are always lists, never a shell.

With ``network="deny"`` (the default) the run is **refused** (:class:`SandboxUnavailable`) when
namespaces or Landlock are missing. ``require_isolation=False`` (syntax-check CLIs, which parse but
do not execute code) falls back to rlimits + scrubbed environment only, and says so in
``SandboxResult.isolation``.

Not guaranteed: pathname unix sockets outside the hidden directories stay connectable (Landlock
does not mediate ``connect`` on unix sockets); files readable in the allowed system paths and the
Python prefix (e.g. installed packages) are readable; kernel exploits are out of scope.

**container runtime** (``runtime="docker"`` / ``"podman"``): ``--network none``, read-only root,
``--cap-drop ALL``, ``no-new-privileges``, memory / PID / CPU limits, the work directory mounted
read-write, the host uid; the image must already be present (``--pull never``).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "PYTHON",
    "Limits",
    "SandboxResult",
    "SandboxUnavailable",
    "Support",
    "container_argv",
    "isolation_support",
    "run",
]

LAUNCHER = Path(__file__).with_name("_launcher.py")
PYTHON = "{python}"  # command placeholder: the host interpreter, or ``python`` in a container
RUNTIMES = ("subprocess", "docker", "podman")
SETUP_FAILED = 125
HIDDEN_DIRS = ("/tmp", "/run", "/dev/shm", "/var/tmp")
SYSTEM_READ_PATHS = (
    "/usr",
    "/lib",
    "/lib64",
    "/lib32",
    "/bin",
    "/sbin",
    "/etc/ld.so.cache",
    "/etc/localtime",
    "/etc/ssl",  # node / OpenSSL configuration (private keys stay unreadable by permissions)
)
DEV_PATHS = ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random")
MAX_COLLECT_BYTES = 1024 * 1024


class SandboxUnavailable(RuntimeError):
    """The requested isolation cannot be provided here; nothing was run."""


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 10.0  # wall clock
    cpu_s: int | None = None  # default: ceil(timeout_s) + 1
    memory_mb: int | None = 512  # address space (RLIMIT_AS); None = unlimited (V8, JVM)
    file_mb: int = 16  # largest file the code may write
    nofile: int = 256
    nproc: int = 64
    output_bytes: int = 64 * 1024  # per stream (stdout, stderr)

    def cpu(self) -> int:
        return self.cpu_s if self.cpu_s is not None else int(self.timeout_s + 0.999) + 1


@dataclass(frozen=True)
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_s: float
    isolation: Mapping[str, Any]
    files: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class Support:
    unshare: bool
    landlock_abi: int
    reason: str | None = None

    @property
    def full(self) -> bool:
        return self.unshare and self.landlock_abi >= 1


def _landlock_abi() -> int:
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        rc = libc.syscall(444, None, ctypes.c_size_t(0), 1)
        return int(rc) if rc > 0 else 0
    except (OSError, AttributeError):
        return 0


def _unshare_cmd(network: bool) -> list[str] | None:
    exe = shutil.which("unshare", path="/usr/bin:/bin:/usr/sbin:/sbin")
    if exe is None:
        return None
    cmd = [exe, "--user", "--map-root-user", "--mount", "--pid", "--fork", "--kill-child"]
    if network:
        cmd.append("--net")
    return [*cmd, "--propagation", "private", "--"]


@lru_cache(maxsize=1)
def isolation_support() -> Support:
    """Probe (once) for unprivileged namespaces and Landlock."""
    if not sys.platform.startswith("linux"):
        return Support(False, 0, "the subprocess sandbox needs Linux")
    abi = _landlock_abi()
    cmd = _unshare_cmd(network=True)
    if cmd is None:
        return Support(False, abi, "unshare(1) not found")
    true = shutil.which("true", path="/usr/bin:/bin") or "/bin/true"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*cmd, true], capture_output=True, timeout=10, env={"PATH": "/usr/bin:/bin"}
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Support(False, abi, f"unshare probe failed: {exc}")
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip()[:200]
        return Support(False, abi, f"unprivileged namespaces unavailable: {msg}")
    return Support(True, abi, None if abi else "Landlock is not enabled in this kernel")


# --------------------------------------------------------------------------- helpers


def _safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts):
        raise ValueError(f"invalid sandbox file name {name!r}")
    return str(p)


def _write_files(workdir: Path, files: Mapping[str, str | bytes]) -> None:
    for name, content in files.items():
        path = workdir / _safe_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode() if isinstance(content, str) else bytes(content)
        path.write_bytes(data)


def _read_collected(workdir: Path, names: Sequence[str]) -> dict[str, bytes]:
    """Read back regular files (never following symlinks, never blocking on FIFOs), capped."""
    out: dict[str, bytes] = {}
    root = os.path.realpath(workdir)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    for name in names:
        path = workdir / _safe_name(name)
        # A symlinked parent directory (``d -> /etc``) would escape the work dir.
        parent = os.path.realpath(path.parent)
        if parent != root and not parent.startswith(root + os.sep):
            continue
        try:
            fd = os.open(path, flags)
        except OSError:
            continue
        with os.fdopen(fd, "rb") as f:
            if stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                out[name] = f.read(MAX_COLLECT_BYTES)
    return out


def _rmtree(path: Path) -> None:
    def onerror(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(os.path.dirname(p), 0o700)
            if os.path.isdir(p) and not os.path.islink(p):
                os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)  # never follows symlinks


class _CappedReader(threading.Thread):
    """Drain a pipe, keeping at most ``cap`` bytes (the child never blocks on a full pipe)."""

    def __init__(self, stream: Any, cap: int):
        super().__init__(daemon=True)
        self.stream, self.cap = stream, cap
        self.chunks: list[bytes] = []
        self.size = 0
        self.truncated = False

    def run(self) -> None:
        while True:
            chunk = self.stream.read(65536)
            if not chunk:
                break
            room = self.cap - self.size
            if room > 0:
                self.chunks.append(chunk[:room])
                self.size += min(room, len(chunk))
            if len(chunk) > max(room, 0):
                self.truncated = True
        self.stream.close()

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")


def _env(extra: Mapping[str, str] | None) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "NO_COLOR": "1",
    }
    env.update(extra or {})
    return env


def _python_read_paths() -> list[str]:
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    paths.add(os.path.dirname(os.path.realpath(sys.executable)))
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = sysconfig.get_paths().get(key)
        if value:
            paths.add(value)
    return sorted(p for p in paths if p)


def _tool_read_paths(exe: str) -> list[str]:
    """The tool's directory and its prefix (``<prefix>/bin/tool``), never ``/`` or ``$HOME``."""
    real = os.path.realpath(exe)
    bin_dir = os.path.dirname(real)
    prefix = os.path.dirname(bin_dir)
    too_broad = {"/", os.path.realpath(os.path.expanduser("~"))}
    if prefix in too_broad:
        return [bin_dir]
    return [prefix, bin_dir]


def _rlimits(limits: Limits) -> dict[str, Any]:
    mb = 1024 * 1024
    rl: dict[str, Any] = {
        "RLIMIT_CPU": [limits.cpu(), limits.cpu() + 1],  # SIGXCPU, then SIGKILL
        "RLIMIT_FSIZE": limits.file_mb * mb,
        "RLIMIT_NOFILE": limits.nofile,
        "RLIMIT_NPROC": limits.nproc,
        "RLIMIT_CORE": 0,
    }
    if limits.memory_mb is not None:
        rl["RLIMIT_AS"] = limits.memory_mb * mb
    return rl


def _hidden(path: str) -> bool:
    return any(path == d or path.startswith(d + "/") for d in HIDDEN_DIRS)


def kept_paths(paths: Sequence[str]) -> list[str]:
    """Directories under a hidden dir that must stay visible (read-only), never a hidden dir
    itself or a path that would re-expose one."""
    out = set()
    for p in paths:
        real = os.path.realpath(p)
        if _hidden(real) and real not in HIDDEN_DIRS and os.path.isdir(real):
            out.add(real)
    return sorted(out)


def _resolve_command(command: Sequence[str], python: str) -> list[str]:
    argv = [python if a == PYTHON else a for a in command]
    if not argv:
        raise ValueError("empty command")
    if not os.path.isabs(argv[0]):
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(f"{argv[0]!r} not found on PATH")
        argv[0] = exe
    return argv


def container_argv(
    runtime: str,
    image: str,
    command: Sequence[str],
    workdir: str,
    limits: Limits,
    *,
    name: str,
    network: str = "deny",
) -> list[str]:
    """The ``docker run`` / ``podman run`` argv for ``command`` (``PYTHON`` -> ``python``)."""
    mem = f"{limits.memory_mb}m" if limits.memory_mb else None
    argv = [runtime, "run", "--rm", "-i", "--name", name, "--pull", "never"]
    if network == "deny":
        argv += ["--network", "none"]
    argv += [
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(limits.nproc),
        "--cpus",
        "1",
        "--ulimit",
        f"cpu={limits.cpu()}:{limits.cpu() + 1}",
        "--ulimit",
        f"nofile={limits.nofile}:{limits.nofile}",
        "--ulimit",
        f"fsize={limits.file_mb * 1024 * 1024}",
        "--ulimit",
        "core=0",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{workdir}:/work:rw",
        "-w",
        "/work",
    ]
    if mem:
        argv += ["--memory", mem, "--memory-swap", mem]
    for key, value in _env({"HOME": "/work", "TMPDIR": "/work"}).items():
        argv += ["--env", f"{key}={value}"]
    return [*argv, "--", image, *("python" if a == PYTHON else a for a in command)]


# --------------------------------------------------------------------------- run


def run(
    command: Sequence[str],
    files: Mapping[str, str | bytes],
    *,
    limits: Limits | None = None,
    network: str = "deny",
    runtime: str = "subprocess",
    image: str | None = None,
    stdin: bytes = b"",
    collect: Sequence[str] = (),
    require_isolation: bool = True,
) -> SandboxResult:
    """Write ``files`` into a fresh temp work dir and run ``command`` (argv list) there.

    ``collect`` names work-dir files read back (capped) into ``SandboxResult.files``. Raises
    :class:`SandboxUnavailable` when the requested isolation cannot be provided.
    """
    limits = limits or Limits()
    if network not in ("deny", "allow"):
        raise ValueError("network must be 'deny' or 'allow'")
    if runtime not in RUNTIMES:
        raise ValueError(f"runtime must be one of {RUNTIMES}")
    if isinstance(command, str):
        raise TypeError("command must be an argv list")
    work = Path(tempfile.mkdtemp(prefix="bnbeval-sbx-"))
    control = Path(tempfile.mkdtemp(prefix="bnbeval-ctl-"))
    try:
        _write_files(work, files)
        if runtime == "subprocess":
            argv, isolation, cleanup = _subprocess_argv(
                command, work, control, limits, network, require_isolation
            )
        else:
            exe = shutil.which(runtime)
            if exe is None:
                raise SandboxUnavailable(f"container runtime {runtime!r} not found on PATH")
            if not image:
                raise ValueError(f"runtime={runtime!r} needs an image")
            name = f"bnbeval-{secrets.token_hex(6)}"
            argv = container_argv(
                exe, image, command, str(work), limits, name=name, network=network
            )
            isolation = {"runtime": runtime, "image": image, "network": network}
            cleanup = [exe, "kill", name]
        return _execute(argv, stdin, limits, isolation, cleanup, work, collect)
    finally:
        _rmtree(work)
        _rmtree(control)


def _subprocess_argv(
    command: Sequence[str],
    work: Path,
    control: Path,
    limits: Limits,
    network: str,
    require_isolation: bool,
) -> tuple[list[str], dict[str, Any], list[str] | None]:
    support = isolation_support()
    if not support.full and require_isolation:
        raise SandboxUnavailable(
            f"cannot isolate untrusted code here ({support.reason}); "
            "use runtime='docker' or 'podman'"
        )
    target = _resolve_command(command, sys.executable)
    read_paths = [*SYSTEM_READ_PATHS, *_python_read_paths(), *_tool_read_paths(target[0])]
    namespaces = support.unshare
    cfg = {
        "workdir": str(work),
        "mounts": namespaces,
        "hide": list(HIDDEN_DIRS),
        # interpreter / venv directories under a hidden dir (e.g. a venv in /tmp), re-bound
        # read-only; Landlock still limits them to read/execute
        "keep": kept_paths(_python_read_paths()),
        "rlimits": _rlimits(limits),
        "drop_caps": namespaces,
        "landlock": support.landlock_abi >= 1,
        "read_paths": read_paths,
        "dev_paths": list(DEV_PATHS),
        "deny_tcp": network == "deny",
        "argv": target,
        "env": _env(None),
        "env_workdir": ["HOME", "TMPDIR"],
    }
    cfg_path = control / "launch.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    launcher = [sys.executable, "-I", "-S", str(LAUNCHER), str(cfg_path)]
    prefix = _unshare_cmd(network == "deny") if namespaces else None
    isolation = {
        "runtime": "subprocess",
        "namespaces": bool(prefix),
        "network": _network_label(network, bool(prefix), support.landlock_abi),
        "landlock_abi": support.landlock_abi,
        "filesystem": "landlock" if support.landlock_abi >= 1 else "none",
    }
    return [*(prefix or []), *launcher], isolation, None


def _network_label(network: str, netns: bool, abi: int) -> str:
    if network == "allow":
        return "allowed"
    if netns:
        return "netns"
    return "tcp_denied" if abi >= 4 else "not_isolated"


def _execute(
    argv: list[str],
    stdin: bytes,
    limits: Limits,
    isolation: dict[str, Any],
    cleanup: list[str] | None,
    work: Path,
    collect: Sequence[str],
) -> SandboxResult:
    start = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(None),
        cwd=str(work),
        start_new_session=True,
        close_fds=True,
    )
    readers = [_CappedReader(proc.stdout, limits.output_bytes)]
    readers.append(_CappedReader(proc.stderr, limits.output_bytes))
    for r in readers:
        r.start()
    try:
        if stdin:
            proc.stdin.write(stdin)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except (BrokenPipeError, OSError):
        pass
    timed_out = False
    try:
        proc.wait(timeout=limits.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(proc, cleanup)
    for r in readers:
        r.join(timeout=5)
    duration = time.monotonic() - start
    out, err = readers
    stderr = err.text()
    if proc.returncode == SETUP_FAILED and "bnbeval-sandbox:" in stderr:
        raise SandboxUnavailable(stderr.strip().splitlines()[-1])
    return SandboxResult(
        returncode=proc.returncode,
        stdout=out.text(),
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        duration_s=duration,
        isolation=isolation,
        files=_read_collected(work, collect),
    )


def _kill(proc: subprocess.Popen[bytes], cleanup: list[str] | None) -> None:
    if cleanup:
        try:
            subprocess.run(cleanup, capture_output=True, timeout=10)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the PID namespace dies with its init
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
