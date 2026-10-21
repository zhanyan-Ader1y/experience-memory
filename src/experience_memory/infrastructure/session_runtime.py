import hashlib
from pathlib import Path
import socket

from ..domain.errors import ErrorCode, MemoryError
from ..domain.models import ControlPointer, NamespaceMapping, require_identity
from .scope import resolve_scope,resolve_project_root
from .session_pointer import SessionPointerStore


class LocalSessionRuntime:
    """Local preparation only; T03 must confirm the remote binding."""

    def __init__(self, pointers: SessionPointerStore | None = None, *, project_scope: str | None = None,
                 namespace_mapping: NamespaceMapping | None = None, host_id: str | None = None, project_root: Path | None = None):
        self.project_root = resolve_project_root(project_root) if project_root is not None else None
        self.pointers = pointers
        self.project_scope = project_scope
        self.namespace_mapping = namespace_mapping
        self.host_id = host_id or "host-v1-" + hashlib.sha256(socket.gethostname().encode()).hexdigest()
        require_identity(self.host_id)

    def resolve_scope(self, cwd: Path):
        if self.project_root is not None and resolve_project_root(cwd)!=self.project_root:
            raise MemoryError(ErrorCode.SCOPE_MISMATCH)
        return resolve_scope(cwd, self.project_scope, namespace_mapping=self.namespace_mapping)

    def prepare(self, cwd: Path, session_id: str) -> ControlPointer:
        if self.pointers is None:
            raise MemoryError(ErrorCode.NOT_CONFIGURED)
        scope = self.resolve_scope(cwd)
        try:
            pointer = ControlPointer(scope.project_key, session_id, self.host_id)
        except ValueError:
            raise MemoryError(ErrorCode.POINTER_INVALID) from None
        return self.pointers.prepare(pointer)

    def bind_session(self, scope, session_id):
        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)

    def verify_session_handle(self, handle, cwd):
        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)

    def lock(self, project_key, session_id=None, *, timeout_seconds=10):
        if self.pointers is None:
            raise MemoryError(ErrorCode.NOT_CONFIGURED)
        return self.pointers.lock(project_key, session_id, timeout_seconds=timeout_seconds)
