"""Read-only, bounded Codex 0.163.0 preflight for offline C1 bundles.

This module never starts a thread, turn, tool call, or model task. The caller
supplies the executable command; bundle content is never used as an executable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from scripts.codex_c1_bundle import ReviewSnapshot, load_snapshot


TARGET_VERSION = "codex-cli 0.163.0"
RPC_ALLOWLIST = frozenset({
    "initialize", "initialized", "config/read", "hooks/list", "account/rateLimits/read",
})
MAX_RPC_LINE_BYTES = 262_144
MAX_PREFLIGHT_RECEIVED_BYTES = 2_097_152
MAX_VERSION_LINE_BYTES = 8_192
SERVER_NAME = "experience_memory_c1_wiring"
DISABLED_FEATURES = frozenset({
    "plugins", "memories", "apps", "hooks", "multi_agent", "browser_use", "computer_use",
    "shell_tool", "unified_exec", "skill_search", "skill_mcp_dependency_install",
    "workspace_dependencies", "image_generation", "shell_snapshot", "unbounded_connection_retries",
})
EXPECTED_OVERRIDES = {
    **{"features." + name: False for name in DISABLED_FEATURES},
    "mcp_servers.node_repl.enabled": False,
    "mcp_servers.apifox-2.enabled": False,
    "web_search": "disabled",
}
CLEANUP_RESERVE_SECONDS = 1.0


class _ProbeFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str):
    raise ValueError(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ProbeFailure("C1_RPC_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise _ProbeFailure("C1_RPC_INVALID_JSON")


def _parse_json(raw: bytes):
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)
    except _ProbeFailure:
        raise
    except (UnicodeError, ValueError, TypeError):
        raise _ProbeFailure("C1_RPC_INVALID_JSON") from None


def _canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _parse_generated_preview(preview_bytes: bytes) -> dict:
    """Parse only the review exporter's fixed seven-line MCP preview format."""
    try:
        lines = preview_bytes.decode("utf-8", errors="strict").splitlines()
        if (len(lines) != 7
                or lines[0] != "# REVIEW ONLY: not installed; requires separate native authorization and preflight."
                or lines[1] != "[mcp_servers." + SERVER_NAME + "]"):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        values = {}
        for line in lines[2:]:
            key, separator, value = line.partition(" = ")
            if not separator or key in values:
                _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
            values[key] = value
        if set(values) != {"command", "args", "enabled", "startup_timeout_sec", "tool_timeout_sec"}:
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        command = json.loads(values["command"])
        args = json.loads(values["args"])
        if (type(command) is not str or type(args) is not list
                or any(type(arg) is not str for arg in args)
                or values["enabled"] != "true"
                or not re.fullmatch(r"[1-9][0-9]*", values["startup_timeout_sec"])
                or not re.fullmatch(r"[1-9][0-9]*", values["tool_timeout_sec"])):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        return {
            "command": command,
            "args": args,
            "enabled": True,
            "startup_timeout_sec": int(values["startup_timeout_sec"]),
            "tool_timeout_sec": int(values["tool_timeout_sec"]),
        }
    except (ValueError, TypeError, UnicodeError):
        _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")


