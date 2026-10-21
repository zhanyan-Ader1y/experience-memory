from importlib.metadata import PackageNotFoundError
from pathlib import Path
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from experience_memory.infrastructure.environment import inspect_environment

ROOT = Path(__file__).resolve().parents[2]


class EnvironmentTests(unittest.TestCase):
    def test_matching_sdk_is_ready_without_importing_sdk(self):
        with patch("experience_memory.infrastructure.environment.version", return_value="2.3.0"):
            report = inspect_environment()
        self.assertTrue(report["ready"])
        self.assertEqual(report["mcp_required"], "2.3.0")
        self.assertEqual(report["mcp_installed"], "2.3.0")

    def test_missing_or_wrong_sdk_is_not_ready(self):
        for installed in (None, "1.28.1", "2.2.0", "2.3.1"):
            with self.subTest(installed=installed):
                kwargs = {"side_effect": PackageNotFoundError("mcp")} if installed is None else {"return_value": installed}
                with patch("experience_memory.infrastructure.environment.version", **kwargs):
                    report = inspect_environment()
                self.assertFalse(report["mcp_compatible"])
                self.assertFalse(report["ready"])
                self.assertEqual(report["mcp_installed"], installed)

    def test_python_below_declared_floor_is_not_ready(self):
        with patch("experience_memory.infrastructure.environment.sys.version_info", (3, 9, 0)):
            with patch("experience_memory.infrastructure.environment.version", return_value="2.3.0"):
                report = inspect_environment()
        self.assertFalse(report["python_supported"])
        self.assertFalse(report["ready"])

    def test_required_sdk_matches_project_declaration(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIn('"mcp==' + inspect_environment()["mcp_required"] + '"',
                      (root / "pyproject.toml").read_text(encoding="utf-8"))

    def test_fresh_process_environment_inspection_imports_no_mcp_modules(self):
        script = (
            "import json, sys\n"
            "from experience_memory.infrastructure.environment import inspect_environment\n"
            "before = sorted(n for n in sys.modules if n == 'mcp' or n.startswith('mcp.'))\n"
            "report = inspect_environment()\n"
            "after = sorted(n for n in sys.modules if n == 'mcp' or n.startswith('mcp.'))\n"
            "print(json.dumps({'before': before, 'after': after, 'ready': report['ready']}))\n"
        )
        env = dict(os.environ)
        src = str(ROOT / "src")
        env["PYTHONPATH"] = src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["before"], [])
        self.assertEqual(payload["after"], [])
