"""Task5 real Windows Job/duplex/local SDK contracts; no native host or API."""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import codex_c1_bundle as bundles
from scripts import codex_c1_executor as old_exec
from scripts.codex_c1_process import OwnedProcess, OwnedProcessError
from tests.contracts.test_codex_c1_executor import pid_exited
from tests.unit.test_codex_c1_bundle import approval as simulation_approval

ROOT = Path(__file__).resolve().parents[2]
GATE = "C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING"


@unittest.skipUnless(sys.platform == "win32", "actual Windows Job-owned offline app-server")
class AppServerCandidateTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("scripts.codex_c1_appserver_candidate")
            self.review = importlib.import_module("scripts.prepare_codex_c1_appserver_review")
            self.runtime = importlib.import_module("scripts.codex_c1_appserver_runtime")
        except ModuleNotFoundError as error:
            if error.name and error.name.startswith("scripts."):
                self.fail("Task5 offline app-server candidate/review/runtime implementation is missing")
            raise
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.launches, self.processes = [], []
        self.fresh("initial")

    def fresh(self, name):
        self.bundle = self.directory / name
        self.review.prepare_appserver_review(self.bundle, "c1-appserver-" + name.replace("_", "-"))
        self.snapshot = bundles.load_snapshot(self.bundle)
        self.approval = simulation_approval(self.snapshot, actions=[
            "install_configs", "inspect", "run_project", "rollback_config"])
        self.receipt = bundles.install_configs(self.snapshot, self.approval)
        self.contract = self.review.transport_contract(self.snapshot)
        self.launches, self.processes = [], []

    def factory(self, mode="success", seconds=15, *, project="a", mutate=None,
                send_failure=None, cleanup_unknown=False, stopped=None):
        bundle = self.bundle
        prefix = [sys.executable, "-I", str(ROOT / "tests/fixtures/codex_c1_appserver_fake.py"),
                  "--bundle", str(bundle), "--project", project, "--mode", mode]

        def create(*, cwd, env, deadline, stdin_mode):
            self.assertEqual(cwd, bundle / "projects" / project)
            self.assertEqual(stdin_mode, "pipe")
            self.assertNotIn("MEM0_API_KEY", {key.upper(): value for key, value in env.items()})
            self.assertLessEqual(deadline - time.monotonic(), 180)
            self.assertFalse((bundle / "attempts" / (project + "-reservation.json")).exists())
            self.launches.append(prefix)
            if mutate:
                mutate()
            process = OwnedProcess(prefix, cwd=cwd, env=env, stdin_mode=stdin_mode,
                                   deadline=min(deadline, time.monotonic() + seconds))
            self.assertTrue(process.is_job_member())
            self.processes.append(process)
            if send_failure:
                original_send = process.send_line
                def failed_send(raw):
                    request = json.loads(raw)
                    if request.get("method") == send_failure:
                        self.assertTrue((bundle / "attempts" / (project + "-reservation.json")).is_file())
                        original_send(raw)  # Actual bytes written, then acknowledgement becomes ambiguous.
                        raise OwnedProcessError("C1_PROCESS_STDIN_FAILED")
                    original_send(raw)
                process.send_line = failed_send
            if cleanup_unknown or stopped:
                original_stop = process.stop
                def stop():
                    result = original_stop()  # Never replace real Job cleanup with a mocked result.
                    if stopped:
                        stopped()
                    if cleanup_unknown:
                        return {"root_exited": True, "descendants_exited": False, "reason": "cleanup_unconfirmed"}
                    return result
                process.stop = stop
            return process
        return create

    def run_fake(self, mode="success", *, project="a", **kwargs):
        return self.api.run_appserver_simulation(
            self.snapshot, project, self.approval, self.receipt, self.contract,
            process_factory=self.factory(mode, project=project, **kwargs), version_proof="codex-cli 0.163.0")

    def observed(self):
        return json.loads((self.bundle / "fake-appserver-observations.json").read_text("utf-8"))

    def assert_stopped(self, report, *, reserved=True):
        self.assertNotEqual(report["status"], "verified", report)
        self.assertIs(report["attempt_reserved"], reserved)
        self.assertTrue(report["shutdown"]["cleanup"]["root_exited"], report)
        self.assertTrue(report["shutdown"]["cleanup"]["descendants_exited"], report)
        self.assertTrue(report["shutdown"]["io_threads_stopped"], report)
        if reserved:
            self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
            with self.assertRaises(ValueError):
                bundles.reserve_attempt(self.snapshot, "b", self.approval)
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
        self.assertEqual(len(self.launches), 1)

    def test_real_sdk_five_plus_five_natural_eof_and_final_ledger(self):
        report = self.run_fake()
        self.assertEqual(report["status"], "verified", report)
        self.assertEqual(report["execution_origin"], "offline_simulation")
        self.assertEqual(report["transport_kind"], "app_server_stdio_0.163.0")
        self.assertFalse(report["native_evidence_claimed"])
        self.assertEqual(report["native_entry_status"], GATE)
        self.assertEqual((report["transport_count"], report["business_count"]), (5, 5))
        self.assertTrue(all(report[key] is True for key in (
            "process_started", "attempt_reserved", "thread_start_sent", "turn_start_sent", "input_eof_sent")))
        self.assertIsNone(report["pending_client_id"])
        self.assertEqual(report["finalization"], {"attempted": True, "confirmed": True})
        self.assertTrue(report["session"]["new_turn_verified"])
        self.assertEqual(report["session"]["state"], "protocol_complete")
        self.assertEqual(report["session"]["policy"]["approvals_reviewer"], "user")
        self.assertEqual(report["session"]["policy"]["network_access_source"], "explicit")
        self.assertEqual(report["shutdown"], {
            "input_eof_sent": True, "stdin_eof": True, "stdout_eof": True, "stderr_eof": True,
            "natural_exit_code": 0, "forced_termination": False, "io_failure": None,
            "io_threads_stopped": True,
            "cleanup": {"root_exited": True, "descendants_exited": True, "reason": "job_empty"}})
        self.assertEqual(self.observed()["methods"], ["initialize", "initialized", "config/read", "hooks/list",
                         "account/rateLimits/read", "thread/start", "turn/start"])
        self.assertTrue(self.observed()["reservation_before_thread"])
        self.assertTrue(self.observed()["input_eof_observed"])
        self.assertEqual(self.observed()["sdk_calls"], 5)
        rows = [json.loads(path.read_text("utf-8")) for path in (self.bundle / "audit/a").glob("*.json")]
        self.assertEqual(sum(row["event"] == "request_transport" for row in rows), 5)
        self.assertEqual(sum(row["event"] == "request_business" for row in rows), 5)
        mapping = report["business"]["call_mapping"]
        self.assertEqual([row["call_id"] for row in mapping], ["offline-mcp-call-" + str(i) for i in range(1, 6)])
        self.assertTrue(all(row["call_id"] != row["item_id"] for row in mapping))
        self.assertEqual(json.loads((self.bundle / "attempts/a-report.json").read_text())["state"], "verified")
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
        self.assertTrue(pid_exited(self.observed()["pid"]))

    def test_early_actual_messages_replay_in_wire_order_and_full_completion_is_observed(self):
        for mode in ("early_items", "all_before_response", "thread_notice_first", "full_completion"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], "verified", report)
                proof = report["events"]["appserver_event_proof"]
                self.assertTrue(proof["turn_started"] and proof["turn_completed"])
                self.assertEqual(len(proof["call_items"]), 5)
                self.assertEqual(proof["completion"]["items_view"], "full" if mode == "full_completion" else "notLoaded")

    def test_wrong_returned_policy_reviewer_root_model_provider_or_network_never_sends_turn(self):
        for mode in ("wrong_policy", "wrong_reviewer", "wrong_root", "wrong_model", "wrong_provider", "wrong_network"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], "failed", report)
                self.assertIs(report["thread_start_sent"], True)
                self.assertIs(report["turn_start_sent"], False)
                self.assertNotIn("turn/start", self.observed()["methods"])
                self.assert_stopped(report)

    def assert_final_fix_complete(self, report):
        self.assertEqual(report["status"], "verified", report)
        self.assertEqual(report["session"]["state"], "protocol_complete")
        self.assertEqual(report["session"]["failure_codes"], [])
        proof = report["events"]["appserver_event_proof"]
        self.assertEqual((proof["unique_calls"], proof["completed_calls"]), (5, 5))
        self.assertEqual(proof["failure_reasons"], [])
        self.assertEqual(proof["unknown_reasons"], [])
        self.assertEqual((report["transport_count"], report["business_count"]), (5, 5))
        self.assertEqual(report["shutdown"], {
            "input_eof_sent": True, "stdin_eof": True, "stdout_eof": True, "stderr_eof": True,
            "natural_exit_code": 0, "forced_termination": False, "io_failure": None,
            "io_threads_stopped": True,
            "cleanup": {"root_exited": True, "descendants_exited": True, "reason": "job_empty"}})
        observed = self.observed()
        self.assertEqual((observed["thread_count"], observed["turn_count"], observed["sdk_calls"]), (1, 1, 5))
        self.assertTrue(observed["reservation_before_thread"])
        self.assertTrue(observed["input_eof_observed"])
        self.assertTrue(pid_exited(observed["pid"]))
        rows = [json.loads(path.read_text("utf-8")) for path in (self.bundle / "audit/a").glob("*.json")]
        self.assertEqual(sum(row["event"] == "request_transport" for row in rows), 5)
        self.assertEqual(sum(row["event"] == "request_business" for row in rows), 5)
        self.assertEqual(report["finalization"], {"attempted": True, "confirmed": True})
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
        self.assertEqual(len(self.launches), 1)

    def test_final_w1_malformed_known_response_optionals_never_send_turn_or_call_sdk(self):
        for mode in ("bad_disabled_plugins", "bad_multi_agent_mode", "bad_service_tier", "bad_reasoning_effort",
                     "bad_instruction_sources", "bad_runtime_roots"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], "failed", report)
                self.assertIs(report["thread_start_sent"], True)
                self.assertIs(report["turn_start_sent"], False)
                self.assertFalse(report["session"]["thread_response_verified"])
                self.assertIn("C1_APPSERVER_SCHEMA_INVALID", report["session"]["failure_codes"])
                observed = self.observed()
                self.assertNotIn("turn/start", observed["methods"])
                self.assertEqual((observed["thread_count"], observed["turn_count"], observed["sdk_calls"]), (1, 0, 0))
                self.assertEqual((report["transport_count"], report["business_count"]), (0, 0))
                self.assert_stopped(report)

    def test_final_w1_valid_response_optional_omissions_null_defaults_and_extras_complete(self):
        for mode in ("response_optionals_omitted", "response_optionals_null_defaults", "response_optionals_custom_extras"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assert_final_fix_complete(report)
                self.assertEqual(report["session"]["policy"]["network_access_source"],
                                 "schema_default" if mode == "response_optionals_omitted" else "explicit")
                self.assertNotIn("private-synthetic-instruction-source", json.dumps(report))

    def test_final_w2_exact_bound_thread_notice_repeats_during_collecting_and_after_completion(self):
        for mode in ("thread_duplicate_collecting", "thread_duplicate_after_completion"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assert_final_fix_complete(report)
                self.assertEqual(self.observed()["thread_notice_repeats"], 1)
                self.assertEqual(self.observed()["methods"].count("thread/start"), 1)
                self.assertEqual(self.observed()["methods"].count("turn/start"), 1)

    def test_final_w2_changed_snapshot_foreign_and_additional_thread_still_fail_in_both_phases(self):
        for stage in ("collecting", "after_completion"):
            for change in ("conflict", "foreign", "additional"):
                mode = "thread_" + change + "_" + stage
                with self.subTest(mode=mode):
                    if self.launches:
                        self.fresh(mode)
                    report = self.run_fake(mode)
                    self.assertEqual(report["status"], "failed", report)
                    self.assertIn("C1_APPSERVER_THREAD_MISMATCH", report["session"]["failure_codes"])
                    if change != "conflict":
                        self.assertIn("appserver_event_identity_mismatch",
                                      report["events"]["appserver_event_proof"]["failure_reasons"])
                    self.assertEqual(self.observed()["thread_notice_repeats"], 1)
                    self.assertEqual(self.observed()["thread_count"], 1)
                    self.assertEqual(self.observed()["turn_count"], 1)
                    if stage == "after_completion":
                        self.assertEqual((report["transport_count"], report["business_count"]), (5, 5))
                    self.assert_stopped(report)

    def test_final_w2_permitted_thread_duplicates_do_not_hide_a_late_remote_error(self):
        report = self.run_fake("thread_duplicate_then_late_error")
        self.assertEqual(report["status"], "failed", report)
        self.assertEqual(self.observed()["thread_notice_repeats"], 2)
        self.assertEqual((report["transport_count"], report["business_count"]), (5, 5))
        self.assertIn("C1_RPC_REMOTE_ERROR", report["events"]["appserver_event_proof"]["failure_reasons"])
        self.assert_stopped(report)

    def test_thread_remote_failure_consumes_reservation_and_unknown_source_cannot_start_turn(self):
        for mode in ("thread_error", "unknown_source"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertIs(report["turn_start_sent"], False)
                self.assert_stopped(report)

    def test_missing_or_mutated_authority_never_invokes_factory(self):
        create = self.factory()
        for mutate in (lambda: self.approval.update(actions=["install_configs"]),
                       lambda: self.approval.update(scope="native-c1-wiring")):
            with self.subTest(mutate=mutate):
                original = dict(self.approval)
                mutate()
                with self.assertRaises(ValueError):
                    self.api.run_appserver_simulation(self.snapshot, "a", self.approval, self.receipt, self.contract,
                        process_factory=create, version_proof="codex-cli 0.163.0")
                self.approval.clear()
                self.approval.update(original)
        (self.bundle / "approval.json").unlink()
        with self.assertRaises((ValueError, OSError)):
            self.run_fake()
        self.assertFalse(self.launches)
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_native_origin_default_factory_and_untrusted_contract_are_rejected_before_launch(self):
        for kwargs in ({"execution_origin": "native_codex"}, {"process_factory": OwnedProcess},
                       {"process_factory": None}, {"version_proof": "codex-cli 0.164.0"}):
            with self.subTest(kwargs=kwargs):
                args = {"process_factory": self.factory(), "version_proof": "codex-cli 0.163.0", **kwargs}
                with self.assertRaises(ValueError):
                    self.api.run_appserver_simulation(self.snapshot, "a", self.approval, self.receipt, self.contract, **args)
        changed = dict(self.contract, native_entry_enabled=True)
        with self.assertRaises(ValueError):
            self.api.run_appserver_simulation(self.snapshot, "a", self.approval, self.receipt, changed,
                process_factory=self.factory(), version_proof="codex-cli 0.163.0")
        self.assertFalse(self.launches)

    def test_preflight_denial_reports_real_process_but_no_reserved_attempt(self):
        report = self.run_fake("usage_denied")
        self.assertIs(report["process_started"], True)
        self.assertIs(report["thread_start_sent"], False)
        self.assertIs(report["turn_start_sent"], False)
        self.assertEqual(report["finalization"], {"attempted": False, "confirmed": None})
        self.assert_stopped(report, reserved=False)
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_preflight_source_authority_recheck_blocks_reservation_after_real_rpc(self):
        original = self.runtime.validate_preflight
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.bundle / "approval.json").unlink()
            return result
        with patch.object(self.runtime, "validate_preflight", side_effect=revoke):
            report = self.run_fake()
        self.assertIs(report["attempt_reserved"], False)
        self.assertIs(report["thread_start_sent"], False)
        self.assertNotIn("thread/start", self.observed()["methods"])
        self.assert_stopped(report, reserved=False)

    def test_unknown_interaction_never_gets_any_callback_response(self):
        report = self.run_fake("unknown_interaction")
        self.assert_stopped(report)
        self.assertEqual(self.observed()["callback_results"], [])
        self.assertIs(report["approval_cancel_sent"], False)
        self.assertIs(report["interrupt_sent"], False)

    def test_known_approval_only_cancels_and_interrupts_verified_nonpending_turn(self):
        for mode, expected in (("approval", {"decision": "cancel"}),
                               ("file_approval", {"decision": "cancel"}),
                               ("permissions", {"permissions": {}, "scope": "turn"})):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], "failed", report)
                self.assertTrue(report["approval_cancel_sent"])
                self.assertTrue(report["interrupt_sent"])
                self.assertEqual(self.observed()["callback_results"], [expected])
                self.assertTrue(self.observed()["interrupt_observed"])
                self.assertEqual(self.observed()["sdk_calls"], 0)
                self.assert_stopped(report)

    def test_pending_turn_response_allows_cancel_but_never_interrupt_or_fake_verified_id(self):
        report = self.run_fake("pending_approval")
        self.assertEqual(report["status"], "failed", report)
        self.assertIs(report["approval_cancel_sent"], True)
        self.assertIs(report["interrupt_sent"], False)
        self.assertIsNotNone(report["pending_client_id"])
        self.assertFalse(report["session"]["new_turn_verified"])
        self.assertIsNone(report["session"]["turn_id"])
        self.assertNotIn("turn/interrupt", self.observed()["methods"])
        self.assert_stopped(report)

    def test_late_rpc_error_and_unknown_request_are_not_hidden_by_expected_eof(self):
        for mode in ("late_error", "late_unknown"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual((report["transport_count"], report["business_count"]), (5, 5))
                self.assertNotEqual(report["evidence"]["status"], "verified", report)
                reasons = report["events"]["appserver_event_proof"]
                self.assertTrue(reasons["failure_reasons"] or reasons["unknown_reasons"])
                self.assert_stopped(report)
                self.assertEqual(self.observed()["callback_results"], [])

    def test_raw_partial_result_negative_scope_survives_missing_handle_and_text(self):
        for mode in ("partial_foreign_scope", "early_partial_foreign"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], "failed", report)
                self.assertIn("appserver_observed_business_scope_mismatch",
                              report["events"]["appserver_event_proof"]["failure_reasons"])
                self.assertTrue(report["session"]["new_turn_verified"])
                self.assert_stopped(report)

    def test_stdout_eof_is_not_natural_exit_and_sleeping_descendant_is_not_clean_success(self):
        for mode in ("eof_no_exit", "sleepchild", "close_hang", "blocked"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                started = time.monotonic()
                process_started, process_stopped = [], []
                # Time construction through actual Job/I/O stop, not pre-launch
                # source checks or post-stop evidence finalization. The natural
                # parent-exit case must finish BEFORE its 15s fallback watchdog.
                seconds = 15 if mode == "sleepchild" else 5
                report = self.run_fake(mode, seconds=seconds,
                    mutate=lambda: process_started.append(time.monotonic()),
                    stopped=lambda: process_stopped.append(time.monotonic()))
                finished = time.monotonic()
                self.assert_stopped(report)
                self.assertTrue(report["shutdown"]["forced_termination"] or report["shutdown"]["io_failure"])
                observed = self.observed()
                self.assertTrue(pid_exited(observed["pid"]))
                self.assertTrue(all(pid_exited(pid) for pid in observed["child_pids"]))
                if mode in ("blocked", "close_hang"):
                    self.assertEqual(report["shutdown"]["io_failure"], "C1_PROCESS_DEADLINE")
                if mode == "eof_no_exit":
                    self.assertTrue(report["shutdown"]["stdout_eof"])
                    self.assertIsNone(report["shutdown"]["natural_exit_code"])
                if mode == "sleepchild":
                    self.assertEqual(report["shutdown"]["natural_exit_code"], 0)
                    self.assertTrue(observed["child_pids"])
                self.assertEqual(len(process_started), 1)
                self.assertEqual(len(process_stopped), 1)
                self.assertLess(process_stopped[0] - process_started[0], 9)
                self.assertLess(finished - started, 180)

    def test_actual_nonzero_exit_and_unconfirmed_cleanup_veto_complete_business(self):
        for mode, extra in (("nonzero", {}), ("success", {"cleanup_unknown": True})):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh("cleanup_" + mode)
                report = self.run_fake(mode, **extra)
                self.assertEqual(report["business"]["status"], "verified", report)
                self.assertNotEqual(report["status"], "verified")
                self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
                if mode == "nonzero":
                    self.assertEqual(report["shutdown"]["natural_exit_code"], 7)
                    self.assertEqual(report["status"], "failed")

    def test_ambiguous_actual_thread_write_preserves_pending_id_and_consumes_once(self):
        report = self.run_fake(send_failure="thread/start")
        self.assertIs(report["thread_start_sent"], None)
        self.assertIs(report["turn_start_sent"], False)
        self.assertIsNotNone(report["pending_client_id"])
        self.assertTrue(report["session"]["thread_start_intent"])
        self.assert_stopped(report)
        with self.assertRaises(ValueError):
            bundles.reserve_attempt(self.snapshot, "a", self.approval)

    def test_ambiguous_actual_turn_write_does_not_invent_unsent_or_new_turn(self):
        report = self.run_fake(send_failure="turn/start")
        self.assertIs(report["thread_start_sent"], True)
        self.assertIs(report["turn_start_sent"], None)
        self.assertIsNotNone(report["pending_client_id"])
        self.assertFalse(report["session"]["new_turn_verified"])
        self.assert_stopped(report)

    def test_launch_exception_is_ambiguous_without_fabricated_exit_or_reservation(self):
        def failed_factory(**kwargs):
            raise OSError("private-launch-secret")
        report = self.api.run_appserver_simulation(self.snapshot, "a", self.approval, self.receipt, self.contract,
                    process_factory=failed_factory, version_proof="codex-cli 0.163.0")
        self.assertEqual(report["status"], "unknown")
        self.assertIsNone(report["process_started"])
        self.assertIs(report["attempt_reserved"], False)
        self.assertIsNone(report["shutdown"]["natural_exit_code"])
        self.assertNotIn("private-launch-secret", json.dumps(report))

    def test_late_authority_revoked_inside_actual_finish_lock_cannot_unlock_b(self):
        original_lock = bundles._lock
        lock_count = 0
        @contextmanager
        def race(snapshot, **kwargs):
            nonlocal lock_count
            with original_lock(snapshot, **kwargs):
                lock_count += 1
                if lock_count == 2:  # reserve first; actual finish lock second.
                    (self.bundle / "approval.json").unlink()
                yield
        with patch.object(bundles, "_lock", side_effect=race), patch.object(
                self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake()
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["reason"], "C1_FINALIZATION_AUTHORITY_CHANGED")
        self.assertEqual(json.loads((self.bundle / "attempts/a-report.json").read_text())["state"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())

    def test_evidence_file_or_directory_conflict_still_attempts_finish_exactly_once(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                if self.launches:
                    self.fresh("evidence_directory")
                path = self.bundle / "attempts/a-appserver-evidence.json"
                if directory:
                    path.mkdir()
                else:
                    path.write_bytes(b"existing-owned-evidence")
                with patch.object(self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
                    report = self.run_fake()
                self.assertEqual(finish.call_count, 1)
                self.assertEqual(report["status"], "unknown")
                self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
                self.assertTrue(path.is_dir()) if directory else self.assertEqual(path.read_bytes(), b"existing-owned-evidence")

    def test_report_conflict_never_retries_finish_or_overwrites_old_evidence(self):
        # Collision is injected only after real process/SDK cleanup, not before reserve's safety checks.
        path = self.bundle / "attempts/a-report.json"
        def collide():
            path.write_bytes(b"old-finalization-evidence")
        with patch.object(self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake(stopped=collide)
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["finalization"], {"attempted": True, "confirmed": False})
        self.assertEqual(path.read_bytes(), b"old-finalization-evidence")
        with self.assertRaises(ValueError):
            bundles.reserve_attempt(self.snapshot, "b", self.approval)

    def test_missing_actual_audit_cannot_be_replaced_by_wire_success(self):
        def remove_one_actual_audit():
            next((self.bundle / "audit/a").glob("*.json")).unlink()
        report = self.run_fake(stopped=remove_one_actual_audit)
        self.assertNotEqual(report["status"], "verified", report)
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertEqual(len(list((self.bundle / "audit/a").glob("*.json"))), 9)

    def test_failed_a_blocks_b_without_launch_retry_or_refund(self):
        report = self.run_fake("wrong_policy")
        self.assert_stopped(report)
        before = (self.bundle / "attempts/a-reservation.json").read_bytes()
        with self.assertRaises(ValueError):
            self.run_fake(project="b")
        self.assertEqual(len(self.launches), 1)
        self.assertEqual((self.bundle / "attempts/a-reservation.json").read_bytes(), before)

    def test_rollback_a_and_b_preserves_exact_preview_bytes_and_failed_evidence(self):
        report = self.run_fake("wrong_policy")
        self.assert_stopped(report)
        before = {path.name: path.read_bytes() for path in (self.bundle / "attempts").glob("*.json")}
        for project in ("a", "b"):
            original = (self.bundle / (project + "-config.preview.toml")).read_bytes()
            result = bundles.rollback_config(self.snapshot, project, self.receipt)
            self.assertEqual((self.bundle / result["quarantine_path"]).read_bytes(), original)
            self.assertFalse((self.bundle / "projects" / project / ".codex/config.toml").exists())
        self.assertEqual({path.name: path.read_bytes() for path in (self.bundle / "attempts").glob("*.json")}, before)

    def test_reports_never_persist_model_text_handles_or_raw_errors(self):
        report = self.run_fake()
        self.assertEqual(report["status"], "verified", report)
        encoded = json.dumps(report) + "".join(path.read_text("utf-8") for path in (self.bundle / "attempts").glob("*.json"))
        for secret in ("private-model-secret", "private-delta-secret", "private-thread-secret", '"session_handle"',
                       "private-attestation-secret", "private-late-error-secret", '"prompt"', '"content"'):
            self.assertNotIn(secret, encoded)
        artifact = json.loads((self.bundle / "attempts/a-appserver-evidence.json").read_text("utf-8"))
        self.assertEqual(artifact["status"], "pending_finalization")
        self.assertEqual(artifact["candidate_status"], "verified")

    def test_preflight_watchdog_stops_same_actual_job_and_latches_timeout(self):
        deadline = time.monotonic() + 5
        process = OwnedProcess([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                               cwd=self.directory, env=dict(os.environ), deadline=deadline, stdin_mode="pipe")
        watchdog = self.runtime.PreflightWatchdog(process, deadline, timeout_seconds=0.2)
        try:
            with self.assertRaises((ValueError, OwnedProcessError)):
                with watchdog:
                    process.read_line()
            self.assertTrue(watchdog.expired)
            self.assertFalse(watchdog.alive)
        finally:
            cleanup = process.stop()
        self.assertTrue(cleanup["root_exited"] and cleanup["descendants_exited"])
        self.assertTrue(process.shutdown_state()["io_threads_stopped"])

    def test_cancelled_preflight_watchdog_is_joined_and_cannot_kill_next_phase(self):
        deadline = time.monotonic() + 5
        process = OwnedProcess([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                               cwd=self.directory, env=dict(os.environ), deadline=deadline, stdin_mode="pipe")
        watchdog = self.runtime.PreflightWatchdog(process, deadline, timeout_seconds=0.2)
        try:
            with watchdog:
                self.assertTrue(process.is_job_member())
            time.sleep(0.3)  # Beyond the cancelled phase timer, still the same live owned process.
            self.assertFalse(watchdog.expired)
            self.assertFalse(watchdog.alive)
            self.assertTrue(process.is_job_member())
            self.assertIsNone(process.exit_code)
        finally:
            cleanup = process.stop()
        self.assertTrue(cleanup["root_exited"] and cleanup["descendants_exited"])

    def test_reserve_raises_after_actual_persistence_still_finishes_once_without_thread_send(self):
        original = bundles._publish_ledger
        publications = 0
        def persisted_then_failed(snapshot, ledger):
            nonlocal publications
            original(snapshot, ledger)
            publications += 1
            if publications == 1:
                raise OSError("private-reservation-publish-secret")
        with patch.object(bundles, "_publish_ledger", side_effect=persisted_then_failed), patch.object(
                self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake()
        self.assertEqual(finish.call_count, 1)
        self.assertIs(report["attempt_reserved"], True)
        self.assertIs(report["thread_start_sent"], False)
        self.assertFalse(report["session"]["reservation_bound"])
        self.assertNotIn("thread/start", self.observed()["methods"])
        self.assertEqual(report["status"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertNotIn("private-reservation-publish-secret", json.dumps(report))

    def test_current_approval_mutation_after_sdk_uses_detached_expected_record_and_stops_b(self):
        def revoke_current():
            self.approval["user_confirmation_reference"] = "test-only:revoked-current-record"
        with patch.object(self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake(stopped=revoke_current)
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["reason"], "C1_FINALIZATION_AUTHORITY_CHANGED")
        self.assertEqual(finish.call_args.kwargs["expected_approval"]["user_confirmation_reference"], "test-only:synthetic-approval")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())

    def test_installed_config_drift_after_preflight_is_not_repaired_or_reserved(self):
        original = self.runtime.validate_preflight
        path = self.bundle / "projects/a/.codex/config.toml"
        changed = path.read_bytes() + b"\n# test-only changed installed bytes\n"
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_bytes(changed)
            return result
        with patch.object(self.runtime, "validate_preflight", side_effect=drift):
            report = self.run_fake()
        self.assertIs(report["attempt_reserved"], False)
        self.assertIs(report["thread_start_sent"], False)
        self.assertEqual(path.read_bytes(), changed)
        with self.assertRaises(ValueError):
            bundles.rollback_config(self.snapshot, "a", self.receipt)
        self.assertEqual(path.read_bytes(), changed)
        self.assert_stopped(report, reserved=False)

    def test_collecting_absence_is_not_success_and_premature_end_remains_terminal_unknown(self):
        report = self.run_fake("premature_end")
        self.assertEqual(report["status"], "unknown", report)
        self.assertEqual(report["session"]["state"], "protocol_complete")
        self.assertEqual(report["events"]["native"]["unique_calls"], 1)
        self.assertEqual(report["events"]["native"]["completed_calls"], 1)
        self.assertIn("appserver_incomplete_calls_at_turn_end", report["events"]["appserver_event_proof"]["unknown_reasons"])
        self.assertEqual((report["transport_count"], report["business_count"]), (1, 1))
        self.assert_stopped(report)

    def test_missing_finish_result_or_unknown_item_is_not_tolerated_as_collecting_absence(self):
        for mode, status in (("missing_finish", "failed"), ("missing_result", "unknown"), ("unknown_item", "failed")):
            with self.subTest(mode=mode):
                if self.launches:
                    self.fresh(mode)
                report = self.run_fake(mode)
                self.assertEqual(report["status"], status, report)
                self.assertIsNotNone(report["events"])
                self.assertFalse(report["events"]["appserver_event_proof"]["turn_completed"])
                if mode == "missing_result":
                    self.assertEqual(report["events"]["native"]["unique_calls"], 1)
                    self.assertTrue(set(report["events"]["appserver_event_proof"]["unknown_reasons"]) - {
                        "missing_appserver_turn_end", "missing_call_completion", "missing_turn_completion"})
                self.assert_stopped(report)

    def test_unreadable_persisted_reservation_is_unknown_without_guessed_finish_or_thread(self):
        original_read = bundles._read_json
        original_publish = bundles._publish_ledger
        reservation_path = self.bundle / "attempts/a-reservation.json"
        published = False
        preserved = None
        def unreadable(path, **kwargs):
            if path == reservation_path and published:
                raise PermissionError("private-reservation-filesystem-secret")
            return original_read(path, **kwargs)
        def publish_then_fail(snapshot, ledger):
            nonlocal published, preserved
            original_publish(snapshot, ledger)
            preserved = reservation_path.read_bytes()
            published = True
            raise OSError("private-ledger-filesystem-secret")
        with patch.object(bundles, "_read_json", side_effect=unreadable), patch.object(
                bundles, "_publish_ledger", side_effect=publish_then_fail), patch.object(
                self.api, "reserve_attempt", wraps=self.api.reserve_attempt) as reserve, patch.object(
                self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake()
        self.assertEqual(reserve.call_count, 1)
        self.assertEqual(finish.call_count, 0)
        self.assertIsNone(report["attempt_reserved"])
        self.assertIs(report["thread_start_sent"], False)
        self.assertIs(report["turn_start_sent"], False)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["reason"], "C1_APPSERVER_RESERVATION_UNCONFIRMED")
        self.assertEqual(report["finalization"], {"attempted": False, "confirmed": False})
        self.assertEqual(reservation_path.read_bytes(), preserved)
        self.assertNotIn("thread/start", self.observed()["methods"])
        self.assertTrue(report["shutdown"]["io_threads_stopped"])
        artifact = self.bundle / "attempts/a-appserver-finalization-unknown.json"
        self.assertTrue(artifact.is_file())
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
        with self.assertRaises(ValueError):
            bundles.reserve_attempt(self.snapshot, "b", self.approval)

    def test_candidate_watchdog_timeout_is_sticky_and_reports_actual_preflight_timeout(self):
        original = self.runtime.PreflightWatchdog
        def short_timer(process, deadline):
            return original(process, deadline, timeout_seconds=0.2)
        with patch.object(self.runtime, "PreflightWatchdog", side_effect=short_timer):
            report = self.run_fake("preflight_blocked", seconds=5)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["reason"], "C1_APPSERVER_PREFLIGHT_TIMEOUT")
        self.assertIs(report["process_started"], True)
        self.assertIs(report["attempt_reserved"], False)
        self.assertIs(report["thread_start_sent"], False)
        self.assertTrue(report["shutdown"]["forced_termination"])
        self.assertTrue(report["shutdown"]["cleanup"]["descendants_exited"])
        self.assertTrue(report["shutdown"]["io_threads_stopped"])
        self.assertEqual(report["finalization"], {"attempted": False, "confirmed": None})
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_one_original_absolute_deadline_reaches_actual_process_reserve_and_finish(self):
        self.assertIn("deadline", inspect.signature(bundles.reserve_attempt).parameters)
        with patch.object(self.api, "reserve_attempt", wraps=self.api.reserve_attempt) as reserve, patch.object(
                self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake()
        self.assertEqual(report["status"], "verified", report)
        self.assertEqual(reserve.call_count, 1)
        self.assertEqual(finish.call_count, 1)
        original_deadline = reserve.call_args.kwargs["deadline"]
        self.assertEqual(finish.call_args.kwargs["deadline"], original_deadline)
        self.assertLessEqual(self.processes[0].deadline, original_deadline)
        self.assertLessEqual(original_deadline - time.monotonic(), 180)

    def test_expired_finalization_still_invokes_actual_finish_once_and_retains_slot(self):
        self.assertIn("deadline", inspect.signature(self.runtime.finalize).parameters)
        reservation = bundles.reserve_attempt(self.snapshot, "a", self.approval)
        before = (self.bundle / "attempts/a-reservation.json").read_bytes()
        report = {"schema_version": 1, "project": "a", "status": "unknown",
                  "reason": "C1_APPSERVER_DEADLINE", "run_nonce": self.snapshot.run_nonce,
                  "manifest_sha256": self.snapshot.manifest_sha256,
                  "finalization": {"attempted": False, "confirmed": None}}
        deadline = time.monotonic() - 1
        with patch.object(self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            self.runtime.finalize(self.snapshot, "a", reservation, report, approval=self.approval,
                expected_approval=dict(self.approval), receipt=self.receipt, contract=self.contract, deadline=deadline)
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(finish.call_args.kwargs["deadline"], deadline)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["finalization"], {"attempted": True, "confirmed": False})
        self.assertEqual((self.bundle / "attempts/a-reservation.json").read_bytes(), before)
        self.assertFalse((self.bundle / "attempts/a-report.json").exists())
        self.assertTrue((self.bundle / "attempts/a-appserver-finalization-unknown.json").is_file())
        with self.assertRaises(ValueError):
            bundles.reserve_attempt(self.snapshot, "b", self.approval)

    def test_reparse_evidence_path_preserves_entry_and_still_finishes_once(self):
        import stat
        from types import SimpleNamespace
        path = self.bundle / "attempts/a-appserver-evidence.json"
        path.write_bytes(b"preserve-unsafe-evidence-entry")
        original_lstat = Path.lstat
        def reparse(candidate):
            if candidate == path:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
            return original_lstat(candidate)
        with patch.object(Path, "lstat", autospec=True, side_effect=reparse), patch.object(
                self.runtime, "finish_attempt", wraps=self.runtime.finish_attempt) as finish:
            report = self.run_fake()
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(path.read_bytes(), b"preserve-unsafe-evidence-entry")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())

    def test_old_exec_native_gate_remains_unconditional(self):
        self.assertFalse(old_exec.NATIVE_DISPATCH_SUPPORTED)
        native = dict(self.approval, scope="native-c1-wiring")
        with self.assertRaisesRegex(ValueError, "^C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED$"):
            old_exec.run_project(self.snapshot, "a", native, self.receipt, executable=Path(sys.executable))
        self.assertFalse(self.launches)


if __name__ == "__main__":
    unittest.main()
