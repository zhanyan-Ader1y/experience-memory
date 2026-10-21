from dataclasses import dataclass
from ..domain.ports import OperationStatus, WriteOutcome

from ..domain.errors import ErrorCode, MemoryError


@dataclass(frozen=True)
class HookRequest:
    event: str
    cwd: str
    session_id: str
    source: str | None = None
    trigger: str | None = None
    boundary_id: str | None = None


@dataclass(frozen=True)
class HookResult:
    continue_: bool
    stop_reason: str | None = None
    additional_context: str | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    package_version: str
    development_stage: str
    adapters: tuple[tuple[str, str], ...]
    configured_core_count: int
    credentials_available: bool
    enabled: bool
    mem0_authentication: str = "not_checked"
    native_compaction: str = "not_verified"
    runtime_environment: dict | None = None
