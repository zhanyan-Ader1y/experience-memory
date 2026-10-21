"""Bounded Windows Job-owned processes. No command discovery or shell execution."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

MAX_LINE_BYTES = 262_144
MAX_RECEIVED_BYTES = 2_097_152
MAX_SENT_BYTES = 2_097_152
QUEUE_LINES = 16


class _WriteRequest:
    def __init__(self, raw):
        self.raw = raw
        self.done = threading.Event()
        self.error = None


class OwnedProcessError(RuntimeError):
    """Static diagnostic only; creation failures include conservative cleanup facts."""
    def __init__(self, code, cleanup=None):
        super().__init__(code)
        self.code = code
        self.cleanup = cleanup


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _JobBasicLimits), ("io", _IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _JobAccounting(ctypes.Structure):
    _fields_ = [("user", ctypes.c_int64), ("kernel", ctypes.c_int64),
                ("period_user", ctypes.c_int64), ("period_kernel", ctypes.c_int64),
                ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("tid", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("priority", wintypes.LONG), ("delta", wintypes.LONG),
                ("flags", wintypes.DWORD)]


def _windows_api():
    if sys.platform != "win32":
        raise OwnedProcessError("C1_PROCESS_UNSUPPORTED")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.c_void_p], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


class OwnedProcess:
    """Create suspended, assign our non-breakaway Job, then resume its sole thread.

    `deadline` is absolute time.monotonic(), including up to one second reserved
    for cleanup. Both readers and their queues are bounded; stderr is never
    yielded or retained in a report. Caller must always call stop(), even after
    normal exit. Non-Windows dispatch has no parent-only fallback.

    Pipe mode has one writer and at most 16 queued frames. send_line acknowledges
    the actual complete write and flush, not queue admission. All operations use
    the original operation deadline; an ambiguous write is terminal, never retried.
    Select either events() (one iterator) or sequential read_line() for this
    object's lifetime. Concurrent stdout consumers are rejected, not serialized.
    """
    def __init__(self, argv: list[str], *, cwd: Path, env: dict, deadline: float,
                 stdin_mode="devnull"):
        if stdin_mode not in ("devnull", "pipe"):
            raise ValueError("C1_STDIN_MODE_INVALID")
        now = time.monotonic()
        if (type(argv) is not list or not argv
                or any(type(arg) is not str or not arg or "\x00" in arg for arg in argv)
                or not Path(argv[0]).is_absolute()
                or not isinstance(cwd, Path) or not cwd.is_absolute() or not cwd.is_dir()
                or type(env) is not dict or any(type(k) is not str or type(v) is not str for k, v in env.items())
                or type(deadline) not in (float, int) or not math.isfinite(deadline)
                or not 0 < deadline - now <= 180):
            raise ValueError("C1_PROCESS_INVALID_INPUT")
        self.deadline = float(deadline)
        self._operation_deadline = deadline - min(1.0, (deadline - now) * 0.25)
        self._api = _windows_api()
        self._job = None
        self._process = None
        self._assigned = False
        self._resumed = False
        self._cleanup = None
        self._lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._stdout_consumer = None
        self._cancel = threading.Event()
        self._failure = None
        self._stdin_mode = stdin_mode
        self._input_closing = False
        self._input_closed = threading.Event()
        self._write_queue = queue.Queue(maxsize=QUEUE_LINES)
        self._stdin_reserved = 0
        self._natural_exit_code = None
        self._forced_termination = False
        self._queues = {name: queue.Queue(maxsize=QUEUE_LINES) for name in ("stdout", "stderr")}
        self._eof = {name: threading.Event() for name in self._queues}
        self._observed_eof = {name: False for name in self._queues}
        self._stats = {name: {"bytes": 0, "hash": hashlib.sha256()}
                       for name in ("stdout", "stderr", "stdin")}
        self._threads = []
        child_env = {k: v for k, v in env.items() if k.upper() != "MEM0_API_KEY"}
        try:
            self._job = self._api.CreateJobObjectW(None, None)
            if not self._job:
                raise OSError()
            limits = _JobExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags.
            if not self._api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError()
            self._process = subprocess.Popen(
                argv, cwd=str(cwd), env=child_env, shell=False, close_fds=True,
                stdin=subprocess.PIPE if stdin_mode == "pipe" else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=0x00000004 | subprocess.CREATE_NO_WINDOW,  # CREATE_SUSPENDED
            )
            self._assign_job()
            if not self.is_job_member():
                raise OSError()
            self._resume_owned_process()
            self._resumed = True
            for name in self._queues:
                thread = threading.Thread(target=self._read_stream, args=(name,), daemon=True)
                thread.start()
                self._threads.append(thread)
            if stdin_mode == "pipe":
                thread = threading.Thread(target=self._write_input, daemon=True)
                thread.start()
                self._threads.append(thread)
        except BaseException:
            cleanup = self.stop()
            raise OwnedProcessError("C1_PROCESS_START_FAILED", cleanup) from None

    def _assign_job(self):
        if not self._api.AssignProcessToJobObject(self._job, int(self._process._handle)):
            raise OSError()
        self._assigned = True

    def is_job_member(self):
        answer = wintypes.BOOL()
        if not self._api.IsProcessInJob(int(self._process._handle), self._job, ctypes.byref(answer)):
            raise OSError()
        return bool(answer.value)

    def _resume_owned_process(self):
        # Popen closes the CreateProcess primary-thread handle. The child is still
        # suspended (no user instructions or descendants), so enumerate its one
        # initial thread and open just THREAD_SUSPEND_RESUME, never NtResumeProcess.
        snapshot = self._api.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError()
        tids = []
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            present = self._api.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.pid == self._process.pid:
                    tids.append(entry.tid)
                present = self._api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise OSError()
        finally:
            self._api.CloseHandle(snapshot)
        if len(tids) != 1:
            raise OSError()
        thread = self._api.OpenThread(0x0002, False, tids[0])
        if not thread:
            raise OSError()
        try:
            if time.monotonic() >= self._operation_deadline or not self.is_job_member():
                raise OSError()
            if self._api.ResumeThread(thread) != 1:
                raise OSError()
        finally:
            self._api.CloseHandle(thread)

    @property
    def exit_code(self):
        with self._lock:
            code = self._process.poll() if self._process is not None else None
            if code is not None and self._resumed and not self._forced_termination:
                self._natural_exit_code = code
            return code

    def _check_operation(self):
        if self._failure is not None:
            raise OwnedProcessError(self._failure)
        if self._cancel.is_set():
            raise OwnedProcessError("C1_PROCESS_STOPPED")
        if time.monotonic() >= self._operation_deadline:
            self._fail("C1_PROCESS_DEADLINE")
            raise OwnedProcessError(self._failure)

    def _require_pipe(self):
        if self._stdin_mode != "pipe":
            raise OwnedProcessError("C1_PROCESS_STDIN_UNAVAILABLE")

    def send_line(self, raw: bytes) -> None:
        """Write one LF-terminated frame; unknown partial writes are never resent.

        Limits include LF: 262,144 bytes/frame, 2,097,152 admitted stdin bytes.
        The stdin budget is independent of the combined stdout/stderr budget.
        Rejected validation/limit checks write nothing; admitted bytes are never
        refunded after an ambiguous failure. Stats count only observed writes.
        Exact type and length are checked before any framing-content inspection;
        rejecting an oversized caller buffer requires no input-sized copy/scan.
        """
        self._require_pipe()
        if type(raw) is not bytes:
            raise ValueError("C1_PROCESS_STDIN_LINE_INVALID")
        if len(raw) > MAX_LINE_BYTES:
            raise OwnedProcessError("C1_PROCESS_STDIN_LINE_LIMIT")
        if not raw.endswith(b"\n") or raw.find(b"\n", 0, len(raw) - 1) != -1:
            raise ValueError("C1_PROCESS_STDIN_LINE_INVALID")
        request = _WriteRequest(raw)
        try:
            while True:
                self._check_operation()
                with self._lock:
                    if self._input_closing:
                        raise OwnedProcessError("C1_PROCESS_STDIN_CLOSED")
                    if self._stdin_reserved + len(raw) > MAX_SENT_BYTES:
                        raise OwnedProcessError("C1_PROCESS_STDIN_TOTAL_LIMIT")
                    try:
                        self._write_queue.put_nowait(request)
                    except queue.Full:
                        pass
                    else:
                        self._stdin_reserved += len(raw)
                        break
                self._cancel.wait(0.005)
            while True:
                self._check_operation()
                if request.done.wait(0.005):
                    if request.error is not None:
                        raise OwnedProcessError(request.error)
                    return
        except OwnedProcessError as error:
            if error.code == "C1_PROCESS_DEADLINE":
                self._fail(error.code)
            raise

    def close_input(self) -> None:
        """Drain admitted frames and observe actual stdin close, within the same deadline.

        Idempotent after actual close. A concurrent send is either admitted before
        closing starts or rejected; there is no lock held over writing or waiting.
        """
        self._require_pipe()
        with self._lock:
            self._input_closing = True
        while not self._input_closed.is_set():
            try:
                self._check_operation()
            except OwnedProcessError as error:
                if error.code == "C1_PROCESS_DEADLINE":
                    self._fail(error.code)
                raise
            self._input_closed.wait(0.005)

    def _write_input(self):
        request = None
        try:
            while True:
                self._check_operation()
                with self._lock:
                    if self._input_closing and self._write_queue.empty():
                        break
                try:
                    request = self._write_queue.get(timeout=0.02)
                except queue.Empty:
                    continue
                view = memoryview(request.raw)
                while view:
                    self._check_operation()
                    count = os.write(self._process.stdin.fileno(), view)
                    if count <= 0:
                        raise OSError()
                    with self._lock:
                        self._stats["stdin"]["bytes"] += count
                        self._stats["stdin"]["hash"].update(view[:count])
                    view = view[count:]
                self._process.stdin.flush()
                self._check_operation()
                request.done.set()
                request = None
        except OwnedProcessError as error:
            if error.code != "C1_PROCESS_STOPPED":
                self._fail(error.code)
        except (OSError, ValueError):
            if not self._cancel.is_set():
                self._fail("C1_PROCESS_STDIN_FAILED")
        finally:
            if request is not None:
                request.error = self._failure or "C1_PROCESS_STOPPED"
                request.done.set()
            # Wake every admitted sender, including a saturated queue. Unadmitted
            # senders and close_input also observe cancellation/failure directly.
            with self._lock:
                self._input_closing = True
                while True:
                    try:
                        queued = self._write_queue.get_nowait()
                    except queue.Empty:
                        break
                    queued.error = self._failure or "C1_PROCESS_STOPPED"
                    queued.done.set()
            self._close_stdin()

    def _close_stdin(self):
        if self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except (OSError, ValueError):
                self._fail("C1_PROCESS_STDIN_FAILED")
            else:
                self._input_closed.set()

    def wait_for_exit(self) -> int | None:
        """Observe a natural root exit only; expiry returns None and never kills."""
        while True:
            self.exit_code  # Capture a real natural exit before any forced stop.
            with self._lock:
                code = self._natural_exit_code
            if code is not None:
                return code
            if self._cancel.is_set() or time.monotonic() >= self._operation_deadline:
                return None
            self._cancel.wait(min(0.005, max(0, self._operation_deadline - time.monotonic())))

    def _fail(self, code):
        with self._lock:
            if self._failure is None:
                self._failure = code

    def _read_stream(self, name):
        pending = bytearray()
        stream = getattr(self._process, name)
        try:
            while not self._cancel.is_set():
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    with self._lock:
                        self._observed_eof[name] = True
                    if pending and name == "stdout":
                        self._fail("C1_PROCESS_PARTIAL_LINE")
                    return
                with self._lock:
                    self._stats[name]["bytes"] += len(chunk)
                    self._stats[name]["hash"].update(chunk)
                    too_large = sum(self._stats[key]["bytes"] for key in self._queues) > MAX_RECEIVED_BYTES
                if too_large:
                    self._fail("C1_PROCESS_TOTAL_LIMIT")
                    return
                pending.extend(chunk)
                while b"\n" in pending:
                    end = pending.index(b"\n") + 1
                    if end > MAX_LINE_BYTES:
                        self._fail("C1_PROCESS_LINE_LIMIT")
                        return
                    line = bytes(pending[:end])
                    del pending[:end]
                    # Stderr is summary-only, never a second consumer's queue.
                    # Keep the same byte and line checks without backpressuring
                    # duplex input behind diagnostics nobody is allowed to read.
                    if name == "stderr":
                        continue
                    while not self._cancel.is_set():
                        try:
                            self._queues[name].put(line, timeout=0.02)
                            break
                        except queue.Full:
                            if time.monotonic() >= self._operation_deadline:
                                self._fail("C1_PROCESS_DEADLINE")
                                return
                if len(pending) > MAX_LINE_BYTES:
                    self._fail("C1_PROCESS_LINE_LIMIT")
                    return
        except (OSError, ValueError):
            if not self._cancel.is_set():
                self._fail("C1_PROCESS_STREAM_FAILED")
        finally:
            self._eof[name].set()

    def _claim_stdout(self, consumer):
        with self._lock:
            if self._stdout_consumer is None:
                self._stdout_consumer = consumer
            elif self._stdout_consumer != consumer:
                raise OwnedProcessError("C1_PROCESS_STDOUT_CONSUMER")

    def _next_stdout(self):
        while True:
            self._check_operation()
            try:
                return self._queues["stdout"].get_nowait()
            except queue.Empty:
                if self._eof["stdout"].is_set() and self._queues["stdout"].empty():
                    if self._failure is not None:
                        raise OwnedProcessError(self._failure)
                    return None
            self._cancel.wait(0.005)

    def read_line(self) -> bytes | None:
        """Read a complete stdout line, or actual EOF; no concurrent consumers."""
        self._claim_stdout("read_line")
        if not self._read_lock.acquire(blocking=False):
            raise OwnedProcessError("C1_PROCESS_STDOUT_CONSUMER")
        try:
            return self._next_stdout()
        finally:
            self._read_lock.release()

    def events(self):
        self._claim_stdout(object())  # One iterator owns stdout, even across yields.
        while True:
            line = self._next_stdout()
            if line is None:
                break
            yield line
        # Preserve the old events() contract: stdout EOF alone does not finish
        # the iterator until stderr has ended and the root has actually exited.
        while True:
            self._check_operation()
            if all(event.is_set() for event in self._eof.values()) and self.exit_code is not None:
                if self._failure is not None:
                    raise OwnedProcessError(self._failure)
                return
            self._cancel.wait(0.005)

    def _active_job_processes(self):
        accounting = _JobAccounting()
        if not self._api.QueryInformationJobObject(self._job, 1, ctypes.byref(accounting),
                                                   ctypes.sizeof(accounting), None):
            raise OSError()
        return accounting.active

    def stream_summary(self):
        with self._lock:
            return {name: {"bytes": row["bytes"], "sha256": row["hash"].hexdigest()}
                    for name, row in self._stats.items()}

    def shutdown_state(self) -> dict:
        """Detached observations only; stop()'s legacy cleanup schema is unchanged.

        stdin_mode: 'devnull' or 'pipe'. stdin_eof: actual pipe close observed
        (None for devnull). stdout_eof/stderr_eof: actual zero-byte OS read,
        NOT merely reader termination. natural_exit_code: observed root status
        before any forced Job/root termination, else None (never synthetic 0).
        forced_termination: a successful explicit or KILL_ON_JOB_CLOSE termination
        request on a nonempty/unconfirmed Job or suspended root; it can coexist
        with a natural root exit
        when descendants needed termination. io_threads_stopped: every started
        I/O worker is no longer alive. io_failure: first static I/O/limit/deadline
        code or None. Observed read/events deadlines remain sticky even if real
        EOF and natural exit follow; wait_for_exit returning None alone is not
        an I/O failure. EOF/worker observations do not prove an empty Job; callers
        must independently require stop()['descendants_exited'].
        """
        self.exit_code
        with self._lock:
            return {"stdin_mode": self._stdin_mode,
                    "stdin_eof": self._input_closed.is_set() if self._stdin_mode == "pipe" else None,
                    "stdout_eof": self._observed_eof["stdout"],
                    "stderr_eof": self._observed_eof["stderr"],
                    "natural_exit_code": self._natural_exit_code,
                    "forced_termination": self._forced_termination,
                    "io_threads_stopped": not any(thread.is_alive() for thread in self._threads),
                    "io_failure": self._failure}

    def stop(self) -> dict:
        with self._stop_lock:
            return self._stop()

    def _stop(self) -> dict:
        if self._cleanup is not None:
            return dict(self._cleanup)
        self._cancel.set()
        root_exited = self._process is None
        descendants_exited = self._process is None or not self._resumed
        try:
            if self._assigned:
                # TerminateJobObject applies to every descendant, not a PID/name snapshot.
                # Capture the natural root status first. Query failure cannot stop
                # us terminating the Job, and never becomes proof of a natural exit.
                natural = self.exit_code is not None
                try:
                    active = self._active_job_processes()
                    # A signaled root handle can precede the Job accounting
                    # update, especially with the Windows venv redirector.
                    # Allow that update a bounded part of cleanup's reserve;
                    # genuine surviving descendants are still terminated below.
                    settle_until = min(self.deadline, time.monotonic() + 0.05)
                    while natural and active and time.monotonic() < settle_until:
                        time.sleep(min(0.005, max(0, settle_until - time.monotonic())))
                        active = self._active_job_processes()
                except (OSError, ValueError):
                    active = None
                with self._lock:
                    code = self._process.poll()
                    if code is not None and self._resumed and not self._forced_termination:
                        self._natural_exit_code = code
                    if code is None or active != 0:
                        if not self._api.TerminateJobObject(self._job, 1):
                            raise OSError()
                        self._forced_termination = True
            elif self._process is not None:
                # Assignment failed before resume; this suspended root cannot have children.
                with self._lock:
                    self._process.kill()
                    self._forced_termination = True
            while self._process is not None:
                root_exited = self.exit_code is not None
                if self._assigned:
                    descendants_exited = self._active_job_processes() == 0
                if root_exited and descendants_exited or time.monotonic() >= self.deadline:
                    break
                time.sleep(0.005)
        except (OSError, ValueError):
            root_exited = self._process is None or self.exit_code is not None
            descendants_exited = False
        finally:
            if self._job:
                # KILL_ON_JOB_CLOSE is a force request on an unconfirmed Job,
                # never proof that cleanup succeeded or that the root exited
                # naturally. Serialize this observation with exit-code polling.
                with self._lock:
                    closed = self._api.CloseHandle(self._job)
                    if closed and not (root_exited and descendants_exited):
                        self._forced_termination = True
                    self._job = None
            for thread in self._threads:
                thread.join(max(0, self.deadline - time.monotonic()))
            streams_closed = not any(thread.is_alive() for thread in self._threads)
            if self._process is not None and streams_closed:
                self._close_stdin()
                for name in self._queues:
                    getattr(self._process, name).close()
            if not streams_closed:
                descendants_exited = False
        self._cleanup = {"root_exited": bool(root_exited), "descendants_exited": bool(descendants_exited),
                         "reason": "job_empty" if root_exited and descendants_exited else "cleanup_unconfirmed"}
        return dict(self._cleanup)
