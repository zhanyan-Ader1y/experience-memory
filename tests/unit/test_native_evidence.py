"""Synthetic evidence shapes test the validator only, never native host behavior."""

import copy
import unittest

from tests.fixtures.native_evidence import validate_compaction_evidence


def synthetic_bundle():
    def case(index, trigger):
        before_id, after_id = f"{index * 2:032x}", f"{index * 2 + 1:032x}"
        common = {"session_id": "same-session", "nonce": "test-run", "cwd_sha256": "c" * 64}
        return {
            "precompact": dict(common, event="PreCompact", trigger=trigger, invocation_id=before_id),
            "compact_start": dict(common, event="SessionStart", source="compact",
                                  invocation_id=after_id, payload_sha256=f"{index:064x}", payload_bytes=8192),
            "immediate_model_request": {"sentinel_sha256": f"{index:064x}", "invocation_id": after_id},
            "boundary_link_verified": True, "workspace_source_verified": True, "hook_output_spilled": False,
        }
    return {"source": "reviewed_native_host_capture", "host_version": "synthetic-test-only",
            "trusted_hook_definition_sha256": "d" * 64, "run_nonce": "test-run",
            "cases": {"manual": case(1, "manual"), "auto": case(2, "auto")},
            "consecutive": [case(3, "manual"), case(4, "manual")]}


class NativeEvidenceTests(unittest.TestCase):
    def test_consistent_synthetic_shape_is_accepted(self):
        validate_compaction_evidence(synthetic_bundle())

    def test_replayed_or_unlinked_events_are_rejected(self):
        for change in ("replay", "old_hash", "request_id", "request_hash", "nonce", "session", "cwd", "no_sequence"):
            with self.subTest(change=change):
                bundle = synthetic_bundle()
                last = bundle["consecutive"][1]
                if change == "replay":
                    bundle["consecutive"][1] = copy.deepcopy(bundle["consecutive"][0])
                elif change == "old_hash":
                    previous_hash = bundle["consecutive"][0]["compact_start"]["payload_sha256"]
                    last["compact_start"]["payload_sha256"] = previous_hash
                    last["immediate_model_request"]["sentinel_sha256"] = previous_hash
                elif change == "request_id":
                    last["immediate_model_request"]["invocation_id"] = "f" * 32
                elif change == "request_hash":
                    last["immediate_model_request"]["sentinel_sha256"] = "f" * 64
                elif change == "nonce":
                    last["compact_start"]["nonce"] = "previous-run"
                elif change == "session":
                    last["precompact"]["session_id"] = last["compact_start"]["session_id"] = "other-session"
                elif change == "cwd":
                    last["compact_start"]["cwd_sha256"] = "f" * 64
                else:
                    del bundle["consecutive"]
                with self.assertRaises(ValueError):
                    validate_compaction_evidence(bundle)

    def test_boolean_and_budget_claims_are_checked_strictly(self):
        for field, value in (("boundary_link_verified", "true"), ("workspace_source_verified", 1),
                             ("hook_output_spilled", 0), ("payload_bytes", True),
                             ("payload_bytes", 0), ("payload_bytes", 8193)):
            with self.subTest(field=field, value=value):
                bundle = synthetic_bundle()
                target = bundle["cases"]["manual"]
                if field == "payload_bytes":
                    target = target["compact_start"]
                target[field] = value
                with self.assertRaises(ValueError):
                    validate_compaction_evidence(bundle)
