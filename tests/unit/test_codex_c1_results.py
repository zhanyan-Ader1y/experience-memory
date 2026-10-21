import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import codex_c1_results as results_api
from scripts import codex_c1_bundle as bundle_api
from scripts import prepare_codex_c1_review as exporter
from scripts.codex_c1_results import NativeEvent, NativeRunState, decode_exec_line


NONCE = "c1-offline"
SERVER = "experience_memory_c1_wiring"
THREAD = "synthetic-current-session"
# Independently calculated from the literal UTF-8 JSON, not production helpers.
BOOT_HASH = "ddb4118744d15758138906f3e9d7af8e0239ac076cd23fd28efbdb58452abb97"
CURRENT_HASH = "b3eeb6000183fa1770bbe9ba81bfd645571597481092812897900112d53497c3"
PRIOR_HASH = "7559104e23d47d72f5513566429ebfaa0cee3532651c0f5b387a34972af2b703"
FOREIGN_HASH = "c3c39708d30eef8359a67232614ccbeeb917c9bf901fa93a5a48b23a2371e4a3"


def bootstrap_body():
    return {
        "status": "ready", "session_id": THREAD, "project_key": "local:c1-offline-a",
        "session_handle": "synthetic-current-handle", "host_source": "codex_stdio_0.163.0",
        "backend": "in_memory_platform_double", "seed_origin": "synthetic_not_prior_native_sessions",
        "prior_session": {"session_id": "synthetic-prior-session", "project_key": "local:c1-offline-a",
                          "session_handle": "synthetic-prior-handle", "owner_status_verified": True},
        "foreign_project": {"session_id": "synthetic-foreign-session", "project_key": "local:c1-offline-b",
                            "session_handle": "synthetic-foreign-handle", "owner_status_verified": True},
    }


def status_body():
    return {"succeeded": True, "session_handle": "synthetic-current-handle",
            "session_id": THREAD, "project_key": "local:c1-offline-a", "state": "active",
            "generation": 1, "u_cursor": 0, "entries": [], "native_compaction": "not_verified",
            "host_source": "codex_stdio_0.163.0"}


def raw_call(step=1, *, completed=False, body=None, status=None):
    arguments = ({"probe_nonce": NONCE} if step == 1 else
                 {"session_handle": {3: "synthetic-prior-handle", 4: "synthetic-foreign-handle"}.get(
                     step, "synthetic-current-handle")})
    if completed and body is None:
        body = (bootstrap_body() if step == 1 else
                {"status": "failed", "error_code": "SESSION_INVALID"} if step in (3, 4) else status_body())
    return {"type": "item.completed" if completed else "item.started", "item": {
        "id": "exec-item-" + str(step), "type": "mcp_tool_call", "server": SERVER,
        "tool": "c1_bootstrap" if step == 1 else "memory_status", "arguments": arguments,
        "status": status or ("failed" if step in (3, 4) else "completed") if completed else "in_progress",
        "result": {"content": [{"type": "text", "text": json.dumps(body)}],
                   "structured_content": None} if completed else None,
        "error": None,
    }}


def decode(value):
    return decode_exec_line(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def turn_end():
    return {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0,
            "cache_write_input_tokens": 0, "output_tokens": 1, "reasoning_output_tokens": 0}}


