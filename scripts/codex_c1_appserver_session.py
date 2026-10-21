"""One reserved C1 app-server thread/turn, with returned-policy evidence only.

No I/O, preflight authority, client RPC ID allocation, retry, or acceptance gate
lives here. The driver owns those and must stop/close after any frozen state.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from scripts.codex_c1_appserver_rpc import RpcMessage, is_valid_approval_request
from scripts.codex_c1_bundle import ReviewSnapshot


_APPROVAL_ACTIONS = {
    "item/commandExecution/requestApproval": "cancel",
    "item/fileChange/requestApproval": "cancel",
    "item/permissions/requestApproval": "deny_permissions",
}
_DELTAS = {
    "item/agentMessage/delta": None,
    "item/reasoning/summaryTextDelta": "summaryIndex",
    "item/reasoning/summaryPartAdded": "summaryIndex",
    "item/reasoning/textDelta": "contentIndex",
}
_MISSING = object()


def _int64(value):
    return type(value) is int and -(2**63) <= value < 2**63


def _id(value):
    return _int64(value) or (type(value) is str and bool(value))


def _text(value):
    return type(value) is str and bool(value)


def _fingerprint(value):
    # Only a digest is retained for exact notification duplicate comparison.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class _Evidence:
    """Collect all checks before choosing failure over absent evidence."""

    def __init__(self):
        self.failures = []
        self.missing = []

    def field(self, obj, name, predicate, code="C1_APPSERVER_SCHEMA_INVALID"):
        if name not in obj:
            self.missing.append("C1_APPSERVER_EVIDENCE_MISSING")
            return _MISSING
        value = obj[name]
        if not predicate(value):
            self.failures.append(code)
        return value


class AppServerSession:
    """Pure lifecycle core; only a persisted reservation's detached DTO is bound.

    Construction does NOT establish initialized/preflight_checked. The driver
    must obtain those independently before reserve_attempt and thread/start.
    """

    def __init__(self, snapshot: ReviewSnapshot, project: str, *, model: str, provider: str):
        try:
            if (not isinstance(snapshot, ReviewSnapshot) or type(project) is not str
                    or project not in ("a", "b") or not _text(model) or not _text(provider)):
                raise ValueError
            info = snapshot.manifest["projects"][project]
            root_value, prompt = info["root"], info["prompt"]
            if type(root_value) is not str or not Path(root_value).is_absolute() or not _text(prompt):
                raise ValueError
            root = Path(root_value).resolve(strict=True)
            if (not root.is_dir() or root != (snapshot.bundle / "projects" / project).resolve(strict=True)
                    or not _text(snapshot.run_nonce) or not _text(snapshot.manifest_sha256)):
                raise ValueError
        except (AttributeError, KeyError, TypeError, OSError, ValueError):
            raise ValueError("C1_APPSERVER_INPUT_INVALID") from None
        self._project = project
        self._nonce = snapshot.run_nonce
        self._manifest_sha256 = snapshot.manifest_sha256
        self._root = root
        self._root_sha256 = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        self._prompt = prompt
        self._model, self._provider = model, provider
        self._state = "created"
        self._failure_codes = []
        self._reservation_bound = False
        self._thread_intent = False
        self._turn_intent = False
        self._thread_id = None
        self._thread_candidate = None
        self._thread_response_verified = False
        self._thread_fingerprint = None
        self._thread_started_fingerprint = None
        self._thread_without_source_fingerprint = None
        self._turn_candidate = None
        self._turn_response_observed = False
        self._turn_started_fingerprint = None
        self._turn_completed_fingerprint = None
        self._new_turn_verified = False
        self._turn_status = None
        self._settings_updates = 0
        self._settings_network_sources = set()
        self._approval_observed = False
        self._approval_action = None
        self._approval_method = None
        self._approval_seen = set()
        self._interrupt_intent = False
        self._policy = {key: None for key in (
            "approval_policy", "approvals_reviewer", "sandbox_type", "network_access",
            "network_access_source", "model", "model_provider", "root_sha256")}

    def _freeze(self, code, *, unknown=False):
        # Unknown may become failed upon independently observed contradiction;
        # neither frozen state can ever become live again.
        if self._state != "failed":
            self._state = "unknown" if unknown else "failed"
        if code not in self._failure_codes:
            self._failure_codes.append(code)

    def _reject(self, code, *, unknown=False):
        self._freeze(code, unknown=unknown)
        raise ValueError(code)

    def _require_live(self):
        if self._state in ("failed", "unknown"):
            raise ValueError(self._failure_codes[0])

    def _finish_checks(self, evidence):
        if evidence.failures or evidence.missing:
            for code in evidence.missing:
                self._freeze(code, unknown=True)
            for code in evidence.failures:
                self._freeze(code)
            raise ValueError((evidence.failures or evidence.missing)[0])

    def _same_root(self, value):
        if type(value) is not str or not Path(value).is_absolute():
            return False
        try:
            return Path(value).resolve(strict=True) == self._root
        except (OSError, ValueError):
            return False

    def bind_reservation(self, reservation: dict) -> None:
        self._require_live()
        expected = {"schema_version": 1, "project": self._project, "attempt": 1,
                    "state": "reserved", "run_nonce": self._nonce,
                    "manifest_sha256": self._manifest_sha256}
        if (self._state != "created" or type(reservation) is not dict or reservation != expected
                or type(reservation.get("schema_version")) is not int
                or type(reservation.get("attempt")) is not int):
            self._reject("C1_APPSERVER_RESERVATION_INVALID")
        self._reservation_bound = True
        self._state = "attempt_reserved"

    def thread_start_params(self) -> dict:
        self._require_live()
        if self._state != "attempt_reserved" or not self._reservation_bound or self._thread_intent:
            self._reject("C1_APPSERVER_LIFECYCLE_INVALID")
        # Intent is consumed before the caller performs an ambiguous send.
        self._thread_intent = True
        self._state = "thread_start_sent"
        return {"cwd": str(self._root), "model": self._model, "modelProvider": self._provider,
                "approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "read-only",
                "ephemeral": True, "allowProviderModelFallback": False, "experimentalRawEvents": False}

    def _check_policy(self, obj, evidence, *, settings=False):
        evidence.field(obj, "approvalPolicy", lambda x: x == "on-request", "C1_APPSERVER_POLICY_MISMATCH")
        evidence.field(obj, "approvalsReviewer", lambda x: x == "user", "C1_APPSERVER_POLICY_MISMATCH")
        evidence.field(obj, "model", lambda x: x == self._model, "C1_APPSERVER_MODEL_MISMATCH")
        evidence.field(obj, "modelProvider", lambda x: x == self._provider, "C1_APPSERVER_MODEL_MISMATCH")
        evidence.field(obj, "cwd", self._same_root, "C1_APPSERVER_ROOT_MISMATCH")
        sandbox = evidence.field(obj, "sandboxPolicy" if settings else "sandbox", lambda x: type(x) is dict)
        network_source = None
        if type(sandbox) is dict:
            evidence.field(sandbox, "type", lambda x: x == "readOnly", "C1_APPSERVER_POLICY_MISMATCH")
            if set(sandbox) - {"type", "networkAccess"}:
                evidence.failures.append("C1_APPSERVER_POLICY_MISMATCH")
            if "networkAccess" in sandbox:
                if sandbox["networkAccess"] is not False:
                    evidence.failures.append("C1_APPSERVER_POLICY_MISMATCH")
                network_source = "explicit"
            else:
                # SandboxPolicy.ReadOnly: schema default false, not truthiness.
                network_source = "schema_default"
        if "activePermissionProfile" in obj and obj["activePermissionProfile"] is not None:
            profile = obj["activePermissionProfile"]
            if type(profile) is not dict:
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
            else:
                evidence.field(profile, "id", lambda x: type(x) is str)
                if ("extends" in profile and profile["extends"] is not None
                        and type(profile["extends"]) is not str):
                    evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
                # Pinned auxiliary profile schema permits additional fields;
                # none are merged into the fixed effective sandbox policy.
        self._check_optional_configuration(obj, evidence)
        if settings:
            self._check_optional_settings(obj, evidence)
            collaboration = evidence.field(obj, "collaborationMode", lambda x: type(x) is dict)
            if type(collaboration) is dict:
                evidence.field(collaboration, "mode", lambda x: x in ("default", "plan"))
                nested = evidence.field(collaboration, "settings", lambda x: type(x) is dict)
                if type(nested) is dict:
                    evidence.field(nested, "model", lambda x: x == self._model, "C1_APPSERVER_MODEL_MISMATCH")
                    if ("developer_instructions" in nested and nested["developer_instructions"] is not None
                            and type(nested["developer_instructions"]) is not str):
                        evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
                    if ("reasoning_effort" in nested and nested["reasoning_effort"] is not None
                            and not _text(nested["reasoning_effort"])):
                        evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        return network_source

    def _check_optional_configuration(self, obj, evidence):
        # Fields shared by pinned ThreadStartResponse and ThreadSettings. Do not
        # apply settings-only predicates to response auxiliary extension keys.
        if "disabledPluginIds" in obj:
            disabled = obj["disabledPluginIds"]
            if type(disabled) is not list or any(type(x) is not str for x in disabled):
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if "serviceTier" in obj and obj["serviceTier"] is not None and type(obj["serviceTier"]) is not str:
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if "multiAgentMode" in obj:
            mode = obj["multiAgentMode"]
            valid = (type(mode) is str and mode in ("explicitRequestOnly", "proactive"))
            if type(mode) is dict:
                # This particular union alternative has additionalProperties=false.
                valid = set(mode) == {"custom"} and type(mode["custom"]) is str
            if not valid:
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")

    def _check_optional_response(self, response, evidence):
        # Response-specific known fields: absence/defaults are not evidence gaps,
        # but present malformed values must veto before any turn intent.
        if ("reasoningEffort" in response and response["reasoningEffort"] is not None
                and not _text(response["reasoningEffort"])):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        for name in ("instructionSources", "runtimeWorkspaceRoots"):
            if name in response:
                paths = response[name]
                if type(paths) is not list or any(type(path) is not str for path in paths):
                    evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")

    def _check_optional_settings(self, settings, evidence):
        # Exact settings-only fields. Optional absence/defaults are legal;
        # present null is legal only where the pinned field is nullable.
        # Auxiliary extras are discarded, not interpreted as policy.
        if "effort" in settings and settings["effort"] is not None and not _text(settings["effort"]):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if ("personality" in settings and settings["personality"] is not None
                and settings["personality"] not in ("none", "friendly", "pragmatic")):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if ("summary" in settings and settings["summary"] is not None
                and settings["summary"] not in ("auto", "concise", "detailed", "none")):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")

    def _check_thread(self, thread, evidence):
        if type(thread) is not dict:
            if thread is not _MISSING:
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
            return None
        thread_id = evidence.field(thread, "id", _text, "C1_APPSERVER_THREAD_MISMATCH")
        evidence.field(thread, "cwd", self._same_root, "C1_APPSERVER_ROOT_MISMATCH")
        evidence.field(thread, "modelProvider", lambda x: x == self._provider, "C1_APPSERVER_MODEL_MISMATCH")
        if "model" in thread and thread["model"] is not None and thread["model"] != self._model:
            evidence.failures.append("C1_APPSERVER_MODEL_MISMATCH")
        evidence.field(thread, "ephemeral", lambda x: x is True, "C1_APPSERVER_THREAD_MISMATCH")
        evidence.field(thread, "turns", lambda x: type(x) is list and not x, "C1_APPSERVER_ACTIVE_TURN")
        status = evidence.field(thread, "status", lambda x: type(x) is dict)
        if type(status) is dict:
            evidence.field(status, "type", lambda x: x == "idle", "C1_APPSERVER_ACTIVE_TURN")
        for name in ("parentThreadId", "forkedFromId"):
            if thread.get(name) is not None:
                evidence.failures.append("C1_APPSERVER_THREAD_MISMATCH")
        for name in ("cliVersion", "sessionId", "preview"):
            evidence.field(thread, name, lambda x: type(x) is str)
        for name in ("createdAt", "updatedAt"):
            evidence.field(thread, name, _int64)
        evidence.field(thread, "projectId", lambda x: x is None or type(x) is str)
        source = evidence.field(thread, "source", lambda x: type(x) is str and x in {
            "cli", "vscode", "exec", "appServer", "unknown"})
        if source == "unknown":
            # Schema-valid is not verified origin evidence. Never infer the
            # missing origin from the request or another matching identifier.
            evidence.missing.append("C1_APPSERVER_SOURCE_UNKNOWN")
        if (_text(thread_id) and self._thread_candidate is not None
                and thread_id != self._thread_candidate):
            evidence.failures.append("C1_APPSERVER_THREAD_MISMATCH")
        return thread_id

    def _check_thread_fingerprints(self, thread, evidence):
        if type(thread) is not dict:
            return None, None
        fingerprint = _fingerprint(thread)
        without_source = _fingerprint({key: value for key, value in thread.items() if key != "source"})
        previous = self._thread_started_fingerprint or self._thread_fingerprint
        if previous is not None:
            # Unknown provenance exempts ONLY source from exact comparison.
            # Every other fact remains covered, including auxiliary identity
            # fields. No origin is substituted or inferred, and no body kept.
            conflict = (without_source != self._thread_without_source_fingerprint
                        if thread.get("source") == "unknown" else fingerprint != previous)
            if conflict:
                evidence.failures.append("C1_APPSERVER_THREAD_MISMATCH")
        return fingerprint, without_source

    def accept_thread_response(self, result: dict) -> None:
        evidence = _Evidence()
        if type(result) is not dict:
            self._reject("C1_APPSERVER_SCHEMA_INVALID")
        source = self._check_policy(result, evidence)
        self._check_optional_response(result, evidence)
        thread = evidence.field(result, "thread", lambda x: type(x) is dict)
        thread_id = self._check_thread(thread, evidence)
        fingerprint, without_source = self._check_thread_fingerprints(thread, evidence)
        self._finish_checks(evidence)
        self._require_live()
        if not self._thread_intent or self._thread_response_verified or self._turn_intent:
            self._reject("C1_APPSERVER_LIFECYCLE_INVALID")
        self._thread_candidate = self._thread_id = thread_id
        self._thread_fingerprint = fingerprint
        self._thread_without_source_fingerprint = without_source
        self._thread_response_verified = True
        self._policy = {"approval_policy": result["approvalPolicy"], "approvals_reviewer": result["approvalsReviewer"],
                        "sandbox_type": result["sandbox"]["type"], "network_access": False,
                        "network_access_source": source, "model": result["model"],
                        "model_provider": result["modelProvider"], "root_sha256": self._root_sha256}
        self._state = "thread_verified"

    def turn_start_params(self) -> dict:
        self._require_live()
        if (not self._thread_response_verified or self._state != "thread_verified"
                or self._turn_intent):
            self._reject("C1_APPSERVER_LIFECYCLE_INVALID")
        self._turn_intent = True
        self._state = "turn_sent"
        return {"threadId": self._thread_id, "input": [{"type": "text", "text": self._prompt}],
                "approvalPolicy": "on-request", "approvalsReviewer": "user",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False}}

    def _check_owner(self, params, evidence, *, turn=False):
        thread_id = evidence.field(params, "threadId", _text)
        known_thread = self._thread_id or self._thread_candidate
        if _text(thread_id):
            if known_thread is None:
                evidence.missing.append("C1_APPSERVER_OWNERSHIP_UNKNOWN")
            elif thread_id != known_thread:
                evidence.failures.append("C1_APPSERVER_THREAD_MISMATCH")
        if turn:
            turn_id = evidence.field(params, "turnId", _text)
            self._check_turn_id(turn_id, evidence)
            return turn_id
        return None

    def _check_turn_id(self, turn_id, evidence):
        if not self._turn_intent:
            evidence.failures.append("C1_APPSERVER_ACTIVE_TURN")
        if _text(turn_id) and self._turn_candidate is not None and turn_id != self._turn_candidate:
            evidence.failures.append("C1_APPSERVER_TURN_MISMATCH")

    def _check_turn(self, turn, evidence, *, completed=False):
        if type(turn) is not dict:
            if turn is not _MISSING:
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
            return None
        turn_id = evidence.field(turn, "id", _text)
        self._check_turn_id(turn_id, evidence)
        evidence.field(turn, "items", lambda x: type(x) is list)
        if not completed and turn.get("items"):
            evidence.failures.append("C1_APPSERVER_ACTIVE_TURN")
        status = evidence.field(turn, "status", lambda x: x in ("inProgress", "completed", "failed", "interrupted"))
        if status is not _MISSING and status != ("completed" if completed else "inProgress"):
            evidence.failures.append("C1_APPSERVER_TURN_FAILED")
        if turn.get("error") is not None:
            evidence.failures.append("C1_APPSERVER_TURN_FAILED")
        for name in ("startedAt", "completedAt", "durationMs"):
            if name in turn and turn[name] is not None and not _int64(turn[name]):
                evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if "rootTurnId" in turn and turn["rootTurnId"] is not None and turn["rootTurnId"] != turn_id:
            evidence.failures.append("C1_APPSERVER_TURN_MISMATCH")
        if "itemsView" in turn and turn["itemsView"] not in ("full", "notLoaded", "summary"):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        return turn_id

    def _advance_turn(self):
        self._new_turn_verified = bool(self._thread_response_verified and self._turn_response_observed
                                       and self._turn_started_fingerprint is not None)
        if self._new_turn_verified:
            self._state = "protocol_complete" if self._turn_completed_fingerprint is not None else "collecting"

    def _observe_thread_started(self, params, evidence):
        thread = evidence.field(params, "thread", lambda x: type(x) is dict)
        thread_id = self._check_thread(thread, evidence)
        fingerprint, without_source = self._check_thread_fingerprints(thread, evidence)
        if not self._thread_intent:
            evidence.failures.append("C1_APPSERVER_LIFECYCLE_INVALID")
        self._finish_checks(evidence)
        self._require_live()
        self._thread_candidate = thread_id
        self._thread_started_fingerprint = fingerprint
        self._thread_without_source_fingerprint = without_source

    def _observe_turn(self, params, evidence, *, completed):
        self._check_owner(params, evidence)
        turn = evidence.field(params, "turn", lambda x: type(x) is dict)
        turn_id = self._check_turn(turn, evidence, completed=completed)
        fingerprint = _fingerprint(turn) if type(turn) is dict else None
        previous = self._turn_completed_fingerprint if completed else self._turn_started_fingerprint
        if previous is not None and fingerprint != previous:
            evidence.failures.append("C1_APPSERVER_TURN_MISMATCH")
        if completed and self._turn_started_fingerprint is None:
            # RPC response interleaving is permitted, lifecycle reversal is not.
            # The first validated start must precede the first completion.
            evidence.failures.append("C1_APPSERVER_TURN_ORDER_INVALID")
        self._finish_checks(evidence)
        self._require_live()
        self._turn_candidate = turn_id
        if completed:
            self._turn_completed_fingerprint = fingerprint
            self._turn_status = "completed"
        else:
            self._turn_started_fingerprint = fingerprint
        self._advance_turn()

    def _check_status(self, params, evidence):
        status = evidence.field(params, "status", lambda x: type(x) is dict)
        if type(status) is not dict:
            return
        kind = evidence.field(status, "type", lambda x: x in ("idle", "active", "notLoaded", "systemError"))
        if kind in ("notLoaded", "systemError"):
            evidence.failures.append("C1_APPSERVER_THREAD_STATUS_INVALID")
        if kind == "active":
            flags = evidence.field(status, "activeFlags", lambda x: type(x) is list and all(
                type(y) is str and y in ("waitingOnApproval", "waitingOnUserInput") for y in x))
            if not self._turn_intent or (flags is not _MISSING and flags):
                evidence.failures.append("C1_APPSERVER_ACTIVE_TURN")

    def _check_usage(self, params, evidence):
        usage = evidence.field(params, "tokenUsage", lambda x: type(x) is dict)
        if type(usage) is not dict:
            return
        for name in ("last", "total"):
            breakdown = evidence.field(usage, name, lambda x: type(x) is dict)
            if type(breakdown) is dict:
                for field in ("cachedInputTokens", "inputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens"):
                    evidence.field(breakdown, field, _int64)
                if "cacheWriteInputTokens" in breakdown and not _int64(breakdown["cacheWriteInputTokens"]):
                    evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        if "modelContextWindow" in usage and usage["modelContextWindow"] is not None and not _int64(usage["modelContextWindow"]):
            evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")

    def _check_item(self, item, evidence):
        if type(item) is not dict:
            return
        evidence.field(item, "id", _text)
        kind = evidence.field(item, "type", lambda x: type(x) is str and x in (
            "userMessage", "agentMessage", "reasoning", "mcpToolCall"), "C1_APPSERVER_ITEM_NOT_ALLOWED")
        if kind == "agentMessage":
            evidence.field(item, "text", lambda x: type(x) is str)
        elif kind == "userMessage":
            evidence.field(item, "content", lambda x: type(x) is list)
        elif kind == "reasoning":
            for name in ("summary", "content"):
                if name in item and (type(item[name]) is not list or any(type(x) is not str for x in item[name])):
                    evidence.failures.append("C1_APPSERVER_SCHEMA_INVALID")
        elif kind == "mcpToolCall":
            # Lifecycle shape only. Task4 validates tool allowlist/order, SDK
            # DTOs, result/error semantics, item replay and audit correlation.
            evidence.field(item, "arguments", lambda x: True)
            for name in ("server", "tool"):
                evidence.field(item, name, _text)
            evidence.field(item, "status", lambda x: x in ("inProgress", "completed", "failed"))

    def observe(self, message: RpcMessage) -> None:
        if type(message) is not RpcMessage or type(message.payload) is not dict:
            self._reject("C1_APPSERVER_MESSAGE_INVALID")
        if message.kind == "server_request":
            self._reject("C1_APPSERVER_REQUEST_ROUTE_INVALID")
        if message.kind == "error" or (message.kind == "notification" and message.method == "error"):
            self._reject("C1_APPSERVER_REMOTE_ERROR")
        evidence = _Evidence()
        if message.kind == "response":
            # Peer/driver correlated this response; Session never guesses its ID.
            result = message.payload.get("result")
            if type(result) is not dict:
                self._reject("C1_APPSERVER_SCHEMA_INVALID")
            turn = evidence.field(result, "turn", lambda x: type(x) is dict)
            turn_id = self._check_turn(turn, evidence)
            self._finish_checks(evidence)
            self._require_live()
            if not self._turn_intent or self._turn_response_observed:
                self._reject("C1_APPSERVER_LIFECYCLE_INVALID")
            self._turn_candidate = turn_id
            self._turn_response_observed = True
            self._advance_turn()
            return
        if message.kind != "notification" or message.rpc_id is not None:
            self._reject("C1_APPSERVER_MESSAGE_INVALID")
        method = message.method
        known = {"thread/started", "turn/started", "turn/completed", "thread/settings/updated",
                 "thread/status/changed", "thread/tokenUsage/updated", "item/started", "item/completed",
                 "serverRequest/resolved", *_DELTAS}
        if method not in known:
            self._reject("C1_APPSERVER_NOTIFICATION_UNKNOWN", unknown=True)
        params = message.payload.get("params")
        if type(params) is not dict:
            self._reject("C1_APPSERVER_SCHEMA_INVALID")
        if method == "thread/started":
            self._observe_thread_started(params, evidence)
            return
        if method in ("turn/started", "turn/completed"):
            self._observe_turn(params, evidence, completed=method == "turn/completed")
            return
        if method == "thread/settings/updated":
            self._check_owner(params, evidence)
            settings = evidence.field(params, "threadSettings", lambda x: type(x) is dict)
            source = self._check_policy(settings, evidence, settings=True) if type(settings) is dict else None
            self._finish_checks(evidence)
            self._require_live()
            self._settings_updates += 1
            self._settings_network_sources.add(source)
            return
        has_turn = method not in ("thread/status/changed", "serverRequest/resolved")
        turn_id = self._check_owner(params, evidence, turn=has_turn)
        if method == "thread/status/changed":
            self._check_status(params, evidence)
        elif method == "thread/tokenUsage/updated":
            self._check_usage(params, evidence)
        elif method in _DELTAS:
            evidence.field(params, "itemId", _text)
            if method != "item/reasoning/summaryPartAdded":
                evidence.field(params, "delta", lambda x: type(x) is str)
            if _DELTAS[method] is not None:
                evidence.field(params, _DELTAS[method], _int64)
        elif method in ("item/started", "item/completed"):
            evidence.field(params, "startedAtMs" if method == "item/started" else "completedAtMs", _int64)
            item = evidence.field(params, "item", lambda x: type(x) is dict)
            self._check_item(item, evidence)
        elif method == "serverRequest/resolved":
            request_id = evidence.field(params, "requestId", _id)
            if _id(request_id):
                key = (type(request_id), request_id)
                if key not in self._approval_seen:
                    evidence.failures.append("C1_APPSERVER_SERVER_RESOLUTION_INVALID")
        self._finish_checks(evidence)
        self._require_live()
        if has_turn and self._turn_candidate is None:
            self._turn_candidate = turn_id
        # Status, usage, items and text never establish success or reopen state.

    def on_server_request(self, message: RpcMessage) -> str:
        if (type(message) is not RpcMessage or message.kind != "server_request"
                or type(message.payload) is not dict or not _id(message.rpc_id)
                or type(message.payload.get("id")) is not type(message.rpc_id)
                or message.payload.get("id") != message.rpc_id
                or type(message.method) is not str
                or message.payload.get("method") != message.method
                or "result" in message.payload or "error" in message.payload
                or ("jsonrpc" in message.payload and message.payload["jsonrpc"] != "2.0")):
            self._reject("C1_APPSERVER_APPROVAL_SCHEMA_INVALID")
        if message.method not in _APPROVAL_ACTIONS:
            self._freeze("C1_APPSERVER_INTERACTION_UNKNOWN", unknown=True)
            return "stop"
        if not is_valid_approval_request(message):
            self._reject("C1_APPSERVER_APPROVAL_SCHEMA_INVALID")
        params = message.payload["params"]
        # A matching turn/started can bind a cancellation target while the
        # turn/start response is still pending. It is NOT enough to interrupt
        # or claim a verified new turn; those also require the response.
        if (not self._thread_response_verified or self._turn_started_fingerprint is None
                or params["threadId"] != self._thread_id
                or params["turnId"] != self._turn_candidate or not _text(params["itemId"])):
            self._reject("C1_APPSERVER_APPROVAL_OWNERSHIP_INVALID")
        key = (type(message.rpc_id), message.rpc_id)
        if key in self._approval_seen:
            self._reject("C1_APPSERVER_APPROVAL_ID_REUSED")
        if self._approval_observed:
            self._reject("C1_APPSERVER_APPROVAL_ALREADY_HANDLED")
        # This first fully validated cancellation is a shutdown-only action,
        # not a return to live collection. It must also expose the independent
        # prohibited-approval failure after an earlier unknown/failure latch.
        self._approval_seen.add(key)
        self._approval_observed = True
        self._approval_method = message.method
        self._approval_action = _APPROVAL_ACTIONS[message.method]
        self._freeze("C1_APPSERVER_APPROVAL_REQUIRED")
        # Do not throw before the caller can send Peer.reply_cancel's fixed DTO.
        return self._approval_action

    def interrupt_params(self) -> dict | None:
        # No pending-client-ID check can be made here. The driver MUST also
        # require peer.pending_client_id is None; otherwise close/stop instead.
        if (self._state not in ("failed", "unknown") or not self._new_turn_verified
                or self._interrupt_intent):
            return None
        self._interrupt_intent = True
        return {"threadId": self._thread_id, "turnId": self._turn_candidate}

    def summary(self) -> dict:
        """Detached whitelist evidence; not business/shutdown/native acceptance."""
        return copy.deepcopy({
            "schema_version": 1, "state": self._state, "project": self._project,
            "run_nonce": self._nonce, "manifest_sha256": self._manifest_sha256,
            "root_sha256": self._root_sha256, "reservation_bound": self._reservation_bound,
            "thread_start_intent": self._thread_intent, "turn_start_intent": self._turn_intent,
            "thread_id": self._thread_id,
            "turn_id": self._turn_candidate if self._new_turn_verified else None,
            "thread_response_verified": self._thread_response_verified,
            "thread_started_observed": self._thread_started_fingerprint is not None,
            "turn_response_observed": self._turn_response_observed,
            "turn_started_observed": self._turn_started_fingerprint is not None,
            "new_turn_verified": self._new_turn_verified,
            "turn_completed_observed": self._turn_completed_fingerprint is not None,
            "turn_status": self._turn_status, "policy": self._policy,
            "settings_updates_verified": self._settings_updates,
            "settings_network_access_sources": sorted(self._settings_network_sources),
            "approval_observed": self._approval_observed, "approval_method": self._approval_method,
            "approval_action": self._approval_action, "interrupt_intent": self._interrupt_intent,
            "failure_codes": self._failure_codes,
        })
