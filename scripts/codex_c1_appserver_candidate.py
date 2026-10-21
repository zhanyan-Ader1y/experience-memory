"""Offline-only C1-T composition; native acceptance remains independently pending.

The required factory is a trusted PROGRAMMATIC local-test seam, not a CLI,
bundle import, native launcher or authorization mechanism. No default factory,
Codex/version invocation, retry, reconnect, resume, automatic B or approval exists.
"""
from __future__ import annotations

import copy
from pathlib import Path
import time

from scripts import codex_c1_bundle as bundles
from scripts import codex_c1_appserver_runtime as runtime
from scripts.codex_c1_bundle import ReviewSnapshot, reserve_attempt, validate_approval, verify_snapshot
from scripts.codex_c1_process import OwnedProcess, OwnedProcessError
from scripts.codex_c1_appserver_rpc import AppServerPeer, RpcProtocolError
from scripts.codex_c1_appserver_session import AppServerSession
from scripts.codex_c1_appserver_events import (
    AppServerEvidenceError, AppServerRunState, validate_appserver_business_evidence, validate_appserver_evidence,
)
from scripts.prepare_codex_c1_appserver_review import NATIVE_ENTRY_STATUS, TRANSPORT_KIND, transport_contract

NATIVE_DISPATCH_SUPPORTED = False
_BUSINESS_NOTIFICATIONS = frozenset({"thread/started", "turn/started", "turn/completed", "item/started", "item/completed"})
# Public summaries include these completeness absences on every live partial run.
# Coordinator-approved ONLY while genuinely collecting; never edit proof/reasons.
_COLLECTING_ABSENCES = frozenset({"missing_appserver_turn_end", "missing_call_completion", "missing_turn_completion"})


class _Stop(Exception):
    def __init__(self, code):
        self.code = code


class _ObservedWire:
    """Bind only actual validated Session IDs; bounded original-message replay.

    Session and event owner independently see the SAME original RpcMessage.
    In particular no predecode/discard of partial result channels can erase an
    independently present foreign-owner fact from EventState.observe.
    """
    def __init__(self, snapshot, project, session, process):
        self.snapshot, self.project, self.session, self.process = snapshot, project, session, process
        self.events = None
        self._buffer = []
        self._buffer_bytes = 0

    def observe(self, message, frame_bytes):
        session_error = False
        try:
            self.session.observe(message)
        except ValueError:
            session_error = True  # Still feed actual raw wire to an already-bound independent event owner.
        business_message = message.kind == "notification" and message.method in _BUSINESS_NOTIFICATIONS
        if self.events is not None:
            if business_message or message.kind == "error":
                try:
                    self.events.observe(message)
                except AppServerEvidenceError:
                    pass  # Its sticky public proof carries authoritative failed/unknown facts.
        elif business_message:
            if len(self._buffer) >= 16 or self._buffer_bytes + frame_bytes > 2097152:
                raise _Stop("C1_APPSERVER_EARLY_BUFFER_LIMIT")
            self._buffer.append(message)
            self._buffer_bytes += frame_bytes
        summary = self.session.summary()
        if (self.events is None and summary["new_turn_verified"]
                and summary["state"] in ("collecting", "protocol_complete")):
            self.events = AppServerRunState(self.snapshot, self.project, session=summary)
            for original in self._buffer:
                try:
                    self.events.observe(original)
                except AppServerEvidenceError:
                    pass  # Replay ALL actual buffered negative facts in actual wire order.
            self._buffer.clear()
            self._buffer_bytes = 0
        if session_error or self.severity() is not None:
            raise _Stop("C1_APPSERVER_OBSERVED_EVIDENCE_STOPPED")

    def severity(self):
        session = self.session.summary()
        state = session["state"]
        proof = self.events.summary()["appserver_event_proof"] if self.events is not None else {}
        if state == "failed" or proof.get("failure_reasons"):
            return "failed"
        if state == "unknown":
            return "unknown"
        unknowns = set(proof.get("unknown_reasons", ()))
        if unknowns:
            collecting = (state == "collecting" and not session["turn_completed_observed"]
                          and not session["approval_observed"] and not session["failure_codes"]
                          and self.process.shutdown_state()["io_failure"] is None)
            if not collecting or unknowns - _COLLECTING_ABSENCES:
                return "unknown"
        return None

    def complete(self):
        return (self.events is not None and self.session.summary()["state"] == "protocol_complete"
                and self.events.summary()["appserver_event_proof"]["turn_completed"] and self.severity() is None)

    def rpc_failure(self, peer):
        if self.events is not None and peer.failure_code is not None:
            try:
                self.events.record_rpc_failure(peer.failure_code)
            except AppServerEvidenceError:
                pass  # Never manufacture a wire error or duplicate the producer's static classification map.


