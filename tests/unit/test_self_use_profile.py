import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from experience_memory.config import Config, load_config, load_project_config
from experience_memory.domain.errors import ErrorCode, MemoryError
from experience_memory.infrastructure.core_loader import load_core
from experience_memory.infrastructure.scope import resolve_scope

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = ROOT / "examples/self-use/project-config.preview.json"
EXPECTED = {
    "enabled": False,
    "mem0_api_url": "https://api.mem0.ai",
    "project_scope": "local:experience-memory",
    "core_paths": ["docs/self-use/core.md"],
    "retention_budget_bytes": 8192,
    "search_limit": 5,
    "request_timeout_seconds": 10,
    "hook_deadline_seconds": 30,
    "shared_scope": False,
    "namespace_mapping": None,
}


def init_git(root):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    subprocess.run(["git", "-C", str(root), "init", "--initial-branch=self-use-fixture"],
                   env=env, check=True, capture_output=True, timeout=10)


class SelfUseProfileTests(unittest.TestCase):
    def test_preview_is_exact_disabled_existing_config_schema(self):
        values = json.loads(PREVIEW.read_text(encoding="utf-8"))
        self.assertEqual(values, EXPECTED)
        self.assertEqual(load_config(PREVIEW), Config.from_mapping(EXPECTED))

    def test_preview_is_not_automatically_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "examples/self-use/project-config.preview.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(PREVIEW.read_bytes())
            automatic = load_project_config(root)
            explicit = load_project_config(root, target)
            self.assertIsNone(automatic.project_scope)
            self.assertEqual(automatic.core_paths, ())
            self.assertEqual(explicit.project_scope, EXPECTED["project_scope"])
            self.assertIs(explicit.enabled, False)
            self.assertFalse((root / ".experience-memory.json").exists())

    def test_no_remote_requires_explicit_identity_and_nested_root_is_stable(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_git(root)
            with self.assertRaises(MemoryError) as raised:
                resolve_scope(root)
            self.assertEqual(raised.exception.code, ErrorCode.SCOPE_UNRESOLVED)
            nested = root / "nested"
            nested.mkdir()
            first = resolve_scope(root, config.project_scope)
            second = resolve_scope(nested, config.project_scope)
            digest = hashlib.sha256(config.project_scope.encode("utf-8")).hexdigest()
            self.assertEqual(first, second)
            self.assertEqual(first.project_key, "local:experience-memory")
            self.assertEqual(first.app_id, "experience-v1-" + digest)
            self.assertEqual(first.project_id, "project-v1-" + digest)
            self.assertEqual(first.legacy_project_ids, ())

    def test_reviewed_project_identity_survives_directory_move(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "original"
            root.mkdir()
            init_git(root)
            before = resolve_scope(root, config.project_scope)
            moved = base / "moved project"
            root.rename(moved)
            self.assertEqual(resolve_scope(moved, config.project_scope), before)

    def test_unrelated_explicit_scope_does_not_share_namespace(self):
        config = load_config(PREVIEW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_git(root)
            current = resolve_scope(root, config.project_scope)
            unrelated = resolve_scope(root, "local:self-use-unrelated-fixture")
            self.assertNotEqual(current.project_key, unrelated.project_key)
            self.assertNotEqual(current.app_id, unrelated.app_id)
            self.assertNotEqual(current.project_id, unrelated.project_id)

    def test_only_short_project_core_is_loaded_without_link_expansion(self):
        config = load_config(PREVIEW)
        self.assertEqual(config.core_paths, ("docs/self-use/core.md",))
        core = load_core(ROOT, config.core_paths, budget_bytes=config.retention_budget_bytes)
        expected = (ROOT / "docs/self-use/core.md").read_bytes().decode("utf-8")
        self.assertEqual(core, expected)
        self.assertTrue(core.startswith("# 本项目自用核心"))
        self.assertGreater(len(core.encode("utf-8")), 0)
        self.assertLessEqual(len(core.encode("utf-8")), 1024)
        fixed = (ROOT / "prompts/core.md").read_text(encoding="utf-8") + "\n" + core
        self.assertLessEqual(len(fixed.encode("utf-8")), 4096)
        self.assertNotIn("{entries}", core)
