"""Passive app-server contract sidecar, immutable base semantics and CLI tests."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import codex_c1_bundle as bundles
from scripts import prepare_codex_c1_review as base

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_codex_c1_appserver_review.py"
NONCE = "c1-passive-appserver-task5"


class AppServerReviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("scripts.prepare_codex_c1_appserver_review")
        except ModuleNotFoundError as error:
            if error.name == "scripts.prepare_codex_c1_appserver_review":
                self.fail("Task5 passive app-server review exporter is missing")
            raise
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.bundle = self.directory / "review with spaces"

    def prepare(self):
        self.result = self.api.prepare_appserver_review(self.bundle, NONCE)
        self.path = self.bundle / "appserver-contract.preview.json"
        return json.loads(self.path.read_text("utf-8"))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                              cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_new_passive_bundle_binds_actual_base_bytes_and_matching_current_sources(self):
        sidecar = self.prepare()
        manifest_path = self.bundle / "launch-review.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        self.assertEqual(self.result["status"], "appserver_review_prepared")
        self.assertEqual(sidecar["schema_version"], 1)
        self.assertEqual(sidecar["transport_kind"], "app_server_stdio_0.163.0")
        self.assertEqual(sidecar["target_version"], "0.163.0")
        self.assertEqual(sidecar["base_manifest_sha256"], hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertEqual(sidecar["source_sha256"], manifest["source_sha256"])
        for name in ("scripts/codex_c1_appserver_candidate.py", "scripts/codex_c1_appserver_runtime.py",
                     "scripts/prepare_codex_c1_appserver_review.py", "tests/fixtures/codex_c1_appserver_fake.py"):
            self.assertEqual(sidecar["source_sha256"][name], hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        self.assertIs(sidecar["native_entry_enabled"], False)
        self.assertEqual(sidecar["native_entry_status"], "C1_APPSERVER_NATIVE_ACCEPTANCE_PENDING")
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["mode"], "review_only")
        self.assertFalse(manifest["configuration_installed"])
        self.assertEqual(manifest["executor"], {"cli": "scripts/run_codex_c1.py", "default_action": "dry-run",
            "native_dispatch_supported": False, "native_dispatch_blocked_reason": "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED",
            "simulation_origin": "trusted_programmatic_tests_only"})
        self.assertEqual(base.verify(self.bundle)["status"], "review_unchanged")
        self.assertEqual(self.api.verify_appserver_review(self.bundle)["status"], "appserver_review_unchanged")
        for project in ("a", "b"):
            self.assertFalse((self.bundle / "projects" / project / ".codex/config.toml").exists())
        self.assertFalse((self.bundle / "attempts").exists())
        self.assertFalse((self.bundle / "approval.json").exists())

    def test_fixed_profile_declares_only_cancel_responses_and_original_tight_limits(self):
        sidecar = self.prepare()
        self.assertEqual(sidecar["client_requests"], ["initialize", "config/read", "hooks/list", "account/rateLimits/read",
                         "thread/start", "turn/start", "turn/interrupt"])
        self.assertEqual(sidecar["client_notifications"], ["initialized"])
        self.assertEqual(sidecar["server_notifications"], ["thread/started", "turn/started", "item/started", "item/completed",
            "turn/completed", "thread/settings/updated", "thread/status/changed", "thread/tokenUsage/updated",
            "item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
            "item/reasoning/textDelta", "serverRequest/resolved", "error"])
        self.assertEqual(sidecar["server_cancel_responses"], {
            "item/commandExecution/requestApproval": {"decision": "cancel"},
            "item/fileChange/requestApproval": {"decision": "cancel"},
            "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"}})
        self.assertEqual(sidecar["policy"], {"approval_policy": "on-request", "approvals_reviewer": "user",
            "sandbox": "read-only", "network_access": False, "ephemeral": True,
            "allow_provider_model_fallback": False, "experimental_raw_events": False})
        self.assertEqual(sidecar["limits"], {"task_timeout_seconds": 180, "preflight_timeout_seconds": 30,
            "max_line_bytes": 262144, "max_received_bytes": 2097152, "max_sent_bytes": 2097152,
            "queue_lines": 16, "max_attempts_total": 2, "max_attempts_per_project": 1,
            "max_tool_calls_per_task": 5, "automatic_retry": False})

    def test_existing_output_never_overwrites_base_or_sidecar(self):
        self.prepare()
        before = {name: (self.bundle / name).read_bytes() for name in ("launch-review.json", "appserver-contract.preview.json")}
        with self.assertRaises((ValueError, OSError)):
            self.api.prepare_appserver_review(self.bundle, NONCE)
        self.assertEqual({name: (self.bundle / name).read_bytes() for name in before}, before)

    def test_base_only_old_bundle_is_not_augmented_in_place(self):
        base.prepare(self.bundle, NONCE)
        before = (self.bundle / "launch-review.json").read_bytes()
        with self.assertRaises((ValueError, OSError)):
            self.api.prepare_appserver_review(self.bundle, NONCE)
        self.assertFalse((self.bundle / "appserver-contract.preview.json").exists())
        self.assertEqual((self.bundle / "launch-review.json").read_bytes(), before)

    def test_verify_reconstructs_profile_not_merely_self_reported_hashes(self):
        original = self.prepare()
        for change in ({"native_entry_enabled": True}, {"client_requests": ["thread/resume"]},
                       {"server_cancel_responses": {"item/fileChange/requestApproval": {"decision": "accept"}}},
                       {"base_manifest_sha256": "0" * 64}, {"target_version": "0.164.0"},
                       {"policy": dict(original["policy"], approvals_reviewer="auto_review")},
                       {"arbitrary_command": "must-not-execute"}):
            with self.subTest(change=change):
                self.path.write_text(json.dumps(dict(original, **change)), encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    self.api.verify_appserver_review(self.bundle)
                self.assertEqual(self.path.read_bytes(), before)

    def test_missing_sidecar_or_source_drift_is_never_verified(self):
        self.prepare()
        raw = self.path.read_bytes()
        self.path.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.api.verify_appserver_review(self.bundle)
        self.path.write_bytes(raw)
        value = json.loads(raw)
        value["source_sha256"]["scripts/codex_c1_appserver_candidate.py"] = "0" * 64
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)

    def test_duplicate_keys_and_oversize_sidecars_are_rejected_without_repair(self):
        self.prepare()
        for raw in (b'{"schema_version":1,"schema_version":1}', b" " * (1048576 + 1)):
            with self.subTest(size=len(raw)):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.api.verify_appserver_review(self.bundle)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_base_manifest_key_is_rejected_even_when_values_agree(self):
        self.prepare()
        path = self.bundle / "launch-review.json"
        raw = path.read_bytes()
        path.write_bytes(b'{"schema_version":2,' + raw[1:])
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)

    def test_nonfinite_or_wrong_typed_profile_never_matches_python_equal_values(self):
        original = self.prepare()
        for field, value in (("schema_version", True), ("native_entry_enabled", 0),
                             ("limits", dict(original["limits"], max_attempts_total=2.0))):
            with self.subTest(field=field):
                self.path.write_text(json.dumps(dict(original, **{field: value})), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.api.verify_appserver_review(self.bundle)
        self.path.write_bytes(b'{"schema_version":NaN}')
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)

    def test_contract_detached_and_bound_to_current_immutable_snapshot(self):
        self.prepare()
        snapshot = bundles.load_snapshot(self.bundle)
        first = self.api.transport_contract(snapshot)
        first["policy"]["network_access"] = True
        first["source_sha256"].clear()
        self.assertFalse(self.api.transport_contract(snapshot)["policy"]["network_access"])
        path = self.bundle / "launch-review.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            self.api.transport_contract(snapshot)

    def test_sidecar_reparse_or_directory_entries_are_refused_without_overwrite(self):
        self.prepare()
        original_lstat = Path.lstat
        def reparse(path):
            if path == self.path:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
            return original_lstat(path)
        with patch.object(Path, "lstat", autospec=True, side_effect=reparse):
            with self.assertRaises(ValueError):
                self.api.verify_appserver_review(self.bundle)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)
        self.assertTrue(self.path.is_dir())

    def test_output_reparse_parent_is_rejected_before_base_creation(self):
        original_lstat = Path.lstat
        parent = self.directory
        def reparse(path):
            if path == parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path)
        with patch.object(Path, "lstat", autospec=True, side_effect=reparse):
            with self.assertRaises(ValueError):
                self.api.prepare_appserver_review(self.bundle, NONCE)
        self.assertFalse(self.bundle.exists())

    def test_installed_bundle_never_passes_review_only_verification(self):
        from tests.unit.test_codex_c1_bundle import approval
        self.prepare()
        snapshot = bundles.load_snapshot(self.bundle)
        bundles.install_configs(snapshot, approval(snapshot))
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)

    def test_cli_only_prepares_or_verifies_passive_material_never_native_run(self):
        prepared = self.run_cli("--output", self.bundle, "--run-nonce", NONCE)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(json.loads(prepared.stdout)["status"], "appserver_review_prepared")
        verified = self.run_cli("--verify", self.bundle)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "appserver_review_unchanged")
        for args in (("--run", "a"), ("--process-factory", "untrusted.module"),
                     ("--execution-origin", "native_codex"), ("--executable", "codex")):
            rejected = self.run_cli(*args)
            self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse((self.bundle / "approval.json").exists())
        self.assertFalse((self.bundle / "attempts").exists())

    def test_byte_only_sidecar_change_is_refused_without_canonicalizing_old_artifact(self):
        self.prepare()
        changed = b" " + self.path.read_bytes() + b"\n"
        self.path.write_bytes(changed)
        with self.assertRaises(ValueError):
            self.api.verify_appserver_review(self.bundle)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_verified_sidecar_hash_never_performs_an_unbounded_second_read(self):
        self.prepare()
        original = Path.read_bytes
        def bounded_only(path):
            if path == self.path:
                raise AssertionError("sidecar must be hashed from its bounded validated read")
            return original(path)
        with patch.object(Path, "read_bytes", autospec=True, side_effect=bounded_only):
            result = self.api.verify_appserver_review(self.bundle)
        self.assertEqual(result["status"], "appserver_review_unchanged")
        self.assertEqual(result["sidecar_sha256"], hashlib.sha256(original(self.path)).hexdigest())

    def test_cli_explicit_empty_nonce_is_rejected_instead_of_silently_replaced(self):
        result = self.run_cli("--output", self.bundle, "--run-nonce", "")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.bundle.exists())

    def test_bad_nonce_or_relative_directory_creates_nothing(self):
        for output, nonce in ((self.bundle, "../unsafe"), (Path("relative-bundle"), NONCE)):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    self.api.prepare_appserver_review(output, nonce)
        self.assertFalse(self.bundle.exists())


if __name__ == "__main__":
    unittest.main()
