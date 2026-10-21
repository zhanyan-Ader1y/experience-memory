"""Local Python fake host. Actual SDK calls; synthetic metadata, never native proof.

Only trusted tests supply this script as a command prefix. Production CLI has no
factory, command-prefix, mode, origin or executable-from-bundle switch.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import AsyncExitStack

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = "experience_memory_c1_wiring"
THREAD = "offline-fake-host-thread"


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def preview_server(bundle, project):
    assignments = {}
    for line in (bundle / (project + "-config.preview.toml")).read_text(encoding="utf-8").splitlines()[2:]:
        key, _, value = line.partition(" = ")
        assignments[key] = json.loads(value)
    return assignments


def probe(bundle, project, mode, argv):
    if "--version" in argv:
        print("codex-cli 0.163.0", flush=True)
        return
    samples = json.loads((ROOT / "tests/fixtures/codex_c1_event_samples.json").read_text(encoding="utf-8"))
    server = preview_server(bundle, project)
    root = (bundle / "projects" / project).resolve()
    config = copy.deepcopy(samples["config_read"]["config"])
    config["mcp_servers"][SERVER] = server
    config["mcp_servers"]["node_repl"] = {"enabled": False}
    config["mcp_servers"]["apifox-2"] = {"enabled": False}
    layer = copy.deepcopy(samples["project_layer"])
    layer["name"]["dotCodexFolder"] = (root / ".codex").as_posix()
    layer["config"]["mcp_servers"][SERVER] = server
    initialize = copy.deepcopy(samples["initialize"])
    initialize["codexHome"] = str(bundle)
    hooks = {"data": [{"cwd": str(root), "errors": [], "hooks": [], "warnings": []}]}
    usage = copy.deepcopy(samples["usage"])
    if mode == "quota_false":
        usage["ordinaryUsageAllowed"] = False
    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        if method == "initialized":
            continue
        if method == "initialize":
            result = initialize
        elif method == "config/read":
            result = {"config": config, "layers": [layer], "origins": {}}
        elif method == "hooks/list":
            result = hooks
        elif method == "account/rateLimits/read":
            result = usage
        else:
            raise SystemExit(19)
        emit({"jsonrpc": "2.0", "id": request["id"], "result": result})


async def execute(bundle, project, mode):
    if any(key.upper() == "MEM0_API_KEY" for key in os.environ):
        raise SystemExit(18)
    if mode == "crash":
        raise SystemExit(17)
    if mode == "a_failed":
        emit({"type": "turn.failed", "error": {"message": "private raw failure secret"}})
        return
    if mode == "unknown_event":
        emit({"type": "future.secret.event", "body": "private raw failure secret"})
        time.sleep(60)
    if mode == "long_stdout":
        sys.stdout.write("X" * 262145)
        sys.stdout.flush()
        time.sleep(60)
    if mode in ("timeout", "child_hang"):
        if mode == "child_hang":
            child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
            (bundle / "fake-pids.json").write_text(json.dumps([os.getpid(), child.pid]), encoding="utf-8")
        time.sleep(60)
    if mode == "stderr_secret":
        print("SYNTHETIC-STDERR-SECRET accountId=private-fake-account", file=sys.stderr, flush=True)
    server = preview_server(bundle, project)
    root = (bundle / "projects" / project).resolve()
    metadata = {"sessionId": THREAD, "threadId": THREAD, "x-codex-turn-metadata": {
        "session_id": THREAD, "thread_id": THREAD, "codex_version": "0.163.0",
        "workspaces": {str(root): {}}}}
    emit({"type": "thread.started", "thread_id": THREAD})
    emit({"type": "turn.started"})
    async with AsyncExitStack() as stack:
        parameters = StdioServerParameters(command=server["command"], args=server["args"], cwd=str(root))
        read, write = await stack.enter_async_context(stdio_client(parameters))
        client = await stack.enter_async_context(ClientSession(read, write))
        await client.initialize()
        ready = None
        for step in range(1, 7 if mode == "sixth" else 6):
            tool = "c1_bootstrap" if step == 1 else "memory_status"
            if mode == "forbidden" and step == 2:
                tool = "memory_search"
            arguments = ({"probe_nonce": json.loads((bundle / "launch-review.json").read_text(encoding="utf-8"))["run_nonce"]}
                         if step == 1 else {"session_handle": ready["prior_session"]["session_handle"] if step == 3
                                           else ready["foreign_project"]["session_handle"] if step == 4
                                           else ready["session_handle"]})
            meta = dict(metadata, callId="offline-metadata-call-" + str(step))
            item = {"id": "offline-exec-item-" + str(step), "type": "mcp_tool_call", "server": SERVER,
                    "tool": tool, "arguments": arguments, "result": None, "error": None, "status": "in_progress"}
            emit({"type": "item.started", "item": item})
            result = await client.call_tool(tool, arguments, meta=meta)
            if step == 1:
                ready = json.loads(result.content[0].text)
            item["status"] = "failed" if result.is_error else "completed"
            item["result"] = {"content": [block.model_dump(mode="json", by_alias=True, exclude_none=True)
                                          for block in result.content],
                              "structured_content": result.structured_content}
            emit({"type": "item.completed", "item": item})
    emit({"type": "item.completed", "item": {"id": "offline-label", "type": "agent_message",
                                                "text": "offline_simulation; C1_WIRING_DONE"}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                               "output_tokens": 1, "reasoning_output_tokens": 0}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--project", choices=("a", "b"), required=True)
    parser.add_argument("--mode", required=True)
    args, argv = parser.parse_known_args()
    if "exec" in argv:
        asyncio.run(execute(args.bundle, args.project, args.mode))
    else:
        probe(args.bundle, args.project, args.mode, argv)


if __name__ == "__main__":
    main()
