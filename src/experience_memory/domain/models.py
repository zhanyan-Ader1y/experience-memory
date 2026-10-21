from dataclasses import dataclass, field
from enum import Enum
import re


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    PENDING_VERIFICATION = "pending_verification"
    INVALIDATED = "invalidated"


def require_identity(value: str) -> None:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or any(ch in value for ch in "*?\r\n\x00")):
        raise ValueError("invalid identity")


def require_sequence(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("invalid sequence")


@dataclass(frozen=True)
class Scope:
    project_key: str
    app_id: str
    project_id: str
    legacy_project_ids: tuple[str, ...]
    user_id: str

    def __post_init__(self):
        for value in (self.project_key, self.app_id, self.project_id, self.user_id):
            require_identity(value)
        if type(self.legacy_project_ids) is not tuple:
            raise ValueError("legacy identities must be a tuple")
        for value in self.legacy_project_ids:
            require_identity(value)


@dataclass(frozen=True)
class SessionContext:
    scope: Scope
    session_id: str
    session_handle: str
    host_id: str

    def __post_init__(self):
        if not isinstance(self.scope, Scope):
            raise ValueError("invalid scope")
        for value in (self.session_id, self.session_handle, self.host_id):
            require_identity(value)


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    experience_id: str
    status: MemoryStatus
    body: str
    retention_text: str
    fingerprint: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        require_identity(self.record_id)
        require_experience_id(self.experience_id)
        if not isinstance(self.status, MemoryStatus):
            raise ValueError("invalid memory status")
        for text in (self.body, self.retention_text, self.fingerprint):
            if not isinstance(text, str) or not text:
                raise ValueError("invalid record text")


def require_experience_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("invalid experience identifier")


@dataclass(frozen=True)
class ReadReceipt:
    receipt_id: str
    record_id: str
    experience_id: str
    fingerprint: str
    session_handle: str

    def __post_init__(self):
        for value in (self.receipt_id, self.record_id, self.fingerprint, self.session_handle):
            require_identity(value)
        require_experience_id(self.experience_id)


def require_entries(entries: tuple["WindowEntry", ...]) -> None:
    if type(entries) is not tuple or any(not isinstance(entry, WindowEntry) for entry in entries):
        raise ValueError("invalid window entries")


@dataclass(frozen=True)
class WindowEntry:
    record_id: str
    experience_id: str
    priority: Priority
    last_used_seq: int
    fingerprint: str

    def __post_init__(self):
        require_identity(self.record_id)
        require_experience_id(self.experience_id)
        require_identity(self.fingerprint)
        if not isinstance(self.priority, Priority):
            raise ValueError("priority must be typed")
        require_sequence(self.last_used_seq)


@dataclass(frozen=True)
class ActiveWindow:
    record_id: str
    session_handle: str
    generation: int
    u_cursor: int
    entries: tuple[WindowEntry, ...]
    parent_window_id: str | None

    def __post_init__(self):
        require_identity(self.record_id)
        require_identity(self.session_handle)
        require_sequence(self.generation)
        require_sequence(self.u_cursor)
        require_entries(self.entries)
        if any(e.last_used_seq > self.u_cursor for e in self.entries):
            raise ValueError("cursor precedes entries")
        if self.parent_window_id is not None:
            require_identity(self.parent_window_id)


@dataclass(frozen=True)
class Checkpoint:
    record_id: str
    session_handle: str
    generation: int
    source_window_id: str
    entries: tuple[WindowEntry, ...]
    budget_bytes: int
    schema_version: int = 1

    def __post_init__(self):
        for value in (self.record_id, self.session_handle, self.source_window_id):
            require_identity(value)
        require_sequence(self.generation)
        require_entries(self.entries)
        if type(self.budget_bytes) is not int or not 1 <= self.budget_bytes <= 8192:
            raise ValueError("invalid checkpoint budget")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported checkpoint schema")


@dataclass(frozen=True)
class NamespaceMapping:
    app_id: str
    project_id: str
    legacy_project_ids: tuple[str, ...] = ()

    def __post_init__(self):
        require_identity(self.app_id)
        require_identity(self.project_id)
        if type(self.legacy_project_ids) is not tuple:
            raise ValueError("invalid legacy identities")
        for value in self.legacy_project_ids:
            require_identity(value)
        if len(set(self.legacy_project_ids)) != len(self.legacy_project_ids) or self.project_id in self.legacy_project_ids:
            raise ValueError("duplicate namespace")


@dataclass(frozen=True)
class ControlPointer:
    """Local control metadata only. No rule collections or bodies are allowed."""

    project_key: str
    session_id: str
    host_id: str
    session_handle: str | None = None
    generation: int = 0
    state: str = "prepared"
    window_id: str | None = None
    checkpoint_id: str | None = None
    operation_id: str | None = None
    event_id: str | None = None
    schema_version: int = 1
    request_fingerprint: str | None = None
    result_id: str | None = None
    operation_status: str | None = None
    boundary_id: str | None = None
    checkpoint_state: str | None = None
    error_code: str | None = None
    client_fingerprint: str | None = None

    def __post_init__(self):
        for value in (self.project_key, self.session_id, self.host_id):
            require_identity(value)
        for value in (self.session_handle, self.window_id, self.checkpoint_id, self.operation_id, self.event_id):
            if value is not None:
                require_identity(value)
        require_sequence(self.generation)
        if self.state not in ("prepared", "pending", "ready"):
            raise ValueError("invalid pointer state")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("invalid pointer schema")
        if self.state == "prepared" and any(value is not None for value in (
                self.session_handle, self.window_id, self.checkpoint_id, self.operation_id, self.event_id)):
            raise ValueError("unbound preparation cannot contain remote state")
        if self.state == "ready" and (self.session_handle is None or self.window_id is None):
            raise ValueError("ready needs confirmed remote identifiers")
        if self.state == "pending" and self.operation_id is None:
            raise ValueError("pending needs operation identifier")
        for value in (self.request_fingerprint, self.result_id, self.boundary_id,self.client_fingerprint):
            if value is not None:
                require_identity(value)
        if self.operation_status not in (None, "pending", "unknown", "confirmed", "failed"):
            raise ValueError("invalid operation status")
        if self.error_code is not None:
            from .errors import ErrorCode
            if self.error_code not in {code.value for code in ErrorCode}:raise ValueError("invalid error code")
        if self.checkpoint_state not in (None, "pending", "ready", "restored"):
            raise ValueError("invalid checkpoint state")


from pathlib import Path


@dataclass(frozen=True)
class HostContext:
    cwd: Path
    session_id: str
    host_id: str
    source: str

    def __post_init__(self):
        for value in (self.session_id,self.host_id,self.source): require_identity(value)
        if not isinstance(self.cwd,Path) or not self.cwd.is_absolute():
            raise ValueError('absolute host workspace required')


