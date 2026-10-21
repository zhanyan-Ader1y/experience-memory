"""Real local Python subprocess contracts; never native Codex authorization/evidence."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.codex_c1_process import OwnedProcess, OwnedProcessError
from scripts.codex_c1_bundle import install_configs, load_snapshot, reserve_attempt, rollback_config, validate_approval
from scripts.codex_c1_host import ReadOnlyProbe
from scripts.prepare_codex_c1_review import prepare
from scripts import codex_c1_executor as executor_api
from scripts.codex_c1_executor import run_project, inspect_project
from tests.unit.test_codex_c1_bundle import approval as simulation_approval

ROOT = Path(__file__).resolve().parents[2]


def python_process(code, directory, seconds=8):
    return OwnedProcess([sys.executable, "-I", "-c", code], cwd=directory,
                        env=dict(os.environ), deadline=time.monotonic() + seconds)


def pid_exited(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


@unittest.skipUnless(sys.platform == "win32", "Windows Job-owned offline integration")
class ExecutorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = self.directory / "synthetic-bundle"
        prepare(self.bundle, "c1-executor-offline-20261019")
        self.snapshot = load_snapshot(self.bundle)
        self.approval = simulation_approval(self.snapshot, actions=[
            "install_configs", "inspect", "run_project", "rollback_config"])
        self.receipt = install_configs(self.snapshot, self.approval)
        self.launches = []

    def factories(self, mode="success", seconds=12, *, mutate=None, cleanup_unknown=False):
        prefix = [sys.executable, "-I", str(ROOT / "tests/fixtures/codex_c1_fake_host.py"),
                  "--bundle", str(self.bundle), "--project", "a", "--mode", mode]

        def probe_factory(command, overrides):
            self.assertEqual(command, [str(Path(sys.executable))])
            return ReadOnlyProbe(prefix, overrides, timeout_seconds=8)

        def process_factory(argv, *, cwd, env, deadline):
            self.assertTrue((self.bundle / "attempts/a-reservation.json").is_file())
            self.assertEqual(argv[0], str(Path(sys.executable)))
            self.assertEqual(argv[1:4], ["--no-daemon", "exec", "--ephemeral"])
            self.assertNotIn("--ask-for-approval", argv)
            self.assertIn('approval_policy="on-request"', argv)
            self.assertIn("--json", argv)
            self.assertIn("--sandbox", argv)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[argv.index("-C") + 1], str(self.bundle / "projects/a"))
            self.assertEqual(cwd, self.bundle / "projects/a")
            self.assertNotIn("MEM0_API_KEY", env)
            self.launches.append(argv)
            if mutate is not None:
                mutate()
            process = OwnedProcess(prefix + argv[1:], cwd=cwd, env=env,
                                   deadline=min(deadline, time.monotonic() + seconds))
            if cleanup_unknown:
                original = process.stop
                def unconfirmed():
                    original()  # Real whole-job termination still happens.
                    return {"root_exited": True, "descendants_exited": False, "reason": "cleanup_unconfirmed"}
                process.stop = unconfirmed
            return process
        return process_factory, probe_factory

    def run_fake(self, mode="success", **kwargs):
        process_factory, probe_factory = self.factories(mode, **kwargs)
        return run_project(self.snapshot, "a", self.approval, self.receipt,
                           executable=Path(sys.executable), process_factory=process_factory,
                           probe_factory=probe_factory, execution_origin="offline_simulation")

    def test_real_sdk_five_steps_verify_distinct_exec_metadata_ids_and_exact_rollback(self):
        result = self.run_fake()
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["execution_origin"], "offline_simulation")
        self.assertIs(result["native_evidence_claimed"], False)
        self.assertEqual(result["evidence"]["transport_count"], 5)
        self.assertEqual(result["evidence"]["business_count"], 5)
        mapping = result["evidence"]["call_mapping"]
        self.assertEqual([row["step"] for row in mapping], [1, 2, 3, 4, 5])
        self.assertEqual([row["call_id"] for row in mapping], ["offline-metadata-call-" + str(i) for i in range(1, 6)])
        self.assertTrue(all(row["call_id"] != row["item_id"] for row in mapping))
        self.assertEqual(result["cleanup"], {"root_exited": True, "descendants_exited": True, "reason": "job_empty"})
        self.assertEqual(len(self.launches), 1)
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "a", self.approval)
        rolled = rollback_config(self.snapshot, "a", self.receipt)
        self.assertEqual(rolled["state"], "rolled_back")
        self.assertFalse((self.bundle / "projects/a/.codex/config.toml").exists())
        self.assertEqual((self.bundle / rolled["quarantine_path"]).read_bytes(),
                         (self.bundle / "a-config.preview.toml").read_bytes())
        second = rollback_config(self.snapshot, "b", self.receipt)
        self.assertFalse((self.bundle / "projects/b/.codex/config.toml").exists())
        self.assertEqual((self.bundle / second["quarantine_path"]).read_bytes(),
                         (self.bundle / "b-config.preview.toml").read_bytes())

    def test_sixth_forbidden_and_a_failure_stop_b_and_preserve_one_consumed_attempt(self):
        # Each subcase uses its own newly generated disposable bundle.
        for mode in ("sixth", "forbidden", "a_failed", "crash", "unknown_event"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.bundle = self.directory / ("synthetic-" + mode)
                    prepare(self.bundle, "c1-executor-" + mode.replace("_", "-"))
                    self.snapshot = load_snapshot(self.bundle)
                    self.approval = simulation_approval(self.snapshot, actions=["install_configs", "inspect", "run_project", "rollback_config"])
                    self.receipt = install_configs(self.snapshot, self.approval)
                    self.launches = []
                result = self.run_fake(mode)
                self.assertIn(result["status"], ("failed", "unknown"))
                self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
                with self.assertRaises(ValueError):
                    reserve_attempt(self.snapshot, "b", self.approval)
                self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
                self.assertEqual(len(self.launches), 1)
                self.assertTrue(result["cleanup"]["descendants_exited"])

    def test_quota_false_blocks_before_reservation_and_spawn(self):
        with self.assertRaises(ValueError):
            self.run_fake("quota_false")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        self.assertFalse(self.launches)

    def test_timeout_stream_limit_and_child_hang_stop_whole_owned_job(self):
        for mode in ("timeout", "long_stdout", "child_hang"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.bundle = self.directory / ("synthetic-" + mode)
                    prepare(self.bundle, "c1-executor-" + mode.replace("_", "-"))
                    self.snapshot = load_snapshot(self.bundle)
                    self.approval = simulation_approval(self.snapshot, actions=["install_configs", "inspect", "run_project", "rollback_config"])
                    self.receipt = install_configs(self.snapshot, self.approval)
                    self.launches = []
                started = time.monotonic()
                result = self.run_fake(mode, seconds=2)
                self.assertNotEqual(result["status"], "verified")
                self.assertLess(time.monotonic() - started, 10)
                self.assertTrue(result["cleanup"]["root_exited"] and result["cleanup"]["descendants_exited"])
                if mode == "child_hang":
                    self.assertTrue(all(pid_exited(pid) for pid in json.loads((self.bundle / "fake-pids.json").read_text())))

    def test_stderr_secrets_and_tool_handles_never_enter_persisted_executor_reports(self):
        result = self.run_fake("stderr_secret")
        self.assertEqual(result["status"], "verified", result)
        self.assertGreater(result["streams"]["stderr"]["bytes"], 0)
        encoded = json.dumps(result) + "".join(p.read_text(encoding="utf-8") for p in (self.bundle / "attempts").glob("*.json"))
        for secret in ("SYNTHETIC-STDERR-SECRET", "private-fake-account", "session_handle", "private raw failure secret"):
            self.assertNotIn(secret, encoded)

    def test_start_failure_consumes_reservation_and_finishes_unknown_without_retry(self):
        process_factory, probe_factory = self.factories()
        def failed_factory(*args, **kwargs):
            self.assertTrue((self.bundle / "attempts/a-reservation.json").exists())
            raise OSError("private raw failure secret")
        result = run_project(self.snapshot, "a", self.approval, self.receipt, executable=Path(sys.executable),
                             process_factory=failed_factory, probe_factory=probe_factory, execution_origin="offline_simulation")
        self.assertEqual(result["status"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertNotIn("private raw", json.dumps(result))
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "a", self.approval)

    def test_unconfirmed_whole_job_cleanup_overrides_even_valid_evidence_to_unknown(self):
        result = self.run_fake(cleanup_unknown=True)
        self.assertEqual(result["evidence"]["status"], "verified", result)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)

    def test_inspection_uses_real_whitelisted_rpc_then_rechecks_install_before_reservation(self):
        process_factory, probe_factory = self.factories()
        result = inspect_project(self.snapshot, "a", self.approval, self.receipt,
                                 executable=Path(sys.executable), probe_factory=probe_factory,
                                 execution_origin="offline_simulation")
        self.assertEqual(result["state"], "preflight_ready")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        def changing_probe(command, overrides):
            probe = probe_factory(command, overrides)
            original = probe.inspect
            def inspect(snapshot, project):
                result = original(snapshot, project)
                config = self.bundle / "projects/a/.codex/config.toml"
                config.write_bytes(config.read_bytes() + b"\n# changed during preflight\n")
                return result
            probe.inspect = inspect
            return probe
        with self.assertRaises(ValueError):
            run_project(self.snapshot, "a", self.approval, self.receipt, executable=Path(sys.executable),
                        process_factory=process_factory, probe_factory=changing_probe,
                        execution_origin="offline_simulation")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        changed = (self.bundle / "projects/a/.codex/config.toml").read_bytes()
        with self.assertRaises(ValueError):
            rollback_config(self.snapshot, "a", self.receipt)
        self.assertEqual((self.bundle / "projects/a/.codex/config.toml").read_bytes(), changed)

    def test_late_approval_drift_after_reservation_never_verifies_or_unlocks_b(self):
        for mode in ("deleted", "scope", "actions", "confirmation", "current_record"):
            with self.subTest(mode=mode):
                if self.launches:
                    self.bundle = self.directory / ("late-approval-" + mode)
                    prepare(self.bundle, "c1-late-approval-" + mode.replace("_", "-"))
                    self.snapshot = load_snapshot(self.bundle)
                    self.approval = simulation_approval(self.snapshot, actions=["install_configs", "inspect", "run_project", "rollback_config"])
                    self.receipt = install_configs(self.snapshot, self.approval)
                    self.launches = []
                path = self.bundle / "approval.json"
                def revoke():
                    if mode == "deleted":
                        path.unlink()
                    elif mode == "current_record":
                        self.approval["user_confirmation_reference"] = "test-only-current-record-revoked"
                    else:
                        record = json.loads(path.read_text(encoding="utf-8"))
                        if mode == "scope":
                            record["scope"] = "revoked-invalid-scope"
                        elif mode == "actions":
                            record["actions"] = ["install_configs", "inspect", "rollback_config"]
                        else:
                            record["user_confirmation_reference"] = "test-only-saved-reference-revoked"
                        path.write_text(json.dumps(record), encoding="utf-8")
                with patch.object(executor_api, "finish_attempt", wraps=executor_api.finish_attempt) as finished:
                    result = self.run_fake(mutate=revoke)
                self.assertEqual(finished.call_count, 1)
                self.assertEqual(result["evidence"]["status"], "verified", result)
                self.assertEqual(result["status"], "unknown", result)
                self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
                ledger = json.loads((self.bundle / "attempts/ledger.json").read_text())
                self.assertEqual(ledger["projects"], {"a": "unknown", "b": "blocked"})
                self.assertTrue((self.bundle / "attempts/a-reservation.json").is_file())
                self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())
                if mode == "deleted":
                    self.assertFalse(path.exists(), "revoked approval must not be recreated")
                elif mode != "current_record":
                    self.assertEqual(json.loads(path.read_text())["scope"] if mode == "scope" else
                                     json.loads(path.read_text())["actions"] if mode == "actions" else
                                     json.loads(path.read_text())["user_confirmation_reference"],
                                     "revoked-invalid-scope" if mode == "scope" else
                                     ["install_configs", "inspect", "rollback_config"] if mode == "actions" else
                                     "test-only-saved-reference-revoked")

    def test_approval_deleted_at_actual_finish_boundary_downgrades_once(self):
        path = self.bundle / "approval.json"
        original = executor_api.finish_attempt
        def finish_after_revocation(*args, **kwargs):
            path.unlink()
            return original(*args, **kwargs)  # Real lock, writes, stop and ledger side effects.
        with patch.object(executor_api, "finish_attempt", side_effect=finish_after_revocation) as finished:
            result = self.run_fake()
        self.assertEqual(finished.call_count, 1)
        self.assertEqual(result["evidence"]["status"], "verified", result)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "C1_FINALIZATION_AUTHORITY_CHANGED")
        self.assertEqual(finished.call_args.kwargs["expected_approval"], self.approval)
        self.assertFalse(path.exists())
        self.assertEqual(json.loads((self.bundle / "attempts/a-report.json").read_text())["state"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertTrue((self.bundle / "attempts/a-finalization-unknown.json").is_file())
        self.assertEqual(json.loads((self.bundle / "attempts/ledger.json").read_text())["projects"],
                         {"a": "unknown", "b": "blocked"})

    def test_directory_evidence_collision_still_finalizes_once_and_preserves_entry(self):
        evidence = self.bundle / "attempts/a-execution.json"
        def collide():
            evidence.mkdir()
            (evidence / "existing-marker").write_bytes(b"preserve prior directory")
        with patch.object(executor_api, "finish_attempt", wraps=executor_api.finish_attempt) as finished:
            try:
                result = self.run_fake(mutate=collide)
            finally:
                self.assertEqual(finished.call_count, 1)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual((evidence / "existing-marker").read_bytes(), b"preserve prior directory")
        self.assertTrue((self.bundle / "attempts/a-finalization-unknown.json").is_file())
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertEqual(json.loads((self.bundle / "attempts/ledger.json").read_text())["projects"],
                         {"a": "unknown", "b": "blocked"})

    def test_unwritable_unknown_artifact_does_not_escape_or_repeat_finalization(self):
        evidence = self.bundle / "attempts/a-execution.json"
        emergency = self.bundle / "attempts/a-finalization-unknown.json"
        def collide():
            for path in (evidence, emergency):
                path.mkdir()
                (path / "existing-marker").write_bytes(b"preserve existing unknown path")
        with patch.object(executor_api, "finish_attempt", wraps=executor_api.finish_attempt) as finished:
            try:
                result = self.run_fake(mutate=collide)
            finally:
                self.assertEqual(finished.call_count, 1)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        for path in (evidence, emergency):
            self.assertEqual((path / "existing-marker").read_bytes(), b"preserve existing unknown path")

    def test_changed_approval_is_rejected_before_any_probe_process(self):
        altered = dict(self.approval, user_confirmation_reference="test-only-not-installed-record")
        def must_not_construct(*args, **kwargs):
            self.fail("changed independent approval reached host construction")
        with self.assertRaises(ValueError):
            inspect_project(self.snapshot, "a", altered, self.receipt, executable=Path(sys.executable),
                            probe_factory=must_not_construct, execution_origin="offline_simulation")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_changed_approval_during_preflight_is_rejected_not_reported_ready(self):
        process_factory, probe_factory = self.factories()
        def changing_probe(command, overrides):
            probe = probe_factory(command, overrides)
            original = probe.inspect
            def inspect(snapshot, project):
                result = original(snapshot, project)
                path = self.bundle / "approval.json"
                saved = json.loads(path.read_text())
                saved["user_confirmation_reference"] = "test-only-changed-during-preflight"
                path.write_text(json.dumps(saved), encoding="utf-8")
                return result
            probe.inspect = inspect
            return probe
        with self.assertRaises(ValueError):
            inspect_project(self.snapshot, "a", self.approval, self.receipt, executable=Path(sys.executable),
                            probe_factory=changing_probe, execution_origin="offline_simulation")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_inspect_and_run_actions_are_independent_and_missing_inspect_never_reserves(self):
        limited = dict(self.approval, actions=["run_project"])
        process_factory, probe_factory = self.factories()
        with self.assertRaises(ValueError):
            run_project(self.snapshot, "a", limited, self.receipt, executable=Path(sys.executable),
                        process_factory=process_factory, probe_factory=probe_factory, execution_origin="offline_simulation")
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        self.assertFalse(self.launches)

    def test_changed_config_after_reservation_preserves_unknown_and_consumed_slot(self):
        config = self.bundle / "projects/a/.codex/config.toml"
        def change():
            config.write_bytes(config.read_bytes() + b"\n# concurrent local edit\n")
        result = self.run_fake(mutate=change)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "C1_FINALIZATION_UNCONFIRMED")
        self.assertTrue((self.bundle / "attempts/a-reservation.json").exists())
        self.assertTrue((self.bundle / "attempts/a-finalization-unknown.json").exists())
        self.assertIn(b"# concurrent local edit", config.read_bytes())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())

    def test_existing_execution_evidence_is_not_overwritten_or_refunded(self):
        evidence = self.bundle / "attempts/a-execution.json"
        evidence.write_bytes(b'{"status":"old-unknown-evidence"}')
        before = evidence.read_bytes()
        result = self.run_fake()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(evidence.read_bytes(), before)
        self.assertTrue((self.bundle / "attempts/a-finalization-unknown.json").exists())
        self.assertEqual(json.loads((self.bundle / "attempts/a-report.json").read_text())["state"], "unknown")
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "a", self.approval)
        self.assertFalse((self.bundle / "attempts/b-reservation.json").exists())

    def test_simulation_cannot_select_default_native_factories_or_dynamic_cli_origin(self):
        with self.assertRaises(ValueError):
            run_project(self.snapshot, "a", self.approval, self.receipt,
                        executable=Path(sys.executable), execution_origin="offline_simulation")
        # Native dispatch is deliberately unsupported at this pinned exec version:
        # exec forces Never; requested app-server config is not exec policy proof.
        with self.assertRaisesRegex(ValueError, "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED"):
            run_project(self.snapshot, "a", self.approval, self.receipt, executable=Path(sys.executable))
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())


class ExecutorCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = self.directory / "bundle"
        prepare(self.bundle, "c1-cli-offline-20261019")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(ROOT / "scripts/run_codex_c1.py"),
                               "--bundle", str(self.bundle), *args], cwd=self.directory,
                              capture_output=True, text=True, encoding="utf-8", timeout=20)

    def test_default_dry_run_is_read_only_and_never_reserves_or_installs(self):
        before = {p.relative_to(self.bundle).as_posix(): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["status"], "dry_run")
        self.assertIs(body["native_dispatch_supported"], False)
        self.assertIs(body["native_evidence_claimed"], False)
        after = {p.relative_to(self.bundle).as_posix(): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_run_without_authorization_or_simulation_approval_never_launches(self):
        result = self.cli("run", "--project", "a", "--executable", sys.executable)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["native_dispatch_blocked_reason"], "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED")
        self.assertFalse((self.bundle / "attempts").exists())
        snapshot = load_snapshot(self.bundle)
        record = simulation_approval(snapshot)
        install_configs(snapshot, record)
        before = (self.bundle / "attempts/ledger.json").read_bytes()
        result = self.cli("run", "--project", "a", "--executable", sys.executable)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        self.assertEqual((self.bundle / "attempts/ledger.json").read_bytes(), before)

    def test_simulation_record_cannot_authorize_production_install_inspect_or_rollback(self):
        snapshot = load_snapshot(self.bundle)
        record = simulation_approval(snapshot, actions=["install_configs", "inspect", "run_project", "rollback_config"])
        confirmation = self.bundle / "simulation-only-confirmation.json"
        confirmation.write_text(json.dumps(record), encoding="utf-8")
        result = self.cli("install", "--approval", str(confirmation))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.bundle / "install-receipt.json").exists())
        receipt = install_configs(snapshot, record)
        config = self.bundle / "projects/a/.codex/config.toml"
        before = config.read_bytes()
        for args in (("inspect", "--project", "a", "--executable", sys.executable),
                     ("rollback", "--project", "a")):
            with self.subTest(action=args[0]):
                self.assertNotEqual(self.cli(*args).returncode, 0)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())

    def test_cli_rejects_factories_origins_retry_and_relative_paths(self):
        for extra in (("run", "--origin", "offline_simulation"), ("run", "--retry"),
                      ("run", "--all"), ("run", "--process-factory", "tests.fake"),
                      ("run", "--force"), ("inspect", "--executable", "codex")):
            with self.subTest(extra=extra):
                self.assertNotEqual(self.cli(*extra).returncode, 0)
        self.assertFalse((self.bundle / "attempts").exists())


@unittest.skipUnless(sys.platform == "win32", "Windows Job Object runtime contract")
class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_suspended_root_is_owned_before_first_instruction_and_whole_job_is_reaped(self):
        marker = self.directory / "first-instruction"
        observed = []
        original = OwnedProcess._resume_owned_process

        def inspect_before_resume(process):
            observed.append((process.is_job_member(), marker.exists()))
            return original(process)

        code = ("import os,subprocess,sys,time; from pathlib import Path; "
                f"Path({str(marker)!r}).write_text('ran'); "
                "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
                "print(str(os.getpid())+' '+str(child.pid),flush=True); time.sleep(60)")
        with patch.object(OwnedProcess, "_resume_owned_process", inspect_before_resume):
            process = python_process(code, self.directory)
        try:
            root, child = map(int, next(process.events()).split())
            self.assertEqual(observed, [(True, False)])
            self.assertTrue(marker.is_file())
            self.assertFalse(pid_exited(root))
            self.assertFalse(pid_exited(child))
        finally:
            cleanup = process.stop()
        self.assertEqual(cleanup, {"root_exited": True, "descendants_exited": True, "reason": "job_empty"})
        self.assertTrue(pid_exited(root))
        self.assertTrue(pid_exited(child))
        self.assertEqual(process.stop(), cleanup)

    def test_normal_exit_preserves_actual_exit_code_and_only_yields_stdout(self):
        process = python_process("import sys; print('one'); print('secret stderr',file=sys.stderr)", self.directory)
        try:
            self.assertEqual(list(process.events()), [b"one\r\n"])
            self.assertEqual(process.exit_code, 0)
        finally:
            cleanup = process.stop()
        self.assertTrue(cleanup["descendants_exited"])
        self.assertNotIn("secret stderr", json.dumps(process.stream_summary()))
        self.assertGreater(process.stream_summary()["stderr"]["bytes"], 0)

    def test_deadline_kills_hanging_parent_and_child_with_bounded_elapsed(self):
        process = python_process("import subprocess,sys,time,os; p=subprocess.Popen([sys.executable,'-I','-c',"
                                 "'import time; time.sleep(60)']); print(os.getpid(),p.pid,flush=True); time.sleep(60)",
                                 self.directory, seconds=2)
        started = time.monotonic()
        try:
            iterator = process.events()
            pids = list(map(int, next(iterator).split()))
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_DEADLINE"):
                list(iterator)
        finally:
            cleanup = process.stop()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(cleanup["root_exited"] and cleanup["descendants_exited"])
        self.assertTrue(all(pid_exited(pid) for pid in pids))

    def test_stream_line_and_total_limits_stop_real_writers_without_exposing_bytes(self):
        cases = [("stdout", "x" * 262145, "C1_PROCESS_LINE_LIMIT"),
                 ("stderr", "SECRET" * 50000, "C1_PROCESS_LINE_LIMIT"),
                 ("stdout", ("x" * 65535 + "\n") * 33, "C1_PROCESS_TOTAL_LIMIT")]
        for stream, body, reason in cases:
            with self.subTest(stream=stream, reason=reason):
                # File input keeps Windows command-line length independent of stream test size.
                payload = self.directory / (stream + reason + ".txt")
                payload.write_bytes(body.encode())
                code = (f"import sys,time; from pathlib import Path; sys.{stream}.buffer.write("
                        f"Path({str(payload)!r}).read_bytes()); sys.{stream}.flush(); time.sleep(60)")
                process = python_process(code, self.directory)
                try:
                    with self.assertRaisesRegex(OwnedProcessError, reason):
                        list(process.events())
                finally:
                    cleanup = process.stop()
                self.assertTrue(cleanup["descendants_exited"])
                self.assertNotIn("SECRET", json.dumps(process.stream_summary()))

    def test_assignment_failure_never_executes_suspended_python(self):
        marker = self.directory / "must-not-run"
        with patch.object(OwnedProcess, "_assign_job", side_effect=OSError("private raw detail")):
            with self.assertRaises(OwnedProcessError) as caught:
                python_process(f"from pathlib import Path; Path({str(marker)!r}).touch()", self.directory)
        self.assertFalse(marker.exists())
        self.assertNotIn("private raw detail", str(caught.exception))
        self.assertTrue(caught.exception.cleanup["root_exited"])
        self.assertTrue(caught.exception.cleanup["descendants_exited"])

    def test_resume_failure_never_executes_and_unconfirmed_job_cleanup_is_not_success(self):
        marker = self.directory / "must-not-run-resume"
        with patch.object(OwnedProcess, "_resume_owned_process", side_effect=OSError("secret")):
            with self.assertRaises(OwnedProcessError) as caught:
                python_process(f"from pathlib import Path; Path({str(marker)!r}).touch()", self.directory)
        self.assertFalse(marker.exists())
        self.assertTrue(caught.exception.cleanup["descendants_exited"])
        process = python_process("import time; time.sleep(60)", self.directory)
        with patch.object(process, "_active_job_processes", side_effect=OSError("secret")):
            cleanup = process.stop()
        self.assertFalse(cleanup["descendants_exited"])
        self.assertEqual(cleanup["reason"], "cleanup_unconfirmed")

    def test_mem0_key_is_removed_case_insensitively_from_child_environment(self):
        env = dict(os.environ, MEM0_API_KEY="synthetic-secret")
        process = OwnedProcess([sys.executable, "-I", "-c",
                                "import os; print(any(k.upper()=='MEM0_API_KEY' for k in os.environ))"],
                               cwd=self.directory, env=env, deadline=time.monotonic() + 8)
        try:
            self.assertEqual(b"".join(process.events()).strip(), b"False")
        finally:
            self.assertTrue(process.stop()["descendants_exited"])

    def test_invalid_inputs_fail_before_launch(self):
        for argv in (["python", "-c", "pass"], "shell string", [sys.executable, "bad\x00arg"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                OwnedProcess(argv, cwd=self.directory, env={}, deadline=time.monotonic() + 2)
        with self.assertRaises(ValueError):
            OwnedProcess([sys.executable], cwd=Path("relative"), env={}, deadline=time.monotonic() + 2)
