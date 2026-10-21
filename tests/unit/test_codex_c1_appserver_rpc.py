"""Strict C1 app-server JSON-RPC classification and bounded peer tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.codex_c1_appserver_rpc import (
    AppServerPeer,
    RpcProtocolError,
    classify_message,
)


APPROVAL_PARAMS = {
    "itemId": "item-1",
    "startedAtMs": 1,
    "threadId": "thread-1",
    "turnId": "turn-1",
}


def frame(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


class MessageClassificationTests(unittest.TestCase):
    def test_server_request_and_client_response_with_same_id_keep_direction(self):
        request = classify_message(
            b'{"id":1,"method":"item/commandExecution/requestApproval",'
            b'"params":{"threadId":"t","turnId":"u","itemId":"i"}}\n'
        )
        response = classify_message(b'{"id":1,"result":{}}\n')
        self.assertEqual(request.kind, "server_request")
        self.assertEqual(response.kind, "response")
        self.assertEqual(request.rpc_id, response.rpc_id)

    def test_accepts_only_exactly_one_bounded_utf8_json_line(self):
        for raw in (
            b'{"id":1,"result":{}}',
            b'{"id":1,"result":{}}\n\n',
            b"\xff\n",
            b'{"id":NaN}\n',
            b'{"id":1,"id":2,"result":{}}\n',
            b"[]\n",
            b'{"jsonrpc":"1.0","method":"x","id":1}\n',
            b'{"jsonrpc":2.0,"method":"x","id":1}\n',
            b'{"id":true,"result":{}}\n',
            b'{"id":"","result":{}}\n',
            b'{"id":1,"result":1e999}\n',
            b'{"id":1,"method":"x","result":{}}\n',
            b'{"id":1,"result":{},"error":{"code":1,"message":"x"}}\n',
        ):
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(RpcProtocolError):
                    classify_message(raw)
        with self.assertRaises(RpcProtocolError):
            classify_message(b" " * (262_144 - 1) + b"\n")
        self.assertEqual(classify_message(b'{"id":1,"result":{}}\r\n').kind, "response")

    def test_sample_fixture_is_complete_synthetic_schema_data(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" /
                              "codex_c1_appserver_samples.json").read_text(encoding="utf-8"))
        self.assertFalse(fixture["provenance"]["native_capture"])
        self.assertEqual(fixture["provenance"]["schema_tag"], "rust-v0.163.0")
        self.assertEqual(len(fixture["examples"]), 3)
        for example in fixture["examples"]:
            class FixtureProcess:
                def __init__(self):
                    self.incoming = [frame(example["request"])]
                    self.writes = []

                def send_line(self, raw):
                    self.writes.append(raw)

                def read_line(self):
                    return self.incoming.pop(0) if self.incoming else None

            process = FixtureProcess()
            peer = AppServerPeer(process)
            request = peer.next_message()
            self.assertEqual(request.kind, "server_request")
            peer.reply_cancel(request)
            self.assertEqual(json.loads(process.writes[0]), example["cancel_response"])
            self.assertEqual(classify_message(frame(example["cancel_response"])).kind, "response")
        self.assertEqual(fixture["examples"][2]["cancel_response"]["result"],
                         {"permissions": {}, "scope": "turn"})

    def test_classifies_notification_and_error_without_conflating_fields(self):
        notification = classify_message(b'{"method":"thread/started","params":{}}\n')
        error = classify_message(b'{"id":"x","error":{"code":-1,"message":"failed"}}\n')
        self.assertEqual(notification.kind, "notification")
        self.assertEqual(error.kind, "error")
        self.assertEqual(error.rpc_id, "x")


class PeerStateTests(unittest.TestCase):
    def setUp(self):
        self.writes = []

        class RecordingProcess:
            def send_line(inner, raw):
                self.writes.append(raw)

            def read_line(inner):
                if not self.incoming:
                    return None
                return self.incoming.pop(0)

        self.incoming = []
        self.process = RecordingProcess()
        self.peer = AppServerPeer(self.process)

    def test_one_pending_client_and_strict_response_matching(self):
        client_id = self.peer.send_request("initialize", {})
        self.assertEqual(client_id, 1)
        with self.assertRaises(RpcProtocolError):
            self.peer.send_request("config/read", {})
        self.incoming.append(frame({"id": 2, "result": {}}))
        with self.assertRaises(RpcProtocolError):
            self.peer.next_message()
        self.assertEqual(self.peer.pending_client_id, 1)
        with self.assertRaises(RpcProtocolError):
            self.peer.next_message()
        self.assertEqual(self.peer.pending_client_id, 1)

    def test_client_methods_are_allowlisted_and_initialized_is_fixed(self):
        for method in ("arbitrary/method", "item/commandExecution/requestApproval", "turn/steer"):
            with self.subTest(method=method):
                with self.assertRaises(RpcProtocolError):
                    self.peer.send_request(method, {})
        self.peer.send_initialized()
        self.assertEqual(self.writes, [b'{"method":"initialized"}\n'])

    def test_server_cancel_does_not_consume_same_value_client_id(self):
        client_id = self.peer.send_request("initialize", {})
        approval = {"id": 1, "method": "item/commandExecution/requestApproval",
                    "params": {**APPROVAL_PARAMS}}
        self.incoming.extend((frame(approval), frame({"method": "thread/started", "params": {}}),
                              frame({"id": 1, "result": {}})))
        request = self.peer.next_message()
        self.assertEqual(request.kind, "server_request")
        self.peer.reply_cancel(request)
        self.assertEqual(self.peer.pending_client_id, client_id)
        self.assertEqual(json.loads(self.writes[-1]), {
            "id": 1, "result": {"decision": "cancel"},
        })
        self.assertEqual(self.peer.next_message().kind, "notification")
        self.assertEqual(self.peer.next_message().kind, "response")
        self.assertIsNone(self.peer.pending_client_id)

    def test_replayed_client_response_and_invalid_approval_fail_closed(self):
        peer = AppServerPeer(self.process)
        peer.process = self.process
        self.incoming = [frame({"id": 1, "result": {}}), frame({"id": 1, "result": {}})]
        peer.send_request("initialize", {})
        self.assertEqual(peer.next_message().kind, "response")
        with self.assertRaises(RpcProtocolError):
            peer.next_message()

        peer = AppServerPeer(self.process)
        peer.process = self.process
        self.incoming = [frame({"id": 11, "method": "item/commandExecution/requestApproval",
                                "params": {"itemId": "i"}})]
        request = peer.next_message()
        with self.assertRaises(RpcProtocolError):
            peer.reply_cancel(request)
        with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_CANCEL_SCHEMA_INVALID"):
            peer.next_message()
        self.assertEqual(json.loads(self.writes[-1])["method"], "initialize")

    def test_error_notification_poison_is_static_and_repeatable(self):
        self.incoming = [frame({"method": "error", "params": {
            "error": {"message": "private server detail"}, "threadId": "t",
            "turnId": "u", "willRetry": False,
        }})]
        with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_REMOTE_ERROR") as caught:
            self.peer.next_message()
        self.assertNotIn("private server detail", str(caught.exception))
        self.assertEqual(self.peer.failure_code, "C1_RPC_REMOTE_ERROR")
        with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_REMOTE_ERROR"):
            self.peer.next_message()

    def test_server_resolution_correlates_server_id_and_thread_only(self):
        client_id = self.peer.send_request("initialize", {})
        request = {"id": 1, "method": "item/commandExecution/requestApproval",
                   "params": {**APPROVAL_PARAMS}}
        resolved = {"method": "serverRequest/resolved", "params": {
            "requestId": 1, "threadId": "thread-1",
        }}
        self.incoming.extend((frame(request), frame(resolved), frame(resolved),
                              frame({"id": 1, "result": {}})))
        server_request = self.peer.next_message()
        self.peer.reply_cancel(server_request)
        self.assertEqual(self.peer.next_message().method, "serverRequest/resolved")
        self.assertEqual(self.peer.pending_client_id, client_id)
        with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_SERVER_RESOLUTION_INVALID"):
            self.peer.next_message()
        self.assertEqual(self.peer.pending_client_id, client_id)

    def test_server_resolution_rejects_unseen_wrong_type_and_wrong_thread_ids(self):
        invalid_params = (
            {"requestId": 2, "threadId": "thread-1"},
            {"requestId": "1", "threadId": "thread-1"},
            {"requestId": 1, "threadId": "other-thread"},
        )
        for params in invalid_params:
            peer = AppServerPeer(self.process)
            self.incoming = [frame({"id": 1, "method": "item/fileChange/requestApproval",
                                    "params": {**APPROVAL_PARAMS}}),
                             frame({"method": "serverRequest/resolved", "params": params})]
            peer.next_message()
            with self.subTest(params=params):
                with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_SERVER_RESOLUTION_INVALID"):
                    peer.next_message()

        peer = AppServerPeer(self.process)
        peer.send_request("initialize", {})
        self.incoming = [frame({"method": "serverRequest/resolved", "params": {
            "requestId": 1, "threadId": "thread-1",
        }})]
        with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_SERVER_RESOLUTION_INVALID"):
            peer.next_message()
        self.assertEqual(peer.pending_client_id, 1)

    def test_cancel_validates_all_optional_and_nested_approval_schema_fields(self):
        full_cases = (
            ("item/commandExecution/requestApproval", {
                **APPROVAL_PARAMS, "approvalId": None, "availableDecisions": ["cancel"],
                "command": "echo synthetic", "commandActions": [{"type": "read", "command": "read",
                    "name": "sample", "path": "C:/synthetic/sample"}], "cwd": "C:/synthetic/root",
                "environmentId": None, "kind": "command",
                "networkApprovalContext": {"host": "example.invalid", "protocol": "https"},
                "proposedExecpolicyAmendment": ["synthetic-policy"],
                "proposedNetworkPolicyAmendments": [{"action": "deny", "host": "example.invalid"}],
                "reason": None, "additionalPermissions": None,
            }),
            ("item/fileChange/requestApproval", {**APPROVAL_PARAMS, "reason": None, "grantRoot": None}),
            ("item/permissions/requestApproval", {
                **APPROVAL_PARAMS, "cwd": "C:/synthetic/root", "environmentId": None, "reason": None,
                "permissions": {"fileSystem": {
                    "entries": [{"access": "read", "path": {"type": "path", "path": "C:/synthetic/a"}},
                                {"access": "write", "path": {"type": "glob_pattern", "pattern": "*.txt"}},
                                {"access": "deny", "path": {"type": "special", "value": {"kind": "root"}}}],
                    "globScanMaxDepth": 1, "read": ["C:/synthetic/read"], "write": None,
                }, "network": {"enabled": False}},
            }),
        )
        for method, params in full_cases:
            peer = AppServerPeer(self.process)
            self.incoming = [frame({"id": 41, "method": method, "params": params})]
            request = peer.next_message()
            with self.subTest(method=method):
                peer.reply_cancel(request)
                self.assertEqual(json.loads(self.writes[-1])["id"], 41)

        invalid_cases = (
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS, "command": 5}),
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS,
                "commandActions": [{"type": "read", "command": "read"}]}),
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS,
                "availableDecisions": [{"acceptWithExecpolicyAmendment": {}}]}),
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS,
                "networkApprovalContext": {"host": "example.invalid", "protocol": "ftp"}}),
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS,
                "proposedNetworkPolicyAmendments": [{"action": "allow"}]}),
            ("item/fileChange/requestApproval", {**APPROVAL_PARAMS, "grantRoot": 5}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": ".",
                "permissions": {"unexpected": True}}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": ".",
                "permissions": {"network": {"enabled": "yes"}}}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": ".",
                "permissions": {"fileSystem": {"globScanMaxDepth": True}}}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": ".",
                "permissions": {"fileSystem": {"entries": [{"access": "read",
                    "path": {"type": "special", "value": {"kind": "unknown"}}}]}}}),
        )
        for method, params in invalid_cases:
            peer = AppServerPeer(self.process)
            self.writes.clear()
            self.incoming = [frame({"id": 42, "method": method, "params": params})]
            request = peer.next_message()
            with self.subTest(method=method, params=params):
                with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_CANCEL_SCHEMA_INVALID"):
                    peer.reply_cancel(request)
                self.assertEqual(self.writes, [])
                with self.assertRaisesRegex(RpcProtocolError, "C1_RPC_CANCEL_SCHEMA_INVALID"):
                    peer.next_message()

    def test_server_ids_reject_duplicates_and_unknown_or_unsupported_requests_fail_closed(self):
        self.incoming.extend((frame({"id": "s", "method": "item/fileChange/requestApproval",
                                     "params": {**APPROVAL_PARAMS}}),
                              frame({"id": "s", "method": "item/fileChange/requestApproval",
                                     "params": {**APPROVAL_PARAMS}})))
        first = self.peer.next_message()
        with self.assertRaises(RpcProtocolError):
            self.peer.next_message()
        with self.assertRaises(RpcProtocolError):
            self.peer.reply_cancel(first)  # Duplicate poisoned the stream; no guessed callback.

        for method in ("attestation/generate", "mcpServer/elicitation/request",
                       "item/tool/call", "account/login"):
            peer = AppServerPeer(self.process)
            peer.process = self.process
            self.incoming = [frame({"id": 9, "method": method, "params": {}})]
            with self.subTest(method=method):
                with self.assertRaises(RpcProtocolError):
                    peer.next_message()

    def test_cancel_requires_complete_known_approval_schema_and_is_one_shot(self):
        for method, params, result in (
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS}, {"decision": "cancel"}),
            ("item/fileChange/requestApproval", {**APPROVAL_PARAMS}, {"decision": "cancel"}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": ".", "permissions": {}},
             {"permissions": {}, "scope": "turn"}),
        ):
            with self.subTest(method=method):
                peer = AppServerPeer(self.process)
                peer.process = self.process
                self.incoming = [frame({"id": 7, "method": method, "params": params})]
                request = peer.next_message()
                peer.reply_cancel(request)
                self.assertEqual(json.loads(self.writes[-1]), {"id": 7, "result": result})
                with self.assertRaises(RpcProtocolError):
                    peer.reply_cancel(request)

        for method, params in (
            ("item/commandExecution/requestApproval", {**APPROVAL_PARAMS, "startedAtMs": True}),
            ("item/fileChange/requestApproval", {"itemId": "i"}),
            ("item/permissions/requestApproval", {**APPROVAL_PARAMS, "cwd": "."}),
        ):
            peer = AppServerPeer(self.process)
            peer.process = self.process
            self.incoming = [frame({"id": 8, "method": method, "params": params})]
            request = peer.next_message()
            with self.assertRaises(RpcProtocolError):
                peer.reply_cancel(request)
            self.assertNotIn(b"accept", b"".join(self.writes))


@unittest.skipUnless(sys.platform == "win32", "requires Task1 Windows Job-owned child pipes")
class OwnedProcessPipeIntegrationTests(unittest.TestCase):
    def test_interleaved_server_request_notification_and_response_use_real_pipe(self):
        from scripts.codex_c1_process import OwnedProcess

        child = r'''
import json, sys
request = json.loads(sys.stdin.buffer.readline())
assert request["id"] == 1 and request["method"] == "initialize"
params = {"itemId":"item-1","startedAtMs":1,"threadId":"thread-1","turnId":"turn-1"}
messages = [
 {"id":1,"method":"item/commandExecution/requestApproval","params":params},
 {"method":"thread/started","params":{}}
]
for message in messages:
 sys.stdout.buffer.write(json.dumps(message,separators=(",", ":")).encode()+b"\n")
 sys.stdout.buffer.flush()
cancel = json.loads(sys.stdin.buffer.readline())
assert cancel == {"id":1,"result":{"decision":"cancel"}}
sys.stdout.buffer.write(b'{"id":1,"result":{}}\n')
sys.stdout.buffer.flush()
'''
        with tempfile.TemporaryDirectory() as temp:
            process = OwnedProcess([sys._base_executable, "-I", "-c", child], cwd=Path(temp),
                                   env=dict(os.environ), deadline=time.monotonic() + 8,
                                   stdin_mode="pipe")
            peer = AppServerPeer(process)
            try:
                self.assertEqual(peer.send_request("initialize", {}), 1)
                approval = peer.next_message()
                self.assertEqual((approval.kind, approval.rpc_id), ("server_request", 1))
                peer.reply_cancel(approval)
                notification = peer.next_message()
                self.assertEqual((notification.kind, notification.method), ("notification", "thread/started"))
                response = peer.next_message()
                self.assertEqual((response.kind, response.rpc_id), ("response", 1))
                self.assertIsNone(peer.pending_client_id)
                process.close_input()
                self.assertIsNone(process.read_line())
                self.assertEqual(process.wait_for_exit(), 0)
            finally:
                cleanup = process.stop()
            self.assertEqual(cleanup["reason"], "job_empty")
            self.assertTrue(process.shutdown_state()["io_threads_stopped"])


if __name__ == "__main__":
    unittest.main()
