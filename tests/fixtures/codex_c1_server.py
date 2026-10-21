"""Synthetic fixed-root C1 fixture. Not a production launcher or native proof.

Only the in-memory PlatformDouble is used. Bootstrap is fixture-only and never
accepts a workspace/session identity from tool arguments. Seed bindings are
synthetic owners, not sessions retained from earlier native Codex runs.
"""
import argparse
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experience_memory.application.services import MemoryApplicationService, RetentionApplicationService
from experience_memory.application.session_binding import SessionBinding
from experience_memory.domain.errors import ErrorCode, MemoryError
from experience_memory.domain.models import HostContext, Scope
from experience_memory.infrastructure.control_repository import Mem0ControlRepository
from experience_memory.infrastructure.experience_repository import Mem0ExperienceRepository
from experience_memory.infrastructure.host_context import codex_request_host, current_host
from experience_memory.infrastructure.mem0_store import Mem0Store
from experience_memory.infrastructure.scope import resolve_project_root
from experience_memory.infrastructure.session_pointer import SessionPointerStore
from experience_memory.interfaces.mcp_server import Identity, _invoke, create_server
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ToolAnnotations
from tests.unit.test_mem0_store import PlatformDouble
from tests.fixtures.codex_c1_policy import RequestInfo, ScenarioPolicy


@dataclass(frozen=True)
class _FixtureRequest:
    request: RequestInfo
    host: HostContext
    admission: object = None


class _BoundedMemoryProxy:
    def __init__(self, fixture, raw):
        self._fixture, self._raw = fixture, raw

    def _invoke(self, name, *arguments):
        current = self._fixture._request_context.get()
        if current is None:
            raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        if current.host != current_host():
            self._fixture.policy.reject_transport(ErrorCode.HOST_SOURCE_UNVERIFIED.value)
            raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        operation = getattr(self._raw, name)
        return self._fixture.policy.invoke(current.request, lambda: operation(*arguments),
                                           admission=current.admission)

    def search(self, *arguments):
        return self._invoke("search", *arguments)

    def read(self, *arguments):
        return self._invoke("read", *arguments)

    def write(self, *arguments):
        return self._invoke("write", *arguments)

    def status(self, *arguments):
        return self._invoke("status", *arguments)


class _BoundedRetentionProxy:
    def __init__(self, fixture, raw):
        self._fixture, self._raw = fixture, raw

    def use(self, *arguments):
        current = self._fixture._request_context.get()
        if current is None:
            raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        if current.host != current_host():
            self._fixture.policy.reject_transport(ErrorCode.HOST_SOURCE_UNVERIFIED.value)
            raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        return self._fixture.policy.invoke(current.request, lambda: self._raw.use(*arguments),
                                           admission=current.admission)


def checked_root(root: Path, project: str, nonce: str) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("absolute synthetic root required")
    try:
        root = root.resolve(strict=True)
        if resolve_project_root(root) != root:
            raise ValueError("repository root required")
        marker_path = (root / ".codex/c1-fixture.json").resolve(strict=True)
        marker_path.relative_to(root)
        if marker_path.stat().st_size > 2048:
            raise ValueError("invalid synthetic marker")
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        expected = {"schema_version": 1, "purpose": "experience-memory-c1-synthetic",
                    "run_nonce": nonce, "project": project}
        if type(marker) is not dict or marker != expected or type(marker.get("schema_version")) is not int:
            raise ValueError("synthetic marker does not match this run")
        return root
    except (OSError, UnicodeError, ValueError):
        raise ValueError("invalid or unmarked synthetic repository root") from None


