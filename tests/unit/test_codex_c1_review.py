import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import prepare_codex_c1_review as review_exporter

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_codex_c1_review.py"
NONCE = "c1-review-offline-20261018"


class CodexC1ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = self.directory / "review bundle"

    def run_script(self, *arguments):
        self.assertTrue(SCRIPT.is_file(), "offline-only C1 review exporter is not implemented")
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, arguments)],
                              cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def prepare(self):
        result = self.run_script("--output", self.bundle, "--run-nonce", NONCE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "review_prepared")
        return json.loads((self.bundle / "launch-review.json").read_text(encoding="utf-8"))

    def test_prepares_uninstalled_previews_with_exact_hashes_and_no_native_authorization(self):
        manifest = self.prepare()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["mode"], "review_only")
        self.assertEqual(manifest["target_codex_version"], "0.163.0")
        self.assertFalse(manifest["host_version_observed"])
        self.assertEqual(manifest["native_authorization"], {"granted": False, "remaining_model_tasks": 0})
        self.assertEqual(manifest["proposal"]["max_model_task_attempts"], 2)
        self.assertEqual(manifest["proposal"]["max_tool_calls_per_task"], 5)
        self.assertEqual(manifest["proposal"]["max_tool_calls_total"], 10)
        self.assertFalse(manifest["proposal"]["automatic_retry"])
        self.assertEqual(manifest["proposal"]["allowed_tools"], ["c1_bootstrap", "memory_status"])
        self.assertEqual(manifest["limits_enforcement"], "bounded_offline_executor_native_blocked")
        self.assertEqual(manifest["executor"], {
            "cli": "scripts/run_codex_c1.py", "default_action": "dry-run",
            "native_dispatch_supported": False,
            "native_dispatch_blocked_reason": "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED",
            "simulation_origin": "trusted_programmatic_tests_only",
        })
        self.assertGreater(len(manifest["stop_conditions"]), 3)
        self.assertIn("features.hooks=false", manifest["invocation_only_overrides"])
        source = "src/experience_memory/infrastructure/host_context.py"
        self.assertEqual(manifest["source_sha256"][source], hashlib.sha256((ROOT / source).read_bytes()).hexdigest())
        executor = "scripts/codex_c1_bundle.py"
        self.assertEqual(manifest["source_sha256"][executor], hashlib.sha256((ROOT / executor).read_bytes()).hexdigest())
        for project, other in (("a", "b"), ("b", "a")):
            root = self.bundle / "projects" / project
            self.assertFalse((root / ".codex/config.toml").exists())
            self.assertFalse((root / ".codex/hooks.json").exists())
            marker = json.loads((root / ".codex/c1-fixture.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["run_nonce"], NONCE)
            self.assertEqual(marker["project"], project)
            preview = self.bundle / (project + "-config.preview.toml")
            self.assertEqual(manifest["projects"][project]["config_preview_sha256"],
                             hashlib.sha256(preview.read_bytes()).hexdigest())
            config = tomllib.loads(preview.read_text(encoding="utf-8"))
            self.assertEqual(set(config["mcp_servers"]), {"experience_memory_c1_wiring"})
            server = config["mcp_servers"]["experience_memory_c1_wiring"]
            self.assertEqual(Path(server["command"]), Path(sys.executable))
            self.assertEqual(server["args"][:2], ["-I", (ROOT / "tests/fixtures/codex_c1_server.py").as_posix()])
            self.assertIn((self.bundle / "projects" / other).as_posix(), server["args"])
            self.assertIn(NONCE, server["args"])
            self.assertIn("--bounded-scenario", server["args"])
            self.assertEqual(server["tool_timeout_sec"], 15)
            self.assertEqual(len(manifest["projects"][project]["tool_sequence"]), 5)
        verified = self.run_script("--verify", self.bundle)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "review_unchanged")

    def test_existing_bundle_is_preserved_and_never_overwritten(self):
        self.prepare()
        path = self.bundle / "launch-review.json"
        before = path.read_bytes()
        result = self.run_script("--output", self.bundle, "--run-nonce", NONCE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_nonce_creates_no_artifacts(self):
        for nonce in ("../unsafe", "", "c1-a\n", "C1-WRONG"):
            with self.subTest(nonce=nonce):
                result = self.run_script("--output", self.bundle, "--run-nonce", nonce)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.bundle.exists())

    def test_changed_preview_fails_read_only_verification(self):
        self.prepare()
        path = self.bundle / "a-config.preview.toml"
        path.write_bytes(path.read_bytes() + b"\n# deliberately changed fixture\n")
        before = path.read_bytes()
        result = self.run_script("--verify", self.bundle)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_review_verification_rejects_dangling_config_and_hooks_reparse_entries(self):
        self.prepare()
        original_exists = Path.exists
        original_lstat = Path.lstat
        for relative in ("projects/a/.codex/config.toml", "projects/a/.codex/hooks.json"):
            with self.subTest(path=relative):
                entry = self.bundle / relative
                def fake_exists(path):
                    if path == entry:
                        return False
                    return original_exists(path)
                def fake_lstat(path):
                    if path == entry:
                        return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
                    return original_lstat(path)
                with patch.object(Path, "exists", autospec=True, side_effect=fake_exists):
                    with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
                        with self.assertRaises(ValueError):
                            review_exporter.verify(self.bundle)

    def test_changed_source_digest_or_marker_cannot_pass_verification(self):
        self.prepare()
        path = self.bundle / "launch-review.json"
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest["source_sha256"]["src/experience_memory/infrastructure/host_context.py"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        changed = path.read_bytes()
        result = self.run_script("--verify", self.bundle)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), changed)
        path.write_bytes(original)
        marker = self.bundle / "projects/b/.codex/c1-fixture.json"
        marker.write_text('{}', encoding="utf-8")
        result = self.run_script("--verify", self.bundle)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), '{}')


if __name__ == "__main__":
    unittest.main()
