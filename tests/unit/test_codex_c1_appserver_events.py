"""Independent v2 wire, actual SDK audit, lifecycle and shutdown proof controls."""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from scripts import codex_c1_bundle as bundle_api
from scripts import codex_c1_results as results_api
from scripts import prepare_codex_c1_review as exporter
from scripts.codex_c1_appserver_rpc import AppServerPeer, RpcMessage, RpcProtocolError, classify_message
from scripts.codex_c1_appserver_session import AppServerSession


SAMPLES = Path(__file__).resolve().parents[1] / "fixtures/codex_c1_appserver_samples.json"
NONCE = "c1-events-test"
THREAD, TURN = "synthetic-thread", "synthetic-turn"


def message(value):
    return classify_message(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")


def notification(method, params):
    return message({"method": method, "params": params})


class AppServerEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This produces DTOs and BOTH actual audit streams; no fabricated success rows.
        from experience_memory.interfaces.mcp_server import _invoke
        from tests.fixtures.codex_c1_server import NativeCodexFixture

        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.bundle = Path(cls.temp.name).resolve() / "review"
        exporter.prepare(cls.bundle, NONCE)
        cls.snapshot = bundle_api.load_snapshot(cls.bundle)
        cls.root = cls.bundle / "projects/a"
        cls.reservation = {"schema_version": 1, "project": "a", "attempt": 1,
                           "state": "reserved", "run_nonce": NONCE,
                           "manifest_sha256": cls.snapshot.manifest_sha256}
        cls.samples = json.loads(SAMPLES.read_text("utf-8").replace(
            "__SYNTHETIC_ROOT__", cls.root.as_posix()))
        cls.wire = []
        audit = Path(cls.temp.name).resolve() / "sdk-audit"
        fixture = NativeCodexFixture(project="a", project_root=cls.root,
                                     other_project_root=cls.bundle / "projects/b", run_nonce=NONCE,
                                     audit_dir=audit, bounded_scenario=True)
        ready = None
        try:
            for step in range(1, 6):
                tool = "c1_bootstrap" if step == 1 else "memory_status"
                arguments = ({"probe_nonce": NONCE} if step == 1 else {"session_handle": (
                    ready["prior_session"]["session_handle"] if step == 3 else
                    ready["foreign_project"]["session_handle"] if step == 4 else ready["session_handle"])})
                item = {"type": "mcpToolCall", "id": "v2-item-" + str(step),
                        "server": "experience_memory_c1_wiring", "tool": tool,
                        "arguments": arguments, "status": "inProgress", "result": None, "error": None}
                cls.wire.append({"method": "item/started", "params": {
                    "threadId": THREAD, "turnId": TURN, "startedAtMs": 1700000000000 + step,
                    "item": copy.deepcopy(item)}})
                meta = {"callId": "sdk-meta-" + str(step), "sessionId": THREAD, "threadId": THREAD,
                        "x-codex-turn-metadata": {"session_id": THREAD, "thread_id": THREAD,
                        "codex_version": "0.163.0", "workspaces": {str(cls.root): {}}}}
                if step == 1:
                    result = _invoke(fixture.bootstrap, meta, NONCE)
                    ready = json.loads(result.content[0].text)
                else:
                    with fixture.request_host(meta, tool=tool, arguments=arguments):
                        result = _invoke(fixture.memory.status, arguments["session_handle"])
                # Adapt the SDK DTO to the pinned v2 envelope explicitly, never by recursive renaming.
                item.update(status="failed" if result.is_error else "completed", result={
                    "content": [block.model_dump(by_alias=True, exclude_none=True) for block in result.content],
                    "structuredContent": result.structured_content})
                cls.wire.append({"method": "item/completed", "params": {
                    "threadId": THREAD, "turnId": TURN, "completedAtMs": 1700000000100 + step,
                    "item": item}})
        finally:
            fixture.close()
        rows = [json.loads(path.read_text("utf-8")) for path in audit.glob("*.json")]
        cls.transport = [row for row in rows if row["event"] == "request_transport"]
        cls.outcomes = [row for row in rows if row["event"] == "request_business"]
        cls.handles = [ready["session_handle"], ready["prior_session"]["session_handle"],
                       ready["foreign_project"]["session_handle"]]

    def setUp(self):
        try:
            self.api = importlib.import_module("scripts.codex_c1_appserver_events")
        except ModuleNotFoundError as error:
            if error.name != "scripts.codex_c1_appserver_events":
                raise
            self.fail("Task4 independent app-server event/proof implementation is missing")

    def session(self, complete=False):
        session = AppServerSession(self.snapshot, "a", model="synthetic-model", provider="synthetic-provider")
        session.bind_reservation(copy.deepcopy(self.reservation))
        session.thread_start_params()
        session.accept_thread_response(copy.deepcopy(self.samples["session"]["thread_start_response"]))
        session.observe(notification("thread/started", {
            "thread": copy.deepcopy(self.samples["session"]["thread_start_response"]["thread"])}))
        session.turn_start_params()
        session.observe(message({"id": 23, "result": copy.deepcopy(self.samples["session"]["turn_start_response"])}))
        session.observe(notification("turn/started", copy.deepcopy(self.samples["session"]["turn_started"])))
        if complete:
            session.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
        return session

    def state(self, wires=None, complete=True):
        state = self.api.AppServerRunState(self.snapshot, "a", session=self.session().summary())
        state.observe(notification("turn/started", copy.deepcopy(self.samples["session"]["turn_started"])))
        for value in self.wire if wires is None else wires:
            state.observe(message(copy.deepcopy(value)))
        if complete:
            state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
        return state

    def business(self, state=None, transport=None, outcomes=None):
        return self.api.validate_appserver_business_evidence(
            self.snapshot, "a", self.reservation, self.state() if state is None else state,
            self.transport if transport is None else transport,
            self.outcomes if outcomes is None else outcomes)

    def shutdown(self):
        return {"input_eof_sent": True, "stdin_eof": True, "stdout_eof": True, "stderr_eof": True,
                "natural_exit_code": 0, "forced_termination": False, "io_failure": None,
                "io_threads_stopped": True,
                "cleanup": {"root_exited": True, "descendants_exited": True, "reason": "job_empty"}}

    def validate(self, business=None, session=None, shutdown=None, origin="offline_simulation"):
        return self.api.validate_appserver_evidence(
            self.snapshot, "a", self.reservation, self.business() if business is None else business,
            self.session(True).summary() if session is None else session,
            self.shutdown() if shutdown is None else shutdown, execution_origin=origin)

    def assert_decode_problem(self, value, severity):
        with self.assertRaises(ValueError) as raised:
            self.api.decode_appserver_notification(message(value))
        self.assertEqual(getattr(raised.exception, "state", None), severity)
        self.assertNotIn("private-body", str(raised.exception))

    def test_literal_v2_failed_terminal_preserves_expected_business_error(self):
        value = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
                 "completedAtMs": 1700000000000, "item": {"type": "mcpToolCall", "id": "literal-item",
                 "server": "experience_memory_c1_wiring", "tool": "memory_status",
                 "arguments": {"session_handle": "private-body"}, "status": "failed", "error": None,
                 "result": {"content": [{"type": "text", "text":
                     '{"status":"failed","error_code":"SESSION_INVALID"}'}]}}}}
        event = self.api.decode_appserver_notification(message(value))
        self.assertEqual((event.thread_id, event.turn_id, event.kind), (THREAD, TURN, "call_finished"))
        self.assertEqual(event.item.terminal_status, "failed")
        self.assertEqual(event.item.outcome_state, "valid")
        self.assertEqual(event.item.outcome, {"status": "failed", "error_code": "SESSION_INVALID"})
        self.assertNotIn("private-body", repr(event))
        with self.assertRaises(FrozenInstanceError):
            event.kind = "turn_completed"

    def test_camel_structured_channel_and_text_must_agree(self):
        value = copy.deepcopy(self.wire[1])
        body = json.loads(value["params"]["item"]["result"]["content"][0]["text"])
        value["params"]["item"]["result"]["structuredContent"] = body
        self.assertEqual(self.api.decode_appserver_notification(message(value)).item.outcome_state, "valid")
        body["backend"] = "private-body"
        event = self.api.decode_appserver_notification(message(value))
        self.assertEqual(event.item.outcome_state, "failed")
        self.assertNotIn("private-body", repr(event))

    def test_structured_only_is_missing_text_not_success_and_known_failure_dominates(self):
        value = copy.deepcopy(self.wire[1])
        result = value["params"]["item"]["result"]
        body = json.loads(result.pop("content")[0]["text"])
        result["structuredContent"] = body
        self.assertEqual(self.api.decode_appserver_notification(message(value)).item.outcome_state, "unknown")
        body["prior_session"]["owner_status_verified"] = False
        self.assertEqual(self.api.decode_appserver_notification(message(value)).item.outcome_state, "failed")

    def test_exec_keys_cannot_supply_missing_v2_identity_type_or_status(self):
        for field, old, val in (("threadId", "thread_id", THREAD), ("turnId", "turn_id", TURN)):
            value = copy.deepcopy(self.wire[0])
            value["params"].pop(field)
            value["params"][old] = val
            self.assert_decode_problem(value, "unknown")
        value = copy.deepcopy(self.wire[0])
        value["params"]["item"]["type"] = "mcp_tool_call"
        self.assert_decode_problem(value, "unknown")
        value["params"]["item"]["type"] = "mcpToolCall"
        value["params"]["item"]["status"] = "in_progress"
        self.assert_decode_problem(value, "unknown")

    def test_snake_structured_content_never_satisfies_camel_channel(self):
        value = copy.deepcopy(self.wire[1])
        result = value["params"]["item"]["result"]
        body = json.loads(result["content"][0]["text"])
        result.pop("content")
        result["structured_content"] = body
        result.pop("structuredContent", None)
        self.assertEqual(self.api.decode_appserver_notification(message(value)).item.outcome_state, "unknown")

    def test_missing_wire_fields_are_unknown_bad_present_fields_are_failed(self):
        for target, key in (("params", "threadId"), ("params", "turnId"), ("params", "startedAtMs"),
                            ("item", "id"), ("item", "arguments"), ("item", "status"), ("item", "tool")):
            value = copy.deepcopy(self.wire[0])
            obj = value["params"] if target == "params" else value["params"]["item"]
            obj.pop(key)
            with self.subTest(missing=key):
                self.assert_decode_problem(value, "unknown")
        for key, wrong in (("id", ""), ("arguments", []), ("status", None)):
            value = copy.deepcopy(self.wire[0])
            value["params"]["item"][key] = wrong
            self.assert_decode_problem(value, "failed")
        value = copy.deepcopy(self.wire[0])
        value["params"].pop("turnId")
        value["params"]["item"]["arguments"] = []
        self.assert_decode_problem(value, "failed")

    def test_mcp_phase_error_and_terminal_body_contradictions_fail(self):
        for index, field, wrong in ((0, "status", "completed"), (1, "status", "inProgress"),
                                    (1, "status", "failed"), (5, "status", "completed"),
                                    (1, "error", {"message": "private-body"})):
            value = copy.deepcopy(self.wire[index])
            value["params"]["item"][field] = wrong
            with self.subTest(index=index, field=field):
                try:
                    event = self.api.decode_appserver_notification(message(value))
                except ValueError as error:
                    self.assertEqual(error.state, "failed")
                else:
                    self.assertEqual(event.item.outcome_state, "failed")
                    self.assertNotIn("private-body", repr(event))

    def test_unknown_and_non_mcp_actions_never_become_business_success(self):
        for kind in ("commandExecution", "fileChange", "webSearch", "dynamicToolCall", "collabAgentToolCall"):
            value = copy.deepcopy(self.wire[0])
            value["params"]["item"]["type"] = kind
            self.assertEqual(self.api.decode_appserver_notification(message(value)).kind, "unexpected_tool")
        value = copy.deepcopy(self.wire[0])
        value["params"]["item"]["type"] = "futureTool"
        self.assert_decode_problem(value, "unknown")
        for field, wrong in (("server", "foreign"), ("tool", "memory_write")):
            value = copy.deepcopy(self.wire[0])
            value["params"]["item"][field] = wrong
            self.assertEqual(self.api.decode_appserver_notification(message(value)).kind, "unexpected_tool")

    def test_decoder_rechecks_full_rpc_envelope_and_has_a_memory_bound(self):
        original = message(self.wire[0])
        variants = [RpcMessage("response", original.method, None, original.payload),
                    RpcMessage("notification", "item/completed", None, original.payload),
                    RpcMessage("notification", original.method, 1, original.payload),
                    RpcMessage("notification", original.method, None, dict(original.payload, id=1)),
                    RpcMessage("notification", original.method, None, dict(original.payload, result={})),
                    RpcMessage("notification", original.method, None, dict(original.payload, extra="x" * 262145))]
        for value in variants:
            with self.subTest(kind=value.kind):
                with self.assertRaises(ValueError):
                    self.api.decode_appserver_notification(value)

    def test_model_text_marker_does_not_complete_or_count_calls(self):
        value = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
                 "completedAtMs": 1700000000000,
                 "item": {"type": "agentMessage", "id": "answer", "text": "C1_WIRING_DONE private-body"}}}
        event = self.api.decode_appserver_notification(message(value))
        self.assertEqual(event.kind, "ignored_known")
        self.assertIsNotNone(event.item, "Discarded item identity/hash evidence is missing")
        self.assertIsNone(event.item.outcome)
        state = self.state(wires=[], complete=False)
        state.consume(event)
        self.assertEqual(state.summary()["native"]["unique_calls"], 0)
        self.assertFalse(state.summary()["appserver_event_proof"]["turn_completed"])
        self.assertNotIn("private-body", repr(state.summary()))

    def test_real_sdk_dtos_and_audits_verify_without_fake_process_exit(self):
        state = self.state()
        self.assertNotIn("process_exit", state.summary()["native"])
        business = self.business(state)
        self.assertEqual(business["status"], "verified", business)
        self.assertEqual(business["transport_kind"], "app_server_stdio_0.163.0")
        self.assertEqual((business["transport_count"], business["business_count"]), (5, 5))
        self.assertEqual([(row["item_id"], row["call_id"]) for row in business["call_mapping"]],
                         [("v2-item-" + str(step), "sdk-meta-" + str(step)) for step in range(1, 6)])
        final = self.validate(business)
        self.assertEqual(final["status"], "verified", final)
        self.assertFalse(final["native_evidence_claimed"])
        for handle in self.handles:
            for value in (state.summary(), business, final, self.transport, self.outcomes):
                self.assertNotIn(handle, json.dumps(value))

    def test_state_requires_validated_session_ids_not_request_intent(self):
        summary = self.session().summary()
        for key, wrong in (("new_turn_verified", False), ("thread_response_verified", False),
                           ("run_nonce", "c1-old"), ("project", "b"), ("manifest_sha256", "0" * 64),
                           ("thread_id", None), ("turn_id", None), ("state", "unknown")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.api.AppServerRunState(self.snapshot, "a", session=dict(summary, **{key: wrong}))

    def test_thread_turn_and_item_identity_checked_independently_and_sticky(self):
        for key in ("threadId", "turnId"):
            wires = copy.deepcopy(self.wire)
            wires[1]["params"][key] = "foreign"
            state = self.state(wires, complete=False)
            self.assertEqual(self.business(state)["status"], "failed")
            state.observe(message(self.wire[1]))
            self.assertEqual(self.business(state)["status"], "failed")
        wires = copy.deepcopy(self.wire)
        wires[1]["params"]["item"]["id"] = "unstarted"
        self.assertEqual(self.business(self.state(wires))["status"], "failed")

    def test_final_w2_bound_thread_duplicates_preserve_actual_five_call_proof_across_lifecycle(self):
        session = self.session()
        state = self.api.AppServerRunState(self.snapshot, "a", session=session.summary())
        notice = notification("thread/started", {
            "thread": copy.deepcopy(self.samples["session"]["thread_start_response"]["thread"])})
        state.observe(notice)
        state.observe(notification("turn/started", copy.deepcopy(self.samples["session"]["turn_started"])))
        for index, value in enumerate(self.wire):
            state.observe(message(copy.deepcopy(value)))
            if index == 1:
                before = state.summary()
                session.observe(notice)
                state.observe(notice)
                self.assertEqual(state.summary(), before)
                self.assertEqual(state.summary()["appserver_event_proof"]["failure_reasons"], [])
        completed = notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"]))
        session.observe(completed)
        state.observe(completed)
        before = state.summary()
        session.observe(notice)
        state.observe(notice)
        self.assertEqual(state.summary(), before)
        self.assertEqual(session.summary()["state"], "protocol_complete")
        proof = before["appserver_event_proof"]
        self.assertEqual((proof["unique_calls"], proof["completed_calls"]), (5, 5))
        self.assertEqual(proof["failure_reasons"], [])
        self.assertEqual(proof["unknown_reasons"], [])
        business = self.business(state)
        self.assertEqual(business["status"], "verified", business)
        self.assertEqual((business["transport_count"], business["business_count"]), (5, 5))

    def test_final_w2_event_owner_independently_rejects_foreign_thread_and_thread_turn_identity(self):
        for complete in (False, True):
            for thread, turn, reason in (("foreign-thread", None, "appserver_event_identity_mismatch"),
                                         (THREAD, TURN, "appserver_thread_order_invalid")):
                with self.subTest(complete=complete, thread=thread, turn=turn):
                    state = self.state(complete=complete)
                    state.consume(self.api.AppServerEvent(thread, turn, "thread_started", None))
                    self.assertIn(reason, state.summary()["appserver_event_proof"]["failure_reasons"])
                    notice = notification("thread/started", {
                        "thread": copy.deepcopy(self.samples["session"]["thread_start_response"]["thread"])})
                    state.observe(notice)
                    self.assertIn(reason, state.summary()["appserver_event_proof"]["failure_reasons"])
                    self.assertEqual(self.business(state)["status"], "failed")

    def test_exact_duplicates_are_idempotent_conflicting_replay_and_order_fail(self):
        wires = [value for original in self.wire for value in (original, copy.deepcopy(original))]
        state = self.state(wires)
        summary = state.summary()
        self.assertEqual(summary["native"]["unique_calls"], 5)
        self.assertEqual(self.business(state)["status"], "verified")
        detached = state.summary()
        detached["appserver_event_proof"]["item_ids"].clear()
        self.assertEqual(len(state.summary()["appserver_event_proof"]["item_ids"]), 5)
        for mutation in ("arguments", "reused_id", "order", "extra"):
            values = copy.deepcopy(self.wire)
            if mutation == "arguments":
                duplicate = copy.deepcopy(values[0])
                duplicate["params"]["item"]["arguments"] = {"probe_nonce": "c1-old"}
                values.insert(1, duplicate)
            elif mutation == "reused_id":
                for value in values[2:4]:
                    value["params"]["item"]["id"] = "v2-item-1"
            elif mutation == "order":
                values[0], values[1] = values[1], values[0]
            else:
                extra = copy.deepcopy(values[-2:])
                for value in extra:
                    value["params"]["item"]["id"] = "item-six"
                values.extend(extra)
            with self.subTest(mutation=mutation):
                self.assertEqual(self.business(self.state(values))["status"], "failed")

    def test_unknown_decode_freezes_and_later_contradiction_dominates(self):
        state = self.state(wires=[], complete=False)
        missing = copy.deepcopy(self.wire[0])
        missing["params"].pop("turnId")
        state.observe(message(missing))
        for value in self.wire:
            state.observe(message(value))
        self.assertEqual(self.business(state)["status"], "unknown")
        foreign = copy.deepcopy(self.wire[0])
        foreign["params"]["threadId"] = "foreign"
        state.observe(message(foreign))
        self.assertEqual(self.business(state)["status"], "failed")

    def test_turn_begin_end_order_and_failure_do_not_manufacture_completion(self):
        state = self.api.AppServerRunState(self.snapshot, "a", session=self.session().summary())
        state.observe(message(self.wire[0]))
        self.assertEqual(self.business(state)["status"], "failed")
        state = self.state(complete=False)
        self.assertEqual(self.validate(self.business(state))["status"], "unknown")
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(status="failed", error={"message": "private-body"})
        state.observe(notification("turn/completed", completed))
        report = self.validate(self.business(state))
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("private-body", json.dumps(report))

    def test_full_audits_do_not_replace_missing_native_completions(self):
        for count in (0, 1, 8, 9):
            state = self.state(self.wire[:count], complete=False)
            with self.subTest(count=count):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")

    def test_expected_error_only_steps_three_and_four_and_no_unexpected_code(self):
        for step in (1, 2, 5):
            wires = copy.deepcopy(self.wire)
            wires[step * 2 - 1]["params"]["item"].update(status="failed", result={"content": [{
                "type": "text", "text": '{"status":"failed","error_code":"SESSION_INVALID"}'}]})
            self.assertEqual(self.business(self.state(wires))["status"], "failed")
        wires = copy.deepcopy(self.wire)
        wires[5]["params"]["item"]["result"]["content"][0]["text"] = (
            '{"status":"failed","error_code":"private-body"}')
        proof = self.business(self.state(wires))
        self.assertEqual(proof["status"], "failed")
        self.assertNotIn("private-body", json.dumps(proof))

    def test_nonce_hash_audit_replay_and_mapping_failure_dominate_missing(self):
        for mutation in ("nonce", "hash", "replay", "call_replay", "root", "serial"):
            transport, outcomes = copy.deepcopy(self.transport), copy.deepcopy(self.outcomes)
            outcomes.sort(key=lambda row: row["step"])
            transport.sort(key=lambda row: row["time_ns"])
            outcomes[0].pop("result")
            if mutation == "nonce":
                outcomes[1]["run_nonce"] = "c1-old"
            elif mutation == "hash":
                outcomes[1]["arguments_sha256"] = "0" * 64
            elif mutation == "replay":
                outcomes[1]["invocation_id"] = transport[0]["invocation_id"]
            elif mutation == "call_replay":
                outcomes[1]["call_id"] = outcomes[0]["call_id"]
            elif mutation == "root":
                transport[1]["cwd_sha256"] = "0" * 64
            else:
                transport[1]["time_ns"] = transport[0]["time_ns"]
            with self.subTest(mutation=mutation):
                self.assertEqual(self.business(transport=transport, outcomes=outcomes)["status"], "failed")

    def test_forced_cleanup_and_unobserved_exit_are_unknown_not_natural_success(self):
        for fields in ({"natural_exit_code": None, "forced_termination": True},
                       {"natural_exit_code": None}, {"forced_termination": True},
                       {"input_eof_sent": False}, {"stdout_eof": False},
                       {"stderr_eof": False}, {"io_threads_stopped": False}):
            with self.subTest(fields=fields):
                proof = self.validate(shutdown=dict(self.shutdown(), **fields))
                self.assertEqual(proof["status"], "unknown")
                self.assertFalse(proof["native_evidence_claimed"])

        # A successful close acknowledgement plus an observed open stdin is an
        # explicit internal contradiction, unlike an unobserved EOF alone.
        self.assertEqual(self.validate(shutdown=dict(self.shutdown(), stdin_eof=False))["status"], "failed")

    def test_all_shutdown_fields_required_and_strict_types(self):
        for key in self.shutdown():
            shutdown = self.shutdown()
            shutdown.pop(key)
            with self.subTest(missing=key):
                self.assertEqual(self.validate(shutdown=shutdown)["status"], "unknown")
        for key, value in (("natural_exit_code", True), ("natural_exit_code", 7),
                           ("forced_termination", 0), ("input_eof_sent", 1), ("stdout_eof", None),
                           ("io_threads_stopped", 1), ("io_failure", 0)):
            with self.subTest(key=key, value=value):
                self.assertEqual(self.validate(shutdown=dict(self.shutdown(), **{key: value}))["status"], "failed")

    def test_actual_close_input_ack_cannot_be_replaced_with_forced_stdin_eof(self):
        shutdown = self.shutdown()
        shutdown.pop("input_eof_sent")
        shutdown.update(stdin_eof=True, forced_termination=True, natural_exit_code=None)
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "unknown")
        shutdown.update(forced_termination=False, natural_exit_code=0)
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "unknown")

    def test_sticky_io_failure_overrides_later_natural_zero_and_missing_eof(self):
        shutdown = self.shutdown()
        shutdown.update(io_failure="C1_PROCESS_DEADLINE")
        shutdown.pop("stderr_eof")
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "failed")
        shutdown = self.shutdown()
        shutdown["io_failure"] = "private-body"
        proof = self.validate(shutdown=shutdown)
        self.assertEqual(proof["status"], "failed")
        self.assertNotIn("private-body", json.dumps(proof))

    def test_job_nonempty_unconfirmed_and_worker_alive_cannot_verify(self):
        for root, descendants, reason, want in ((True, False, "job_not_empty", "unknown"),
                                              (False, True, "root_not_exited", "unknown"),
                                              (True, True, "job_empty", "verified")):
            shutdown = self.shutdown()
            shutdown["cleanup"] = {"root_exited": root, "descendants_exited": descendants, "reason": reason}
            self.assertEqual(self.validate(shutdown=shutdown)["status"], want)
        shutdown = self.shutdown()
        shutdown["cleanup"]["descendants_exited"] = 1
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "failed")
        shutdown = self.shutdown()
        shutdown["cleanup"].pop("reason")
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "unknown")

    def test_real_session_v1_not_illustrative_policy_verified(self):
        summary = self.session(True).summary()
        self.assertNotIn("policy_verified", summary)
        summary.pop("policy")
        summary["policy_verified"] = True
        self.assertEqual(self.validate(session=summary)["status"], "unknown")
        for key in self.session(True).summary():
            summary = self.session(True).summary()
            summary.pop(key)
            with self.subTest(missing=key):
                self.assertEqual(self.validate(session=summary)["status"], "unknown")

    def test_session_failed_unknown_approval_policy_and_source_veto_success(self):
        for key, value, want in (("state", "failed", "failed"), ("state", "unknown", "unknown"),
                                 ("approval_observed", True, "failed"), ("interrupt_intent", True, "failed"),
                                 ("new_turn_verified", False, "unknown"), ("turn_status", None, "unknown")):
            summary = self.session(True).summary()
            summary[key] = value
            self.assertEqual(self.validate(session=summary)["status"], want)
        for field, value in (("approval_policy", "never"), ("approvals_reviewer", "auto_review"),
                             ("network_access", True), ("sandbox_type", "workspaceWrite"),
                             ("root_sha256", "0" * 64), ("network_access_source", "model")):
            summary = self.session(True).summary()
            summary["policy"][field] = value
            summary.pop("turn_completed_observed")
            self.assertEqual(self.validate(session=summary)["status"], "failed")
        summary = self.session(True).summary()
        summary.update(state="unknown", failure_codes=["C1_APPSERVER_SOURCE_UNKNOWN"])
        self.assertEqual(self.validate(session=summary)["status"], "unknown")

    def test_required_event_proof_crossmatches_thread_turn_snapshot_and_call_mapping(self):
        for key, value in (("thread_id", "foreign"), ("native_turn_id", "foreign"), ("run_nonce", "c1-old"),
                           ("project", "b"), ("manifest_sha256", "0" * 64), ("root_sha256", "0" * 64),
                           ("unique_calls", 6), ("completed_calls", 4), ("item_ids", ["replay"] * 5),
                           ("order_verified", False), ("replay_detected", True), ("failure_reasons", ["observed"])):
            business = self.business()
            business["appserver_event_proof"][key] = value
            business["appserver_event_proof"].pop("turn_completed")
            with self.subTest(key=key):
                self.assertEqual(self.validate(business)["status"], "failed")
        business = self.business()
        business.pop("appserver_event_proof")
        self.assertEqual(self.validate(business)["status"], "unknown")
        business = self.business()
        business["transport_kind"] = "exec"
        self.assertEqual(self.validate(business)["status"], "failed")

    def test_business_claim_does_not_hide_missing_checks_counts_or_mapping(self):
        for mutation, want in (("missing_check", "unknown"), ("false_check", "failed"),
                               ("missing_mapping", "unknown"), ("replay_mapping", "failed"),
                               ("count_bool", "failed"), ("status_failed", "failed")):
            business = self.business()
            if mutation == "missing_check":
                business["checks"].pop("scope_bound")
            elif mutation == "false_check":
                business["checks"]["scope_bound"] = False
            elif mutation == "missing_mapping":
                business.pop("call_mapping")
            elif mutation == "replay_mapping":
                business["call_mapping"][1]["item_id"] = business["call_mapping"][0]["item_id"]
            elif mutation == "count_bool":
                business["completed_calls"] = True
            else:
                business["status"] = "failed"
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(business)["status"], want)

    def test_session_business_and_shutdown_failures_dominate_other_missing_fields(self):
        for layer in ("business", "session", "shutdown"):
            business, summary, shutdown = self.business(), self.session(True).summary(), self.shutdown()
            business.pop("call_mapping")
            summary.pop("turn_id")
            shutdown.pop("stderr_eof")
            if layer == "business":
                business["status"] = "failed"
            elif layer == "session":
                summary["state"] = "failed"
            else:
                shutdown["natural_exit_code"] = 9
            self.assertEqual(self.validate(business, summary, shutdown)["status"], "failed")

    def test_origin_is_explicit_and_native_claim_never_from_model_or_failed_proof(self):
        self.assertEqual(self.validate(origin="model_native")["status"], "failed")
        proof = self.validate(origin="native_codex")
        self.assertEqual(proof["status"], "verified")
        self.assertTrue(proof["native_evidence_claimed"])
        shutdown = dict(self.shutdown(), forced_termination=True)
        self.assertFalse(self.validate(shutdown=shutdown, origin="native_codex")["native_evidence_claimed"])

    def test_result_contradiction_is_not_masked_by_missing_outer_wire_field(self):
        for missing in ("threadId", "turnId", "id", "status", "tool"):
            value = copy.deepcopy(self.wire[1])
            body = json.loads(value["params"]["item"]["result"]["content"][0]["text"])
            body["prior_session"]["owner_status_verified"] = False
            value["params"]["item"]["result"]["content"][0]["text"] = json.dumps(body)
            obj = value["params"] if missing in ("threadId", "turnId") else value["params"]["item"]
            obj.pop(missing)
            with self.subTest(missing=missing):
                self.assert_decode_problem(value, "failed")
        value = copy.deepcopy(self.wire[1])
        value["params"]["item"].pop("status")
        value["params"]["item"]["error"] = {"message": "private-body"}
        self.assert_decode_problem(value, "failed")

    def test_premature_turn_end_with_missing_items_is_unknown_and_cannot_resume(self):
        state = self.state(self.wire[:8], complete=True)
        self.assertEqual(self.business(state)["status"], "unknown")
        for value in self.wire[8:]:
            state.observe(message(value))
        # The first incomplete end was unknown. Actual later items are an
        # independent after-turn ordering contradiction, not permission to resume.
        self.assertEqual(self.business(state)["status"], "failed")
        self.assertEqual(state.summary()["native"]["unique_calls"], 4)
        self.assertEqual(state.summary()["native"]["completed_calls"], 4)

    def test_event_boundary_false_cannot_hide_observed_completed_calls(self):
        business = self.business()
        business["appserver_event_proof"]["turn_started"] = False
        self.assertEqual(self.validate(business)["status"], "failed")
        business = self.business()
        business["appserver_event_proof"]["thread_bound"] = False
        self.assertEqual(self.validate(business)["status"], "failed")

    def test_defaulted_reasoning_arrays_and_optional_mcp_metadata_are_validated(self):
        reasoning = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
                     "completedAtMs": 1700000000000, "item": {"type": "reasoning", "id": "thought"}}}
        try:
            event = self.api.decode_appserver_notification(message(reasoning))
        except ValueError as error:
            self.fail("Pinned schema defaults for discarded reasoning were rejected: " + str(error))
        self.assertEqual(event.kind, "ignored_known")
        for key, value in (("readOnlyHint", "true"), ("pluginId", 7), ("mcpAppResourceUri", [])):
            wrong = copy.deepcopy(self.wire[1])
            wrong["params"]["item"][key] = value
            with self.subTest(key=key):
                self.assert_decode_problem(wrong, "failed")

    def test_approval_attestation_and_remote_error_never_become_tool_proof(self):
        for method, want in (("item/permissions/requestApproval", "failed"),
                             ("item/tool/requestAttestation", "unknown")):
            value = {"id": 5, "method": method, "params": {"threadId": THREAD, "turnId": TURN}}
            self.assert_decode_problem(value, want)
        self.assert_decode_problem({"method": "error", "params": {"private": "private-body"}}, "failed")

    def test_event_proof_required_fields_and_types_have_no_success_defaults(self):
        valid = self.business()
        for key in valid["appserver_event_proof"]:
            business = copy.deepcopy(valid)
            business["appserver_event_proof"].pop(key)
            with self.subTest(missing=key):
                self.assertEqual(self.validate(business)["status"], "unknown")
        for key, value in (("schema_version", True), ("thread_bound", 1), ("turn_started", 1),
                           ("turn_completed", "true"), ("item_ids", None), ("completed_calls", True),
                           ("failure_reasons", "private-body"), ("unknown_reasons", 1)):
            business = copy.deepcopy(valid)
            business["appserver_event_proof"][key] = value
            with self.subTest(key=key):
                report = self.validate(business)
                self.assertEqual(report["status"], "failed")
                self.assertNotIn("private-body", json.dumps(report))

    def test_manually_supplied_normalized_event_cannot_persist_raw_body_or_reason(self):
        state = self.state(wires=self.wire[:1], complete=False)
        event = self.api.decode_appserver_notification(message(self.wire[1]))
        outcome = dict(event.item.outcome, raw_body="private-body")
        state.consume(replace(event, item=replace(event.item, outcome=outcome, outcome_reason="private-body")))
        self.assertNotIn("private-body", repr(state.summary()))
        self.assertEqual(self.business(state)["status"], "failed")
        for field, value in (("item_id", []), ("item_id", None)):
            state = self.state(wires=self.wire[:1], complete=False)
            malformed = replace(event, item=replace(event.item, **{field: value}))
            try:
                state.consume(malformed)
            except (TypeError, ValueError):
                self.fail("Malformed normalized identity escaped the fail-closed reducer")
            self.assertEqual(self.business(state)["status"], "failed")
        state = self.state(wires=self.wire[:3], complete=False)
        status_event = self.api.decode_appserver_notification(message(self.wire[3]))
        try:
            state.consume(replace(status_event, item=replace(status_event.item, arguments_sha256=[])))
        except (TypeError, ValueError):
            self.fail("Malformed normalized argument hash escaped the fail-closed inspector")
        self.assertEqual(self.business(state)["status"], "failed")

    def test_static_rpc_failure_mapping_unknown_code_and_missing_failure_veto(self):
        for code, expected in (("C1_RPC_REMOTE_ERROR", "failed"), ("C1_RPC_RESPONSE_ID_MISMATCH", "failed"),
                               ("C1_RPC_FRAME_INVALID", "failed"), ("C1_RPC_METHOD_NOT_ALLOWED", "failed"),
                               ("C1_RPC_UNEXPECTED_EOF", "unknown"), ("C1_RPC_READ_FAILED", "unknown"),
                               ("C1_RPC_WRITE_FAILED", "unknown"), ("C1_RPC_SERVER_REQUEST_NOT_ALLOWED", "unknown")):
            state = self.state()
            helper = getattr(state, "record_rpc_failure", None)
            self.assertTrue(callable(helper), "Actual Peer failure propagation API is missing")
            helper(code)
            with self.subTest(code=code):
                self.assertEqual(self.validate(self.business(state))["status"], expected)
        for unknown in (None, "private-body", "C1_RPC_FUTURE_ERROR"):
            state = self.state()
            helper = getattr(state, "record_rpc_failure", None)
            self.assertTrue(callable(helper), "Actual Peer failure propagation API is missing")
            with self.assertRaises(ValueError) as raised:
                helper(unknown)
            self.assertNotIn("private-body", str(raised.exception))
            self.assertNotIn("private-body", repr(state.summary()))
            self.assertEqual(self.validate(self.business(state))["status"], "unknown")

    @unittest.skipUnless(sys.platform == "win32", "Windows Job-owned RPC integration")
    def test_actual_peer_remote_error_vetoes_completed_business_after_clean_shutdown(self):
        from scripts.codex_c1_process import OwnedProcess

        state = self.state()
        helper = getattr(state, "record_rpc_failure", None)
        self.assertTrue(callable(helper), "Actual Peer failure propagation API is missing")
        # Only a local isolated Python child; its output is a synthetic pinned RPC
        # error envelope. The actual Peer consumes it, poisons, and retains code.
        code = ('import sys; print(\'{"method":"error","params":{"error":{"message":"private-body"},'
                '"threadId":"synthetic-thread","turnId":"synthetic-turn","willRetry":false}}\',flush=True); '
                'sys.stdin.buffer.read()')
        process = OwnedProcess([sys._base_executable, "-I", "-c", code], cwd=Path(self.temp.name),
                               env=dict(os.environ), deadline=time.monotonic() + 6, stdin_mode="pipe")
        eof_sent = False
        try:
            peer = AppServerPeer(process)
            with self.assertRaisesRegex(RpcProtocolError, "^C1_RPC_REMOTE_ERROR$"):
                peer.next_message()
            helper(peer.failure_code)
            process.close_input()
            eof_sent = True
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
        observed = process.shutdown_state()
        shutdown = {key: observed[key] for key in ("stdin_eof", "stdout_eof", "stderr_eof", "natural_exit_code",
                                                   "forced_termination", "io_failure", "io_threads_stopped")}
        shutdown.update(input_eof_sent=eof_sent, cleanup=cleanup)
        self.assertEqual(shutdown["natural_exit_code"], 0)
        self.assertIs(shutdown["io_failure"], None)
        report = self.validate(self.business(state), shutdown=shutdown)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["native_evidence_claimed"])
        self.assertNotIn("private-body", json.dumps(report))
        # The SAME actual clean shutdown facts do verify the control sequence.
        self.assertEqual(self.validate(shutdown=shutdown)["status"], "verified")

    def test_matched_rpc_error_response_is_observed_failure_not_missing_tool_result(self):
        value = {"id": 3, "error": {"code": -32603, "message": "private-body"}}
        self.assert_decode_problem(value, "failed")
        state = self.state()
        state.observe(message(value))
        report = self.validate(self.business(state))
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("private-body", json.dumps(report))

    def test_missing_nested_notification_objects_are_unknown_not_explicit_null(self):
        cases = [({"method": "item/started"}, "params"),
                 ({"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN,
                                                       "startedAtMs": 1}}, "item"),
                 ({"method": "turn/started", "params": {"threadId": THREAD}}, "turn"),
                 ({"method": "thread/started", "params": {}}, "thread")]
        for value, field in cases:
            with self.subTest(missing=field):
                self.assert_decode_problem(value, "unknown")
            null_value = copy.deepcopy(value)
            if field == "params":
                null_value["params"] = None
            else:
                null_value["params"][field] = None
            with self.subTest(explicit_null=field):
                self.assert_decode_problem(null_value, "failed")

    def user_message(self, *, completed=False, text=None, item_id="initial-user", **item_extra):
        return {"method": "item/completed" if completed else "item/started", "params": {
            "threadId": THREAD, "turnId": TURN,
            "completedAtMs" if completed else "startedAtMs": 1700000000000,
            "item": {"type": "userMessage", "id": item_id, "clientId": None,
                     "content": [{"type": "text", "text": self.snapshot.manifest["projects"]["a"]["prompt"]
                                  if text is None else text}], **item_extra}}}

    def test_pinned_text_user_message_is_discarded_not_a_sixth_business_call(self):
        literal = self.user_message(text="literal user input")
        literal["params"]["item"]["clientId"] = "literal-client"
        literal["params"]["item"]["content"][0]["text_elements"] = [
            {"byteRange": {"start": 0, "end": 7}, "placeholder": "literal"}]
        try:
            event = self.api.decode_appserver_notification(message(literal))
        except ValueError as error:
            self.fail("Supported text-only userMessage was rejected: " + str(error))
        self.assertEqual(event.kind, "ignored_known")
        self.assertNotIn("literal user input", repr(event))
        state = self.state(wires=[], complete=False)
        for value in (self.user_message(), self.user_message(), self.user_message(completed=True),
                      self.user_message(completed=True), *self.wire):
            state.observe(message(value))
        state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
        self.assertEqual(self.validate(self.business(state))["status"], "verified")
        self.assertEqual(state.summary()["native"]["unique_calls"], 5)
        self.assertEqual(len(state.summary()["appserver_event_proof"]["discarded_items"]), 1)
        self.assertNotIn(self.snapshot.manifest["projects"]["a"]["prompt"], json.dumps(state.summary()))

    def test_user_message_missing_malformed_and_unsupported_content_never_prove_success(self):
        for mutation, want in (("missing_text", "unknown"), ("numeric_text", "failed"),
                               ("nontext", "failed"), ("future", "unknown"),
                               ("metadata", "failed"), ("client_id", "failed")):
            value = self.user_message(text="literal")
            block = value["params"]["item"]["content"][0]
            if mutation == "missing_text":
                block.pop("text")
            elif mutation == "numeric_text":
                block["text"] = 7
            elif mutation == "nontext":
                block["type"] = "image"
            elif mutation == "future":
                block["type"] = "futureInput"
            elif mutation == "metadata":
                block["text_elements"] = [{"byteRange": {"start": True, "end": 3}}]
            else:
                value["params"]["item"]["clientId"] = 7
            with self.subTest(mutation=mutation):
                self.assert_decode_problem(value, want)

    def test_discarded_item_ownership_lifecycle_replay_and_extra_user_origin_are_checked(self):
        for mutation in ("foreign_thread", "foreign_turn", "conflicting", "extra", "unstarted", "wrong_prompt"):
            state = self.state(wires=[], complete=False)
            values = [self.user_message(), self.user_message(completed=True)]
            if mutation == "foreign_thread":
                values[0]["params"]["threadId"] = "foreign"
            elif mutation == "foreign_turn":
                values[0]["params"]["turnId"] = "foreign"
            elif mutation == "conflicting":
                other = self.user_message(text="private-body")
                values.insert(1, other)
            elif mutation == "extra":
                values += [self.user_message(item_id="extra-user")]
            elif mutation == "unstarted":
                values = values[1:]
            else:
                values = [self.user_message(text="private-body")]
            for value in (*values, *self.wire):
                state.observe(message(value))
            state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
            with self.subTest(mutation=mutation):
                self.assertEqual(self.business(state)["status"], "failed")
                self.assertNotIn("private-body", repr(state.summary()))
        # Model/reasoning item duplicates carry hashes, not raw content, and can
        # never collide with an admitted MCP item identity.
        state = self.state(wires=[], complete=False)
        first = {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                 "item": {"type": "agentMessage", "id": "v2-item-1", "text": ""}}}
        state.observe(message(first))
        state.observe(message(self.wire[0]))
        self.assertEqual(self.business(state)["status"], "failed")

    def test_missing_other_wire_proof_cannot_mask_present_owner_or_call_contradiction(self):
        for wrong, absent in (("threadId", "turnId"), ("turnId", "threadId")):
            value = copy.deepcopy(self.wire[0])
            value["params"][wrong] = "foreign"
            value["params"].pop(absent)
            state = self.state(wires=[], complete=False)
            state.observe(message(value))
            with self.subTest(wrong=wrong):
                self.assertEqual(self.business(state)["status"], "failed")
        for mutation in ("arguments", "tool", "item_id", "server"):
            value = copy.deepcopy(self.wire[1])
            value["params"]["item"].pop("status")
            if mutation == "arguments":
                value["params"]["item"]["arguments"] = {"probe_nonce": "c1-old"}
            elif mutation == "tool":
                value["params"]["item"]["tool"] = "memory_write"
            elif mutation == "item_id":
                value["params"]["item"]["id"] = "never-started"
            else:
                value["params"]["item"]["server"] = "foreign"
            if mutation in ("tool", "server"):
                self.assert_decode_problem(value, "failed")
            state = self.state(wires=self.wire[:1], complete=False)
            state.observe(message(value))
            with self.subTest(mutation=mutation):
                self.assertEqual(self.business(state)["status"], "failed")

    def test_discarded_model_item_fingerprints_and_fixed_memory_budget_are_enforced(self):
        state = self.state(wires=[], complete=False)
        for number in range(16):
            for completed in (False, True):
                value = {"method": "item/completed" if completed else "item/started", "params": {
                    "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                    "item": {"type": "agentMessage", "id": "answer-" + str(number),
                             "text": "private-body" if completed else ""}}}
                state.observe(message(value))
        for value in self.wire:
            state.observe(message(value))
        state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
        self.assertEqual(self.validate(self.business(state))["status"], "verified")
        self.assertEqual(len(state.summary()["appserver_event_proof"]["discarded_items"]), 16)
        self.assertNotIn("private-body", repr(state.summary()))
        # Same phase with different canonical content is a conflict, not replay
        # success. This remains independently failed even after turn completion.
        changed = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
                   "completedAtMs": 1, "item": {"type": "agentMessage", "id": "answer-0", "text": "changed"}}}
        state.observe(message(changed))
        self.assertEqual(self.business(state)["status"], "failed")
        state = self.state(wires=[], complete=False)
        for number in range(17):
            value = {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                     "item": {"type": "reasoning", "id": "thought-" + str(number)}}}
            state.observe(message(value))
        self.assertEqual(len(state.summary()["appserver_event_proof"]["discarded_items"]), 16)
        self.assertEqual(self.business(state)["status"], "failed")

    def test_review_i1_completion_items_cannot_hide_extra_forbidden_or_conflicting_items(self):
        for mutation in ("command", "extra_mcp", "wrong_arguments", "wrong_result", "cross_kind", "scalar", "duplicate",
                         "missing_and_forbidden"):
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"]["itemsView"] = "full"
            items = [copy.deepcopy(value["params"]["item"]) for value in self.wire[1::2]]
            if mutation == "command":
                items.append({"type": "commandExecution", "id": "forbidden", "command": "private-body"})
            elif mutation == "extra_mcp":
                extra = copy.deepcopy(items[-1])
                extra["id"] = "sixth-item"
                items.append(extra)
            elif mutation == "wrong_arguments":
                items[0]["arguments"] = {"probe_nonce": "c1-old"}
            elif mutation == "wrong_result":
                body = json.loads(items[0]["result"]["content"][0]["text"])
                body["prior_session"]["owner_status_verified"] = False
                items[0]["result"]["content"][0]["text"] = json.dumps(body)
            elif mutation == "cross_kind":
                items[0] = {"type": "agentMessage", "id": "v2-item-1", "text": "private-body"}
            elif mutation == "scalar":
                items.append(7)
            elif mutation == "duplicate":
                items.append(copy.deepcopy(items[0]))
            else:
                completed["turn"].pop("status")
                items[0].pop("arguments")
                items.append({"type": "fileChange", "id": "forbidden", "changes": []})
            completed["turn"]["items"] = items
            state = self.state(complete=False)
            state.observe(notification("turn/completed", completed))
            with self.subTest(mutation=mutation):
                proof = self.validate(self.business(state))
                self.assertEqual(proof["status"], "failed")
                self.assertEqual(state.summary()["native"]["unique_calls"], 5)
                self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i1_actual_last_agent_summary_is_reconciled_not_counted(self):
        state = self.state(complete=False)
        agent = {"type": "agentMessage", "id": "last-answer", "text": "private-body", "phase": "final_answer",
                 "delivery": None, "questions": None, "memoryCitation": None}
        state.observe(notification("item/started", {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                                                    "item": dict(agent, text="")}))
        state.observe(notification("item/completed", {"threadId": THREAD, "turnId": TURN, "completedAtMs": 2,
                                                      "item": agent}))
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="summary", items=[copy.deepcopy(agent)])
        state.observe(notification("turn/completed", completed))
        proof = self.validate(self.business(state))
        self.assertEqual(proof["status"], "verified", proof)
        self.assertEqual(state.summary()["native"]["unique_calls"], 5)
        metadata = state.summary()["appserver_event_proof"].get("completion")
        self.assertIsInstance(metadata, dict, "Required completion metadata was dropped")
        self.assertEqual(metadata["items_view"], "summary")
        self.assertEqual(metadata["items"][0]["item_id"], "last-answer")
        self.assertNotIn("private-body", repr(state.summary()))
        # A conflicting later completion cannot be hidden behind the earlier end.
        completed["turn"]["items"][0]["text"] = "changed private-body"
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "failed")

    def test_review_i1_snapshots_never_supply_missing_call_notifications(self):
        state = self.state(wires=self.wire[:1], complete=False)
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
        state.observe(notification("turn/completed", completed))
        summary = state.summary()
        self.assertEqual((summary["native"]["unique_calls"], summary["native"]["completed_calls"]), (1, 0))
        self.assertNotEqual(self.validate(self.business(state))["status"], "verified")

    def test_review_i2_unknown_then_known_call_conflicts_fail_without_admission(self):
        for mutation in ("arguments", "tool", "fingerprint", "completion_without_start", "wrong_step", "cross_kind",
                         "step_three_success"):
            count = 5 if mutation == "step_three_success" else 1
            state = self.state(wires=self.wire[:count], complete=False)
            missing = copy.deepcopy(self.wire[count - 1])
            missing["params"].pop("startedAtMs")
            state.observe(message(missing))
            before = state.summary()["native"]
            value = copy.deepcopy(self.wire[count - 1])
            if mutation == "arguments":
                value["params"]["item"]["arguments"] = {"probe_nonce": "c1-old"}
            elif mutation == "tool":
                value["params"]["item"]["tool"] = "memory_status"
            elif mutation == "fingerprint":
                value["params"]["item"]["readOnlyHint"] = False
            elif mutation == "completion_without_start":
                value = copy.deepcopy(self.wire[3])
            elif mutation == "wrong_step":
                value["params"]["item"]["id"] = "unexpected-bootstrap"
            elif mutation == "cross_kind":
                value["params"]["item"] = {"type": "agentMessage", "id": "v2-item-1", "text": "private-body"}
            else:
                value = copy.deepcopy(self.wire[5])
                value["params"]["item"].update(status="completed", result=copy.deepcopy(self.wire[3]["params"]["item"]["result"]))
            state.observe(message(value))
            with self.subTest(mutation=mutation):
                report = self.validate(self.business(state))
                self.assertEqual(report["status"], "failed")
                after = state.summary()
                self.assertEqual((after["native"]["unique_calls"], after["native"]["completed_calls"]),
                                 (before["unique_calls"], before["completed_calls"]))
                if mutation in ("arguments", "tool", "fingerprint", "cross_kind"):
                    self.assertTrue(after["appserver_event_proof"]["replay_detected"])
                self.assertNotIn("private-body", repr(after))

    def test_review_i2_discarded_replay_after_unknown_is_failure_not_suppressed(self):
        state = self.state(wires=[], complete=False)
        value = {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                 "item": {"type": "agentMessage", "id": "answer", "text": ""}}}
        state.observe(message(value))
        state.record_rpc_failure("C1_RPC_UNEXPECTED_EOF")
        value["params"]["item"]["text"] = "private-body"
        state.observe(message(value))
        self.assertEqual(self.business(state)["status"], "failed")
        self.assertTrue(state.summary()["appserver_event_proof"]["replay_detected"])
        self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i3_known_mcp_optional_fields_are_typed_even_with_other_missing_proof(self):
        cases = [("appContext", 7, "failed"), ("mcpAppUi", 7, "failed"),
                 ("appContext", {}, "unknown"), ("appContext", {"connectorId": 7}, "failed"),
                 ("appContext", {"appName": 7}, "failed"),
                 ("appContext", {"connectorId": "", "resourceUri": []}, "failed"),
                 ("mcpAppUi", {"resourceUri": ""}, "unknown"),
                 ("mcpAppUi", {"resourceUri": "", "preferredModelDisplayMode": "future"}, "failed"),
                 ("mcpAppUi", {"resourceUri": 7, "preferredModelDisplayMode": "inline"}, "failed")]
        for field, value, want in cases:
            raw = copy.deepcopy(self.wire[0])
            raw["params"]["item"][field] = value
            with self.subTest(field=field, value=value):
                self.assert_decode_problem(raw, want)
                if want == "failed":
                    raw["params"].pop("turnId")
                    self.assert_decode_problem(raw, "failed")
        for field in ("appContext", "mcpAppUi"):
            wires = copy.deepcopy(self.wire)
            wires[0]["params"]["item"][field] = 7
            self.assertEqual(self.validate(self.business(self.state(wires)))["status"], "failed")

    def test_review_i3_agent_optional_and_nested_fields_are_not_opaque(self):
        cases = [("phase", 7, "failed"), ("phase", "future", "failed"), ("delivery", "inline", "failed"),
                 ("questions", 7, "failed"), ("questions", [{"title": 7}], "failed"),
                 ("questions", [{"title": "", "options": [1]}], "failed"),
                 ("questions", [{"options": None}], "unknown"),
                 ("questions", [{"title": "", "extra": True}], "failed"),
                 ("memoryCitation", 7, "failed"), ("memoryCitation", {"entries": [], "threadIds": [7]}, "failed"),
                 ("memoryCitation", {"entries": [{}], "threadIds": []}, "unknown"),
                 ("memoryCitation", {"entries": [{"lineStart": True, "lineEnd": 1, "note": "", "path": ""}],
                                     "threadIds": []}, "failed"),
                 ("memoryCitation", {"entries": [{"lineStart": 0, "lineEnd": 2**32, "note": "", "path": ""}],
                                     "threadIds": []}, "failed")]
        for field, value, want in cases:
            raw = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN, "completedAtMs": 1,
                   "item": {"type": "agentMessage", "id": "answer", "text": "private-body", field: value}}}
            with self.subTest(field=field, value=value):
                self.assert_decode_problem(raw, want)
                if want == "failed":
                    raw["params"].pop("turnId")
                    self.assert_decode_problem(raw, "failed")
        state = self.state(complete=False)
        for completed in (False, True):
            state.observe(notification("item/completed" if completed else "item/started", {
                "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                "item": {"type": "agentMessage", "id": "answer", "text": "", "phase": 7}}))
        self.assertEqual(self.validate(self.business(state))["status"], "failed")

    def test_review_i3_nullable_default_and_nested_auxiliary_fields_remain_valid(self):
        contexts = [None, {"connectorId": "", "actionName": None, "appName": "", "linkId": None,
                           "resourceUri": "", "auxiliary": {"discard": "private-body"}}]
        displays = [None, {"preferredModelDisplayMode": "inline", "resourceUri": "", "auxiliary": 7},
                    {"preferredModelDisplayMode": "fullscreen", "resourceUri": "synthetic"}]
        for context in contexts:
            for ui in displays:
                wires = copy.deepcopy(self.wire)
                for value in wires:
                    value["params"]["item"].update(appContext=copy.deepcopy(context), mcpAppUi=copy.deepcopy(ui), auxiliary=True)
                with self.subTest(context=context is None, ui=ui is None):
                    report = self.validate(self.business(self.state(wires)))
                    self.assertEqual(report["status"], "verified", report)
        for phase in (None, "commentary", "partial_answer", "final_answer"):
            agent = {"type": "agentMessage", "id": "answer", "text": "private-body", "phase": phase,
                     "delivery": "async", "questions": [{"title": "", "options": None}, {"title": "", "options": []}],
                     "memoryCitation": {"entries": [{"lineStart": 0, "lineEnd": 2**32-1, "note": "private-body",
                                                       "path": "private-body", "auxiliary": True}],
                                        "threadIds": [""], "auxiliary": True}, "auxiliary": True}
            state = self.state(complete=False)
            for completed in (False, True):
                state.observe(notification("item/completed" if completed else "item/started", {
                    "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                    "item": copy.deepcopy(agent)}))
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"].update(itemsView="summary", items=[agent])
            state.observe(notification("turn/completed", completed))
            with self.subTest(phase=phase):
                report = self.validate(self.business(state))
                self.assertEqual(report["status"], "verified", report)
                self.assertNotIn("private-body", repr(state.summary()))
        user = self.user_message()
        user["params"]["item"]["auxiliary"] = True
        user["params"]["item"]["content"][0]["auxiliary"] = True
        try:
            event = self.api.decode_appserver_notification(message(user))
        except ValueError as error:
            self.fail("Schema-permitted auxiliary user properties were rejected: " + str(error))
        self.assertEqual(event.kind, "ignored_known")

    def test_review_i1_items_view_coverage_has_no_persisted_history_success_assumption(self):
        items = [copy.deepcopy(value["params"]["item"]) for value in self.wire[1::2]]
        for view, included, want in (("notLoaded", [], "verified"), ("full", items, "verified"),
                                     ("full", [], "unknown"), ("full", items[:-1], "unknown"),
                                     ("notLoaded", items, "failed"), ("summary", [], "unknown")):
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"].update(itemsView=view, items=copy.deepcopy(included))
            state = self.state(complete=False)
            state.observe(notification("turn/completed", completed))
            with self.subTest(view=view, count=len(included)):
                self.assertEqual(self.validate(self.business(state))["status"], want)
                self.assertEqual(state.summary()["native"]["unique_calls"], 5)
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].pop("itemsView")  # Exact schema default full, not notLoaded.
        completed["turn"]["items"] = items
        state = self.state(complete=False)
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "verified")
        business = self.business(state)
        completion = business["appserver_event_proof"].get("completion")
        self.assertIsInstance(completion, dict, "Final event proof must carry completion metadata")
        self.assertEqual(completion["items_view"], "full")
        business["appserver_event_proof"].pop("completion")
        self.assertEqual(self.validate(business)["status"], "unknown")
        business = self.business(state)
        business["appserver_event_proof"]["completion"]["items"][0]["arguments_sha256"] = "0" * 64
        business["appserver_event_proof"].pop("turn_started")
        self.assertEqual(self.validate(business)["status"], "failed")

    def test_review_i1_summary_missing_live_association_is_unknown_not_new_item_admission(self):
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="summary", items=[{
            "type": "agentMessage", "id": "not-observed-live", "text": "private-body"}])
        state = self.state(complete=False)
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "unknown")
        self.assertEqual(state.summary()["native"]["unique_calls"], 5)
        self.assertEqual(state.summary()["appserver_event_proof"]["discarded_items"], [])
        self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i2_matching_post_unknown_sequences_are_nonpositive_not_false_failures(self):
        for count in (0, 1, 2, 5, 10):
            state = self.state(wires=self.wire[:count], complete=False)
            before = state.summary()
            state.record_rpc_failure("C1_RPC_UNEXPECTED_EOF")
            for value in self.wire[count:]:
                state.observe(message(value))
            state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
            after = state.summary()
            with self.subTest(prefix=count):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                self.assertEqual(after["appserver_event_proof"]["failure_reasons"], [])
                self.assertEqual(after["native"], before["native"])
                self.assertEqual(after["appserver_event_proof"]["call_items"], before["appserver_event_proof"]["call_items"])
                self.assertEqual(after["appserver_event_proof"]["discarded_items"], before["appserver_event_proof"]["discarded_items"])
                self.assertFalse(after["appserver_event_proof"]["turn_completed"])
        state = self.state(wires=[], complete=False)
        state.record_rpc_failure("C1_RPC_UNEXPECTED_EOF")
        for completed in (False, True):
            state.observe(notification("item/completed" if completed else "item/started", {
                "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                "item": {"type": "agentMessage", "id": "unadmitted-answer", "text": "private-body"}}))
        self.assertEqual(self.business(state)["status"], "unknown")
        self.assertEqual(state.summary()["appserver_event_proof"]["discarded_items"], [])
        self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i1_partial_completion_still_checks_independent_snapshot_conflicts(self):
        state = self.state(complete=False)
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
        completed["turn"].pop("status")
        completed["turn"]["items"][0]["arguments"] = {"probe_nonce": "c1-old"}
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "failed")
        state = self.state(complete=False)
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
        completed["turn"]["items"][0].pop("tool")
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "unknown")
        self.assertEqual(state.summary()["native"]["unique_calls"], 5)

    def test_review_i1_unknown_completion_view_and_item_are_unknown_not_silently_accepted(self):
        for mutation in ("view", "kind", "missing_id"):
            state = self.state(complete=False)
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
            if mutation == "view":
                completed["turn"]["itemsView"] = "futureView"
            elif mutation == "kind":
                completed["turn"]["items"].append({"type": "futureItem", "id": "unknown-item"})
            else:
                completed["turn"]["items"][0].pop("id")
            state.observe(notification("turn/completed", completed))
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                self.assertFalse(state.summary()["appserver_event_proof"]["turn_completed"])

    def test_review_i1_full_coverage_includes_discarded_items_and_last_agent_must_match(self):
        state = self.state(complete=False)
        agents = []
        for number in (1, 2):
            agent = {"type": "agentMessage", "id": "answer-" + str(number), "text": "private-body"}
            agents.append(agent)
            for completed in (False, True):
                state.observe(notification("item/completed" if completed else "item/started", {
                    "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                    "item": copy.deepcopy(agent)}))
        full = copy.deepcopy(self.samples["session"]["turn_completed"])
        full["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]] + agents)
        state.observe(notification("turn/completed", full))
        self.assertEqual(self.validate(self.business(state))["status"], "verified")
        business = self.business(state)
        business["appserver_event_proof"]["completion"]["items"] = business["appserver_event_proof"]["completion"]["items"][:-1]
        self.assertEqual(self.validate(business)["status"], "unknown")
        business = self.business(state)
        business["appserver_event_proof"]["completion"].update(items_view="summary", items=[
            business["appserver_event_proof"]["completion"]["items"][-2]])
        self.assertEqual(self.validate(business)["status"], "failed")
        self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i2_missing_item_id_does_not_hide_known_nonce_handle_or_user_origin(self):
        for kind in ("bootstrap", "status", "user"):
            state = self.state(wires=self.wire[:2], complete=False)
            if kind == "bootstrap":
                value = copy.deepcopy(self.wire[0])
                value["params"]["item"]["arguments"] = {"probe_nonce": "c1-old"}
            elif kind == "status":
                value = copy.deepcopy(self.wire[2])
                value["params"]["item"]["arguments"] = {"session_handle": "private-body"}
            else:
                value = self.user_message(text="private-body")
            value["params"]["item"].pop("id")
            state.observe(message(value))
            with self.subTest(kind=kind):
                self.assertEqual(self.validate(self.business(state))["status"], "failed")
                self.assertEqual(state.summary()["native"]["unique_calls"], 1)
                self.assertNotIn("private-body", repr(state.summary()))
        for value in (copy.deepcopy(self.wire[0]), copy.deepcopy(self.wire[2]), self.user_message()):
            state = self.state(wires=self.wire[:2], complete=False)
            value["params"]["item"].pop("id")
            state.observe(message(value))
            self.assertEqual(self.validate(self.business(state))["status"], "unknown")

    def test_review_i1_exact_older_agent_completion_duplicate_cannot_change_last_agent(self):
        state = self.state(complete=False)
        agents = []
        for number in (1, 2):
            agent = {"type": "agentMessage", "id": "answer-" + str(number), "text": "private-body"}
            agents.append(agent)
            for completed in (False, True):
                state.observe(notification("item/completed" if completed else "item/started", {
                    "threadId": THREAD, "turnId": TURN, "completedAtMs" if completed else "startedAtMs": 1,
                    "item": copy.deepcopy(agent)}))
        state.observe(notification("item/completed", {"threadId": THREAD, "turnId": TURN,
                                                      "completedAtMs": 1, "item": agents[0]}))
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].update(itemsView="summary", items=[agents[1]])
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "verified")
        self.assertEqual(state.summary()["appserver_event_proof"]["last_agent_item_id"], "answer-2")
        self.assertEqual(state.summary()["native"]["unique_calls"], 5)
        self.assertNotIn("private-body", repr(state.summary()))

    def test_review_i1_partial_duplicates_after_end_are_unknown_unless_independently_conflicting(self):
        for kind in ("mcp", "agent", "turn"):
            state = self.state(complete=False)
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            agent = {"type": "agentMessage", "id": "answer", "text": "private-body"}
            if kind == "agent":
                for ended in (False, True):
                    state.observe(notification("item/completed" if ended else "item/started", {
                        "threadId": THREAD, "turnId": TURN, "completedAtMs" if ended else "startedAtMs": 1,
                        "item": copy.deepcopy(agent)}))
                completed["turn"].update(itemsView="summary", items=[agent])
            state.observe(notification("turn/completed", completed))
            self.assertEqual(self.validate(self.business(state))["status"], "verified")
            if kind == "mcp":
                value = copy.deepcopy(self.wire[1])
                value["params"]["item"].pop("arguments")
            elif kind == "agent":
                value = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN,
                         "completedAtMs": 1, "item": {"type": "agentMessage", "id": "answer"}}}
            else:
                completed["turn"].pop("status")
                value = {"method": "turn/completed", "params": completed}
            state.observe(message(value))
            with self.subTest(kind=kind):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                self.assertEqual(state.summary()["native"]["unique_calls"], 5)
                self.assertEqual(state.summary()["native"]["completed_calls"], 5)
                self.assertNotIn("private-body", repr(state.summary()))
        state = self.state()
        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
        completed["turn"].pop("status")
        completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
        completed["turn"]["items"][0]["arguments"] = {"probe_nonce": "c1-old"}
        state.observe(notification("turn/completed", completed))
        self.assertEqual(self.validate(self.business(state))["status"], "failed")

    def test_fix2_r1_missing_snapshot_result_preserves_unknown_and_hash_provenance(self):
        for mutation in ("absent_result", "null_result", "missing_text", "missing_text_null_structured"):
            state = self.state(complete=False)
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
            item = completed["turn"]["items"][0]
            body = json.loads(item["result"]["content"][0]["text"])
            if mutation == "absent_result":
                item.pop("result")
            elif mutation == "null_result":
                item["result"] = None
            else:
                item["result"].pop("content")
                item["result"]["structuredContent"] = body if mutation == "missing_text" else None
            event = self.api.decode_appserver_notification(notification("turn/completed", completed))
            metadata = event.completion["items"][0]
            self.assertIsNone(metadata["fingerprint"], "Incomplete result is not an exact full-item replay claim")
            if mutation == "missing_text":
                canonical_body = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                self.assertEqual(metadata["result_sha256"], hashlib.sha256(canonical_body).hexdigest())
            else:
                self.assertIsNone(metadata["result_sha256"], "A partial result envelope is not a business body")
            state.observe(notification("turn/completed", completed))
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                self.assertEqual(state.summary()["appserver_event_proof"]["failure_reasons"], [])
                self.assertEqual((state.summary()["native"]["unique_calls"], state.summary()["native"]["completed_calls"]), (5, 5))
                for handle in self.handles:
                    self.assertNotIn(handle, repr(state.summary()))

    def test_fix2_r1_partial_snapshot_still_rejects_independent_known_contradictions(self):
        for mutation in ("false_owner", "foreign_body_owner", "backend", "arguments", "tool", "terminal", "thread"):
            state = self.state(complete=False)
            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
            completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
            item = completed["turn"]["items"][0]
            body = json.loads(item["result"].pop("content")[0]["text"])
            item["result"]["structuredContent"] = body
            if mutation == "false_owner":
                body["prior_session"]["owner_status_verified"] = False
            elif mutation == "foreign_body_owner":
                body["session_id"] = "foreign-owner"
            elif mutation == "backend":
                body["backend"] = "private-body"
            elif mutation == "arguments":
                item["arguments"] = {"probe_nonce": "c1-old"}
            elif mutation == "tool":
                item["tool"] = "memory_status"
            elif mutation == "terminal":
                item["status"] = "failed"
            else:
                completed["threadId"] = "foreign-owner"
            state.observe(notification("turn/completed", completed))
            with self.subTest(mutation=mutation):
                self.assertEqual(self.validate(self.business(state))["status"], "failed")
                self.assertEqual(state.summary()["native"]["unique_calls"], 5)
                self.assertNotIn("private-body", repr(state.summary()))

    def test_fix2_r1_partial_live_or_turn_duplicate_is_not_a_verified_hash_conflict(self):
        for kind in ("live", "turn"):
            for missing in ("result", "content"):
                state = self.state(complete=False)
                completed = copy.deepcopy(self.samples["session"]["turn_completed"])
                completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
                state.observe(notification("turn/completed", completed))
                self.assertEqual(self.validate(self.business(state))["status"], "verified")
                if kind == "turn":
                    value = {"method": "turn/completed", "params": copy.deepcopy(completed)}
                    item = value["params"]["turn"]["items"][0]
                else:
                    value = copy.deepcopy(self.wire[1])
                    item = value["params"]["item"]
                if missing == "result":
                    item["result"] = None
                else:
                    body = json.loads(item["result"].pop("content")[0]["text"])
                    item["result"]["structuredContent"] = body
                state.observe(message(value))
                with self.subTest(kind=kind, missing=missing):
                    self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                    self.assertEqual(state.summary()["appserver_event_proof"]["failure_reasons"], [])
                    self.assertEqual(state.summary()["native"]["unique_calls"], 5)

    def test_fix2_r2_partial_mcp_starts_are_observed_not_admitted(self):
        for missing in ("arguments", "tool"):
            state = self.state(wires=[], complete=False)
            start = copy.deepcopy(self.wire[0])
            start["params"]["item"].pop(missing)
            state.observe(message(start))
            self.assertEqual(self.business(state)["status"], "unknown")
            state.observe(message(self.wire[1]))
            with self.subTest(missing=missing):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                summary = state.summary()
                self.assertEqual((summary["native"]["unique_calls"], summary["native"]["completed_calls"]), (0, 0))
                self.assertEqual(summary["appserver_event_proof"]["call_items"], [])
                self.assertEqual(summary["appserver_event_proof"]["failure_reasons"], [])
            conflicting = copy.deepcopy(self.wire[1])
            conflicting["params"]["item"]["arguments"] = {"probe_nonce": "c1-old"}
            state.observe(message(conflicting))
            self.assertEqual(self.validate(self.business(state))["status"], "failed")
            self.assertEqual(state.summary()["native"]["unique_calls"], 0)
        state = self.state(wires=[], complete=False)
        state.record_rpc_failure("C1_RPC_UNEXPECTED_EOF")
        state.observe(message(self.wire[1]))
        self.assertEqual(self.business(state)["status"], "failed", "A genuinely unseen finish still fails")
        self.assertEqual(state.summary()["native"]["unique_calls"], 0)

    def test_fix2_r2_partial_finish_receipts_do_not_invent_overlap_or_positive_calls(self):
        state = self.state(wires=[], complete=False)
        start = copy.deepcopy(self.wire[0])
        start["params"]["item"].pop("arguments")
        finish = copy.deepcopy(self.wire[1])
        finish["params"]["item"]["result"] = None
        state.observe(message(start))
        state.observe(message(finish))
        for value in self.wire[2:]:
            state.observe(message(value))
        state.observe(message(self.wire[1]))  # Actual later full finish, not a fabricated backfill.
        state.observe(notification("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"])))
        report = self.validate(self.business(state))
        self.assertEqual(report["status"], "unknown", report)
        summary = state.summary()
        self.assertEqual((summary["native"]["unique_calls"], summary["native"]["completed_calls"]), (0, 0))
        self.assertEqual(summary["appserver_event_proof"]["call_items"], [])
        self.assertEqual(summary["appserver_event_proof"]["failure_reasons"], [])
        self.assertFalse(summary["appserver_event_proof"]["turn_completed"])

    def test_fix2_r2_partial_agent_and_user_starts_match_finishes_without_admission(self):
        for kind in ("agent", "user"):
            state = self.state(wires=[], complete=False)
            if kind == "agent":
                started = {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                           "item": {"type": "agentMessage", "id": "answer", "text": "private-body"}}}
                finished = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN, "completedAtMs": 2,
                            "item": copy.deepcopy(started["params"]["item"])}}
                started["params"]["item"].pop("text")
            else:
                started, finished = self.user_message(), self.user_message(completed=True)
                started["params"]["item"]["content"][0].pop("text")
            state.observe(message(started))
            state.observe(message(finished))
            with self.subTest(kind=kind):
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                self.assertEqual(state.summary()["native"]["unique_calls"], 0)
                self.assertEqual(state.summary()["appserver_event_proof"]["discarded_items"], [])
                self.assertEqual(state.summary()["appserver_event_proof"]["failure_reasons"], [])
            wrong = copy.deepcopy(finished)
            if kind == "agent":
                wrong["params"]["item"]["text"] = "changed private-body"
            else:
                wrong["params"]["item"]["content"][0]["text"] = "changed private-body"
            state.observe(message(wrong))
            self.assertEqual(self.business(state)["status"], "failed")
            self.assertNotIn("private-body", repr(state.summary()))
            unseen = self.state(wires=[], complete=False)
            unseen.observe(message(finished))
            self.assertEqual(self.business(unseen)["status"], "failed")

    def test_fix2_r2_partial_discarded_finish_then_full_replay_is_checked(self):
        state = self.state(wires=[], complete=False)
        started = {"method": "item/started", "params": {"threadId": THREAD, "turnId": TURN, "startedAtMs": 1,
                   "item": {"type": "agentMessage", "id": "answer"}}}
        partial = {"method": "item/completed", "params": {"threadId": THREAD, "turnId": TURN, "completedAtMs": 2,
                   "item": copy.deepcopy(started["params"]["item"])}}
        full = copy.deepcopy(partial)
        full["params"]["item"]["text"] = "private-body"
        state.observe(message(started))
        state.observe(message(partial))
        state.observe(message(full))
        self.assertEqual(self.business(state)["status"], "unknown")
        self.assertEqual(state.summary()["appserver_event_proof"]["discarded_items"], [])
        conflict = copy.deepcopy(full)
        conflict["params"]["item"]["type"] = "reasoning"
        conflict["params"]["item"].pop("text")
        state.observe(message(conflict))
        self.assertEqual(self.business(state)["status"], "failed")
        self.assertEqual(state.summary()["native"]["unique_calls"], 0)
        self.assertNotIn("private-body", repr(state.summary()))

    def test_fix3_r3_partial_bootstrap_scope_survives_missing_handle_and_text(self):
        # Missing full normalization must not suppress independently known scope.
        for route in ("snapshot", "live"):
            for mutation in ("missing_only", "foreign_session", "foreign_project", "foreign_seed_project"):
                with self.subTest(route=route, mutation=mutation):
                    state = self.state()
                    self.assertEqual(self.validate(self.business(state))["status"], "verified")
                    before = state.summary()
                    if route == "snapshot":
                        completed = copy.deepcopy(self.samples["session"]["turn_completed"])
                        completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
                        value = {"method": "turn/completed", "params": completed}
                        item = completed["turn"]["items"][0]
                    else:
                        value = copy.deepcopy(self.wire[1])
                        item = value["params"]["item"]
                    body = json.loads(item["result"].pop("content")[0]["text"])
                    body.pop("session_handle")
                    if mutation == "foreign_session":
                        body["session_id"] = "foreign-private-owner"
                    elif mutation == "foreign_project":
                        # Internally consistent wrong project: not a seed mismatch.
                        body["project_key"] = "local:" + NONCE + "-b"
                        body["prior_session"]["project_key"] = "local:" + NONCE + "-b"
                        body["foreign_project"]["project_key"] = "local:" + NONCE + "-a"
                    elif mutation == "foreign_seed_project":
                        body["foreign_project"]["project_key"] = "local:unbound-private-project"
                    item["result"]["structuredContent"] = body
                    event = self.api.decode_appserver_notification(message(value))
                    proof = event.completion["items"][0] if route == "snapshot" else None
                    self.assertIsNone(proof["result_sha256"] if proof is not None else event.item.result_sha256)
                    self.assertIsNone(proof["fingerprint"] if proof is not None else event.item.fingerprint)
                    state.observe(message(value))
                    self.assertEqual(self.validate(self.business(state))["status"],
                                     "unknown" if mutation == "missing_only" else "failed")
                    after = state.summary()
                    self.assertEqual(after["native"], before["native"])
                    self.assertEqual(after["appserver_event_proof"]["call_items"], before["appserver_event_proof"]["call_items"])
                    if mutation == "missing_only":
                        self.assertEqual(after["appserver_event_proof"]["failure_reasons"], [])
                    for raw in (*self.handles, "foreign-private-owner", "local:unbound-private-project"):
                        self.assertNotIn(raw, repr(after))

    def test_fix3_r3_partial_status_scope_is_checked_in_each_actual_channel(self):
        for route in ("snapshot", "live"):
            for channel in ("structured", "text"):
                for mutation in ("missing_only", "foreign_session", "foreign_project"):
                    with self.subTest(route=route, channel=channel, mutation=mutation):
                        state = self.state()
                        before = state.summary()
                        if route == "snapshot":
                            completed = copy.deepcopy(self.samples["session"]["turn_completed"])
                            completed["turn"].update(itemsView="full", items=[copy.deepcopy(v["params"]["item"]) for v in self.wire[1::2]])
                            value = {"method": "turn/completed", "params": completed}
                            item = completed["turn"]["items"][1]
                        else:
                            value = copy.deepcopy(self.wire[3])
                            item = value["params"]["item"]
                        body = json.loads(item["result"].pop("content")[0]["text"])
                        body.pop("session_handle")
                        if mutation == "foreign_session":
                            body["session_id"] = "foreign-private-owner"
                        elif mutation == "foreign_project":
                            body["project_key"] = "local:unbound-private-project"
                        item["result"]["structuredContent"] = body if channel == "structured" else None
                        if channel == "text":
                            item["result"]["content"] = [{"type": "text", "text": json.dumps(body)}]
                        state.observe(message(value))
                        self.assertEqual(self.validate(self.business(state))["status"],
                                         "unknown" if mutation == "missing_only" else "failed")
                        after = state.summary()
                        self.assertEqual(after["native"], before["native"])
                        self.assertEqual(after["appserver_event_proof"]["call_items"], before["appserver_event_proof"]["call_items"])
                        if mutation == "missing_only":
                            self.assertEqual(after["appserver_event_proof"]["failure_reasons"], [])
                        for raw in (*self.handles, "foreign-private-owner", "local:unbound-private-project"):
                            self.assertNotIn(raw, repr(after))

    def test_fix3_r3_absent_scope_facts_never_fill_proof_or_reopen_admission(self):
        for route in ("snapshot", "live"):
            with self.subTest(route=route):
                state = self.state(wires=[], complete=False)
                start = copy.deepcopy(self.wire[0])
                start["params"]["item"].pop("arguments")
                state.observe(message(start))
                finish = copy.deepcopy(self.wire[1])
                finish["params"]["item"]["result"] = {"structuredContent": {}}
                if route == "snapshot":
                    completed = copy.deepcopy(self.samples["session"]["turn_completed"])
                    completed["turn"].update(itemsView="full", items=[finish["params"]["item"]])
                    value = {"method": "turn/completed", "params": completed}
                else:
                    value = finish
                state.observe(message(value))
                self.assertEqual(self.validate(self.business(state))["status"], "unknown")
                after = state.summary()
                self.assertEqual((after["native"]["unique_calls"], after["native"]["completed_calls"]), (0, 0))
                self.assertEqual(after["appserver_event_proof"]["call_items"], [])
                self.assertEqual(after["appserver_event_proof"]["failure_reasons"], [])
                self.assertFalse(after["appserver_event_proof"]["turn_completed"])
                finish["params"]["item"]["result"]["structuredContent"]["session_id"] = "foreign-private-owner"
                state.observe(message(value))
                self.assertEqual(self.validate(self.business(state))["status"], "failed")
                self.assertEqual(state.summary()["native"]["unique_calls"], 0)
                self.assertEqual(state.summary()["appserver_event_proof"]["call_items"], [])
                self.assertNotIn("foreign-private-owner", repr(state.summary()))

    def test_independent_samples_are_extended_without_rewriting_prior_examples(self):
        section = self.samples.get("appserver_events")
        self.assertIsInstance(section, dict, "Task4 independent v2 samples are missing")
        self.assertFalse(section["provenance"]["native_capture"])
        self.assertEqual(section["provenance"]["schema_tag"], "rust-v0.163.0")
        for value in section["notifications"]:
            event = self.api.decode_appserver_notification(message(value))
            self.assertIn(event.kind, ("call_started", "call_finished", "ignored_known"))


if __name__ == "__main__":
    unittest.main()
