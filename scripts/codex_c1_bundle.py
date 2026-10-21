"""Offline-only immutable C1 review bundles and conservative attempt ledger."""
from dataclasses import dataclass
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from experience_memory.infrastructure.session_pointer import SessionPointerStore
from scripts import prepare_codex_c1_review as exporter

MAX_JSON_BYTES = 1024 * 1024
APPROVAL_FIELDS = {
    "schema_version", "scope", "run_nonce", "manifest_sha256", "projects",
    "max_attempts_total", "max_attempts_per_project", "max_tool_calls_per_task",
    "task_timeout_seconds", "actions", "user_confirmation_reference",
}
APPROVED_ACTIONS = {"install_configs", "inspect", "run_project", "rollback_config"}
PROJECTS = ("a", "b")
RECEIPT_NAME = "install-receipt.json"


class _FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("review snapshot is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


class _FrozenList(list):
    def _immutable(self, *args, **kwargs):
        raise TypeError("review snapshot is immutable")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = _immutable
    __iadd__ = __imul__ = _immutable


def _freeze(value):
    if type(value) is dict:
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return _FrozenList(_freeze(item) for item in value)
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _read_json_bytes(path: Path, *, max_bytes=MAX_JSON_BYTES):
    _assert_safe_existing(path)
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("JSON exceeds size limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid strict JSON") from error
    return raw, value


def _read_json(path: Path, *, max_bytes=MAX_JSON_BYTES):
    return _read_json_bytes(path, max_bytes=max_bytes)[1]


def _is_reparse(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _assert_safe_existing(path: Path):
    metadata = path.lstat()
    if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("expected a regular, non-reparse file")


def _assert_safe_directory(path: Path):
    metadata = path.lstat()
    if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("expected a directory without reparse points")


def _safe_bundle(bundle: Path, *, strict=True) -> Path:
    if not isinstance(bundle, Path) or not bundle.is_absolute():
        raise ValueError("absolute bundle path required")
    _assert_safe_directory(bundle)
    path = bundle.resolve(strict=strict)
    return path


def _safe_child(root: Path, relative: str, *, expect="file") -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid bundle-relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("unsafe bundle-relative path")
    candidate = root
    for index, part in enumerate(pure.parts):
        candidate = candidate / part
        if _path_entry_exists(candidate):
            if index == len(pure.parts) - 1 and expect == "file":
                _assert_safe_existing(candidate)
            else:
                _assert_safe_directory(candidate)
    if not candidate.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise ValueError("bundle path escaped root")
    return candidate


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ReviewSnapshot:
    bundle: Path
    manifest_sha256: str
    run_nonce: str
    manifest: dict


def _check_snapshot_current(snapshot):
    if not isinstance(snapshot, ReviewSnapshot):
        raise ValueError("review snapshot required")
    root = _safe_bundle(snapshot.bundle)
    if root != snapshot.bundle:
        raise ValueError("bundle path changed")
    manifest_path = _safe_child(root, "launch-review.json")
    raw, current = _read_json_bytes(manifest_path)
    if _hash(raw) != snapshot.manifest_sha256:
        raise ValueError("immutable manifest bytes changed")
    if current != snapshot.manifest:
        raise ValueError("manifest no longer matches snapshot")
    if current.get("run_nonce") != snapshot.run_nonce:
        raise ValueError("snapshot nonce changed")
    return root, current


def load_snapshot(bundle: Path) -> ReviewSnapshot:
    root = _safe_bundle(bundle)
    manifest_path = _safe_child(root, "launch-review.json")
    raw, manifest = _read_json_bytes(manifest_path)
    if type(manifest) is not dict or manifest.get("schema_version") != 2:
        raise ValueError("schema-version-2 review bundle required")
    nonce = manifest.get("run_nonce")
    if type(nonce) is not str or not re.fullmatch(r"c1-[a-z0-9-]{1,80}", nonce):
        raise ValueError("invalid run nonce")
    snapshot = ReviewSnapshot(root, _hash(raw), nonce, _freeze(copy.deepcopy(manifest)))
    _validate_bundle_payload(root, manifest)
    for project in PROJECTS:
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        config = project_root / ".codex/config.toml"
        if _path_entry_exists(config):
            if _safe_child(project_root, ".codex/config.toml").read_bytes() != _preview_bytes(root, manifest, project):
                raise ValueError("installed config differs from immutable preview")
    return snapshot


def _approval_validity(snapshot, approval, action, project):
    if type(approval) is not dict or set(approval) != APPROVAL_FIELDS:
        raise ValueError("approval schema mismatch")
    if type(approval["schema_version"]) is not int or approval["schema_version"] != 1:
        raise ValueError("unsupported approval schema")
    if type(approval["scope"]) is not str or approval["scope"] not in ("offline-simulation", "native-c1-wiring"):
        raise ValueError("unsupported approval scope")
    # Schema compatibility is not dispatch authority: production orchestration
    # separately requires native scope, explicit action, and supported transport.
    if approval["run_nonce"] != snapshot.run_nonce or approval["manifest_sha256"] != snapshot.manifest_sha256:
        raise ValueError("approval does not bind this snapshot")
    if type(approval["projects"]) is not list or approval["projects"] != list(PROJECTS):
        raise ValueError("approval project scope mismatch")
    expected_limits = {"max_attempts_total": 2, "max_attempts_per_project": 1,
                       "max_tool_calls_per_task": 5, "task_timeout_seconds": 180}
    for field, expected in expected_limits.items():
        if type(approval[field]) is not int or approval[field] != expected:
            raise ValueError("approval limits mismatch")
    actions = approval["actions"]
    if (type(actions) is not list or not actions or any(type(item) is not str for item in actions)
            or len(actions) != len(set(actions))):
        raise ValueError("invalid approval actions")
    if any(item not in APPROVED_ACTIONS for item in actions):
        raise ValueError("unsupported approval action")
    if action not in APPROVED_ACTIONS or action not in actions:
        raise ValueError("action not approved")
    reference = approval["user_confirmation_reference"]
    if (type(reference) is not str or not reference.strip() or reference != reference.strip()
            or len(reference.encode("utf-8")) > 512
            or re.search(r"(?i)(https?://|file://|\\\\)", reference)):
        raise ValueError("invalid local confirmation reference")
    if project is not None and (type(project) is not str or project not in PROJECTS
                                or project not in approval["projects"]):
        raise ValueError("project not approved")


def validate_approval(snapshot: ReviewSnapshot, approval: dict, action: str, project: str | None) -> None:
    _check_snapshot_current(snapshot)
    _approval_validity(snapshot, approval, action, project)


def _deadline_timeout(deadline):
    """Optional original absolute deadline; None preserves the legacy 10s lock."""
    if deadline is None:
        return 10
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("C1_ATTEMPT_DEADLINE_INVALID")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("C1_ATTEMPT_DEADLINE_EXPIRED")
    return min(10, remaining)


def _lock(snapshot, *, deadline=None):
    _deadline_timeout(deadline)
    root = _safe_bundle(snapshot.bundle)
    coordination = root / ".coordination"
    try:
        _assert_safe_directory(coordination)
    except FileNotFoundError:
        try:
            coordination.mkdir()
        except FileExistsError:
            pass
        _assert_safe_directory(coordination)
    if coordination.resolve(strict=True) != coordination:
        raise ValueError("coordination path must remain inside the bundle")
    store = SessionPointerStore(coordination)
    if store.data_root != coordination:
        raise ValueError("lock store resolved outside the bundle")
    # Recheck immediately before SessionPointerStore.lock creates/opens the lock file.
    _assert_safe_directory(coordination)
    if store.data_root != coordination:
        raise ValueError("coordination path changed before locking")
    if deadline is None:
        # Preserve the original relative API for every omitted-deadline caller.
        return store.lock(snapshot.run_nonce, "native-attempts", timeout_seconds=10)
    # The old backend starts its relative timer AFTER file setup. An explicit
    # original deadline therefore needs the small interoperable absolute loop.
    from scripts.codex_c1_lock import absolute_attempt_lock
    return absolute_attempt_lock(snapshot, deadline=deadline)


def _write_bytes_exclusive(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_directory(path.parent)
    if _path_entry_exists(path):
        raise ValueError("owned evidence already exists")
    if type(payload) is not bytes or len(payload) > MAX_JSON_BYTES:
        raise ValueError("evidence exceeds size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_exclusive(path: Path, value):
    _write_bytes_exclusive(path, _json_bytes(value))


def _replace_owned(path: Path, value):
    _assert_safe_existing(path)
    temporary = path.with_name(path.name + ".tmp")
    _write_exclusive(temporary, value)
    try:
        _assert_safe_existing(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _preview_bytes(root, manifest, project):
    item = manifest["projects"][project]
    preview_path = _safe_child(root, item["config_preview"])
    data = preview_path.read_bytes()
    if _hash(data) != item["config_preview_sha256"]:
        raise ValueError("preview bytes changed")
    return data


def _validate_bundle_payload(root, manifest):
    if exporter.manifest_for(root, manifest["run_nonce"]) != manifest:
        raise ValueError("review manifest/source/environment changed")
    for project in PROJECTS:
        _preview_bytes(root, manifest, project)
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        marker = _safe_child(project_root, ".codex/c1-fixture.json")
        if _hash(marker.read_bytes()) != manifest["projects"][project]["marker_sha256"]:
            raise ValueError("project marker changed")
        hooks = project_root / ".codex/hooks.json"
        if _path_entry_exists(hooks):
            raise ValueError("hooks configuration is forbidden")


def _receipt_path(root):
    return _safe_child(root, "install-receipt.json")


def _ledger_id(snapshot):
    return _hash((snapshot.run_nonce + ":" + snapshot.manifest_sha256 + ":attempt-ledger").encode("ascii"))


def _ledger_path(root):
    return root / "attempts" / "ledger.json"


def _ledger_initial(snapshot):
    return {"schema_version": 1, "ledger_id": _ledger_id(snapshot),
            "run_nonce": snapshot.run_nonce, "manifest_sha256": snapshot.manifest_sha256,
            "projects": {"a": "available", "b": "blocked"}}


def _receipt_expected(snapshot, state, projects):
    return {"schema_version": 1, "state": state, "run_nonce": snapshot.run_nonce,
            "manifest_sha256": snapshot.manifest_sha256, "attempt_ledger_id": _ledger_id(snapshot),
            "projects": projects}


def _read_receipt(root, supplied):
    disk = _read_json(_receipt_path(root))
    if type(supplied) is not dict or disk != supplied:
        raise ValueError("independent installation receipt mismatch")
    if disk.get("schema_version") != 1 or disk.get("state") != "installed":
        raise ValueError("installation receipt is incomplete")
    return disk


def verify_snapshot(snapshot: ReviewSnapshot, *, phase: str, receipt: dict | None = None) -> dict:
    if phase not in ("review", "installed"):
        raise ValueError("phase must be review or installed")
    root, manifest = _check_snapshot_current(snapshot)
    if phase == "review":
        if receipt is not None:
            raise ValueError("review phase does not accept installation receipt")
        exporter.verify(root)
        receipt_path = root / RECEIPT_NAME
        if _path_entry_exists(receipt_path):
            raise ValueError("review bundle must not have installation receipt")
        return {"state": "review", "manifest_sha256": snapshot.manifest_sha256}
    _validate_bundle_payload(root, manifest)
    saved = _read_receipt(root, receipt)
    expected_projects = {}
    for project in PROJECTS:
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        config_path = _safe_child(project_root, ".codex/config.toml")
        expected = _preview_bytes(root, manifest, project)
        actual = config_path.read_bytes()
        if actual != expected:
            raise ValueError("installed config bytes differ from frozen preview")
        expected_projects[project] = {"config_sha256": _hash(actual),
                                      "marker_sha256": manifest["projects"][project]["marker_sha256"],
                                      "created": True}
    expected = _receipt_expected(snapshot, "installed", expected_projects)
    if saved != expected:
        raise ValueError("installation receipt contents changed")
    ledger = _read_json(_ledger_path(root))
    if (type(ledger) is not dict or ledger.get("schema_version") != 1
            or ledger.get("ledger_id") != _ledger_id(snapshot)
            or ledger.get("run_nonce") != snapshot.run_nonce
            or ledger.get("manifest_sha256") != snapshot.manifest_sha256
            or type(ledger.get("projects")) is not dict
            or set(ledger["projects"]) != set(PROJECTS)):
        raise ValueError("attempt ledger missing or foreign")
    return {"state": "installed", "manifest_sha256": snapshot.manifest_sha256,
            "receipt_sha256": _hash(_json_bytes(saved))}


def _install_configs_locked(snapshot, approval):
    root, manifest = _check_snapshot_current(snapshot)
    _approval_validity(snapshot, approval, "install_configs", None)
    exporter.verify(root)
    targets = []
    for project in PROJECTS:
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        config = _safe_child(project_root, ".codex/config.toml")
        if _path_entry_exists(config):
            raise ValueError("refusing to overwrite existing config")
        targets.append((project, config))
    approval_path = _safe_child(root, "approval.json")
    _write_exclusive(approval_path, approval)
    receipt_path = _receipt_path(root)
    initial = {**_receipt_expected(snapshot, "installing", {}), "created_files": []}
    _write_exclusive(receipt_path, initial)
    installed = {}
    created_files = []
    try:
        for project, config in targets:
            project_root = _safe_child(root, "projects/" + project, expect="directory")
            data = _preview_bytes(root, manifest, project)
            marker = _safe_child(project_root, ".codex/c1-fixture.json")
            progress = {"path": f"projects/{project}/.codex/config.toml",
                        "state": "creating", "expected_config_sha256": _hash(data),
                        "marker_sha256": _hash(marker.read_bytes())}
            created_files.append(progress)
            _replace_owned(receipt_path, {**initial, "created_files": created_files})
            _write_bytes_exclusive(config, data)
            installed[project] = {"config_sha256": _hash(data),
                                  "marker_sha256": progress["marker_sha256"], "created": True}
            created_files[-1] = {"path": progress["path"], "state": "created",
                                 "config_sha256": _hash(data),
                                 "marker_sha256": progress["marker_sha256"]}
            _replace_owned(receipt_path, {**initial, "created_files": created_files})
        ledger = _ledger_initial(snapshot)
        _write_exclusive(_ledger_path(root), ledger)
        complete = _receipt_expected(snapshot, "installed", installed)
        _replace_owned(receipt_path, complete)
        return complete
    except BaseException as error:
        _replace_owned(receipt_path, {**initial, "state": "install_failed",
                                      "created_files": created_files,
                                      "error_type": type(error).__name__})
        raise


def install_configs(snapshot: ReviewSnapshot, approval: dict) -> dict:
    _check_snapshot_current(snapshot)
    _approval_validity(snapshot, approval, "install_configs", None)
    with _lock(snapshot):
        return _install_configs_locked(snapshot, approval)


def _file_identity(path: Path):
    metadata = path.lstat()
    if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("config must remain a regular, non-reparse file")
    return metadata.st_dev, metadata.st_ino


def _restore_quarantined_without_overwrite(quarantined: Path, original: Path):
    if _path_entry_exists(original):
        return False
    try:
        _assert_safe_existing(quarantined)
        os.link(quarantined, original)
    except (OSError, ValueError):
        return False
    return True


def _verify_rollback_state(snapshot, target, saved):
    """Rollback-only peer state; never used by installed/run verification.

    A missing peer must have our exact completed rollback record and the same
    quarantined file identity/bytes. An arbitrary missing/replaced config never
    becomes an idempotent success, and the target must still be installed.
    """
    root, manifest = _check_snapshot_current(snapshot)
    _validate_bundle_payload(root, manifest)
    expected_projects = {}
    for project in PROJECTS:
        expected = _preview_bytes(root, manifest, project)
        expected_projects[project] = {"config_sha256": _hash(expected),
                                      "marker_sha256": manifest["projects"][project]["marker_sha256"],
                                      "created": True}
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        config = _safe_child(project_root, ".codex/config.toml")
        record_path = _safe_child(root, "rollback-" + project + ".json")
        if _path_entry_exists(config):
            if config.read_bytes() != expected or _path_entry_exists(record_path):
                raise ValueError("active rollback config changed or has conflicting evidence")
            continue
        if project == target:
            raise ValueError("rollback target is not an installed owned file")
        record = _read_json(record_path)
        fields = {"schema_version", "state", "project", "run_nonce", "manifest_sha256",
                  "config_sha256", "quarantine_path", "config_identity"}
        if (type(record) is not dict or set(record) != fields
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["state"] != "rolled_back" or record["project"] != project
                or record["run_nonce"] != snapshot.run_nonce
                or record["manifest_sha256"] != snapshot.manifest_sha256
                or record["config_sha256"] != _hash(expected)
                or type(record["quarantine_path"]) is not str
                or not re.fullmatch("rollback-quarantine/" + project + r"-[0-9a-f]{32}/config\.toml",
                                    record["quarantine_path"])
                or type(record["config_identity"]) is not list or len(record["config_identity"]) != 2
                or any(type(number) is not int or number < 0 for number in record["config_identity"])):
            raise ValueError("peer rollback evidence is missing or changed")
        quarantine = _safe_child(root, record["quarantine_path"])
        identity = tuple(record["config_identity"])
        if (_file_identity(quarantine) != identity or quarantine.read_bytes() != expected
                or _file_identity(quarantine) != identity):
            raise ValueError("peer quarantine identity or bytes changed")
    if saved != _receipt_expected(snapshot, "installed", expected_projects):
        raise ValueError("installation receipt contents changed")
    # Preserve the independent conservative attempt ledger checks; rollback does
    # not reset reservations or make any task available again.
    _attempt_records(root, snapshot)


def rollback_config(snapshot: ReviewSnapshot, project: str, receipt: dict) -> dict:
    root, manifest = _check_snapshot_current(snapshot)
    _approval_validity(snapshot, _read_json(_safe_child(root, "approval.json")), "rollback_config", project)
    _lock_context = _lock(snapshot)
    with _lock_context:
        _check_snapshot_current(snapshot)
        saved = _read_receipt(root, receipt)
        _verify_rollback_state(snapshot, project, saved)
        approval = _read_json(_safe_child(root, "approval.json"))
        _approval_validity(snapshot, approval, "rollback_config", project)
        project_root = _safe_child(root, "projects/" + project, expect="directory")
        config_path = _safe_child(project_root, ".codex/config.toml")
        expected = _preview_bytes(root, manifest, project)
        entry = saved["projects"].get(project)
        if entry != {"config_sha256": _hash(expected),
                     "marker_sha256": manifest["projects"][project]["marker_sha256"], "created": True}:
            raise ValueError("receipt does not own this config")
        checked_identity = _file_identity(config_path)
        actual = config_path.read_bytes()
        if (_file_identity(config_path) != checked_identity
                or actual != expected or _hash(actual) != entry["config_sha256"]):
            raise ValueError("config changed; refusing rollback")

        quarantine_root = root / "rollback-quarantine"
        if not _path_entry_exists(quarantine_root):
            quarantine_root.mkdir()
        _assert_safe_directory(quarantine_root)
        quarantine_dir = quarantine_root / (project + "-" + uuid.uuid4().hex)
        quarantine_dir.mkdir(exist_ok=False)
        _assert_safe_directory(quarantine_dir)
        quarantined = quarantine_dir / "config.toml"
        quarantine_relative = quarantined.relative_to(root).as_posix()
        rollback_path = _safe_child(root, "rollback-" + project + ".json")
        rollback_record = {"schema_version": 1, "state": "rollback_started",
                           "project": project, "run_nonce": snapshot.run_nonce,
                           "manifest_sha256": snapshot.manifest_sha256,
                           "config_sha256": entry["config_sha256"],
                           "config_identity": list(checked_identity),
                           "quarantine_path": quarantine_relative}
        _write_exclusive(rollback_path, rollback_record)
        # Rename is the atomic ownership boundary. The moved object is never unlinked;
        # preserve it in quarantine even when a concurrent replacement wins the race.
        os.rename(config_path, quarantined)
        try:
            moved_identity = _file_identity(quarantined)
            moved_bytes = quarantined.read_bytes()
        except (OSError, ValueError):
            _restore_quarantined_without_overwrite(quarantined, config_path)
            _replace_owned(rollback_path, {**rollback_record, "state": "rollback_conflict"})
            raise ValueError("config changed during rollback; staged bytes preserved") from None
        if moved_identity != checked_identity or moved_bytes != expected or _hash(moved_bytes) != entry["config_sha256"]:
            _restore_quarantined_without_overwrite(quarantined, config_path)
            _replace_owned(rollback_path, {**rollback_record, "state": "rollback_conflict",
                                           "staged_config_sha256": _hash(moved_bytes)})
            raise ValueError("config changed during rollback; staged bytes preserved")
        _replace_owned(rollback_path, {**rollback_record, "state": "rolled_back"})
        return {"state": "rolled_back", "project": project,
                "manifest_sha256": snapshot.manifest_sha256,
                "quarantine_path": quarantine_relative}


def _attempt_dir(root):
    path = root / "attempts"
    if not _path_entry_exists(path):
        path.mkdir()
    _assert_safe_directory(path)
    return path


def _attempt_path(root, project, suffix=None):
    name = project + ("-" + suffix if suffix else "") + ".json"
    return _attempt_dir(root) / name


def _attempt_records(root, snapshot):
    records = {}
    directory = root / "attempts"
    _assert_safe_directory(directory)
    ledger = _read_json(_ledger_path(root))
    if (type(ledger) is not dict or ledger != _ledger_initial(snapshot)
            and (set(ledger) != set(_ledger_initial(snapshot))
                 or ledger.get("schema_version") != 1
                 or ledger.get("ledger_id") != _ledger_id(snapshot)
                 or ledger.get("run_nonce") != snapshot.run_nonce
                 or ledger.get("manifest_sha256") != snapshot.manifest_sha256
                 or type(ledger.get("projects")) is not dict
                 or set(ledger["projects"]) != set(PROJECTS))):
        raise ValueError("attempt ledger missing, damaged, or foreign")
    for project in PROJECTS:
        row = {}
        for suffix in ("reservation", "report"):
            path = directory / (project + "-" + suffix + ".json")
            if _path_entry_exists(path):
                row[suffix] = _read_json(path)
        records[project] = row
    stopped_path = directory / "stopped.json"
    records["stopped"] = _read_json(stopped_path) if _path_entry_exists(stopped_path) else None
    statuses = ledger["projects"]
    allowed = {"blocked", "available", "reserved", "verified", "failed", "unknown"}
    if any(type(statuses[name]) is not str or statuses[name] not in allowed for name in PROJECTS):
        raise ValueError("invalid attempt ledger status")
    a, b = records["a"], records["b"]
    a_status, b_status = statuses["a"], statuses["b"]
    if (a_status == "available" and a) or (a_status == "reserved" and (not a.get("reservation") or a.get("report"))) or (a_status in ("verified", "failed", "unknown") and (not a.get("reservation") or not a.get("report") or a["report"].get("state") != a_status)):
        raise ValueError("project A reservation/report missing or inconsistent")
    if (b_status in ("blocked", "available") and b) or (b_status == "reserved" and (not b.get("reservation") or b.get("report"))) or (b_status in ("verified", "failed", "unknown") and (not b.get("reservation") or not b.get("report") or b["report"].get("state") != b_status)):
        raise ValueError("project B reservation/report missing or inconsistent")
    if b_status == "available" and a_status != "verified":
        raise ValueError("project B became available without verified A")
    if b_status == "blocked" and a_status == "verified":
        raise ValueError("project B ledger did not advance after verified A")
    records["ledger"] = ledger
    return records


def _publish_ledger(snapshot, ledger):
    path = _ledger_path(snapshot.bundle)
    _replace_owned(path, ledger)


def _check_manifest_approval_and_run_state(snapshot, project, approval):
    root, manifest = _check_snapshot_current(snapshot)
    _approval_validity(snapshot, approval, "run_project", project)
    saved_receipt = _read_json(_receipt_path(root))
    verify_snapshot(snapshot, phase="installed", receipt=saved_receipt)
    saved_approval = _read_json(_safe_child(root, "approval.json"))
    if saved_approval != approval:
        raise ValueError("independent approval record changed")
    if manifest.get("native_authorization") != {"granted": False, "remaining_model_tasks": 0}:
        raise ValueError("native authorization must remain disabled")
    records = _attempt_records(root, snapshot)
    if records.get("stopped") is not None:
        raise ValueError("bounded attempt round is stopped")
    for name in PROJECTS:
        row = records.get(name, {})
        reservation = row.get("reservation")
        report = row.get("report")
        if reservation is not None:
            expected = {"schema_version": 1, "project": name, "attempt": 1,
                        "state": "reserved", "run_nonce": snapshot.run_nonce,
                        "manifest_sha256": snapshot.manifest_sha256}
            if reservation != expected:
                raise ValueError("invalid or foreign reservation")
            if report is not None and (type(report) is not dict
                    or set(report) != {"schema_version", "project", "attempt", "state", "detail", "run_nonce", "manifest_sha256"}
                    or report.get("schema_version") != 1 or report.get("attempt") != 1
                    or report.get("project") != name or report.get("run_nonce") != snapshot.run_nonce
                    or report.get("manifest_sha256") != snapshot.manifest_sha256
                    or report.get("state") not in ("verified", "failed", "unknown")
                    or type(report.get("detail")) is not str):
                raise ValueError("invalid final attempt report")
        elif report is not None:
            raise ValueError("report without reservation")
    a = records.get("a", {})
    b = records.get("b", {})
    if project == "a":
        if any(a.values()) or any(b.values()):
            raise ValueError("project A already reserved or round already advanced")
    elif project == "b":
        if not a.get("reservation") or not a.get("report") or a["report"].get("state") != "verified":
            raise ValueError("project B requires verified A report")
        if any(b.values()):
            raise ValueError("project B already reserved")
    else:
        raise ValueError("unknown project")
    if sum(bool(records.get(name, {}).get("reservation")) for name in PROJECTS) >= 2:
        raise ValueError("total attempt budget exhausted")
    return root, records


def _write_exclusive_reservation(snapshot, reservation):
    root, _ = _check_snapshot_current(snapshot)
    expected = {"schema_version": 1, "project": reservation.get("project"),
                "attempt": 1, "state": "reserved", "run_nonce": snapshot.run_nonce,
                "manifest_sha256": snapshot.manifest_sha256}
    if type(reservation) is not dict or reservation != expected or reservation["project"] not in PROJECTS:
        raise ValueError("invalid reservation")
    _write_exclusive(_attempt_path(root, reservation["project"], "reservation"), reservation)
    return reservation


def reserve_attempt(snapshot: ReviewSnapshot, project: str, approval: dict, *, deadline=None) -> dict:
    """Persist once before prewarm; optional deadline never renews the lock budget."""
    _deadline_timeout(deadline)
    _check_snapshot_current(snapshot)
    _approval_validity(snapshot, approval, "run_project", project)
    with (_lock(snapshot) if deadline is None else _lock(snapshot, deadline=deadline)):
        _deadline_timeout(deadline)
        _, records = _check_manifest_approval_and_run_state(snapshot, project, approval)
        reservation = {"schema_version": 1, "project": project, "attempt": 1,
                       "state": "reserved", "run_nonce": snapshot.run_nonce,
                       "manifest_sha256": snapshot.manifest_sha256}
        _deadline_timeout(deadline)
        written = _write_exclusive_reservation(snapshot, reservation)
        statuses = dict(records["ledger"]["projects"])
        statuses[project] = "reserved"
        _deadline_timeout(deadline)
        _publish_ledger(snapshot, {**records["ledger"], "projects": statuses})
        return written


def finish_attempt(snapshot: ReviewSnapshot, reservation: dict, outcome: dict, *,
                   expected_approval: dict | None = None, deadline=None) -> None:
    """Finalize once; executor verified claims recheck original authority in-lock.

    Legacy callers may omit expected_approval. The executor always supplies its
    detached pre-launch record; omission is not a native-dispatch authority path.
    Authority loss downgrades/persists unknown and stops the round before raising
    a static error, so the caller cannot return a verified claim after the race.
    deadline optionally bounds all controllable lock waits/checkpoints using the
    caller's ORIGINAL absolute budget. None preserves legacy behavior. Atomic
    filesystem calls/fsync cannot be preempted safely; no mid-write killer exists.
    """
    _deadline_timeout(deadline)
    root, _ = _check_snapshot_current(snapshot)
    if type(reservation) is not dict or reservation.get("project") not in PROJECTS:
        raise ValueError("invalid reservation")
    project = reservation["project"]
    expected = {"schema_version": 1, "project": project, "attempt": 1,
                "state": "reserved", "run_nonce": snapshot.run_nonce,
                "manifest_sha256": snapshot.manifest_sha256}
    if reservation != expected:
        raise ValueError("reservation does not bind this snapshot")
    if type(outcome) is not dict or set(outcome) - {"state", "detail"}:
        raise ValueError("invalid attempt outcome")
    state = outcome.get("state")
    if state not in ("verified", "failed", "unknown"):
        raise ValueError("attempt outcome must be verified, failed, or unknown")
    detail = outcome.get("detail", "")
    if (type(detail) is not str or len(detail.encode("utf-8")) > 1024
            or re.search(r"(?i)(https?://|file://|\\\\)", detail)):
        raise ValueError("invalid outcome detail")
    with (_lock(snapshot) if deadline is None else _lock(snapshot, deadline=deadline)):
        _deadline_timeout(deadline)
        _check_snapshot_current(snapshot)
        saved_receipt = _read_json(_receipt_path(root))
        verify_snapshot(snapshot, phase="installed", receipt=saved_receipt)
        _attempt_records(root, snapshot)
        reservation_path = _attempt_path(root, project, "reservation")
        if _read_json(reservation_path) != expected:
            raise ValueError("reservation is missing or changed")
        authority_changed = False
        if state == "verified" and expected_approval is not None:
            try:
                for action in ("run_project", "inspect"):
                    _approval_validity(snapshot, expected_approval, action, project)
                if _read_json(_safe_child(root, "approval.json")) != expected_approval:
                    raise ValueError("C1_FINALIZATION_AUTHORITY_CHANGED")
            except (OSError, ValueError, TypeError):
                state, detail = "unknown", "C1_FINALIZATION_AUTHORITY_CHANGED"
                authority_changed = True
        report = {"schema_version": 1, "project": project, "attempt": 1,
                  "state": state, "detail": detail, "run_nonce": snapshot.run_nonce,
                  "manifest_sha256": snapshot.manifest_sha256}
        _deadline_timeout(deadline)
        _write_exclusive(_attempt_path(root, project, "report"), report)
        if state in ("failed", "unknown"):
            stopped = {"schema_version": 1, "state": "stopped", "project": project,
                       "reason": state, "run_nonce": snapshot.run_nonce,
                       "manifest_sha256": snapshot.manifest_sha256}
            _deadline_timeout(deadline)
            _write_exclusive(_attempt_path(root, "stopped"), stopped)
        _deadline_timeout(deadline)
        ledger = _read_json(_ledger_path(root))
        statuses = dict(ledger["projects"])
        statuses[project] = state
        if project == "a" and state == "verified":
            statuses["b"] = "available"
        _deadline_timeout(deadline)
        _publish_ledger(snapshot, {**ledger, "projects": statuses})
        if authority_changed:
            raise ValueError("C1_FINALIZATION_AUTHORITY_CHANGED")
