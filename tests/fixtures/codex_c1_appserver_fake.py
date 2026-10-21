"""Literal offline app-server double; real MCP SDK and bounded fixture, never Codex.

Only trusted programmatic tests launch this script in a new synthetic bundle.
Wire/result bodies remain in memory. The observation file stores only method
names, booleans, counts and local PIDs, not requests, prompts, handles or results.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from scripts import codex_c1_bundle as bundles
from tests.fixtures.codex_c1_fake_host import preview_server

SERVER = "experience_memory_c1_wiring"
THREAD, TURN = "synthetic-thread", "synthetic-turn"


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def notify(method, params):
    emit({"method": method, "params": params})


class FakeAppServer:
    def __init__(self, bundle, project, mode):
        self.bundle, self.project, self.mode = bundle, project, mode
        self.snapshot = bundles.load_snapshot(bundle)
        receipt = bundles._read_json(bundle / "install-receipt.json")
        bundles.verify_snapshot(self.snapshot, phase="installed", receipt=receipt)
        approval = bundles._read_json(bundle / "approval.json")
        bundles.validate_approval(self.snapshot, approval, "inspect", project)
        if approval["scope"] != "offline-simulation" or any(
                key.upper() == "MEM0_API_KEY" for key in os.environ):
            raise SystemExit(18)
        self.root = bundle / "projects" / project
        self.server = preview_server(bundle, project)
        self.samples = json.loads((ROOT / "tests/fixtures/codex_c1_appserver_samples.json").read_text(
            "utf-8").replace("__SYNTHETIC_ROOT__", self.root.as_posix()))
        old = json.loads((ROOT / "tests/fixtures/codex_c1_event_samples.json").read_text("utf-8"))
        self.config = copy.deepcopy(old["config_read"]["config"])
        self.config.update(model="synthetic-model", model_provider="synthetic-provider")
        self.config["mcp_servers"][SERVER] = self.server
        self.config["mcp_servers"]["node_repl"] = {"enabled": False}
        self.config["mcp_servers"]["apifox-2"] = {"enabled": False}
        self.layer = copy.deepcopy(old["project_layer"])
        self.layer["name"]["dotCodexFolder"] = (self.root / ".codex").as_posix()
        self.layer["config"]["mcp_servers"][SERVER] = self.server
        self.initialize = copy.deepcopy(old["initialize"])
        self.initialize["codexHome"] = str(bundle)
        self.usage = copy.deepcopy(old["usage"])
        self.observed = {"offline_simulation": True, "pid": os.getpid(), "child_pids": [],
                         "methods": [], "callback_results": [], "reservation_before_thread": False,
                         "thread_count": 0, "turn_count": 0, "sdk_calls": 0, "thread_notice_repeats": 0,
                         "input_eof_observed": False, "interrupt_observed": False}
        self.save()

    def save(self):
        (self.bundle / "fake-appserver-observations.json").write_text(
            json.dumps(self.observed, sort_keys=True), encoding="utf-8")

    def reply(self, request, result):
        emit({"id": request["id"], "result": result})

    def thread(self, request):
        expected = {"schema_version": 1, "project": self.project, "attempt": 1,
                    "state": "reserved", "run_nonce": self.snapshot.run_nonce,
                    "manifest_sha256": self.snapshot.manifest_sha256}
        path = self.bundle / "attempts" / (self.project + "-reservation.json")
        self.observed["reservation_before_thread"] = path.is_file() and bundles._read_json(path) == expected
        self.observed["thread_count"] += 1
        self.save()
        if not self.observed["reservation_before_thread"] or self.observed["thread_count"] != 1:
            raise SystemExit(23)
        params = request["params"]
        expected_params = {"cwd": str(self.root), "model": "synthetic-model",
                           "modelProvider": "synthetic-provider", "approvalPolicy": "on-request",
                           "approvalsReviewer": "user", "sandbox": "read-only", "ephemeral": True,
                           "allowProviderModelFallback": False, "experimentalRawEvents": False}
        if params != expected_params:
            raise SystemExit(24)
        if self.mode == "thread_error":
            emit({"id": request["id"], "error": {"code": -32000, "message": "private-thread-secret"}})
            return
        response = copy.deepcopy(self.samples["session"]["thread_start_response"])
        mutations = {"wrong_policy": ("approvalPolicy", "never"),
                     "wrong_reviewer": ("approvalsReviewer", "auto_review"),
                     "wrong_model": ("model", "other-model"),
                     "wrong_provider": ("modelProvider", "other-provider"),
                     "wrong_root": ("cwd", str(self.bundle / "projects" / ("b" if self.project == "a" else "a"))),
                     "bad_disabled_plugins": ("disabledPluginIds", False),
                     "bad_multi_agent_mode": ("multiAgentMode", []),
                     "bad_service_tier": ("serviceTier", 0),
                     "bad_reasoning_effort": ("reasoningEffort", {}),
                     "bad_instruction_sources": ("instructionSources", 42),
                     "bad_runtime_roots": ("runtimeWorkspaceRoots", [False])}
        if self.mode in mutations:
            key, value = mutations[self.mode]
            response[key] = value
        if self.mode == "wrong_network":
            response["sandbox"]["networkAccess"] = True
        if self.mode == "unknown_source":
            response["thread"]["source"] = "unknown"
        if self.mode == "response_optionals_omitted":
            for field in ("disabledPluginIds", "multiAgentMode", "serviceTier", "reasoningEffort",
                          "instructionSources", "runtimeWorkspaceRoots", "activePermissionProfile"):
                response.pop(field, None)
            del response["sandbox"]["networkAccess"]
        if self.mode == "response_optionals_null_defaults":
            response.update(disabledPluginIds=[], multiAgentMode="explicitRequestOnly", serviceTier=None,
                            reasoningEffort=None, instructionSources=[], runtimeWorkspaceRoots=[],
                            activePermissionProfile=None)
        if self.mode == "response_optionals_custom_extras":
            response.update(disabledPluginIds=["", "synthetic-plugin"], multiAgentMode={"custom": ""},
                            serviceTier="", reasoningEffort="synthetic-effort",
                            instructionSources=["", "private-synthetic-instruction-source"],
                            runtimeWorkspaceRoots=[str(self.root)],
                            activePermissionProfile={"id": "", "extends": None, "schemaPermittedExtra": {}},
                            schemaPermittedExtra={"opaque": True}, effort=42, personality=[], summary=False)
        self.thread_notice = {"method": "thread/started", "params": {"thread": copy.deepcopy(response["thread"])}}
        if self.mode == "thread_notice_first":
            emit(self.thread_notice)
        self.reply(request, response)
        if self.mode != "thread_notice_first":
            emit(self.thread_notice)

    def repeat_thread_notice(self, stage):
        modes = {"thread_duplicate_" + stage, "thread_conflict_" + stage,
                 "thread_foreign_" + stage, "thread_additional_" + stage}
        if self.mode not in modes and self.mode != "thread_duplicate_then_late_error":
            return
        notice = copy.deepcopy(self.thread_notice)
        thread = notice["params"]["thread"]
        if self.mode == "thread_conflict_" + stage:
            thread["updatedAt"] += 1
        elif self.mode == "thread_foreign_" + stage:
            thread["id"] = "foreign-thread"
        elif self.mode == "thread_additional_" + stage:
            thread.update(id="additional-thread", sessionId="additional-session")
        emit(notice)
        self.observed["thread_notice_repeats"] += 1
        self.save()

    def approval(self, request):
        index = 2 if self.mode == "permissions" else 1 if self.mode == "file_approval" else 0
        message = copy.deepcopy(self.samples["examples"][index]["request"])
        # Same-valued client and server IDs deliberately exercise independent spaces.
        message["id"] = request["id"]
        message["params"]["cwd"] = str(self.root)
        emit(message)

    async def business(self, request):
        self.observed["turn_count"] += 1
        self.save()
        if self.observed["thread_count"] != 1 or self.observed["turn_count"] != 1:
            raise SystemExit(25)
        expected_params = {"threadId": THREAD,
                           "input": [{"type": "text", "text": self.snapshot.manifest["projects"][self.project]["prompt"]}],
                           "approvalPolicy": "on-request", "approvalsReviewer": "user",
                           "sandboxPolicy": {"type": "readOnly", "networkAccess": False}}
        if request["params"] != expected_params:
            raise SystemExit(26)
        if self.mode == "blocked":
            time.sleep(60)
        early = self.mode in ("early_items", "early_partial_foreign", "all_before_response", "pending_approval")
        if not early:
            self.reply(request, copy.deepcopy(self.samples["session"]["turn_start_response"]))
        notify("turn/started", copy.deepcopy(self.samples["session"]["turn_started"]))
        if self.mode in ("approval", "pending_approval", "permissions", "file_approval"):
            self.approval(request)
            return
        if self.mode == "unknown_interaction":
            emit({"id": request["id"], "method": "attestation/request", "params": {"secret": "private-attestation-secret"}})
            return
        metadata = {"sessionId": THREAD, "threadId": THREAD, "x-codex-turn-metadata": {
            "session_id": THREAD, "thread_id": THREAD, "codex_version": "0.163.0",
            "workspaces": {str(self.root): {}}}}
        ready, completed_items = None, []
        async with AsyncExitStack() as stack:
            parameters = StdioServerParameters(command=self.server["command"], args=self.server["args"], cwd=str(self.root))
            read, write = await stack.enter_async_context(stdio_client(parameters))
            client = await stack.enter_async_context(ClientSession(read, write))
            await client.initialize()
            for step in range(1, 6):
                tool = "c1_bootstrap" if step == 1 else "memory_status"
                arguments = ({"probe_nonce": self.snapshot.run_nonce} if step == 1 else {"session_handle": (
                    ready["prior_session"]["session_handle"] if step == 3 else
                    ready["foreign_project"]["session_handle"] if step == 4 else ready["session_handle"])})
                item = {"type": "mcpToolCall", "id": "offline-v2-item-" + str(step), "server": SERVER,
                        "tool": tool, "arguments": arguments, "status": "inProgress", "result": None, "error": None}
                started_item = copy.deepcopy(item)
                if self.mode == "unknown_item" and step == 1:
                    started_item["type"] = "futureUnknownTool"
                notify("item/started", {"threadId": THREAD, "turnId": TURN,
                                        "startedAtMs": int(time.time() * 1000), "item": started_item})
                result = await client.call_tool(tool, arguments, meta=dict(metadata, callId="offline-mcp-call-" + str(step)))
                self.observed["sdk_calls"] += 1
                self.save()
                if step == 1:
                    ready = json.loads(result.content[0].text)
                item.update(status="failed" if result.is_error else "completed", result={
                    "content": [block.model_dump(mode="json", by_alias=True, exclude_none=True) for block in result.content],
                    "structuredContent": result.structured_content})
                if self.mode in ("partial_foreign_scope", "early_partial_foreign") and step == 1:
                    body = json.loads(item["result"]["content"][0]["text"])
                    body.pop("session_handle")
                    body["session_id"] = "independently-foreign-owner"
                    item["result"] = {"structuredContent": body}
                if self.mode == "missing_result" and step == 1:
                    item.pop("result")
                if not (self.mode == "missing_finish" and step == 1):
                    notify("item/completed", {"threadId": THREAD, "turnId": TURN,
                                              "completedAtMs": int(time.time() * 1000), "item": copy.deepcopy(item)})
                completed_items.append(item)
                if step == 1:
                    self.repeat_thread_notice("collecting")
                if self.mode == "premature_end" and step == 1:
                    notify("turn/completed", copy.deepcopy(self.samples["session"]["turn_completed"]))
                    return
                if early and self.mode != "all_before_response" and step == 1:
                    self.reply(request, copy.deepcopy(self.samples["session"]["turn_start_response"]))
        agent = {"type": "agentMessage", "id": "offline-final-agent", "text": "private-model-secret C1_WIRING_DONE"}
        notify("item/started", {"threadId": THREAD, "turnId": TURN, "startedAtMs": int(time.time() * 1000),
                                "item": copy.deepcopy(agent)})
        notify("item/agentMessage/delta", {"threadId": THREAD, "turnId": TURN, "itemId": agent["id"],
                                          "delta": "private-delta-secret"})
        notify("item/completed", {"threadId": THREAD, "turnId": TURN, "completedAtMs": int(time.time() * 1000),
                                  "item": agent})
        end = copy.deepcopy(self.samples["session"]["turn_completed"])
        if self.mode == "full_completion":
            end["turn"].update(itemsView="full", items=completed_items + [agent])
        notify("turn/completed", end)
        self.repeat_thread_notice("after_completion")
        if self.mode == "all_before_response":
            self.reply(request, copy.deepcopy(self.samples["session"]["turn_start_response"]))
        if self.mode in ("late_error", "thread_duplicate_then_late_error"):
            notify("error", {"threadId": THREAD, "turnId": TURN,
                             "error": {"message": "private-late-error-secret"}, "willRetry": False})
        if self.mode == "late_unknown":
            emit({"id": "late-interaction", "method": "attestation/request", "params": {}})
        if self.mode == "eof_no_exit":
            os.close(sys.stdout.fileno())
            os.close(sys.stderr.fileno())
            time.sleep(60)
        if self.mode == "sleepchild":
            child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.observed["child_pids"].append(child.pid)
            self.save()

    def run(self):
        for raw in sys.stdin:
            request = json.loads(raw)
            if "method" not in request:
                result = request.get("result")
                self.observed["callback_results"].append(result)
                self.save()
                if result not in ({"decision": "cancel"}, {"permissions": {}, "scope": "turn"}):
                    raise SystemExit(27)
                continue
            method = request["method"]
            self.observed["methods"].append(method)
            self.save()
            if method == "initialize":
                if self.mode == "preflight_blocked":
                    time.sleep(60)
                self.reply(request, self.initialize)
            elif method == "initialized":
                continue
            elif method == "config/read":
                self.reply(request, {"config": self.config, "layers": [self.layer], "origins": {}})
            elif method == "hooks/list":
                self.reply(request, {"data": [{"cwd": str(self.root), "errors": [], "hooks": [], "warnings": []}]})
            elif method == "account/rateLimits/read":
                if self.mode == "usage_denied":
                    self.usage["ordinaryUsageAllowed"] = False
                self.reply(request, self.usage)
            elif method == "thread/start":
                self.thread(request)
            elif method == "turn/start":
                asyncio.run(self.business(request))
            elif method == "turn/interrupt":
                self.observed["interrupt_observed"] = True
                self.save()
                self.reply(request, {})
            else:
                raise SystemExit(28)
        self.observed["input_eof_observed"] = True
        self.save()
        if self.mode == "close_hang":
            time.sleep(60)
        if self.mode == "nonzero":
            raise SystemExit(7)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--project", required=True, choices=("a", "b"))
    parser.add_argument("--mode", default="success")
    args = parser.parse_args()
    FakeAppServer(args.bundle, args.project, args.mode).run()


if __name__ == "__main__":
    main()
