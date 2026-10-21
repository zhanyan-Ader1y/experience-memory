import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from experience_memory.application.services import MemoryApplicationService, RetentionApplicationService
from experience_memory.infrastructure.host_context import codex_request_host, current_host
from experience_memory.interfaces.mcp_server import create_server
from tests.fixtures.workflow import Workflow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=("a", "b"), required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    workflow = Workflow(session="session-a", project=args.project)
    other = workflow.other_session("session-b", project=args.project)
    try:
        seed_a = workflow.memory.write(
            workflow.handle, "upsert", workflow.candidate(
                body="synthetic session A experience", eid="EXP-CODEX-SESSION-A"), "seed-session-a")
        seed_b = other.memory.write(
            other.handle, "upsert", other.candidate(
                body="synthetic session B experience", eid="EXP-CODEX-SESSION-B"), "seed-session-b")
        ready = {
            "root": str(workflow.host.cwd.resolve(strict=True)),
            "host_id": workflow.host.host_id,
            "session_a": workflow.host.session_id,
            "session_b": other.host.session_id,
            "handle_a": workflow.handle,
            "handle_b": other.handle,
            "record_a": seed_a["record_id"],
            "record_b": seed_b["record_id"],
        }
        args.ready_file.write_text(json.dumps(ready), encoding="utf-8")

        memory = MemoryApplicationService(
            workflow.experiences, workflow.controls, None,
            binding=workflow.binding, host_source=current_host)
        retention = RetentionApplicationService(
            workflow.experiences, workflow.controls, None,
            binding=workflow.binding, host_source=current_host)
        server = create_server(
            memory, retention,
            host_context_manager=lambda meta: codex_request_host(
                meta, ready["host_id"], Path(ready["root"])))
        asyncio.run(server.run_stdio_async())
    finally:
        workflow.close()


if __name__ == "__main__":
    main()
