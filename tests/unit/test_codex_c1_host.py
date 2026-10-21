import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import codex_c1_bundle as bundle_api
from scripts import prepare_codex_c1_review as review_exporter
from scripts.codex_c1_host import ReadOnlyProbe, validate_preflight


SAMPLES_PATH = ROOT / "tests/fixtures/codex_c1_event_samples.json"
SERVER_NAME = "experience_memory_c1_wiring"
NONCE = "c1-host-unit-20261019"


def read_preview_server(preview_bytes):
    lines = preview_bytes.decode("utf-8").splitlines()
    if (len(lines) != 7 or lines[0] != "# REVIEW ONLY: not installed; requires separate native authorization and preflight."
            or lines[1] != "[mcp_servers.experience_memory_c1_wiring]"):
        raise AssertionError("unexpected generated preview shape")
    values = {}
    for line in lines[2:]:
        key, separator, value = line.partition(" = ")
        if not separator or key in values:
            raise AssertionError("unexpected generated preview assignment")
        values[key] = value
    return {
        "command": json.loads(values["command"]),
        "args": json.loads(values["args"]),
        "enabled": values["enabled"] == "true",
        "startup_timeout_sec": int(values["startup_timeout_sec"]),
        "tool_timeout_sec": int(values["tool_timeout_sec"]),
    }


FAKE_HOST = r'''
import json
import os
import sys
import time

mode = sys.argv[1]
payload = json.loads(sys.argv[2])
audit_path = sys.argv[3]
if "--version" in sys.argv[4:]:
    print("codex-cli 0.163.0", flush=True)
    raise SystemExit(0)
if mode == "stderr_secret":
    print("MEM0_API_KEY=synthetic-secret-must-not-escape", file=sys.stderr, flush=True)
if mode == "credential_guard" and "MEM0_API_KEY" in os.environ:
    raise SystemExit(9)
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    with open(audit_path, "a", encoding="utf-8") as audit:
        audit.write(method + "\n")
    if method == "initialized":
        continue
    if mode == "unexpected_notification" and method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "method": "thread/started", "params": {}}), flush=True)
    if mode == "timeout":
        time.sleep(8)
    if mode == "duplicate_key":
        sys.stdout.write('{"jsonrpc":"2.0","id":1,"id":1,"result":{}}\n')
        sys.stdout.flush()
        continue
    if mode == "long_line" and method == "config/read":
        sys.stdout.write("x" * 262145 + "\n")
        sys.stdout.flush()
        continue
    if mode == "malformed_initialize" and method == "initialize":
        result = {}
    elif method == "initialize":
        result = payload["initialize"]
    elif method == "config/read":
        result = {"config": payload["config"], "layers": payload["layers"], "origins": {}}
    elif method == "hooks/list":
        result = payload["hooks"]
    elif method == "account/rateLimits/read":
        result = {"ordinaryUsageAllowed": True} if mode == "missing_usage_limits" else payload["usage"]
    else:
        raise SystemExit("unexpected request")
    response_id = request["id"] + 100 if mode == "wrong_id" else request["id"]
    print(json.dumps({"jsonrpc": "2.0", "id": response_id, "result": result}), flush=True)
'''


