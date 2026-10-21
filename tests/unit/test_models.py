import unittest

from experience_memory.domain.models import ActiveWindow, Checkpoint, Priority, ReadReceipt, Scope, WindowEntry


class ModelBoundaryTests(unittest.TestCase):
    def test_unknown_priority_is_rejected(self):
        for value in ("P0", "p1", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Priority(value)

    def test_window_rejects_untyped_priority_and_boolean_sequence(self):
        for priority, seq in (("P1", 1), (Priority.P1, True), (Priority.P2, -1)):
            with self.subTest(priority=priority, seq=seq), self.assertRaises(ValueError):
                WindowEntry("r-1", "EXP-SAMPLE-1", priority, seq, "digest")

    def test_marker_identifier_must_be_safe_ascii(self):
        for value in ("EXP-ID|U9", "中文", " ", "../experience", "EXP\nID"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WindowEntry("r-1", value, Priority.P1, 1, "digest")

    def test_scope_rejects_wildcard_identities(self):
        for value in ("", "*", "project?", "with\nline"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Scope(value, "app", "project", (), "user")

    def test_window_rejects_boolean_cursor_and_mutable_entries(self):
        entry = WindowEntry("r-1", "EXP-SAMPLE-1", Priority.P1, 8, "digest")
        for cursor, entries in ((True, (entry,)), (8, [entry]), (7, (entry,))):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                ActiveWindow("w-1", "h-1", 0, cursor, entries, None)

    def test_receipt_rejects_untyped_and_unsafe_identifiers(self):
        for values in ((None, True, [], {}, 0), ("rec", "r", "EXP|ID", "digest", "h"),
                       ("rec", "r", "EXP-ID", "", "h")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ReadReceipt(*values)

    def test_checkpoint_rejects_mutability_booleans_and_unknown_schema(self):
        for values in ((None, None, True, None, [], False, 999),
                       ("cp", "h", 0, "w", [], 8192, 1),
                       ("cp", "h", True, "w", (), 8192, 1),
                       ("cp", "h", 0, "w", (), False, 1),
                       ("cp", "h", 0, "w", (), 8193, 1),
                       ("cp", "h", 0, "w", (), 8192, True),
                       ("cp", "h", 0, "w", (), 8192, 999)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Checkpoint(*values)

    def test_parent_window_identifier_cannot_be_mutable_or_empty(self):
        for parent in ([], "", True):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                ActiveWindow("w", "h", 0, 0, (), parent)