def _snapshot_inputs(snapshot: ReviewSnapshot, project: str):
    if not isinstance(snapshot, ReviewSnapshot) or type(project) is not str or project not in ("a", "b"):
        _reject("C1_PREFLIGHT_INVALID_INPUT")
    try:
        current = load_snapshot(snapshot.bundle)
        if (current.manifest_sha256 != snapshot.manifest_sha256
                or current.run_nonce != snapshot.run_nonce
                or current.manifest != snapshot.manifest):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        manifest = current.manifest
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest.get("schema_version") != 2 or manifest.get("mode") != "review_only"
                or manifest.get("target_codex_version") != "0.163.0"):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        projects = manifest.get("projects")
        project_info = projects.get(project) if isinstance(projects, dict) else None
        if not isinstance(project_info, dict) or type(project_info.get("root")) is not str:
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        root = Path(project_info["root"]).resolve(strict=True)
        expected_root = (current.bundle / "projects" / project).resolve(strict=True)
        if root != expected_root or not root.is_dir():
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        preview_name = project_info.get("config_preview")
        if type(preview_name) is not str or preview_name != project + "-config.preview.toml":
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        preview_path = current.bundle / preview_name
        preview_bytes = preview_path.read_bytes()
        if hashlib.sha256(preview_bytes).hexdigest() != project_info.get("config_preview_sha256"):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        expected_server = _parse_generated_preview(preview_bytes)
        if set(expected_server) != {"command", "args", "enabled", "startup_timeout_sec", "tool_timeout_sec"}:
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        overrides = manifest.get("invocation_only_overrides")
        if not isinstance(overrides, list) or not overrides or any(type(item) is not str for item in overrides):
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        return current, manifest, project_info, root, expected_server, overrides
    except ValueError as error:
        if str(error).startswith("C1_"):
            raise
        _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
    except (OSError, UnicodeError, TypeError, KeyError, AttributeError):
        _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")


def _nested_value(mapping, dotted_path):
    current = mapping
    for component in dotted_path.split("."):
        if type(current) is not dict or component not in current:
            return False, None
        current = current[component]
    return True, current


def _expected_overrides(overrides):
    expected = {}
    for raw in overrides:
        if type(raw) is not str or "=" not in raw:
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        path, encoded = raw.split("=", 1)
        if path not in EXPECTED_OVERRIDES or path in expected:
            _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        pinned = EXPECTED_OVERRIDES[path]
        if type(pinned) is bool:
            if encoded != ("true" if pinned else "false"):
                _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
            value = encoded == "true"
        else:
            try:
                value = json.loads(encoded)
            except (ValueError, TypeError):
                _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
            if type(value) is not str or value != pinned or json.dumps(value) != encoded:
                _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
        expected[path] = value
    if expected != EXPECTED_OVERRIDES:
        _reject("C1_PREFLIGHT_SNAPSHOT_MISMATCH")
    return expected


def _validate_effective_config(config, expected_server, overrides):
    if type(config) is not dict:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    if config.get("approval_policy") != "on-request" or config.get("sandbox_mode") != "read-only":
        _reject("C1_PREFLIGHT_PERMISSION_MISMATCH")
    features = config.get("features")
    if type(features) is not dict or set(features) != DISABLED_FEATURES:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    if any(type(value) is not bool or value is not False for value in features.values()):
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    mcp_servers = config.get("mcp_servers")
    if type(mcp_servers) is not dict or SERVER_NAME not in mcp_servers:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    actual_server = mcp_servers[SERVER_NAME]
    if type(actual_server) is not dict or actual_server != expected_server:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    if type(actual_server.get("enabled")) is not bool or actual_server["enabled"] is not True:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    for name, server in mcp_servers.items():
        if type(name) is not str or type(server) is not dict:
            _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
        if name != SERVER_NAME:
            enabled = server.get("enabled", True)
            if type(enabled) is not bool or enabled is not False:
                _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    plugins = config.get("plugins", {})
    if type(plugins) is not dict:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    for plugin in plugins.values():
        if type(plugin) is not dict or type(plugin.get("enabled")) is not bool or plugin["enabled"] is not False:
            _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    expected_values = _expected_overrides(overrides)
    for path, expected in expected_values.items():
        present, actual = _nested_value(config, path)
        if not present or type(actual) is not type(expected) or actual != expected:
            _reject("C1_PREFLIGHT_CONFIG_MISMATCH")


def _validate_initialize_response(result):
    required = {"codexHome", "platformFamily", "platformOs", "userAgent"}
    if type(result) is not dict or set(result) != required:
        raise _ProbeFailure("C1_RPC_INVALID_INITIALIZE_RESPONSE")
    if (type(result["codexHome"]) is not str or not Path(result["codexHome"]).is_absolute()
            or any(type(result[key]) is not str or not result[key]
                   for key in ("platformFamily", "platformOs", "userAgent"))):
        raise _ProbeFailure("C1_RPC_INVALID_INITIALIZE_RESPONSE")


