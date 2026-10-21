"""Per-request identity adapter. Never derives identity from the tool's handle or cwd args.

The exact Codex metadata contract is pending G1. Missing fields fail closed.
A fixture provider can be injected without enabling a production capability claim.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from ..domain.models import HostContext
from ..domain.errors import MemoryError,ErrorCode

_current=ContextVar('memory_host_identity',default=None)


def current_host():
    value=_current.get()
    if value is None:raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
    return value


@contextmanager
def request_host(metadata,host_id,*,verified_contract=False):
    if verified_contract is not True:
        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
    # This is an explicit provisional contract, not guessed launch cwd or a claimed native verification.
    values=metadata.model_dump(by_alias=True) if hasattr(metadata,'model_dump') else metadata
    if not isinstance(values,dict):raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
    identity=values.get('experience_memory_host')
    if type(identity) is not dict or set(identity)!={'cwd','session_id'}:
        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
    try:
        host=HostContext(Path(identity['cwd']),identity['session_id'],host_id,'mcp_request_metadata_unverified')
    except (TypeError,ValueError):raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED) from None
    token=_current.set(host)
    try:yield
    finally:_current.reset(token)


def decode_codex_metadata(metadata, host_id: str, project_root: Path) -> HostContext:
    try:
        values = metadata.model_dump(by_alias=True) if hasattr(metadata, 'model_dump') else metadata
        if type(values) is not dict:
            raise ValueError
        nested = values.get('x-codex-turn-metadata')
        if type(nested) is not dict or nested.get('codex_version') != '0.163.0':
            raise ValueError
        ids = (values.get('sessionId'), values.get('threadId'),
               nested.get('session_id'), nested.get('thread_id'))
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != 1:
            raise ValueError
        workspaces = nested.get('workspaces')
        if type(workspaces) is not dict or len(workspaces) != 1:
            raise ValueError
        workspace, workspace_value = next(iter(workspaces.items()))
        if type(workspace_value) is not dict or not isinstance(workspace, str):
            raise ValueError
        workspace_path = Path(workspace)
        if not workspace_path.is_absolute():
            raise ValueError
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise ValueError
        canonical_workspace = workspace_path.resolve(strict=True)
        canonical_root = project_root.resolve(strict=True)
        if not canonical_workspace.is_dir() or not canonical_root.is_dir():
            raise ValueError
        # Codex workspaces identify repository roots, not the request's dynamic cwd.
        if canonical_workspace != canonical_root:
            raise MemoryError(ErrorCode.SCOPE_MISMATCH)
        host = HostContext(canonical_root, ids[0], host_id, 'codex_stdio_0.163.0')
    except MemoryError:
        raise
    except Exception:
        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED) from None
    return host


@contextmanager
def codex_request_host(metadata, host_id: str, project_root: Path):
    host = decode_codex_metadata(metadata, host_id, project_root)
    token = _current.set(host)
    try:
        yield
    finally:
        _current.reset(token)

