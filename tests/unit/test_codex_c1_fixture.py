import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from experience_memory.domain.errors import ErrorCode, MemoryError
from experience_memory.infrastructure.host_context import current_host
from tests.fixtures.codex_c1_projects import make_projects

NONCE = "c1-offline-20261018"


def metadata(root, session="native-session-a"):
    return {"sessionId": session, "threadId": session, "x-codex-turn-metadata": {
        "session_id": session, "thread_id": session, "codex_version": "0.163.0",
        "workspaces": {str(root): {}}}}


class CodexC1FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.roots = make_projects(self.directory / "projects", NONCE)

    def fixture(self, **overrides):
        self.assertIsNotNone(importlib.util.find_spec("tests.fixtures.codex_c1_server"),
                             "fixed-root C1 fixture has not been implemented")
        from tests.fixtures.codex_c1_server import NativeCodexFixture
        arguments = dict(project="a", project_root=self.roots["a"],
                         other_project_root=self.roots["b"], run_nonce=NONCE,
                         audit_dir=self.directory / "audit")
        arguments.update(overrides)
        fixture = NativeCodexFixture(**arguments)
        self.addCleanup(fixture.close)
        return fixture

    def test_bootstrap_binds_decoded_session_and_is_idempotent(self):
        fixture = self.fixture()
        first = fixture.bootstrap(metadata(self.roots["a"]), NONCE)
        second = fixture.bootstrap(metadata(self.roots["a"]), NONCE)
        self.assertEqual(first["session_handle"], second["session_handle"])
        self.assertEqual(first["session_id"], "native-session-a")
        self.assertEqual(first["project_key"], "local:" + NONCE + "-a")
        self.assertEqual(first["host_source"], "codex_stdio_0.163.0")
        self.assertEqual(first["backend"], "in_memory_platform_double")
        self.assertEqual(first["seed_origin"], "synthetic_not_prior_native_sessions")
        with fixture.request_host(metadata(self.roots["a"])):
            self.assertEqual(fixture.memory.status(first["session_handle"])["u_cursor"], 0)
        with self.assertRaises(MemoryError) as caught:
            current_host()
        self.assertEqual(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)

    def test_seed_handles_are_valid_for_owners_but_rejected_for_current_session(self):
        fixture = self.fixture()
        ready = fixture.bootstrap(metadata(self.roots["a"]), NONCE)
        for name in ("prior_session", "foreign_project"):
            with self.subTest(seed=name):
                seed = ready[name]
                owner = fixture.owner_status(name)
                self.assertTrue(owner["succeeded"])
                self.assertEqual(owner["session_handle"], seed["session_handle"])
                self.assertEqual(owner["session_id"], seed["session_id"])
                self.assertEqual(owner["project_key"], seed["project_key"])
                self.assertEqual(owner["u_cursor"], 0)
                self.assertTrue(seed["owner_status_verified"])
                with fixture.request_host(metadata(self.roots["a"])):
                    with self.assertRaises(MemoryError) as caught:
                        fixture.memory.status(seed["session_handle"])
                self.assertEqual(caught.exception.code, ErrorCode.SESSION_INVALID)
        self.assertNotEqual(ready["prior_session"]["session_id"], ready["session_id"])
        self.assertEqual(ready["prior_session"]["project_key"], ready["project_key"])
        self.assertNotEqual(ready["foreign_project"]["project_key"], ready["project_key"])

    def test_bad_nonce_or_identity_cannot_create_a_current_binding(self):
        fixture = self.fixture()
        before = dict(fixture.api.rows)
        cases = [(metadata(self.roots["a"]), "c1-wrong", ErrorCode.INVALID_INPUT),
                 (None, NONCE, ErrorCode.HOST_SOURCE_UNVERIFIED),
                 ({"sessionId": "native-session-a"}, NONCE, ErrorCode.HOST_SOURCE_UNVERIFIED),
                 (metadata(self.roots["b"]), NONCE, ErrorCode.SCOPE_MISMATCH)]
        for meta, nonce, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(MemoryError) as caught:
                fixture.bootstrap(meta, nonce)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(fixture.api.rows, before)
        valid = fixture.bootstrap(metadata(self.roots["a"]), NONCE)
        self.assertEqual(valid["session_id"], "native-session-a")

    def test_unmarked_mismatched_or_nonroot_projects_fail_before_audit(self):
        marker_path = self.roots["b"] / ".codex/c1-fixture.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        for value in (None, dict(marker, run_nonce="c1-another-run"), dict(marker, project="a")):
            if value is None:
                marker_path.unlink()
            else:
                marker_path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(marker=value), self.assertRaises((ValueError, MemoryError)):
                self.fixture()
            self.assertFalse((self.directory / "audit").exists())
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        nested = self.roots["a"] / "nested"
        nested.mkdir()
        for overrides in ({"project_root": nested}, {"other_project_root": self.roots["a"]},
                          {"project_root": Path("relative")}, {"run_nonce": "../unsafe"}):
            with self.subTest(overrides=overrides), self.assertRaises((ValueError, MemoryError)):
                self.fixture(**overrides)
            self.assertFalse((self.directory / "audit").exists())

    def test_transport_audit_is_id_only_and_context_recovers_after_exception(self):
        fixture = self.fixture()
        root = self.roots["a"]
        secret = "DO-NOT-PERSIST-CANDIDATE-OR-TRANSPORT-EXTRA"
        meta = metadata(root)
        meta["arbitrary_extra"] = {"body": secret}
        ready = fixture.bootstrap(meta, NONCE)
        with self.assertRaises(RuntimeError):
            with fixture.request_host(meta):
                self.assertEqual(current_host().session_id, "native-session-a")
                raise RuntimeError(secret)
        with self.assertRaises(MemoryError):
            current_host()
        with fixture.request_host(meta):
            self.assertTrue(fixture.memory.status(ready["session_handle"])["succeeded"])
        rows = [json.loads(path.read_text(encoding="utf-8"))
                for path in (self.directory / "audit").glob("*.json")]
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row["invocation_id"] for row in rows}), 3)
        allowed = {"schema_version", "event", "run_nonce", "invocation_id", "time_ns",
                   "transport_status", "session_id", "cwd_sha256", "error_code"}
        for row in rows:
            self.assertEqual(row["transport_status"], "decoded")
            self.assertEqual(row["session_id"], "native-session-a")
            self.assertLessEqual(set(row), allowed)
        local_files = [*fixture.pointers.data_root.glob("*"),
                       *(self.directory / "audit").glob("*.json")]
        persisted = "".join(path.read_text(encoding="utf-8") for path in local_files if path.is_file())
        self.assertNotIn(secret, persisted)
        self.assertNotIn('"entries"', persisted)
        self.assertNotIn('"body"', persisted)
        state_root = fixture.pointers.data_root
        fixture.close()
        self.assertFalse(state_root.exists())


if __name__ == "__main__":
    unittest.main()
