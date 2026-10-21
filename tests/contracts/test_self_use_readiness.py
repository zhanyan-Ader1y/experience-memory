from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experience_memory.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = ROOT / "examples/self-use/project-config.preview.json"


class SelfUseReadinessTests(unittest.TestCase):
    def check_report(self, report):
        self.assertIs(report["enabled"], False)
        self.assertIs(report["local_checks_passed"], True)
        self.assertIs(report["runtime_checks_passed"], True)
        self.assertEqual(report["local_project"]["status"], "prepared")
        self.assertEqual(report["local_project"]["project_key"], "local:experience-memory")
        self.assertIs(report["local_project"]["remote_binding_confirmed"], False)
        self.assertEqual(report["adapters"]["experience"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["control_state"], "NOT_CONFIGURED")
        self.assertEqual(report["adapters"]["session_runtime"], "local_only")
        self.assertEqual(report["mem0_authentication"], "not_checked")
        self.assertEqual(report["native_compaction"], "not_verified")
        self.assertEqual(set(report["capabilities"].values()),
                         {"implemented_host_contract_unverified"})
        self.assertNotIn("session_handle", report)

    def test_doctor_never_assembles_remote_client_binding_or_pointer_store(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            control = Path(directory) / "must-not-exist"
            stack.enter_context(patch.dict(os.environ, {
                "MEM0_API_KEY": "", "PLUGIN_DATA": str(control),
            }))
            for target in ("Mem0Client", "credentials", "SessionBinding", "SessionPointerStore"):
                stack.enter_context(patch("experience_memory.bootstrap." + target,
                                          side_effect=AssertionError("forbidden doctor assembly")))
            with redirect_stdout(output):
                code = main(["doctor", "--config", str(PREVIEW),
                             "--project-root", str(ROOT), "--plugin-root", str(ROOT)])
            self.assertEqual(code, 0)
            self.check_report(json.loads(output.getvalue()))
            self.assertFalse(control.exists())

    def test_original_source_launcher_checks_preview_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            control = foreign / "must-not-exist"
            env = dict(os.environ, MEM0_API_KEY="", PLUGIN_DATA=str(control))
            env.pop("PYTHONPATH", None)
            env.pop("PLUGIN_ROOT", None)
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/launch.py"), "doctor",
                "--config", str(PREVIEW), "--project-root", str(ROOT),
            ], cwd=foreign, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.check_report(json.loads(result.stdout))
            self.assertNotIn("# 本项目自用核心", result.stdout)
            self.assertFalse(control.exists())
