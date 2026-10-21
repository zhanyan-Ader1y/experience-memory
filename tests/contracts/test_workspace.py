import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SourceWorkspaceContractTests(unittest.TestCase):
    def test_doctor_distinguishes_package_checks_from_memory_support(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/launch.py"), "doctor"],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(report["local_checks_passed"], True)
        self.assertIs(report["runtime_checks_passed"], True)
        self.assertEqual(report["runtime_environment"]["mcp_installed"], "2.3.0")
        self.assertEqual(report["adapters"]["experience"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["control_state"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["session_runtime"], "local_only")
        self.assertEqual(set(report["capabilities"].values()), {"implemented_host_contract_unverified"})
        self.assertEqual(report["mem0_authentication"], "not_checked")
        self.assertEqual(report["native_compaction"], "not_verified")
        self.assertNotIn("scope", report)

    def test_source_runs_in_a_path_with_spaces_without_project_cwd(self):
        with tempfile.TemporaryDirectory(prefix="memory plugin ") as directory:
            root = Path(directory)
            extracted = root / "experience-memory"
            shutil.copytree(ROOT, extracted, ignore=shutil.ignore_patterns(
                ".git", ".venv", "build", "dist", "*.egg-info", "__pycache__", "*.pyc",
                ".superpowers", "local-evidence",
            ))
            env = {key: value for key, value in os.environ.items() if key not in ("PLUGIN_ROOT", "PYTHONPATH")}
            result = subprocess.run([sys.executable, str(extracted / "scripts/launch.py"), "doctor"], cwd=root,
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIs(json.loads(result.stdout)["local_checks_passed"], True)

    def test_doctor_project_checks_use_root_config_but_do_not_bind_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init"], capture_output=True, check=True)
            (root / "core.md").write_text("synthetic fixed core", encoding="utf-8")
            (root / ".experience-memory.json").write_text(json.dumps({
                "project_scope": "local:doctor-fixture", "core_paths": ["core.md"],
            }), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/launch.py"), "doctor", "--project-root", str(root)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["local_project"]["status"], "prepared")
            self.assertEqual(output["local_project"]["core_bytes"], 20)
            self.assertIs(output["local_project"]["remote_binding_confirmed"], False)
            self.assertNotIn("synthetic fixed core", result.stdout)
            self.assertFalse((root / ".experience-memory").exists())

    def test_doctor_reports_sdk_mismatch_with_nonzero_exit(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import patch
        from experience_memory.__main__ import main
        output = io.StringIO()
        with patch("experience_memory.infrastructure.environment.version", return_value="1.28.1"):
            with redirect_stdout(output):
                code = main(["doctor", "--plugin-root", str(ROOT)])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertTrue(report["local_checks_passed"])
        self.assertFalse(report["runtime_checks_passed"])
        self.assertEqual(report["mem0_authentication"], "not_checked")
        self.assertEqual(report["native_compaction"], "not_verified")
