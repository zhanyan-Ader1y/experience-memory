import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from experience_memory.domain.errors import MemoryError
from experience_memory.infrastructure.core_loader import load_core


class CoreLoaderTests(unittest.TestCase):
    def test_only_explicit_core_files_are_loaded_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "core.md").write_text("核心一", encoding="utf-8")
            (root / "second.md").write_text("核心二", encoding="utf-8")
            (root / "noncore.md").write_text("SHOULD NOT LOAD", encoding="utf-8")
            self.assertEqual(load_core(root, ("core.md", "second.md")), "核心一\n\n核心二")
            self.assertEqual(load_core(root, ()), "")

    def test_missing_external_and_over_budget_core_fail_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "big.md").write_text("中" * 3000, encoding="utf-8")
            for paths in (("missing.md",), ("../outside.md",), ("\\outside\\core.md",), ("big.md",)):
                with self.subTest(paths=paths), self.assertRaises(MemoryError):
                    load_core(root, paths)

    def test_junction_or_symlink_to_external_core_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, outside = base / "project", base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "core.md").write_text("external", encoding="utf-8")
            link = root / "linked"
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
                self.assertEqual(result.returncode, 0)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                with self.assertRaises(MemoryError):
                    load_core(root, ("linked/core.md",))
            finally:
                # Unlink only the verified fixture link, never recurse into its target.
                if os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()
