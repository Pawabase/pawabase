"""Confining a sandbox worker with what an unprivileged Linux process can do to itself: Landlock and seccomp.

A worker is a process of the server's own user. Without more, a function could open sockets (past the platform's SSRF guard), read any
file that user can (other deployments, the server's configuration), or signal and trace the server. Namespaces (bubblewrap, nsjail)
would need privileges a container normally lacks; these two mechanisms need none, work inside Docker's default profile, and cannot be
undone by the process once applied:

* **Landlock** (Linux 5.13+): the filesystem is reduced to what running the deployment needs — reading the Python installation, the
  platform's own packages, the deployment and its libraries; writing only the worker's temporary directory. From ABI 4 (6.7) TCP
  ``connect``/``bind`` is refused too; from ABI 6 (6.12) abstract Unix sockets and signals stop at the sandbox's edge.
* **seccomp**: ``socket()`` fails for every family (``socketpair``, which asyncio needs, stays), and the calls a function never needs and an
  escape would — ``ptrace``, reading another process's memory, ``mount``, new namespaces, ``bpf``, kernel keys and modules, ``io_uring``
  (which would bypass these filters) — fail with ``EPERM``. ``kill``/``tgkill`` may only target the worker itself.

:func:`confine` applies both and says what it applied. Callers decide whether a missing part is fatal (``required``) or tolerated.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# ── Landlock ────────────────────────────────────────────────────────────

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5
NET_BIND_TCP = 1 << 0  # ABI 4
NET_CONNECT_TCP = 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0  # ABI 6
SCOPE_SIGNAL = 1 << 1

FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV
READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR


def _fs_rights(abi: int) -> int:
    rights = (1 << 13) - 1
    if abi >= 2:
        rights |= FS_REFER
    if abi >= 3:
        rights |= FS_TRUNCATE
    if abi >= 5:
        rights |= FS_IOCTL_DEV
    return rights


_libc: Any = None


def _lib() -> Any:
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.syscall.restype = ctypes.c_long
    return _libc


def _syscall(number: int, *args: Any) -> int:
    result = _lib().syscall(ctypes.c_long(number), *args)
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def landlock_abi() -> int:
    """The Landlock ABI the kernel offers, or 0."""
    if sys.platform != "linux":
        return 0
    try:
        return _syscall(
            SYS_LANDLOCK_CREATE_RULESET,
            None,
            ctypes.c_size_t(0),
            ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION),
        )
    except OSError:
        return 0


def _no_new_privs() -> None:
    if _lib().prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def apply_landlock(
    read: Iterable[Path], write: Iterable[Path], files: dict[Path, int]
) -> dict[str, Any]:
    """Restrict this process's filesystem (and, where the kernel can, TCP, abstract sockets and signals) to the given paths."""
    abi = landlock_abi()
    if abi < 1:
        raise OSError(errno.ENOSYS, "Landlock is not available in this kernel")
    handled = _fs_rights(abi)
    net = NET_BIND_TCP | NET_CONNECT_TCP if abi >= 4 else 0
    scoped = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if abi >= 6 else 0
    if abi >= 6:
        attr = struct.pack("=QQQ", handled, net, scoped)
    elif abi >= 4:
        attr = struct.pack("=QQ", handled, net)
    else:
        attr = struct.pack("=Q", handled)
    buffer = ctypes.create_string_buffer(attr, len(attr))
    ruleset = _syscall(
        SYS_LANDLOCK_CREATE_RULESET, buffer, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0)
    )
    granted: list[str] = []
    try:
        rules = [(p, READ) for p in read] + [(p, handled) for p in write] + list(files.items())
        for path, rights in rules:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except FileNotFoundError:
                continue
            try:
                if not os.path.isdir(path):
                    rights &= FILE_RIGHTS
                rule = ctypes.create_string_buffer(
                    struct.pack("=Qi", rights & handled, fd), 12
                )  # landlock_path_beneath_attr, packed
                _syscall(
                    SYS_LANDLOCK_ADD_RULE,
                    ctypes.c_int(ruleset),
                    ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
                    rule,
                    ctypes.c_uint32(0),
                )
                granted.append(str(path))
            finally:
                os.close(fd)
        _no_new_privs()
        _syscall(SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0))
    finally:
        os.close(ruleset)
    return {"abi": abi, "network": bool(net), "scoped": bool(scoped), "paths": granted}


