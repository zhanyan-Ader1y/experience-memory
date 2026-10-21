import json
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from tests.fixtures.codex_c1_projects import make_projects
from tests.fixtures.codex_c1_server import NativeCodexFixture

ROOT = Path(__file__).resolve().parents[2]
NONCE = "c1-stdio-offline-20261018"


def metadata(root, session="native-thread-one", call_id=...):
    values = {"sessionId": session, "threadId": session, "x-codex-turn-metadata": {
        "session_id": session, "thread_id": session, "codex_version": "0.163.0",
        "workspaces": {str(root): {}}}}
    if call_id is not ...:
        values["callId"] = call_id
    return values


class CodexC1StdioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.roots = await anyio.to_thread.run_sync(make_projects, self.directory / "projects", NONCE)

    async def start_server(self, stack, *, bounded=False, audit_name="audit"):
        self.assertTrue(hasattr(NativeCodexFixture, "create_server"),
                        "native-ready fixture has not registered production tools and bootstrap")
        arguments = ["-I", str(ROOT / "tests/fixtures/codex_c1_server.py"),
                     "--project", "a", "--project-root", str(self.roots["a"]),
                     "--other-project-root", str(self.roots["b"]), "--run-nonce", NONCE,
                     "--audit-dir", str(self.directory / audit_name)]
        if bounded:
            arguments.append("--bounded-scenario")
        params = StdioServerParameters(command=sys.executable, args=arguments, cwd=str(self.directory))
        read, write = await stack.enter_async_context(stdio_client(params))
        client = await stack.enter_async_context(ClientSession(read, write))
        with anyio.fail_after(15):
            initialized = await client.initialize()
        self.assertEqual(initialized.server_info.name, "experience-memory")
        return client

    async def call(self, client, tool, arguments, meta=None, error=None, *, check=True):
        with anyio.fail_after(10):
            result = await client.call_tool(tool, arguments, meta=meta)
        body = json.loads(result.content[0].text)
        if check:
            self.assertEqual(result.is_error, error is not None, body)
            if error is not None:
                self.assertEqual(body, {"status": "failed", "error_code": error})
        return body

    async def bootstrap(self, client, meta, **extra):
        return await self.call(client, "c1_bootstrap", dict(probe_nonce=NONCE, **extra), meta)

    async def test_bounded_stdio_enforces_five_business_steps_and_separate_transport_audit(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack, bounded=True)
            call_ids = ["meta-call-1", "meta-call-2", "meta-call-3", "meta-call-4", "meta-call-5"]
            metas = [metadata(self.roots["a"], call_id=value) for value in call_ids]
            ready = await self.bootstrap(client, metas[0])
            handle = ready["session_handle"]
            await self.call(client, "memory_status", {"session_handle": handle}, metas[1])
            await self.call(client, "memory_status", {"session_handle": ready["prior_session"]["session_handle"]},
                            metas[2], error="SESSION_INVALID")
            await self.call(client, "memory_status", {"session_handle": ready["foreign_project"]["session_handle"]},
                            metas[3], error="SESSION_INVALID")
            await self.call(client, "memory_status", {"session_handle": handle}, metas[4])
            rows_after_five = [json.loads(path.read_text(encoding="utf-8"))
                               for path in (self.directory / "audit").glob("*.json")]

            sixth = await self.call(client, "memory_status", {"session_handle": handle},
                                    metadata(self.roots["a"], call_id="meta-call-six"), check=False)
            rows_after_six = [json.loads(path.read_text(encoding="utf-8"))
                              for path in (self.directory / "audit").glob("*.json")]

        business = sorted((row for row in rows_after_five if row["event"] == "request_business"),
                          key=lambda row: row["step"])
        transport = [row for row in rows_after_five if row["event"] == "request_transport"]
        self.assertEqual(len(business), 5)
        self.assertEqual([row["step"] for row in business], [1, 2, 3, 4, 5])
        self.assertEqual([row["call_id"] for row in business], call_ids)
        self.assertEqual([row["outcome"] for row in business],
                         ["succeeded", "succeeded", "expected_error", "expected_error", "succeeded"])
        self.assertEqual(len(transport), 5)
        self.assertTrue(all(row["transport_status"] == "decoded" for row in transport))
        self.assertTrue(all(row["session_id"] == "native-thread-one" for row in transport))
        self.assertNotEqual({row["invocation_id"] for row in business},
                            {row["invocation_id"] for row in transport})
        self.assertEqual(sixth, {"status": "failed", "error_code": "INVALID_INPUT"})
        self.assertEqual(sum(row["event"] == "request_business" for row in rows_after_six), 5)
        self.assertEqual(sum(row["event"] == "request_business_rejected" for row in rows_after_six), 1)
        rejected = next(row for row in rows_after_six if row["event"] == "request_business_rejected")
        self.assertEqual(rejected["call_id"], "meta-call-six")
        self.assertEqual(sum(row["event"] == "request_transport" for row in rows_after_six), 6)
        encoded = "".join(json.dumps(row) for row in rows_after_six)
        self.assertNotIn("session_handle", encoded)
        self.assertNotIn('"entries"', encoded)

    async def test_bounded_stdio_raw_sdk_rejections_freeze_following_status(self):
        vectors = (("allowed", "memory_status", {"session_handle": 17}),
                   ("forbidden", "memory_search", {}),
                   ("unknown", "not_a_registered_tool", {}))
        for label, tool, arguments in vectors:
            with self.subTest(vector=label):
                audit_name = "audit-raw-" + label
                async with AsyncExitStack() as stack:
                    client = await self.start_server(stack, bounded=True, audit_name=audit_name)
                    ready = await self.bootstrap(client, metadata(self.roots["a"], call_id="bootstrap"))
                    invalid = await client.call_tool(tool, arguments,
                                                     meta=metadata(self.roots["a"], call_id="invalid"))
                    following = await client.call_tool("memory_status", {"session_handle": ready["session_handle"]},
                                                       meta=metadata(self.roots["a"], call_id="following"))
                    rows = [json.loads(path.read_text(encoding="utf-8"))
                            for path in (self.directory / audit_name).glob("*.json")]
                self.assertTrue(invalid.is_error)
                self.assertTrue(following.is_error, "SDK rejection must freeze subsequent business")
                self.assertEqual(json.loads(following.content[0].text),
                                 {"status": "failed", "error_code": "INVALID_INPUT"})
                self.assertEqual(sum(row["event"] == "request_transport" for row in rows), 3)
                self.assertEqual(sum(row["event"] == "request_business" for row in rows), 1)
                self.assertEqual(sum(row["event"] == "request_business_rejected" for row in rows), 2)

    async def test_metadata_call_id_duplicate_freezes_despite_distinct_json_rpc_ids(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack, bounded=True, audit_name="audit-duplicate-call-id")
            repeated = metadata(self.roots["a"], call_id="same-logical-call")
            ready = await self.bootstrap(client, repeated)
            # These are separate call_tool requests, so the SDK sends distinct JSON-RPC envelope IDs.
            second_result = await self.call(client, "memory_status",
                                            {"session_handle": ready["session_handle"]}, repeated, check=False)
            rows = [json.loads(path.read_text(encoding="utf-8"))
                    for path in (self.directory / "audit-duplicate-call-id").glob("*.json")]

        accepted = [row for row in rows if row["event"] == "request_business"]
        rejected = [row for row in rows if row["event"] == "request_business_rejected"]
        self.assertEqual(second_result, {"status": "failed", "error_code": "INVALID_INPUT"})
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["call_id"], "same-logical-call")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["call_id"], "same-logical-call")
        self.assertEqual(rejected[0]["error_code"], "INVALID_INPUT")

    async def test_missing_or_invalid_metadata_call_id_rejects_before_business_backend(self):
        cases = (("missing", metadata(self.roots["a"])),
                 ("empty", metadata(self.roots["a"], call_id="")),
                 ("nonstring", metadata(self.roots["a"], call_id=17)),
                 ("whitespace", metadata(self.roots["a"], call_id="   ")))
        for name, meta in cases:
            with self.subTest(call_id=name):
                async with AsyncExitStack() as stack:
                    audit_name = "audit-missing-call-id" if name == "missing" else "audit-invalid-call-id-" + name
                    client = await self.start_server(stack, bounded=True, audit_name=audit_name)
                    result = await self.call(client, "c1_bootstrap", {"probe_nonce": NONCE}, meta, check=False)
                    rows = [json.loads(path.read_text(encoding="utf-8"))
                            for path in (self.directory / audit_name).glob("*.json")]
                self.assertEqual(result, {"status": "failed", "error_code": "HOST_SOURCE_UNVERIFIED"})
                self.assertFalse(any(row["event"] in ("request_business", "request_business_rejected")
                                     for row in rows))
                transport = [row for row in rows if row["event"] == "request_transport"]
                self.assertEqual(len(transport), 1)
                self.assertEqual(transport[0]["transport_status"], "rejected")
                self.assertEqual(transport[0]["error_code"], "HOST_SOURCE_UNVERIFIED")

    async def test_registers_production_five_tools_and_nonce_only_fixture_bootstrap(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack)
            tools = (await client.list_tools()).tools
            by_name = {tool.name: tool for tool in tools}
            self.assertEqual(set(by_name), {"memory_search", "memory_read", "memory_write",
                                            "memory_use", "memory_status", "c1_bootstrap"})
            schema = by_name["c1_bootstrap"].input_schema
            self.assertEqual(set(schema["properties"]), {"probe_nonce"})
            self.assertEqual(schema["required"], ["probe_nonce"])
            self.assertFalse(by_name["c1_bootstrap"].annotations.read_only_hint)
            meta = metadata(self.roots["a"])
            await self.call(client, "memory_status", {"session_handle": "unbound"}, meta,
                            error="SESSION_INVALID")
            ready = await self.bootstrap(client, meta)
            status = await self.call(client, "memory_status", {"session_handle": ready["session_handle"]}, meta)
            self.assertEqual(status["session_id"], "native-thread-one")
            self.assertEqual(status["project_key"], "local:" + NONCE + "-a")
            self.assertEqual(status["host_source"], "codex_stdio_0.163.0")

    async def test_valid_seed_handles_are_rejected_without_advancing_current_u(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack)
            meta = metadata(self.roots["a"])
            ready = await self.bootstrap(client, meta)
            for name in ("prior_session", "foreign_project"):
                self.assertTrue(ready[name]["owner_status_verified"])
                await self.call(client, "memory_status", {"session_handle": ready[name]["session_handle"]},
                                meta, error="SESSION_INVALID")
            status = await self.call(client, "memory_status", {"session_handle": ready["session_handle"]}, meta)
            self.assertEqual(status["u_cursor"], 0)
            self.assertEqual(status["entries"], [])

    async def test_another_transport_session_cannot_use_a_still_valid_first_session_handle(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack)
            first_meta = metadata(self.roots["a"])
            second_meta = metadata(self.roots["a"], "native-thread-two")
            first = await self.bootstrap(client, first_meta)
            second = await self.bootstrap(client, second_meta)
            self.assertNotEqual(first["session_handle"], second["session_handle"])
            await self.call(client, "memory_status", {"session_handle": first["session_handle"]},
                            second_meta, error="SESSION_INVALID")
            owner = await self.call(client, "memory_status", {"session_handle": first["session_handle"]}, first_meta)
            self.assertEqual(owner["session_id"], "native-thread-one")
            self.assertEqual(owner["u_cursor"], 0)
            current = await self.call(client, "memory_status", {"session_handle": second["session_handle"]}, second_meta)
            self.assertEqual(current["session_id"], "native-thread-two")
            self.assertEqual(current["u_cursor"], 0)

    async def test_missing_foreign_or_malformed_transport_and_fake_arguments_fail_closed(self):
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack)
            meta = metadata(self.roots["a"])
            for bad_meta, error in ((None, "HOST_SOURCE_UNVERIFIED"),
                                    ({"sessionId": "partial"}, "HOST_SOURCE_UNVERIFIED"),
                                    (metadata(self.roots["b"]), "SCOPE_MISMATCH")):
                await self.call(client, "c1_bootstrap", {"probe_nonce": NONCE}, bad_meta, error=error)
            await self.call(client, "c1_bootstrap", {"probe_nonce": NONCE, "_meta": meta,
                            "session_id": "forged", "cwd": str(self.roots["a"])},
                            error="HOST_SOURCE_UNVERIFIED")
            await self.call(client, "c1_bootstrap", {"probe_nonce": "c1-wrong"}, meta, error="INVALID_INPUT")
            ready = await self.bootstrap(client, meta, session_id="forged", cwd=str(self.roots["b"]))
            self.assertEqual(ready["session_id"], "native-thread-one")
            status = await self.call(client, "memory_status", {"session_handle": ready["session_handle"]}, meta)
            self.assertEqual(status["u_cursor"], 0)

    async def test_production_write_read_use_remain_in_memory_and_audit_omits_bodies(self):
        secret = "C1-SYNTHETIC-BODY-MUST-NOT-ENTER-AUDIT"
        async with AsyncExitStack() as stack:
            client = await self.start_server(stack)
            meta = metadata(self.roots["a"])
            ready = await self.bootstrap(client, meta)
            handle = ready["session_handle"]
            candidate = {"experience_id": "EXP-C1-SYNTHETIC", "body": secret,
                         "retention_text": "synthetic summary", "status": "active",
                         "sources": ["synthetic-test"], "tags": [], "admission_basis": "offline fixture",
                         "metadata": {"evidence_type": "verified", "_meta": {"sessionId": "forged"}},
                         "deduplication": {"decision": "new", "reason": "synthetic fixture"}}
            written = await self.call(client, "memory_write", {"session_handle": handle, "action": "upsert",
                                      "candidate": candidate, "operation_id": "c1-write"}, meta)
            self.assertEqual(written["status"], "confirmed")
            read = await self.call(client, "memory_read", {"session_handle": handle,
                                   "record_id": written["record_id"]}, meta)
            self.assertEqual(read["record"]["body"], secret)
            used = await self.call(client, "memory_use", {"session_handle": handle,
                                   "receipt_id": read["receipt_id"], "priority": "P1",
                                   "reason": "synthetic use", "operation_id": "c1-use"}, meta)
            self.assertTrue(used["succeeded"])
            status = await self.call(client, "memory_status", {"session_handle": handle}, meta)
            self.assertEqual(status["session_id"], "native-thread-one")
            self.assertEqual(status["u_cursor"], 1)
        audit_files = list((self.directory / "audit").glob("*.json"))
        self.assertEqual(len(audit_files), 5)
        audit = "".join(path.read_text(encoding="utf-8") for path in audit_files)
        self.assertNotIn(secret, audit)
        self.assertNotIn("synthetic summary", audit)
        self.assertNotIn('"entries"', audit)


