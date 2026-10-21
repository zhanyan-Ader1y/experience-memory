import json
from pathlib import Path
import tempfile
import unittest

from experience_memory.domain.errors import MemoryError
from experience_memory.domain.models import ControlPointer
from experience_memory.infrastructure.session_pointer import SessionPointerStore


class PointerBoundaryTests(unittest.TestCase):
    def test_oversized_pointer_is_rejected_before_replacing_readable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionPointerStore(Path(directory))
            original = ControlPointer("local:a", "session", "host")
            store.write(original)
            with self.assertRaises(MemoryError):
                store.write(ControlPointer("local:a", "session", "中" * 6000))
            self.assertEqual(store.read("local:a", "session"), original)

    def test_foreign_pointer_and_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionPointerStore(Path(directory))
            pointer = ControlPointer("local:a", "session", "host")
            store.write(pointer)
            path = store.path_for("local:a", "session")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["project_key"] = "local:b"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(MemoryError):
                store.read("local:a", "session")
            path.write_text('{"project_key":"local:a","project_key":"local:b"}', encoding="utf-8")
            with self.assertRaises(MemoryError):
                store.read("local:a", "session")

    def test_preparation_does_not_reset_remote_or_other_host_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionPointerStore(Path(directory))
            store.write(ControlPointer("local:a", "session", "other-host"))
            with self.assertRaises(MemoryError):
                store.prepare(ControlPointer("local:a", "session", "host"))
            confirmed = ControlPointer("local:a", "session", "host", session_handle="h", window_id="w", state="ready")
            store.write(confirmed)
            with self.assertRaises(MemoryError):
                store.prepare(ControlPointer("local:a", "session", "host"))
            self.assertEqual(store.read("local:a", "session"), confirmed)