# ── seccomp ─────────────────────────────────────────────────────────────

AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7, "arm64": 0xC00000B7}

#: Syscall numbers by architecture. x86_64 has its own table; aarch64 uses the generic one.
SYSCALLS = {
    "x86_64": {
        "socket": 41,
        "clone": 56,
        "kill": 62,
        "ptrace": 101,
        "pivot_root": 155,
        "chroot": 161,
        "mount": 165,
        "umount2": 166,
        "swapon": 167,
        "swapoff": 168,
        "reboot": 169,
        "init_module": 175,
        "delete_module": 176,
        "tkill": 200,
        "tgkill": 234,
        "kexec_load": 246,
        "add_key": 248,
        "request_key": 249,
        "keyctl": 250,
        "unshare": 272,
        "perf_event_open": 298,
        "name_to_handle_at": 303,
        "open_by_handle_at": 304,
        "setns": 308,
        "process_vm_readv": 310,
        "process_vm_writev": 311,
        "finit_module": 313,
        "kexec_file_load": 320,
        "bpf": 321,
        "userfaultfd": 323,
        "pidfd_send_signal": 424,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "pidfd_open": 434,
        "clone3": 435,
        "pidfd_getfd": 438,
    },
    "aarch64": {
        "umount2": 39,
        "mount": 40,
        "pivot_root": 41,
        "chroot": 51,
        "unshare": 97,
        "kexec_load": 104,
        "init_module": 105,
        "delete_module": 106,
        "ptrace": 117,
        "kill": 129,
        "tkill": 130,
        "tgkill": 131,
        "reboot": 142,
        "socket": 198,
        "add_key": 217,
        "request_key": 218,
        "keyctl": 219,
        "clone": 220,
        "swapon": 224,
        "swapoff": 225,
        "perf_event_open": 241,
        "name_to_handle_at": 264,
        "open_by_handle_at": 265,
        "setns": 268,
        "process_vm_readv": 270,
        "process_vm_writev": 271,
        "finit_module": 273,
        "bpf": 280,
        "userfaultfd": 282,
        "kexec_file_load": 294,
        "pidfd_send_signal": 424,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "pidfd_open": 434,
        "clone3": 435,
        "pidfd_getfd": 438,
    },
}
SYSCALLS["arm64"] = SYSCALLS["aarch64"]

DENIED = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "mount",
    "umount2",
    "pivot_root",
    "chroot",
    "unshare",
    "setns",
    "bpf",
    "perf_event_open",
    "keyctl",
    "add_key",
    "request_key",
    "init_module",
    "finit_module",
    "delete_module",
    "kexec_load",
    "kexec_file_load",
    "reboot",
    "swapon",
    "swapoff",
    "userfaultfd",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "pidfd_open",
    "pidfd_send_signal",
    "pidfd_getfd",
    "open_by_handle_at",
    "name_to_handle_at",
    "tkill",
)
#: ``CLONE_NEWNS | NEWCGROUP | NEWUTS | NEWIPC | NEWUSER | NEWPID | NEWNET``.
NAMESPACE_FLAGS = 0x7E020000

LD_W_ABS = 0x20
JEQ = 0x15
JGE = 0x35
JSET = 0x45
RET = 0x06
RET_ALLOW = 0x7FFF0000
RET_KILL = 0x80000000
RET_ERRNO = 0x00050000


def _errno(code: int) -> int:
    return RET_ERRNO | code