def _validate_rate_limit_snapshot(snapshot):
    fields = {"credits", "individualLimit", "limitId", "limitName", "normalModelSlug", "planType",
              "primary", "rateLimitReachedType", "secondary", "spendControlReached"}
    plan_types = {"free", "go", "plus", "pro", "prolite", "promax", "team",
                  "self_serve_business_prolite", "self_serve_business_usage_based", "business", "ent26",
                  "enterprise_cbp_automation", "enterprise_cbp_usage_based", "enterprise", "edu",
                  "edu_plus", "edu_pro", "unknown"}
    reached_types = {"rate_limit_reached", "workspace_owner_credits_depleted", "workspace_member_credits_depleted",
                     "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached"}
    if type(snapshot) is not dict or set(snapshot) - fields:
        raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    for key in ("limitId", "limitName", "normalModelSlug"):
        if key in snapshot and snapshot[key] is not None and type(snapshot[key]) is not str:
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    if "planType" in snapshot and snapshot["planType"] is not None:
        if type(snapshot["planType"]) is not str or snapshot["planType"] not in plan_types:
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    if "rateLimitReachedType" in snapshot and snapshot["rateLimitReachedType"] is not None:
        if (type(snapshot["rateLimitReachedType"]) is not str
                or snapshot["rateLimitReachedType"] not in reached_types):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    if "spendControlReached" in snapshot and snapshot["spendControlReached"] is not None:
        if type(snapshot["spendControlReached"]) is not bool:
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    credits = snapshot.get("credits")
    if credits is not None:
        if (type(credits) is not dict or set(credits) - {"balance", "hasCredits", "unlimited"}
                or not {"hasCredits", "unlimited"}.issubset(credits)
                or type(credits["hasCredits"]) is not bool or type(credits["unlimited"]) is not bool
                or ("balance" in credits and credits["balance"] is not None and type(credits["balance"]) is not str)):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    individual = snapshot.get("individualLimit")
    if individual is not None:
        if (type(individual) is not dict
                or set(individual) != {"limit", "remainingPercent", "resetsAt", "used"}
                or type(individual["limit"]) is not str or type(individual["used"]) is not str
                or type(individual["remainingPercent"]) is not int or type(individual["resetsAt"]) is not int):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    for window_key in ("primary", "secondary"):
        window = snapshot.get(window_key)
        if window is not None:
            if (type(window) is not dict or set(window) - {"resetsAt", "usedPercent", "windowDurationMins"}
                    or "usedPercent" not in window or type(window["usedPercent"]) is not int):
                raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
            for field in ("resetsAt", "windowDurationMins"):
                if field in window and window[field] is not None and type(window[field]) is not int:
                    raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")


def _validate_usage_response(usage):
    known_fields = {"accountId", "ordinaryUsageAllowed", "rateLimitResetCredits", "rateLimitUpsell",
                    "rateLimits", "rateLimitsByLimitId"}
    if type(usage) is not dict or set(usage) - known_fields or "rateLimits" not in usage:
        raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    if "accountId" in usage and usage["accountId"] is not None and type(usage["accountId"]) is not str:
        raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
    if (type(usage.get("ordinaryUsageAllowed")) is not bool
            or usage["ordinaryUsageAllowed"] is not True):
        raise _ProbeFailure("C1_PREFLIGHT_USAGE_NOT_ALLOWED")
    _validate_rate_limit_snapshot(usage["rateLimits"])
    by_limit = usage.get("rateLimitsByLimitId")
    if by_limit is not None:
        if type(by_limit) is not dict or any(type(key) is not str for key in by_limit):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
        for value in by_limit.values():
            _validate_rate_limit_snapshot(value)
    reset_summary = usage.get("rateLimitResetCredits")
    if reset_summary is not None:
        if (type(reset_summary) is not dict or set(reset_summary) - {"availableCount", "credits"}
                or type(reset_summary.get("availableCount")) is not int):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
        reset_credits = reset_summary.get("credits")
        if reset_credits not in (None, []):
            raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")