class EventDecoderTests(unittest.TestCase):
    def test_exact_raw_type_maps_and_canonical_hash_is_literal(self):
        raw = raw_call()
        event = decode(raw)
        self.assertEqual((event.kind, event.item_id, event.tool, event.arguments_sha256),
                         ("call_started", "exec-item-1", "c1_bootstrap", BOOT_HASH))
        self.assertEqual(decode({"type": "thread.started", "thread_id": THREAD}).thread_id, THREAD)
        self.assertEqual(decode(turn_end()).kind, "turn_completed")
        with self.assertRaises(FrozenInstanceError):
            event.kind = "turn_completed"

    def test_actual_text_result_normalizes_without_exposing_handles_or_content(self):
        event = decode(raw_call(completed=True))
        self.assertEqual(event.outcome_state, "valid")
        self.assertEqual(event.outcome["prior_session"]["owner_status_verified"], True)
        self.assertEqual(event.outcome["status_arguments_sha256"], CURRENT_HASH)
        self.assertEqual(event.outcome["prior_session"]["status_arguments_sha256"], PRIOR_HASH)
        self.assertEqual(event.outcome["foreign_project"]["status_arguments_sha256"], FOREIGN_HASH)
        serialized = json.dumps(event.outcome)
        self.assertNotIn("synthetic-current-handle", serialized)
        self.assertNotIn('"content"', serialized)
        self.assertRegex(event.result_sha256, r"^[0-9a-f]{64}$")

    def test_expected_business_error_survives_native_failed_terminal(self):
        event = decode(raw_call(3, completed=True))
        self.assertEqual(event.terminal_status, "failed")
        self.assertEqual(event.outcome_state, "valid")
        self.assertEqual(event.outcome, {"status": "failed", "error_code": "SESSION_INVALID"})

    def test_structured_content_must_agree_with_fixture_text(self):
        raw = raw_call(completed=True)
        raw["item"]["result"]["structured_content"] = bootstrap_body()
        self.assertEqual(decode(raw).outcome_state, "valid")
        raw["item"]["result"]["structured_content"]["backend"] = "other"
        self.assertEqual(decode(raw).outcome_state, "failed")

    def test_missing_result_unknown_but_false_proof_observed_failure(self):
        raw = raw_call(completed=True)
        raw["item"]["result"] = None
        self.assertEqual(decode(raw).outcome_state, "unknown")
        body = bootstrap_body()
        del body["prior_session"]["owner_status_verified"]
        self.assertEqual(decode(raw_call(completed=True, body=body)).outcome_state, "unknown")
        body["prior_session"]["owner_status_verified"] = False
        self.assertEqual(decode(raw_call(completed=True, body=body)).outcome_state, "failed")

    def test_strict_raw_decoder_rejects_unknown_duplicate_invalid_and_oversized(self):
        for raw in (b'{"type":"new.event"}', b'{"type":"turn.started","type":"turn.started"}',
                    b'{', b'[]', b'\xff', b'x' * (262144 + 1),
                    b'{"type":"turn.started","extra":NaN}',
                    b'{"type":"thread.started"}', b'{"type":"turn.completed","usage":{}}'):
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(ValueError):
                    decode_exec_line(raw)

    def test_out_of_scope_actions_not_ignored_and_model_marker_is_not_evidence(self):
        raw = raw_call()
        for field, value in (("tool", "memory_write"), ("server", "unreviewed_server")):
            changed = copy.deepcopy(raw)
            changed["item"][field] = value
            self.assertEqual(decode(changed).kind, "unexpected_tool")
        for item_type in ("command_execution", "file_change", "web_search", "collab_tool_call"):
            self.assertEqual(decode({"type": "item.started", "item": {
                "id": "out-of-scope", "type": item_type}}).kind, "unexpected_tool")
        self.assertEqual(decode({"type": "item.completed", "item": {
            "id": "answer", "type": "agent_message", "text": "C1_WIRING_DONE"}}).kind, "ignored_known")
        with self.assertRaises(ValueError):
            decode({"type": "item.completed", "item": {"id": "future", "type": "future_tool"}})

    def test_observed_false_fact_is_not_masked_by_other_missing_native_proofs(self):
        body = bootstrap_body()
        del body["foreign_project"]["owner_status_verified"]
        body["backend"] = "untrusted"
        self.assertEqual(decode(raw_call(completed=True, body=body)).outcome_state, "failed")
        body = status_body()
        del body["host_source"]
        body["u_cursor"] = True
        self.assertEqual(decode(raw_call(2, completed=True, body=body)).outcome_state, "failed")

    def test_non_whitelisted_error_code_never_escapes_in_normalized_summary(self):
        raw = raw_call(3, completed=True, body={"status": "failed", "error_code": "raw-secret-body"})
        event = decode(raw)
        self.assertEqual(event.outcome_state, "failed")
        self.assertNotIn("raw-secret-body", repr(event))

    def test_review_i1_missing_envelope_fields_do_not_hide_raw_result_failures(self):
        for case in ("text_false_owner", "structured_false_owner", "multiple_text_blocks",
                     "failed_terminal_with_partial_success", "failed_bootstrap_without_result",
                     "error_extra_missing_code", "wrong_error_missing_status",
                     "error_code_success_terminal_missing_status", "structured_error_success_terminal"):
            raw = raw_call(2 if case == "failed_terminal_with_partial_success" else 1, completed=True)
            if case in ("text_false_owner", "structured_false_owner"):
                body = bootstrap_body()
                body["prior_session"]["owner_status_verified"] = False
                if case == "text_false_owner":
                    raw["item"]["result"]["content"][0]["text"] = json.dumps(body)
                    raw["item"]["result"].pop("structured_content")
                else:
                    raw["item"]["result"] = {"structured_content": body}
            elif case == "multiple_text_blocks":
                raw["item"]["result"]["content"] *= 2
                raw["item"]["result"].pop("structured_content")
            elif case == "failed_terminal_with_partial_success":
                body = status_body()
                body.pop("host_source")
                raw = raw_call(2, completed=True, body=body, status="failed")
            elif case == "error_extra_missing_code":
                raw = raw_call(3, completed=True, body={"status": "failed", "body": "raw-secret-body"})
            elif case == "wrong_error_missing_status":
                raw = raw_call(3, completed=True, body={"error_code": "INTERNAL_ERROR"})
            elif case == "error_code_success_terminal_missing_status":
                raw = raw_call(2, completed=True, body={"error_code": "SESSION_INVALID"})
            elif case == "structured_error_success_terminal":
                raw = raw_call(2, completed=True)
                raw["item"]["result"] = {"structured_content": {"status": "failed", "error_code": "SESSION_INVALID"}}
            else:
                raw["item"]["status"] = "failed"
                raw["item"]["result"] = None
            with self.subTest(case=case):
                self.assertEqual(decode(raw).outcome_state, "failed")
        raw = raw_call(3, completed=True, body={"error_code": "SESSION_INVALID"})
        self.assertEqual(decode(raw).outcome_state, "unknown")
        for missing in ("content", "structured_content"):
            raw = raw_call(completed=True)
            raw["item"]["result"]["structured_content"] = bootstrap_body()
            raw["item"]["result"].pop(missing)
            with self.subTest(absent_only=missing):
                self.assertEqual(decode(raw).outcome_state, "unknown")

    def test_parameter_object_canonicalization_and_missing_arguments(self):
        raw = raw_call(2)
        raw["item"]["arguments"] = {"z": "中文", "a": 1}
        event = decode(raw)
        # A hand-written canonical byte sequence is independent of the implementation.
        self.assertEqual(event.arguments_sha256,
                         hashlib.sha256('{"a":1,"z":"中文"}'.encode("utf-8")).hexdigest())
        for arguments in (None, [], "{}"):
            raw["item"]["arguments"] = arguments
            with self.assertRaises(ValueError):
                decode(raw)
        del raw["item"]["arguments"]
        with self.assertRaises(ValueError):
            decode(raw)


