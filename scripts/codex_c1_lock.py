"""C1-only absolute-deadline advisory lock, interoperable with the old backend.

The file key and byte-zero OS lock deliberately duplicate the small existing
SessionPointerStore lock primitive. This is NOT another coordination namespace
or a general lock framework. Compatibility tests use actual old-backend holders
in both directions; a key/JSON-format/OS-lock change requires joint review.

Only controllable acquisition/retry waits are bounded. Actual OS file setup,
locking syscalls, flush and close cannot safely be preempted or killed mid-write.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
import time

from scripts import codex_c1_bundle as bundles
from experience_memory.domain.errors import ErrorCode, MemoryError as ContractError

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _remaining(deadline, wait_until):
    return min(deadline, wait_until) - time.monotonic()


@contextmanager
def absolute_attempt_lock(snapshot: bundles.ReviewSnapshot, *, deadline):
    """Same .coordination/key/OS lock, but NEVER start a relative setup timer.

    Called only for an explicit original absolute deadline. Omitted-deadline
    bundle callers retain the unchanged SessionPointerStore implementation.
    """
    if not isinstance(snapshot, bundles.ReviewSnapshot):
        raise ValueError("C1_ATTEMPT_LOCK_SNAPSHOT_REQUIRED")
    bundles._deadline_timeout(deadline)
    wait_until = min(deadline, time.monotonic() + 10)
    root = bundles._safe_bundle(snapshot.bundle)
    coordination = bundles._safe_child(root, ".coordination", expect="directory")
    bundles._assert_safe_directory(coordination)
    # EXACT old SessionPointerStore.lock key encoding (including JSON defaults).
    digest = hashlib.sha256(json.dumps(["lock", snapshot.run_nonce, "native-attempts"]).encode()).hexdigest()
    path = bundles._safe_child(root, ".coordination/" + digest + ".lock")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        raise ContractError(ErrorCode.POINTER_IO_ERROR) from None
    try:
        stream = os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        try:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            bundles._assert_safe_directory(coordination)
            bundles._assert_safe_existing(path)
        except OSError:
            raise ContractError(ErrorCode.POINTER_IO_ERROR) from None
        # File open/fdopen/fstat/initial byte/flush/path checks ALL precede this
        # absolute check. Delayed setup must not buy another relative wait.
        bundles._deadline_timeout(deadline)
        acquired = False
        try:
            while not acquired:
                if _remaining(deadline, wait_until) <= 0:
                    raise ContractError(ErrorCode.LOCK_TIMEOUT)
                try:
                    stream.seek(0)
                    # seek itself may stall; recheck immediately before attempt.
                    if _remaining(deadline, wait_until) <= 0:
                        raise ContractError(ErrorCode.LOCK_TIMEOUT)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise ContractError(ErrorCode.POINTER_IO_ERROR) from None
                    remaining = _remaining(deadline, wait_until)
                    if remaining <= 0:
                        raise ContractError(ErrorCode.LOCK_TIMEOUT) from None
                    time.sleep(min(0.05, remaining))
            bundles._deadline_timeout(deadline)
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