def _same_path(value, expected):
    if type(value) is not str:
        return False
    try:
        return Path(value).resolve(strict=True) == expected
    except (OSError, ValueError):
        return False


def _validate_layers(layers, project_root, expected_server):
    if type(layers) is not list:
        _reject("C1_PREFLIGHT_LAYER_MISMATCH")
    project_layers = []
    known_types = {
        "packagedDefaults", "mdm", "system", "enterpriseManaged", "user", "project",
        "sessionFlags", "legacyManagedConfigTomlFromFile", "legacyManagedConfigTomlFromMdm",
    }
    for layer in layers:
        if type(layer) is not dict or type(layer.get("name")) is not dict or type(layer.get("version")) is not str:
            _reject("C1_PREFLIGHT_LAYER_MISMATCH")
        name = layer["name"]
        source_type = name.get("type")
        if type(source_type) is not str or source_type not in known_types:
            _reject("C1_PREFLIGHT_LAYER_MISMATCH")
        disabled_reason = layer.get("disabledReason")
        if disabled_reason is not None and type(disabled_reason) is not str:
            _reject("C1_PREFLIGHT_LAYER_MISMATCH")
        config = layer.get("config")
        if type(config) is not dict:
            _reject("C1_PREFLIGHT_LAYER_MISMATCH")
        if source_type == "project":
            dotcodex = name.get("dotCodexFolder")
            if type(dotcodex) is not str:
                _reject("C1_PREFLIGHT_LAYER_MISMATCH")
            expected_layer_root = (project_root / ".codex").resolve(strict=True)
            if not _same_path(dotcodex, expected_layer_root):
                _reject("C1_PREFLIGHT_LAYER_MISMATCH")
            project_layers.append(layer)
    if len(project_layers) != 1:
        _reject("C1_PREFLIGHT_LAYER_MISMATCH")
    layer = project_layers[0]
    if layer.get("disabledReason") is not None:
        _reject("C1_PREFLIGHT_PROJECT_DISABLED")
    project_config = layer.get("config")
    project_servers = project_config.get("mcp_servers")
    if (type(project_servers) is not dict or set(project_servers) != {SERVER_NAME}
            or type(project_servers.get(SERVER_NAME)) is not dict
            or project_servers[SERVER_NAME] != expected_server):
        _reject("C1_PREFLIGHT_LAYER_MISMATCH")


def _validate_hooks(hooks, project_root):
    if type(hooks) is not dict or set(hooks) != {"data"} or type(hooks.get("data")) is not list:
        _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
    if len(hooks["data"]) != 1:
        _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
    entry = hooks["data"][0]
    if type(entry) is not dict or set(entry) != {"cwd", "errors", "hooks", "warnings"}:
        _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
    if (not _same_path(entry.get("cwd"), project_root)
            or type(entry.get("errors")) is not list or entry["errors"]
            or type(entry.get("warnings")) is not list or entry["warnings"]
            or type(entry.get("hooks")) is not list):
        _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
    required = {"currentHash", "displayOrder", "enabled", "eventName", "isManaged",
                "key", "source", "sourcePath", "timeoutSec", "trustStatus"}
    event_names = {"preToolUse", "permissionRequest", "postToolUse", "preCompact", "postCompact",
                   "sessionStart", "sessionEnd", "userPromptSubmit", "subagentStart", "subagentStop",
                   "stop", "interrupt"}
    trust_states = {"managed", "untrusted", "trusted", "modified"}
    hook_sources = {"system", "user", "project", "mdm", "sessionFlags", "plugin",
                    "cloudRequirements", "cloudManagedConfig", "legacyManagedConfigFile",
                    "legacyManagedConfigMdm", "unknown"}
    for hook in entry["hooks"]:
        if type(hook) is not dict or not required.issubset(hook):
            _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
        if (type(hook["enabled"]) is not bool or type(hook["isManaged"]) is not bool
                or type(hook["displayOrder"]) is not int or type(hook["timeoutSec"]) is not int
                or type(hook["currentHash"]) is not str or type(hook["key"]) is not str
                or type(hook["sourcePath"]) is not str or type(hook["eventName"]) is not str
                or type(hook["source"]) is not str or type(hook["trustStatus"]) is not str
                or hook["eventName"] not in event_names or hook["source"] not in hook_sources
                or hook["trustStatus"] not in trust_states):
            _reject("C1_PREFLIGHT_HOOKS_MISMATCH")
        if hook["enabled"]:
            _reject("C1_PREFLIGHT_HOOK_ENABLED")