def _empty_shutdown():
    return {"input_eof_sent": False, "stdin_eof": None, "stdout_eof": None, "stderr_eof": None,
            "natural_exit_code": None, "forced_termination": None, "io_failure": None, "io_threads_stopped": None,
            "cleanup": {"root_exited": False, "descendants_exited": False, "reason": "cleanup_unconfirmed"}}


def run_appserver_simulation(snapshot: ReviewSnapshot, project: str, approval: dict, receipt: dict, contract: dict,
                             *, process_factory, version_proof: str,
                             execution_origin="offline_simulation") -> dict:
    """Run one explicit local fake process, sharing one <=180s absolute deadline.

    Factory signature: factory(*, cwd: Path, env: dict, deadline: float,
    stdin_mode="pipe") -> actual OwnedProcess. It supplies its OWN local Python
    fixture command; this module supplies no executable/native argv at all.
    Sent flags are False before intent/write, True only after actual write ACK,
    None after an attempted ambiguous write. Pending ID is the actual Peer value.
    A reserved failure finalizes exactly once; nothing schedules/retries B.
    """
    deadline = time.monotonic() + 180
    if execution_origin != "offline_simulation":
        raise ValueError(NATIVE_ENTRY_STATUS)
    if (not callable(process_factory) or process_factory is OwnedProcess
            or type(approval) is not dict or approval.get("scope") != "offline-simulation"):
        raise ValueError("C1_APPSERVER_TRUSTED_OFFLINE_FACTORY_REQUIRED")
    if type(version_proof) is not str or version_proof != "codex-cli 0.163.0":
        raise ValueError("C1_APPSERVER_SYNTHETIC_VERSION_MISMATCH")
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    for action in ("run_project", "inspect"):
        validate_approval(snapshot, approval, action, project)
    expected_approval = copy.deepcopy(approval)
    runtime.authority(snapshot, project, approval, expected_approval, receipt, contract)
    runtime.check_deadline(deadline)
    # A prior consumed/stopped round cannot even spawn another preflight process.
    # Actual admission remains reserve_attempt's independent in-lock decision below.
    bundles._check_manifest_approval_and_run_state(snapshot, project, expected_approval)
    report = {"schema_version": 1, "run_nonce": snapshot.run_nonce, "manifest_sha256": snapshot.manifest_sha256,
        "project": project, "status": "unknown", "reason": None, "execution_origin": "offline_simulation",
        "transport_kind": TRANSPORT_KIND, "native_evidence_claimed": False, "native_entry_status": NATIVE_ENTRY_STATUS,
        "process_factory_invoked": False, "process_started": False, "attempt_reserved": False,
        "thread_start_sent": False, "turn_start_sent": False, "approval_cancel_sent": False,
        "interrupt_sent": False, "input_eof_sent": False, "pending_client_id": None,
        "preflight": None, "session": None, "events": None, "business": None, "evidence": None,
        "transport_count": None, "business_count": None, "streams": {}, "shutdown": _empty_shutdown(),
        "finalization": {"attempted": False, "confirmed": None}}
    process = peer = pipe = observed = session = reservation = None
    reason = None

    def route(message):
        if message.kind != "server_request":
            observed.observe(message, pipe.last_frame_bytes)
            return
        action = session.on_server_request(message)
        if action in ("cancel", "deny_permissions"):
            report["approval_cancel_sent"] = None
            peer.reply_cancel(message)
            report["approval_cancel_sent"] = True
            # Never even consume interrupt intent with a pending client request.
            if peer.pending_client_id is None:
                params = session.interrupt_params()
                if params is not None:
                    report["interrupt_sent"] = None
                    peer.send_request("turn/interrupt", params)
                    report["interrupt_sent"] = True
                    while peer.pending_client_id is not None:
                        followup = peer.next_message()
                        if followup.kind == "response":
                            break  # Correlated interrupt response, NOT a turn/start response.
                        try:
                            if followup.kind == "server_request":
                                session.on_server_request(followup)  # Never answer a second interaction.
                                break
                            observed.observe(followup, pipe.last_frame_bytes)
                        except (ValueError, _Stop):
                            pass  # Only shutdown observation, never normal collection/admission.
        raise _Stop("C1_APPSERVER_INTERACTION_STOPPED")

    try:
        report["process_factory_invoked"], report["process_started"] = True, None
        created = process_factory(cwd=Path(snapshot.manifest["projects"][project]["root"]),
                                  env=runtime.child_environment(), deadline=deadline, stdin_mode="pipe")
        if not isinstance(created, OwnedProcess):
            raise _Stop("C1_APPSERVER_ACTUAL_OWNED_PROCESS_REQUIRED")
        process = created
        report["process_started"] = True
        pipe = runtime.LookaheadPipe(process)
        peer = AppServerPeer(pipe)
        report["preflight"], model, provider = runtime.preflight(
            peer, process, snapshot, project, version_proof=version_proof, deadline=deadline)
        runtime.check_deadline(deadline)
        runtime.authority(snapshot, project, approval, expected_approval, receipt, contract)
        runtime.check_deadline(deadline)
        session = AppServerSession(snapshot, project, model=model, provider=provider)
        observed = _ObservedWire(snapshot, project, session, process)
        report["attempt_reserved"] = None  # Reserve may persist and then encounter an I/O/ledger failure.
        reservation = reserve_attempt(snapshot, project, expected_approval, deadline=deadline)
        report["attempt_reserved"] = True
        session.bind_reservation(reservation)
        # ALL thread/start sends are concentrated here, AFTER actual persisted reservation.
        params = session.thread_start_params()
        report["thread_start_sent"] = None
        peer.send_request("thread/start", params)
        report["thread_start_sent"] = True
        while not session.summary()["thread_response_verified"]:
            message = peer.next_message()
            if message.kind == "response":
                session.accept_thread_response(message.payload["result"])
            else:
                route(message)
        params = session.turn_start_params()
        report["turn_start_sent"] = None
        peer.send_request("turn/start", params)
        report["turn_start_sent"] = True
        while not observed.complete():
            route(peer.next_message())
        process.close_input()
        report["input_eof_sent"] = True  # Actual successful close ACK, never stop's forced closure.
        # Post-completion frames STILL go through Peer and both actual observers.
        # Only true expected EOF avoids the unexpected-EOF failure path.
        while True:
            expected_eof = (report["input_eof_sent"] is True and peer.pending_client_id is None and observed.complete())
            if expected_eof and pipe.peek_eof(expected=True):
                break
            route(peer.next_message())
        if process.wait_for_exit() is None:
            reason = "C1_APPSERVER_NATURAL_EXIT_UNOBSERVED"
    except runtime.PreflightTimeout:
        reason = "C1_APPSERVER_PREFLIGHT_TIMEOUT"
    except runtime.DeadlineExpired:
        reason = "C1_APPSERVER_DEADLINE"
    except RpcProtocolError:
        reason = "C1_APPSERVER_RPC_FAILED"
        if observed is not None:
            observed.rpc_failure(peer)
    except OwnedProcessError:
        reason = "C1_APPSERVER_PROCESS_FAILED"
    except _Stop as error:
        reason = error.code
    except BaseException:
        # No raw exceptions/DTOs are recorded; known producer states remain authoritative.
        reason = "C1_APPSERVER_DRIVER_FAILED"
    finally:
        if report["attempt_reserved"] is None:
            try:
                reservation = runtime.recover_reservation(snapshot, project)
                report["attempt_reserved"] = reservation is not None
            except (OSError, ValueError, TypeError):
                reason = "C1_APPSERVER_RESERVATION_UNCONFIRMED"
        if process is not None:
            if report["input_eof_sent"] is not True:
                try:
                    process.close_input()
                    report["input_eof_sent"] = True
                except BaseException:
                    reason = reason or "C1_APPSERVER_INPUT_CLOSE_UNCONFIRMED"
            try:
                if process.wait_for_exit() is None:
                    reason = reason or "C1_APPSERVER_NATURAL_EXIT_UNOBSERVED"
            except BaseException:
                reason = reason or "C1_APPSERVER_NATURAL_EXIT_UNOBSERVED"
            cleanup = report["shutdown"]["cleanup"]
            try:
                cleanup = process.stop()  # Actual whole-Job and all owned I/O shutdown.
            except BaseException:
                reason = reason or "C1_APPSERVER_CLEANUP_UNCONFIRMED"
            try:
                state = process.shutdown_state()
                report["shutdown"] = {key: state[key] for key in (
                    "stdin_eof", "stdout_eof", "stderr_eof", "natural_exit_code", "forced_termination",
                    "io_failure", "io_threads_stopped")}
                report["shutdown"].update(input_eof_sent=report["input_eof_sent"], cleanup=cleanup)
                report["streams"] = process.stream_summary()
            except BaseException:
                reason = reason or "C1_APPSERVER_SHUTDOWN_UNCONFIRMED"
        if peer is not None:
            report["pending_client_id"] = peer.pending_client_id
        if session is not None:
            report["session"] = session.summary()
        if observed is not None and observed.events is not None:
            report["events"] = observed.events.summary()

    report["reason"] = reason
    if reservation is not None:
        try:
            runtime.check_deadline(deadline)
            transport, outcomes = runtime.read_audits(snapshot, project)
            runtime.check_deadline(deadline)
            report["transport_count"], report["business_count"] = len(transport), len(outcomes)
            if observed is not None and observed.events is not None:
                report["business"] = validate_appserver_business_evidence(
                    snapshot, project, reservation, observed.events, transport, outcomes)
        except runtime.DeadlineExpired:
            report["reason"] = "C1_APPSERVER_DEADLINE"
        except (OSError, ValueError, TypeError):
            report["reason"] = report["reason"] or "C1_APPSERVER_AUDIT_INVALID"
        try:
            runtime.check_deadline(deadline)
            report["evidence"] = validate_appserver_evidence(snapshot, project, reservation,
                report["business"] or {}, report["session"] or {}, report["shutdown"], execution_origin="offline_simulation")
            runtime.check_deadline(deadline)
            report["status"] = report["evidence"]["status"]
        except runtime.DeadlineExpired:
            report["status"], report["reason"] = "unknown", "C1_APPSERVER_DEADLINE"
        except (OSError, ValueError, TypeError):
            report["status"], report["reason"] = "unknown", "C1_APPSERVER_EVIDENCE_UNCONFIRMED"
        if observed is not None and observed.severity() == "failed":
            report["status"] = "failed"
        if report["reason"] is not None and report["status"] == "verified":
            report["status"] = "unknown"
        runtime.finalize(snapshot, project, reservation, report, approval=approval,
                         expected_approval=expected_approval, receipt=receipt, contract=contract, deadline=deadline)
    elif report["attempt_reserved"] is None:
        report["status"] = "unknown"
        report["finalization"] = {"attempted": False, "confirmed": False}
        runtime.unknown_artifact(snapshot, project, report)
    return report
