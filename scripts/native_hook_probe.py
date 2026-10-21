"""Opt-in synthetic native hook; records metadata only, never transcripts/rules."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


def process(event, output_dir, nonce, payload_bytes=8000):
    if (type(event) is not dict or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce)
            or type(payload_bytes) is not int or not 1 <= payload_bytes <= 8192):
        raise ValueError("INVALID_PROBE_INPUT")
    name, session, cwd = event.get("hook_event_name"), event.get("session_id"), event.get("cwd")
    if name not in ("SessionStart", "PreCompact", "PostCompact"):
        raise ValueError("INVALID_PROBE_EVENT")
    if not isinstance(session, str) or not session or not isinstance(cwd, str) or not cwd:
        raise ValueError("INVALID_PROBE_IDENTITY")
    source, trigger = event.get("source"), event.get("trigger")
    if name == "SessionStart" and source not in ("startup", "resume", "clear", "compact"):
        raise ValueError("INVALID_PROBE_SOURCE")
    if name != "SessionStart" and trigger not in ("manual", "auto"):
        raise ValueError("INVALID_PROBE_TRIGGER")
    output = {"continue": True}
    context = ""
    invocation_id = uuid.uuid4().hex
    if name == "SessionStart":
        prefix = "EXPERIENCE_MEMORY_SYNTHETIC_PROBE:" + nonce + ":" + source + ":" + invocation_id + "\n"
        if len(prefix.encode("utf-8")) > payload_bytes:
            raise ValueError("INVALID_PROBE_BUDGET")
        context = prefix + "x" * (payload_bytes - len(prefix.encode("utf-8")))
        output["hookSpecificOutput"] = {"hookEventName": name, "additionalContext": context}
    audit = {"event": name, "session_id": session, "source": source, "trigger": trigger,
             "turn_id": event.get("turn_id") if isinstance(event.get("turn_id"), str) else None,
             "cwd_sha256": hashlib.sha256(cwd.encode()).hexdigest(),
             "process_cwd_sha256": hashlib.sha256(os.getcwd().encode()).hexdigest(),
             "nonce": nonce, "invocation_id": invocation_id, "time_ns": time.time_ns(),
             "payload_bytes": len(context.encode("utf-8")),
             "payload_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest()}
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / (invocation_id + ".json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(audit, stream)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--payload-bytes", type=int, default=8000)
    args = parser.parse_args()
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError()
        output = process(json.loads(raw), args.output_dir, args.nonce, args.payload_bytes)
    except Exception:
        output = {"continue": False, "stopReason": "EXPERIENCE_MEMORY_PROBE_FAILED"}
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
