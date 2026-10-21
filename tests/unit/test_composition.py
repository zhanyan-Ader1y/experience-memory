import json
from pathlib import Path
import tempfile
import unittest

from experience_memory.bootstrap import build_runtime
from experience_memory.config import Config, load_config
from experience_memory.domain.errors import ErrorCode, MemoryError


class CompositionBoundaryTests(unittest.TestCase):
    def test_config_rejects_unknown_fields_and_wrong_types(self):
        for fields in ({"memories": ["rule"]}, {"search_limit": True}, {"enabled": "false"},
                       {"retention_budget_bytes": 8193}, {"search_limit": 0},
                       {"core_paths": ["../core.md"]}, {"mem0_api_url": "http://example.invalid"},
                       {"request_timeout_seconds": float("nan")}, {"project_scope": "*"}):
            with self.subTest(fields=fields), self.assertRaises(MemoryError) as ctx:
                Config.from_mapping(fields)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_duplicate_config_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"enabled": true, "enabled": false}', encoding="utf-8")
            with self.assertRaises(MemoryError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_windows_root_relative_core_path_is_rejected(self):
        for path in ("\\outside\\core.md", "\\\\server\\share\\core.md", "C:core.md"):
            with self.subTest(path=path):
                with self.assertRaises(MemoryError) as ctx:
                    Config.from_mapping({"core_paths": [path]})
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_missing_host_source_rejects_before_injected_ports(self):
        class MustNotCall:
            def __getattr__(self, name):
                raise AssertionError("skeleton must not do storage, binding or network I/O")

        fake = MustNotCall()
        runtime = build_runtime(Config(), experiences=fake, controls=fake, sessions=fake)
        for operation, args in (
            (runtime.memory.search, ("handle", "query", 5)),
            (runtime.memory.read, ("handle", "record")),
            (runtime.memory.write, ("handle", "upsert", {}, "op-1")),
            (runtime.retention.use, ("handle", "receipt", "P1", "reason", "op-2")),
            (runtime.memory.status, ("handle",)),
        ):
            with self.subTest(operation=operation.__name__), self.assertRaises(MemoryError) as ctx:
                operation(*args)
            self.assertEqual(ctx.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)

    def test_missing_adapter_is_explicit_failure_not_empty_library(self):
        runtime = build_runtime(Config())
        with self.assertRaises(MemoryError) as ctx:
            runtime.experiences.search(None, "query", 5)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_CONFIGURED)

    def test_disabled_runtime_does_not_claim_tools_work(self):
        runtime = build_runtime(Config(enabled=False))
        with self.assertRaises(MemoryError) as ctx:
            runtime.memory.search("handle", "query", 5)
        self.assertEqual(ctx.exception.code, ErrorCode.DISABLED)

    def test_unconfigured_adapters_implement_current_port_failures(self):
        runtime=build_runtime(Config())
        operations=[(runtime.experiences,'exact',(None,'EXP-X')),
                    (runtime.experiences,'retry',(None,'op',{},'fp')),
                    (runtime.experiences,'verify_write_scope',(None,'id')),
                    (runtime.controls,'save',(None,'kind','key',{},'op')),
                    (runtime.controls,'read',(None,'id','kind')),
                    (runtime.controls,'write',(None,'kind','key',{},'op')),
                    (runtime.controls,'find',(None,'kind','key'))]
        for adapter,name,args in operations:
            with self.subTest(name=name):
                with self.assertRaises(MemoryError) as caught:getattr(adapter,name)(*args)
                self.assertEqual(caught.exception.code,ErrorCode.NOT_CONFIGURED)
