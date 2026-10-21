import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from experience_memory.config import Config, NamespaceMapping, load_project_config
from experience_memory.domain.errors import MemoryError
from experience_memory.domain.models import ControlPointer
from experience_memory.infrastructure.scope import canonical_remote, resolve_scope
from experience_memory.infrastructure.session_runtime import LocalSessionRuntime
from experience_memory.infrastructure.session_pointer import SessionPointerStore


def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


class ScopeRuntimeTests(unittest.TestCase):
    def test_ambiguous_user_relative_ssh_paths_cannot_collapse_projects(self):
        for value in ("alice@git.example.com:team/repo.git", "bob@git.example.com:team/repo.git",
                      "ssh://alice@git.example.com/~/team/repo.git", "git@private.example.com:team/repo.git"):
            with self.subTest(value=value), self.assertRaises(MemoryError):
                canonical_remote(value)
        # Caller-supplied canonical HTTPS identity remains available for custom servers.
        self.assertEqual(canonical_remote("https://private.example.com/team/repo.git"),
                         "https://private.example.com/team/repo")
    def test_https_and_ssh_are_same_identity_but_hosts_are_not(self):
        self.assertEqual(canonical_remote("git@github.com:team/repo.git"), "https://github.com/team/repo")
        self.assertEqual(canonical_remote("ssh://git@github.com/team/repo.git"), "https://github.com/team/repo")
        self.assertEqual(canonical_remote("https://github.com/team/repo.git/"), "https://github.com/team/repo")
        self.assertNotEqual(canonical_remote("https://gitlab.com/team/repo"), "https://github.com/team/repo")
        for identity in ("file:///tmp/repo", "https://secret:pass@example.com/repo", "https://example.com/a/../b", "https://example.com/a?token=x"):
            with self.subTest(identity=identity), self.assertRaises(MemoryError):
                canonical_remote(identity)

    def test_no_remote_requires_stable_explicit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init", "--initial-branch=test")
            with self.assertRaises(MemoryError):
                resolve_scope(root)
            first = resolve_scope(root, "local:fixture-project")
            (root / "nested").mkdir()
            second = resolve_scope(root / "nested", "local:fixture-project")
            self.assertEqual(first.project_key, "local:fixture-project")
            self.assertEqual(first.project_id, second.project_id)

    def test_worktrees_share_scope_and_sessions_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            git(root, "init", "--initial-branch=test")
            git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "synthetic")
            git(root, "remote", "add", "origin", "git@github.com:fixtures/project.git")
            worktree = Path(directory) / "worktree"
            git(root, "worktree", "add", "--detach", str(worktree))
            a, b = resolve_scope(root), resolve_scope(worktree)
            self.assertEqual(a.project_key, b.project_key)
            self.assertEqual(a.project_id, b.project_id)
            store = SessionPointerStore(Path(directory) / "control")
            left = LocalSessionRuntime(store, project_scope=None, host_id="host")
            one = left.prepare(root, "session-one")
            two = left.prepare(worktree, "session-two")
            self.assertNotEqual(store.path_for(one.project_key, one.session_id), store.path_for(two.project_key, two.session_id))
            self.assertEqual(one.state, "prepared")
            self.assertIsNone(one.session_handle)
            with self.assertRaises(MemoryError):
                left.bind_session(a, "session-one")

    def test_ambiguous_remotes_are_rejected_without_explicit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "remote", "add", "first", "https://example.com/team/a")
            git(root, "remote", "add", "second", "https://example.com/team/b")
            with self.assertRaises(MemoryError):
                resolve_scope(root)

    def test_namespace_mapping_is_explicit_and_immutable(self):
        mapping = NamespaceMapping("old-app", "old-project", ("older-project",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            scope = resolve_scope(root, "local:fixture", namespace_mapping=mapping)
            self.assertEqual((scope.app_id, scope.project_id, scope.legacy_project_ids), ("old-app", "old-project", ("older-project",)))
        for value in ({"app_id": "*", "project_id": "old"}, {"app_id": "old", "project_id": "old", "memories": []}):
            with self.subTest(value=value), self.assertRaises(MemoryError):
                Config.from_mapping({"namespace_mapping": value})

    def test_only_project_root_config_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "project"
            root.mkdir()
            (parent / ".experience-memory.json").write_text('{"enabled": false}', encoding="utf-8")
            self.assertTrue(load_project_config(root).enabled)
            (root / ".experience-memory.json").write_text('{"search_limit": 7}', encoding="utf-8")
            self.assertEqual(load_project_config(root).search_limit, 7)
            explicit = parent / "explicit.json"
            explicit.write_text('{"enabled": false}', encoding="utf-8")
            self.assertFalse(load_project_config(root, explicit).enabled)

    def test_pointer_contains_no_rule_data_and_atomic_failure_preserves_old(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionPointerStore(Path(directory))
            pointer = ControlPointer("local:project", "session", "host")
            store.write(pointer)
            path = store.path_for(pointer.project_key, pointer.session_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("entries", data)
            self.assertNotIn("body", data)
            self.assertNotIn("summary", data)
            with patch("experience_memory.infrastructure.session_pointer.os.replace", side_effect=OSError("fixture interruption")):
                with self.assertRaises(MemoryError):
                    store.write(ControlPointer("local:project", "session", "host", generation=1))
            self.assertEqual(store.read("local:project", "session"), pointer)
            self.assertFalse(list(Path(directory).glob("*.tmp")))
            data["entries"] = ["rule"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(MemoryError):
                store.read("local:project", "session")

    def test_lock_is_shared_between_processes_and_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionPointerStore(Path(directory))
            with store.lock("local:project", "session", timeout_seconds=1):
                code = "from pathlib import Path; from experience_memory.infrastructure.session_pointer import SessionPointerStore; from experience_memory.domain.errors import MemoryError; s=SessionPointerStore(Path(__import__('sys').argv[1]));\ntry:\n with s.lock('local:project','session',timeout_seconds=0.2): print('unexpected')\nexcept MemoryError as e: print(e.code.value)"
                env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
                child = subprocess.run([sys.executable, "-c", code, directory], env=env,
                                       capture_output=True, text=True, timeout=5)
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertEqual(child.stdout.strip(), "LOCK_TIMEOUT")
            with store.lock("local:project", "session", timeout_seconds=0.2):
                pass
