"""Strict, bounded JSON-RPC peer for the fixed C1 app-server contract."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math

from scripts.codex_c1_process import MAX_LINE_BYTES


_CLIENT_METHODS = frozenset({
    "initialize", "config/read", "hooks/list", "account/rateLimits/read",
    "thread/start", "turn/start", "turn/interrupt",
})
_SERVER_CANCEL_METHODS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
})
_NOTIFICATIONS = frozenset({
    "thread/started", "turn/started", "item/started", "item/completed",
    "turn/completed", "thread/settings/updated", "thread/status/changed",
    "thread/tokenUsage/updated", "item/agentMessage/delta",
    "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
    "item/reasoning/textDelta", "serverRequest/resolved", "error",
})


class RpcProtocolError(RuntimeError):
    """Static, body-free protocol failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RpcMessage:
    kind: str
    method: str | None
    rpc_id: int | str | None
    payload: dict


def _reject_constant(_value: str):
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _valid_id(value) -> bool:
    if type(value) is int:
        return -(2**63) <= value < 2**63
    return type(value) is str and bool(value)


def classify_message(raw: bytes) -> RpcMessage:
    """Decode one strictly bounded LF-framed JSON-RPC message without logging it."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_LINE_BYTES:
        raise RpcProtocolError("C1_RPC_FRAME_INVALID")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise RpcProtocolError("C1_RPC_FRAME_INVALID")
    try:
        text = raw[:-1].decode("utf-8", errors="strict")
        decoded = json.loads(text, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant, parse_float=_finite_float)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise RpcProtocolError("C1_RPC_JSON_INVALID") from None
    if type(decoded) is not dict:
        raise RpcProtocolError("C1_RPC_MESSAGE_INVALID")
    if "jsonrpc" in decoded and decoded["jsonrpc"] != "2.0":
        raise RpcProtocolError("C1_RPC_VERSION_INVALID")

    has_id = "id" in decoded
    rpc_id = decoded.get("id")
    if has_id and not _valid_id(rpc_id):
        raise RpcProtocolError("C1_RPC_ID_INVALID")

    has_method = "method" in decoded
    has_result = "result" in decoded
    has_error = "error" in decoded
    if has_method:
        method = decoded["method"]
        if type(method) is not str:
            raise RpcProtocolError("C1_RPC_METHOD_INVALID")
        if has_result or has_error:
            raise RpcProtocolError("C1_RPC_MESSAGE_INVALID")
        return RpcMessage("server_request" if has_id else "notification",
                          method, rpc_id if has_id else None, decoded)
    if has_result == has_error or not has_id:
        raise RpcProtocolError("C1_RPC_MESSAGE_INVALID")
    if has_error:
        error = decoded["error"]
        if (type(error) is not dict or type(error.get("code")) is not int
                or not -(2**63) <= error["code"] < 2**63
                or type(error.get("message")) is not str):
            raise RpcProtocolError("C1_RPC_ERROR_INVALID")
        return RpcMessage("error", None, rpc_id, decoded)
    return RpcMessage("response", None, rpc_id, decoded)


def _is_string(value) -> bool:
    return type(value) is str


def _is_string_or_null(value) -> bool:
    return value is None or type(value) is str


def _is_int64(value) -> bool:
    return type(value) is int and -(2**63) <= value < 2**63


def _is_nullable_string_field(params: dict, field: str) -> bool:
    return field not in params or _is_string_or_null(params[field])


def _valid_network_amendment(value) -> bool:
    return (type(value) is dict and _is_string(value.get("host"))
            and type(value.get("action")) is str and value["action"] in ("allow", "deny"))


def _valid_command_decision(value) -> bool:
    if type(value) is str:
        return value in {"accept", "acceptForSession", "decline", "cancel"}
    if type(value) is not dict:
        return False
    if set(value) == {"acceptWithExecpolicyAmendment"}:
        nested = value["acceptWithExecpolicyAmendment"]
        return (type(nested) is dict and "execpolicy_amendment" in nested
                and type(nested["execpolicy_amendment"]) is list
                and all(_is_string(rule) for rule in nested["execpolicy_amendment"]))
    if set(value) == {"applyNetworkPolicyAmendment"}:
        nested = value["applyNetworkPolicyAmendment"]
        return (type(nested) is dict and "network_policy_amendment" in nested
                and _valid_network_amendment(nested["network_policy_amendment"]))
    return False


def _valid_command_action(value) -> bool:
    if type(value) is not dict or type(value.get("type")) is not str:
        return False
    kind = value["type"]
    if kind == "read":
        return all(_is_string(value.get(field)) for field in ("command", "name", "path"))
    if kind == "listFiles":
        return (_is_string(value.get("command"))
                and ("path" not in value or _is_string_or_null(value["path"])) )
    if kind == "search":
        return (_is_string(value.get("command"))
                and ("path" not in value or _is_string_or_null(value["path"]))
                and ("query" not in value or _is_string_or_null(value["query"])))
    if kind == "unknown":
        return _is_string(value.get("command"))
    return False


def _valid_network_context(value) -> bool:
    return (type(value) is dict and _is_string(value.get("host"))
            and type(value.get("protocol")) is str
            and value["protocol"] in {"http", "https", "socks5Tcp", "socks5Udp"})


def _valid_command_params(params: dict) -> bool:
    if not (_is_string(params.get("itemId")) and _is_int64(params.get("startedAtMs"))
            and _is_string(params.get("threadId")) and _is_string(params.get("turnId"))):
        return False
    if not all(_is_nullable_string_field(params, field)
               for field in ("approvalId", "command", "cwd", "environmentId", "reason")):
        return False
    if "kind" in params and (type(params["kind"]) is not str
                              or params["kind"] not in {"command", "writeStdin"}):
        return False
    if "additionalPermissions" in params:
        profile = params["additionalPermissions"]
        if profile is not None and not _valid_additional_profile(profile):
            return False
    decisions = params.get("availableDecisions")
    if decisions is not None and (type(decisions) is not list
                                  or not all(_valid_command_decision(item) for item in decisions)):
        return False
    actions = params.get("commandActions")
    if actions is not None and (type(actions) is not list
                                or not all(_valid_command_action(item) for item in actions)):
        return False
    context = params.get("networkApprovalContext")
    if context is not None and not _valid_network_context(context):
        return False
    amendment = params.get("proposedExecpolicyAmendment")
    if amendment is not None and (type(amendment) is not list or not all(_is_string(x) for x in amendment)):
        return False
    amendments = params.get("proposedNetworkPolicyAmendments")
    if amendments is not None and (type(amendments) is not list
                                   or not all(_valid_network_amendment(x) for x in amendments)):
        return False
    return True


def _valid_additional_profile(value) -> bool:
    if type(value) is not dict:
        return False
    if "fileSystem" in value and value["fileSystem"] is not None and not _valid_filesystem_permissions(value["fileSystem"]):
        return False
    if "network" in value and value["network"] is not None and not _valid_network_permissions(value["network"]):
        return False
    return True


def _valid_network_permissions(value) -> bool:
    return (type(value) is dict and
            ("enabled" not in value or value["enabled"] is None or type(value["enabled"]) is bool))


def _valid_filesystem_permissions(value) -> bool:
    if type(value) is not dict:
        return False
    if "entries" in value:
        entries = value["entries"]
        if entries is not None and (type(entries) is not list or not all(_valid_filesystem_entry(x) for x in entries)):
            return False
    if "globScanMaxDepth" in value:
        depth = value["globScanMaxDepth"]
        if depth is not None and (type(depth) is not int or depth < 1):
            return False
    for field in ("read", "write"):
        paths = value.get(field)
        if field in value and paths is not None and (type(paths) is not list or not all(_is_string(x) for x in paths)):
            return False
    return True


def _valid_filesystem_entry(value) -> bool:
    return (type(value) is dict and type(value.get("access")) is str
            and value["access"] in {"read", "write", "deny"}
            and _valid_filesystem_path(value.get("path")))


def _valid_filesystem_path(value) -> bool:
    if type(value) is not dict:
        return False
    kind = value.get("type")
    if kind == "path":
        return _is_string(value.get("path"))
    if kind == "glob_pattern":
        return _is_string(value.get("pattern"))
    if kind == "special":
        return _valid_special_path(value.get("value"))
    return False


def _valid_special_path(value) -> bool:
    if type(value) is not dict or type(value.get("kind")) is not str:
        return False
    kind = value["kind"]
    if kind in {"root", "minimal", "tmpdir", "slash_tmp"}:
        return True
    if kind == "project_roots":
        return "subpath" not in value or _is_string_or_null(value["subpath"])
    if kind == "unknown":
        return (_is_string(value.get("path"))
                and ("subpath" not in value or _is_string_or_null(value["subpath"])))
    return False


def _valid_permission_profile(value) -> bool:
    if type(value) is not dict or not set(value).issubset({"fileSystem", "network"}):
        return False
    if "fileSystem" in value and value["fileSystem"] is not None and not _valid_filesystem_permissions(value["fileSystem"]):
        return False
    if "network" in value and value["network"] is not None and not _valid_network_permissions(value["network"]):
        return False
    return True


def _valid_approval(message: RpcMessage) -> bool:
    params = message.payload.get("params")
    if type(params) is not dict:
        return False
    method = message.method
    if method == "item/commandExecution/requestApproval":
        return _valid_command_params(params)
    if method == "item/fileChange/requestApproval":
        if not (_is_string(params.get("itemId")) and _is_int64(params.get("startedAtMs"))
                and _is_string(params.get("threadId")) and _is_string(params.get("turnId"))):
            return False
        return all(_is_nullable_string_field(params, field) for field in ("reason", "grantRoot"))
    if method == "item/permissions/requestApproval":
        return (_is_string(params.get("cwd")) and _is_string(params.get("itemId"))
                and _is_int64(params.get("startedAtMs")) and _is_string(params.get("threadId"))
                and _is_string(params.get("turnId")) and _valid_permission_profile(params.get("permissions"))
                and all(_is_nullable_string_field(params, field) for field in ("reason", "environmentId")))
    return False


def is_valid_approval_request(message: RpcMessage) -> bool:
    """Pure pinned approval schema predicate; no ownership or reply authority.

    The caller must still bind thread/turn/item ownership and direction-specific
    ID state. This does not register, answer, or approve a server request.
    """
    if (type(message) is not RpcMessage or message.kind != "server_request"
            or type(message.payload) is not dict or not _valid_id(message.rpc_id)
            or type(message.method) is not str or message.method not in _SERVER_CANCEL_METHODS):
        return False
    payload = message.payload
    if (type(payload.get("id")) is not type(message.rpc_id)
            or payload.get("id") != message.rpc_id or payload.get("method") != message.method
            or "result" in payload or "error" in payload
            or ("jsonrpc" in payload and payload["jsonrpc"] != "2.0")):
        return False
    return _valid_approval(message)


class AppServerPeer:
    """Synchronous single-client-request peer over Task1's owned binary pipes."""

    def __init__(self, process):
        if not callable(getattr(process, "send_line", None)) or not callable(getattr(process, "read_line", None)):
            raise RpcProtocolError("C1_RPC_PROCESS_INVALID")
        self.process = process
        self._next_client_id = 1
        self._pending_client: tuple[int, str] | None = None
        self._server_seen: set[tuple[type, int | str]] = set()
        self._server_pending: dict[tuple[type, int | str], RpcMessage] = {}
        self._server_threads: dict[tuple[type, int | str], object] = {}
        self._server_resolved: set[tuple[type, int | str]] = set()
        self._server_replied: set[tuple[type, int | str]] = set()
        self._initialized_sent = False
        self._failed = False
        self._failure_code: str | None = None

    @property
    def pending_client_id(self) -> int | None:
        return self._pending_client[0] if self._pending_client is not None else None

    @property
    def failure_code(self) -> str | None:
        """Static failure classification, suitable for later Session handling."""
        return self._failure_code

    def _poison(self, code: str):
        self._failed = True
        if self._failure_code is None:
            self._failure_code = code

    def _require_live(self):
        if self._failed:
            raise RpcProtocolError(self._failure_code or "C1_RPC_PEER_FAILED")

    def _send(self, message: dict):
        try:
            raw = json.dumps(message, ensure_ascii=True, allow_nan=False,
                              separators=(",", ":")).encode("utf-8") + b"\n"
        except (TypeError, ValueError, OverflowError):
            self._poison("C1_RPC_OUTBOUND_INVALID")
            raise RpcProtocolError(self._failure_code) from None
        if len(raw) > MAX_LINE_BYTES:
            self._poison("C1_RPC_FRAME_LIMIT")
            raise RpcProtocolError(self._failure_code)
        try:
            self.process.send_line(raw)
        except Exception:
            # A write can have an unknown prefix. Do not clear state or resend.
            self._poison("C1_RPC_WRITE_FAILED")
            raise RpcProtocolError(self._failure_code) from None

    def send_request(self, method: str, params: dict) -> int:
        self._require_live()
        if type(method) is not str or method not in _CLIENT_METHODS:
            raise RpcProtocolError("C1_RPC_METHOD_NOT_ALLOWED")
        if type(params) is not dict:
            raise RpcProtocolError("C1_RPC_PARAMS_INVALID")
        if self._pending_client is not None:
            raise RpcProtocolError("C1_RPC_CLIENT_REQUEST_PENDING")
        rpc_id = self._next_client_id
        self._next_client_id += 1
        self._pending_client = (rpc_id, method)
        self._send({"id": rpc_id, "method": method, "params": params})
        return rpc_id

    def send_initialized(self) -> None:
        self._require_live()
        if self._initialized_sent:
            raise RpcProtocolError("C1_RPC_NOTIFICATION_DUPLICATE")
        self._send({"method": "initialized"})
        self._initialized_sent = True

    def _validate_resolution(self, message: RpcMessage) -> None:
        params = message.payload.get("params")
        if type(params) is not dict:
            raise RpcProtocolError("C1_RPC_SERVER_RESOLUTION_INVALID")
        request_id, thread_id = params.get("requestId"), params.get("threadId")
        if not _valid_id(request_id) or not _is_string(thread_id):
            raise RpcProtocolError("C1_RPC_SERVER_RESOLUTION_INVALID")
        key = (type(request_id), request_id)
        if (key not in self._server_seen or key in self._server_resolved
                or self._server_threads.get(key) != thread_id):
            raise RpcProtocolError("C1_RPC_SERVER_RESOLUTION_INVALID")
        self._server_resolved.add(key)

    def next_message(self) -> RpcMessage:
        self._require_live()
        try:
            raw = self.process.read_line()
            if raw is None:
                raise RpcProtocolError("C1_RPC_UNEXPECTED_EOF")
            message = classify_message(raw)
            if message.kind == "response" or message.kind == "error":
                if self._pending_client is None or type(message.rpc_id) is not int or message.rpc_id != self._pending_client[0]:
                    raise RpcProtocolError("C1_RPC_RESPONSE_ID_MISMATCH")
                self._pending_client = None
                return message
            if message.kind == "notification":
                if message.method not in _NOTIFICATIONS:
                    raise RpcProtocolError("C1_RPC_NOTIFICATION_NOT_ALLOWED")
                if message.method == "error":
                    raise RpcProtocolError("C1_RPC_REMOTE_ERROR")
                if message.method == "serverRequest/resolved":
                    self._validate_resolution(message)
                return message
            if message.kind == "server_request":
                if message.method not in _SERVER_CANCEL_METHODS:
                    raise RpcProtocolError("C1_RPC_SERVER_REQUEST_NOT_ALLOWED")
                key = (type(message.rpc_id), message.rpc_id)
                if key in self._server_seen:
                    raise RpcProtocolError("C1_RPC_SERVER_ID_REUSED")
                self._server_seen.add(key)
                params = message.payload.get("params")
                self._server_threads[key] = params.get("threadId") if type(params) is dict else None
                self._server_pending[key] = message
                return message
            raise RpcProtocolError("C1_RPC_MESSAGE_INVALID")
        except RpcProtocolError as error:
            self._poison(error.code)
            raise
        except Exception:
            self._poison("C1_RPC_READ_FAILED")
            raise RpcProtocolError(self._failure_code) from None

    def reply_cancel(self, message: RpcMessage) -> None:
        self._require_live()
        if type(message) is not RpcMessage or message.kind != "server_request":
            self._poison("C1_RPC_CANCEL_REQUEST_INVALID")
            raise RpcProtocolError(self._failure_code)
        key = (type(message.rpc_id), message.rpc_id)
        if self._server_pending.get(key) is not message or key in self._server_replied:
            self._poison("C1_RPC_CANCEL_REQUEST_UNKNOWN")
            raise RpcProtocolError(self._failure_code)
        if not is_valid_approval_request(message):
            self._poison("C1_RPC_CANCEL_SCHEMA_INVALID")
            raise RpcProtocolError(self._failure_code)
        if message.method == "item/permissions/requestApproval":
            result = {"permissions": {}, "scope": "turn"}
        else:
            result = {"decision": "cancel"}
        self._send({"id": message.rpc_id, "result": result})
        self._server_replied.add(key)
        del self._server_pending[key]
