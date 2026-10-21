"""Check reviewed evidence consistency; cannot authenticate host captures."""

import re


def validate_compaction_evidence(bundle):
    def require(condition):
        if not condition:
            raise ValueError("INVALID_NATIVE_EVIDENCE")

    def hex_value(value, length):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None

    try:
        require(bundle["source"] == "reviewed_native_host_capture")
        require(isinstance(bundle["host_version"], str) and bool(bundle["host_version"].strip()))
        require(hex_value(bundle["trusted_hook_definition_sha256"], 64))
        nonce = bundle["run_nonce"]
        require(isinstance(nonce, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce) is not None)
        invocation_ids, payload_hashes = set(), set()

        def check_case(case, trigger):
            before, after = case["precompact"], case["compact_start"]
            require(before["event"] == "PreCompact" and before["trigger"] == trigger)
            require(after["event"] == "SessionStart" and after["source"] == "compact")
            require(isinstance(before["session_id"], str) and bool(before["session_id"]))
            require(before["session_id"] == after["session_id"])
            require(hex_value(before["cwd_sha256"], 64) and before["cwd_sha256"] == after["cwd_sha256"])
            for row in (before, after):
                require(row["nonce"] == nonce)
                invocation_id = row["invocation_id"]
                require(hex_value(invocation_id, 32) and invocation_id not in invocation_ids)
                invocation_ids.add(invocation_id)
            require(case["boundary_link_verified"] is True)
            require(case["workspace_source_verified"] is True)
            require(case["hook_output_spilled"] is False)
            require(type(after["payload_bytes"]) is int and 1 <= after["payload_bytes"] <= 8192)
            digest = after["payload_sha256"]
            require(hex_value(digest, 64) and digest not in payload_hashes)
            payload_hashes.add(digest)
            request = case["immediate_model_request"]
            require(request["sentinel_sha256"] == digest)
            require(request["invocation_id"] == after["invocation_id"])

        for trigger in ("manual", "auto"):
            check_case(bundle["cases"][trigger], trigger)
        consecutive = bundle["consecutive"]
        require(type(consecutive) is list and len(consecutive) == 2)
        for case in consecutive:
            check_case(case, "manual")
        first, second = (case["compact_start"] for case in consecutive)
        require(first["session_id"] == second["session_id"] and first["cwd_sha256"] == second["cwd_sha256"])
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("INVALID_NATIVE_EVIDENCE") from error
