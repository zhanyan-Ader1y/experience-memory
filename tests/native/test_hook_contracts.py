"""Validate a reviewed native evidence bundle, never synthetic stdin as native proof."""

import json
import os
from pathlib import Path
import unittest

from tests.fixtures.native_evidence import validate_compaction_evidence


@unittest.skipUnless(os.environ.get("EXPERIENCE_MEMORY_NATIVE_EVIDENCE"),
                     "native host evidence not supplied; unverified")
class NativeHookContractTests(unittest.TestCase):
    def test_manual_and_auto_compaction_reach_immediate_model_request(self):
        bundle = json.loads(Path(os.environ["EXPERIENCE_MEMORY_NATIVE_EVIDENCE"]).read_text(encoding="utf-8"))
        validate_compaction_evidence(bundle)

    def test_binding_source_is_verified_not_a_user_supplied_handle(self):
        bundle = json.loads(Path(os.environ["EXPERIENCE_MEMORY_NATIVE_EVIDENCE"]).read_text(encoding="utf-8"))
        self.assertIs(bundle["mcp_workspace_source_verified"], True)
        self.assertIs(bundle["cross_workspace_handle_rejected"], True)
