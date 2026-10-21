"""Bounded, fixture-only C1 scenario admission policy."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Callable

from experience_memory.domain.errors import ErrorCode, MemoryError


@dataclass(frozen=True)
class RequestInfo:
    session_id: str
    call_id: str
    tool: str
    arguments: dict


@dataclass(eq=False)
class _Admission:
    request: RequestInfo
    step: int
    started: bool = False
    outcome: str | None = None


class ScenarioPolicy:
    """Reserve raw dispatch before SDK validation; count only business entry.

    The state lock covers short state/audit transitions, never SDK awaits or
    business operations. The single reservation spans both, so overlap freezes
    rather than waiting to become the next step. Workers recheck the latch
    before entry and before commit; already-started operations are not undone.
    """

    _TOOLS = ("c1_bootstrap", "memory_status", "memory_status", "memory_status", "memory_status")
    _BASE_FIELDS = {"schema_version", "event", "run_nonce", "invocation_id", "time_ns",
                    "step", "session_id", "call_id", "tool", "arguments_sha256", "outcome", "result"}

    def __init__(self, run_nonce: str, audit_dir: Path):
        if not isinstance(run_nonce, str) or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", run_nonce):
            raise ValueError("invalid bounded scenario nonce")
        if not isinstance(audit_dir, Path) or not audit_dir.is_absolute() or not audit_dir.is_dir():
            raise ValueError("existing absolute audit directory required")
        self.run_nonce = run_nonce
        self.audit_dir = audit_dir
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self._audit_failed = threading.Event()
        self._audit_lock = threading.Lock()
        self.phase = "ready"
        self.admitted = 0
        self.completed = 0
        self.session_id = None
        self.failure_reason = None
        self._call_ids = set()
        self._handle = None
        self._project_key = None
        self._seed_handles = {}
        self._active = None

    def snapshot(self) -> dict:
        with self.lock:
            return {"phase": self.phase, "admitted": self.admitted, "completed": self.completed,
                    "session_id": self.session_id, "failure_reason": self.failure_reason}

    def reject_transport(self, error_code: str) -> None:
        self.stop_requested.set()
        if not isinstance(error_code, str) or not error_code:
            error_code = ErrorCode.HOST_SOURCE_UNVERIFIED.value
        with self.lock:
            self._freeze("rejected", error_code)

    def fail_audit(self) -> None:
        self._audit_failed.set()
        self.stop_requested.set()
        with self.lock:
            self._freeze("unknown", "audit_failure")

    def reserve(self, request: RequestInfo) -> _Admission:
        """Validate exact raw input and reserve dispatch, without admitting business."""
        if not self.lock.acquire(blocking=False):
            # Signal before waiting for a short state/audit transition. Otherwise
            # a contender could queue until the active dispatch releases its slot.
            self.stop_requested.set()
            with self.lock:
                self._freeze("rejected", ErrorCode.INVALID_INPUT.value)
                self._write_rejection(request, ErrorCode.INVALID_INPUT.value)
            raise MemoryError(ErrorCode.INVALID_INPUT)
        try:
            try:
                step = self._check_request_before_operation(request)
            except MemoryError as error:
                self._freeze("rejected", error.code.value)
                self._write_rejection(request, error.code.value)
                raise
            # Valid bounded arguments contain one string; detach from raw params.
            request = RequestInfo(request.session_id, request.call_id, request.tool, dict(request.arguments))
            admission = _Admission(request, step)
            self._active = admission
            return admission
        finally:
            self.lock.release()

    def finish_dispatch(self, admission: _Admission, *, failed=False, is_error=None) -> None:
        """Release the reservation, latching SDK errors or missing business entry.

        Expected SESSION_INVALID is a business outcome, not a dispatch failure.
        A returned SDK error without proxy entry and a raised SDK error both
        reject without increasing admitted/completed.
        """
        with self.lock:
            try:
                if self._active is not admission:
                    self._freeze("rejected", ErrorCode.INVALID_INPUT.value)
                    raise MemoryError(ErrorCode.INVALID_INPUT)
                mismatch = (is_error is not None and admission.outcome in ("succeeded", "expected_error")
                            and is_error is not (admission.outcome == "expected_error"))
                if failed or admission.outcome is None or mismatch:
                    self._reject_admission(admission, ErrorCode.INVALID_INPUT.value)
                    raise MemoryError(ErrorCode.INVALID_INPUT)
            finally:
                if self._active is admission:
                    self._active = None

    def _reject_admission(self, admission: _Admission, error_code: str, *, phase="rejected") -> None:
        self._freeze(phase, error_code)
        if admission.outcome is None:
            # A failed audit cannot supply proof; don't retry or manufacture a row.
            if not self._audit_failed.is_set():
                self._write_rejection(admission.request, error_code, step=admission.step)
            admission.outcome = "rejected"

    def invoke(self, request: RequestInfo, operation: Callable[[], dict], *, admission=None) -> dict:
        owns_reservation = admission is None
        if owns_reservation:
            admission = self.reserve(request)
        try:
            with self.lock:
                if (self._active is not admission or admission.started or request != admission.request
                        or self.stop_requested.is_set()):
                    self._reject_admission(admission, ErrorCode.INVALID_INPUT.value)
                    raise MemoryError(ErrorCode.INVALID_INPUT)
                admission.started = True
                self.admitted += 1
            step = admission.step
            # No state lock is held while synchronous business runs in an SDK worker.
            try:
                value = operation()
            except MemoryError as error:
                with self.lock:
                    try:
                        self._check_commit_allowed()
                        if step in (3, 4) and error.code is ErrorCode.SESSION_INVALID:
                            self._finish_error(request, step, error.code.value)
                            admission.outcome = "expected_error"
                        else:
                            self._reject_admission(admission, error.code.value)
                    except MemoryError as commit_error:
                        self._reject_admission(admission, commit_error.code.value)
                        raise
                raise
            except BaseException:
                with self.lock:
                    if self._audit_failed.is_set():
                        self._reject_admission(admission, "audit_failure", phase="unknown")
                    else:
                        self._reject_admission(admission, "operation_exception", phase="unknown")
                raise
            with self.lock:
                try:
                    self._finish_value(request, step, value)
                    admission.outcome = "succeeded"
                except MemoryError as error:
                    self._reject_admission(admission, error.code.value)
                    raise
            return value
        finally:
            if owns_reservation:
                self.finish_dispatch(admission)

    def _check_request_before_operation(self, request: RequestInfo) -> int:
        if not isinstance(request, RequestInfo):
            raise MemoryError(ErrorCode.INVALID_INPUT)
        try:
            self.canonical_arguments(request.arguments)
        except ValueError:
            raise MemoryError(ErrorCode.INVALID_INPUT) from None
        if not all(type(value) is str and value for value in
                   (request.session_id, request.call_id, request.tool)):
            raise MemoryError(ErrorCode.INVALID_INPUT)
        if (self._active is not None or self.stop_requested.is_set()
                or self.phase in ("rejected", "unknown", "complete")):
            raise MemoryError(ErrorCode.INVALID_INPUT)
        step = self.completed + 1
        if step > len(self._TOOLS) or request.call_id in self._call_ids:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        if request.tool != self._TOOLS[step - 1]:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        if step == 1:
            expected_arguments = {"probe_nonce": self.run_nonce}
            if request.arguments != expected_arguments:
                raise MemoryError(ErrorCode.INVALID_INPUT)
        else:
            if request.session_id != self.session_id:
                raise MemoryError(ErrorCode.INVALID_INPUT)
            handle = self._handle if step in (2, 5) else self._seed_handles.get("prior_session" if step == 3 else "foreign_project")
            if request.arguments != {"session_handle": handle} or not handle:
                raise MemoryError(ErrorCode.INVALID_INPUT)
        self._call_ids.add(request.call_id)
        return step

    def _finish_error(self, request: RequestInfo, step: int, error_code: str) -> None:
        self._check_commit_allowed()
        try:
            self._write_business(request, step, "expected_error", error_code=error_code,
                                 result={"error_code": error_code})
        except Exception:
            self._freeze("unknown", "audit_failure")
            raise MemoryError(ErrorCode.INVALID_INPUT) from None
        self.completed += 1
        self.phase = "complete" if self.completed == len(self._TOOLS) else "active"

    def _finish_value(self, request: RequestInfo, step: int, value: dict) -> None:
        self._check_commit_allowed()
        if not self._valid_value(request, step, value):
            raise MemoryError(ErrorCode.INVALID_INPUT)
        if step == 1:
            self.session_id = request.session_id
            self._handle = value["session_handle"]
            self._project_key = value["project_key"]
            self._seed_handles = {name: value[name]["session_handle"]
                                  for name in ("prior_session", "foreign_project")}
        try:
            result = ({"status": "ready", "session_id": value["session_id"],
                       "project_key": value["project_key"]} if step == 1 else
                      {"status": "succeeded", "session_id": value["session_id"],
                       "project_key": value["project_key"], "u_cursor": value["u_cursor"],
                       "entry_count": len(value["entries"])})
            self._write_business(request, step, "succeeded", result=result)
        except Exception:
            self._freeze("unknown", "audit_failure")
            raise MemoryError(ErrorCode.INVALID_INPUT) from None
        self.completed += 1
        self.phase = "complete" if self.completed == len(self._TOOLS) else "active"

    def _check_commit_allowed(self) -> None:
        if self._audit_failed.is_set():
            self._freeze("unknown", "audit_failure")
            raise MemoryError(ErrorCode.INVALID_INPUT)
        if self.stop_requested.is_set():
            self._freeze("rejected", ErrorCode.INVALID_INPUT.value)
            raise MemoryError(ErrorCode.INVALID_INPUT)

    def _valid_value(self, request: RequestInfo, step: int, value) -> bool:
        if type(value) is not dict:
            return False
        if step == 1:
            if (value.get("status") != "ready" or value.get("session_id") != request.session_id
                    or not self._valid_id(value.get("session_handle"))
                    or not isinstance(value.get("project_key"), str)
                    or not re.fullmatch(r"local:" + re.escape(self.run_nonce) + r"-[ab]", value["project_key"])
                    or value.get("host_source") != "codex_stdio_0.163.0"
                    or value.get("backend") != "in_memory_platform_double"
                    or value.get("seed_origin") != "synthetic_not_prior_native_sessions"):
                return False
            prior, foreign = value.get("prior_session"), value.get("foreign_project")
            if type(prior) is not dict or type(foreign) is not dict:
                return False
            ids = (value["session_id"], prior.get("session_id"), foreign.get("session_id"))
            handles = (value["session_handle"], prior.get("session_handle"), foreign.get("session_handle"))
            return (prior.get("owner_status_verified") is True and foreign.get("owner_status_verified") is True
                    and all(self._valid_id(item) for item in ids + handles)
                    and len(set(ids)) == 3 and len(set(handles)) == 3
                    and prior.get("project_key") == value["project_key"]
                    and isinstance(foreign.get("project_key"), str)
                    and foreign["project_key"] != value["project_key"])
        if (value.get("succeeded") is not True or value.get("session_id") != self.session_id
                or value.get("project_key") != self._project_key
                or value.get("host_source") != "codex_stdio_0.163.0"
                or type(value.get("u_cursor")) is not int or value["u_cursor"] != 0
                or type(value.get("entries")) is not list or value["entries"]):
            return False
        return True

    @staticmethod
    def _valid_id(value) -> bool:
        return isinstance(value, str) and bool(value) and len(value) <= 512

    def _write_business(self, request: RequestInfo, step: int, outcome: str, *, result: dict,
                        error_code=None) -> None:
        row = self._base_row(request, step, "request_business", outcome)
        row["result"] = result
        if error_code is not None:
            row["error_code"] = error_code
        self._write_row(row, "request_business")

    def _write_rejection(self, request: RequestInfo, error_code: str, *, step=None) -> None:
        try:
            next_step = self.completed + 1 if step is None else step
            row = self._base_row(request, next_step, "request_business_rejected", "rejected")
            row["result"] = {"error_code": error_code}
            row["error_code"] = error_code
            self._write_row(row, "request_business_rejected")
        except Exception:
            self._freeze("unknown", "audit_failure")
            raise MemoryError(ErrorCode.INVALID_INPUT) from None

    def _base_row(self, request: RequestInfo, step: int, event: str, outcome: str) -> dict:
        return {"schema_version": 1, "event": event, "run_nonce": self.run_nonce,
                "invocation_id": uuid.uuid4().hex, "time_ns": time.time_ns(), "step": step,
                "session_id": request.session_id, "call_id": request.call_id, "tool": request.tool,
                "arguments_sha256": hashlib.sha256(self.canonical_arguments(request.arguments)).hexdigest(),
                "outcome": outcome}

    @staticmethod
    def canonical_arguments(arguments: dict) -> bytes:
        if type(arguments) is not dict or any(type(key) is not str for key in arguments):
            raise ValueError("arguments must be a JSON object with string keys")
        try:
            return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("arguments are not canonical JSON") from None

    def _write_row(self, row: dict, event: str) -> None:
        expected = self._BASE_FIELDS | ({"error_code"} if "error_code" in row else set())
        if set(row) != expected or row["event"] != event:
            raise ValueError("business audit schema mismatch")
        if (type(row["schema_version"]) is not int or row["schema_version"] != 1
                or type(row["time_ns"]) is not int or type(row["step"]) is not int
                or not 1 <= row["step"] <= len(self._TOOLS) + 1
                or row["run_nonce"] != self.run_nonce
                or not all(isinstance(row[key], str) and row[key] for key in
                           ("run_nonce", "invocation_id", "session_id", "call_id", "tool", "outcome",
                            "arguments_sha256"))
                or not re.fullmatch(r"[0-9a-f]{32}", row["invocation_id"])
                or (event != "request_business_rejected" and row["tool"] not in self._TOOLS)
                or not re.fullmatch(r"[0-9a-f]{64}", row["arguments_sha256"])):
            raise ValueError("business audit field type mismatch")
        if type(row["result"]) is not dict:
            raise ValueError("business audit result must be an object")
        if event == "request_business_rejected":
            if (row["outcome"] != "rejected" or "error_code" not in row
                    or row["result"] != {"error_code": row["error_code"]}):
                raise ValueError("business rejection audit result mismatch")
        elif row["outcome"] == "expected_error":
            if (row["step"] not in (3, 4) or row.get("error_code") != ErrorCode.SESSION_INVALID.value
                    or row["result"] != {"error_code": ErrorCode.SESSION_INVALID.value}):
                raise ValueError("expected-error audit result mismatch")
        elif row["outcome"] == "succeeded" and row["step"] == 1:
            result = row["result"]
            if (set(result) != {"status", "session_id", "project_key"} or result.get("status") != "ready"
                    or not self._valid_id(result.get("session_id"))
                    or not re.fullmatch(r"local:" + re.escape(self.run_nonce) + r"-[ab]",
                                        str(result.get("project_key", "")))):
                raise ValueError("bootstrap audit result mismatch")
        elif row["outcome"] == "succeeded" and row["step"] in (2, 5):
            result = row["result"]
            if (set(result) != {"status", "session_id", "project_key", "u_cursor", "entry_count"}
                    or result.get("status") != "succeeded" or not self._valid_id(result.get("session_id"))
                    or not isinstance(result.get("project_key"), str)
                    or type(result.get("u_cursor")) is not int or result["u_cursor"] != 0
                    or type(result.get("entry_count")) is not int or result["entry_count"] != 0):
                raise ValueError("status audit result mismatch")
        else:
            raise ValueError("business audit outcome mismatch")
        if "error_code" in row and (not isinstance(row["error_code"], str) or not row["error_code"]):
            raise ValueError("business audit error field mismatch")
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False)
        with self._audit_lock:
            with (self.audit_dir / (row["invocation_id"] + ".json")).open("x", encoding="utf-8") as stream:
                stream.write(encoded)

    def _freeze(self, phase: str, reason: str) -> None:
        self.stop_requested.set()
        if phase == "unknown" and reason == "audit_failure":
            self._audit_failed.set()
        # Failure is irreversible; audit loss dominates a known rejection.
        if self.phase == "unknown" or (self.phase == "rejected" and phase != "unknown"):
            return
        self.phase = phase
        self.failure_reason = reason
