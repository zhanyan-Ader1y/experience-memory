"""Fail-closed, offline-testable Codex 0.163 exec reduction and C1 evidence checks.

Raw DTOs exist only during bounded decoding. Public summaries contain IDs,
canonical digests and whitelisted proof facts, never handles or result bodies.
These contracts do not authenticate a fabricated but internally consistent log.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
import re

from scripts.codex_c1_bundle import ReviewSnapshot, load_snapshot


MAX_EXEC_LINE_BYTES = 262144
SERVER = "experience_memory_c1_wiring"
TOOLS = ("c1_bootstrap", "memory_status", "memory_status", "memory_status", "memory_status")
KINDS = {"thread_started", "call_started", "call_finished", "turn_completed", "turn_failed",
         "ignored_known", "unexpected_tool"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON number")


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 512 and value == value.strip()
            and not any(char in value for char in ("\r", "\n", "\0", "*", "?")))


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class NativeEvent:
    kind: str
    item_id: str | None = None
    thread_id: str | None = None
    tool: str | None = None
    arguments_sha256: str | None = None
    terminal_status: str | None = None
    outcome: dict | None = None
    outcome_state: str | None = None
    outcome_reason: str | None = None
    result_sha256: str | None = None
    fingerprint: str | None = None


class _EvidenceProblem(ValueError):
    def __init__(self, state, reason):
        super().__init__(reason)
        self.state = state
        self.reason = reason


def _require_fields(value, fields, reason):
    if type(value) is not dict:
        raise _EvidenceProblem("failed", reason)
    if not fields <= value.keys():
        raise _EvidenceProblem("unknown", "missing_" + reason)


def _expect(condition, reason):
    if not condition:
        raise _EvidenceProblem("failed", reason)


def _observed(value, rules, reason):
    """Check present facts before reporting absent ones, so failure dominates unknown."""
    _expect(type(value) is dict, reason)
    for key, rule in rules.items():
        if key in value:
            _expect(rule(value[key]), reason)


def _handle_proof(value):
    _expect(_identifier(value), "invalid_handle")
    return {"session_handle_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "status_arguments_sha256": _digest({"session_handle": value})}


def normalize_c1_body(tool: str, body: dict) -> dict:
    """Shared C1 business whitelist; raw bodies/handles are never returned."""
    if type(body) is not dict:
        raise _EvidenceProblem("failed", "non_object_result")
    if body.get("status") == "failed" or "error_code" in body:
        facts = _Facts()
        facts.object(body, {"status", "error_code"}, "error_result", exact=True)
        facts.observed(body, {"status": lambda v: v == "failed",
                              "error_code": lambda v: v == "SESSION_INVALID"}, "unexpected_business_error")
        facts.finish()
        return {"status": "failed", "error_code": "SESSION_INVALID"}
    if tool == "c1_bootstrap":
        fields = {"status", "session_id", "project_key", "session_handle", "host_source", "backend",
                  "seed_origin", "prior_session", "foreign_project"}
        facts = _Facts()
        facts.object(body, fields, "bootstrap_proof")
        facts.observed(body, {"status": lambda v: v == "ready", "session_id": _identifier,
                              "project_key": _identifier, "session_handle": _identifier,
                              "host_source": lambda v: v == "codex_stdio_0.163.0",
                              "backend": lambda v: v == "in_memory_platform_double",
                              "seed_origin": lambda v: v == "synthetic_not_prior_native_sessions"},
                       "bootstrap_provenance")
        sessions, handles = [body.get("session_id")], [body.get("session_handle")]
        for name in ("prior_session", "foreign_project"):
            if name not in body:
                continue
            seed = facts.object(body[name], {"session_id", "project_key", "session_handle", "owner_status_verified"},
                                "seed_proof")
            facts.observed(seed, {"owner_status_verified": lambda v: v is True, "session_id": _identifier,
                                  "project_key": _identifier, "session_handle": _identifier}, "seed_owner_or_identity")
            sessions.append(seed.get("session_id"))
            handles.append(seed.get("session_handle"))
            if name == "prior_session":
                facts.equal(seed, "project_key", body, "project_key", "seed_project_mismatch")
            elif "project_key" in seed and "project_key" in body:
                facts.known(seed["project_key"] != body["project_key"], "seed_project_mismatch")
        facts.distinct(sessions, "seed_session_not_distinct")
        facts.distinct(handles, "seed_handle_not_distinct")
        facts.finish()
        result = {key: body[key] for key in ("status", "session_id", "project_key", "host_source", "backend", "seed_origin")}
        result.update(_handle_proof(body["session_handle"]))
        for name in ("prior_session", "foreign_project"):
            seed = body[name]
            result[name] = {key: seed[key] for key in ("session_id", "project_key", "owner_status_verified")}
            result[name].update(_handle_proof(seed["session_handle"]))
        return result
    fields = {"succeeded", "session_id", "project_key", "session_handle", "host_source", "u_cursor", "entries"}
    _observed(body, {"succeeded": lambda v: v is True,
                     "u_cursor": lambda v: type(v) is int and v == 0,
                     "entries": lambda v: type(v) is list and not v,
                     "host_source": lambda v: v == "codex_stdio_0.163.0",
                     "session_id": _identifier, "project_key": _identifier, "session_handle": _identifier},
              "status_not_empty_u0")
    _require_fields(body, fields, "status_result")
    _expect(body["succeeded"] is True and type(body["u_cursor"]) is int and body["u_cursor"] == 0
            and type(body["entries"]) is list and not body["entries"], "status_not_empty_u0")
    _expect(_identifier(body["session_id"]) and _identifier(body["project_key"])
            and body["host_source"] == "codex_stdio_0.163.0", "status_identity")
    return {"succeeded": True, "session_id": body["session_id"], "project_key": body["project_key"],
            "host_source": body["host_source"], "u_cursor": 0, "entry_count": 0,
            **_handle_proof(body["session_handle"])}


def _normalize_result(item):
    """Inspect every available channel without treating an absent channel as success."""
    result = item.get("result")
    facts = _Facts()
    try:
        facts.known(item.get("error") is None, "native_tool_error")
        if item["tool"] == "c1_bootstrap":
            facts.known(item["status"] == "completed", "unexpected_bootstrap_terminal")
        if result is None:
            facts.missing("missing_native_result")
            facts.finish()
        envelope = facts.object(result, {"content", "structured_content"}, "native_result_fields")
        body, outcome, parsed = None, None, False
        if "content" in envelope:
            content = envelope["content"]
            valid_shape = (type(content) is list and len(content) == 1 and type(content[0]) is dict
                           and set(content[0]) == {"type", "text"} and content[0].get("type") == "text"
                           and type(content[0].get("text")) is str)
            facts.known(valid_shape, "fixture_content_shape")
            if valid_shape:
                try:
                    body = _json(content[0]["text"])
                    parsed = True
                except (ValueError, RecursionError):
                    facts.missing("unparseable_native_result")
        structured = envelope.get("structured_content")
        if structured is not None:
            facts.capture(lambda: normalize_c1_body(item["tool"], structured))
            if parsed:
                facts.known(_canonical(structured) == _canonical(body), "contradictory_result_channels")
        if parsed:
            outcome = facts.capture(lambda: normalize_c1_body(item["tool"], body))
        for observed_body in ([body] if parsed else []) + ([structured] if structured is not None else []):
            if type(observed_body) is dict:
                if observed_body.get("status") == "failed" or "error_code" in observed_body:
                    facts.known(item["status"] == "failed", "native_result_status_mismatch")
                elif observed_body.get("status") == "ready" or observed_body.get("succeeded") is True:
                    facts.known(item["status"] == "completed", "native_result_status_mismatch")
        facts.finish()
        return outcome, "valid", None, _digest(body)
    except _EvidenceProblem as problem:
        return None, problem.state, problem.reason, _digest(result) if result is not None else None


def decode_exec_line(raw: bytes) -> NativeEvent:
    """Read only the pinned exec JSONL shape, with bounded strict JSON and safe errors."""
    try:
        if type(raw) is not bytes or not raw or len(raw) > MAX_EXEC_LINE_BYTES:
            raise ValueError("exec line size/type")
        value = _json(raw.decode("utf-8"))
        if type(value) is not dict or type(value.get("type")) is not str:
            raise ValueError("exec event object required")
        kind = value["type"]
        if kind == "thread.started":
            if set(value) != {"type", "thread_id"} or not _identifier(value["thread_id"]):
                raise ValueError("invalid thread event")
            return NativeEvent("thread_started", thread_id=value["thread_id"])
        if kind == "turn.started":
            if set(value) != {"type"}:
                raise ValueError("invalid turn start")
            return NativeEvent("ignored_known")
        if kind == "turn.completed":
            usage = value.get("usage")
            required = {"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"}
            if (set(value) != {"type", "usage"} or type(usage) is not dict or not required <= usage.keys()
                    or not set(usage) <= required | {"cache_write_input_tokens"}
                    or any(type(n) is not int or not 0 <= n <= 2**63 - 1 for n in usage.values())):
                raise ValueError("invalid turn usage")
            return NativeEvent("turn_completed")
        if kind in ("turn.failed", "error"):
            error = value.get("error") if kind == "turn.failed" else value
            if type(error) is not dict or type(error.get("message")) is not str:
                raise ValueError("invalid turn error")
            return NativeEvent("turn_failed")
        if kind not in ("item.started", "item.updated", "item.completed"):
            raise ValueError("unknown exec event")
        item = value.get("item")
        if (set(value) != {"type", "item"} or type(item) is not dict
                or not _identifier(item.get("id")) or type(item.get("type")) is not str):
            raise ValueError("invalid item event")
        item_type = item["type"]
        if item_type in ("agent_message", "reasoning"):
            if type(item.get("text")) is not str:
                raise ValueError("missing item text")
            return NativeEvent("ignored_known", item_id=item["id"])
        if item_type == "error":
            if type(item.get("message")) is not str:
                raise ValueError("missing item error")
            return NativeEvent("turn_failed")
        if item_type in ("command_execution", "file_change", "web_search", "collab_tool_call", "todo_list"):
            return NativeEvent("unexpected_tool", item_id=item["id"])
        if item_type != "mcp_tool_call":
            raise ValueError("unknown item type")
        if item.get("server") != SERVER or item.get("tool") not in TOOLS:
            return NativeEvent("unexpected_tool", item_id=item["id"])
        if (type(item.get("arguments")) is not dict or not {"result", "error", "status"} <= item.keys()
                or item["status"] not in ("in_progress", "completed", "failed")):
            raise ValueError("invalid MCP item")
        completed = kind == "item.completed"
        if (completed and item["status"] == "in_progress") or (not completed and
                (item["status"] != "in_progress" or item["result"] is not None or item["error"] is not None)):
            raise ValueError("MCP phase/status mismatch")
        outcome, state, reason, result_hash = _normalize_result(item) if completed else (None, None, None, None)
        return NativeEvent("call_finished" if completed else "call_started", item_id=item["id"],
                           tool=item["tool"], arguments_sha256=_digest(item["arguments"]),
                           terminal_status=item["status"] if completed else None, outcome=outcome,
                           outcome_state=state, outcome_reason=reason, result_sha256=result_hash,
                           fingerprint=_digest(item))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid or unsupported bounded exec event") from None


class NativeRunState:
    """At most five serial unique items; duplicate notifications never add calls."""
    def __init__(self, run_nonce: str, project: str):
        if (type(run_nonce) is not str or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", run_nonce)
                or project not in ("a", "b")):
            raise ValueError("invalid run scope")
        self.run_nonce, self.project = run_nonce, project
        self.thread_id = None
        self.turn_completed = False
        self._started, self._finished = {}, {}
        self._failure_reasons = []

    def _fail(self, reason):
        if reason not in self._failure_reasons:
            self._failure_reasons.append(reason)
        raise ValueError("native sequence rejected: " + reason)

    def consume(self, event: NativeEvent) -> None:
        if self._failure_reasons:
            raise ValueError("native sequence already rejected")
        if not isinstance(event, NativeEvent) or event.kind not in KINDS:
            self._fail("invalid_normalized_event")
        if event.thread_id is not None and self.thread_id is not None and event.thread_id != self.thread_id:
            self._fail("thread_changed")
        if event.kind == "thread_started":
            if not _identifier(event.thread_id) or self.turn_completed:
                self._fail("invalid_thread_start")
            self.thread_id = event.thread_id
            return
        if event.kind == "turn_failed":
            # Keep the observed failure without throwing away its final summary.
            self._failure_reasons.append("native_turn_failed")
            return
        if event.kind == "unexpected_tool":
            self._fail("unexpected_tool")
        if event.kind == "ignored_known":
            return
        if self.thread_id is None:
            self._fail("missing_thread_start")
        if event.kind == "turn_completed":
            if len(self._started) != 5 or len(self._finished) != 5:
                self._fail("premature_turn_completed")
            self.turn_completed = True
            return
        if (not _identifier(event.item_id) or event.tool not in TOOLS or not _sha(event.arguments_sha256)):
            self._fail("invalid_call_identity")
        records = self._started if event.kind == "call_started" else self._finished
        if event.item_id in records:
            if records[event.item_id] != event:
                self._fail("conflicting_duplicate")
            return
        if self.turn_completed:
            self._fail("call_after_turn")
        if event.kind == "call_started":
            if len(self._started) >= 5:
                self._fail("call_budget_exceeded")
            if len(self._started) != len(self._finished):
                self._fail("overlapping_calls")
            if event.tool != TOOLS[len(self._started)]:
                self._fail("tool_order")
            self._started[event.item_id] = copy.deepcopy(event)
        else:
            started = self._started.get(event.item_id)
            if started is None:
                self._fail("completion_without_start")
            if (started.tool != event.tool or started.arguments_sha256 != event.arguments_sha256
                    or event.terminal_status not in ("completed", "failed")):
                self._fail("completion_conflict")
            self._finished[event.item_id] = copy.deepcopy(event)

    def summary(self) -> dict:
        unknown, failures, calls = [], list(self._failure_reasons), []
        if self.thread_id is None:
            unknown.append("missing_native_thread")
        if not self.turn_completed:
            unknown.append("missing_turn_completion")
        if len(self._started) != 5 or len(self._finished) != 5:
            unknown.append("missing_call_completion")
        for step, (item_id, event) in enumerate(self._started.items(), 1):
            finished = self._finished.get(item_id)
            calls.append({"step": step, "item_id": item_id, "tool": event.tool,
                          "arguments_sha256": event.arguments_sha256,
                          "terminal_status": finished.terminal_status if finished else None,
                          "outcome": copy.deepcopy(finished.outcome) if finished else None,
                          "outcome_state": finished.outcome_state if finished else None,
                          "outcome_reason": finished.outcome_reason if finished else None,
                          "result_sha256": finished.result_sha256 if finished else None})
            if finished is not None:
                if finished.outcome_state == "failed":
                    failures.append("native_result_failed")
                elif finished.outcome_state != "valid":
                    unknown.append("missing_native_outcome")
        return {"run_nonce": self.run_nonce, "project": self.project, "native_thread_id": self.thread_id,
                "unique_calls": len(self._started), "completed_calls": len(self._finished),
                "turn_completed": self.turn_completed, "calls": calls,
                "failure_reasons": sorted(set(failures)), "unknown_reasons": sorted(set(unknown))}


TRANSPORT_FIELDS = {"schema_version", "event", "run_nonce", "invocation_id", "time_ns",
                    "transport_status", "session_id", "cwd_sha256"}
BUSINESS_FIELDS = {"schema_version", "event", "run_nonce", "invocation_id", "time_ns", "step",
                   "session_id", "call_id", "tool", "arguments_sha256", "outcome", "result"}


class _Facts:
    """Defer missing proof until every independent available fact was inspected."""
    def __init__(self):
        self.problems = set()

    def capture(self, operation):
        try:
            return operation()
        except _EvidenceProblem as problem:
            self.problems.add((problem.state, problem.reason))
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, OverflowError):
            self.problems.add(("failed", "invalid_evidence_schema"))
        return None

    def known(self, condition, reason):
        if not condition:
            self.problems.add(("failed", reason))

    def missing(self, reason):
        self.problems.add(("unknown", reason))

    def object(self, value, fields, reason, *, exact=False, optional=()):
        if type(value) is not dict:
            self.known(False, reason)
            return {}
        if not fields <= value.keys():
            self.missing("missing_" + reason)
        if exact:
            self.known(set(value) <= fields | set(optional), reason)
        return value

    def observed(self, value, rules, reason):
        if type(value) is dict:
            for key, rule in rules.items():
                if key in value:
                    self.capture(lambda key=key, rule=rule: _expect(rule(value[key]), reason))

    def equal(self, left, left_key, right, right_key, reason):
        if type(left) is dict and type(right) is dict and left_key in left and right_key in right:
            self.capture(lambda: _expect(_canonical(left[left_key]) == _canonical(right[right_key]), reason))

    def distinct(self, values, reason):
        observed = [value for value in values if type(value) is str]
        self.known(len(observed) == len(set(observed)), reason)

    def finish(self, value=None):
        for state in ("failed", "unknown"):
            reasons = sorted(reason for severity, reason in self.problems if severity == state)
            if reasons:
                raise _EvidenceProblem(state, "|".join(reasons))
        return value


def _scope(snapshot, project, reservation, native):
    facts = _Facts()
    _expect(isinstance(snapshot, ReviewSnapshot) and project in ("a", "b"), "invalid_snapshot_scope")
    try:
        current = load_snapshot(snapshot.bundle)
    except (ValueError, OSError, TypeError):
        facts.known(False, "snapshot_not_current")
    else:
        facts.known(current.manifest_sha256 == snapshot.manifest_sha256 and current.run_nonce == snapshot.run_nonce
                    and current.manifest == snapshot.manifest, "snapshot_mismatch")
    required = {"schema_version", "project", "attempt", "state", "run_nonce", "manifest_sha256"}
    reservation = facts.object(reservation, required, "reservation", exact=True)
    facts.observed(reservation, {"schema_version": lambda v: type(v) is int and v == 1,
                                "attempt": lambda v: type(v) is int and v == 1,
                                "state": lambda v: v == "reserved", "project": lambda v: v == project,
                                "run_nonce": lambda v: v == snapshot.run_nonce,
                                "manifest_sha256": lambda v: v == snapshot.manifest_sha256}, "reservation_mismatch")
    native = facts.object(native, {"run_nonce", "project"}, "native_scope")
    facts.observed(native, {"run_nonce": lambda v: v == snapshot.run_nonce,
                           "project": lambda v: v == project}, "native_scope_mismatch")
    return facts.finish(True)


def _native_finished(native):
    facts = _Facts()
    native = facts.object(native, {"turn_completed", "process_exit", "failure_reasons", "unknown_reasons"},
                          "native_finish")
    facts.observed(native, {"process_exit": lambda v: type(v) is int and v == 0,
                            "failure_reasons": lambda v: type(v) is list and not v,
                            "unknown_reasons": lambda v: type(v) is list,
                            "turn_completed": lambda v: type(v) is bool}, "observed_native_failure")
    if native.get("turn_completed") is False or native.get("unknown_reasons"):
        facts.missing("native_uncertainty")
    return facts.finish(True)


def _native_calls(native):
    facts = _Facts()
    native = facts.object(native, {"unique_calls", "completed_calls", "calls"}, "native_calls")
    facts.observed(native, {"unique_calls": lambda v: type(v) is int and 0 <= v <= 5,
                            "completed_calls": lambda v: type(v) is int and 0 <= v <= 5}, "native_call_count")
    unique, completed, calls = native.get("unique_calls"), native.get("completed_calls"), native.get("calls")
    if type(unique) is int and type(completed) is int:
        facts.known(completed <= unique, "native_call_count")
    if "calls" not in native:
        return facts.finish([])
    if type(calls) is not list:
        facts.known(False, "native_calls_schema")
        return facts.finish([])
    facts.known(len(calls) <= 5, "extra_native_calls")
    if type(unique) is int:
        facts.known(len(calls) <= unique, "native_calls_schema_or_count")
        if len(calls) < unique:
            facts.missing("missing_native_calls")
    ids, all_terminal, observed_terminals = [], True, 0
    for step, value in enumerate(calls[:5], 1):
        call = facts.object(value, {"step", "item_id", "tool", "arguments_sha256", "terminal_status"}, "native_call")
        facts.observed(call, {"step": lambda v: type(v) is int and v == step, "item_id": _identifier,
                              "tool": lambda v: v == TOOLS[step - 1], "arguments_sha256": _sha},
                       "native_call_identity")
        ids.append(call.get("item_id"))
        terminal = call.get("terminal_status")
        if terminal is None:
            all_terminal = False
            facts.missing("missing_native_terminal")
        else:
            observed_terminals += terminal in ("completed", "failed")
            facts.known(terminal == ("failed" if step in (3, 4) else "completed"), "native_terminal_mismatch")
    facts.distinct(ids, "native_item_replay")
    if type(completed) is int:
        facts.known(completed >= observed_terminals, "native_completion_below_observed")
    if all_terminal and type(unique) is int and len(calls) == unique and type(completed) is int:
        facts.known(completed == len(calls), "native_completion_count_conflict")
    if len(calls) < 5:
        facts.missing("missing_native_calls")
    return facts.finish(calls)


def _thread(native):
    _require_fields(native, {"native_thread_id"}, "native_thread")
    if native["native_thread_id"] is None:
        raise _EvidenceProblem("unknown", "missing_native_thread")
    _expect(_identifier(native["native_thread_id"]), "native_thread_schema")
    return native["native_thread_id"]


def _audit_rows(rows, event, fields, nonce):
    _expect(type(rows) is list, "audit_rows_schema")
    # Validate all observed rows before treating an incomplete set as unknown.
    seen, missing = set(), False
    optional = {"error_code"} if event == "request_business" else set()
    for row in rows:
        _expect(type(row) is dict, "audit_row_schema")
        if row.get("transport_status") == "rejected" or row.get("event") == "request_business_rejected":
            raise _EvidenceProblem("failed", "observed_audit_rejection")
        _expect(set(row) <= fields | optional, "audit_scope_schema")
        _observed(row, {"schema_version": lambda v: type(v) is int and v == 1,
                        "event": lambda v: v == event, "run_nonce": lambda v: v == nonce,
                        "time_ns": lambda v: type(v) is int and v > 0}, "audit_scope_schema")
        if "invocation_id" in row:
            invocation = row["invocation_id"]
            _expect(type(invocation) is str and re.fullmatch(r"[0-9a-f]{32}", invocation) is not None
                    and invocation not in seen, "audit_replay_or_timestamp")
            seen.add(invocation)
        missing |= not fields <= row.keys()
    _expect(len(rows) <= 5, "extra_audit_rows")
    if missing or len(rows) < 5:
        raise _EvidenceProblem("unknown", "missing_audit_fields_or_rows")
    return rows


def _transport(snapshot, project, native, rows):
    facts = _Facts()
    facts.capture(lambda: _audit_rows(rows, "request_transport", TRANSPORT_FIELDS, snapshot.run_nonce))
    thread = facts.capture(lambda: _thread(native))
    expected_root = snapshot.manifest["projects"][project]["expected_cwd_sha256"]
    for value in rows if type(rows) is list else []:
        row = facts.object(value, TRANSPORT_FIELDS, "transport_fields")
        facts.observed(row, {"transport_status": lambda v: v == "decoded", "session_id": _identifier,
                             "cwd_sha256": lambda v: _sha(v) and v == expected_root},
                       "transport_identity_mismatch")
        if thread is not None:
            facts.observed(row, {"session_id": lambda v: v == thread}, "transport_identity_mismatch")
    facts.finish()
    return sorted(rows, key=lambda row: row["time_ns"])


def _business_rows(snapshot, native, rows):
    facts = _Facts()
    facts.capture(lambda: _audit_rows(rows, "request_business", BUSINESS_FIELDS, snapshot.run_nonce))
    thread = facts.capture(lambda: _thread(native))
    steps, calls = [], []
    for value in rows if type(rows) is list else []:
        row = facts.object(value, BUSINESS_FIELDS, "business_fields")
        facts.observed(row, {"step": lambda v: type(v) is int and 1 <= v <= 5,
                             "call_id": _identifier, "session_id": _identifier,
                             "tool": lambda v: v in TOOLS, "arguments_sha256": _sha}, "business_identity_mismatch")
        if thread is not None:
            facts.observed(row, {"session_id": lambda v: v == thread}, "business_identity_mismatch")
        step = row.get("step")
        if type(step) is int and 1 <= step <= 5:
            facts.observed(row, {"tool": lambda v: v == TOOLS[step - 1]}, "business_identity_mismatch")
            steps.append(str(step))
        calls.append(row.get("call_id"))
    facts.distinct(steps, "business_step_replay")
    facts.distinct(calls, "business_call_replay")
    facts.finish()
    return sorted(rows, key=lambda row: row["step"])


def _bootstrap_summary(value, project_key, foreign_key, thread):
    facts = _Facts()
    fields = {"status", "session_id", "project_key", "host_source", "backend", "seed_origin",
              "session_handle_sha256", "status_arguments_sha256", "prior_session", "foreign_project"}
    value = facts.object(value, fields, "bootstrap_summary", exact=True)
    facts.observed(value, {"status": lambda v: v == "ready", "session_id": _identifier,
                           "project_key": lambda v: v == project_key,
                           "host_source": lambda v: v == "codex_stdio_0.163.0",
                           "backend": lambda v: v == "in_memory_platform_double",
                           "seed_origin": lambda v: v == "synthetic_not_prior_native_sessions",
                           "session_handle_sha256": _sha, "status_arguments_sha256": _sha},
                   "bootstrap_summary_mismatch")
    if thread is not None:
        facts.observed(value, {"session_id": lambda v: v == thread}, "bootstrap_summary_mismatch")
    sessions, handles = [value.get("session_id")], [value.get("session_handle_sha256")]
    seed_fields = {"session_id", "project_key", "owner_status_verified", "session_handle_sha256",
                   "status_arguments_sha256"}
    for name, expected_project in (("prior_session", project_key), ("foreign_project", foreign_key)):
        if name not in value:
            continue  # Parent schema already recorded absence; inspect the other seed independently.
        seed = facts.object(value[name], seed_fields, "seed_summary", exact=True)
        facts.observed(seed, {"owner_status_verified": lambda v: v is True,
                              "session_id": _identifier, "project_key": lambda v: v == expected_project,
                              "session_handle_sha256": _sha, "status_arguments_sha256": _sha},
                       "seed_summary_mismatch")
        sessions.append(seed.get("session_id"))
        handles.append(seed.get("session_handle_sha256"))
    facts.distinct(sessions, "seed_session_not_distinct")
    facts.distinct(handles, "seed_handle_not_distinct")
    return facts.finish(value)


def _status_summary(value, project_key, thread, bootstrap):
    facts = _Facts()
    fields = {"succeeded", "session_id", "project_key", "host_source", "u_cursor", "entry_count",
              "session_handle_sha256", "status_arguments_sha256"}
    value = facts.object(value, fields, "status_summary", exact=True)
    facts.observed(value, {"succeeded": lambda v: v is True, "session_id": _identifier,
                           "project_key": lambda v: v == project_key,
                           "host_source": lambda v: v == "codex_stdio_0.163.0",
                           "u_cursor": lambda v: type(v) is int and v == 0,
                           "entry_count": lambda v: type(v) is int and v == 0,
                           "session_handle_sha256": _sha, "status_arguments_sha256": _sha}, "status_summary_mismatch")
    if thread is not None:
        facts.observed(value, {"session_id": lambda v: v == thread}, "status_summary_mismatch")
    for field in ("session_handle_sha256", "status_arguments_sha256"):
        facts.equal(value, field, bootstrap, field, "status_handle_mismatch")
    return facts.finish(value)


def _error_summary(value):
    facts = _Facts()
    value = facts.object(value, {"status", "error_code"}, "native_error_proof", exact=True)
    facts.observed(value, {"status": lambda v: v == "failed", "error_code": lambda v: v == "SESSION_INVALID"},
                   "expected_rejection_mismatch")
    return facts.finish(value)


def _audit_business_result(row, step, project_key, thread):
    facts = _Facts()
    row = facts.object(row, {"outcome", "result"}, "business_outcome")
    facts.observed(row, {"outcome": lambda v: v in ("succeeded", "expected_error"),
                         "error_code": lambda v: v == "SESSION_INVALID"}, "business_outcome_mismatch")
    if row.get("tool") == "c1_bootstrap":
        facts.observed(row, {"outcome": lambda v: v == "succeeded"}, "unexpected_bootstrap_rejection")
        facts.known("error_code" not in row, "unexpected_bootstrap_rejection")
    result = None
    if "result" in row:
        result = facts.object(row["result"], set(), "business_result")
        success_fields = {"status", "session_id", "project_key", "u_cursor", "entry_count"}
        facts.known(set(result) <= success_fields | {"error_code"}, "business_result_mismatch")
        facts.observed(result, {"status": lambda v: v in ("ready", "succeeded"),
                                "session_id": _identifier, "project_key": lambda v: v == project_key,
                                "u_cursor": lambda v: type(v) is int and v == 0,
                                "entry_count": lambda v: type(v) is int and v == 0,
                                "error_code": lambda v: v == "SESSION_INVALID"}, "business_result_mismatch")
        if "error_code" in result:
            facts.known(set(result) == {"error_code"}, "business_result_mismatch")
        if result.get("status") == "ready":
            facts.known(set(result) <= {"status", "session_id", "project_key"}, "business_result_mismatch")
        if row.get("tool") == "c1_bootstrap":
            facts.observed(result, {"status": lambda v: v == "ready"}, "business_result_mismatch")
            facts.known("error_code" not in result, "unexpected_bootstrap_rejection")
        if thread is not None:
            facts.observed(result, {"session_id": lambda v: v == thread}, "business_result_mismatch")
    if type(step) is not int or not 1 <= step <= 5:
        # No invented step: retain generic present facts; the step schema records absence/invalidity.
        return facts.finish(row)
    expected_error = step in (3, 4)
    facts.observed(row, {"outcome": lambda v: v == ("expected_error" if expected_error else "succeeded")},
                   "business_outcome_mismatch")
    if expected_error:
        facts.object(row, {"error_code"}, "business_error_proof")
        fields, rules = {"error_code"}, {"error_code": lambda v: v == "SESSION_INVALID"}
    else:
        facts.known("error_code" not in row, "unexpected_business_error")
        fields = {"status", "session_id", "project_key"}
        rules = {"status": lambda v: v == ("ready" if step == 1 else "succeeded")}
        if step != 1:
            fields |= {"u_cursor", "entry_count"}
    if result is not None:
        facts.object(result, fields, "business_result", exact=True)
        facts.observed(result, rules, "business_result_mismatch")
    return facts.finish(row)


def _argument_proofs(bootstrap, nonce):
    proofs = {1: _digest({"probe_nonce": nonce})}
    if type(bootstrap) is dict:
        for step, source in ((2, bootstrap), (5, bootstrap), (3, bootstrap.get("prior_session")),
                             (4, bootstrap.get("foreign_project"))):
            if type(source) is dict and "status_arguments_sha256" in source:
                proofs[step] = source["status_arguments_sha256"]
    return proofs


def _business(snapshot, project, native, rows):
    facts = _Facts()
    facts.capture(lambda: _native_calls(native))
    facts.capture(lambda: _business_rows(snapshot, native, rows))
    thread = facts.capture(lambda: _thread(native))
    project_key = "local:" + snapshot.run_nonce + "-" + project
    foreign_key = "local:" + snapshot.run_nonce + "-" + ("b" if project == "a" else "a")
    calls = native.get("calls", []) if type(native) is dict else []
    calls = calls if type(calls) is list else []
    bootstrap = calls[0].get("outcome") if calls and type(calls[0]) is dict else None
    proofs = _argument_proofs(bootstrap, snapshot.run_nonce)
    by_step = {}
    for value in rows if type(rows) is list else []:
        if type(value) is not dict:
            continue  # The row-schema validator already latched this failure.
        step = value.get("step")
        facts.capture(lambda value=value, step=step: _audit_business_result(value, step, project_key, thread))
        if type(step) is int and 1 <= step <= 5:
            by_step[step] = value
            if step in proofs:
                facts.observed(value, {"arguments_sha256": lambda v: v == proofs[step]}, "argument_hash_mismatch")
    for step, value in enumerate(calls[:5], 1):
        call = facts.object(value, {"outcome", "outcome_state", "result_sha256"}, "native_outcome")
        state = call.get("outcome_state")
        facts.observed(call, {"outcome_state": lambda v: v in (None, "valid", "unknown", "failed")},
                       "native_outcome_schema")
        facts.known(state != "failed", "observed_native_result_failure")
        if state is None or state == "unknown":
            facts.missing("missing_native_result_proof")
        if call.get("result_sha256") is None:
            facts.missing("missing_native_result_hash")
        else:
            facts.observed(call, {"result_sha256": _sha}, "native_outcome_schema")
        if step in proofs:
            facts.observed(call, {"arguments_sha256": lambda v: v == proofs[step]}, "argument_hash_mismatch")
        facts.equal(call, "arguments_sha256", by_step.get(step), "arguments_sha256", "argument_hash_mismatch")
        outcome = call.get("outcome")
        if outcome is None:
            facts.missing("missing_native_result_proof")
        elif step == 1:
            facts.capture(lambda: _bootstrap_summary(outcome, project_key, foreign_key, thread))
        elif step in (3, 4):
            facts.capture(lambda: _error_summary(outcome))
        else:
            facts.capture(lambda: _status_summary(outcome, project_key, thread, bootstrap))
    return facts.finish(rows)


def _mapping(snapshot, project, native, transport_rows, outcome_rows):
    facts = _Facts()
    calls = facts.capture(lambda: _native_calls(native))
    transport = facts.capture(lambda: _transport(snapshot, project, native, transport_rows))
    business = facts.capture(lambda: _business_rows(snapshot, native, outcome_rows))
    raw_transport = transport_rows if type(transport_rows) is list else []
    raw_business = outcome_rows if type(outcome_rows) is list else []
    # Replay is observable even if unrelated rows/DTOs are incomplete.
    facts.distinct([row.get("invocation_id") for row in raw_transport + raw_business if type(row) is dict],
                   "cross_layer_audit_replay")
    timed = [row for row in raw_transport if type(row) is dict and type(row.get("time_ns")) is int]
    times = [row["time_ns"] for row in timed]
    facts.known(len(times) == len(set(times)), "ambiguous_serial_mapping")
    anchors = sorted((row["step"], row["time_ns"]) for row in raw_business
                     if type(row) is dict and type(row.get("step")) is int and 1 <= row["step"] <= 5
                     and type(row.get("time_ns")) is int)
    facts.known(all(left[1] < right[1] for left, right in zip(anchors, anchors[1:])),
                "business_timestamp_order")
    facts.known(all(timestamp != boundary for timestamp in times for _, boundary in anchors),
                "partial_serial_rank_impossible")
    # Known business steps bound interval capacity without assigning any missing timestamp a rank.
    # Before B_s there are s transports; between B_s/B_t there are t-s; after B_s there are 5-s.
    bounds = [(0, None)] + anchors + [(5, None)]
    for (left_step, left_time), (right_step, right_time) in zip(bounds, bounds[1:]):
        observed = sum((left_time is None or left_time < timestamp)
                       and (right_time is None or timestamp < right_time) for timestamp in times)
        facts.known(observed <= right_step - left_step, "partial_serial_rank_impossible")
    if len(timed) == 5 and len(raw_transport) == 5:
        timed.sort(key=lambda row: row["time_ns"])
        for row in raw_business:
            if type(row) is not dict:
                continue
            step, timestamp = row.get("step"), row.get("time_ns")
            if type(step) is int and 1 <= step <= 5 and type(timestamp) is int:
                facts.known(timed[step - 1]["time_ns"] < timestamp
                            and (step == 5 or timestamp < timed[step]["time_ns"]), "ambiguous_serial_mapping")
    raw_calls = native.get("calls", []) if type(native) is dict else []
    if type(raw_calls) is list:
        for row in raw_business:
            if type(row) is dict and type(row.get("step")) is int and 1 <= row["step"] <= len(raw_calls):
                call = raw_calls[row["step"] - 1]
                for field in ("step", "tool", "arguments_sha256"):
                    facts.equal(call, field, row, field, "ambiguous_serial_mapping")
    facts.finish()
    return [{"step": call["step"], "item_id": call["item_id"], "call_id": row["call_id"],
             "tool": call["tool"], "arguments_sha256": call["arguments_sha256"],
             "transport_invocation_id": tr["invocation_id"], "business_invocation_id": row["invocation_id"]}
            for call, tr, row in zip(calls, transport, business)]


def validate_business_evidence(snapshot: ReviewSnapshot, project: str, reservation: dict,
                               native: dict, transport_rows: list[dict], outcome_rows: list[dict]) -> dict:
    """Scope/five-call/dual-audit business proof, independent of turn/process exit.

    No exit status or origin is added or assumed. Transport owners must compose
    their own lifecycle and shutdown gates. Returned data is body/handle free.
    """
    checks, reasons, mapping = {}, [], []
    for name, operation in (
        ("scope_bound", lambda: _scope(snapshot, project, reservation, native)),
        ("five_unique_calls", lambda: _native_calls(native)),
        ("transport_linked", lambda: _transport(snapshot, project, native, transport_rows)),
        ("business_verified", lambda: _business(snapshot, project, native, outcome_rows)),
        ("id_mapping", lambda: _mapping(snapshot, project, native, transport_rows, outcome_rows)),
    ):
        try:
            result = operation()
            checks[name] = True
            if name == "id_mapping":
                mapping = result
        except _EvidenceProblem as problem:
            checks[name] = False if problem.state == "failed" else None
            reasons.append(name + ":" + problem.reason)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, OverflowError):
            checks[name] = False
            reasons.append(name + ":invalid_evidence_schema")
    status = ("failed" if any(value is False for value in checks.values()) else
              "verified" if all(value is True for value in checks.values()) else "unknown")
    return {"status": status, "checks": checks, "reasons": reasons,
            "transport_count": len(transport_rows) if type(transport_rows) is list else None,
            "business_count": len(outcome_rows) if type(outcome_rows) is list else None,
            "call_mapping": mapping if checks["id_mapping"] is True else []}


def validate_evidence(snapshot: ReviewSnapshot, project: str, reservation: dict,
                      native: dict, transport_rows: list[dict], outcome_rows: list[dict]) -> dict:
    """Independently check separate transport/business evidence against executor data.

    `native` is executor-owned: add strict integer process_exit and execution_origin
    from the supervisor's actual mode, never from model text, bundle or result DTOs.
    Missing proof is unknown; observed contradictions/rejections are failed. Neither
    this validator nor hashes prove the authenticity of supplied native evidence.
    """
    checks, reasons, mapping = {}, [], []

    def check(name, operation):
        try:
            result = operation()
            checks[name] = True
            return result
        except _EvidenceProblem as problem:
            checks[name] = False if problem.state == "failed" else None
            reasons.append(name + ":" + problem.reason)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, OverflowError):
            checks[name] = False
            reasons.append(name + ":invalid_evidence_schema")
        return None

    business = validate_business_evidence(snapshot, project, reservation, native, transport_rows, outcome_rows)
    # Preserve the exec gate, its original ordering, and the exact public shape.
    checks["scope_bound"] = business["checks"]["scope_bound"]
    reasons.extend(reason for reason in business["reasons"] if reason.startswith("scope_bound:"))
    check("native_finished", lambda: _native_finished(native))
    checks.update({key: value for key, value in business["checks"].items() if key != "scope_bound"})
    reasons.extend(reason for reason in business["reasons"] if not reason.startswith("scope_bound:"))
    mapping = business["call_mapping"]

    def origin():
        _require_fields(native, {"execution_origin"}, "execution_origin")
        _expect(native["execution_origin"] in ("native_codex", "offline_simulation"), "invalid_execution_origin")
        return native["execution_origin"]

    execution_origin = check("execution_origin", origin)
    status = ("failed" if any(value is False for value in checks.values()) else
              "verified" if all(value is True for value in checks.values()) else "unknown")
    return {"status": status, "checks": checks, "reasons": reasons, "execution_origin": execution_origin,
            "transport_count": len(transport_rows) if type(transport_rows) is list else None,
            "business_count": len(outcome_rows) if type(outcome_rows) is list else None,
            "call_mapping": mapping}