def validate_preflight(snapshot: ReviewSnapshot, project: str, *, version: str,
                       effective_config: dict, layers: list[dict], hooks: dict, usage: dict) -> dict:
    """Validate one synthetic project and return only sanitized readiness metadata."""
    current, manifest, project_info, root, expected_server, _overrides = _snapshot_inputs(snapshot, project)
    if type(version) is not str or version != TARGET_VERSION:
        _reject("C1_PREFLIGHT_VERSION_MISMATCH")
    _validate_effective_config(effective_config, expected_server, _overrides)
    _validate_layers(layers, root, expected_server)
    _validate_hooks(hooks, root)
    try:
        _validate_usage_response(usage)
    except _ProbeFailure as error:
        _reject(error.code)
    command = expected_server.get("command")
    args = expected_server.get("args")
    if type(command) is not str or type(args) is not list or any(type(arg) is not str for arg in args):
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    if type(expected_server.get("startup_timeout_sec")) is not int or type(expected_server.get("tool_timeout_sec")) is not int:
        _reject("C1_PREFLIGHT_CONFIG_MISMATCH")
    root_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return {
        "state": "preflight_ready",
        "project": project,
        "root_sha256": root_hash,
        "version": TARGET_VERSION,
        "checks": {
            "version": True,
            "effective_config": True,
            "project_layer": True,
            "hooks_disabled": True,
            "usage_allowed": True,
        },
        "mcp_server": {
            "name": SERVER_NAME,
            "command_basename": Path(command).name,
            "args_count": len(args),
            "args_sha256": hashlib.sha256(_canonical_json(args)).hexdigest(),
            "enabled": True,
            "disabled": False,
            "startup_timeout_sec": expected_server["startup_timeout_sec"],
            "tool_timeout_sec": expected_server["tool_timeout_sec"],
        },
        "usage_allowed": True,
    }


