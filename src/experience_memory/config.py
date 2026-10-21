from dataclasses import dataclass, fields
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from .domain.errors import ErrorCode, MemoryError
from .domain.models import NamespaceMapping, require_identity


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    mem0_api_url: str = "https://api.mem0.ai"
    project_scope: str | None = None
    core_paths: tuple[str, ...] = ()
    retention_budget_bytes: int = 8192
    search_limit: int = 5
    request_timeout_seconds: float = 10
    hook_deadline_seconds: float = 30
    shared_scope: bool = False
    namespace_mapping: NamespaceMapping | None = None

    def __post_init__(self):
        try:
            if type(self.enabled) is not bool or self.shared_scope is not False:
                raise ValueError()
            url = urlsplit(self.mem0_api_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError()
            if self.project_scope is not None:
                require_identity(self.project_scope)
            if self.namespace_mapping is not None and not isinstance(self.namespace_mapping, NamespaceMapping):
                raise ValueError()
            if type(self.core_paths) is not tuple:
                raise ValueError()
            for path in self.core_paths:
                if not isinstance(path, str) or not path or "\x00" in path:
                    raise ValueError()
                posix, windows = PurePosixPath(path), PureWindowsPath(path)
                if (posix.is_absolute() or windows.root or windows.drive
                        or ".." in posix.parts or ".." in windows.parts):
                    raise ValueError()
            for value, low, high in ((self.search_limit, 1, 20), (self.retention_budget_bytes, 1, 8192)):
                if type(value) is not int or not low <= value <= high:
                    raise ValueError()
            for value in (self.request_timeout_seconds, self.hook_deadline_seconds):
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30:
                    raise ValueError()
            if self.request_timeout_seconds >= self.hook_deadline_seconds:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise MemoryError(ErrorCode.INVALID_CONFIG) from None

    @classmethod
    def from_mapping(cls, values: dict):
        if type(values) is not dict or set(values) - {field.name for field in fields(cls)}:
            raise MemoryError(ErrorCode.INVALID_CONFIG)
        values = dict(values)
        if "core_paths" in values:
            if type(values["core_paths"]) is not list:
                raise MemoryError(ErrorCode.INVALID_CONFIG)
            values["core_paths"] = tuple(values["core_paths"])
        if values.get("namespace_mapping") is not None:
            mapping = values["namespace_mapping"]
            if type(mapping) is not dict or set(mapping) - {"app_id", "project_id", "legacy_project_ids"}:
                raise MemoryError(ErrorCode.INVALID_CONFIG)
            mapping = dict(mapping)
            legacy = mapping.get("legacy_project_ids", [])
            if type(legacy) is not list:
                raise MemoryError(ErrorCode.INVALID_CONFIG)
            mapping["legacy_project_ids"] = tuple(legacy)
            try:
                values["namespace_mapping"] = NamespaceMapping(**mapping)
            except (TypeError, ValueError):
                raise MemoryError(ErrorCode.INVALID_CONFIG) from None
        return cls(**values)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MemoryError(ErrorCode.INVALID_CONFIG)
        result[key] = value
    return result


def load_config(path: Path | None = None) -> Config:
    if path is None:
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        return Config.from_mapping(data)
    except (OSError, ValueError, UnicodeError):
        raise MemoryError(ErrorCode.INVALID_CONFIG) from None


def load_project_config(project_root: Path, explicit_path: Path | None = None) -> Config:
    """Discover only root config; explicit config may live outside the project."""
    try:
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError()
        if explicit_path is not None:
            return load_config(explicit_path)
        candidate = root / ".experience-memory.json"
        if not candidate.exists() and not candidate.is_symlink():
            return Config()
        target = candidate.resolve(strict=True)
        target.relative_to(root)
        return load_config(target)
    except (OSError, ValueError):
        raise MemoryError(ErrorCode.INVALID_CONFIG) from None
