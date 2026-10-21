from dataclasses import asdict
import json
from pathlib import Path

from ..application.contracts import DiagnosticReport
from ..application.services import MemoryApplicationService

REQUIRED_RESOURCES = (
    "plugin.json", "mcp.json", "hooks/hooks.json", "hooks/adapter.py", "scripts/launch.py",
    "skills/memory-query/SKILL.md", "skills/memory-intake/SKILL.md", "examples/project-config.json",
    "prompts/core.md", "prompts/register.md", "prompts/restore.md", "prompts/compact-supplement.md",
)


def doctor(service: MemoryApplicationService, report: DiagnosticReport, root: Path | None) -> dict:
    report = service.diagnose_local(report)
    missing = [name for name in REQUIRED_RESOURCES if root is None or not (root / name).is_file()]
    result = asdict(report)
    result["adapters"] = dict(report.adapters)
    result["local_checks_passed"] = not missing
    result["missing_resources"] = missing
    result["capabilities"] = {name: "implemented_host_contract_unverified" for name in (
        "memory_search", "memory_read", "memory_write", "memory_use", "memory_status", "retention_hooks",
    )}
    result["runtime_checks_passed"] = bool(
        report.runtime_environment is not None and report.runtime_environment.get("ready") is True
    )
    return result