def seccomp_program(machine: str, pid: int) -> list[tuple[int, int, int, int]]:
    """The filter as BPF instructions ``(code, jt, jf, k)``. Labels are resolved here; every jump is forward."""
    numbers = SYSCALLS[machine]
    program: list[tuple] = []  # (code, jt_label|int, jf_label|int, k)
    program.append((LD_W_ABS, 0, 0, 4))  # arch
    program.append((JEQ, 1, 0, AUDIT_ARCH[machine]))
    program.append((RET, 0, 0, RET_KILL))
    program.append((LD_W_ABS, 0, 0, 0))  # syscall number
    if machine == "x86_64":
        program.append((JGE, "x32", 0, 0x40000000))
    for name in DENIED:
        program.append((JEQ, "eperm", 0, numbers[name]))
    program.append((JEQ, "eacces", 0, numbers["socket"]))
    program.append(
        (JEQ, "enosys", 0, numbers["clone3"])
    )  # glibc falls back to clone, whose flags can be read
    program.append((JEQ, "clone", 0, numbers["clone"]))
    program.append((JEQ, "signal", 0, numbers["kill"]))
    program.append((JEQ, "signal", 0, numbers["tgkill"]))
    program.append((RET, 0, 0, RET_ALLOW))
    labels: dict[str, int] = {}
    labels["clone"] = len(program)
    program.append((LD_W_ABS, 0, 0, 16))  # flags, low word
    program.append((JSET, "eperm", 0, NAMESPACE_FLAGS))
    program.append((RET, 0, 0, RET_ALLOW))
    labels["signal"] = len(program)
    program.append((LD_W_ABS, 0, 0, 16))  # pid / tgid, low word
    program.append((JEQ, "allow", "eperm", pid))
    labels["allow"] = len(program)
    program.append((RET, 0, 0, RET_ALLOW))
    labels["eperm"] = len(program)
    program.append((RET, 0, 0, _errno(errno.EPERM)))
    labels["eacces"] = len(program)
    program.append((RET, 0, 0, _errno(errno.EACCES)))
    labels["enosys"] = len(program)
    program.append((RET, 0, 0, _errno(errno.ENOSYS)))
    labels["x32"] = len(program)
    program.append((RET, 0, 0, RET_KILL))

    def jump(value: Any, index: int) -> int:
        if not isinstance(value, str):
            return value
        offset = labels[value] - index - 1
        if not 0 <= offset <= 255:
            raise ValueError(f"seccomp jump to {value} out of range")
        return offset

    resolved = [
        (code, jump(jt, index), jump(jf, index), k)
        for index, (code, jt, jf, k) in enumerate(program)
    ]
    return resolved


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def apply_seccomp() -> dict[str, Any]:
    machine = platform.machine().lower()
    if sys.platform != "linux" or machine not in SYSCALLS:
        raise OSError(errno.ENOSYS, f"no seccomp filter for {sys.platform}/{machine}")
    program = seccomp_program(machine, os.getpid())
    filters = (_SockFilter * len(program))(*[_SockFilter(*instruction) for instruction in program])
    fprog = _SockFprog(len(program), filters)
    _no_new_privs()
    if _lib().prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return {"machine": machine, "instructions": len(program)}


# ── both ────────────────────────────────────────────────────────────────


def readable_roots(artifact: Path, libs: list[Path]) -> list[Path]:
    """What the worker may read: Python, the platform's packages, the deployment, its libraries, and system libraries.

    A ``sys.path`` entry that contains the deployment (a deployments root, ``/``) is left out: it would open every other deployment.
    """
    candidates = [
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.exec_prefix),
        *(Path(p) for p in sys.path if p),
        artifact,
        *libs,
        Path("/usr/lib"),
        Path("/usr/lib64"),
        Path("/lib"),
        Path("/lib64"),
        Path("/usr/local/lib"),
        Path("/usr/share/zoneinfo"),
        Path("/etc/localtime"),
        Path("/etc/ld.so.cache"),
    ]
    artifact = artifact.resolve()
    roots: list[Path] = []
    for path in candidates:
        try:
            path = path.resolve()
        except OSError:
            continue
        if path != artifact and artifact.is_relative_to(path):
            continue
        if path not in roots:
            roots.append(path)
    return roots


def confine(mode: str, *, artifact: Path, libs: list[Path], tmp: Path) -> dict[str, Any]:
    """Apply what the system supports. ``required``: raise unless both Landlock and seccomp apply. ``best``: apply what can be."""
    report: dict[str, Any] = {"mode": mode}
    problems: list[str] = []
    try:
        report["filesystem"] = apply_landlock(
            readable_roots(artifact, libs),
            [tmp],
            {
                Path("/dev/null"): FS_READ_FILE | FS_WRITE_FILE,
                Path("/dev/urandom"): FS_READ_FILE,
                Path("/proc/self"): READ,
            },
        )
    except OSError as exc:
        problems.append(f"Landlock: {exc.strerror or exc}")
    try:
        report["syscalls"] = apply_seccomp()
    except (OSError, ValueError) as exc:
        problems.append(f"seccomp: {getattr(exc, 'strerror', None) or exc}")
    report["problems"] = problems
    if problems and mode == "required":
        raise ConfinementUnavailable("; ".join(problems))
    return report


class ConfinementUnavailable(RuntimeError):
    """The worker could not be confined and the configuration requires it."""
