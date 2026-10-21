import hashlib
import json
from inspect import signature
from pathlib import Path
import tempfile
import threading
import unittest

from experience_memory.domain.errors import ErrorCode, MemoryError
from tests.fixtures.codex_c1_projects import make_projects
from tests.fixtures.codex_c1_server import NativeCodexFixture

NONCE = "c1-policy-20261018"


def metadata(root, session="native-session-a", call_id=...):
    values = {"sessionId": session, "threadId": session, "x-codex-turn-metadata": {
        "session_id": session, "thread_id": session, "codex_version": "0.163.0",
        "workspaces": {str(root): {}}}}
    if call_id is not ...:
        values["callId"] = call_id
    return values


class CodexC1PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.roots = make_projects(self.directory / "projects", NONCE)
        self.fixture_count = 0

    def test_fixture_exposes_explicit_bounded_mode(self):
        self.assertIn("bounded_scenario", signature(NativeCodexFixture).parameters)

    def fixture(self):
        self.fixture_count += 1
        fixture = NativeCodexFixture(
            project="a", project_root=self.roots["a"], other_project_root=self.roots["b"],
            run_nonce=NONCE, audit_dir=self.directory / ("audit-" + str(self.fixture_count)),
            bounded_scenario=True)
        self.addCleanup(fixture.close)
        return fixture

    def call(self, fixture, meta, tool, arguments, operation):
        with fixture.request_host(meta, tool=tool, arguments=arguments):
            return operation()

    def guided(self, fixture):
        ready = fixture.bootstrap(metadata(self.roots["a"], call_id="call-1"), NONCE)
        handle = ready["session_handle"]
        first = self.call(fixture, metadata(self.roots["a"], call_id="call-2"), "memory_status",
                          {"session_handle": handle}, lambda: fixture.memory.status(handle))
        self.assertTrue(first["succeeded"])
        for name, call_id in (("prior_session", "call-3"), ("foreign_project", "call-4")):
            seed_handle = ready[name]["session_handle"]
            with self.assertRaises(MemoryError) as caught:
                self.call(fixture, metadata(self.roots["a"], call_id=call_id), "memory_status",
                          {"session_handle": seed_handle}, lambda: fixture.memory.status(seed_handle))
            self.assertEqual(caught.exception.code, ErrorCode.SESSION_INVALID)
        fifth = self.call(fixture, metadata(self.roots["a"], call_id="call-5"), "memory_status",
                          {"session_handle": handle}, lambda: fixture.memory.status(handle))
        self.assertTrue(fifth["succeeded"])
        return ready

    def test_real_five_step_sequence_rejects_sixth_before_repository(self):
        fixture = self.fixture()
        ready = self.guided(fixture)
        before = len(fixture.api.calls)
        meta = metadata(self.roots["a"], call_id="call-six")
        with self.assertRaises(MemoryError) as caught:
            self.call(fixture, meta, "memory_status", {"session_handle": ready["session_handle"]},
                      lambda: fixture.memory.status(ready["session_handle"]))
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
        self.assertEqual(len(fixture.api.calls), before)
        self.assertEqual(fixture.policy.snapshot()["admitted"], 5)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

    def test_write_is_blocked_before_backend(self):
        fixture = self.fixture()
        ready = fixture.bootstrap(metadata(self.roots["a"], call_id="write-bootstrap"), NONCE)
        candidate = {"experience_id": "EXP-SYNTHETIC-WRITE", "body": "synthetic body",
                     "retention_text": "synthetic summary", "status": "active", "sources": ["test"],
                     "tags": [], "admission_basis": "offline fixture", "metadata": {},
                     "deduplication": {"decision": "new", "reason": "fixture"}}
        before = len(fixture.api.calls)
        args = {"session_handle": ready["session_handle"], "action": "upsert",
                "candidate": candidate, "operation_id": "offline-write"}
        with self.assertRaises(MemoryError) as caught:
            self.call(fixture, metadata(self.roots["a"], call_id="blocked-write"), "memory_write", args,
                      lambda: fixture.memory.write(ready["session_handle"], "upsert", candidate, "offline-write"))
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
        self.assertEqual(len(fixture.api.calls), before)

    def test_expected_session_invalid_advances_only_its_step(self):
        fixture = self.fixture()
        ready = fixture.bootstrap(metadata(self.roots["a"], call_id="call-1"), NONCE)
        first_handle = ready["session_handle"]
        self.call(fixture, metadata(self.roots["a"], call_id="call-2"), "memory_status",
                  {"session_handle": first_handle}, lambda: fixture.memory.status(first_handle))
        before = fixture.policy.snapshot()
        prior = ready["prior_session"]["session_handle"]
        with self.assertRaises(MemoryError) as caught:
            self.call(fixture, metadata(self.roots["a"], call_id="call-3"), "memory_status",
                      {"session_handle": prior}, lambda: fixture.memory.status(prior))
        self.assertEqual(caught.exception.code, ErrorCode.SESSION_INVALID)
        self.assertEqual(fixture.policy.snapshot()["completed"], before["completed"] + 1)
        self.assertEqual(fixture.policy.snapshot()["phase"], "active")
        self.assertEqual(fixture.policy.snapshot()["admitted"], before["admitted"] + 1)

    def test_wrong_handle_or_extra_argument_freezes(self):
        fixture = self.fixture()
        ready = fixture.bootstrap(metadata(self.roots["a"], call_id="call-1"), NONCE)
        before = len(fixture.api.calls)
        with self.assertRaises(MemoryError) as caught:
            self.call(fixture, metadata(self.roots["a"], call_id="call-2"), "memory_status",
                      {"session_handle": "forged-handle"}, lambda: fixture.memory.status("forged-handle"))
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
        self.assertEqual(len(fixture.api.calls), before)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

        fixture2 = self.fixture()
        ready2 = fixture2.bootstrap(metadata(self.roots["a"], call_id="call-1"), NONCE)
        with self.assertRaises(MemoryError) as extra:
            self.call(fixture2, metadata(self.roots["a"], call_id="call-2"), "memory_status",
                      {"session_handle": ready2["session_handle"], "debug": True},
                      lambda: fixture2.memory.status(ready2["session_handle"]))
        self.assertEqual(extra.exception.code, ErrorCode.INVALID_INPUT)
        self.assertEqual(fixture2.policy.snapshot()["phase"], "rejected")

    def test_duplicate_call_or_session_switch_freezes(self):
        fixture = self.fixture()
        ready = fixture.bootstrap(metadata(self.roots["a"], call_id="repeat"), NONCE)
        with self.assertRaises(MemoryError):
            self.call(fixture, metadata(self.roots["a"], call_id="repeat"), "memory_status",
                      {"session_handle": ready["session_handle"]},
                      lambda: fixture.memory.status(ready["session_handle"]))
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

        fixture2 = self.fixture()
        ready2 = fixture2.bootstrap(metadata(self.roots["a"], call_id="switch-1"), NONCE)
        with self.assertRaises(MemoryError):
            self.call(fixture2, metadata(self.roots["a"], session="native-session-b", call_id="switch-2"),
                      "memory_status", {"session_handle": ready2["session_handle"]},
                      lambda: fixture2.memory.status(ready2["session_handle"]))
        self.assertEqual(fixture2.policy.snapshot()["phase"], "rejected")

    def test_concurrent_calls_cannot_both_pass_one_step(self):
        from tests.fixtures.codex_c1_policy import RequestInfo

        fixture = self.fixture()
        policy = fixture.policy
        entered = threading.Event()
        release = threading.Event()
        errors = []
        first = RequestInfo("native-session-a", "parallel-1", "c1_bootstrap", {"probe_nonce": NONCE})
        second = RequestInfo("native-session-a", "parallel-2", "c1_bootstrap", {"probe_nonce": NONCE})

        def slow_operation():
            entered.set()
            release.wait(5)
            return {"status": "ready", "session_id": "native-session-a",
                    "session_handle": "synthetic-handle", "project_key": "local:c1-policy-20261018-a",
                    "host_source": "codex_stdio_0.163.0", "backend": "in_memory_platform_double",
                    "seed_origin": "synthetic_not_prior_native_sessions",
                    "prior_session": {"session_handle": "prior", "session_id": "synthetic-prior",
                                      "project_key": "local:c1-policy-20261018-a",
                                      "owner_status_verified": True},
                    "foreign_project": {"session_handle": "foreign", "session_id": "synthetic-foreign",
                                        "project_key": "local:c1-policy-20261018-b",
                                        "owner_status_verified": True}}

        def first_call():
            try:
                policy.invoke(first, slow_operation)
            except MemoryError as error:
                errors.append(error.code)

        thread = threading.Thread(target=first_call)
        thread.start()
        self.assertTrue(entered.wait(5))
        with self.assertRaises(MemoryError) as caught:
            policy.invoke(second, lambda: self.fail("concurrent request reached operation"))
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
        self.assertEqual(fixture.policy.snapshot()["completed"], 0)

    def test_contended_state_transitions_latch_before_waiting_for_state_lock(self):
        from tests.fixtures.codex_c1_policy import RequestInfo

        for entry in ("reserve", "reject_transport", "fail_audit"):
            with self.subTest(entry=entry):
                fixture = self.fixture()
                policy = fixture.policy
                request = RequestInfo("native-session-a", "contended", "c1_bootstrap", {"probe_nonce": NONCE})
                attempting = threading.Event()
                errors = []

                def contender():
                    attempting.set()
                    try:
                        if entry == "reserve":
                            policy.reserve(request)
                        elif entry == "reject_transport":
                            policy.reject_transport("HOST_SOURCE_UNVERIFIED")
                        else:
                            policy.fail_audit()
                    except MemoryError as error:
                        errors.append(error.code)

                policy.lock.acquire()
                worker = threading.Thread(target=contender)
                worker.start()
                try:
                    self.assertTrue(attempting.wait(5))
                    latched = policy.stop_requested.wait(1)
                finally:
                    policy.lock.release()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertTrue(latched, "a queued state lock must not delay the irreversible stop latch")
                self.assertEqual(policy.snapshot()["phase"], "unknown" if entry == "fail_audit" else "rejected")
                self.assertEqual(policy.snapshot()["admitted"], 0)
                if entry == "reserve":
                    self.assertEqual(errors, [ErrorCode.INVALID_INPUT])

    def test_audit_failure_freezes_unknown(self):
        from tests.fixtures.codex_c1_policy import RequestInfo

        fixture = self.fixture()
        request = RequestInfo("native-session-a", "audit-fail", "c1_bootstrap", {"probe_nonce": NONCE})
        fixture.policy.audit_dir.rmdir()
        ready = {"status": "ready", "session_id": "native-session-a", "session_handle": "current",
                 "project_key": "local:c1-policy-20261018-a", "host_source": "codex_stdio_0.163.0",
                 "backend": "in_memory_platform_double", "seed_origin": "synthetic_not_prior_native_sessions",
                 "prior_session": {"session_handle": "prior", "session_id": "prior-session",
                                  "project_key": "local:c1-policy-20261018-a", "owner_status_verified": True},
                 "foreign_project": {"session_handle": "foreign", "session_id": "foreign-session",
                                      "project_key": "local:c1-policy-20261018-b", "owner_status_verified": True}}
        with self.assertRaises(MemoryError):
            fixture.policy.invoke(request, lambda: ready)
        self.assertEqual(fixture.policy.snapshot()["phase"], "unknown")

    def test_bad_transport_freezes_bounded_session(self):
        fixture = self.fixture()
        with self.assertRaises(MemoryError) as caught:
            fixture.bootstrap(None, NONCE)
        self.assertEqual(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
        self.assertEqual(fixture.policy.snapshot()["failure_reason"], ErrorCode.HOST_SOURCE_UNVERIFIED.value)

    def test_missing_or_invalid_metadata_call_id_freezes_before_backend(self):
        cases = (("missing", metadata(self.roots["a"])),
                 ("null", metadata(self.roots["a"], call_id=None)),
                 ("empty", metadata(self.roots["a"], call_id="")),
                 ("nonstring", metadata(self.roots["a"], call_id=17)))
        for name, meta in cases:
            with self.subTest(call_id=name):
                fixture = self.fixture()
                before = len(fixture.api.calls)
                with self.assertRaises(MemoryError) as caught:
                    fixture.bootstrap(meta, NONCE)
                self.assertEqual(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)
                self.assertEqual(len(fixture.api.calls), before)
                self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

    def test_business_audit_uses_canonical_argument_hash_without_bodies(self):
        fixture = self.fixture()
        self.guided(fixture)
        rows = sorted((json.loads(path.read_text(encoding="utf-8"))
                       for path in fixture.audit_dir.glob("*.json")),
                      key=lambda row: row.get("step", 0))
        business = [row for row in rows if row["event"] == "request_business"]
        self.assertEqual(len(business), 5)
        self.assertEqual([row["step"] for row in business], [1, 2, 3, 4, 5])
        self.assertEqual(business[0]["arguments_sha256"], hashlib.sha256(
            b'{"probe_nonce":"c1-policy-20261018"}').hexdigest())
        self.assertEqual([row["outcome"] for row in business],
                         ["succeeded", "succeeded", "expected_error", "expected_error", "succeeded"])
        self.assertNotIn("session_handle", "".join(json.dumps(row) for row in business))
        self.assertNotIn("entries", "".join(json.dumps(row) for row in business))


if __name__ == "__main__":
    unittest.main()