class NativeRunStateTests(unittest.TestCase):
    def state(self):
        state = NativeRunState(NONCE, "a")
        state.consume(NativeEvent("thread_started", thread_id=THREAD))
        return state

    def test_started_completed_same_item_counts_once_with_idempotent_repeats(self):
        state = self.state()
        started = NativeEvent("call_started", item_id="item-1", tool="c1_bootstrap", arguments_sha256="a" * 64)
        ended = replace(started, kind="call_finished", terminal_status="completed")
        for event in (started, started, ended, ended):
            state.consume(event)
        self.assertEqual(state.summary()["unique_calls"], 1)
        self.assertEqual(state.summary()["completed_calls"], 1)
        self.assertFalse(state.summary()["turn_completed"])

    def test_different_items_same_tool_are_counted_and_sixth_is_latched_failure(self):
        state = self.state()
        for step in range(1, 6):
            state.consume(decode(raw_call(step)))
            state.consume(decode(raw_call(step, completed=True)))
        self.assertEqual(state.summary()["unique_calls"], 5)
        self.assertEqual(state.summary()["completed_calls"], 5)
        extra = raw_call(6)
        with self.assertRaises(ValueError):
            state.consume(decode(extra))
        self.assertTrue(state.summary()["failure_reasons"])
        with self.assertRaises(ValueError):
            state.consume(decode(turn_end()))

    def test_conflicting_repeat_and_interleaved_calls_rejected(self):
        state = self.state()
        state.consume(decode(raw_call()))
        conflict = raw_call()
        conflict["item"]["arguments"]["probe_nonce"] = "c1-other"
        with self.assertRaises(ValueError):
            state.consume(decode(conflict))
        state = self.state()
        state.consume(decode(raw_call()))
        with self.assertRaises(ValueError):
            state.consume(decode(raw_call(2)))

    def test_completion_without_start_and_wrong_thread_and_early_turn_rejected(self):
        for event in (decode(raw_call(completed=True)), decode(turn_end()),
                      NativeEvent("thread_started", thread_id="other-thread"),
                      replace(decode(raw_call()), thread_id="other-thread")):
            with self.subTest(event=event.kind):
                state = self.state()
                with self.assertRaises(ValueError):
                    state.consume(event)
                self.assertTrue(state.summary()["failure_reasons"])

    def test_missing_completion_is_unknown_not_success_and_summary_is_detached(self):
        state = self.state()
        state.consume(decode(raw_call()))
        summary = state.summary()
        self.assertTrue(summary["unknown_reasons"])
        summary["calls"].clear()
        self.assertEqual(len(state.summary()["calls"]), 1)
        marker = decode({"type": "item.completed", "item": {
            "id": "marker", "type": "agent_message", "text": "C1_WIRING_DONE"}})
        state.consume(marker)
        self.assertFalse(state.summary()["turn_completed"])

    def test_full_native_sequence_separates_business_errors_from_turn_failure(self):
        state = self.state()
        for step in range(1, 6):
            state.consume(decode(raw_call(step)))
            state.consume(decode(raw_call(step, completed=True)))
        state.consume(decode(turn_end()))
        summary = state.summary()
        self.assertTrue(summary["turn_completed"])
        self.assertEqual(summary["failure_reasons"], [])
        self.assertEqual(summary["unknown_reasons"], [])
        self.assertEqual([call["step"] for call in summary["calls"]], [1, 2, 3, 4, 5])
        self.assertNotIn("synthetic-current-handle", json.dumps(summary))

    def test_turn_failed_sanitizes_message_and_prevents_more_calls(self):
        state = self.state()
        state.consume(decode({"type": "turn.failed", "error": {"message": "raw-secret-body"}}))
        self.assertTrue(state.summary()["failure_reasons"])
        self.assertNotIn("raw-secret-body", json.dumps(state.summary()))
        with self.assertRaises(ValueError):
            state.consume(decode(raw_call()))


class EvidenceValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.temp.name).resolve() / "bundle"
        exporter.prepare(cls.bundle, NONCE)
        cls.snapshot = bundle_api.load_snapshot(cls.bundle)
        approval = {"schema_version": 1, "scope": "offline-simulation", "run_nonce": NONCE,
                    "manifest_sha256": cls.snapshot.manifest_sha256, "projects": ["a", "b"],
                    "max_attempts_total": 2, "max_attempts_per_project": 1,
                    "max_tool_calls_per_task": 5, "task_timeout_seconds": 180,
                    "actions": ["install_configs", "run_project"],
                    "user_confirmation_reference": "synthetic-unit-test-authorization"}
        bundle_api.install_configs(cls.snapshot, approval)
        cls.reservation = bundle_api.reserve_attempt(cls.snapshot, "a", approval)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def material(self, bodies=None, terminals=None):
        state = NativeRunState(NONCE, "a")
        state.consume(decode({"type": "thread.started", "thread_id": THREAD}))
        for step in range(1, 6):
            state.consume(decode(raw_call(step)))
            state.consume(decode(raw_call(step, completed=True, body=(bodies or {}).get(step),
                                          status=(terminals or {}).get(step))))
        state.consume(decode(turn_end()))
        native = state.summary()
        native.update(process_exit=0, execution_origin="offline_simulation")
        transport, business = [], []
        hashes = [BOOT_HASH, CURRENT_HASH, PRIOR_HASH, FOREIGN_HASH, CURRENT_HASH]
        for step in range(1, 6):
            transport.append({"schema_version": 1, "event": "request_transport", "run_nonce": NONCE,
                              "invocation_id": f"{step:032x}", "time_ns": step * 100,
                              "transport_status": "decoded", "session_id": THREAD,
                              "cwd_sha256": self.snapshot.manifest["projects"]["a"]["expected_cwd_sha256"]})
            result = ({"status": "ready", "session_id": THREAD, "project_key": "local:c1-offline-a"}
                      if step == 1 else {"error_code": "SESSION_INVALID"} if step in (3, 4) else
                      {"status": "succeeded", "session_id": THREAD, "project_key": "local:c1-offline-a",
                       "u_cursor": 0, "entry_count": 0})
            row = {"schema_version": 1, "event": "request_business", "run_nonce": NONCE,
                   "invocation_id": f"{step + 5:032x}", "time_ns": step * 100 + 10, "step": step,
                   "session_id": THREAD, "call_id": "metadata-call-" + str(step),
                   "tool": "c1_bootstrap" if step == 1 else "memory_status",
                   "arguments_sha256": hashes[step - 1],
                   "outcome": "expected_error" if step in (3, 4) else "succeeded", "result": result}
            if step in (3, 4):
                row["error_code"] = "SESSION_INVALID"
            business.append(row)
        return native, transport, business

    def validate(self, native=None, transport=None, business=None, reservation=None, project="a"):
        defaults = self.material()
        return results_api.validate_evidence(
            self.snapshot, project, copy.deepcopy(self.reservation if reservation is None else reservation),
            defaults[0] if native is None else native,
            defaults[1] if transport is None else transport,
            defaults[2] if business is None else business)

    def test_five_of_each_and_distinct_id_spaces_verify_with_explicit_mapping(self):
        native, transport, business = self.material()
        # File enumeration order is not call order; timestamps/steps define the serial join.
        report = self.validate(native, list(reversed(transport)), list(reversed(business)))
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["execution_origin"], "offline_simulation")
        self.assertEqual(report["transport_count"], 5)
        self.assertEqual(report["business_count"], 5)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual([(m["item_id"], m["call_id"]) for m in report["call_mapping"]],
                         [("exec-item-" + str(i), "metadata-call-" + str(i)) for i in range(1, 6)])
        self.assertNotIn("synthetic-current-handle", json.dumps(report))
        self.assertNotIn('"content"', json.dumps(report))

    def test_five_transport_without_business_is_unknown_not_verified(self):
        report = self.validate(business=[])
        self.assertEqual(report["status"], "unknown")
        self.assertIsNone(report["checks"]["business_verified"])
        self.assertEqual(report["business_count"], 0)

    def test_replay_nonce_thread_root_session_and_manifest_hash_are_failures(self):
        for layer, field, wrong in (
            (0, "run_nonce", "c1-old"), (0, "native_thread_id", "other-thread"),
            (1, "run_nonce", "c1-old"), (1, "session_id", "other-thread"), (1, "cwd_sha256", "0" * 64),
            (2, "run_nonce", "c1-old"), (2, "session_id", "other-thread"),
        ):
            material = self.material()
            target = material[0] if layer == 0 else material[layer][0]
            target[field] = wrong
            with self.subTest(layer=layer, field=field):
                self.assertEqual(self.validate(*material)["status"], "failed")
        for field, wrong in (("run_nonce", "c1-old"), ("manifest_sha256", "0" * 64),
                             ("project", "b"), ("attempt", True), ("state", "verified")):
            reservation = copy.deepcopy(self.reservation)
            reservation[field] = wrong
            with self.subTest(reservation=field):
                self.assertEqual(self.validate(reservation=reservation)["status"], "failed")

    def test_native_zero_exit_and_counts_are_strict_not_python_truthiness(self):
        for field, value in (("process_exit", True), ("process_exit", False), ("process_exit", 1),
                             ("turn_completed", 1),
                             ("unique_calls", True), ("unique_calls", 6), ("completed_calls", 4)):
            native, transport, business = self.material()
            native[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native, _, _ = self.material()
        del native["process_exit"]
        self.assertEqual(self.validate(native=native)["status"], "unknown")

    def test_missing_native_notifications_remain_unknown_not_observed_failure(self):
        _, transport, business = self.material()
        for completed_steps in (0, 4, 5):
            state = NativeRunState(NONCE, "a")
            state.consume(decode({"type": "thread.started", "thread_id": THREAD}))
            for step in range(1, completed_steps + 1):
                state.consume(decode(raw_call(step)))
                state.consume(decode(raw_call(step, completed=True)))
            native = state.summary()
            native.update(process_exit=0, execution_origin="offline_simulation")
            with self.subTest(completed_steps=completed_steps):
                self.assertEqual(self.validate(native, transport, business)["status"], "unknown")
        state = NativeRunState(NONCE, "a")
        state.consume(decode({"type": "thread.started", "thread_id": THREAD}))
        state.consume(decode(raw_call()))
        native = state.summary()
        native.update(process_exit=0, execution_origin="offline_simulation")
        self.assertEqual(self.validate(native, transport, business)["status"], "unknown")

    def test_native_incomplete_evidence_is_unknown_even_with_all_audits(self):
        for remove in ("calls", "native_thread_id", "turn_completed"):
            native, transport, business = self.material()
            del native[remove]
            with self.subTest(remove=remove):
                self.assertEqual(self.validate(native, transport, business)["status"], "unknown")
        native, transport, business = self.material()
        native["calls"][0]["outcome"] = None
        native["calls"][0]["outcome_state"] = "unknown"
        self.assertEqual(self.validate(native, transport, business)["status"], "unknown")

    def test_status_true_and_integer_zero_enforced_on_native_and_audit(self):
        for field, value in (("succeeded", 1), ("succeeded", "true"), ("u_cursor", True),
                             ("u_cursor", 1), ("entries", ["synthetic-body"])):
            body = status_body()
            body[field] = value
            with self.subTest(native=field, value=value):
                self.assertEqual(self.validate(*self.material({2: body}))["status"], "failed")
        for field, value in (("u_cursor", False), ("entry_count", False), ("u_cursor", 1), ("entry_count", 1)):
            native, transport, business = self.material()
            business[1]["result"][field] = value
            with self.subTest(audit=field, value=value):
                self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_bootstrap_backend_owner_distinctness_and_project_proofs_not_in_audit_are_required(self):
        for mutation in ("backend", "seed_origin", "false_owner", "numeric_owner", "same_handle",
                         "same_session", "prior_project", "foreign_project", "current_project"):
            body = bootstrap_body()
            if mutation in ("backend", "seed_origin"):
                body[mutation] = "untrusted"
            elif mutation in ("false_owner", "numeric_owner"):
                body["prior_session"]["owner_status_verified"] = False if mutation == "false_owner" else 1
            elif mutation == "same_handle":
                body["foreign_project"]["session_handle"] = body["prior_session"]["session_handle"]
            elif mutation == "same_session":
                body["foreign_project"]["session_id"] = body["prior_session"]["session_id"]
            elif mutation == "prior_project":
                body["prior_session"]["project_key"] = "local:c1-offline-b"
            elif mutation == "foreign_project":
                body["foreign_project"]["project_key"] = body["project_key"]
            else:
                body["project_key"] = "local:c1-other-a"
                body["prior_session"]["project_key"] = body["project_key"]
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(*self.material({1: body}))["status"], "failed")
        body = bootstrap_body()
        del body["foreign_project"]["owner_status_verified"]
        self.assertEqual(self.validate(*self.material({1: body}))["status"], "unknown")

    def test_bootstrap_error_is_observed_failure_not_missing_owner_proof(self):
        material = self.material({1: {"status": "failed", "error_code": "SESSION_INVALID"}}, {1: "failed"})
        self.assertEqual(self.validate(*material)["status"], "failed")
        native, transport, business = self.material()
        del native["calls"][0]["outcome"]["prior_session"]["session_id"]
        native["calls"][0]["outcome"]["prior_session"]["owner_status_verified"] = False
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_missing_expected_error_audit_proof_is_unknown(self):
        for field in ("error_code", "result_code"):
            native, transport, business = self.material()
            if field == "result_code":
                del business[2]["result"]["error_code"]
            else:
                del business[2]["error_code"]
            with self.subTest(field=field):
                self.assertEqual(self.validate(native, transport, business)["status"], "unknown")

    def test_unexpected_error_and_step_three_success_do_not_verify(self):
        for body in ({"status": "failed", "error_code": "INTERNAL_ERROR"}, status_body()):
            self.assertEqual(self.validate(*self.material({3: body}))["status"], "failed")
        native, transport, business = self.material()
        business[2]["error_code"] = "OTHER_ERROR"
        business[2]["result"] = {"error_code": "OTHER_ERROR"}
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_argument_hash_join_catches_reused_wrong_handle_and_wrong_nonce(self):
        for step in (1, 2, 3, 4, 5):
            native, transport, business = self.material()
            native["calls"][step - 1]["arguments_sha256"] = "a" * 64
            business[step - 1]["arguments_sha256"] = "a" * 64
            with self.subTest(step=step):
                self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native, transport, business = self.material()
        business[2]["arguments_sha256"] = CURRENT_HASH
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_reused_invocations_call_ids_item_ids_and_cross_layer_ids_rejected(self):
        for mutation in ("business", "transport", "cross_layer", "call_id", "item_id", "step"):
            native, transport, business = self.material()
            if mutation == "business":
                business[1]["invocation_id"] = business[0]["invocation_id"]
            elif mutation == "transport":
                transport[1]["invocation_id"] = transport[0]["invocation_id"]
            elif mutation == "cross_layer":
                business[0]["invocation_id"] = transport[0]["invocation_id"]
            elif mutation == "call_id":
                business[1]["call_id"] = business[0]["call_id"]
            elif mutation == "item_id":
                native["calls"][1]["item_id"] = native["calls"][0]["item_id"]
            else:
                business[1]["step"] = 1
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_serial_mapping_must_be_unambiguous_with_strict_timestamps(self):
        for mutation in ("same_time", "overlap", "business_reorder", "native_step", "tool", "unknown_field"):
            native, transport, business = self.material()
            if mutation == "same_time":
                transport[1]["time_ns"] = transport[0]["time_ns"]
            elif mutation == "overlap":
                transport[1]["time_ns"] = business[0]["time_ns"] - 1
            elif mutation == "business_reorder":
                business[2]["time_ns"] = 50
            elif mutation == "native_step":
                native["calls"][2]["step"] = 2
            elif mutation == "tool":
                business[1]["tool"] = "c1_bootstrap"
            else:
                business[0]["body"] = "raw-secret-body"
            with self.subTest(mutation=mutation):
                report = self.validate(native, transport, business)
                self.assertEqual(report["status"], "failed")
                self.assertNotIn("raw-secret-body", json.dumps(report))

    def test_missing_fields_are_unknown_while_extra_rejections_or_rows_are_failed(self):
        for layer, field in ((1, "cwd_sha256"), (1, "invocation_id"), (2, "arguments_sha256"),
                             (2, "result")):
            material = self.material()
            del material[layer][0][field]
            with self.subTest(layer=layer, field=field):
                self.assertEqual(self.validate(*material)["status"], "unknown")
        for layer in (1, 2):
            material = self.material()
            material[layer].append(copy.deepcopy(material[layer][0]))
            self.assertEqual(self.validate(*material)["status"], "failed")
        native, transport, business = self.material()
        transport[0] = {"schema_version": 1, "event": "request_transport", "run_nonce": NONCE,
                        "invocation_id": "1" * 32, "time_ns": 100, "transport_status": "rejected",
                        "error_code": "HOST_SOURCE_UNVERIFIED"}
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_proof_summary_cannot_be_forged_by_removing_unknown_flag(self):
        native, transport, business = self.material()
        del native["calls"][0]["outcome"]["prior_session"]["owner_status_verified"]
        self.assertEqual(self.validate(native, transport, business)["status"], "unknown")
        native, transport, business = self.material()
        native["calls"][0]["outcome"]["prior_session"]["owner_status_verified"] = False
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_execution_origin_is_explicit_executor_input_never_inferred(self):
        native, transport, business = self.material()
        del native["execution_origin"]
        self.assertEqual(self.validate(native, transport, business)["status"], "unknown")
        native["execution_origin"] = "model_says_native"
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native["execution_origin"] = "native_codex"
        self.assertEqual(self.validate(native, transport, business)["execution_origin"], "native_codex")

    def test_observed_replay_and_exit_failure_dominate_missing_fields(self):
        native, transport, business = self.material()
        transport[0]["run_nonce"] = "c1-old"
        del transport[0]["cwd_sha256"]
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native, transport, business = self.material()
        business[1]["invocation_id"] = business[0]["invocation_id"]
        del business[1]["result"]
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native, transport, business = self.material()
        native["process_exit"] = 7
        del native["turn_completed"]
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        body = bootstrap_body()
        del body["foreign_project"]["owner_status_verified"]
        body["backend"] = "untrusted"
        self.assertEqual(self.validate(*self.material({1: body}))["status"], "failed")

    def test_review_i1_missing_proof_cannot_mask_independent_failures(self):
        def no_bootstrap(native):
            native["calls"][0].update(outcome=None, outcome_state="unknown", result_sha256=None,
                                      outcome_reason="missing_native_result")
            native["unknown_reasons"] = ["missing_native_outcome"]

        cases = [
            ("review_wrong_root_other_missing_invocation", lambda n, t, b, r: (
                t[0].update(cwd_sha256="0" * 64), t[1].pop("invocation_id"))),
            ("review_missing_bootstrap_step3_succeeded", lambda n, t, b, r: (
                no_bootstrap(n), b[2].update(outcome="succeeded"))),
            ("review_attempt2_missing_state", lambda n, t, b, r: (r.update(attempt=2), r.pop("state"))),
            ("review_bootstrap_failed_missing_body", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][0].update(terminal_status="failed"))),
            ("reservation_nonce_missing_hash", lambda n, t, b, r: (
                r.update(run_nonce="c1-old"), r.pop("manifest_sha256"))),
            ("native_count6_missing_calls", lambda n, t, b, r: (
                n.update(unique_calls=6), n.pop("calls"))),
            ("native_completed6_missing_unique", lambda n, t, b, r: (
                n.update(completed_calls=6), n.pop("unique_calls"))),
            ("native_duplicate_other_missing_argument", lambda n, t, b, r: (
                n["calls"][0].pop("arguments_sha256"),
                n["calls"][4].update(item_id=n["calls"][3]["item_id"]))),
            ("native_step3_completed_missing_bootstrap", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][2].update(terminal_status="completed"))),
            ("native_step5_failed_proof_missing_bootstrap", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][4].update(outcome_state="failed"))),
            ("native_status_u_true_missing_bootstrap", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][1]["outcome"].update(u_cursor=True))),
            ("native_status_foreign_session_missing_bootstrap", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][1]["outcome"].update(session_id="another-session"))),
            ("transport_wrong_thread_other_missing_root", lambda n, t, b, r: (
                t[4].update(session_id="another-session"), t[0].pop("cwd_sha256"))),
            ("business_wrong_error_missing_bootstrap", lambda n, t, b, r: (
                no_bootstrap(n), b[3].update(error_code="INTERNAL_ERROR"))),
            ("business_u_true_missing_native_status", lambda n, t, b, r: (
                no_bootstrap(n), n["calls"][1].update(outcome=None, outcome_state="unknown"),
                b[1]["result"].update(u_cursor=True))),
            ("business_wrong_session_other_missing_invocation", lambda n, t, b, r: (
                b[4].update(session_id="another-session"), b[0].pop("invocation_id"))),
            ("business_bootstrap_extra_fields_missing_step", lambda n, t, b, r: (
                no_bootstrap(n), b[0].pop("step"), b[0]["result"].update(u_cursor=0))),
            ("business_known_bootstrap_rejection_missing_step", lambda n, t, b, r: (
                no_bootstrap(n), b[0].pop("tool"), b[2].pop("step"), b[2].update(tool="c1_bootstrap"))),
            ("business_result_u_true_missing_step", lambda n, t, b, r: (
                no_bootstrap(n), b[1].pop("step"), b[1]["result"].update(u_cursor=True))),
            ("business_result_foreign_project_missing_step", lambda n, t, b, r: (
                no_bootstrap(n), b[1].pop("step"), b[1]["result"].update(project_key="local:c1-offline-b"))),
            ("business_result_error_other_missing_step", lambda n, t, b, r: (
                no_bootstrap(n), b[2].pop("step"), b[2]["result"].update(error_code="INTERNAL_ERROR"))),
            ("business_duplicate_call_other_missing_tool", lambda n, t, b, r: (
                b[0].pop("tool"), b[4].update(call_id=b[3]["call_id"]))),
            ("cross_layer_replay_missing_native_calls", lambda n, t, b, r: (
                n.pop("calls"), b[4].update(invocation_id=t[0]["invocation_id"]))),
            ("cross_layer_replay_other_missing_business_result", lambda n, t, b, r: (
                b[0].pop("result"), b[4].update(invocation_id=t[0]["invocation_id"]))),
            ("serial_overlap_other_missing_native_hash", lambda n, t, b, r: (
                n["calls"][0].pop("arguments_sha256"), t[3].update(time_ns=b[2]["time_ns"] - 1))),
            ("false_owner_other_missing_seed_identity", lambda n, t, b, r: (
                n["calls"][0]["outcome"]["prior_session"].update(owner_status_verified=False),
                n["calls"][0]["outcome"]["foreign_project"].pop("session_id"))),
            ("duplicate_seed_handle_missing_root_provenance", lambda n, t, b, r: (
                n["calls"][0]["outcome"].pop("backend"),
                n["calls"][0]["outcome"]["foreign_project"].update(
                    session_handle_sha256=n["calls"][0]["outcome"]["prior_session"]["session_handle_sha256"]))),
            ("wrong_seed_project_missing_other_owner", lambda n, t, b, r: (
                n["calls"][0]["outcome"]["prior_session"].update(project_key="local:c1-offline-b"),
                n["calls"][0]["outcome"]["foreign_project"].pop("owner_status_verified"))),
        ]
        for name, mutate in cases:
            native, transport, business = self.material()
            reservation = copy.deepcopy(self.reservation)
            mutate(native, transport, business, reservation)
            with self.subTest(case=name):
                self.assertEqual(self.validate(native, transport, business, reservation)["status"], "failed")

    def test_review_i1_combined_absences_without_contradiction_remain_unknown(self):
        native, transport, business = self.material()
        native["calls"][0].update(outcome=None, outcome_state="unknown", result_sha256=None)
        native.pop("process_exit")
        transport[1].pop("invocation_id")
        business[4].pop("result")
        reservation = copy.deepcopy(self.reservation)
        reservation.pop("state")
        self.assertEqual(self.validate(native, transport, business, reservation)["status"], "unknown")

    def test_review_i1_nested_raw_contradictions_survive_missing_proofs(self):
        for mutation in ("duplicate_handles", "wrong_seed_project"):
            body = bootstrap_body()
            if mutation == "duplicate_handles":
                body.pop("seed_origin")
                body["foreign_project"]["session_handle"] = body["prior_session"]["session_handle"]
            else:
                body["prior_session"]["project_key"] = "local:c1-offline-b"
                body["foreign_project"].pop("owner_status_verified")
            with self.subTest(mutation=mutation):
                self.assertEqual(decode(raw_call(completed=True, body=body)).outcome_state, "failed")

    def test_review_i1_observed_terminals_bound_completed_count_without_all_terminals(self):
        for missing_steps, claimed, expected in (
            ((0,), 0, "failed"), ((0,), 3, "failed"), ((0,), 4, "unknown"), ((0,), 5, "unknown"),
            ((1, 3), 2, "failed"), ((1, 3), 3, "unknown"),
        ):
            native, transport, business = self.material()
            for index in missing_steps:
                native["calls"][index]["terminal_status"] = None
            native.update(completed_calls=claimed, turn_completed=False,
                          unknown_reasons=["missing_call_completion", "missing_turn_completion"])
            with self.subTest(missing=missing_steps, claimed=claimed):
                report = self.validate(native, transport, business)
                self.assertEqual(report["status"], expected)
                if expected == "unknown":
                    self.assertIsNone(report["checks"]["five_unique_calls"])
                    self.assertEqual(report["call_mapping"], [])
                else:
                    self.assertIs(report["checks"]["five_unique_calls"], False)

    def test_review_i1_partial_timestamps_preserve_provable_serial_rank_failures(self):
        cases = [
            ("review_two_transports_before_step1", lambda t, b: (
                t[4].pop("time_ns"), t[1].update(time_ns=b[0]["time_ns"] - 1))),
            ("known_interval_exceeds_step_capacity", lambda t, b: (
                t[4].pop("time_ns"), b[0].pop("time_ns"), b[3].pop("time_ns"),
                t[1].update(time_ns=220), t[2].update(time_ns=230))),
            ("known_transport_equals_business_boundary", lambda t, b: (
                t[4].pop("time_ns"), t[0].update(time_ns=b[0]["time_ns"]))),
            ("business_order_wrong_all_transport_times_missing", lambda t, b: (
                [row.pop("time_ns") for row in t], b[1].update(time_ns=b[0]["time_ns"] - 1))),
            ("business_order_equal_with_missing_transport_time", lambda t, b: (
                t[4].pop("time_ns"), b[1].update(time_ns=b[0]["time_ns"]))),
        ]
        for name, mutate in cases:
            native, transport, business = self.material()
            mutate(transport, business)
            with self.subTest(case=name):
                report = self.validate(native, transport, business)
                self.assertEqual(report["status"], "failed")
                self.assertIs(report["checks"]["id_mapping"], False)
                self.assertEqual(report["call_mapping"], [])
        for absent in ("transport5", "business2", "all_business_and_two_transport"):
            native, transport, business = self.material()
            if absent == "transport5":
                transport[4].pop("time_ns")
            elif absent == "business2":
                business[1].pop("time_ns")
            else:
                for row in business:
                    row.pop("time_ns")
                transport[1].pop("time_ns")
                transport[4].pop("time_ns")
            with self.subTest(absence_only=absent):
                report = self.validate(native, transport, business)
                self.assertEqual(report["status"], "unknown")
                self.assertIsNone(report["checks"]["id_mapping"])
                self.assertEqual(report["call_mapping"], [])

    def test_real_bounded_fixture_dtos_and_audits_verify_offline_without_rewriting_schemas(self):
        from experience_memory.interfaces.mcp_server import _invoke
        from tests.fixtures.codex_c1_server import NativeCodexFixture

        audit = Path(self.temp.name).resolve() / "real-fixture-audit"
        root = self.bundle / "projects/a"
        fixture = NativeCodexFixture(project="a", project_root=root,
                                     other_project_root=self.bundle / "projects/b", run_nonce=NONCE,
                                     audit_dir=audit, bounded_scenario=True)
        state = NativeRunState(NONCE, "a")
        state.consume(decode({"type": "thread.started", "thread_id": THREAD}))
        ready = None
        try:
            for step in range(1, 6):
                meta = {"callId": "actual-meta-" + str(step), "sessionId": THREAD, "threadId": THREAD,
                        "x-codex-turn-metadata": {"session_id": THREAD, "thread_id": THREAD,
                        "codex_version": "0.163.0", "workspaces": {str(root): {}}}}
                arguments = ({"probe_nonce": NONCE} if step == 1 else {"session_handle": (
                    ready["prior_session"]["session_handle"] if step == 3 else
                    ready["foreign_project"]["session_handle"] if step == 4 else ready["session_handle"])})
                started = raw_call(step)
                started["item"]["arguments"] = arguments
                state.consume(decode(started))
                if step == 1:
                    result = _invoke(fixture.bootstrap, meta, NONCE)
                    ready = json.loads(result.content[0].text)
                else:
                    with fixture.request_host(meta, tool="memory_status", arguments=arguments):
                        result = _invoke(fixture.memory.status, arguments["session_handle"])
                completed = raw_call(step, completed=True)
                completed["item"]["arguments"] = arguments
                completed["item"]["result"] = {
                    "content": [block.model_dump(by_alias=True, exclude_none=True) for block in result.content],
                    "structured_content": result.structured_content}
                completed["item"]["status"] = "failed" if result.is_error else "completed"
                state.consume(decode(completed))
            state.consume(decode(turn_end()))
        finally:
            fixture.close()
        rows = [json.loads(path.read_text("utf-8")) for path in audit.glob("*.json")]
        transport = [row for row in rows if row["event"] == "request_transport"]
        business = [row for row in rows if row["event"] == "request_business"]
        native = state.summary()
        native.update(process_exit=0, execution_origin="offline_simulation")
        report = self.validate(native, transport, business)
        self.assertEqual(report["status"], "verified", report)
        self.assertEqual(report["transport_count"], 5)
        self.assertEqual(report["business_count"], 5)
        self.assertNotIn(ready["session_handle"], json.dumps(report))
        self.assertNotIn(ready["session_handle"], json.dumps(rows))

    def test_shared_samples_have_exact_exec_provenance_and_absolute_codex_home(self):
        samples = json.loads((Path(__file__).resolve().parents[1] / "fixtures/codex_c1_event_samples.json").read_text("utf-8"))
        self.assertIn("exec", samples)
        self.assertEqual(samples["exec"]["provenance"]["target_version"], "0.163.0")
        self.assertEqual(samples["exec"]["provenance"]["kind"], "synthetic schema-shaped examples; not captured native evidence")
        self.assertTrue(Path(samples["initialize"]["codexHome"]).is_absolute())
        for raw in samples["exec"]["events"]:
            decode(raw)

    def test_shared_business_api_is_public_and_does_not_require_or_forge_process_exit(self):
        helper = getattr(results_api, "validate_business_evidence", None)
        self.assertTrue(callable(helper), "Task4 shared business extraction is missing")
        native, transport, business = self.material()
        native.pop("process_exit")
        native.pop("execution_origin")
        native.pop("turn_completed")
        report = helper(self.snapshot, "a", self.reservation, native, transport, business)
        self.assertEqual(report["status"], "verified", report)
        self.assertNotIn("native_finished", report["checks"])
        self.assertNotIn("execution_origin", report)
        self.assertNotIn("process_exit", native)
        # Reusing business evidence must NOT open the old exec natural-exit gate.
        report = self.validate(native, transport, business)
        self.assertEqual(report["status"], "unknown")
        native.update(process_exit=True, execution_origin="offline_simulation", turn_completed=True)
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")
        native["process_exit"] = 7
        self.assertEqual(self.validate(native, transport, business)["status"], "failed")

    def test_shared_body_normalizer_returns_only_existing_whitelist_and_hashes(self):
        helper = getattr(results_api, "normalize_c1_body", None)
        self.assertTrue(callable(helper), "Task4 public body normalizer is missing")
        body = bootstrap_body()
        normalized = helper("c1_bootstrap", body)
        self.assertEqual(normalized, decode(raw_call(completed=True, body=body)).outcome)
        self.assertEqual(normalized["status_arguments_sha256"], CURRENT_HASH)
        self.assertNotIn("synthetic-current-handle", json.dumps(normalized))
        self.assertEqual(helper("memory_status", {"status": "failed", "error_code": "SESSION_INVALID"}),
                         {"status": "failed", "error_code": "SESSION_INVALID"})
        malformed = bootstrap_body()
        malformed["backend"] = "private-body"
        with self.assertRaises(ValueError) as raised:
            helper("c1_bootstrap", malformed)
        self.assertNotIn("private-body", str(raised.exception))

    def test_shared_extraction_preserves_failure_precedence_and_mapping_without_exit(self):
        helper = getattr(results_api, "validate_business_evidence", None)
        self.assertTrue(callable(helper), "Task4 shared business extraction is missing")
        native, transport, business = self.material()
        native.pop("process_exit")
        business[0].pop("result")
        business[1]["invocation_id"] = transport[0]["invocation_id"]
        report = helper(self.snapshot, "a", self.reservation, native, transport, business)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["call_mapping"], [])
        self.assertIs(report["checks"]["id_mapping"], False)


if __name__ == "__main__":
    unittest.main()
