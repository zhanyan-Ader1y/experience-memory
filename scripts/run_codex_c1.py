"""Thin passive-by-default C1 CLI; no origin/factory/dynamic-command switches."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts import codex_c1_bundle as bundle_api
from scripts.codex_c1_executor import (NATIVE_DISPATCH_SUPPORTED, NATIVE_UNSUPPORTED_REASON,
                                      inspect_project, run_project)


def _record(snapshot, path, default):
    if path is None:
        path = snapshot.bundle / default
    if not path.is_absolute() or not path.is_relative_to(snapshot.bundle):
        raise ValueError("C1_RECORD_OUTSIDE_BUNDLE")
    relative = path.relative_to(snapshot.bundle).as_posix()
    safe = bundle_api._safe_child(snapshot.bundle, relative)
    return bundle_api._read_json(safe)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--bundle", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="action")
    subcommands.add_parser("dry-run", allow_abbrev=False)
    for action in ("inspect", "install", "run", "rollback"):
        subparser = subcommands.add_parser(action, allow_abbrev=False)
        subparser.add_argument("--approval", type=Path)
        if action in ("inspect", "run", "rollback"):
            subparser.add_argument("--project", choices=("a", "b"), required=True)
        if action in ("inspect", "run"):
            subparser.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        snapshot = bundle_api.load_snapshot(args.bundle)
        action = args.action or "dry-run"
        if action == "dry-run":
            if (snapshot.bundle / "install-receipt.json").exists():
                receipt = _record(snapshot, None, "install-receipt.json")
                checked = bundle_api.verify_snapshot(snapshot, phase="installed", receipt=receipt)
            else:
                checked = bundle_api.verify_snapshot(snapshot, phase="review")
            result = {"status": "dry_run", "run_nonce": snapshot.run_nonce,
                      "manifest_sha256": snapshot.manifest_sha256, "phase": checked["state"],
                      "native_dispatch_supported": NATIVE_DISPATCH_SUPPORTED,
                      "native_dispatch_blocked_reason": NATIVE_UNSUPPORTED_REASON,
                      "native_authorization_granted": False, "native_evidence_claimed": False,
                      "model_tasks_started": 0}
        else:
            approval = _record(snapshot, args.approval, "approval.json")
            approval_action = {"install": "install_configs", "run": "run_project",
                               "inspect": "inspect", "rollback": "rollback_config"}[action]
            bundle_api.validate_approval(snapshot, approval, approval_action, getattr(args, "project", None))
            # Simulation is available only to trusted programmatic tests; it is
            # never promoted to production CLI host inspection or launch authority.
            if approval["scope"] != "native-c1-wiring":
                raise ValueError("C1_NATIVE_SCOPE_REQUIRED")
            if action == "install":
                result = bundle_api.install_configs(snapshot, approval)
            else:
                saved = _record(snapshot, None, "approval.json")
                if saved != approval:
                    raise ValueError("C1_APPROVAL_CHANGED")
                receipt = _record(snapshot, None, "install-receipt.json")
                if action == "rollback":
                    result = bundle_api.rollback_config(snapshot, args.project, receipt)
                else:
                    operation = inspect_project if action == "inspect" else run_project
                    result = operation(snapshot, args.project, approval, receipt, executable=args.executable)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        # No arbitrary exception text, env, account data, raw paths or DTOs.
        print(json.dumps({"status": "blocked", "error_code": "C1_REQUEST_INVALID_OR_UNSUPPORTED",
                          "native_dispatch_supported": False,
                          "native_dispatch_blocked_reason": NATIVE_UNSUPPORTED_REASON,
                          "native_evidence_claimed": False}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("status") not in ("failed", "unknown") else 1


if __name__ == "__main__":
    raise SystemExit(main())
