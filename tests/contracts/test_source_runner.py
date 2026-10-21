import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/test.py"

class SourceRunnerTests(unittest.TestCase):
    def load_runner(self):
        spec = importlib.util.spec_from_file_location("source_test_runner", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runner_works_outside_checkout_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--pattern", "test_models.py"],
                cwd=directory, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r"Ran (\d+) tests?", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        self.assertGreater(int(match.group(1)), 0, result.stderr)

    def test_runner_disables_external_evidence_before_discovery(self):
        runner = self.load_runner()
        def discover(*args, **kwargs):
            self.assertEqual(os.environ["EXPERIENCE_MEMORY_LIVE_CONTRACTS"], "0")
            self.assertEqual(os.environ["EXPERIENCE_MEMORY_LIVE_FUNCTIONAL"], "0")
            self.assertNotIn("EXPERIENCE_MEMORY_NATIVE_EVIDENCE", os.environ)
            self.assertEqual(Path(kwargs["start_dir"]), ROOT / "tests")
            self.assertEqual(Path(kwargs["top_level_dir"]), ROOT)
            return unittest.TestSuite()
        flags = {"EXPERIENCE_MEMORY_LIVE_CONTRACTS": "1",
                 "EXPERIENCE_MEMORY_LIVE_FUNCTIONAL": "1",
                 "EXPERIENCE_MEMORY_NATIVE_EVIDENCE": "must-not-read.json"}
        stream = io.StringIO()
        with patch.dict(os.environ, flags), patch.object(sys, "path", list(sys.path)):
            with patch.object(unittest.TestLoader, "discover", side_effect=discover):
                with patch.object(sys, "stderr", stream):
                    self.assertEqual(runner.main([]), 0)

    def test_runner_propagates_test_failure(self):
        runner = self.load_runner()
        module_name = "test_source_runner_intentional_failure"
        path_before = list(sys.path)
        stream = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as directory:
                failing = Path(directory) / f"{module_name}.py"
                failing.write_text(
                    "import unittest\n"
                    "\n"
                    "class FailingCase(unittest.TestCase):\n"
                    "    def test_fails(self):\n"
                    "        self.fail('intentional failure')\n",
                    encoding="utf-8",
                )
                suite = unittest.TestLoader().discover(
                    start_dir=directory, pattern=f"{module_name}.py", top_level_dir=directory,
                )
            self.assertEqual(suite.countTestCases(), 1)
            with patch.dict(os.environ), patch.object(sys, "path", list(sys.path)):
                with patch.object(unittest.TestLoader, "discover", return_value=suite):
                    with patch.object(sys, "stderr", stream):
                        self.assertEqual(runner.main([]), 1)
            output = stream.getvalue()
            self.assertIn("Ran 1 test", output)
            self.assertIn("FAILED", output)
            self.assertIn("intentional failure", output)
        finally:
            sys.path[:] = path_before
            sys.modules.pop(module_name, None)
        self.assertEqual(list(sys.path), path_before)
        self.assertNotIn(module_name, sys.modules)
