"""Keep external tool processes from outliving port-bridge.

Debug-probe GDB servers (J-Link, ST-LINK, OpenOCD) are child processes that hold a USB probe
and TCP ports. If port-bridge exits without stopping them they keep running, so every child is
put in its own process group (POSIX) or Windows job object, and registered so that an
``atexit`` hook can reap whatever the normal shutdown path missed.

A Windows job object is created with ``KILL_ON_JOB_CLOSE``: the OS then also kills the children
when port-bridge dies abruptly. POSIX has no equivalent without ``preexec_fn`` (unsafe with the
GUI's threads), so a SIGKILL of port-bridge itself still orphans the children there.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_live: dict[int, ChildGuard] = {}

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_ATEXIT_GRACE_SECONDS = 1.0


def spawn_kwargs() -> dict[str, Any]:
    """Popen / create_subprocess_exec keywords that isolate the child from our terminal."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class ChildGuard:
    """Handle to a child's whole process tree (POSIX group or Windows job object)."""

    def __init__(self, pid: int, kind: str) -> None:
        self.pid = pid
        self.kind = kind
        self._job: Any = _create_job(pid) if sys.platform == "win32" else None

    def terminate(self) -> bool:
        """Ask the tree to exit. Returns False if the caller must signal the child itself."""
        return self._signal(graceful=True)

    def kill(self) -> bool:
        """Force the tree to exit. Returns False if the caller must signal the child itself."""
        return self._signal(graceful=False)

    def _signal(self, *, graceful: bool) -> bool:
        if sys.platform == "win32":
            if self._job is None:
                return False
            return _terminate_job(self._job)
        try:
            os.killpg(self.pid, signal.SIGTERM if graceful else signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            logger.debug("Cannot signal process group %d", self.pid, exc_info=True)
            return False
        return True

    def alive(self) -> bool:
        if sys.platform == "win32":
            return False
        # Only used at interpreter exit, when nothing else waits on the child; reaping the
        # leader keeps its zombie from making the group look alive.
        with contextlib.suppress(ChildProcessError):
            os.waitpid(self.pid, os.WNOHANG)
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def release(self) -> None:
        """Forget the child. Closing a Windows job handle kills anything still inside it."""
        with _lock:
            _live.pop(self.pid, None)
        if self._job is not None:
            _close_handle(self._job)
            self._job = None


def adopt(proc: Any, kind: str) -> ChildGuard | None:
    """Guard a real ``asyncio`` / ``subprocess`` child. Anything else (test doubles) is ignored."""
    if not isinstance(proc, (asyncio.subprocess.Process, subprocess.Popen)):
        return None
    pid = proc.pid
    if not isinstance(pid, int) or pid <= 1:
        return None
    guard = ChildGuard(pid, kind)
    with _lock:
        _live[pid] = guard
    return guard


def kill_all(kind: str | None = None) -> None:
    """Force-kill every guarded tree, optionally only those registered as *kind*."""
    with _lock:
        guards = [g for g in _live.values() if kind is None or g.kind == kind]
    for guard in guards:
        guard.kill()


def run_tracked(
    argv: list[str], *, kind: str, timeout: float, **popen_kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` for a child that :func:`kill_all` can reach from another thread."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **spawn_kwargs(),
        **popen_kwargs,
    )
    guard = adopt(proc, kind)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException:
        if guard is None or not guard.kill():
            proc.kill()
        proc.communicate()
        raise
    finally:
        if guard is not None:
            guard.release()
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _reap_at_exit() -> None:
    with _lock:
        guards = list(_live.values())
    if not guards:
        return
    for guard in guards:
        guard.terminate()
    deadline = time.monotonic() + _ATEXIT_GRACE_SECONDS
    while time.monotonic() < deadline and any(g.alive() for g in guards):
        time.sleep(0.05)
    for guard in guards:
        guard.kill()
        guard.release()


atexit.register(_reap_at_exit)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r0", "w0", "o0", "r1", "w1", "o1")]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _kernel32() -> Any:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        return k32

    def _create_job(pid: int) -> Any:
        try:
            k32 = _kernel32()
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _ExtendedLimit()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                k32.CloseHandle(job)
                return None
            process = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if not process:
                k32.CloseHandle(job)
                return None
            try:
                assigned = k32.AssignProcessToJobObject(job, process)
            finally:
                k32.CloseHandle(process)
            if not assigned:
                k32.CloseHandle(job)
                return None
            return job
        except OSError:
            logger.debug("Cannot create a job object for pid %d", pid, exc_info=True)
            return None

    def _terminate_job(job: Any) -> bool:
        return bool(_kernel32().TerminateJobObject(job, 1))

    def _close_handle(handle: Any) -> None:
        _kernel32().CloseHandle(handle)

else:

    def _create_job(pid: int) -> Any:
        return None

    def _terminate_job(job: Any) -> bool:
        return False

    def _close_handle(handle: Any) -> None:
        return None
