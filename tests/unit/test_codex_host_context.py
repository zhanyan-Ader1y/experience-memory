import tempfile
import unittest
from pathlib import Path

from experience_memory.domain.errors import ErrorCode, MemoryError
from experience_memory.infrastructure.host_context import (
    codex_request_host,
    current_host,
    decode_codex_metadata,
)


_DEFAULT_ROOT = object()


class _CodexMetadataCases:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.meta = {
            "sessionId": "session-1",
            "threadId": "session-1",
            "x-codex-turn-metadata": {
                "session_id": "session-1",
                "thread_id": "session-1",
                "codex_version": "0.163.0",
                "workspaces": {str(self.root): {"project": "synthetic"}},
                "client_extra": "ignored",
            },
            "untrusted_extra": {"experience_memory_host": {"cwd": "/wrong", "session_id": "wrong"}},
        }

    def assert_unverified(self, metadata, project_root=_DEFAULT_ROOT, host_id="codex"):
        root = self.root if project_root is _DEFAULT_ROOT else project_root
        with self.assertRaises(MemoryError) as caught:
            decode_codex_metadata(metadata, host_id, root)
        self.assertIs(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)
        self.assertEqual(str(caught.exception), "HOST_SOURCE_UNVERIFIED")

    def test_decodes_native_metadata_to_canonical_fixed_repository_root(self):
        nested = self.root / "nested"
        nested.mkdir()
        self.meta["x-codex-turn-metadata"]["workspaces"] = {str(nested / ".."): {}}
        host = decode_codex_metadata(self.meta, "codex", self.root)
        self.assertEqual(host.cwd, self.root.resolve(strict=True))
        self.assertEqual(host.session_id, "session-1")
        self.assertEqual(host.host_id, "codex")
        self.assertEqual(host.source, "codex_stdio_0.163.0")

    def test_ignores_extra_fields_without_changing_identity(self):
        expected = decode_codex_metadata(self.meta, "codex", self.root)
        self.meta["x-codex-turn-metadata"]["model"] = "fake-model"
        self.meta["x-codex-turn-metadata"]["cwd"] = "/untrusted"
        self.meta["arguments"] = {"sessionId": "attacker"}
        self.assertEqual(decode_codex_metadata(self.meta, "codex", self.root), expected)

    def test_accepts_sdk_metadata_model_dump(self):
        from pydantic import BaseModel, ConfigDict

        class SdkMeta(BaseModel):
            model_config = ConfigDict(extra="allow")

        sdk_meta = SdkMeta.model_validate(self.meta)
        self.assertEqual(decode_codex_metadata(sdk_meta, "codex", self.root).session_id, "session-1")

    def test_missing_and_malformed_metadata_are_unverified(self):
        for value in (None, "{}", [], {}, {"sessionId": "session-1"}):
            with self.subTest(value=type(value).__name__):
                self.assert_unverified(value)

    def test_missing_nested_identity_or_version_are_unverified(self):
        variants = []
        no_nested = dict(self.meta); no_nested.pop("x-codex-turn-metadata")
        variants.append(no_nested)
        for key in ("session_id", "thread_id", "codex_version", "workspaces"):
            missing = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
            missing["x-codex-turn-metadata"].pop(key)
            variants.append(missing)
        for key in ("sessionId", "threadId"):
            missing = dict(self.meta); missing.pop(key)
            variants.append(missing)
        for value in variants:
            with self.subTest(meta=value):
                self.assert_unverified(value)

    def test_wrong_versions_and_nonstring_empty_or_conflicting_ids_are_unverified(self):
        variants = []
        for version in ("0.162.0", "0.163.1", 0.163):
            bad = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
            bad["x-codex-turn-metadata"]["codex_version"] = version
            variants.append(bad)
        for field, value in (("sessionId", ""), ("threadId", None), ("sessionId", "other")):
            bad = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
            bad[field] = value
            variants.append(bad)
        for field, value in (("session_id", ""), ("thread_id", 7), ("session_id", "other")):
            bad = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
            bad["x-codex-turn-metadata"][field] = value
            variants.append(bad)
        for value in variants:
            with self.subTest(meta=value):
                self.assert_unverified(value)

    def test_legacy_only_metadata_is_unverified(self):
        self.assert_unverified({"experience_memory_host": {"cwd": str(self.root), "session_id": "old"}})

    def test_host_context_rejects_unsafe_session_and_host_ids(self):
        for value in (" padded ", "line\nbreak"):
            bad = {**self.meta, "sessionId": value, "threadId": value,
                   "x-codex-turn-metadata": {**self.meta["x-codex-turn-metadata"],
                                              "session_id": value, "thread_id": value}}
            with self.subTest(value=value):
                self.assert_unverified(bad)
        self.assert_unverified(self.meta, host_id="bad\nidentity")

    def test_missing_nonabsolute_or_nonexistent_trusted_root_is_unverified(self):
        self.assert_unverified(self.meta, None)
        self.assert_unverified(self.meta, Path("relative-root"))
        self.assert_unverified(self.meta, self.root / "does-not-exist")

    def test_workspace_must_be_one_absolute_existing_root_with_object_value(self):
        for workspaces in ({}, {str(self.root): {}, str(self.root / "other"): {}}, {"relative": {}},
                           {str(self.root / "missing"): {}}, {str(self.root): None},
                           {Path(self.root): {}}, [str(self.root)]):
            bad = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
            bad["x-codex-turn-metadata"]["workspaces"] = workspaces
            with self.subTest(workspaces=workspaces):
                self.assert_unverified(bad)

    def test_valid_different_workspace_is_scope_mismatch(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        bad = {**self.meta, "x-codex-turn-metadata": dict(self.meta["x-codex-turn-metadata"])}
        bad["x-codex-turn-metadata"]["workspaces"] = {str(other): {}}
        with self.assertRaises(MemoryError) as caught:
            decode_codex_metadata(bad, "codex", self.root)
        self.assertIs(caught.exception.code, ErrorCode.SCOPE_MISMATCH)


class CodexMetadataTests(_CodexMetadataCases, unittest.TestCase):
    pass


class CodexRequestContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.meta = {
            "sessionId": "session-1", "threadId": "session-1",
            "x-codex-turn-metadata": {
                "session_id": "session-1", "thread_id": "session-1", "codex_version": "0.163.0",
                "workspaces": {str(self.root): {}},
            },
        }

    def test_current_host_is_unverified_outside_context(self):
        with self.assertRaises(MemoryError) as caught:
            current_host()
        self.assertIs(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)

    def test_context_resets_after_success_exception_and_nested_context(self):
        first = dict(self.meta)
        outer = decode_codex_metadata(first, "codex", self.root)
        with codex_request_host(first, "codex", self.root):
            self.assertEqual(current_host(), outer)
            other = {**first, "sessionId": "session-2", "threadId": "session-2",
                     "x-codex-turn-metadata": {**first["x-codex-turn-metadata"],
                                                "session_id": "session-2", "thread_id": "session-2"}}
            with codex_request_host(other, "codex", self.root):
                self.assertEqual(current_host().session_id, "session-2")
            self.assertEqual(current_host(), outer)
        with self.assertRaises(MemoryError):
            current_host()
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with codex_request_host(first, "codex", self.root):
                raise RuntimeError("synthetic")
        with self.assertRaises(MemoryError) as caught:
            current_host()
        self.assertIs(caught.exception.code, ErrorCode.HOST_SOURCE_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
