import copy
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from experience_memory.domain.errors import MemoryError as ContractError, ErrorCode
from experience_memory.infrastructure.session_pointer import SessionPointerStore
from scripts import codex_c1_bundle as bundle_api
from scripts.codex_c1_bundle import (
    finish_attempt, install_configs, load_snapshot, reserve_attempt,
    rollback_config, validate_approval, verify_snapshot,
)
from scripts.prepare_codex_c1_review import prepare

NONCE = "c1-bundle-offline-20261019"
PROJECTS = ["a", "b"]
ACTIONS = ["install_configs", "run_project", "rollback_config"]


def approval(snapshot, actions=ACTIONS):
    return {
        "schema_version": 1,
        "scope": "offline-simulation",
        "run_nonce": snapshot.run_nonce,
        "manifest_sha256": snapshot.manifest_sha256,
        "projects": PROJECTS,
        "max_attempts_total": 2,
        "max_attempts_per_project": 1,
        "max_tool_calls_per_task": 5,
        "task_timeout_seconds": 180,
        "actions": actions,
        "user_confirmation_reference": "test-only:synthetic-approval",
    }


class CodexC1BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name) / "bundle"
        prepare(self.bundle, NONCE)
        self.snapshot = load_snapshot(self.bundle)
        self.approval = approval(self.snapshot)

    def prepare_attempts(self):
        install_configs(self.snapshot, self.approval)

    @contextmanager
    def manifest_reads(self):
        manifest = self.bundle / "launch-review.json"
        original_open = Path.open
        sizes = []

        @contextmanager
        def traced_open(path, mode="r", *args, **kwargs):
            with original_open(path, mode, *args, **kwargs) as stream:
                if path == manifest and mode == "rb":
                    class Reader:
                        def read(self, size=-1):
                            sizes.append(size)
                            return stream.read(size)
                    yield Reader()
                else:
                    yield stream

        with patch.object(Path, "open", new=traced_open):
            yield sizes

    def _deadline_api(self):
        self.assertIn("deadline", inspect.signature(bundle_api.reserve_attempt).parameters,
                      "reserve must accept the original absolute deadline")
        self.assertIn("deadline", inspect.signature(bundle_api.finish_attempt).parameters,
                      "finish must accept the same original absolute deadline")

    def _contend(self, callback):
        ready, release = threading.Event(), threading.Event()
        errors = []
        def hold():
            try:
                with bundle_api._lock(self.snapshot):
                    ready.set()
                    release.wait(4)
            except BaseException as error:
                errors.append(type(error).__name__)
                ready.set()
        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            self.assertFalse(errors)
            started = time.monotonic()
            with self.assertRaises(ContractError) as caught:
                callback(started + 0.25)
            self.assertEqual(caught.exception.code, ErrorCode.LOCK_TIMEOUT)
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_reserve_deadline_bounds_actual_lock_contention_without_creating_slot(self):
        self._deadline_api()
        self.prepare_attempts()
        self._contend(lambda deadline: reserve_attempt(self.snapshot, "a", self.approval, deadline=deadline))
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        self.assertEqual(reservation["state"], "reserved")

    def test_finish_deadline_bounds_actual_lock_contention_preserving_consumed_slot(self):
        self._deadline_api()
        self.prepare_attempts()
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        before = (self.bundle / "attempts/a-reservation.json").read_bytes()
        self._contend(lambda deadline: finish_attempt(self.snapshot, reservation, {"state": "unknown"}, deadline=deadline))
        self.assertEqual((self.bundle / "attempts/a-reservation.json").read_bytes(), before)
        self.assertFalse((self.bundle / "attempts/a-report.json").exists())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)
        finish_attempt(self.snapshot, reservation, {"state": "unknown"})
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())

    def test_expired_deadline_fails_statically_and_default_old_api_still_finalizes(self):
        self._deadline_api()
        self.prepare_attempts()
        with self.assertRaisesRegex(ValueError, "^C1_ATTEMPT_DEADLINE_EXPIRED$"):
            reserve_attempt(self.snapshot, "a", self.approval, deadline=time.monotonic() - 1)
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        with self.assertRaisesRegex(ValueError, "^C1_ATTEMPT_DEADLINE_EXPIRED$"):
            finish_attempt(self.snapshot, reservation, {"state": "verified"}, deadline=time.monotonic() - 1)
        self.assertFalse((self.bundle / "attempts/a-report.json").exists())
        finish_attempt(self.snapshot, reservation, {"state": "verified"})
        second = reserve_attempt(self.snapshot, "b", self.approval)
        finish_attempt(self.snapshot, second, {"state": "verified"})

    def _delayed_setup_under_old_lock(self, callback):
        # Independent literal old-backend key: this must be the SAME file/key,
        # not a new coordination directory or a second advisory-lock namespace.
        coordination = self.bundle / ".coordination"
        digest = hashlib.sha256(json.dumps(["lock", self.snapshot.run_nonce, "native-attempts"]).encode()).hexdigest()
        lock_path = coordination / (digest + ".lock")
        store = SessionPointerStore(coordination)
        ready, release = threading.Event(), threading.Event()
        errors = []
        def holder():
            try:
                with store.lock(self.snapshot.run_nonce, "native-attempts", timeout_seconds=1):
                    ready.set()
                    release.wait(3)
            except BaseException as error:
                errors.append(type(error).__name__)
                ready.set()
        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        opened_at = None
        delayed = 0
        failure = None
        contender = threading.get_ident()
        original_open = os.open
        def delayed_open(path, flags, *args, **kwargs):
            nonlocal opened_at, delayed
            if Path(path) == lock_path and threading.get_ident() == contender:
                delayed += 1
                time.sleep(0.35)  # Real elapsed setup, no clock/source replacement.
                descriptor = original_open(path, flags, *args, **kwargs)
                opened_at = time.monotonic()
                return descriptor
            return original_open(path, flags, *args, **kwargs)
        try:
            self.assertTrue(ready.wait(2))
            self.assertFalse(errors)
            deadline = time.monotonic() + 0.20
            with patch.object(os, "open", side_effect=delayed_open):
                try:
                    callback(deadline)
                except (ValueError, ContractError) as error:
                    failure = error
            finished_at = time.monotonic()
            self.assertEqual(delayed, 1)
            self.assertIsNotNone(opened_at)
            self.assertLess(deadline - opened_at, 0)
            self.assertLess(finished_at - opened_at, 0.10,
                            "real setup expired: must not renew the earlier relative wait")
            self.assertIsInstance(failure, ValueError)
            self.assertEqual(str(failure), "C1_ATTEMPT_DEADLINE_EXPIRED")
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)

    def test_fix1_reserve_delayed_setup_expiry_never_renews_actual_contended_wait(self):
        self.prepare_attempts()
        self._delayed_setup_under_old_lock(
            lambda deadline: reserve_attempt(self.snapshot, "a", self.approval, deadline=deadline))
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        self.assertEqual(json.loads((self.bundle / "attempts/ledger.json").read_text())["projects"],
                         {"a": "available", "b": "blocked"})

    def test_fix1_finish_delayed_setup_expiry_preserves_actual_consumed_reservation(self):
        self.prepare_attempts()
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        path = self.bundle / "attempts/a-reservation.json"
        before = path.read_bytes()
        self._delayed_setup_under_old_lock(
            lambda deadline: finish_attempt(self.snapshot, reservation, {"state": "unknown"}, deadline=deadline))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.bundle / "attempts/a-report.json").exists())
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)

    def test_fix1_absolute_lock_acquires_remaining_budget_after_real_old_holder_releases(self):
        self.prepare_attempts()
        store = SessionPointerStore(self.bundle / ".coordination")
        ready, release = threading.Event(), threading.Event()
        errors = []
        def holder():
            try:
                with store.lock(self.snapshot.run_nonce, "native-attempts", timeout_seconds=1):
                    ready.set()
                    release.wait(3)
            except BaseException as error:
                errors.append(type(error).__name__)
                ready.set()
        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        timer = None
        try:
            self.assertTrue(ready.wait(2))
            self.assertFalse(errors)
            timer = threading.Timer(0.15, release.set)
            timer.start()
            start = time.monotonic()
            with bundle_api._lock(self.snapshot, deadline=start + 1):
                self.assertTrue(release.is_set())
                self.assertLess(time.monotonic(), start + 1)
            self.assertGreaterEqual(time.monotonic() - start, 0.10)
        finally:
            release.set()
            if timer is not None:
                timer.cancel()
                timer.join(2)
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(list((self.bundle / ".coordination").glob("*.lock"))), 1)

    def test_fix1_absolute_holder_excludes_actual_old_backend_and_releases_same_file(self):
        self.prepare_attempts()
        store = SessionPointerStore(self.bundle / ".coordination")
        ready, release = threading.Event(), threading.Event()
        errors = []
        def holder():
            try:
                with bundle_api._lock(self.snapshot, deadline=time.monotonic() + 2):
                    ready.set()
                    release.wait(1)
            except BaseException as error:
                errors.append(type(error).__name__)
                ready.set()
        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            self.assertFalse(errors)
            with self.assertRaises(ContractError) as caught:
                with store.lock(self.snapshot.run_nonce, "native-attempts", timeout_seconds=0.15):
                    self.fail("old backend entered while absolute C1 holder owned the SAME lock")
            self.assertEqual(caught.exception.code, ErrorCode.LOCK_TIMEOUT)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        with store.lock(self.snapshot.run_nonce, "native-attempts", timeout_seconds=0.5):
            self.assertTrue(release.is_set())
        self.assertEqual(len(list((self.bundle / ".coordination").glob("*.lock"))), 1)

    def test_oversize_manifest_is_read_once_with_limit_before_hashing(self):
        path = self.bundle / "launch-review.json"
        path.write_bytes(b" " * (bundle_api.MAX_JSON_BYTES + 1))
        for label, operation in (("load", lambda: load_snapshot(self.bundle)),
                                 ("recheck", lambda: validate_approval(
                                     self.snapshot, self.approval, "run_project", "a"))):
            with self.subTest(entry=label):
                with self.manifest_reads() as sizes:
                    with self.assertRaisesRegex(ValueError, "JSON exceeds size limit"):
                        operation()
                self.assertEqual(sizes, [bundle_api.MAX_JSON_BYTES + 1],
                                 "oversize rejection must not first read/hash the entire manifest")

    def test_manifest_hash_and_decode_use_the_same_single_bounded_read(self):
        path = self.bundle / "launch-review.json"
        raw = b" \r\n" + path.read_bytes() + b"\n  "
        path.write_bytes(raw)
        with self.manifest_reads() as sizes:
            snapshot = load_snapshot(self.bundle)
        self.assertEqual(sizes, [bundle_api.MAX_JSON_BYTES + 1])
        self.assertEqual(snapshot.manifest_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(snapshot.manifest, self.snapshot.manifest)
        with self.manifest_reads() as sizes:
            validate_approval(snapshot, approval(snapshot), "run_project", "a")
        self.assertEqual(sizes, [bundle_api.MAX_JSON_BYTES + 1])

    def test_schema_two_snapshot_is_immutable_and_bound_to_manifest_bytes(self):
        self.assertEqual(self.snapshot.manifest["schema_version"], 2)
        original = self.snapshot.manifest_sha256
        with self.assertRaises((TypeError, AttributeError)):
            self.snapshot.manifest["mode"] = "installed"
        self.assertEqual(self.snapshot.manifest_sha256, original)
        self.assertEqual(verify_snapshot(self.snapshot, phase="review")["state"], "review")

    def test_installed_verification_requires_separate_receipt_and_review_rejects_install(self):
        receipt = install_configs(self.snapshot, self.approval)
        with self.assertRaises(ValueError):
            verify_snapshot(self.snapshot, phase="review")
        self.assertEqual(verify_snapshot(self.snapshot, phase="installed", receipt=receipt)["state"], "installed")

    def test_changed_config_is_not_verified_or_rolled_back(self):
        receipt = install_configs(self.snapshot, self.approval)
        config = self.bundle / "projects/a/.codex/config.toml"
        config.write_bytes(config.read_bytes() + b"\n# local edit\n")
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            verify_snapshot(self.snapshot, phase="installed", receipt=receipt)
        with self.assertRaises(ValueError):
            rollback_config(self.snapshot, "a", receipt)
        self.assertEqual(config.read_bytes(), before)

    def test_installed_verification_rejects_changed_marker_without_rewriting_it(self):
        receipt = install_configs(self.snapshot, self.approval)
        marker = self.bundle / "projects/a/.codex/c1-fixture.json"
        marker.write_bytes(marker.read_bytes() + b" ")
        before = marker.read_bytes()
        with self.assertRaises(ValueError):
            verify_snapshot(self.snapshot, phase="installed", receipt=receipt)
        self.assertEqual(marker.read_bytes(), before)

    def test_installed_verification_rejects_dangling_config_and_hooks_reparse_entries(self):
        receipt = install_configs(self.snapshot, self.approval)
        original_lstat = Path.lstat
        for relative in ("projects/a/.codex/config.toml", "projects/a/.codex/hooks.json"):
            with self.subTest(path=relative):
                entry = self.bundle / relative
                if relative.endswith("config.toml"):
                    entry.unlink()
                def fake_lstat(path):
                    if path == entry:
                        return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
                    return original_lstat(path)
                with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
                    with self.assertRaises(ValueError):
                        verify_snapshot(self.snapshot, phase="installed", receipt=receipt)
                if relative.endswith("config.toml"):
                    self.assertFalse(entry.exists())
                    # Restore an exact synthetic config only so per-subtest state remains well formed.
                    entry.write_bytes((self.bundle / "a-config.preview.toml").read_bytes())

    def test_install_never_overwrites_existing_config(self):
        config = self.bundle / "projects/a/.codex/config.toml"
        config.write_bytes(b"user-owned config\n")
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            install_configs(self.snapshot, self.approval)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.bundle / "install-receipt.json").exists())

    def test_install_rejects_symlinked_config_and_preserves_target(self):
        target = Path(self.temp.name) / "outside.toml"
        target.write_bytes(b"outside user data")
        config = self.bundle / "projects/a/.codex/config.toml"
        try:
            config.symlink_to(target)
        except (OSError, NotImplementedError):
            original_lstat = Path.lstat
            def fake_lstat(path):
                if path == config:
                    return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
                return original_lstat(path)
            with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
                with self.assertRaises(ValueError):
                    install_configs(self.snapshot, self.approval)
            self.assertFalse(config.exists())
        else:
            with self.assertRaises(ValueError):
                install_configs(self.snapshot, self.approval)
            self.assertTrue(config.is_symlink())
        self.assertEqual(target.read_bytes(), b"outside user data")

    def test_approval_is_bound_to_action_project_and_snapshot(self):
        altered = dict(self.approval, manifest_sha256="0" * 64)
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, altered, "run_project", "a")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, self.approval, "run_project", "c")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, self.approval, "native_dispatch", "a")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, dict(self.approval, scope="unreviewed-scope"), "run_project", "a")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, dict(self.approval, granted=True), "run_project", "a")

    def test_inspect_is_separately_scoped_and_native_schema_is_only_an_in_memory_shape_check(self):
        # Schema DTO only. Not saved, installed, inspected, reserved, or dispatched;
        # this string is not human authorization or actual native evidence.
        shape = dict(self.approval, scope="native-c1-wiring", actions=["inspect"],
                     user_confirmation_reference="test-only-schema-not-human-authority")
        validate_approval(self.snapshot, shape, "inspect", "a")
        validate_approval(self.snapshot, dict(self.approval, actions=["inspect"]), "inspect", "a")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, self.approval, "inspect", "a")
        with self.assertRaises(ValueError):
            validate_approval(self.snapshot, shape, "run_project", "a")
        self.assertFalse((self.bundle / "approval.json").exists())
        self.assertFalse((self.bundle / "attempts").exists())

    def test_failed_or_unknown_attempt_stops_entire_round_and_is_not_reclaimed(self):
        self.prepare_attempts()
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        finish_attempt(self.snapshot, reservation, {"state": "unknown", "detail": "simulated crash"})
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)
        self.assertTrue((self.bundle / "attempts" / "stopped.json").is_file())
        self.assertTrue((self.bundle / "attempts" / "a-reservation.json").is_file())
        self.assertTrue((self.bundle / "attempts" / "a-report.json").is_file())

    def test_verified_a_requires_explicit_b_reservation_and_third_attempt_is_rejected(self):
        self.prepare_attempts()
        first = reserve_attempt(self.snapshot, "a", self.approval)
        finish_attempt(self.snapshot, first, {"state": "verified"})
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "a", self.approval)
        second = reserve_attempt(self.snapshot, "b", self.approval)
        self.assertEqual(second["project"], "b")
        finish_attempt(self.snapshot, second, {"state": "verified"})
        with self.assertRaises(ValueError):
            reserve_attempt(self.snapshot, "b", self.approval)

    def test_two_local_processes_compete_for_one_reservation(self):
        self.prepare_attempts()
        script = """import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1] + '/src', sys.argv[1]]
from scripts.codex_c1_bundle import load_snapshot, reserve_attempt
import json
snapshot = load_snapshot(Path(sys.argv[2]))
approval = json.loads(Path(sys.argv[3]).read_text())
try:
    reserve_attempt(snapshot, 'a', approval)
    print('reserved')
except ValueError:
    print('rejected')
"""
        approval_path = self.bundle / "test-approval.json"
        approval_path.write_text(json.dumps(self.approval), encoding="utf-8")
        env = os.environ.copy()
        command = [sys.executable, "-I", "-c", script, str(ROOT), str(self.bundle), str(approval_path)]
        first = subprocess.Popen(command, cwd=self.temp.name, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, cwd=self.temp.name, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out1, err1 = first.communicate(timeout=20)
        out2, err2 = second.communicate(timeout=20)
        self.assertEqual((first.returncode, second.returncode), (0, 0), (err1, err2))
        self.assertCountEqual([out1.strip(), out2.strip()], ["reserved", "rejected"])
        self.assertTrue((self.bundle / "attempts" / "a-reservation.json").is_file())

    def test_reserved_attempt_survives_restart_without_final_report(self):
        self.prepare_attempts()
        reserved = reserve_attempt(self.snapshot, "a", self.approval)
        reloaded = load_snapshot(self.bundle)
        with self.assertRaises(ValueError):
            reserve_attempt(reloaded, "a", self.approval)
        self.assertEqual(reserved["state"], "reserved")
        self.assertFalse((self.bundle / "attempts" / "a-report.json").exists())

    def test_missing_reserved_evidence_never_reclaims_attempt_slot(self):
        self.prepare_attempts()
        reserve_attempt(self.snapshot, "a", self.approval)
        (self.bundle / "attempts" / "a-reservation.json").unlink()
        with self.assertRaises(ValueError):
            reserve_attempt(load_snapshot(self.bundle), "a", self.approval)

    def test_rollback_preserves_concurrent_replacement_at_atomic_move_boundary(self):
        receipt = install_configs(self.snapshot, self.approval)
        config = self.bundle / "projects/a/.codex/config.toml"
        user_bytes = b"concurrent user replacement\n"
        original_rename = os.rename
        injected = False

        def replace_then_move(source, destination, *args, **kwargs):
            nonlocal injected
            if Path(source) == config and not injected:
                injected = True
                config.unlink()
                config.write_bytes(user_bytes)
            return original_rename(source, destination, *args, **kwargs)

        with patch.object(bundle_api.os, "rename", side_effect=replace_then_move):
            with self.assertRaises(ValueError):
                rollback_config(self.snapshot, "a", receipt)
        self.assertTrue(injected, "rollback never reached its filesystem move boundary")
        self.assertEqual(config.read_bytes(), user_bytes)
        quarantine_copies = [path for path in self.bundle.rglob("config.toml") if path != config]
        self.assertTrue(any(path.read_bytes() == user_bytes for path in quarantine_copies))

    def test_coordination_reparse_point_fails_before_outside_lock_side_effects(self):
        coordination = self.bundle / ".coordination"
        outside = Path(self.temp.name) / "outside-coordination"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_bytes(b"untouched")
        before = sorted(path.name for path in outside.iterdir())
        real_reparse = False
        try:
            os.symlink(outside, coordination, target_is_directory=True)
            real_reparse = True
        except (OSError, NotImplementedError):
            if os.name == "nt":
                command = f'mklink /J "{coordination}" "{outside}"'
                result = subprocess.run(["cmd.exe", "/d", "/c", command],
                                        capture_output=True, text=True, timeout=10)
                real_reparse = result.returncode == 0
        try:
            if real_reparse:
                with self.assertRaises(ValueError):
                    install_configs(self.snapshot, self.approval)
            else:
                original_lstat = Path.lstat
                original_resolve = Path.resolve
                def fake_lstat(path):
                    if path == coordination:
                        return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                    return original_lstat(path)
                def fake_resolve(path, *args, **kwargs):
                    if path == coordination:
                        return outside
                    return original_resolve(path, *args, **kwargs)
                with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
                    with patch.object(Path, "resolve", autospec=True, side_effect=fake_resolve):
                        with self.assertRaises(ValueError):
                            install_configs(self.snapshot, self.approval)
            self.assertEqual(sorted(path.name for path in outside.iterdir()), before)
            self.assertEqual(sentinel.read_bytes(), b"untouched")
            self.assertFalse((self.bundle / "approval.json").exists())
            self.assertFalse((self.bundle / "install-receipt.json").exists())
        finally:
            if real_reparse:
                if coordination.is_symlink():
                    coordination.unlink()
                elif coordination.exists():
                    os.rmdir(coordination)

    def test_finish_expected_approval_is_rechecked_under_actual_lock_and_stops_on_loss(self):
        self.approval = approval(self.snapshot, actions=ACTIONS + ["inspect"])
        receipt = install_configs(self.snapshot, self.approval)
        reservation = reserve_attempt(self.snapshot, "a", self.approval)
        expected = copy.deepcopy(self.approval)
        actual_lock = bundle_api._lock
        path = self.bundle / "approval.json"
        class RevokeWithinLock:
            def __init__(self, context):
                self.context = context
            def __enter__(self):
                value = self.context.__enter__()
                path.unlink()
                return value
            def __exit__(self, *args):
                return self.context.__exit__(*args)
        with patch.object(bundle_api, "_lock", side_effect=lambda snapshot: RevokeWithinLock(actual_lock(snapshot))):
            with self.assertRaisesRegex(ValueError, "^C1_FINALIZATION_AUTHORITY_CHANGED$"):
                finish_attempt(self.snapshot, reservation, {"state": "verified"}, expected_approval=expected)
        self.assertFalse(path.exists())
        report = json.loads((self.bundle / "attempts/a-report.json").read_text())
        self.assertEqual(report["state"], "unknown")
        self.assertEqual(report["detail"], "C1_FINALIZATION_AUTHORITY_CHANGED")
        self.assertEqual(json.loads((self.bundle / "attempts/ledger.json").read_text())["projects"],
                         {"a": "unknown", "b": "blocked"})
        self.assertTrue((self.bundle / "attempts/stopped.json").is_file())
        self.assertEqual((self.bundle / "projects/a/.codex/config.toml").read_bytes(),
                         (self.bundle / "a-config.preview.toml").read_bytes())

    def test_finish_expected_approval_keeps_valid_control_and_legacy_omission(self):
        self.approval = approval(self.snapshot, actions=ACTIONS + ["inspect"])
        install_configs(self.snapshot, self.approval)
        first = reserve_attempt(self.snapshot, "a", self.approval)
        finish_attempt(self.snapshot, first, {"state": "verified"}, expected_approval=copy.deepcopy(self.approval))
        second = reserve_attempt(self.snapshot, "b", self.approval)
        finish_attempt(self.snapshot, second, {"state": "verified"})
        self.assertEqual(json.loads((self.bundle / "attempts/ledger.json").read_text())["projects"],
                         {"a": "verified", "b": "verified"})

    def test_sequential_rollback_a_then_b_preserves_both_quarantines_but_never_verifies_run(self):
        receipt = install_configs(self.snapshot, self.approval)
        results = [rollback_config(self.snapshot, "a", receipt)]
        with self.assertRaises((OSError, ValueError)):
            verify_snapshot(self.snapshot, phase="installed", receipt=receipt)
        results.append(rollback_config(self.snapshot, "b", receipt))
        for project, result in zip(("a", "b"), results):
            self.assertFalse((self.bundle / ("projects/" + project + "/.codex/config.toml")).exists())
            self.assertEqual((self.bundle / result["quarantine_path"]).read_bytes(),
                             (self.bundle / (project + "-config.preview.toml")).read_bytes())
            with self.assertRaises((OSError, ValueError)):
                rollback_config(self.snapshot, project, receipt)
        with self.assertRaises((OSError, ValueError)):
            reserve_attempt(self.snapshot, "a", self.approval)

    def test_peer_rollback_tampering_or_changed_target_never_authorizes_remaining_removal(self):
        for mode in ("report", "identity", "quarantine", "quarantine_path", "receipt", "target", "missing_without_report"):
            with self.subTest(mode=mode):
                if mode != "report":
                    self.bundle = Path(self.temp.name) / ("bundle-" + mode)
                    prepare(self.bundle, "c1-rollback-" + mode.replace("_", "-"))
                    self.snapshot = load_snapshot(self.bundle)
                    self.approval = approval(self.snapshot)
                receipt = install_configs(self.snapshot, self.approval)
                if mode == "missing_without_report":
                    (self.bundle / "projects/a/.codex/config.toml").unlink()
                else:
                    result = rollback_config(self.snapshot, "a", receipt)
                    report_path = self.bundle / "rollback-a.json"
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    if mode == "report":
                        report["manifest_sha256"] = "0" * 64
                    elif mode == "identity":
                        report["config_identity"] = [0, 0]
                    elif mode == "quarantine_path":
                        report["quarantine_path"] = "../outside/config.toml"
                    elif mode == "quarantine":
                        (self.bundle / result["quarantine_path"]).write_bytes(b"tampered quarantine bytes")
                    elif mode == "receipt":
                        path = self.bundle / "install-receipt.json"
                        changed = json.loads(path.read_text())
                        changed["projects"]["a"]["created"] = False
                        path.write_text(json.dumps(changed), encoding="utf-8")
                    elif mode == "target":
                        config = self.bundle / "projects/b/.codex/config.toml"
                        config.write_bytes(config.read_bytes() + b"\n# user edit\n")
                    report_path.write_text(json.dumps(report), encoding="utf-8")
                config = self.bundle / "projects/b/.codex/config.toml"
                before = config.read_bytes()
                with self.assertRaises((OSError, ValueError)):
                    rollback_config(self.snapshot, "b", receipt)
                self.assertEqual(config.read_bytes(), before)
                self.assertFalse((self.bundle / "rollback-b.json").exists())

    def test_rollback_removes_only_files_created_by_its_receipt(self):
        receipt = install_configs(self.snapshot, self.approval)
        result = rollback_config(self.snapshot, "a", receipt)
        self.assertEqual(result["state"], "rolled_back")
        self.assertFalse((self.bundle / "projects/a/.codex/config.toml").exists())
        self.assertTrue((self.bundle / "projects/b/.codex/config.toml").exists())


if __name__ == "__main__":
    unittest.main()
