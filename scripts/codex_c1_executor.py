"""C1 offline orchestration. Native exec is blocked by pinned approval semantics.

Factories/origin are trusted programmatic test seams, never CLI/bundle inputs.
This module neither makes human approvals nor infers them from a manifest flag.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import time

from scripts import codex_c1_bundle as bundle_api
from scripts.codex_c1_bundle import ReviewSnapshot, validate_approval, verify_snapshot, reserve_attempt, finish_attempt
from scripts.codex_c1_host import ReadOnlyProbe
from scripts.codex_c1_process import OwnedProcess, OwnedProcessError
from scripts.codex_c1_results import NativeRunState, decode_exec_line, validate_evidence

NATIVE_DISPATCH_SUPPORTED = False
NATIVE_UNSUPPORTED_REASON = "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED"
# rust-v0.163.0 exec/src/lib.rs (local exec/source.rs):586-631 forces
# harness AskForApproval::Never. core/config/mod.rs:3854 gives this precedence
# over -c approval_policy. The AutoReview exception is NOT an allowed workaround.


def _executable(path):
    if not isinstance(path, Path) or not path.is_absolute() or path.suffix.lower() != ".exe":
        raise ValueError("C1_EXPLICIT_EXECUTABLE_REQUIRED")
    bundle_api._assert_safe_existing(path)
    return path


def _mode(approval, execution_origin, *, process_factory=None, probe_factory=ReadOnlyProbe):
    if execution_origin == "native_codex":
        if approval["scope"] != "native-c1-wiring" or probe_factory is not ReadOnlyProbe:
            raise ValueError("C1_NATIVE_SCOPE_REQUIRED")
        if process_factory is not None and process_factory is not OwnedProcess:
            raise ValueError("C1_NATIVE_FACTORY_FORBIDDEN")
    elif execution_origin == "offline_simulation":
        if (approval["scope"] != "offline-simulation" or probe_factory is ReadOnlyProbe
                or process_factory is OwnedProcess):
            raise ValueError("C1_SIMULATION_REQUIRES_TRUSTED_FACTORIES")
    else:
        raise ValueError("C1_EXECUTION_ORIGIN_INVALID")


def inspect_project(snapshot: ReviewSnapshot, project: str, approval: dict, receipt: dict, *,
                    executable: Path, probe_factory=ReadOnlyProbe,
                    execution_origin: str = "native_codex") -> dict:
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    validate_approval(snapshot, approval, "inspect", project)
    saved = bundle_api._read_json(bundle_api._safe_child(snapshot.bundle, "approval.json"))
    if saved != approval:
        raise ValueError("C1_APPROVAL_CHANGED")
    _mode(approval, execution_origin, probe_factory=probe_factory)
    executable = _executable(executable)
    probe = probe_factory([str(executable)], list(snapshot.manifest["invocation_only_overrides"]))
    try:
        preflight = probe.inspect(snapshot, project)
    finally:
        probe.close()
    if type(preflight) is not dict or preflight.get("state") != "preflight_ready":
        raise ValueError("C1_PREFLIGHT_BLOCKED")
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    validate_approval(snapshot, approval, "inspect", project)
    if bundle_api._read_json(bundle_api._safe_child(snapshot.bundle, "approval.json")) != approval:
        raise ValueError("C1_APPROVAL_CHANGED")
    return preflight


def _exec_argv(snapshot, project, executable):
    """Pinned parser's *requested* configuration, used only by offline simulation.

    This is not a native-ready command: headless exec overrides the requested
    policy. No unsupported exec --ask-for-approval flag or trust bypass is used.
    """
    item = snapshot.manifest["projects"][project]
    argv = [str(executable), "--no-daemon", "exec", "--ephemeral", "--json",
            "--sandbox", "read-only", "-C", item["root"]]
    for override in snapshot.manifest["invocation_only_overrides"]:
        argv.extend(("-c", override))
    argv.extend(("-c", 'approval_policy="on-request"', item["prompt"]))
    return argv


def _child_environment():
    # No credential values or complete environment are emitted/persisted.
    names = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
             "HOMEPATH", "LOCALAPPDATA", "APPDATA", "CODEX_HOME", "HOME", "TMPDIR"}
    return {name: value for name, value in os.environ.items() if name.upper() in names}


def _audit_rows(snapshot, project):
    directory = bundle_api._safe_child(snapshot.bundle, "audit/" + project, expect="directory")
    if not directory.exists():
        return [], []
    bundle_api._assert_safe_directory(directory)
    transport, business, received = [], [], 0
    # Bounded on disk as well as in memory; arbitrary names/bodies are not copied.
    paths = []
    for path in directory.iterdir():
        if len(paths) >= 16 or not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            raise ValueError("C1_AUDIT_INVALID")
        paths.append(path)
    for path in paths:
        bundle_api._assert_safe_existing(path)
        received += path.stat().st_size
        if received > 2_097_152:
            raise ValueError("C1_AUDIT_INVALID")
        row = bundle_api._read_json(path, max_bytes=262_144)
        if type(row) is not dict:
            raise ValueError("C1_AUDIT_INVALID")
        if row.get("event") == "request_transport":
            transport.append(row)
        elif row.get("event") in ("request_business", "request_business_rejected"):
            business.append(row)
        else:
            raise ValueError("C1_AUDIT_INVALID")
    return transport, business


def _confirmed(cleanup):
    return (type(cleanup) is dict and cleanup.get("root_exited") is True
            and cleanup.get("descendants_exited") is True and cleanup.get("reason") == "job_empty")


def _finalization_authority(snapshot, project, approval, expected_approval, receipt):
    """Recheck original authority and installed bytes without repairing drift."""
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    if type(approval) is not dict or approval != expected_approval:
        raise ValueError("C1_FINALIZATION_AUTHORITY_CHANGED")
    for action in ("run_project", "inspect"):
        validate_approval(snapshot, expected_approval, action, project)
    saved = bundle_api._read_json(bundle_api._safe_child(snapshot.bundle, "approval.json"))
    if saved != expected_approval:
        raise ValueError("C1_FINALIZATION_AUTHORITY_CHANGED")


def run_project(snapshot: ReviewSnapshot, project: str, approval: dict, receipt: dict, *,
                executable: Path, process_factory=OwnedProcess,
                probe_factory=ReadOnlyProbe, execution_origin: str = "native_codex") -> dict:
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    validate_approval(snapshot, approval, "run_project", project)
    expected_approval = copy.deepcopy(approval)
    if execution_origin == "native_codex":
        # Unconditional gate: even a native-scoped human record cannot fix this
        # pinned transport. In particular simulation records never authorize it.
        raise ValueError(NATIVE_UNSUPPORTED_REASON)
    _mode(approval, execution_origin, process_factory=process_factory, probe_factory=probe_factory)
    executable = _executable(executable)
    preflight = inspect_project(snapshot, project, approval, receipt, executable=executable,
                                probe_factory=probe_factory, execution_origin=execution_origin)
    verify_snapshot(snapshot, phase="installed", receipt=receipt)
    validate_approval(snapshot, approval, "run_project", project)
    argv = _exec_argv(snapshot, project, executable)
    reservation = reserve_attempt(snapshot, project, approval)  # Persist before ANY process creation.
    process = None
    state = NativeRunState(snapshot.run_nonce, project)
    reason = None
    cleanup = {"root_exited": True, "descendants_exited": True, "reason": "not_started"}
    streams, observed_exit = {}, None
    try:
        process = process_factory(argv, cwd=Path(snapshot.manifest["projects"][project]["root"]),
                                  env=_child_environment(), deadline=time.monotonic() + 180)
        for raw in process.events():
            try:
                event = decode_exec_line(raw)
            except ValueError:
                reason = "C1_EXEC_EVENT_INVALID"
                break
            try:
                state.consume(event)
            except ValueError:
                reason = "C1_EXEC_SEQUENCE_REJECTED"
                break
            summary = state.summary()
            if summary["failure_reasons"] or event.outcome_state in ("failed", "unknown"):
                reason = "C1_EXEC_OUTCOME_REJECTED"
                break
        # Capture before stop: the supervisor's termination code is NOT a
        # naturally observed exit, and must not manufacture success/completion.
        observed_exit = process.exit_code
    except OwnedProcessError as error:
        reason = error.code if error.code in {"C1_PROCESS_DEADLINE", "C1_PROCESS_LINE_LIMIT",
                    "C1_PROCESS_TOTAL_LIMIT", "C1_PROCESS_PARTIAL_LINE", "C1_PROCESS_STREAM_FAILED",
                    "C1_PROCESS_START_FAILED", "C1_PROCESS_UNSUPPORTED"} else "C1_PROCESS_FAILED"
        if type(error.cleanup) is dict:
            cleanup = error.cleanup
    except BaseException:
        # Attempt is consumed even if the OS launch or test factory throws.
        reason = "C1_PROCESS_FAILED"
    finally:
        if process is not None:
            try:
                cleanup = process.stop()
                streams = process.stream_summary()
            except BaseException:
                cleanup = {"root_exited": False, "descendants_exited": False, "reason": "cleanup_unconfirmed"}
    authority_current = True
    try:
        _finalization_authority(snapshot, project, approval, expected_approval, receipt)
    except (OSError, ValueError, TypeError):
        authority_current = False
    native = state.summary()
    native["execution_origin"] = execution_origin
    if type(observed_exit) is int:
        native["process_exit"] = observed_exit
    try:
        transport, business = _audit_rows(snapshot, project)
        evidence = validate_evidence(snapshot, project, reservation, native, transport, business)
    except (OSError, ValueError, TypeError):
        evidence = {"status": "unknown", "checks": {}, "reasons": ["C1_AUDIT_INVALID"],
                    "execution_origin": execution_origin, "transport_count": None, "business_count": None,
                    "call_mapping": []}
        reason = reason or "C1_AUDIT_INVALID"
    status = evidence["status"]
    if reason is not None and status == "verified":
        status = "unknown"
    if not _confirmed(cleanup):
        # No process was created on a plain Popen error; no job can be confirmed.
        # Conservative unknown still consumes the attempt and stops the round.
        status = "unknown"
        reason = "C1_PROCESS_CLEANUP_UNCONFIRMED" if cleanup["reason"] != "not_started" else (reason or "C1_PROCESS_FAILED")
    if not authority_current:
        status, reason = "unknown", "C1_FINALIZATION_AUTHORITY_CHANGED"
    report = {"schema_version": 1, "run_nonce": snapshot.run_nonce, "manifest_sha256": snapshot.manifest_sha256,
              "project": project, "status": status, "execution_origin": execution_origin,
              "native_evidence_claimed": False, "reason": reason, "preflight": preflight,
              "cleanup": cleanup, "streams": streams, "evidence": evidence,
              "native_counts": {key: native[key] for key in ("native_thread_id", "unique_calls", "completed_calls", "turn_completed")}}
    if type(observed_exit) is int:
        report["native_counts"]["process_exit"] = observed_exit
    # One exclusively created sanitized evidence artifact; never replace a failed
    # or unknown attempt. finish_attempt owns its independent ledger/report files.
    needs_unknown_artifact = False
    try:
        # Path validation is publication too: directories/reparse entries and
        # inaccessible ancestors must not escape the one finalization attempt.
        path = bundle_api._safe_child(snapshot.bundle, "attempts/" + project + "-execution.json")
        bundle_api._write_exclusive(path, report)
    except (OSError, ValueError, TypeError):
        report["status"], report["reason"] = "unknown", "C1_FINALIZATION_UNCONFIRMED"
        needs_unknown_artifact = True
    try:
        _finalization_authority(snapshot, project, approval, expected_approval, receipt)
    except (OSError, ValueError, TypeError):
        report["status"], report["reason"] = "unknown", "C1_FINALIZATION_AUTHORITY_CHANGED"
    try:
        # Exactly one finalization attempt, even if evidence publication failed.
        finish_attempt(snapshot, reservation, {"state": report["status"],
                       "detail": report["reason"] or "C1_EVIDENCE_VALIDATED"},
                       expected_approval=expected_approval)
    except (OSError, ValueError, TypeError) as error:
        # Receipt/source drift must not be ignored to obtain a final ledger row.
        # A locked authority downgrade is already committed as unknown/stopped;
        # any other failure still leaves the reservation consumed and B blocked.
        report["status"] = "unknown"
        report["reason"] = ("C1_FINALIZATION_AUTHORITY_CHANGED"
                            if isinstance(error, ValueError) and str(error) == "C1_FINALIZATION_AUTHORITY_CHANGED"
                            else "C1_FINALIZATION_UNCONFIRMED")
        needs_unknown_artifact = True
    if needs_unknown_artifact:
        try:
            emergency = bundle_api._safe_child(snapshot.bundle, "attempts/" + project + "-finalization-unknown.json")
            bundle_api._write_exclusive(emergency, {"schema_version": 1, "run_nonce": snapshot.run_nonce,
                                        "manifest_sha256": snapshot.manifest_sha256, "project": project,
                                        "status": "unknown", "reason": report["reason"]})
        except (OSError, ValueError, TypeError):
            # Preserve an unsafe/existing path. No successful artifact write is
            # claimed, and never retry finish_attempt to make disk look complete.
            pass
    return report
