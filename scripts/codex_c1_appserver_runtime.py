"""Candidate-local C1-T pipe/phase deadline and exactly-once publication helpers.

Uses only the approved process/RPC/bundle APIs (no private transport/state edits).
This module owns no thread/turn inference, native command or success audit data.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import threading
import time

from scripts import codex_c1_bundle as bundles
from experience_memory.domain.errors import MemoryError as ContractError
from scripts.codex_c1_bundle import finish_attempt
from scripts.codex_c1_executor import _audit_rows as read_audits, _child_environment as child_environment
from scripts.codex_c1_executor import _finalization_authority
from scripts.codex_c1_host import validate_preflight
from scripts.prepare_codex_c1_appserver_review import checked_contract


_EMPTY = object()


class PreflightTimeout(ValueError):
    """Actual watchdog expiry, never guessed from arbitrary exception text."""
    def __init__(self):
        super().__init__("C1_APPSERVER_PREFLIGHT_TIMEOUT")


class DeadlineExpired(ValueError):
    def __init__(self):
        super().__init__("C1_APPSERVER_DEADLINE")


def check_deadline(deadline):
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or time.monotonic() >= deadline):
        raise DeadlineExpired()


class LookaheadPipe:
    """Single-reader, at most ONE actual frame, always consumed through Peer.

    Only expected shutdown probes EOF. Every nonempty frame is handed unchanged
    to AppServerPeer.next_message via read_line, never decoded/accepted here.
    Unexpected EOF uses the normal Peer path and its sticky failure observation.
    """
    def __init__(self, process):
        self.process = process
        self._frame = _EMPTY
        self.last_frame_bytes = 0

    def send_line(self, raw):
        return self.process.send_line(raw)

    def read_line(self):
        if self._frame is _EMPTY:
            raw = self.process.read_line()
        else:
            raw, self._frame = self._frame, _EMPTY
        self.last_frame_bytes = len(raw) if raw is not None else 0
        return raw

    def peek_eof(self, *, expected: bool):
        if expected is not True:
            raise ValueError("C1_APPSERVER_EOF_NOT_EXPECTED")
        if self._frame is _EMPTY:
            self._frame = self.process.read_line()
        return self._frame is None


class PreflightWatchdog:
    """One cancellable phase timer, <=30s inside the ORIGINAL overall deadline.

    Expiry stops the same owned Job only. Exit cancels AND joins the timer before
    a reservation/next phase can begin; a raced timeout is sticky, never success.
    The smaller timeout is solely a trusted programmatic local-test seam.
    """
    def __init__(self, process, deadline: float, *, timeout_seconds: float = 30):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
            raise ValueError("C1_APPSERVER_PREFLIGHT_TIMEOUT_INVALID")
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            raise ValueError("C1_APPSERVER_DEADLINE")
        self.process, self.deadline = process, deadline
        self.until = min(now + timeout_seconds, deadline - min(1.0, remaining * 0.25))
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._done = False
        self.expired = False
        self._thread = threading.Thread(target=self._wait, name="c1-appserver-preflight", daemon=True)

    @property
    def alive(self):
        return self._thread.is_alive()

    def _wait(self):
        if self._cancel.wait(max(0, self.until - time.monotonic())):
            return
        with self._lock:
            if self._done:
                return
            self.expired = True
        try:
            self.process.stop()
        except Exception:
            # Candidate's independent final stop/observations remain authoritative.
            pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        with self._lock:
            self._done = True
            self._cancel.set()
        self._thread.join(max(0, self.deadline - time.monotonic()))
        if self.alive:
            raise ValueError("C1_APPSERVER_PREFLIGHT_CLEANUP_UNCONFIRMED") from None
        if self.expired:
            raise PreflightTimeout() from None
        return False


def authority(snapshot, project, approval, expected_approval, receipt, contract):
    """Existing installed/source/approval checks plus the complete passive profile."""
    _finalization_authority(snapshot, project, approval, expected_approval, receipt)
    checked_contract(snapshot, contract)


def preflight(peer, process, snapshot, project, *, version_proof, deadline):
    """Four one-shot RPCs on the SAME connection later used for the reserved turn."""
    root = snapshot.manifest["projects"][project]["root"]

    def request(method, params):
        peer.send_request(method, params)
        message = peer.next_message()
        if message.kind == "error":
            raise ValueError("C1_APPSERVER_PREFLIGHT_REMOTE_ERROR")
        if message.kind != "response" or type(message.payload.get("result")) is not dict:
            raise ValueError("C1_APPSERVER_PREFLIGHT_RESPONSE_INVALID")
        return message.payload["result"]

    with PreflightWatchdog(process, deadline):
        initialized = request("initialize", {"clientInfo": {"name": "experience-memory-offline-c1t", "version": "0.1.0"},
                                             "capabilities": {"experimentalApi": True}})
        if (set(initialized) != {"codexHome", "platformFamily", "platformOs", "userAgent"}
                or any(type(value) is not str or not value for value in initialized.values())
                or not Path(initialized["codexHome"]).is_absolute()):
            raise ValueError("C1_APPSERVER_INITIALIZE_INVALID")
        peer.send_initialized()
        config = request("config/read", {"cwd": root, "includeLayers": True})
        if (not {"config", "origins", "layers"}.issubset(config) or set(config) - {"config", "origins", "layers"}
                or type(config["origins"]) is not dict or type(config["config"]) is not dict
                or type(config["layers"]) is not list):
            raise ValueError("C1_APPSERVER_PREFLIGHT_RESPONSE_INVALID")
        hooks = request("hooks/list", {"cwds": [root]})
        usage = request("account/rateLimits/read", {"excludeResetCreditDetails": True, "supportsLunaReserve": False})
        result = validate_preflight(snapshot, project, version=version_proof, effective_config=config["config"],
                                    layers=config["layers"], hooks=hooks, usage=usage)
        # No guessed model/provider/default: both must be actual same-connection values.
        model, provider = config["config"].get("model"), config["config"].get("model_provider")
        if any(type(value) is not str or not value or len(value.encode("utf-8")) > 512 for value in (model, provider)):
            raise ValueError("C1_APPSERVER_MODEL_SELECTION_MISSING")
    result["version_evidence_origin"] = "local_fake_process_only_not_native_version_evidence"
    return result, model, provider


def recover_reservation(snapshot, project):
    """Observe a possibly persisted reservation after reserve raised; never invent it."""
    path = bundles._safe_child(snapshot.bundle, "attempts/" + project + "-reservation.json")
    if not bundles._path_entry_exists(path):
        return None
    actual = bundles._read_json(path)
    expected = {"schema_version": 1, "project": project, "attempt": 1, "state": "reserved",
                "run_nonce": snapshot.run_nonce, "manifest_sha256": snapshot.manifest_sha256}
    if type(actual) is not dict or bundles._json_bytes(actual) != bundles._json_bytes(expected):
        raise ValueError("C1_APPSERVER_RESERVATION_UNCONFIRMED")
    return actual


def unknown_artifact(snapshot, project, report):
    """Exclusive best-effort UNKNOWN evidence, even after an expired budget.

    No success/write acknowledgement is claimed unless a caller observes a file;
    this never repairs an unsafe path, calls finish, waits on a lock or retries.
    """
    try:
        path = bundles._safe_child(snapshot.bundle, "attempts/" + project + "-appserver-finalization-unknown.json")
        bundles._write_exclusive(path, {"schema_version": 1, "run_nonce": snapshot.run_nonce,
            "manifest_sha256": snapshot.manifest_sha256, "project": project,
            "status": "unknown", "reason": report["reason"],
            "attempt_reserved": report.get("attempt_reserved"),
            "finalization": report.get("finalization"), "shutdown": report.get("shutdown")})
    except (OSError, ValueError, TypeError):
        pass  # Preserve conflicts; never turn a failed best-effort write into proof.


def finalize(snapshot, project, reservation, report, *, approval, expected_approval, receipt, contract, deadline):
    """Publish pending evidence, then attempt finish ONCE, even on path conflicts.

    The immutable evidence file explicitly is NOT a finalized verified claim.
    The existing a/b-report.json and ledger are authoritative finalization proof.
    expected_approval drives the existing late authority check INSIDE finish lock.
    No retry, refund, overwrite or repair is permitted on any failure path.
    """
    needs_unknown = False
    try:
        check_deadline(deadline)
        path = bundles._safe_child(snapshot.bundle, "attempts/" + project + "-appserver-evidence.json")
        payload = copy.deepcopy(report)
        payload["candidate_status"], payload["status"] = report["status"], "pending_finalization"
        bundles._write_exclusive(path, payload)
    except (OSError, ValueError, TypeError):
        report["status"], report["reason"] = "unknown", "C1_FINALIZATION_UNCONFIRMED"
        needs_unknown = True
    try:
        check_deadline(deadline)
        authority(snapshot, project, approval, expected_approval, receipt, contract)
        check_deadline(deadline)
    except DeadlineExpired:
        report["status"], report["reason"] = "unknown", "C1_FINALIZATION_UNCONFIRMED"
        needs_unknown = True
    except (OSError, ValueError, TypeError):
        report["status"], report["reason"] = "unknown", "C1_FINALIZATION_AUTHORITY_CHANGED"
        needs_unknown = True
    report["finalization"] = {"attempted": True, "confirmed": False}
    try:
        finish_attempt(snapshot, reservation, {"state": report["status"],
            "detail": report["reason"] or "C1_APPSERVER_EVIDENCE_VALIDATED"},
            expected_approval=expected_approval, deadline=deadline)
        report["finalization"]["confirmed"] = True
    except (OSError, ValueError, TypeError, ContractError) as error:
        report["status"] = "unknown"
        report["reason"] = ("C1_FINALIZATION_AUTHORITY_CHANGED"
                            if isinstance(error, ValueError) and str(error) == "C1_FINALIZATION_AUTHORITY_CHANGED"
                            else "C1_FINALIZATION_UNCONFIRMED")
        needs_unknown = True
    if needs_unknown:
        unknown_artifact(snapshot, project, report)
