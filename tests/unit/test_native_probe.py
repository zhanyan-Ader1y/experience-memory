import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NativeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("native_probe", ROOT / "scripts/native_hook_probe.py")
        cls.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.probe)

    def test_precompact_never_injects_and_only_allowlisted_metadata_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            event = {"hook_event_name": "PreCompact", "session_id": "session", "cwd": str(ROOT),
                     "turn_id": "turn", "trigger": "manual", "prompt": "PRIVATE BODY"}
            output = self.probe.process(event, Path(directory), "nonce-1", 8000)
            self.assertEqual(output, {"continue": True})
            records = list(Path(directory).glob("*.json"))
            self.assertEqual(len(records), 1)
            text = records[0].read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE BODY", text)
            row = json.loads(text)
            self.assertEqual(row["event"], "PreCompact")
            self.assertNotIn("payload", row)

    def test_compact_payload_is_bounded_and_audit_contains_no_body(self):
        with tempfile.TemporaryDirectory() as directory:
            event = {"hook_event_name": "SessionStart", "session_id": "session", "cwd": str(ROOT), "source": "compact"}
            result = self.probe.process(event, Path(directory), "nonce-1", 8192)
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(len(context.encode("utf-8")), 8192)
            self.assertIn("EXPERIENCE_MEMORY_SYNTHETIC_PROBE", context)
            record = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["payload_bytes"], 8192)
            self.assertNotIn("additionalContext", record)

    def test_repeated_compaction_has_distinct_event_sentinels_linked_to_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            event = {"hook_event_name": "SessionStart", "session_id": "same-session",
                     "cwd": str(ROOT), "source": "compact"}
            contexts = [self.probe.process(event, Path(directory), "same-run", 8192)
                        ["hookSpecificOutput"]["additionalContext"] for _ in range(2)]
            self.assertNotEqual(contexts[0], contexts[1])
            rows = [json.loads(path.read_text(encoding="utf-8"))
                    for path in Path(directory).glob("*.json")]
            self.assertEqual(len({row["invocation_id"] for row in rows}), 2)
            for context in contexts:
                digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
                row, = [row for row in rows if row["payload_sha256"] == digest]
                self.assertRegex(row["invocation_id"], r"^[0-9a-f]{32}$")
                self.assertIn(":" + row["invocation_id"] + "\n", context)
                self.assertTrue((Path(directory) / (row["invocation_id"] + ".json")).is_file())
                self.assertEqual(len(context.encode("utf-8")), 8192)
