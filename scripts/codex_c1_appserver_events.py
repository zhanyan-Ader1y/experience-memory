"""Bounded, independent app-server v2 decoding and composed C1-T proofs.

This module does not launch Codex or authenticate supplied DTOs. Only whitelisted
facts/hashes survive decoding; session, business and owned-process shutdown are
independent gates. Offline simulations never claim native evidence.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from scripts.codex_c1_appserver_rpc import RpcMessage, RpcProtocolError, classify_message
from scripts.codex_c1_bundle import ReviewSnapshot
from scripts.codex_c1_results import (
    NativeEvent, NativeRunState, SERVER, TOOLS, MAX_EXEC_LINE_BYTES,
    _EvidenceProblem, _Facts, _canonical, _digest, _identifier, _json, _sha, _scope,
    _bootstrap_summary, _status_summary, _error_summary, _argument_proofs,
    normalize_c1_body, validate_business_evidence,
)


TRANSPORT_KIND = "app_server_stdio_0.163.0"
BUSINESS_CHECKS = {"scope_bound", "five_unique_calls", "transport_linked", "business_verified", "id_mapping"}
SESSION_FIELDS = {
    "schema_version", "state", "project", "run_nonce", "manifest_sha256", "root_sha256",
    "reservation_bound", "thread_start_intent", "turn_start_intent", "thread_id", "turn_id",
    "thread_response_verified", "thread_started_observed", "turn_response_observed", "turn_started_observed",
    "new_turn_verified", "turn_completed_observed", "turn_status", "policy", "settings_updates_verified",
    "settings_network_access_sources", "approval_observed", "approval_method", "approval_action",
    "interrupt_intent", "failure_codes",
}
POLICY_FIELDS = {"approval_policy", "approvals_reviewer", "sandbox_type", "network_access",
                 "network_access_source", "model", "model_provider", "root_sha256"}
EVENT_FIELDS = {
    "schema_version", "transport_kind", "run_nonce", "project", "manifest_sha256", "root_sha256",
    "thread_id", "native_turn_id", "thread_bound", "turn_bound", "turn_started", "turn_completed",
    "unique_calls", "completed_calls", "item_ids", "order_verified", "replay_detected",
    "failure_reasons", "unknown_reasons", "discarded_items", "call_items", "completion", "last_agent_item_id",
}
MAX_DISCARDED_ITEMS = 16
MAX_COMPLETION_ITEMS = 5 + MAX_DISCARDED_ITEMS
_ITEM_PROOF_FIELDS = {"item_id", "kind", "tool", "arguments_sha256", "terminal_status", "outcome_state",
                      "result_sha256", "fingerprint", "content_sha256"}
_CALL_PROOF_FIELDS = {"item_id", "tool", "arguments_sha256", "started_sha256", "completed_sha256",
                      "terminal_status", "result_sha256"}
_FORBIDDEN_ITEMS = frozenset({"hookPrompt", "functionCallOutput", "plan", "commandExecution", "fileChange",
                              "dynamicToolCall", "collabAgentToolCall", "subAgentActivity", "webSearch",
                              "imageView", "sleep", "imageGeneration", "enteredReviewMode", "exitedReviewMode",
                              "contextCompaction"})
_NONBUSINESS = frozenset({"userMessage", "agentMessage", "reasoning"})
_APPROVAL_METHODS = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                     "item/permissions/requestApproval"}


_RPC_UNKNOWN = frozenset({
    "C1_RPC_UNEXPECTED_EOF", "C1_RPC_READ_FAILED", "C1_RPC_WRITE_FAILED", "C1_RPC_PEER_FAILED",
    "C1_RPC_NOTIFICATION_NOT_ALLOWED", "C1_RPC_SERVER_REQUEST_NOT_ALLOWED",
})
_RPC_FAILED = frozenset({
    "C1_RPC_REMOTE_ERROR", "C1_RPC_FRAME_INVALID", "C1_RPC_JSON_INVALID", "C1_RPC_MESSAGE_INVALID",
    "C1_RPC_VERSION_INVALID", "C1_RPC_ID_INVALID", "C1_RPC_METHOD_INVALID", "C1_RPC_ERROR_INVALID",
    "C1_RPC_PROCESS_INVALID", "C1_RPC_OUTBOUND_INVALID", "C1_RPC_FRAME_LIMIT", "C1_RPC_METHOD_NOT_ALLOWED",
    "C1_RPC_PARAMS_INVALID", "C1_RPC_CLIENT_REQUEST_PENDING", "C1_RPC_NOTIFICATION_DUPLICATE",
    "C1_RPC_SERVER_RESOLUTION_INVALID", "C1_RPC_RESPONSE_ID_MISMATCH", "C1_RPC_SERVER_ID_REUSED",
    "C1_RPC_CANCEL_REQUEST_INVALID", "C1_RPC_CANCEL_REQUEST_UNKNOWN", "C1_RPC_CANCEL_SCHEMA_INVALID",
})


@dataclass(frozen=True)
class AppServerEvent:
    thread_id: str
    turn_id: str | None
    kind: str
    item: NativeEvent | None
    completion: dict | None = None


class AppServerEvidenceError(_EvidenceProblem):
    """Static body-free decoder/binding error with failed/unknown severity."""


def _finish(facts):
    try:
        return facts.finish()
    except _EvidenceProblem as error:
        raise AppServerEvidenceError(error.state, error.reason) from None


def _object_field(facts, parent, key, fields, reason):
    if key not in parent:
        facts.missing("missing_" + reason)
        return {}
    return facts.object(parent[key], fields, reason)


def _int64(value):
    return type(value) is int and -(2**63) <= value < 2**63


def _root_hash(snapshot, project):
    root = Path(snapshot.manifest["projects"][project]["root"]).resolve(strict=True)
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _body_proof(tool, body):
    # When the tool field itself is absent, inspect an independently recognizable
    # business body for contradictions only; the outer decoder still rejects
    # the missing tool and never constructs an admitted call from this choice.
    if tool not in TOOLS and type(body) is dict and body.get("status") == "ready":
        return normalize_c1_body("c1_bootstrap", body)
    return normalize_c1_body(tool, body)


def _result(item):
    """Read the actual v2 channels. Absent structuredContent is schema-permitted."""
    facts = _Facts()
    tool, terminal = item.get("tool"), item.get("status")
    if tool == "c1_bootstrap" and terminal is not None:
        facts.known(terminal == "completed", "unexpected_bootstrap_terminal")
    facts.known(item.get("error") is None, "appserver_tool_error")
    result = item.get("result")
    body, parsed, outcome, body_digest = None, False, None, None
    if result is None:
        facts.missing("missing_appserver_result")
        result = {}
    else:
        result = facts.object(result, {"content"}, "appserver_result_fields")
    if "content" in result:
        content = result["content"]
        shape = (type(content) is list and len(content) == 1 and type(content[0]) is dict
                 and set(content[0]) == {"type", "text"} and content[0].get("type") == "text"
                 and type(content[0].get("text")) is str)
        facts.known(shape, "fixture_content_shape")
        if shape:
            try:
                body = _json(content[0]["text"])
                _canonical(body)
                parsed = True
            except (ValueError, TypeError, RecursionError, OverflowError):
                facts.missing("unparseable_appserver_result")
    structured = result.get("structuredContent")
    if structured is not None:
        normalized = facts.capture(lambda: _body_proof(tool, structured))
        if normalized is not None:
            # Comparable negative evidence is a real normalized business BODY,
            # never the partial envelope. Missing text still vetoes verification.
            body_digest = _digest(structured)
        if parsed:
            facts.capture(lambda: _same_channels(structured, body))
        elif normalized is not None:
            # Content is required by the pinned result schema. A structured-only
            # object can contradict proof but cannot fill the missing text.
            facts.missing("missing_appserver_text_result")
    if parsed:
        outcome = facts.capture(lambda: _body_proof(tool, body))
        if outcome is not None:
            body_digest = _digest(body)
    for observed in ([body] if parsed else []) + ([structured] if structured is not None else []):
        if type(observed) is dict and terminal in ("completed", "failed"):
            if observed.get("status") == "failed" or "error_code" in observed:
                facts.known(terminal == "failed", "appserver_result_status_mismatch")
            elif observed.get("status") == "ready" or observed.get("succeeded") is True:
                facts.known(terminal == "completed", "appserver_result_status_mismatch")
    try:
        facts.finish()
    except _EvidenceProblem as error:
        return None, error.state, error.reason, body_digest
    return outcome, "valid", None, body_digest


def _same_channels(structured, body):
    if _canonical(structured) != _canonical(body):
        raise _EvidenceProblem("failed", "contradictory_result_channels")


def _user_text(facts, item):
    """The fixed C1 turn supports one text input, not multimodal/user steering."""
    facts.object(item, {"id", "type", "content"}, "appserver_user_item")
    facts.observed(item, {"clientId": lambda v: v is None or type(v) is str}, "appserver_user_metadata")
    if "content" not in item:
        return None
    content = item["content"]
    facts.known(type(content) is list and len(content) == 1, "appserver_user_content_shape")
    if type(content) is not list or len(content) != 1:
        return None
    block = facts.object(content[0], {"type"}, "appserver_user_text")
    kind = block.get("type")
    if "type" in block and kind != "text":
        if kind in ("image", "localImage", "inputAudio", "skill", "mention"):
            facts.known(False, "appserver_user_content_unsupported")
        else:
            facts.missing("appserver_user_content_unknown")
    if "type" in block:
        facts.known(type(kind) is str, "appserver_user_type_invalid")
    facts.object(block, {"text"}, "appserver_user_text_fields")
    facts.observed(block, {"text": lambda v: type(v) is str,
                           "text_elements": lambda v: type(v) is list}, "appserver_user_text_schema")
    text = block.get("text")
    if type(block.get("text_elements")) is list:
        for value in block["text_elements"]:
            element = facts.object(value, {"byteRange"}, "appserver_text_element")
            facts.observed(element, {"placeholder": lambda v: v is None or type(v) is str}, "appserver_text_element_schema")
            span = _object_field(facts, element, "byteRange", {"start", "end"}, "appserver_text_range")
            facts.observed(span, {"start": lambda v: type(v) is int and 0 <= v < 2**64,
                                  "end": lambda v: type(v) is int and 0 <= v < 2**64}, "appserver_text_range_schema")
            start, end = span.get("start"), span.get("end")
            if type(start) is int and type(end) is int:
                facts.known(start <= end, "appserver_text_range_order")
                if type(text) is str:
                    facts.known(end <= len(text.encode("utf-8")), "appserver_text_range_bounds")
    return _digest(text) if type(text) is str else None


def _mcp_optional(facts, item):
    if item.get("appContext") is not None:
        context = facts.object(item["appContext"], {"connectorId"}, "appserver_app_context")
        facts.observed(context, {"connectorId": lambda v: type(v) is str,
                                 **{key: lambda v: v is None or type(v) is str
                                    for key in ("actionName", "appName", "linkId", "resourceUri")}},
                       "appserver_app_context_schema")
    if item.get("mcpAppUi") is not None:
        ui = facts.object(item["mcpAppUi"], {"preferredModelDisplayMode", "resourceUri"}, "appserver_mcp_app_ui")
        facts.observed(ui, {"resourceUri": lambda v: type(v) is str,
                            "preferredModelDisplayMode": lambda v: v in ("inline", "fullscreen")},
                       "appserver_mcp_app_ui_schema")
    if item.get("error") is not None:
        error = facts.object(item["error"], {"message"}, "appserver_mcp_error")
        facts.observed(error, {"message": lambda v: type(v) is str}, "appserver_mcp_error_schema")
        facts.known(False, "appserver_tool_error")


def _agent_optional(facts, item):
    facts.observed(item, {"phase": lambda v: v is None or v in ("commentary", "partial_answer", "final_answer"),
                           "delivery": lambda v: v is None or v == "async"}, "appserver_agent_metadata_schema")
    questions = item.get("questions")
    if questions is not None:
        facts.known(type(questions) is list, "appserver_agent_questions_schema")
        for value in questions if type(questions) is list else []:
            question = facts.object(value, {"title"}, "appserver_agent_question", exact=True, optional={"options"})
            facts.observed(question, {"title": lambda v: type(v) is str,
                                      "options": lambda v: v is None or type(v) is list and
                                          all(type(option) is str for option in v)}, "appserver_agent_question_schema")
    citation = item.get("memoryCitation")
    if citation is not None:
        citation = facts.object(citation, {"entries", "threadIds"}, "appserver_memory_citation")
        facts.observed(citation, {"entries": lambda v: type(v) is list,
                                  "threadIds": lambda v: type(v) is list and all(type(x) is str for x in v)},
                       "appserver_memory_citation_schema")
        for value in citation.get("entries", []) if type(citation.get("entries")) is list else []:
            entry = facts.object(value, {"lineStart", "lineEnd", "note", "path"}, "appserver_memory_citation_entry")
            facts.observed(entry, {"lineStart": lambda v: type(v) is int and 0 <= v < 2**32,
                                   "lineEnd": lambda v: type(v) is int and 0 <= v < 2**32,
                                   "note": lambda v: type(v) is str,
                                   "path": lambda v: type(v) is str}, "appserver_memory_citation_entry_schema")


def _inspect_item(parent, value, *, completed):
    """One shared pinned ThreadItem inspector, without inventing lifecycle events.

    Used for actual live notifications and embedded turn observations. The latter
    remain metadata only and are NEVER admitted as native calls/completions.
    """
    facts = _Facts()
    item = facts.object(value, {"id", "type"}, "appserver_item")
    facts.observed(item, {"id": _identifier, "type": lambda v: type(v) is str}, "appserver_item_identity")
    kind = item.get("type")
    data = {"item_id": item.get("id"), "kind": kind, "tool": None, "arguments_sha256": None,
            "terminal_status": None, "outcome_state": None, "result_sha256": None,
            "fingerprint": _digest(item), "content_sha256": None}
    outcome, reason = None, None
    if kind == "mcpToolCall":
        facts.object(item, {"arguments", "server", "tool", "status"}, "appserver_mcp_fields")
        facts.observed(item, {"arguments": lambda v: type(v) is dict, "server": _identifier,
                              "tool": _identifier, "status": lambda v: type(v) is str,
                              "durationMs": lambda v: v is None or _int64(v),
                              "readOnlyHint": lambda v: v is None or type(v) is bool,
                              "pluginId": lambda v: v is None or type(v) is str,
                              "mcpAppResourceUri": lambda v: v is None or type(v) is str}, "appserver_mcp_schema")
        _mcp_optional(facts, item)
        status = item.get("status")
        if type(status) is str:
            if status not in ("inProgress", "completed", "failed"):
                facts.missing("appserver_mcp_status_unknown")
            else:
                facts.known((status != "inProgress") == completed, "appserver_mcp_phase")
        if not completed:
            facts.known(item.get("result") is None and item.get("error") is None, "appserver_mcp_start_result")
        outcome, state, reason, digest = _result(item) if completed else (None, None, None, None)
        if facts.problems or parent.problems:
            facts.observed(item, {"server": lambda v: v == SERVER, "tool": lambda v: v in TOOLS},
                           "appserver_unexpected_tool")
            if state == "failed":
                facts.known(False, "appserver_observed_result_failure")
        data.update(tool=item.get("tool"), terminal_status=status if completed else "in_progress",
                    arguments_sha256=_digest(item["arguments"]) if type(item.get("arguments")) is dict else None,
                    outcome_state=state, result_sha256=digest)
        event_kind = "call_finished" if completed else "call_started"
        if ("server" in item and item["server"] != SERVER) or ("tool" in item and item["tool"] not in TOOLS):
            event_kind = "unexpected_tool"
    elif type(kind) is str and kind in _NONBUSINESS:
        if kind == "userMessage":
            data["content_sha256"] = _user_text(facts, item)
        elif kind == "agentMessage":
            facts.object(item, {"text"}, "appserver_text_item")
            facts.observed(item, {"text": lambda v: type(v) is str}, "appserver_text_schema")
            _agent_optional(facts, item)
        else:
            facts.observed(item, {"summary": lambda v: type(v) is list and all(type(x) is str for x in v),
                                  "content": lambda v: type(v) is list and all(type(x) is str for x in v)}, "appserver_text_schema")
        data["terminal_status"] = "completed" if completed else "in_progress"
        event_kind = "ignored_known"
    elif type(kind) is str and kind in _FORBIDDEN_ITEMS:
        if facts.problems or parent.problems:
            facts.known(False, "appserver_forbidden_item")
        event_kind = "unexpected_tool"
    else:
        facts.missing("appserver_item_type_unknown")
        event_kind = "unsupported"
    if facts.problems or (completed and kind == "mcpToolCall" and data["outcome_state"] != "valid"):
        # A partial item/result is not an exact canonical duplicate claim. Compare its
        # independently available fields, never a hash difference caused solely
        # by a missing field. Fully inspected items still retain exact hashes.
        data["fingerprint"] = None
    parent.problems.update(facts.problems)
    return data, event_kind, outcome, reason


def _completion_metadata(facts, turn):
    view = turn.get("itemsView", "full")
    if type(view) is not str:
        facts.known(False, "appserver_completion_view_schema")
    elif view not in ("notLoaded", "summary", "full"):
        facts.missing("appserver_completion_view_unknown")
    items = turn.get("items")
    metadata, ids = [], []
    if type(items) is list:
        facts.known(len(items) <= MAX_COMPLETION_ITEMS, "appserver_completion_item_budget")
        if view == "notLoaded":
            facts.known(not items, "appserver_not_loaded_items_conflict")
        if view == "summary":
            if not items:
                facts.missing("missing_appserver_summary_item")
            facts.known(len(items) <= 1, "appserver_summary_item_count")
        for item in items:
            data, kind, _, _ = _inspect_item(facts, item, completed=True)
            if kind == "unexpected_tool" or data["outcome_state"] == "failed":
                facts.known(False, "appserver_completion_observed_failure")
            if (view == "summary" and type(data["kind"]) is str
                    and data["kind"] in ({"mcpToolCall"} | _NONBUSINESS | _FORBIDDEN_ITEMS)):
                facts.known(data["kind"] == "agentMessage", "appserver_summary_item_kind")
            ids.append(data["item_id"])
            if (len(metadata) < MAX_COMPLETION_ITEMS and type(data["kind"]) is str
                    and data["kind"] in ({"mcpToolCall"} | _NONBUSINESS)):
                metadata.append(data)
        facts.distinct(ids, "appserver_completion_item_replay")
    complete_items = all(row["fingerprint"] is not None for row in metadata)
    return {"items_view": view, "items": metadata,
            "fingerprint": _digest(turn) if complete_items else None}


def decode_appserver_notification(message: RpcMessage) -> AppServerEvent:
    """Decode reviewed lifecycle/MCP notifications, not exec JSON with renamed keys.

    Full RPC envelopes are rechecked through the authoritative classifier. Missing
    required proof raises AppServerEvidenceError(state='unknown'); contradictory
    observed facts raise failed. Result/body problems become NativeEvent outcome
    facts so independent present contradictions survive missing evidence.
    """
    try:
        if not isinstance(message, RpcMessage) or type(message.payload) is not dict:
            raise AppServerEvidenceError("failed", "appserver_envelope_invalid")
        raw = _canonical(message.payload) + b"\n"
        if len(raw) > MAX_EXEC_LINE_BYTES:
            raise AppServerEvidenceError("failed", "appserver_envelope_limit")
        checked = classify_message(raw)
        if (checked.kind, checked.method, checked.rpc_id) != (message.kind, message.method, message.rpc_id):
            raise AppServerEvidenceError("failed", "appserver_envelope_mismatch")
        if checked.kind == "error":
            raise AppServerEvidenceError("failed", "appserver_remote_error")
        if checked.kind != "notification":
            raise AppServerEvidenceError("failed" if checked.method in _APPROVAL_METHODS else "unknown",
                                         "appserver_not_tool_notification")
        method = checked.method
        if method == "error":
            raise AppServerEvidenceError("failed", "appserver_remote_error")
        if method not in {"item/started", "item/completed", "turn/started", "turn/completed", "thread/started"}:
            raise AppServerEvidenceError("unknown", "appserver_notification_not_business")
        facts = _Facts()
        params = _object_field(facts, checked.payload, "params", set(), "appserver_params")
        if method == "thread/started":
            thread = _object_field(facts, params, "thread", {"id"}, "appserver_thread")
            facts.observed(thread, {"id": _identifier}, "appserver_thread_identity")
            _finish(facts)
            return AppServerEvent(thread["id"], None, "thread_started", None)
        params = facts.object(params, {"threadId", "turn"} if method.startswith("turn/") else
                              {"threadId", "turnId", "item", "startedAtMs" if method == "item/started" else "completedAtMs"},
                              "appserver_notification_fields")
        facts.observed(params, {"threadId": _identifier, "turnId": _identifier,
                               "startedAtMs": _int64, "completedAtMs": _int64}, "appserver_notification_identity")
        if method.startswith("turn/"):
            turn = _object_field(facts, params, "turn", {"id", "status", "items"}, "appserver_turn")
            facts.observed(turn, {"id": _identifier, "items": lambda v: type(v) is list,
                                  "status": lambda v: type(v) is str}, "appserver_turn_schema")
            status = turn.get("status")
            if type(status) is str:
                if status not in ("inProgress", "completed", "failed", "interrupted"):
                    facts.missing("appserver_turn_status_unknown")
                elif method == "turn/started":
                    facts.known(status == "inProgress" and not turn.get("items"), "appserver_turn_start_invalid")
                else:
                    facts.known(status != "inProgress", "appserver_turn_terminal_invalid")
            if turn.get("error") is not None:
                facts.known(False, "appserver_turn_failed")
            completion = _completion_metadata(facts, turn) if method == "turn/completed" else None
            _finish(facts)
            kind = ("turn_started" if method == "turn/started" else
                    "turn_completed" if status == "completed" else "turn_failed")
            return AppServerEvent(params["threadId"], turn["id"], kind, None, completion)
        item = _object_field(facts, params, "item", {"id", "type"}, "appserver_item")
        data, event_kind, outcome, reason = _inspect_item(facts, item, completed=method == "item/completed")
        _finish(facts)
        if event_kind == "unexpected_tool":
            return AppServerEvent(params["threadId"], params["turnId"], event_kind, None)
        native = NativeEvent(event_kind, item_id=data["item_id"], thread_id=params["threadId"],
                             tool=data["tool"] if data["kind"] == "mcpToolCall" else data["kind"],
                             arguments_sha256=data["arguments_sha256"],
                             terminal_status=data["terminal_status"] if method == "item/completed" or
                                 event_kind == "ignored_known" else None,
                             outcome=outcome, outcome_state=data["outcome_state"], outcome_reason=reason,
                             result_sha256=data["result_sha256"] if data["kind"] == "mcpToolCall" else data["content_sha256"],
                             fingerprint=data["fingerprint"])
        return AppServerEvent(params["threadId"], params["turnId"], event_kind, native)
    except AppServerEvidenceError:
        raise
    except (RpcProtocolError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, UnicodeError):
        raise AppServerEvidenceError("failed", "appserver_notification_invalid") from None


def _check_completion(facts, completion, call_items, discarded_items, last_agent, *, nonce, prompt_sha256):
    """Reconcile metadata only; this function cannot create live observations."""
    if completion is None:
        facts.missing("missing_appserver_completion_metadata")
        return
    completion = facts.object(completion, {"items_view", "items", "fingerprint"}, "appserver_completion", exact=True)
    facts.observed(completion, {"fingerprint": lambda v: v is None or _sha(v),
                                "items_view": lambda v: type(v) is str,
                                "items": lambda v: type(v) is list}, "appserver_completion_schema")
    if type(completion.get("items_view")) is str and completion["items_view"] not in ("notLoaded", "summary", "full"):
        facts.missing("appserver_completion_view_unknown")
    if completion.get("fingerprint") is None:
        facts.missing("missing_appserver_completion_hash")
    view, items = completion.get("items_view"), completion.get("items")
    calls = {row["item_id"]: row for row in call_items if type(row) is dict and _identifier(row.get("item_id"))}
    discarded = {row["item_id"]: row for row in discarded_items if type(row) is dict and _identifier(row.get("item_id"))}
    if type(items) is not list:
        return
    facts.known(len(items) <= MAX_COMPLETION_ITEMS, "appserver_completion_item_budget")
    if view == "notLoaded":
        facts.known(not items, "appserver_not_loaded_items_conflict")
    if view == "summary":
        facts.known(len(items) <= 1, "appserver_summary_item_count")
        if not items:
            facts.missing("missing_appserver_summary_item")
    ids, mcp_ids, text_ids, user_ids = [], set(calls), set(discarded), {
        key for key, row in discarded.items() if row.get("kind") == "userMessage"}
    steps = {row.get("item_id"): step for step, row in enumerate(call_items, 1) if type(row) is dict
             and _identifier(row.get("item_id"))}
    for value in items[:MAX_COMPLETION_ITEMS]:
        row = facts.object(value, _ITEM_PROOF_FIELDS, "appserver_completion_item", exact=True)
        facts.observed(row, {"item_id": lambda v: v is None or _identifier(v),
                             "kind": lambda v: type(v) is str and v in ({"mcpToolCall"} | _NONBUSINESS),
                             "fingerprint": lambda v: v is None or _sha(v)}, "appserver_completion_item_schema")
        item_id, kind = row.get("item_id"), row.get("kind")
        if item_id is None:
            facts.missing("missing_appserver_completion_item_identity")
        ids.append(item_id)
        if row.get("fingerprint") is None:
            facts.missing("missing_appserver_completion_item_hash")
        if view == "summary" and kind is not None:
            facts.known(kind == "agentMessage", "appserver_summary_item_kind")
        if kind == "mcpToolCall":
            if _identifier(item_id):
                mcp_ids.add(item_id)
            facts.observed(row, {"tool": lambda v: v is None or v in TOOLS,
                                 "arguments_sha256": lambda v: v is None or _sha(v),
                                 "terminal_status": lambda v: v is None or v in ("completed", "failed"),
                                 "outcome_state": lambda v: v in (None, "valid", "unknown", "failed"),
                                 "result_sha256": lambda v: v is None or _sha(v),
                                 "content_sha256": lambda v: v is None}, "appserver_completion_mcp_schema")
            if row.get("outcome_state") == "failed":
                facts.known(False, "appserver_completion_result_failed")
            elif row.get("outcome_state") != "valid":
                facts.missing("missing_appserver_completion_result")
            if any(row.get(key) is None for key in ("tool", "arguments_sha256", "terminal_status", "result_sha256")):
                facts.missing("missing_appserver_completion_mcp_proof")
            if row.get("tool") == "c1_bootstrap" and row.get("arguments_sha256") is not None:
                facts.known(row["arguments_sha256"] == _digest({"probe_nonce": nonce}), "appserver_completion_nonce_mismatch")
            if _identifier(item_id) and item_id in discarded:
                facts.known(False, "appserver_completion_kind_collision")
            prior = calls.get(item_id) if _identifier(item_id) else None
            if prior is None:
                facts.missing("missing_appserver_completion_live_call")
            else:
                for left, right in (("tool", "tool"), ("tool", "_completed_tool"),
                                    ("arguments_sha256", "arguments_sha256"),
                                    ("arguments_sha256", "_completed_arguments_sha256"),
                                    ("terminal_status", "terminal_status"), ("result_sha256", "result_sha256"),
                                    ("fingerprint", "completed_sha256")):
                    if row.get(left) is not None and prior.get(right) is not None:
                        facts.equal(row, left, prior, right, "appserver_completion_call_conflict")
                if prior.get("completed_sha256") is None:
                    facts.missing("missing_appserver_completion_live_finish")
                step = steps[item_id]
                if row.get("terminal_status") is not None:
                    facts.known(row["terminal_status"] == ("failed" if step in (3, 4) else "completed"),
                                "appserver_completion_terminal_conflict")
        elif type(kind) is str and kind in _NONBUSINESS:
            if _identifier(item_id):
                text_ids.add(item_id)
                if kind == "userMessage":
                    user_ids.add(item_id)
            facts.observed(row, {"tool": lambda v: v is None, "arguments_sha256": lambda v: v is None,
                                 "terminal_status": lambda v: v == "completed", "outcome_state": lambda v: v is None,
                                 "result_sha256": lambda v: v is None,
                                 "content_sha256": lambda v: v == prompt_sha256 if kind == "userMessage" else v is None},
                           "appserver_completion_text_schema")
            if _identifier(item_id) and item_id in calls:
                facts.known(False, "appserver_completion_kind_collision")
            prior = discarded.get(item_id) if _identifier(item_id) else None
            if prior is None:
                facts.missing("missing_appserver_completion_live_text")
            else:
                facts.observed(prior, {"kind": lambda v: v == kind}, "appserver_completion_kind_collision")
                if prior.get("completed_sha256") is None:
                    facts.missing("missing_appserver_completion_live_text_finish")
                elif row.get("fingerprint") is not None:
                    facts.equal(row, "fingerprint", prior, "completed_sha256", "appserver_completion_text_conflict")
            if view == "summary":
                if last_agent is None or prior is None:
                    facts.missing("missing_appserver_last_agent_association")
                else:
                    facts.known(item_id == last_agent, "appserver_last_agent_conflict")
    facts.distinct(ids, "appserver_completion_item_replay")
    facts.known(len(mcp_ids) <= 5, "appserver_completion_extra_call")
    facts.known(len(text_ids) <= MAX_DISCARDED_ITEMS, "appserver_completion_extra_text")
    facts.known(len(user_ids) <= 1, "appserver_completion_extra_user_origin")
    if view == "full":
        # Full describes AVAILABLE persisted history. Missing live coverage is
        # uncertainty (ephemeral/transient history), never invented success/failure.
        if (set(calls) | set(discarded)) - {item_id for item_id in ids if _identifier(item_id)}:
            facts.missing("missing_appserver_full_coverage")


class AppServerRunState:
    """Strict one-session v2 owner; seed only from Session's VERIFIED returned IDs.

    Observe the validated turn/started before items (buffer pre-response messages
    only within the existing transport bounds). Session remains the lifecycle and
    policy owner. No model/request arguments may supply this constructor's IDs.
    """
    def __init__(self, snapshot: ReviewSnapshot, project: str, *, session: dict):
        facts = _Facts()
        if not isinstance(snapshot, ReviewSnapshot) or project not in ("a", "b"):
            raise AppServerEvidenceError("failed", "appserver_scope_invalid")
        self._snapshot, self._project = snapshot, project
        self._root_sha256 = _root_hash(snapshot, project)
        session = facts.object(session, {"schema_version", "run_nonce", "manifest_sha256", "project", "root_sha256",
                                         "thread_id", "turn_id", "thread_response_verified", "new_turn_verified", "state"},
                               "appserver_session_binding")
        facts.observed(session, {"schema_version": lambda v: type(v) is int and v == 1,
                                "run_nonce": lambda v: v == snapshot.run_nonce,
                                "manifest_sha256": lambda v: v == snapshot.manifest_sha256,
                                "project": lambda v: v == project, "root_sha256": lambda v: v == self._root_sha256,
                                "thread_id": _identifier, "turn_id": _identifier,
                                "thread_response_verified": lambda v: v is True,
                                "new_turn_verified": lambda v: v is True,
                                "state": lambda v: v in ("collecting", "protocol_complete")}, "appserver_session_binding")
        _finish(facts)
        self._thread, self._turn = session["thread_id"], session["turn_id"]
        self._native = NativeRunState(snapshot.run_nonce, project)
        self._native.consume(NativeEvent("thread_started", thread_id=self._thread))
        self._started, self._completed = False, False
        self._failures, self._unknowns = set(), set()
        self._replay, self._order = False, True
        self._discarded = {}
        self._calls = {}
        # Nonpositive receipts let frozen runs inspect later contradictions
        # without inventing admitted calls, counts or lifecycle observations.
        self._seen_calls, self._seen_discarded = {}, {}
        self._last_agent_item_id = None
        self._seen_last_agent = None
        self._seen_turn_started = self._seen_turn_completed = False
        self._seen_completion_sha256 = None
        self._completion = None
        self._prompt_sha256 = _digest(snapshot.manifest["projects"][project]["prompt"])

    def observe(self, message: RpcMessage) -> None:
        """Decode then consume; latch static failure/unknown facts without raw data."""
        try:
            event = decode_appserver_notification(message)
            self._observe_business_scope(message)
            self.consume(event)
        except AppServerEvidenceError as error:
            (self._failures if error.state == "failed" else self._unknowns).add(error.reason)
            self._observe_present_conflicts(message)

    def _observe_business_scope(self, message):
        """Check present raw-channel scope only; retain no body, handle or digest.

        Full normalization may stop at an unrelated missing field. That cannot
        erase a session/project contradiction against this verified binding.
        Absent facts contribute nothing and never complete or admit proof.
        """
        if not isinstance(message, RpcMessage) or type(message.payload) is not dict:
            return
        params = message.payload.get("params")
        if type(params) is not dict:
            return
        if message.method == "item/completed":
            items = [params.get("item")]
        elif message.method == "turn/completed" and type(params.get("turn")) is dict:
            items = params["turn"].get("items")
            if type(items) is not list:
                return
            items = items[:MAX_COMPLETION_ITEMS]
        else:
            return
        project_key = "local:" + self._snapshot.run_nonce + "-" + self._project
        foreign_key = "local:" + self._snapshot.run_nonce + "-" + ("b" if self._project == "a" else "a")
        facts = _Facts()
        for item in items:
            if type(item) is not dict or item.get("type") != "mcpToolCall":
                continue
            result = item.get("result")
            if type(result) is not dict:
                continue
            bodies = [result.get("structuredContent")]
            content = result.get("content")
            if (type(content) is list and len(content) == 1 and type(content[0]) is dict
                    and content[0].get("type") == "text" and type(content[0].get("text")) is str):
                try:
                    bodies.append(_json(content[0]["text"]))
                except (ValueError, TypeError, RecursionError, OverflowError):
                    pass  # The decoder owns malformed/missing text classification.
            for body in bodies:
                facts.observed(body, {"session_id": lambda v: v == self._thread,
                                      "project_key": lambda v: v == project_key},
                               "appserver_observed_business_scope_mismatch")
                if type(body) is dict and (item.get("tool") == "c1_bootstrap" or body.get("status") == "ready"):
                    for name, expected in (("prior_session", project_key), ("foreign_project", foreign_key)):
                        facts.observed(body.get(name), {"project_key": lambda v, expected=expected: v == expected},
                                       "appserver_observed_seed_scope_mismatch")
        self._latch(facts)

    def _latch(self, facts):
        for severity, reason in facts.problems:
            (self._failures if severity == "failed" else self._unknowns).add(reason)

    def _inspect_call_data(self, data, phase, *, track):
        facts = _Facts()
        item_id = data.get("item_id")
        calls = self._native.summary()["calls"]
        bootstrap = calls[0].get("outcome") if calls else None
        arguments = _argument_proofs(bootstrap, self._snapshot.run_nonce)
        argument_hash, tool = data.get("arguments_sha256"), data.get("tool")
        if data.get("outcome_state") == "failed":
            facts.known(False, "appserver_observed_result_failure")
        elif phase == "completed_sha256" and data.get("outcome_state") != "valid":
            facts.missing("missing_appserver_result_proof")
        for key in ("fingerprint", "result_sha256"):
            if data.get(key) is not None:
                facts.known(_sha(data[key]), "appserver_observed_hash_schema")
        if tool is not None:
            facts.known(tool in TOOLS, "appserver_unexpected_tool")
        if argument_hash is not None:
            facts.known(_sha(argument_hash), "appserver_argument_hash_schema")
            if tool == "c1_bootstrap":
                facts.known(argument_hash == arguments[1], "appserver_observed_argument_conflict")
            elif tool == "memory_status" and _sha(argument_hash):
                handles = {arguments[key] for key in (2, 3, 4) if key in arguments}
                if len(handles) == 3:
                    facts.known(argument_hash in handles, "appserver_observed_argument_conflict")
        if not _identifier(item_id):
            self._latch(facts)
            return
        if item_id in self._seen_discarded:
            self._replay = True
            facts.known(False, "appserver_item_kind_reused")
        prior = self._seen_calls.get(item_id)
        step = list(self._seen_calls).index(item_id) + 1 if prior is not None else len(self._seen_calls) + 1
        if not self._seen_turn_started:
            self._order = False
            facts.known(False, "appserver_event_before_turn")
        if data.get("tool") is not None:
            facts.known(step <= 5 and data["tool"] == TOOLS[step - 1], "appserver_tool_order_conflict")
        if data.get("arguments_sha256") is not None and step in arguments:
            facts.known(data["arguments_sha256"] == arguments[step], "appserver_observed_argument_conflict")
        if prior is not None:
            for key, completed_key in (("tool", "_completed_tool"),
                                       ("arguments_sha256", "_completed_arguments_sha256")):
                for known_key in (key, completed_key):
                    if data.get(key) is not None and prior.get(known_key) is not None and data[key] != prior[known_key]:
                        self._replay = True
                        facts.known(False, "appserver_call_identity_replay")
            if (data.get("result_sha256") is not None and prior.get("result_sha256") is not None
                    and data["result_sha256"] != prior["result_sha256"]):
                self._replay = True
                facts.known(False, "appserver_observed_body_conflict")
            observed = data.get("fingerprint")
            if observed is not None and prior.get(phase) is not None and observed != prior[phase]:
                self._replay = True
                facts.known(False, "appserver_call_phase_replay")
        if phase == "completed_sha256":
            if prior is None or not prior.get("_started_observed", False):
                self._order = False
                facts.known(False, "appserver_completion_without_start")
            terminal = data.get("terminal_status")
            if terminal is not None:
                facts.known(terminal == ("failed" if step in (3, 4) else "completed"),
                            "appserver_observed_terminal_conflict")
        elif prior is None:
            facts.known(len(self._seen_calls) < 5, "appserver_extra_call")
            if any(not row.get("_completed_observed", False) for row in self._seen_calls.values()):
                self._order = False
                facts.known(False, "appserver_overlapping_calls")
        observed_key = "_started_observed" if phase == "started_sha256" else "_completed_observed"
        phase_seen = prior is not None and prior.get(observed_key, False)
        prior_hash = prior.get(phase) if prior is not None else None
        comparable_repeat = phase_seen and (prior_hash is None or data.get("fingerprint") is None
                                             or prior_hash == data["fingerprint"])
        if self._seen_turn_completed and not comparable_repeat:
            self._order = False
            facts.known(False, "appserver_call_after_turn")
        self._latch(facts)
        if track and not any(state == "failed" for state, _ in facts.problems):
            record = dict(prior) if prior is not None else {
                "item_id": item_id, "tool": data.get("tool") if phase == "started_sha256" else None,
                "arguments_sha256": data.get("arguments_sha256") if phase == "started_sha256" else None,
                "started_sha256": None, "completed_sha256": None, "terminal_status": None, "result_sha256": None,
                "_started_observed": False, "_completed_observed": False,
                "_completed_tool": None, "_completed_arguments_sha256": None}
            record[observed_key] = True
            if data.get("fingerprint") is not None:
                record[phase] = data["fingerprint"]
            if phase == "started_sha256":
                # Only an actual start observation can supply start-side facts.
                for key in ("tool", "arguments_sha256"):
                    if record.get(key) is None and data.get(key) is not None:
                        record[key] = data[key]
            else:
                for key in ("terminal_status", "result_sha256"):
                    if data.get(key) is not None:
                        record[key] = data[key]
                for key, target in (("tool", "_completed_tool"), ("arguments_sha256", "_completed_arguments_sha256")):
                    if data.get(key) is not None:
                        record[target] = data[key]
            self._seen_calls[item_id] = record

    def _inspect_discarded_data(self, data, phase, *, track):
        facts = _Facts()
        item_id, kind = data.get("item_id"), data.get("kind")
        if type(kind) is not str or kind not in _NONBUSINESS:
            return
        if data.get("fingerprint") is not None:
            facts.known(_sha(data["fingerprint"]), "appserver_discarded_hash_schema")
        if kind == "userMessage":
            content = data.get("content_sha256")
            if content is None:
                facts.missing("missing_appserver_user_origin")
            else:
                facts.known(content == self._prompt_sha256, "appserver_user_origin_mismatch")
        if not _identifier(item_id):
            self._latch(facts)
            return
        if item_id in self._seen_calls:
            self._replay = True
            facts.known(False, "appserver_item_kind_reused")
        if not self._seen_turn_started:
            self._order = False
            facts.known(False, "appserver_discarded_before_turn")
        prior = self._seen_discarded.get(item_id)
        if prior is not None:
            if prior["kind"] != kind or (data.get("fingerprint") is not None and prior[phase] is not None
                                         and prior[phase] != data["fingerprint"]):
                self._replay = True
                facts.known(False, "appserver_discarded_replay")
        else:
            facts.known(len(self._seen_discarded) < MAX_DISCARDED_ITEMS, "appserver_discarded_item_budget")
        if phase == "completed_sha256" and (prior is None or not prior.get("_started_observed", False)):
            self._order = False
            facts.known(False, "appserver_discarded_completion_without_start")
        if kind == "userMessage":
            if prior is None and any(row["kind"] == kind for row in self._seen_discarded.values()):
                facts.known(False, "appserver_extra_user_origin")
            if (prior is not None and phase == "completed_sha256" and data.get("fingerprint") is not None
                    and prior["started_sha256"] is not None):
                facts.known(prior["started_sha256"] == data["fingerprint"], "appserver_user_item_changed")
        observed_key = "_started_observed" if phase == "started_sha256" else "_completed_observed"
        phase_seen = prior is not None and prior.get(observed_key, False)
        prior_hash = prior.get(phase) if prior is not None else None
        comparable_repeat = phase_seen and (prior_hash is None or data.get("fingerprint") is None
                                             or prior_hash == data["fingerprint"])
        if self._seen_turn_completed and not comparable_repeat:
            self._order = False
            facts.known(False, "appserver_discarded_after_turn")
        self._latch(facts)
        if track and not any(state == "failed" for state, _ in facts.problems):
            was_completed = prior is not None and prior.get("_completed_observed", False)
            record = dict(prior) if prior is not None else {
                "item_id": item_id, "kind": kind, "started_sha256": None, "completed_sha256": None,
                "content_sha256": data.get("content_sha256") if kind == "userMessage" and phase == "started_sha256" else None,
                "_started_observed": False, "_completed_observed": False}
            record[observed_key] = True
            if data.get("fingerprint") is not None:
                record[phase] = data["fingerprint"]
            if phase == "started_sha256" and kind == "userMessage" and data.get("content_sha256") is not None:
                record["content_sha256"] = data["content_sha256"]
            self._seen_discarded[item_id] = record
            if phase == "completed_sha256" and kind == "agentMessage" and not was_completed:
                self._seen_last_agent = item_id

    def _inspect_completion(self, completion, *, track):
        facts = _Facts()
        _check_completion(facts, completion, list(self._seen_calls.values()), list(self._seen_discarded.values()),
                          self._seen_last_agent, nonce=self._snapshot.run_nonce, prompt_sha256=self._prompt_sha256)
        if type(completion) is dict:
            digest = completion.get("fingerprint")
            if digest is not None and self._seen_completion_sha256 is not None and digest != self._seen_completion_sha256:
                self._replay = True
                facts.known(False, "appserver_completion_replay")
            if track and _sha(digest) and self._seen_completion_sha256 is None:
                self._seen_completion_sha256 = digest
        if not self._seen_turn_started:
            self._order = False
            facts.known(False, "appserver_turn_end_before_start")
        self._latch(facts)
        if track:
            self._seen_turn_completed = True

    def _observe_present_conflicts(self, message):
        # Reinspect available facts after an outer/inner schema absence. The same
        # inspector is used without invented timestamps, fields or notifications.
        self._observe_business_scope(message)
        if not isinstance(message, RpcMessage) or type(message.payload) is not dict:
            return
        params = message.payload.get("params")
        if type(params) is not dict:
            return
        method = message.method
        if method not in ("thread/started", "turn/started", "turn/completed", "item/started", "item/completed"):
            return
        for key, expected in (("threadId", self._thread), ("turnId", self._turn)):
            if key in params and params[key] != expected:
                self._failures.add("appserver_event_identity_mismatch")
        if method == "thread/started":
            thread = params.get("thread")
            if type(thread) is dict and "id" in thread and thread["id"] != self._thread:
                self._failures.add("appserver_event_identity_mismatch")
        if method.startswith("turn/"):
            turn = params.get("turn")
            if type(turn) is dict:
                if "id" in turn and turn["id"] != self._turn:
                    self._failures.add("appserver_event_identity_mismatch")
                if method == "turn/completed":
                    facts = _Facts()
                    completion = _completion_metadata(facts, turn)
                    # This path followed a decode problem, so a full turn hash
                    # cannot assert a replay merely because fields were absent.
                    completion["fingerprint"] = None
                    self._latch(facts)
                    self._inspect_completion(completion, track=False)
        if method.startswith("item/") and type(params.get("item")) is dict:
            facts = _Facts()
            data, kind, _, _ = _inspect_item(facts, params["item"], completed=method == "item/completed")
            self._latch(facts)
            bound = (message.kind == "notification" and message.rpc_id is None
                     and message.payload.get("method") == method
                     and not any(key in message.payload for key in ("id", "result", "error"))
                     and ("jsonrpc" not in message.payload or message.payload["jsonrpc"] == "2.0")
                     and params.get("threadId") == self._thread and params.get("turnId") == self._turn)
            # Unknown item fields do not erase an actual, owned phase. These
            # receipts remain negative-only; malformed/foreign input is not tracked.
            track = bound and not any(state == "failed" for state, _ in facts.problems)
            phase = "completed_sha256" if method == "item/completed" else "started_sha256"
            if kind == "unexpected_tool":
                self._failures.add("appserver_unexpected_tool")
            elif data["kind"] == "mcpToolCall":
                self._inspect_call_data(data, phase, track=track)
            elif type(data["kind"]) is str and data["kind"] in _NONBUSINESS:
                self._inspect_discarded_data(data, phase, track=track)

    def record_rpc_failure(self, code: str) -> None:
        """Latch the actual caught Peer.failure_code, never exception/model text.

        No code is guessed if the peer has no static fact. An unrecognized value
        raises a sanitized error AND freezes unknown without retaining the value.
        Before this state can be bound, the driver owns the same no-success duty.
        """
        if type(code) is not str or code not in _RPC_FAILED | _RPC_UNKNOWN:
            self._unknowns.add("appserver_rpc_failure_unclassified")
            raise AppServerEvidenceError("unknown", "appserver_rpc_failure_code_invalid")
        (self._failures if code in _RPC_FAILED else self._unknowns).add(code)

    def _whitelist_native(self, native):
        facts = _Facts()
        facts.known(native.outcome_state in (None, "valid", "unknown", "failed"), "appserver_native_outcome_invalid")
        facts.known(native.result_sha256 is None or _sha(native.result_sha256), "appserver_native_hash_invalid")
        facts.known(native.fingerprint is None or _sha(native.fingerprint), "appserver_native_hash_invalid")
        outcome = native.outcome
        if outcome is not None:
            key = "local:" + self._snapshot.run_nonce + "-" + self._project
            other = "local:" + self._snapshot.run_nonce + "-" + ("b" if self._project == "a" else "a")
            if native.tool == "c1_bootstrap":
                facts.capture(lambda: _bootstrap_summary(outcome, key, other, self._thread))
            elif type(outcome) is dict and (outcome.get("status") == "failed" or "error_code" in outcome):
                facts.capture(lambda: _error_summary(outcome))
            else:
                calls = self._native.summary()["calls"]
                bootstrap = calls[0].get("outcome") if calls else {}
                facts.capture(lambda: _status_summary(outcome, key, self._thread, bootstrap))
        _finish(facts)
        # Even a manually supplied normalized event cannot introduce raw diagnostic
        # strings into a persisted summary. Decoded reasons are static too.
        reason = (None if native.outcome_state in (None, "valid") else
                  "appserver_result_failed" if native.outcome_state == "failed" else "appserver_result_unknown")
        return replace(native, outcome_reason=reason)

    def _consume_discarded(self, item):
        # Positive proof is populated from this actual complete live event,
        # never copied/backfilled from negative-only partial phase receipts.
        record = dict(self._discarded.get(item.item_id, {
            "item_id": item.item_id, "kind": item.tool, "started_sha256": None,
            "completed_sha256": None, "content_sha256": None}))
        phase = "started_sha256" if item.terminal_status == "in_progress" else "completed_sha256"
        record[phase] = item.fingerprint
        if item.tool == "userMessage":
            record["content_sha256"] = item.result_sha256
        self._discarded[item.item_id] = record
        if item.tool == "agentMessage" and item.terminal_status == "completed":
            # Exact duplicates do not become new chronological completions.
            self._last_agent_item_id = self._seen_last_agent

    def consume(self, event: AppServerEvent) -> None:
        if not isinstance(event, AppServerEvent) or type(event.kind) is not str:
            self._failures.add("appserver_event_invalid")
            return
        if (event.thread_id != self._thread or
                (event.kind != "thread_started" and event.turn_id != self._turn)):
            self._failures.add("appserver_event_identity_mismatch")
            return
        native = None
        if event.kind == "thread_started":
            # The returned Session already binds this thread. Its original-wire
            # fingerprint checks own snapshot/configuration conflicts; this owner
            # independently checks thread/turn identity, even after completion.
            # A same-bound-thread notice establishes no new turn or business call.
            if event.turn_id is not None:
                self._failures.add("appserver_thread_order_invalid")
        elif event.kind == "turn_started":
            self._seen_turn_started = True
        elif event.kind in {"turn_completed", "turn_failed"}:
            self._inspect_completion(event.completion, track=True)
            if event.kind == "turn_failed":
                self._failures.add("appserver_observed_failure")
        elif event.kind == "unexpected_tool":
            self._failures.add("appserver_observed_failure")
        elif event.kind in {"call_started", "call_finished"}:
            item = event.item
            if (not isinstance(item, NativeEvent) or item.kind != event.kind or item.thread_id != self._thread
                    or not _identifier(item.item_id)):
                self._failures.add("appserver_native_identity_mismatch")
                return
            try:
                native = self._whitelist_native(item)
            except AppServerEvidenceError as error:
                (self._failures if error.state == "failed" else self._unknowns).add(error.reason)
            data = {"item_id": item.item_id, "kind": "mcpToolCall", "tool": item.tool,
                    "arguments_sha256": item.arguments_sha256, "fingerprint": item.fingerprint,
                    "terminal_status": item.terminal_status, "result_sha256": item.result_sha256,
                    "outcome_state": item.outcome_state}
            self._inspect_call_data(data, "started_sha256" if event.kind == "call_started" else "completed_sha256",
                                    track=True)
        elif event.kind == "ignored_known":
            item = event.item
            if (not isinstance(item, NativeEvent) or item.kind != "ignored_known" or item.thread_id != self._thread
                    or not _identifier(item.item_id) or type(item.tool) is not str or item.tool not in _NONBUSINESS
                    or item.terminal_status not in ("in_progress", "completed") or not _sha(item.fingerprint)
                    or item.outcome is not None):
                self._failures.add("appserver_discarded_item_invalid")
                return
            self._inspect_discarded_data({"item_id": item.item_id, "kind": item.tool, "fingerprint": item.fingerprint,
                                          "content_sha256": item.result_sha256},
                                         "started_sha256" if item.terminal_status == "in_progress" else "completed_sha256",
                                         track=True)
        else:
            self._failures.add("appserver_event_invalid")
            return
        # Inspection above is ALWAYS performed, including while frozen. Only the
        # successful-admission path below stops: receipts are not positive proof.
        if self._failures or self._unknowns:
            return
        if event.kind == "thread_started":
            return
        if event.kind == "turn_started":
            if not self._completed:
                self._started = True
            return
        if event.kind == "ignored_known":
            self._consume_discarded(event.item)
            return
        if event.kind == "turn_completed":
            if self._completed:
                return
            self._completion = copy.deepcopy(event.completion)
            summary = self._native.summary()
            if summary["unique_calls"] != 5 or summary["completed_calls"] != 5:
                self._unknowns.add("appserver_incomplete_calls_at_turn_end")
            else:
                self._native.consume(NativeEvent("turn_completed", thread_id=self._thread))
            self._completed = True
            return
        if native is None:
            self._failures.add("appserver_native_event_unchecked")
            return
        try:
            self._native.consume(native)
        except ValueError:
            self._failures.add("appserver_sequence_invalid")
            failures = self._native.summary()["failure_reasons"]
            self._replay |= any(code in failures for code in ("conflicting_duplicate", "completion_conflict"))
            self._order &= not any(code in failures for code in ("completion_without_start", "overlapping_calls",
                                                                "call_after_turn", "tool_order"))
        else:
            record = dict(self._calls.get(native.item_id, {
                "item_id": native.item_id, "tool": native.tool, "arguments_sha256": native.arguments_sha256,
                "started_sha256": None, "completed_sha256": None, "terminal_status": None, "result_sha256": None}))
            if native.kind == "call_started":
                record["started_sha256"] = native.fingerprint
            else:
                record.update(completed_sha256=native.fingerprint, terminal_status=native.terminal_status,
                              result_sha256=native.result_sha256)
            self._calls[native.item_id] = record

    def summary(self) -> dict:
        """Detached native reducer data plus required event proof v1, no bodies."""
        native = self._native.summary()
        failures = sorted(self._failures | set(native["failure_reasons"]))
        unknowns = sorted(self._unknowns | set(native["unknown_reasons"]))
        if not self._started:
            unknowns = sorted(set(unknowns) | {"missing_appserver_turn_start"})
        if not self._completed:
            unknowns = sorted(set(unknowns) | {"missing_appserver_turn_end"})
        proof = {"schema_version": 1, "transport_kind": TRANSPORT_KIND,
                 "run_nonce": self._snapshot.run_nonce, "project": self._project,
                 "manifest_sha256": self._snapshot.manifest_sha256, "root_sha256": self._root_sha256,
                 "thread_id": self._thread, "native_turn_id": self._turn,
                 "thread_bound": True, "turn_bound": True, "turn_started": self._started,
                 "turn_completed": self._completed, "unique_calls": native["unique_calls"],
                 "completed_calls": native["completed_calls"],
                 "item_ids": [call["item_id"] for call in native["calls"]],
                 "order_verified": self._order, "replay_detected": self._replay,
                 "failure_reasons": failures, "unknown_reasons": unknowns,
                 "discarded_items": list(self._discarded.values()), "call_items": list(self._calls.values()),
                 "last_agent_item_id": self._last_agent_item_id, "completion": self._completion}
        return copy.deepcopy({"native": native, "appserver_event_proof": proof})


def _status(checks):
    return ("failed" if any(value is False for value in checks.values()) else
            "verified" if all(value is True for value in checks.values()) else "unknown")


def _run_checks(operations):
    checks, reasons = {}, []
    for name, operation in operations:
        try:
            operation()
            checks[name] = True
        except _EvidenceProblem as error:
            checks[name] = False if error.state == "failed" else None
            reasons.append(name + ":" + error.reason)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError, RecursionError, OverflowError):
            checks[name] = False
            reasons.append(name + ":invalid_appserver_evidence")
    return checks, reasons


def _true(facts, value, keys, reason):
    for key in keys:
        if key not in value:
            continue  # Parent object records missing keys.
        if type(value[key]) is not bool:
            facts.known(False, reason)
        elif value[key] is False:
            facts.missing(reason)


def _empty_codes(facts, obj, key, reason):
    if key not in obj:
        return
    value = obj[key]
    valid = type(value) is list and all(type(code) is str and 0 < len(code) <= 512 for code in value)
    facts.known(valid, reason)
    if valid and value:
        if key in {"failure_reasons", "failure_codes"}:
            facts.known(False, reason)
        else:
            facts.missing(reason)


def _discarded_proof(snapshot, project, proof):
    facts = _Facts()
    if "discarded_items" not in proof:
        facts.missing("missing_appserver_discarded_items")
        return facts.finish()
    rows = proof["discarded_items"]
    facts.known(type(rows) is list, "appserver_discarded_schema")
    if type(rows) is not list:
        return facts.finish()
    facts.known(len(rows) <= MAX_DISCARDED_ITEMS, "appserver_discarded_count")
    ids, user_count = [], 0
    for value in rows[:MAX_DISCARDED_ITEMS]:
        row = facts.object(value, {"item_id", "kind", "started_sha256", "completed_sha256", "content_sha256"},
                           "appserver_discarded_item", exact=True)
        facts.observed(row, {"item_id": _identifier,
                             "kind": lambda v: type(v) is str and v in _NONBUSINESS,
                             "started_sha256": lambda v: v is None or _sha(v),
                             "completed_sha256": lambda v: v is None or _sha(v)}, "appserver_discarded_schema")
        ids.append(row.get("item_id"))
        if row.get("started_sha256") is None:
            facts.missing("missing_appserver_discarded_start")
        if proof.get("turn_completed") is True and row.get("completed_sha256") is None:
            facts.missing("missing_appserver_discarded_completion")
        if row.get("kind") == "userMessage":
            user_count += 1
            facts.observed(row, {"content_sha256": lambda v: v == _digest(snapshot.manifest["projects"][project]["prompt"])},
                           "appserver_user_origin_mismatch")
            if row.get("started_sha256") is not None and row.get("completed_sha256") is not None:
                facts.equal(row, "started_sha256", row, "completed_sha256", "appserver_user_item_changed")
        else:
            facts.observed(row, {"content_sha256": lambda v: v is None}, "appserver_discarded_content_unexpected")
    facts.distinct(ids, "appserver_discarded_replay")
    facts.known(user_count <= 1, "appserver_extra_user_origin")
    item_ids = proof.get("item_ids")
    if type(item_ids) is list:
        facts.distinct(ids + item_ids, "appserver_item_kind_reused")
    return facts.finish()


def _admitted_items_proof(snapshot, project, proof, mapping):
    facts = _Facts()
    calls = proof.get("call_items")
    if "call_items" not in proof:
        facts.missing("missing_appserver_call_items")
    elif type(calls) is not list:
        facts.known(False, "appserver_call_items_schema")
    calls = calls if type(calls) is list else []
    facts.known(len(calls) <= 5, "appserver_call_item_budget")
    ids, observed_finishes = [], 0
    for step, value in enumerate(calls[:5], 1):
        row = facts.object(value, _CALL_PROOF_FIELDS, "appserver_call_item", exact=True)
        facts.observed(row, {"item_id": _identifier, "tool": lambda v: v == TOOLS[step - 1],
                             **{key: lambda v: v is None or _sha(v) for key in
                                ("arguments_sha256", "started_sha256", "completed_sha256", "result_sha256")},
                             "terminal_status": lambda v: v is None or v == ("failed" if step in (3, 4) else "completed")},
                       "appserver_call_item_schema")
        ids.append(row.get("item_id"))
        if any(row.get(key) is None for key in _CALL_PROOF_FIELDS - {"item_id", "tool"}):
            facts.missing("missing_appserver_call_item_proof")
        if row.get("completed_sha256") is not None:
            observed_finishes += 1
        if type(mapping) is list and step <= len(mapping) and type(mapping[step - 1]) is dict:
            for key in ("item_id", "tool", "arguments_sha256"):
                if row.get(key) is not None and mapping[step - 1].get(key) is not None:
                    facts.equal(row, key, mapping[step - 1], key, "appserver_call_item_mapping_conflict")
    facts.distinct(ids, "appserver_call_item_replay")
    if type(proof.get("item_ids")) is list:
        if len(proof["item_ids"]) != len(ids):
            facts.missing("missing_appserver_call_registry_coverage")
        for actual, expected in zip(ids, proof["item_ids"]):
            if actual is not None:
                facts.known(actual == expected, "appserver_call_registry_identity_conflict")
    if type(proof.get("unique_calls")) is int:
        facts.known(len(calls) <= proof["unique_calls"], "appserver_call_registry_count_conflict")
        if len(calls) < proof["unique_calls"]:
            facts.missing("missing_appserver_call_registry_coverage")
    if type(proof.get("completed_calls")) is int:
        facts.known(observed_finishes <= proof["completed_calls"], "appserver_call_registry_count_conflict")
        if observed_finishes < proof["completed_calls"]:
            facts.missing("missing_appserver_call_finish_registry")
    discarded = proof.get("discarded_items")
    discarded = discarded if type(discarded) is list else []
    last_agent = proof.get("last_agent_item_id")
    if "last_agent_item_id" in proof:
        facts.known(last_agent is None or _identifier(last_agent), "appserver_last_agent_schema")
    agents = {row["item_id"]: row for row in discarded if type(row) is dict and _identifier(row.get("item_id"))
              and row.get("kind") == "agentMessage" and row.get("completed_sha256") is not None}
    if _identifier(last_agent):
        facts.known(last_agent in agents, "appserver_last_agent_registry_conflict")
    elif agents:
        facts.missing("missing_appserver_last_agent")
    facts.capture(lambda: _check_completion(facts, proof.get("completion"), calls, discarded, last_agent,
                                           nonce=snapshot.run_nonce,
                                           prompt_sha256=_digest(snapshot.manifest["projects"][project]["prompt"])))
    return facts.finish()


def _event_proof(snapshot, project, proof, mapping=None):
    facts = _Facts()
    proof = facts.object(proof, EVENT_FIELDS, "appserver_event_proof", exact=True)
    facts.observed(proof, {"schema_version": lambda v: type(v) is int and v == 1,
                           "transport_kind": lambda v: v == TRANSPORT_KIND,
                           "run_nonce": lambda v: v == snapshot.run_nonce, "project": lambda v: v == project,
                           "manifest_sha256": lambda v: v == snapshot.manifest_sha256,
                           "root_sha256": lambda v: v == _root_hash(snapshot, project),
                           "thread_id": _identifier, "native_turn_id": _identifier,
                           "unique_calls": lambda v: type(v) is int and 0 <= v <= 5,
                           "completed_calls": lambda v: type(v) is int and 0 <= v <= 5,
                           "order_verified": lambda v: v is True,
                           "replay_detected": lambda v: v is False}, "appserver_event_contradiction")
    _true(facts, proof, {"thread_bound", "turn_bound", "turn_started", "turn_completed"}, "missing_appserver_event_boundary")
    for key in ("unique_calls", "completed_calls"):
        if type(proof.get(key)) is int and proof[key] < 5:
            facts.missing("missing_appserver_event_calls")
    unique, completed = proof.get("unique_calls"), proof.get("completed_calls")
    if type(unique) is int and unique > 0:
        for key in ("thread_bound", "turn_bound", "turn_started"):
            if proof.get(key) is False:
                facts.known(False, "appserver_event_boundary_conflict")
    if proof.get("turn_completed") is True and proof.get("turn_started") is False:
        facts.known(False, "appserver_event_boundary_conflict")
    if type(unique) is int and type(completed) is int:
        facts.known(completed <= unique, "appserver_event_count_conflict")
    if "item_ids" in proof:
        ids = proof["item_ids"]
        facts.known(type(ids) is list, "appserver_event_ids_invalid")
        if type(ids) is list:
            facts.known(len(ids) <= 5 and all(_identifier(x) for x in ids), "appserver_event_ids_invalid")
            facts.distinct(ids, "appserver_event_item_replay")
            if type(unique) is int:
                facts.known(len(ids) <= unique, "appserver_event_count_conflict")
                if len(ids) < unique:
                    facts.missing("missing_appserver_event_ids")
            if mapping is not None:
                observed = [row.get("item_id") for row in mapping if type(row) is dict]
                if len(ids) == 5 and len(observed) == 5:
                    facts.known(ids == observed, "appserver_event_mapping_mismatch")
                else:
                    for index, item_id in enumerate(observed):
                        if index < len(ids):
                            facts.known(item_id == ids[index], "appserver_event_mapping_mismatch")
    facts.capture(lambda: _discarded_proof(snapshot, project, proof))
    facts.capture(lambda: _admitted_items_proof(snapshot, project, proof, mapping))
    for key in ("failure_reasons", "unknown_reasons"):
        _empty_codes(facts, proof, key, "appserver_event_" + key)
    if proof.get("turn_completed") is True and type(completed) is int and completed == 5:
        if type(unique) is int:
            facts.known(unique == completed, "appserver_event_count_conflict")
    return facts.finish()


def validate_appserver_business_evidence(snapshot: ReviewSnapshot, project: str, reservation: dict,
                                        event_state: AppServerRunState, transport_rows: list[dict],
                                        outcome_rows: list[dict]) -> dict:
    """Compose the unchanged shared business core with REQUIRED bound v2 proof."""
    if not isinstance(event_state, AppServerRunState):
        raise AppServerEvidenceError("failed", "appserver_state_required")
    data = event_state.summary()
    result = validate_business_evidence(snapshot, project, reservation, data["native"], transport_rows, outcome_rows)
    checks, reasons = _run_checks([("appserver_events", lambda: _event_proof(
        snapshot, project, data["appserver_event_proof"], result["call_mapping"]))])
    result["checks"].update(checks)
    result["reasons"].extend(reasons)
    result.update(status=_status(result["checks"]), transport_kind=TRANSPORT_KIND,
                  native_thread_id=data["native"]["native_thread_id"],
                  native_turn_id=data["appserver_event_proof"]["native_turn_id"],
                  unique_calls=data["native"]["unique_calls"], completed_calls=data["native"]["completed_calls"],
                  appserver_event_proof=data["appserver_event_proof"])
    return result


def _session_proof(snapshot, project, summary):
    facts = _Facts()
    summary = facts.object(summary, SESSION_FIELDS, "appserver_session")
    facts.observed(summary, {"schema_version": lambda v: type(v) is int and v == 1,
                             "run_nonce": lambda v: v == snapshot.run_nonce,
                             "project": lambda v: v == project,
                             "manifest_sha256": lambda v: v == snapshot.manifest_sha256,
                             "root_sha256": lambda v: v == _root_hash(snapshot, project),
                             "thread_id": lambda v: v is None or _identifier(v),
                             "turn_id": lambda v: v is None or _identifier(v),
                             "state": lambda v: v in {"created", "attempt_reserved", "thread_start_sent", "thread_verified",
                                                       "turn_sent", "collecting", "protocol_complete", "unknown", "failed"},
                             "settings_updates_verified": lambda v: type(v) is int and v >= 0,
                             "settings_network_access_sources": lambda v: type(v) is list and len(v) <= 2 and
                                 len(v) == len(set(v)) and all(x in ("explicit", "schema_default") for x in v),
                             "approval_observed": lambda v: v is False,
                             "approval_method": lambda v: v is None,
                             "approval_action": lambda v: v is None,
                             "interrupt_intent": lambda v: v is False}, "appserver_session_contradiction")
    if summary.get("state") == "failed":
        facts.known(False, "appserver_session_failed")
    elif summary.get("state") != "protocol_complete":
        facts.missing("appserver_session_not_complete")
    for key in ("thread_id", "turn_id", "turn_status"):
        if summary.get(key) is None:
            facts.missing("missing_appserver_" + key)
    if "turn_status" in summary and summary["turn_status"] is not None:
        facts.known(summary["turn_status"] == "completed", "appserver_turn_failed")
    _true(facts, summary, {"reservation_bound", "thread_start_intent", "turn_start_intent", "thread_response_verified",
                           "thread_started_observed", "turn_response_observed", "turn_started_observed",
                           "new_turn_verified", "turn_completed_observed"}, "missing_appserver_session_observation")
    # Session freezes unknown with static failure_codes too. Its state is the
    # severity authority; never relabel SOURCE_UNKNOWN as an observed failure.
    if "failure_codes" in summary:
        codes = summary["failure_codes"]
        facts.known(type(codes) is list and all(type(x) is str and x.startswith("C1_") for x in codes),
                    "appserver_session_codes_invalid")
        if codes and summary.get("state") not in ("unknown", "failed"):
            facts.known(False, "appserver_session_state_conflict")
    if "policy" in summary:
        policy = facts.object(summary["policy"], POLICY_FIELDS, "appserver_policy", exact=True)
        facts.observed(policy, {"approval_policy": lambda v: v is None or v == "on-request",
                                "approvals_reviewer": lambda v: v is None or v == "user",
                                "sandbox_type": lambda v: v is None or v == "readOnly",
                                "network_access": lambda v: v is None or v is False,
                                "network_access_source": lambda v: v is None or v in ("explicit", "schema_default"),
                                "model": lambda v: v is None or _identifier(v),
                                "model_provider": lambda v: v is None or _identifier(v),
                                "root_sha256": lambda v: v is None or v == _root_hash(snapshot, project)},
                       "appserver_policy_drift")
        if any(policy.get(key) is None for key in POLICY_FIELDS):
            facts.missing("missing_appserver_policy")
    return facts.finish()


def _business_proof(snapshot, project, business):
    facts = _Facts()
    business = facts.object(business, {"status", "checks", "reasons", "transport_count", "business_count", "call_mapping",
                                       "transport_kind", "native_thread_id", "native_turn_id", "unique_calls",
                                       "completed_calls", "appserver_event_proof"}, "appserver_business")
    facts.observed(business, {"status": lambda v: v in ("verified", "unknown", "failed"),
                              "transport_kind": lambda v: v == TRANSPORT_KIND,
                              "native_thread_id": lambda v: v is None or _identifier(v),
                              "native_turn_id": _identifier,
                              "reasons": lambda v: type(v) is list and all(type(x) is str for x in v)},
                   "appserver_business_contradiction")
    if business.get("status") == "failed":
        facts.known(False, "appserver_business_failed")
    elif business.get("status") != "verified":
        facts.missing("appserver_business_unverified")
    for key in ("transport_count", "business_count", "unique_calls", "completed_calls"):
        if key in business:
            value = business[key]
            facts.known(value is None or type(value) is int and 0 <= value <= 5, "appserver_business_count")
            if value is None or type(value) is int and value < 5:
                facts.missing("missing_appserver_business_count")
    if "checks" in business:
        checks = facts.object(business["checks"], BUSINESS_CHECKS | {"appserver_events"}, "appserver_business_checks", exact=True)
        for key, value in checks.items():
            facts.known(value is None or type(value) is bool, "appserver_business_check_invalid")
            if value is False:
                facts.known(False, "appserver_business_check_failed")
            elif value is None:
                facts.missing("missing_appserver_business_check")
    mapping = business.get("call_mapping")
    if mapping is not None:
        facts.known(type(mapping) is list, "appserver_mapping_invalid")
        if type(mapping) is list:
            facts.known(len(mapping) <= 5, "appserver_mapping_count")
            if len(mapping) < 5:
                facts.missing("missing_appserver_mapping")
            for step, row in enumerate(mapping, 1):
                row = facts.object(row, {"step", "item_id", "call_id", "tool", "arguments_sha256",
                                         "transport_invocation_id", "business_invocation_id"}, "appserver_mapping", exact=True)
                facts.observed(row, {"step": lambda v: type(v) is int and v == step,
                                     "item_id": _identifier, "call_id": _identifier,
                                     "tool": lambda v: step <= 5 and v == TOOLS[step - 1],
                                     "arguments_sha256": _sha,
                                     "transport_invocation_id": lambda v: type(v) is str and len(v) == 32 and
                                         all(c in "0123456789abcdef" for c in v),
                                     "business_invocation_id": lambda v: type(v) is str and len(v) == 32 and
                                         all(c in "0123456789abcdef" for c in v)}, "appserver_mapping_contradiction")
            for key in ("item_id", "call_id"):
                facts.distinct([row.get(key) for row in mapping if type(row) is dict], "appserver_mapping_replay")
            facts.distinct([row.get(key) for row in mapping if type(row) is dict
                            for key in ("transport_invocation_id", "business_invocation_id")], "appserver_mapping_audit_replay")
    else:
        facts.missing("missing_appserver_mapping")
    return facts.finish()


def _cross_proof(snapshot, project, business, session):
    facts = _Facts()
    business = facts.object(business, set(), "appserver_business")
    session = facts.object(session, set(), "appserver_session")
    if "appserver_event_proof" not in business:
        facts.missing("missing_appserver_event_proof")
        proof = {}
    else:
        proof = business["appserver_event_proof"]
        facts.capture(lambda: _event_proof(snapshot, project, proof,
                                           business.get("call_mapping") if type(business.get("call_mapping")) is list else None))
    if type(proof) is dict:
        for proof_key, business_key, session_key in (("thread_id", "native_thread_id", "thread_id"),
                                                    ("native_turn_id", "native_turn_id", "turn_id")):
            facts.equal(proof, proof_key, business, business_key, "appserver_business_identity_mismatch")
            if session.get(session_key) is not None:
                facts.equal(proof, proof_key, session, session_key, "appserver_session_identity_mismatch")
        for key in ("unique_calls", "completed_calls"):
            facts.equal(proof, key, business, key, "appserver_event_count_conflict")
        mapping = business.get("call_mapping")
        if type(mapping) is list:
            terminals = len(mapping)
            for key in ("unique_calls", "completed_calls"):
                if type(proof.get(key)) is int:
                    facts.known(proof[key] >= terminals, "appserver_event_count_below_mapping")
    return facts.finish()


def _shutdown_proof(shutdown):
    facts = _Facts()
    shutdown = facts.object(shutdown, {"input_eof_sent", "stdin_eof", "stdout_eof", "stderr_eof", "natural_exit_code",
                                       "forced_termination", "cleanup", "io_failure", "io_threads_stopped"},
                            "appserver_shutdown", exact=True)
    _true(facts, shutdown, {"input_eof_sent", "stdin_eof", "stdout_eof", "stderr_eof", "io_threads_stopped"},
          "missing_appserver_shutdown_observation")
    facts.observed(shutdown, {"natural_exit_code": lambda v: v is None or type(v) is int and v == 0,
                              "forced_termination": lambda v: type(v) is bool,
                              "io_failure": lambda v: v is None}, "appserver_shutdown_failure")
    if shutdown.get("natural_exit_code") is None:
        facts.missing("missing_appserver_natural_exit")
    if shutdown.get("forced_termination") is True:
        facts.missing("appserver_forced_cleanup")
    if shutdown.get("input_eof_sent") is True and shutdown.get("stdin_eof") is False:
        facts.known(False, "appserver_close_input_observation_conflict")
    if "cleanup" in shutdown:
        cleanup = facts.object(shutdown["cleanup"], {"root_exited", "descendants_exited", "reason"},
                               "appserver_cleanup", exact=True)
        _true(facts, cleanup, {"root_exited", "descendants_exited"}, "missing_appserver_job_cleanup")
        facts.observed(cleanup, {"reason": lambda v: type(v) is str and 0 < len(v) <= 512}, "appserver_cleanup_schema")
        if cleanup.get("root_exited") is True and cleanup.get("descendants_exited") is True:
            if "reason" in cleanup:
                facts.known(cleanup["reason"] == "job_empty", "appserver_cleanup_reason_conflict")
    return facts.finish()


def validate_appserver_evidence(snapshot: ReviewSnapshot, project: str, reservation: dict,
                               business: dict, session: dict, shutdown: dict, *, execution_origin: str) -> dict:
    """Independent business, actual Session v1, v2 ownership and shutdown gates.

    input_eof_sent is the DRIVER's successful close_input acknowledgement; actual
    stdin_eof alone (including forced-stop closure) never establishes it. Required
    io_failure/io_threads_stopped preserve sticky deadline and worker observations.
    No process_exit is synthesized; known contradictions dominate all absences.
    """
    def scope():
        # Shared scope checks re-read the current reviewed snapshot and reservation.
        # Absence is not a contradictory None. Inspect only actual present
        # event-scope fields; the independent event gate records missing proof.
        proof = business.get("appserver_event_proof", {}) if type(business) is dict else {}
        native_scope = {}
        if type(proof) is dict:
            native_scope = {key: proof[key] for key in ("run_nonce", "project") if key in proof}
        return _scope(snapshot, project, reservation, native_scope)

    def origin():
        if execution_origin not in ("native_codex", "offline_simulation"):
            raise _EvidenceProblem("failed", "invalid_execution_origin")

    checks, reasons = _run_checks([
        ("scope_bound", scope),
        ("business_verified", lambda: _business_proof(snapshot, project, business)),
        ("session_verified", lambda: _session_proof(snapshot, project, session)),
        ("appserver_events", lambda: _cross_proof(snapshot, project, business, session)),
        ("shutdown_observed", lambda: _shutdown_proof(shutdown)),
        ("execution_origin", origin),
    ])
    status = _status(checks)
    # Reconstruct a whitelist; never copy caller diagnostics, session, DTOs or raw shutdown.
    return {"status": status, "checks": checks, "reasons": reasons,
            "execution_origin": execution_origin if checks["execution_origin"] is True else None,
            "native_evidence_claimed": status == "verified" and execution_origin == "native_codex",
            "transport_kind": TRANSPORT_KIND,
            "transport_count": business.get("transport_count") if type(business) is dict and
                type(business.get("transport_count")) is int else None,
            "business_count": business.get("business_count") if type(business) is dict and
                type(business.get("business_count")) is int else None}
