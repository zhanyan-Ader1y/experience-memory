"""Prepare passive C1 review material. Never launches Codex or installs config."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experience_memory.domain.errors import MemoryError
from experience_memory.infrastructure.environment import inspect_environment
from tests.fixtures.codex_c1_projects import make_projects
from tests.fixtures.codex_c1_server import checked_root

# Invocation-only proposals, not global settings. Effective config still needs native preflight.
OVERRIDES = [
    "features.plugins=false", "features.memories=false", "features.apps=false",
    "features.hooks=false", "features.multi_agent=false", "features.browser_use=false",
    "features.computer_use=false", "features.shell_tool=false", "features.unified_exec=false",
    "features.skill_search=false", "features.skill_mcp_dependency_install=false",
    "features.workspace_dependencies=false", "mcp_servers.node_repl.enabled=false",
    "mcp_servers.apifox-2.enabled=false", 'web_search="disabled"',
    "features.image_generation=false", "features.shell_snapshot=false",
    "features.unbounded_connection_retries=false",
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_hashes():
    files = [ROOT / name for name in ("pyproject.toml", "plugin.json", "mcp.json")]
    for name in ("src", "tests", "scripts", "hooks", "prompts"):
        files.extend(path for path in (ROOT / name).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in (".py", ".json", ".md"))
    return {path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in sorted(files)}


def preview(bundle, project, nonce):
    other = "b" if project == "a" else "a"
    arguments = ["-I", (ROOT / "tests/fixtures/codex_c1_server.py").as_posix(),
                 "--project", project, "--project-root", (bundle / "projects" / project).as_posix(),
                 "--other-project-root", (bundle / "projects" / other).as_posix(),
                 "--run-nonce", nonce, "--audit-dir", (bundle / "audit" / project).as_posix(),
                 "--bounded-scenario"]
    text = ("# REVIEW ONLY: not installed; requires separate native authorization and preflight.\n"
            "[mcp_servers.experience_memory_c1_wiring]\n"
            "command = " + json.dumps(Path(sys.executable).as_posix()) + "\n"
            "args = " + json.dumps(arguments) + "\n"
            "enabled = true\nstartup_timeout_sec = 15\ntool_timeout_sec = 15\n")
    return text.encode("utf-8")


def manifest_for(bundle, nonce):
    projects = {}
    for project in ("a", "b"):
        root = checked_root(bundle / "projects" / project, project, nonce)
        projects[project] = {
            "root": str(root),
            "marker_sha256": digest((root / ".codex/c1-fixture.json").read_bytes()),
            "config_preview": project + "-config.preview.toml",
            "config_preview_sha256": digest(preview(bundle, project, nonce)),
            "expected_cwd_sha256": digest(str(root).encode("utf-8")),
            "tool_sequence": [
                {"tool": "c1_bootstrap", "arguments": {"probe_nonce": nonce}, "expected": "ready"},
                {"tool": "memory_status", "handle_from": "session_handle", "expected": "succeeded_u0"},
                {"tool": "memory_status", "handle_from": "prior_session.session_handle", "expected": "SESSION_INVALID"},
                {"tool": "memory_status", "handle_from": "foreign_project.session_handle", "expected": "SESSION_INVALID"},
                {"tool": "memory_status", "handle_from": "session_handle", "expected": "succeeded_u0"},
            ],
            "prompt": (
                "This is a synthetic C1 authorization test using only the in-memory fixture. "
                "Use only tools on experience_memory_c1_wiring. Call c1_bootstrap once with probe_nonce="
                + json.dumps(nonce) + ". From its result use the returned handles, never invent identity metadata. "
                "Then call memory_status exactly four times, in order: session_handle (success, U=0), "
                "prior_session.session_handle (expected SESSION_INVALID), foreign_project.session_handle "
                "(expected SESSION_INVALID), session_handle again (success, U=0). "
                "The two expected rejections are not reasons to retry. Stop if any other result occurs. "
                "Do not call any other tools, read files, run shell, compact, or start subagents. "
                "Finish with C1_WIRING_DONE; do not repeat handles in the final reply."
            ),
        }
    return {
        "schema_version": 2, "mode": "review_only", "run_nonce": nonce,
        "target_codex_version": "0.163.0", "host_version_observed": False,
        "native_authorization": {"granted": False, "remaining_model_tasks": 0},
        "runtime_environment": inspect_environment(),
        "limits_enforcement": "bounded_offline_executor_native_blocked",
        "executor": {"cli": "scripts/run_codex_c1.py", "default_action": "dry-run",
                     "native_dispatch_supported": False,
                     "native_dispatch_blocked_reason": "C1_NATIVE_EXEC_APPROVAL_UNSUPPORTED",
                     "simulation_origin": "trusted_programmatic_tests_only"},
        "proposal": {"max_model_task_attempts": 2, "max_attempts_per_project": 1,
                     "max_tool_calls_per_task": 5, "max_tool_calls_total": 10,
                     "task_timeout_seconds": 180, "automatic_retry": False,
                     "allowed_tools": ["c1_bootstrap", "memory_status"]},
        "fixture_exposes": ["memory_search", "memory_read", "memory_write", "memory_use", "memory_status", "c1_bootstrap"],
        "backend": "in_memory_platform_double", "seed_origin": "synthetic_not_prior_native_sessions",
        "production_identity_enabled": False, "live_mem0_allowed": False,
        "configuration_installed": False, "trust_modified": False,
        "invocation_only_overrides": OVERRIDES,
        "required_native_preflight": [
            "Obtain explicit authorization for this run, task count and synthetic roots; this file grants none.",
            "Review an approval-capable native transport; pinned exec overrides on-request to Never and is blocked.",
            "Recheck exact Codex 0.163.0; stop on version drift, never upgrade/downgrade automatically.",
            "Verify source, marker and preview bytes, pinned Python/MCP, project trust via normal host review.",
            "Install only the reviewed synthetic config after authorization; preserve the preview byte hash.",
            "Inspect effective config: only the synthetic MCP server; no enabled hooks, old plugins or unrelated tools.",
            "Remove MEM0_API_KEY from child env; never read credentials. Check ordinary usage is allowed; no fallback.",
            "Use normal on-request approval and read-only sandbox, ephemeral tasks, no trust or permission bypass.",
        ],
        "stop_conditions": [
            "Missing authorization, changed version/hash/marker, untrusted project, or unexpected effective config.",
            "Login/quota/approval failure or task timeout: preserve evidence, stop; no retry or account/model fallback.",
            "Any tool outside the two-tool allowlist, more than five unique calls, wrong order or repeated bootstrap.",
            "Unexpected error/success, missing audit, identity/root mismatch, or U changing from zero.",
            "Any need for real Mem0, compaction, normal-project activation, trust edits or production CLI changes.",
        ],
        "evidence_required": [
            "Native thread.started ID and completion/exit result; exactly five unique MCP calls in proposed order.",
            "Five ID-only transport audit rows correlated to the native thread and fixed-root hash.",
            "Sanitized real tool outcomes including both SESSION_INVALID results and final U=0.",
            "Owner-valid seed proofs; label them synthetic, not handles from prior native sessions.",
            "Preflight source/config hashes and per-attempt reports; preserve failure/unknown evidence.",
        ],
        "not_proven": ["C1 overall", "C2", "production activation", "prior-native-session handle reuse",
                       "arbitrary-client authentication", "Hook context delivery"],
        "projects": projects, "source_sha256": source_hashes(),
    }


def prepare(bundle, nonce):
    if not bundle.is_absolute() or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", nonce):
        raise ValueError("absolute fresh output and safe run nonce required")
    if not inspect_environment()["ready"]:
        raise ValueError("pinned Python/MCP environment required")
    bundle = bundle.resolve()
    bundle.mkdir(parents=True, exist_ok=False)
    make_projects(bundle / "projects", nonce)
    for project in ("a", "b"):
        (bundle / (project + "-config.preview.toml")).write_bytes(preview(bundle, project, nonce))
    manifest = manifest_for(bundle, nonce)
    # Hash the bytes actually written, not newline-translated input text.
    for project in ("a", "b"):
        manifest["projects"][project]["config_preview_sha256"] = digest(
            (bundle / (project + "-config.preview.toml")).read_bytes())
    with (bundle / "launch-review.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
    return {"status": "review_prepared", "manifest": str(bundle / "launch-review.json"),
            "native_model_tasks_started": 0, "configuration_installed": False}


def path_entry_exists(path: Path) -> bool:
    """Return true for any directory entry, including dangling links/reparse points."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _is_reparse(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def verify(bundle):
    if not bundle.is_absolute():
        raise ValueError("absolute review directory required")
    bundle = bundle.resolve(strict=True)
    manifest = json.loads((bundle / "launch-review.json").read_text(encoding="utf-8"))
    nonce = manifest["run_nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", nonce):
        raise ValueError("invalid run nonce")
    if manifest != manifest_for(bundle, nonce):
        raise ValueError("review/source/environment changed")
    for project in ("a", "b"):
        if (bundle / (project + "-config.preview.toml")).read_bytes() != preview(bundle, project, nonce):
            raise ValueError("configuration preview changed")
        root = bundle / "projects" / project
        codex_directory = root / ".codex"
        metadata = codex_directory.lstat()
        if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("synthetic .codex directory changed")
        if (path_entry_exists(codex_directory / "config.toml")
                or path_entry_exists(codex_directory / "hooks.json")):
            raise ValueError("review-only bundle must remain uninstalled")
    return {"status": "review_unchanged", "native_authorization_granted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    parser.add_argument("--run-nonce", default=None)
    args = parser.parse_args()
    try:
        if args.verify:
            if args.run_nonce is not None:
                raise ValueError("verification does not change the run nonce")
            result = verify(args.verify)
        else:
            nonce = args.run_nonce if args.run_nonce is not None else "c1-" + uuid.uuid4().hex
            result = prepare(args.output, nonce)
    except (OSError, ValueError, KeyError, TypeError, MemoryError):
        print(json.dumps({"status": "failed", "error_code": "C1_REVIEW_INVALID_OR_CHANGED"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
