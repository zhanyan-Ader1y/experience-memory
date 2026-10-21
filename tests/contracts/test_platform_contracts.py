"""Opt-in real Platform tests; skipped tests mean unverified, not passed."""

import json
import os
from pathlib import Path
import unittest
import uuid

from tests.fixtures.platform_probe import ProbeClient, ProbeFailure, load_credentials, validate_filter_evidence

LIVE = os.environ.get("EXPERIENCE_MEMORY_LIVE_CONTRACTS") == "1"


@unittest.skipUnless(LIVE, "real Mem0 contracts not enabled; unverified")
class PlatformContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = load_credentials()
        if not key:
            raise unittest.SkipTest("credentials unavailable; unverified")
        cls.run_id = uuid.uuid4().hex
        cls.a = ProbeClient(key, "experience-memory-contract-test", "contract-" + cls.run_id + "-a")
        cls.b = ProbeClient(key, "experience-memory-contract-test", "contract-" + cls.run_id + "-b")
        cls.fixture = json.loads((Path(__file__).parents[1] / "fixtures/synthetic_records.json").read_text(encoding="utf-8"))
        print("synthetic_probe_run=" + cls.run_id)

    def test_infer_false_roundtrip(self):
        ids = self.a.add(self.fixture["payload"], "control_checkpoint", immutable=True)
        self.assertEqual(len(ids), 1)
        saved = self.a.read(ids[0])
        self.assertEqual(saved["memory"], self.fixture["payload"])
        self.assertEqual(saved["metadata"]["record_kind"], "control_checkpoint")

    def test_immutable_control_record(self):
        original = "Synthetic sentinel control field alpha=original."
        ids = self.a.add(original, "control_immutable", immutable=True)
        self.a.add("Synthetic sentinel control field alpha=changed.", "synthetic_extraction", infer=True)
        saved = self.a.read(ids[0])
        self.assertEqual(saved["memory"], original)
        self.assertEqual(saved["metadata"]["record_kind"], "control_immutable")

    def test_control_filter_before_top_k(self):
        # Higher count of matching controls than requested result slots.
        valid_ids = set()
        for index in range(3):
            valid_ids.update(self.a.add(self.fixture["experience"] + " Experience variant " + str(index), "project_experience"))
        for index in range(6):
            self.a.add(self.fixture["query"] + " control " + str(index), "control_noise", immutable=True)
        baseline = self.a.search(self.fixture["query"], limit=3, experiences_only=False)
        result = self.a.search(self.fixture["query"], limit=3)
        validate_filter_evidence([row["id"] for row in baseline], [row["id"] for row in result], valid_ids, 3)
        self.assertEqual(len(result), 3)
        for row in result:
            saved = self.a.read(row["id"])
            self.assertEqual(saved["metadata"]["record_kind"], "project_experience")

    def test_id_read_preserves_scope(self):
        ids = self.b.add("Synthetic project B identity sentinel.", "control_scope", immutable=True)
        saved = self.b.read(ids[0])
        self.assertEqual(saved["app_id"], "experience-memory-contract-test")
        self.assertEqual(saved["agent_id"], "contract-" + self.run_id + "-b")
        with self.assertRaises(ProbeFailure) as ctx:
            self.a.read(ids[0])
        self.assertIn("SCOPE_MISMATCH", str(ctx.exception))