class CodexC1HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = self.directory / "synthetic-bundle"
        review_exporter.prepare(self.bundle, NONCE)
        self.snapshot = bundle_api.load_snapshot(self.bundle)
        self.project_root = Path(self.snapshot.manifest["projects"]["a"]["root"])
        self.samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(self.samples["provenance"]["target_version"], "0.163.0")
        self.initialize_response = copy.deepcopy(self.samples["initialize"])
        self.initialize_response["codexHome"] = str(self.directory.resolve())
        expected_server = read_preview_server((self.bundle / "a-config.preview.toml").read_bytes())
        self.valid_config = copy.deepcopy(self.samples["config_read"]["config"])
        self.valid_config["mcp_servers"][SERVER_NAME] = copy.deepcopy(expected_server)
        self.valid_config["mcp_servers"]["node_repl"] = {"enabled": False}
        self.valid_config["mcp_servers"]["apifox-2"] = {"enabled": False}
        self.valid_layers = [copy.deepcopy(self.samples["project_layer"])]
        self.valid_layers[0]["name"]["dotCodexFolder"] = (self.project_root / ".codex").as_posix()
        self.valid_layers[0]["config"]["mcp_servers"][SERVER_NAME] = copy.deepcopy(expected_server)
        self.disabled_hooks = copy.deepcopy(self.samples["hooks_list"])
        self.disabled_hooks["data"][0]["cwd"] = self.project_root.as_posix()
        self.usage_allowed = copy.deepcopy(self.samples["usage"])

    def call_preflight(self, *, version="codex-cli 0.163.0", effective_config=None,
                       layers=None, hooks=None, usage=None):
        return validate_preflight(
            self.snapshot, "a", version=version,
            effective_config=self.valid_config if effective_config is None else effective_config,
            layers=self.valid_layers if layers is None else layers,
            hooks=self.disabled_hooks if hooks is None else hooks,
            usage=self.usage_allowed if usage is None else usage,
        )

    def test_unexpected_oserror_is_sanitized_not_masked_by_nonexistent_asyncio_exception(self):
        class FailingProbe(ReadOnlyProbe):
            async def _inspect_async(self, snapshot, project, root):
                raise OSError("private raw OS failure")
        probe = FailingProbe([sys.executable], list(self.snapshot.manifest["invocation_only_overrides"]))
        with self.assertRaisesRegex(ValueError, "^C1_READ_ONLY_PREFLIGHT_FAILED$"):
            probe.inspect(self.snapshot, "a")
        probe.close()

    def test_exact_version_and_synthetic_configuration_produce_sanitized_readiness(self):
        result = self.call_preflight()
        self.assertEqual(result["state"], "preflight_ready")
        self.assertEqual(result["project"], "a")
        self.assertEqual(result["version"], "codex-cli 0.163.0")
        self.assertEqual(result["checks"], {
            "version": True, "effective_config": True, "project_layer": True,
            "hooks_disabled": True, "usage_allowed": True,
        })
        self.assertEqual(result["mcp_server"]["name"], SERVER_NAME)
        self.assertEqual(result["mcp_server"]["args_count"], len(self.valid_config["mcp_servers"][SERVER_NAME]["args"]))
        self.assertEqual(result["mcp_server"]["args_sha256"], hashlib.sha256(
            json.dumps(self.valid_config["mcp_servers"][SERVER_NAME]["args"], ensure_ascii=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest())
        self.assertNotIn("args", result["mcp_server"])
        serialized = json.dumps(result)
        self.assertNotIn("synthetic-secret", serialized)
        self.assertNotIn("MEM0_API_KEY", serialized)
        self.assertNotIn(self.valid_config["mcp_servers"][SERVER_NAME]["command"], serialized)
        self.assertIn("root_sha256", result)
        usage = copy.deepcopy(self.usage_allowed)
        usage["accountId"] = "synthetic-account-id"
        self.assertNotIn("synthetic-account-id", json.dumps(self.call_preflight(usage=usage)))

    def test_rejects_version_drift(self):
        with self.assertRaisesRegex(ValueError, "C1_PREFLIGHT_VERSION_MISMATCH"):
            self.call_preflight(version="codex-cli 0.163.1")

    def test_rejects_unknown_enabled_feature(self):
        config = copy.deepcopy(self.valid_config)
        config["features"]["unreviewed_gate"] = True
        with self.assertRaises(ValueError):
            self.call_preflight(effective_config=config)

    def test_rejects_missing_false_and_non_boolean_usage_permission(self):
        for usage in ({}, {"ordinaryUsageAllowed": False}, {"ordinaryUsageAllowed": 1},
                      {"ordinaryUsageAllowed": True}):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                self.call_preflight(usage=usage)

    def test_rejects_disabled_project_layer(self):
        layers = copy.deepcopy(self.valid_layers)
        layers[0]["disabledReason"] = "project is not trusted"
        with self.assertRaises(ValueError):
            self.call_preflight(layers=layers)

    def test_unknown_enabled_mcp_server_is_rejected(self):
        config = copy.deepcopy(self.valid_config)
        config["mcp_servers"]["new_unreviewed_server"] = {"command": "tool", "args": [], "enabled": True}
        with self.assertRaises(ValueError):
            self.call_preflight(effective_config=config)

    def test_layer_disabled_value_does_not_override_enabled_effective_config(self):
        layers = copy.deepcopy(self.valid_layers)
        layers.append({
            "name": {"type": "user", "file": "<synthetic-user-config>"},
            "version": "0.163.0",
            "disabledReason": None,
            "config": {"mcp_servers": {SERVER_NAME: {"enabled": False}}},
        })
        result = self.call_preflight(layers=layers)
        self.assertEqual(result["state"], "preflight_ready")

    def test_rejects_mcp_command_args_environment_and_timeout_mismatches(self):
        for field, value in (
            ("command", "C:/unreviewed/codex.exe"),
            ("args", ["--unreviewed"]),
            ("env", {"MEM0_API_KEY": "secret"}),
            ("startup_timeout_sec", 16),
            ("tool_timeout_sec", True),
        ):
            config = copy.deepcopy(self.valid_config)
            config["mcp_servers"][SERVER_NAME][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.call_preflight(effective_config=config)

    def test_rejects_enabled_hook_and_permissions_or_sandbox_drift(self):
        hooks = copy.deepcopy(self.disabled_hooks)
        hooks["data"][0]["hooks"] = [{
            "currentHash": "synthetic", "displayOrder": 0, "enabled": True,
            "eventName": "sessionStart", "isManaged": False, "key": "synthetic-hook",
            "source": "project", "sourcePath": (self.project_root / ".codex/hook.py").as_posix(),
            "timeoutSec": 1, "trustStatus": "trusted",
        }]
        with self.assertRaises(ValueError):
            self.call_preflight(hooks=hooks)
        for field, value in (("approval_policy", "never"), ("sandbox_mode", "workspace-write")):
            config = copy.deepcopy(self.valid_config)
            config[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.call_preflight(effective_config=config)

    def test_rejects_unknown_config_shapes_and_non_string_version(self):
        config = copy.deepcopy(self.valid_config)
        config["mcp_servers"][SERVER_NAME] = ["unexpected"]
        with self.assertRaises(ValueError):
            self.call_preflight(effective_config=config)
        with self.assertRaises(ValueError):
            self.call_preflight(version=163)

    def test_module_import_does_not_require_python311_tomllib(self):
        source = "import sys; sys.path.insert(0, " + repr(str(ROOT)) + "); sys.modules['tomllib'] = None; import scripts.codex_c1_host"
        result = subprocess.run([sys.executable, "-I", "-c", source], cwd=self.directory,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _fake_probe(self, mode="normal", timeout=3):
        audit = self.directory / ("rpc-methods-" + mode + ".txt")
        payload = json.dumps({
            "initialize": self.initialize_response,
            "config": self.valid_config,
            "layers": self.valid_layers,
            "hooks": self.disabled_hooks,
            "usage": self.usage_allowed,
        })
        probe = ReadOnlyProbe(
            [sys.executable, "-I", "-c", FAKE_HOST, mode, payload, str(audit)],
            list(self.snapshot.manifest["invocation_only_overrides"]), timeout_seconds=timeout,
        )
        self.addCleanup(probe.close)
        return probe, audit

    def test_readonly_probe_rejects_unbound_overrides_before_process_launch(self):
        probe = ReadOnlyProbe(["path-that-is-never-launched"], [])
        with self.assertRaisesRegex(ValueError, "C1_PREFLIGHT_OVERRIDE_MISMATCH"):
            probe.inspect(self.snapshot, "a")
        probe.close()

    def test_local_rpc_subprocess_uses_only_read_only_allowlist_and_cleans_stderr(self):
        probe, audit = self._fake_probe("stderr_secret")
        result = probe.inspect(self.snapshot, "a")
        self.assertEqual(result["state"], "preflight_ready")
        self.assertEqual(audit.read_text(encoding="utf-8").splitlines(), [
            "initialize", "initialized", "config/read", "hooks/list", "account/rateLimits/read",
        ])
        self.assertNotIn("thread/start", audit.read_text(encoding="utf-8"))
        self.assertNotIn("turn/start", audit.read_text(encoding="utf-8"))
        self.assertNotIn("synthetic-secret-must-not-escape", json.dumps(result))
        probe.close()

    def test_mem0_key_is_not_inherited_by_host_child(self):
        probe, _audit = self._fake_probe("credential_guard")
        with patch.dict(os.environ, {"MEM0_API_KEY": "synthetic-env-secret"}):
            result = probe.inspect(self.snapshot, "a")
        self.assertEqual(result["usage_allowed"], True)

    def test_local_rpc_rejects_malformed_initialize_and_missing_rate_limits(self):
        for mode in ("malformed_initialize", "missing_usage_limits"):
            with self.subTest(mode=mode):
                probe, _audit = self._fake_probe(mode)
                with self.assertRaises(ValueError):
                    probe.inspect(self.snapshot, "a")
                probe.close()

    def test_local_rpc_rejects_wrong_response_id_duplicate_keys_and_oversized_line(self):
        for mode in ("wrong_id", "duplicate_key", "long_line", "unexpected_notification"):
            with self.subTest(mode=mode):
                probe, _audit = self._fake_probe(mode)
                with self.assertRaises(ValueError):
                    probe.inspect(self.snapshot, "a")
                probe.close()

    def test_local_rpc_obeys_total_deadline_and_kills_timeout_child(self):
        timeout = 5.0
        probe, _audit = self._fake_probe("timeout", timeout=timeout)
        started = time.monotonic()
        with self.assertRaises(ValueError):
            probe.inspect(self.snapshot, "a")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, timeout + 1.0)
        self.assertTrue(probe._last_child_reaped)
        self.assertIsNotNone(probe._last_child_returncode)
        probe.close()
        self.assertEqual(probe.close(), None)


if __name__ == "__main__":
    unittest.main()