class ReadOnlyProbe:
    """A one-shot bounded app-server reader; command is always caller supplied."""

    def __init__(self, command: list[str], overrides: list[str], *, timeout_seconds: float = 30):
        if (type(command) is not list or not command or any(type(part) is not str or not part or "\x00" in part for part in command)
                or type(overrides) is not list or any(type(item) is not str or "\x00" in item for item in overrides)
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 30):
            raise ValueError("C1_PREFLIGHT_INVALID_INPUT")
        self.command = list(command)
        self.overrides = list(overrides)
        self.timeout_seconds = float(timeout_seconds)
        self._closed = False
        self._last_child_reaped = True
        self._last_child_returncode = None

    def inspect(self, snapshot: ReviewSnapshot, project: str) -> dict:
        if self._closed:
            raise ValueError("C1_PREFLIGHT_CLOSED")
        _current, manifest, _project_info, root, _expected_server, expected_overrides = _snapshot_inputs(snapshot, project)
        if self.overrides != expected_overrides:
            _reject("C1_PREFLIGHT_OVERRIDE_MISMATCH")
        try:
            return asyncio.run(self._inspect_async(snapshot, project, root))
        except _ProbeFailure as error:
            raise ValueError(error.code) from None
        except ValueError as error:
            if str(error).startswith("C1_"):
                raise
            raise ValueError("C1_READ_ONLY_PREFLIGHT_FAILED") from None
        except (OSError, subprocess.SubprocessError, RuntimeError):
            raise ValueError("C1_READ_ONLY_PREFLIGHT_FAILED") from None

    async def _inspect_async(self, snapshot, project, root):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        cleanup_reserve = min(CLEANUP_RESERVE_SECONDS, self.timeout_seconds * 0.5)
        operation_deadline = deadline - cleanup_reserve
        env = {}
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
                     "HOMEPATH", "LOCALAPPDATA", "APPDATA", "CODEX_HOME", "HOME", "TMPDIR"):
            value = os.environ.get(name)
            if type(value) is str:
                env[name] = value
        version = await self._read_version(root, env, operation_deadline, deadline)
        if version != TARGET_VERSION:
            _reject("C1_PREFLIGHT_VERSION_MISMATCH")
        command = list(self.command) + ["app-server", "--stdio"]
        for override in self.overrides:
            command.extend(("-c", override))
        process = await self._spawn(command, root, env)
        received = 0
        request_id = 1
        try:
            _initialize, received = await self._send_request(process, "initialize", {
                "clientInfo": {"name": "experience-memory-readonly-c1", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            }, operation_deadline, received)
            await self._send_notification(process, "initialized", {}, operation_deadline)
            request_id += 1
            config_result, received = await self._call(process, "config/read", {
                "cwd": str(root), "includeLayers": True,
            }, request_id, operation_deadline, received)
            if (type(config_result) is not dict or not {"config", "origins"}.issubset(config_result)
                    or set(config_result) - {"config", "origins", "layers"}):
                raise _ProbeFailure("C1_RPC_INVALID_CONFIG_RESPONSE")
            if type(config_result.get("origins")) is not dict or "layers" not in config_result:
                raise _ProbeFailure("C1_RPC_INVALID_CONFIG_RESPONSE")
            request_id += 1
            hooks_result, received = await self._call(process, "hooks/list", {
                "cwds": [str(root)],
            }, request_id, operation_deadline, received)
            if type(hooks_result) is not dict or set(hooks_result) != {"data"}:
                raise _ProbeFailure("C1_RPC_INVALID_HOOKS_RESPONSE")
            request_id += 1
            usage_result, received = await self._call(process, "account/rateLimits/read", {
                "excludeResetCreditDetails": True, "supportsLunaReserve": False,
            }, request_id, operation_deadline, received)
            if type(usage_result) is not dict:
                raise _ProbeFailure("C1_RPC_INVALID_USAGE_RESPONSE")
            if type(config_result.get("config")) is not dict or type(config_result.get("layers")) is not list:
                raise _ProbeFailure("C1_RPC_INVALID_CONFIG_RESPONSE")
            result = validate_preflight(
                snapshot, project, version=version, effective_config=config_result["config"],
                layers=config_result["layers"], hooks=hooks_result, usage=usage_result,
            )
            return result
        finally:
            await self._dispose(process, deadline)

    async def _spawn(self, command, root, env):
        try:
            return await asyncio.create_subprocess_exec(
                *command, cwd=str(root), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=MAX_RPC_LINE_BYTES,
            )
        except (OSError, ValueError):
            raise _ProbeFailure("C1_PREFLIGHT_PROCESS_FAILED") from None

    async def _read_version(self, root, env, operation_deadline, cleanup_deadline):
        process = await self._spawn(list(self.command) + ["--version"], root, env)
        try:
            remaining = operation_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT")
            try:
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if len(line) > MAX_VERSION_LINE_BYTES or not line.endswith(b"\n"):
                    raise _ProbeFailure("C1_PREFLIGHT_VERSION_INVALID")
                remaining = operation_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT")
                rest = await asyncio.wait_for(process.stdout.read(MAX_VERSION_LINE_BYTES + 1), remaining)
                if rest:
                    raise _ProbeFailure("C1_PREFLIGHT_VERSION_INVALID")
                remaining = operation_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT")
                await asyncio.wait_for(process.wait(), remaining)
            except asyncio.TimeoutError:
                raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT") from None
            except (ValueError, asyncio.LimitOverrunError):
                raise _ProbeFailure("C1_PREFLIGHT_VERSION_INVALID") from None
            if process.returncode != 0:
                raise _ProbeFailure("C1_PREFLIGHT_VERSION_FAILED")
            version_line = line[:-2] if line.endswith(b"\r\n") else line[:-1]
            try:
                return version_line.decode("ascii", errors="strict")
            except UnicodeError:
                raise _ProbeFailure("C1_PREFLIGHT_VERSION_INVALID") from None
        finally:
            await self._dispose(process, cleanup_deadline)

    async def _send_bytes(self, process, body, deadline):
        if type(body) is not dict:
            raise _ProbeFailure("C1_RPC_INVALID_REQUEST")
        try:
            encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) > MAX_RPC_LINE_BYTES:
                raise _ProbeFailure("C1_RPC_LINE_LIMIT")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT")
            process.stdin.write(encoded)
            await asyncio.wait_for(process.stdin.drain(), remaining)
        except asyncio.TimeoutError:
            raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT") from None
        except (BrokenPipeError, ConnectionError):
            raise _ProbeFailure("C1_RPC_PROCESS_CLOSED") from None

    async def _send_request(self, process, method, params, deadline, received):
        # initialize is handled before the remaining ordered, whitelisted reads.
        result, received = await self._call(process, method, params, 1, deadline, received)
        _validate_initialize_response(result)
        return result, received

    async def _send_notification(self, process, method, params, deadline):
        if method not in RPC_ALLOWLIST or method != "initialized":
            raise _ProbeFailure("C1_READ_ONLY_RPC_REJECTED")
        await self._send_bytes(process, {"jsonrpc": "2.0", "method": method, "params": params}, deadline)

    async def _call(self, process, method, params, request_id, deadline, received):
        if method not in RPC_ALLOWLIST or method == "initialized":
            raise _ProbeFailure("C1_READ_ONLY_RPC_REJECTED")
        await self._send_bytes(process, {
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }, deadline)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT")
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                raise _ProbeFailure("C1_PREFLIGHT_TIMEOUT") from None
            except (ValueError, asyncio.LimitOverrunError):
                raise _ProbeFailure("C1_RPC_LINE_LIMIT") from None
            if not raw or not raw.endswith(b"\n"):
                raise _ProbeFailure("C1_RPC_PROCESS_CLOSED")
            if len(raw) > MAX_RPC_LINE_BYTES:
                raise _ProbeFailure("C1_RPC_LINE_LIMIT")
            received += len(raw)
            if received > MAX_PREFLIGHT_RECEIVED_BYTES:
                raise _ProbeFailure("C1_RPC_TOTAL_LIMIT")
            message = _parse_json(raw[:-1])
            if type(message) is not dict or message.get("jsonrpc") != "2.0":
                raise _ProbeFailure("C1_RPC_INVALID_MESSAGE")
            if "method" in message or "id" not in message:
                # No server-initiated requests or notifications are needed for these reads.
                raise _ProbeFailure("C1_RPC_UNEXPECTED_NOTIFICATION")
            if type(message.get("id")) is not int or message["id"] != request_id:
                raise _ProbeFailure("C1_RPC_RESPONSE_ID_MISMATCH")
            if set(message) == {"jsonrpc", "id", "error"}:
                raise _ProbeFailure("C1_RPC_REMOTE_ERROR")
            if set(message) != {"jsonrpc", "id", "result"} or type(message["result"]) is not dict:
                raise _ProbeFailure("C1_RPC_INVALID_RESPONSE")
            return message["result"], received

    async def _dispose(self, process, deadline):
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            remaining = deadline - asyncio.get_running_loop().time()
            grace_period = min(0.1, max(0.0, remaining / 4))
            if grace_period > 0:
                try:
                    await asyncio.wait_for(process.wait(), grace_period)
                except asyncio.TimeoutError:
                    pass
        if process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                try:
                    await asyncio.wait_for(process.wait(), remaining)
                except asyncio.TimeoutError:
                    pass
        self._last_child_reaped = process.returncode is not None
        self._last_child_returncode = process.returncode
        if not self._last_child_reaped:
            raise _ProbeFailure("C1_PREFLIGHT_CHILD_UNRESOLVED")

    def close(self) -> None:
        self._closed = True
