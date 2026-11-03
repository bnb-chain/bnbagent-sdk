"""Sandbox launcher: executed by ``sandbox.py`` (``python -I -S _launcher.py <config>``), never
imported. Stdlib only.

Inside the namespaces created by ``unshare`` it (1) hides ``/tmp``, ``/run``, ``/dev/shm`` and
``/var/tmp`` behind empty tmpfs mounts (pathname unix sockets such as ``docker.sock`` live there)
and bind-mounts the work directory at ``/tmp/bnbeval-work``; (2) as PID 1 of the new PID
namespace, forks the target and reaps it (when PID 1 exits the kernel kills every process left in
the namespace); (3) in the child: rlimits, ``no_new_privs``, an empty capability bounding set,
a Landlock domain (read-only system paths, read-write work directory, TCP denied, signals and
abstract unix sockets scoped), then ``execve`` with a scrubbed environment.

Setup failures exit with status 125 and a ``bnbeval-sandbox:`` line on stderr.
"""

from __future__ import annotations

import ctypes
import json
import os
import resource
import sys

SETUP_FAILED = 125
WORK_MOUNT = "/tmp/bnbeval-work"

# Landlock (include/uapi/linux/landlock.h); the syscall numbers are the same on every arch.
SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
FS_EXECUTE, FS_WRITE_FILE, FS_READ_FILE, FS_READ_DIR = 1, 2, 4, 8
FS_REFER, FS_TRUNCATE, FS_IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
NET_BIND_TCP, NET_CONNECT_TCP = 1, 2
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1, 2
READ_EXEC = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV

PR_SET_NO_NEW_PRIVS, PR_CAPBSET_DROP, PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL = 38, 24, 47, 4
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_REMOUNT, MS_BIND = 1, 2, 4, 32, 4096
O_PATH = getattr(os, "O_PATH", 0o10000000)

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _fail(message: str) -> None:
    sys.stderr.write(f"bnbeval-sandbox: {message}\n")
    sys.stderr.flush()
    os._exit(SETUP_FAILED)


def _check(rc: int, what: str) -> int:
    if rc < 0:
        _fail(f"{what} failed: {os.strerror(ctypes.get_errno())}")
    return rc


def landlock_abi() -> int:
    rc = _libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), CREATE_RULESET_VERSION)
    return int(rc) if rc > 0 else 0


def _fs_all(abi: int) -> int:
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= FS_REFER
    if abi >= 3:
        rights |= FS_TRUNCATE
    if abi >= 5:
        rights |= FS_IOCTL_DEV
    return rights


def _landlock(read_paths: list[str], rw_paths: list[str], dev_paths: list[str], deny_tcp: bool):
    abi = landlock_abi()
    if abi < 1:
        _fail("Landlock is not available")
    handled = _fs_all(abi)
    attr = _RulesetAttr(fs=handled, net=0, scoped=0)
    size = 8
    if abi >= 4:
        size = 16
        if deny_tcp:
            attr.net = NET_BIND_TCP | NET_CONNECT_TCP  # no net rules: every TCP bind/connect fails
    if abi >= 6:
        size = 24
        attr.scoped = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL
    ruleset = _check(
        _libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), 0),
        "landlock_create_ruleset",
    )
    rules = [(p, READ_EXEC) for p in read_paths] + [(p, handled) for p in rw_paths]
    rules += [(p, FS_READ_FILE | FS_WRITE_FILE) for p in dev_paths]
    for path, allowed in rules:
        try:
            fd = os.open(path, O_PATH | os.O_CLOEXEC)
        except OSError:
            continue  # optional system paths may not exist
        try:
            if not os.path.isdir(path):
                allowed &= FILE_RIGHTS
            rule = _PathBeneath(allowed=allowed & handled, parent_fd=fd)
            _check(
                _libc.syscall(SYS_ADD_RULE, ruleset, RULE_PATH_BENEATH, ctypes.byref(rule), 0),
                f"landlock_add_rule({path})",
            )
        finally:
            os.close(fd)
    _check(_libc.syscall(SYS_RESTRICT_SELF, ruleset, 0), "landlock_restrict_self")
    os.close(ruleset)
    return abi


