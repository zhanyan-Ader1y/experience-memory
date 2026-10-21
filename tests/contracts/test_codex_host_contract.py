import json
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MODULE = "tests.fixtures.codex_host_server"


def metadata(root, session_id):
    return {
        "sessionId": session_id,
        "threadId": session_id,
        "x-codex-turn-metadata": {
            "session_id": session_id,
            "thread_id": session_id,
            "codex_version": "0.163.0",
            "workspaces": {root: {}},
        },
    }


def body(result):
    return json.loads(result.content[0].text)


class CodexHostSdkContractTests(unittest.IsolatedAsyncioTestCase):
    async def _start_server(self, stack, project, ready_file):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", FIXTURE_MODULE, "--project", project, "--ready-file", str(ready_file)],
            cwd=str(ROOT),
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        client = await stack.enter_async_context(ClientSession(read, write))
        with anyio.fail_after(15):
            initialized = await client.initialize()
        self.assertEqual(initialized.server_info.name, "experience-memory")
        # The explicit ready file is read once after initialize; stdio stays protocol-only.
        ready = json.loads(ready_file.read_text(encoding="utf-8"))
        self.assertEqual(set(ready), {"root", "host_id", "session_a", "session_b",
                                      "handle_a", "handle_b", "record_a", "record_b"})
        return client, ready

    async def test_real_stdio_sdk_preserves_transport_identity_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root_a_file = Path(temp) / "a-ready.json"
            root_b_file = Path(temp) / "b-ready.json"
            async with AsyncExitStack() as stack:
                client_a, a = await self._start_server(stack, "a", root_a_file)
                client_b, b = await self._start_server(stack, "b", root_b_file)
                meta_a = metadata(a["root"], a["session_a"])
                meta_b = metadata(b["root"], b["session_b"])

                status_a = await client_a.call_tool("memory_status", {"session_handle": a["handle_a"]}, meta=meta_a)
                self.assertFalse(status_a.is_error, body(status_a))
                self.assertEqual(body(status_a)["session_id"], a["session_a"])
                self.assertEqual(body(status_a)["host_source"], "codex_stdio_0.163.0")
                read_a = await client_a.call_tool(
                    "memory_read", {"session_handle": a["handle_a"], "record_id": a["record_a"]}, meta=meta_a)
                self.assertFalse(read_a.is_error, body(read_a))
                receipt_a = body(read_a)["receipt_id"]

                same_project_mismatch = await client_a.call_tool(
                    "memory_status", {"session_handle": a["handle_a"]},
                    meta=metadata(a["root"], a["session_b"]))
                self.assertTrue(same_project_mismatch.is_error)
                self.assertEqual(body(same_project_mismatch)["error_code"], "SESSION_INVALID")

                cross_project_mismatch = await client_b.call_tool(
                    "memory_status", {"session_handle": a["handle_a"]}, meta=meta_b)
                self.assertTrue(cross_project_mismatch.is_error)
                self.assertEqual(body(cross_project_mismatch)["error_code"], "SESSION_INVALID")

                foreign_workspace = metadata(a["root"], b["session_b"])
                scope_rejected = await client_b.call_tool(
                    "memory_status", {"session_handle": b["handle_b"]}, meta=foreign_workspace)
                self.assertTrue(scope_rejected.is_error)
                self.assertEqual(body(scope_rejected)["error_code"], "SCOPE_MISMATCH")

                fake_identity_candidate = {
                    "experience_id": "EXP-ARG-IDENTITY-TEST", "body": "synthetic fixture content",
                    "retention_text": "synthetic fixture summary", "status": "active",
                    "sources": ["synthetic-test"], "tags": [],
                    "admission_basis": "controlled test", "deduplication": {"decision": "new", "reason": "test"},
                    "metadata": {"_meta": {"sessionId": a["session_a"]}, "sessionId": a["session_a"],
                                 "cwd": a["root"], "evidence_type": "verified"},
                }
                written = await client_b.call_tool("memory_write", {
                    "session_handle": b["handle_b"], "action": "upsert", "candidate": fake_identity_candidate,
                    "operation_id": "argument-identity-test",
                }, meta=meta_b)
                self.assertFalse(written.is_error, body(written))
                after_argument = await client_b.call_tool(
                    "memory_status", {"session_handle": b["handle_b"]}, meta=meta_b)
                self.assertEqual(body(after_argument)["session_id"], b["session_b"])
                self.assertEqual(body(after_argument)["project_key"], "local:b")

                meta_session_b = metadata(a["root"], a["session_b"])
                receipt_mismatch = await client_a.call_tool("memory_use", {
                    "session_handle": a["handle_b"], "receipt_id": receipt_a, "priority": "P1",
                    "reason": "synthetic cross-session receipt rejection", "operation_id": "foreign-receipt-test",
                }, meta=meta_session_b)
                self.assertTrue(receipt_mismatch.is_error)
                self.assertEqual(body(receipt_mismatch)["error_code"], "SESSION_INVALID")
                status_session_b = await client_a.call_tool(
                    "memory_status", {"session_handle": a["handle_b"]}, meta=meta_session_b)
                self.assertEqual(body(status_session_b)["u_cursor"], 0)

                missing = await client_b.call_tool("memory_status", {"session_handle": b["handle_b"]})
                self.assertTrue(missing.is_error)
                self.assertEqual(body(missing), {"status": "failed", "error_code": "HOST_SOURCE_UNVERIFIED"})
                malformed = await client_b.call_tool("memory_status", {"session_handle": b["handle_b"]},
                                                    meta={"sessionId": "bad"})
                self.assertTrue(malformed.is_error)
                self.assertEqual(body(malformed), {"status": "failed", "error_code": "HOST_SOURCE_UNVERIFIED"})
                recovered = await client_b.call_tool(
                    "memory_status", {"session_handle": b["handle_b"]}, meta=meta_b)
                self.assertFalse(recovered.is_error, body(recovered))
                self.assertEqual(body(recovered)["session_id"], b["session_b"])


if __name__ == "__main__":
    unittest.main()
