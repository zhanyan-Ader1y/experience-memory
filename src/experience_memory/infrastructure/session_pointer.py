"""Atomic control pointers and single-host advisory locks; no rule persistence."""

from contextlib import contextmanager
from dataclasses import asdict, fields
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
import uuid

from ..domain.errors import ErrorCode, MemoryError
from ..domain.models import ControlPointer, require_identity

MAX_POINTER_BYTES = 16384


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class SessionPointerStore:
    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()

    def path_for(self, project_key: str, session_id: str) -> Path:
        try:
            require_identity(project_key)
            require_identity(session_id)
        except ValueError:
            raise MemoryError(ErrorCode.POINTER_INVALID) from None
        digest = hashlib.sha256(json.dumps([project_key, session_id], ensure_ascii=False).encode("utf-8")).hexdigest()
        return self.data_root / (digest + ".json")

    def _safe_path(self, path: Path):
        if path.parent != self.data_root:
            raise MemoryError(ErrorCode.POINTER_INVALID)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise MemoryError(ErrorCode.POINTER_INVALID)
        if not stat.S_ISREG(metadata.st_mode):
            raise MemoryError(ErrorCode.POINTER_INVALID)

    def read(self, project_key: str, session_id: str) -> ControlPointer | None:
        path = self.path_for(project_key, session_id)
        self._safe_path(path)
        try:
            with path.open("r", encoding="utf-8") as stream:
                raw = stream.read(MAX_POINTER_BYTES + 1)
            if len(raw.encode("utf-8")) > MAX_POINTER_BYTES:
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=_unique_object)
            if type(value) is not dict or set(value) != {field.name for field in fields(ControlPointer)}:
                raise ValueError()
            pointer = ControlPointer(**value)
            if pointer.project_key != project_key or pointer.session_id != session_id:
                raise ValueError()
            return pointer
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, UnicodeError):
            raise MemoryError(ErrorCode.POINTER_INVALID) from None
        except OSError:
            raise MemoryError(ErrorCode.POINTER_IO_ERROR) from None

    def _publish(self, pointer: ControlPointer):
        if not isinstance(pointer, ControlPointer):
            raise MemoryError(ErrorCode.POINTER_INVALID)
        serialized = json.dumps(asdict(pointer), ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(serialized) > MAX_POINTER_BYTES:
            raise MemoryError(ErrorCode.POINTER_INVALID)
        path = self.path_for(pointer.project_key, pointer.session_id)
        self._safe_path(path)
        temporary = self.data_root / (uuid.uuid4().hex + ".tmp")
        try:
            self.data_root.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            raise MemoryError(ErrorCode.POINTER_IO_ERROR) from None
        finally:
            if temporary.exists():
                temporary.unlink()

    def publish_locked(self,pointer):
        """Caller holds the matching project/session or operation lock."""
        self._publish(pointer)

    def write(self, pointer: ControlPointer):
        if not isinstance(pointer, ControlPointer):
            raise MemoryError(ErrorCode.POINTER_INVALID)
        with self.lock(pointer.project_key, pointer.session_id):
            self._publish(pointer)

    def prepare(self, pointer: ControlPointer) -> ControlPointer:
        if pointer.state != "prepared":
            raise MemoryError(ErrorCode.POINTER_INVALID)
        with self.lock(pointer.project_key, pointer.session_id):
            saved = self.read(pointer.project_key, pointer.session_id)
            if saved is not None:
                if saved.host_id != pointer.host_id or saved.state != "prepared":
                    # Local preparation never resets a remote binding.
                    raise MemoryError(ErrorCode.SESSION_INVALID)
                return saved
            self._publish(pointer)
            return pointer

    @contextmanager
    def lock(self, project_key: str, session_id: str | None, *, timeout_seconds: float = 10):
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise MemoryError(ErrorCode.POINTER_INVALID)
        # Project-level writes use a separate key from any real session identifier.
        try:
            require_identity(project_key)
            if session_id is not None:
                require_identity(session_id)
            digest = hashlib.sha256(json.dumps(["lock", project_key, session_id]).encode()).hexdigest()
            path = self.data_root / (digest + ".lock")
            self.data_root.mkdir(parents=True, exist_ok=True)
            self._safe_path(path)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except (OSError, ValueError):
            raise MemoryError(ErrorCode.POINTER_IO_ERROR) from None
        acquired = False
        with os.fdopen(descriptor, "r+b") as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout_seconds
            try:
                while True:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise MemoryError(ErrorCode.POINTER_IO_ERROR) from None
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise MemoryError(ErrorCode.LOCK_TIMEOUT) from None
                        time.sleep(min(0.05, remaining))
                yield
            finally:
                if acquired:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