def _mount(source: str, target: str, fstype: str | None, flags: int, data: str | None) -> None:
    rc = _libc.mount(
        source.encode(),
        target.encode(),
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        data.encode() if data else None,
    )
    _check(rc, f"mount {target}")


def _hide_and_bind(workdir: str, hide: list[str], keep: list[str]) -> str:
    """Hide ``hide`` behind tmpfs, then bind ``keep`` (paths inside them, e.g. a venv under
    ``/tmp``) back read-only, and the work directory at :data:`WORK_MOUNT`."""
    work_fd = os.open(workdir, O_PATH | os.O_DIRECTORY)
    keep_fds = []
    for path in keep:
        try:
            keep_fds.append((path, os.open(path, O_PATH | os.O_DIRECTORY)))
        except OSError:
            continue
    for path in hide:
        if os.path.isdir(path) and not os.path.islink(path):
            _mount("tmpfs", path, "tmpfs", MS_NOSUID | MS_NODEV, "size=1m,mode=0755")
    for path, fd in keep_fds:
        os.makedirs(path, exist_ok=True)
        _mount(f"/proc/self/fd/{fd}", path, None, MS_BIND, None)
        _mount("none", path, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, None)
        os.close(fd)
    os.makedirs(WORK_MOUNT, exist_ok=True)
    _mount(f"/proc/self/fd/{work_fd}", WORK_MOUNT, None, MS_BIND, None)
    os.close(work_fd)
    return WORK_MOUNT


def _rlimits(limits: dict) -> None:
    for name, value in limits.items():
        res = getattr(resource, name)
        soft, hard = (value, value) if isinstance(value, int) else value
        try:
            resource.setrlimit(res, (soft, hard))
        except (ValueError, OSError) as exc:
            _fail(f"setrlimit {name}: {exc}")


def _drop_caps() -> None:
    try:
        with open("/proc/sys/kernel/cap_last_cap") as f:
            last = int(f.read().strip())
    except OSError:
        last = 63
    for cap in range(last + 1):
        _libc.prctl(PR_CAPBSET_DROP, ctypes.c_ulong(cap), 0, 0, 0)  # EPERM outside a userns
    _libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)


def _confine_and_exec(cfg: dict, workdir: str) -> None:
    _rlimits(cfg.get("rlimits", {}))
    _check(_libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "prctl(no_new_privs)")
    if cfg.get("drop_caps"):
        _drop_caps()
    if cfg.get("landlock"):
        _landlock(
            list(cfg.get("read_paths", [])),
            [workdir],
            list(cfg.get("dev_paths", [])),
            bool(cfg.get("deny_tcp")),
        )
    argv = list(cfg["argv"])
    env = dict(cfg.get("env", {}))
    env.update({k: workdir for k in cfg.get("env_workdir", [])})
    try:
        os.execve(argv[0], argv, env)
    except OSError as exc:
        sys.stderr.write(f"bnbeval-sandbox: exec {argv[0]}: {exc}\n")
        os._exit(127)


def _wait_as_init(child: int) -> None:
    """PID 1 of the namespace: reap everything, exit with the target's status."""
    status = 0
    while True:
        try:
            pid, st = os.wait()
        except ChildProcessError:
            break
        if pid == child:
            status = st
            break
    code = os.waitstatus_to_exitcode(status)
    os._exit(128 - code if code < 0 else code)  # killed by signal N -> 128 + N


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    workdir = cfg["workdir"]
    if cfg.get("mounts"):
        workdir = _hide_and_bind(workdir, list(cfg.get("hide", [])), list(cfg.get("keep", [])))
    os.chdir(workdir)
    if os.getpid() == 1:
        child = os.fork()
        if child:
            _wait_as_init(child)
    _confine_and_exec(cfg, workdir)


if __name__ == "__main__":
    main()
