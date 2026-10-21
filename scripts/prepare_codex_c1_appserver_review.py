"""Export/verify a fresh passive C1-T sidecar without changing schema2 semantics.

There is deliberately no run action, executable, origin or factory CLI option.
The sidecar is an immutable fixed profile, not an execution/configuration input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts import codex_c1_bundle as bundles
from scripts import prepare_codex_c1_review as base
from scripts.codex_c1_bundle import ReviewSnapshot

TRANSPORT_KIND = "app_server_stdio_0.163.0"
NATIVE_ENTRY_STATUS = "C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING"
SIDECAR = "appserver-contract.preview.json"


def _safe_path(path: Path, *, fresh=False) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ValueError("C1_APPSERVER_REVIEW_PATH_INVALID")
    # Check lexical ancestors BEFORE resolve(), which would hide reparse entries.
    for directory in reversed((path, *path.parents)):
        if bundles._path_entry_exists(directory):
            bundles._assert_safe_directory(directory)
    if fresh and bundles._path_entry_exists(path):
        raise ValueError("C1_APPSERVER_REVIEW_OUTPUT_EXISTS")
    return path.resolve(strict=not fresh)


def transport_contract(snapshot: ReviewSnapshot) -> dict:
    """Reconstruct the fixed passive profile from actual immutable base bytes.

    Installed synthetic bundles are allowed here for trusted offline callers;
    verify_appserver_review separately enforces the original review-only gate.
    Returned structures are detached, and never grant native dispatch authority.
    """
    if not isinstance(snapshot, ReviewSnapshot):
        raise ValueError("C1_APPSERVER_SNAPSHOT_REQUIRED")
    _safe_path(snapshot.bundle)
    bundles._check_snapshot_current(snapshot)
    current = bundles.load_snapshot(snapshot.bundle)
    if (current.manifest_sha256 != snapshot.manifest_sha256 or current.run_nonce != snapshot.run_nonce
            or current.manifest != snapshot.manifest):
        raise ValueError("C1_APPSERVER_SNAPSHOT_CHANGED")
    return {
        "schema_version": 1,
        "transport_kind": TRANSPORT_KIND,
        "target_version": "0.163.0",
        "base_manifest_sha256": current.manifest_sha256,
        "client_requests": ["initialize", "config/read", "hooks/list", "account/rateLimits/read",
                            "thread/start", "turn/start", "turn/interrupt"],
        "client_notifications": ["initialized"],
        "server_notifications": ["thread/started", "turn/started", "item/started", "item/completed",
            "turn/completed", "thread/settings/updated", "thread/status/changed", "thread/tokenUsage/updated",
            "item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
            "item/reasoning/textDelta", "serverRequest/resolved", "error"],
        "server_cancel_responses": {
            "item/commandExecution/requestApproval": {"decision": "cancel"},
            "item/fileChange/requestApproval": {"decision": "cancel"},
            "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"}},
        "policy": {"approval_policy": "on-request", "approvals_reviewer": "user", "sandbox": "read-only",
            "network_access": False, "ephemeral": True, "allow_provider_model_fallback": False,
            "experimental_raw_events": False},
        "limits": {"task_timeout_seconds": 180, "preflight_timeout_seconds": 30, "max_line_bytes": 262144,
            "max_received_bytes": 2097152, "max_sent_bytes": 2097152, "queue_lines": 16,
            "max_attempts_total": 2, "max_attempts_per_project": 1, "max_tool_calls_per_task": 5,
            "automatic_retry": False},
        "source_sha256": dict(current.manifest["source_sha256"]),
        "native_entry_enabled": False,
        "native_entry_status": NATIVE_ENTRY_STATUS,
    }


def _checked_contract_bytes(snapshot: ReviewSnapshot, supplied: dict):
    expected = transport_contract(snapshot)
    path = bundles._safe_child(snapshot.bundle, SIDECAR)
    raw, actual = bundles._read_json_bytes(path)
    encoded = bundles._json_bytes(expected)
    if (type(supplied) is not dict or bundles._json_bytes(supplied) != encoded
            or bundles._json_bytes(actual) != encoded or raw != encoded):
        raise ValueError("C1_APPSERVER_CONTRACT_CHANGED")
    return expected, raw


def checked_contract(snapshot: ReviewSnapshot, supplied: dict) -> dict:
    """Compare the complete caller and disk profile, including strict JSON types.

    This is candidate-local reuse, not a loader for arbitrary sidecar commands.
    Bounds/duplicate/reparse rejection precede parsing; no repair is attempted.
    """
    return _checked_contract_bytes(snapshot, supplied)[0]


def prepare_appserver_review(output: Path, run_nonce: str) -> dict:
    """Create a NEW schema2 base and exclusively publish its passive sidecar."""
    output = _safe_path(output, fresh=True)
    prepared = base.prepare(output, run_nonce)
    snapshot = bundles.load_snapshot(output)
    contract = transport_contract(snapshot)
    bundles._write_exclusive(bundles._safe_child(output, SIDECAR), contract)
    return {"status": "appserver_review_prepared", "base_review": prepared,
            "sidecar": str(output / SIDECAR), "native_entry_enabled": False,
            "native_entry_status": NATIVE_ENTRY_STATUS, "native_model_tasks_started": 0}


def verify_appserver_review(output: Path) -> dict:
    """Verify original review-only semantics, then independently rebuild sidecar."""
    output = _safe_path(output)
    # Strict bounded manifest decoding supplements, never replaces, old verification.
    snapshot = bundles.load_snapshot(output)
    base.verify(output)
    expected = transport_contract(snapshot)
    _expected, raw = _checked_contract_bytes(snapshot, expected)
    return {"status": "appserver_review_unchanged", "native_authorization_granted": False,
            "native_entry_enabled": False, "native_entry_status": NATIVE_ENTRY_STATUS,
            "base_manifest_sha256": snapshot.manifest_sha256,
            "sidecar_sha256": hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    parser.add_argument("--run-nonce", default=None)
    args = parser.parse_args()
    try:
        if args.verify:
            if args.run_nonce is not None:
                raise ValueError("C1_APPSERVER_VERIFY_NONCE_FORBIDDEN")
            result = verify_appserver_review(args.verify)
        else:
            nonce = args.run_nonce if args.run_nonce is not None else "c1-" + uuid.uuid4().hex
            result = prepare_appserver_review(args.output, nonce)
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "failed", "error_code": "C1_APPSERVER_REVIEW_INVALID_OR_CHANGED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
