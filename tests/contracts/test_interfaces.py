import json
import os
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest

from experience_memory.bootstrap import build_runtime
from experience_memory.config import Config
from experience_memory.interfaces.hooks import handle_event
from experience_memory.interfaces.mcp_server import create_server

ROOT = Path(__file__).resolve().parents[2]


class HookContractTests(unittest.TestCase):
    def event(self, name="SessionStart", **overrides):
        event = {"hook_event_name": name, "session_id": "s-1", "cwd": str(ROOT)}
        event.update({"source": "compact"} if name == "SessionStart" else {"trigger": "manual"})
        return dict(event, **overrides)

    def test_unimplemented_hook_stops_without_injecting_formal_context(self):
        service = build_runtime(Config()).retention
        for name in ("SessionStart", "PreCompact"):
            output = handle_event(service, self.event(name))
            with self.subTest(event=name):
                self.assertIs(output["continue"], False)
                self.assertIn("NOT_CONFIGURED", output["stopReason"])
                self.assertNotIn("hookSpecificOutput", output)

    def test_disabled_hook_passes_without_restoration_claim(self):
        service = build_runtime(Config(enabled=False)).retention
        self.assertEqual(handle_event(service, self.event()), {"continue": True, "suppressOutput": True})

    def test_malformed_events_are_blocked(self):
        service = build_runtime(Config()).retention
        for event in ({}, [], self.event(source="unknown"), self.event(session_id=True),
                      self.event("PostCompact"), self.event("PreCompact", trigger="unknown")):
            with self.subTest(event=event):
                output = handle_event(service, event)
                self.assertIs(output["continue"], False)
                self.assertIn("INVALID_INPUT", output["stopReason"])

    def test_cli_uses_protocol_only_stdout_for_bad_input_and_configured_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)/'project';project.mkdir()
            subprocess.run(['git','init','-q',str(project)],check=True,capture_output=True)
            event=self.event(cwd=str(project))
            cases=[('not json','INVALID_INPUT'),(json.dumps(event),'SCOPE_UNRESOLVED')]
            env=dict(os.environ,PLUGIN_DATA=str(Path(directory)/'control'))
            for data,code in cases:
                self.assert_cli_failure(data,code,env)
            (project/'.experience-memory.json').write_text(
                json.dumps({'project_scope':'local:isolated-cli-contract'}),encoding='utf-8')
            self.assert_cli_failure(json.dumps(event),'SESSION_INVALID',env)

    def assert_cli_failure(self,data,code,env):
        with self.subTest(code=code):
            result=subprocess.run([sys.executable,str(ROOT/'hooks/adapter.py')],input=data,
                capture_output=True,text=True,encoding='utf-8',timeout=10,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            output=json.loads(result.stdout)
            self.assertIs(output['continue'],False)
            self.assertIn(code,output['stopReason'])
            self.assertNotIn('hookSpecificOutput',output)
            self.assertEqual(result.stderr,'')


class MCPContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = build_runtime(Config())
        self.server = create_server(runtime.memory, runtime.retention, search_limit=5)

    async def test_lists_five_tools_with_explicit_session_binding_input(self):
        tools = await self.server.list_tools()
        self.assertEqual({tool.name for tool in tools},
                         {"memory_search", "memory_read", "memory_write", "memory_use", "memory_status"})
        for tool in tools:
            self.assertIn("session_handle", tool.input_schema["required"])
        use = next(tool for tool in tools if tool.name == "memory_use")
        self.assertEqual(use.input_schema["properties"]["priority"]["enum"], ["P1", "P2", "P3"])

    async def test_tools_return_explicit_errors_not_success_payloads(self):
        for name, arguments in (
            ("memory_search", {"session_handle": "h", "query": "idempotency"}),
            ("memory_read", {"session_handle": "h", "record_id": "r"}),
            ("memory_write", {"session_handle": "h", "action": "upsert", "candidate": {}, "operation_id": "op"}),
            ("memory_use", {"session_handle": "h", "receipt_id": "rec", "priority": "P1", "reason": "adopted", "operation_id": "op"}),
            ("memory_status", {"session_handle": "h"}),
        ):
            with self.subTest(name=name):
                result = await self.server.call_tool(name, arguments)
                self.assertIs(result.is_error, True)
                body = json.loads(result.content[0].text)
                self.assertEqual(body, {"status": "failed", "error_code": "HOST_SOURCE_UNVERIFIED"})

    async def test_real_stdio_process_initializes_and_answers_tools(self):
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts/launch.py"), "mcp"])
        with anyio.fail_after(15):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.server_info.name, "experience-memory")
                    self.assertEqual(len((await client.list_tools()).tools), 5)
                    result = await client.call_tool("memory_search", {"session_handle": "h", "query": "query"})
                    self.assertIs(result.is_error, True)
                    self.assertIn("HOST_SOURCE_UNVERIFIED", result.content[0].text)