class CodexC1SDKAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """Real SDK dispatch on memory streams, retaining the real backend call log."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.roots = await anyio.to_thread.run_sync(make_projects, self.directory / "projects", NONCE)
        self.fixture_count = 0

    def fixture(self):
        self.fixture_count += 1
        fixture = NativeCodexFixture(
            project="a", project_root=self.roots["a"], other_project_root=self.roots["b"],
            run_nonce=NONCE, audit_dir=self.directory / ("sdk-audit-" + str(self.fixture_count)),
            bounded_scenario=True)
        self.addCleanup(fixture.close)
        return fixture

    @asynccontextmanager
    async def sdk_client(self, fixture, middleware=None):
        server = fixture.create_server()
        if middleware is not None:
            server.middleware.append(middleware)
        client_write, server_read = anyio.create_memory_object_stream(0)
        server_write, client_read = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(server._lowlevel_server.run, server_read, server_write,
                             server._lowlevel_server.create_initialization_options())
            try:
                async with ClientSession(client_read, client_write) as client:
                    with anyio.fail_after(10):
                        await client.initialize()
                    yield client
            finally:
                tasks.cancel_scope.cancel()

    async def call(self, client, tool, arguments, call_id, *, meta=None):
        if meta is None:
            meta = metadata(self.roots["a"], call_id=call_id)
        with anyio.fail_after(10):
            return await client.call_tool(tool, arguments, meta=meta)

    async def bootstrap(self, client):
        result = await self.call(client, "c1_bootstrap", {"probe_nonce": NONCE}, "bootstrap")
        return json.loads(result.content[0].text)

    def rows(self, fixture):
        return [json.loads(path.read_text(encoding="utf-8")) for path in fixture.audit_dir.glob("*.json")]

    async def test_raw_invalid_allowed_forbidden_unknown_freeze_before_sdk_and_backend(self):
        vectors = (("allowed", "memory_status", {"session_handle": 17}),
                   ("forbidden", "memory_search", {}),
                   ("unknown", "not_a_registered_tool", {}),
                   ("extra", "memory_status", {"session_handle": "current", "debug": True}),
                   ("missing", "memory_status", {}))
        for label, tool, arguments in vectors:
            with self.subTest(vector=label):
                fixture = self.fixture()
                downstream = []

                async def observe(context, call_next):
                    if context.method == "tools/call":
                        downstream.append(context.params["_meta"]["callId"])
                    return await call_next(context)

                async with self.sdk_client(fixture, observe) as client:
                    ready = await self.bootstrap(client)
                    before = len(fixture.api.calls)
                    invalid = await self.call(client, tool, arguments, "invalid")
                    following = await self.call(client, "memory_status",
                                                {"session_handle": ready["session_handle"]}, "following")
                    after = len(fixture.api.calls)
                self.assertTrue(invalid.is_error)
                self.assertTrue(following.is_error, "later valid status reached business after raw violation")
                self.assertEqual(after, before, "neither invalid nor following request may reach the backend")
                self.assertEqual(downstream, ["bootstrap"], "raw admission must precede SDK dispatch")
                self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
                self.assertEqual(fixture.policy.snapshot()["admitted"], 1)
                self.assertEqual(fixture.policy.snapshot()["completed"], 1)
                rows = self.rows(fixture)
                self.assertEqual(sum(row["event"] == "request_transport" for row in rows), 3)
                self.assertEqual(sum(row["event"] == "request_business" for row in rows), 1)
                rejected = [row for row in rows if row["event"] == "request_business_rejected"]
                self.assertEqual(len(rejected), 2)
                raw_rejection = next(row for row in rejected if row["call_id"] == "invalid")
                self.assertEqual(raw_rejection["tool"], tool)
                raw = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
                self.assertEqual(raw_rejection["arguments_sha256"], hashlib.sha256(raw).hexdigest())

    async def test_malformed_raw_envelope_or_transport_irreversibly_blocks_followup(self):
        vectors = ("null_arguments", "array_arguments", "missing_arguments", "nonstring_name",
                   "missing_meta", "array_meta", "invalid_call_id", "mismatched_identity")
        for label in vectors:
            with self.subTest(vector=label):
                fixture = self.fixture()
                async with self.sdk_client(fixture) as client:
                    ready = await self.bootstrap(client)
                    before = len(fixture.api.calls)
                    params = {"name": "memory_status", "arguments": {"session_handle": ready["session_handle"]},
                              "_meta": metadata(self.roots["a"], call_id="raw-invalid")}
                    if label == "null_arguments":
                        params["arguments"] = None
                    elif label == "array_arguments":
                        params["arguments"] = []
                    elif label == "missing_arguments":
                        del params["arguments"]
                    elif label == "nonstring_name":
                        params["name"] = 17
                    elif label == "missing_meta":
                        del params["_meta"]
                    elif label == "array_meta":
                        params["_meta"] = []
                    elif label == "invalid_call_id":
                        params["_meta"]["callId"] = 17
                    else:
                        params["_meta"]["threadId"] = "another-thread"
                    with anyio.fail_after(10):
                        try:
                            raw_result = await client._dispatcher.send_raw_request("tools/call", params)
                            rejected = raw_result.get("isError") is True
                        except MCPError:
                            rejected = True
                    following = await self.call(client, "memory_status",
                                                {"session_handle": ready["session_handle"]}, "following")
                    after = len(fixture.api.calls)
                self.assertTrue(rejected)
                self.assertTrue(following.is_error, "malformed raw transport must latch the scenario")
                self.assertEqual(after, before)
                self.assertEqual(fixture.policy.snapshot()["admitted"], 1)
                self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

    async def test_sdk_params_validation_error_latches_reserved_dispatch_without_business(self):
        fixture = self.fixture()
        async with self.sdk_client(fixture) as client:
            ready = await self.bootstrap(client)
            before = len(fixture.api.calls)
            meta = metadata(self.roots["a"], call_id="sdk-params-error")
            # Identity is valid, but this reserved SDK field cannot be an object.
            meta["progressToken"] = {"invalid": True}
            with anyio.fail_after(10):
                try:
                    raw = await client._dispatcher.send_raw_request("tools/call", {
                        "name": "memory_status", "arguments": {"session_handle": ready["session_handle"]},
                        "_meta": meta})
                    rejected = raw.get("isError") is True
                except MCPError:
                    rejected = True
            following = await self.call(client, "memory_status",
                                        {"session_handle": ready["session_handle"]}, "following")
            after = len(fixture.api.calls)
        self.assertTrue(rejected)
        self.assertTrue(following.is_error, "raised SDK schema errors must freeze the reserved scenario")
        self.assertEqual(after, before)
        self.assertEqual(fixture.policy.snapshot()["admitted"], 1)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
        rejected_rows = [row for row in self.rows(fixture) if row["event"] == "request_business_rejected"]
        self.assertEqual({row["call_id"] for row in rejected_rows}, {"sdk-params-error", "following"})

    async def test_sdk_tool_validation_error_result_latches_without_business(self):
        fixture = self.fixture()
        server = fixture.create_server()
        validator = server._tool_manager.get_tool("memory_status").fn_metadata
        original = validator.validate_arguments

        def broken_validator(arguments):
            original(arguments)
            raise ValueError("synthetic SDK validation failure")

        # Reuse the actual server: the fault is confined to the SDK validator,
        # not the five-tool registration, dispatch, policy, or business service.
        with patch.object(fixture, "create_server", return_value=server):
            async with self.sdk_client(fixture) as client:
                ready = await self.bootstrap(client)
                before = len(fixture.api.calls)
                with patch.object(type(validator), "validate_arguments", autospec=True,
                                  side_effect=lambda self, arguments: broken_validator(arguments)):
                    invalid = await self.call(client, "memory_status",
                                              {"session_handle": ready["session_handle"]}, "sdk-result-error")
                following = await self.call(client, "memory_status",
                                            {"session_handle": ready["session_handle"]}, "following")
                after = len(fixture.api.calls)
        self.assertTrue(invalid.is_error)
        self.assertTrue(following.is_error, "SDK error DTOs without proxy entry must freeze")
        self.assertEqual(after, before)
        self.assertEqual(fixture.policy.snapshot()["admitted"], 1)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")

    async def test_worker_overlap_latches_without_waiting_for_or_deadlocking_business(self):
        fixture = self.fixture()
        entered, release = threading.Event(), threading.Event()
        results = []
        original = fixture.raw_memory.status

        def slow_status(handle):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test worker release deadline")
            return original(handle)

        async with self.sdk_client(fixture) as client:
            ready = await self.bootstrap(client)
            before = len(fixture.api.calls)

            async def first():
                results.append(await self.call(client, "memory_status",
                                               {"session_handle": ready["session_handle"]}, "worker-first"))

            with patch.object(fixture.raw_memory, "status", side_effect=slow_status):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(first)
                    try:
                        with anyio.fail_after(5):
                            worker_entered = await anyio.to_thread.run_sync(entered.wait, 5)
                        # A blocking state lock across the worker operation would deadlock here.
                        with anyio.fail_after(2):
                            during = fixture.policy.snapshot()
                            invalid = await self.call(client, "memory_search", {}, "worker-overlap")
                    finally:
                        release.set()
            after_first = len(fixture.api.calls)
            following = await self.call(client, "memory_status",
                                        {"session_handle": ready["session_handle"]}, "following")
            after_following = len(fixture.api.calls)
        self.assertTrue(worker_entered)
        self.assertEqual(during["admitted"], 2)
        self.assertEqual(during["completed"], 1)
        self.assertTrue(invalid.is_error)
        self.assertTrue(results[0].is_error, "in-flight business must not commit after the overlap latch")
        self.assertTrue(following.is_error)
        # The already-started operation is not rolled back, but no later business runs.
        self.assertGreater(after_first, before)
        self.assertEqual(after_following, after_first)
        self.assertEqual(fixture.policy.snapshot()["admitted"], 2)
        self.assertEqual(fixture.policy.snapshot()["completed"], 1)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")
        rejected = [row for row in self.rows(fixture) if row["event"] == "request_business_rejected"]
        self.assertEqual({row["call_id"] for row in rejected},
                         {"worker-first", "worker-overlap", "following"})

    async def test_invalid_overlap_in_sdk_gap_freezes_reserved_request_before_business(self):
        fixture = self.fixture()
        entered, release = anyio.Event(), anyio.Event()
        results = []

        async def gap(context, call_next):
            if context.method == "tools/call" and context.params["_meta"]["callId"] == "gap-first":
                entered.set()
                await release.wait()
            return await call_next(context)

        async with self.sdk_client(fixture, gap) as client:
            ready = await self.bootstrap(client)
            before = len(fixture.api.calls)

            async def first():
                results.append(await self.call(client, "memory_status",
                                               {"session_handle": ready["session_handle"]}, "gap-first"))

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(first)
                try:
                    with anyio.fail_after(5):
                        await entered.wait()
                    invalid = await self.call(client, "memory_search", {}, "overlap-invalid")
                finally:
                    release.set()
            following = await self.call(client, "memory_status",
                                        {"session_handle": ready["session_handle"]}, "following")
            after = len(fixture.api.calls)
        self.assertTrue(invalid.is_error)
        self.assertTrue(results[0].is_error, "overlapped reservation must not start business")
        self.assertTrue(following.is_error)
        self.assertEqual(after, before)
        self.assertEqual(fixture.policy.snapshot()["admitted"], 1,
                         "SDK reservations are not business admissions")
        self.assertEqual(fixture.policy.snapshot()["completed"], 1)
        self.assertEqual(fixture.policy.snapshot()["phase"], "rejected")


if __name__ == "__main__":
    unittest.main()