class NativeCodexFixture:
    def __init__(self, *, project: str, project_root: Path, other_project_root: Path,
                 run_nonce: str, audit_dir: Path, bounded_scenario: bool = False):
        if project not in ("a", "b") or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", run_nonce):
            raise ValueError("invalid synthetic run")
        other = "b" if project == "a" else "a"
        self.root = checked_root(project_root, project, run_nonce)
        foreign_root = checked_root(other_project_root, other, run_nonce)
        if self.root == foreign_root:
            raise ValueError("two distinct synthetic roots required")
        if not audit_dir.is_absolute():
            raise ValueError("absolute audit directory required")
        # Each process gets a fresh audit directory; failed attempts are never overwritten.
        audit_dir.mkdir(parents=True, exist_ok=False)
        self.audit_dir, self.run_nonce = audit_dir, run_nonce
        if type(bounded_scenario) is not bool:
            raise ValueError("bounded_scenario must be boolean")
        self.bounded_scenario = bounded_scenario
        self.policy = ScenarioPolicy(run_nonce, audit_dir) if bounded_scenario else None
        self._request_context = ContextVar("codex_c1_request_" + uuid.uuid4().hex, default=None)
        self.host_id = "codex-c1-synthetic-host"
        scopes = {root: Scope("local:" + run_nonce + "-" + name,
                             "c1-synthetic-" + run_nonce, "project-" + name, (), "synthetic-user")
                  for root, name in ((self.root, project), (foreign_root, other))}
        self._scopes = scopes
        self._temp = tempfile.TemporaryDirectory(prefix="experience-memory-c1-")
        try:
            self.api = PlatformDouble()
            self.pointers = SessionPointerStore(Path(self._temp.name) / "pointers")
            store = Mem0Store(self.api, self.pointers)
            self.controls = Mem0ControlRepository(store)
            self.experiences = Mem0ExperienceRepository(store)
            self.binding = SessionBinding(self.pointers, self.controls, self.scope, self.host_id)
            self.raw_memory = self.memory_for(current_host)
            self.memory = _BoundedMemoryProxy(self, self.raw_memory) if bounded_scenario else self.raw_memory
            raw_retention = RetentionApplicationService(
                self.experiences, self.controls, None, binding=self.binding, host_source=current_host)
            self.retention = _BoundedRetentionProxy(self, raw_retention) if bounded_scenario else raw_retention
            self.seed_hosts = {
                "prior_session": HostContext(self.root, "synthetic-prior-" + run_nonce,
                                             self.host_id, "synthetic_seed"),
                "foreign_project": HostContext(foreign_root, "synthetic-foreign-" + run_nonce,
                                               self.host_id, "synthetic_seed"),
            }
            self.seed_handles = {name: self.binding.bind(host).session_handle
                                 for name, host in self.seed_hosts.items()}
        except Exception:
            self.close()
            raise

    def scope(self, root):
        scope = self._scopes.get(root.resolve(strict=True))
        if scope is None:
            raise MemoryError(ErrorCode.SCOPE_MISMATCH)
        return scope

    def memory_for(self, host_source):
        return MemoryApplicationService(self.experiences, self.controls, None,
                                        binding=self.binding, host_source=host_source)

    def owner_status(self, name):
        return self.memory_for(lambda: self.seed_hosts[name]).status(self.seed_handles[name])

    def audit(self, *, host=None, error=None):
        invocation = uuid.uuid4().hex
        row = {"schema_version": 1, "event": "request_transport", "run_nonce": self.run_nonce,
               "invocation_id": invocation, "time_ns": time.time_ns(),
               "transport_status": "decoded" if host is not None else "rejected"}
        if host is not None:
            row.update(session_id=host.session_id,
                       cwd_sha256=hashlib.sha256(str(host.cwd).encode("utf-8")).hexdigest())
        else:
            row["error_code"] = error.code.value
        try:
            with (self.audit_dir / (invocation + ".json")).open("x", encoding="utf-8") as stream:
                json.dump(row, stream)
        except OSError:
            raise MemoryError(ErrorCode.POINTER_IO_ERROR) from None

    @staticmethod
    def _valid_call_id(value):
        return (type(value) is str and bool(value) and value == value.strip() and len(value) <= 512
                and not any(char in value for char in "*?\r\n\x00"))

    @contextmanager
    def request_host(self, metadata, *, tool=None, arguments=None):
        active = self._request_context.get()
        if self.bounded_scenario and active is not None and active.admission is not None:
            # The raw middleware already decoded/audited this request. ContextVar
            # propagation into SDK sync workers retains the same trusted identity.
            if active.host != current_host():
                self.policy.reject_transport(ErrorCode.HOST_SOURCE_UNVERIFIED.value)
                raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
            yield
            return
        transport_started = False
        request_token = None
        try:
            with codex_request_host(metadata, self.host_id, self.root):
                host = current_host()
                if self.bounded_scenario:
                    values = metadata.model_dump(by_alias=True) if hasattr(metadata, "model_dump") else metadata
                    values = values if type(values) is dict else {}
                    call_id = values.get("callId")
                    if not self._valid_call_id(call_id) or not self._valid_call_id(host.session_id):
                        raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
                    if not self._valid_call_id(tool):
                        raise MemoryError(ErrorCode.INVALID_INPUT)
                    try:
                        self.policy.canonical_arguments(arguments)
                    except ValueError:
                        raise MemoryError(ErrorCode.INVALID_INPUT) from None
                transport_started = True
                try:
                    self.audit(host=host)
                except MemoryError:
                    if self.policy is not None:
                        self.policy.fail_audit()
                    raise
                if self.bounded_scenario:
                    request = RequestInfo(host.session_id, call_id, tool, arguments)
                    request_token = self._request_context.set(_FixtureRequest(request, host))
                yield
        except MemoryError as error:
            if not transport_started:
                if self.policy is not None:
                    self.policy.reject_transport(error.code.value)
                try:
                    self.audit(error=error)
                except MemoryError:
                    if self.policy is not None:
                        self.policy.fail_audit()
                    raise
            raise
        finally:
            if request_token is not None:
                self._request_context.reset(request_token)

    def bootstrap(self, metadata, probe_nonce):
        with self.request_host(metadata, tool="c1_bootstrap", arguments={"probe_nonce": probe_nonce}):
            def bind_and_check():
                if probe_nonce != self.run_nonce:
                    raise MemoryError(ErrorCode.INVALID_INPUT)
                host = current_host()
                ctx = self.binding.bind(host)
                status = self.raw_memory.status(ctx.session_handle)
                ready = {key: status[key] for key in ("session_handle", "project_key", "session_id", "host_source")}
                ready.update(status="ready", backend="in_memory_platform_double",
                             seed_origin="synthetic_not_prior_native_sessions")
                for name in self.seed_hosts:
                    owner = self.owner_status(name)
                    ready[name] = {key: owner[key] for key in ("session_handle", "project_key", "session_id")}
                    ready[name]["owner_status_verified"] = owner["succeeded"] is True and owner["u_cursor"] == 0
                return ready

            if self.policy is None:
                return bind_and_check()
            active = self._request_context.get()
            if active is None:
                raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
            return self.policy.invoke(active.request, bind_and_check, admission=active.admission)

    def create_server(self):
        server = create_server(self.memory, self.retention, host_context_manager=self.request_host)
        if self.bounded_scenario:
            server.middleware.append(self._capture_route_request)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def c1_bootstrap(probe_nonce: Identity, ctx: Context) -> CallToolResult:
            """Synthetic fixture only: bind this transport session and return ID-only test handles."""
            return _invoke(self.bootstrap, ctx.request_context.meta, probe_nonce)

        return server

    async def _capture_route_request(self, context, call_next):
        if context.method != "tools/call":
            return await call_next(context)
        params = context.params if type(context.params) is dict else {}
        try:
            # This is before SDK method/tool lookup and argument validation. Do
            # not normalize absent/null arguments into a valid empty object.
            with self.request_host(params.get("_meta"), tool=params.get("name"),
                                   arguments=params.get("arguments")):
                active = self._request_context.get()
                admission = self.policy.reserve(active.request)
                token = self._request_context.set(_FixtureRequest(admission.request, active.host, admission))
                try:
                    try:
                        result = await call_next(context)
                        is_error = result.get("isError", False) if isinstance(result, dict) else result.is_error
                    except BaseException:
                        try:
                            self.policy.finish_dispatch(admission, failed=True)
                        except MemoryError:
                            pass  # Preserve SDK protocol errors and cancellation after latching.
                        raise
                    self.policy.finish_dispatch(admission, is_error=is_error)
                    return result
                finally:
                    self._request_context.reset(token)
        except MemoryError as error:
            return _invoke(lambda: (_ for _ in ()).throw(error))

    def close(self):
        self._temp.cleanup()


def main():
    parser = argparse.ArgumentParser(description="C1 synthetic stdio fixture; never connects Mem0")
    parser.add_argument("--project", choices=("a", "b"), required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--other-project-root", type=Path, required=True)
    parser.add_argument("--run-nonce", required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--bounded-scenario", action="store_true")
    args = parser.parse_args()
    try:
        fixture = NativeCodexFixture(**vars(args))
    except (ValueError, OSError, MemoryError):
        raise SystemExit("C1 synthetic fixture preflight failed") from None
    try:
        asyncio.run(fixture.create_server().run_stdio_async())
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
