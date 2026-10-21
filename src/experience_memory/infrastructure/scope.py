"""Stable Git repository identity; no installed plugin/private namespace imports."""

import getpass
import hashlib
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from ..domain.errors import ErrorCode, MemoryError
from ..domain.models import NamespaceMapping, Scope


def _git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(cwd), *args], env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=10)
    if result.returncode != 0:
        raise ValueError("git unresolved")
    return result.stdout.strip()


def resolve_project_root(cwd: Path) -> Path:
    try:
        cwd = cwd.resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError()
        root = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve(strict=True)
        cwd.relative_to(root)
        return root
    except (OSError, ValueError, subprocess.SubprocessError):
        raise MemoryError(ErrorCode.SCOPE_UNRESOLVED) from None


def canonical_remote(value: str) -> str:
    try:
        if not isinstance(value, str) or value != value.strip() or any(ch.isspace() for ch in value):
            raise ValueError()
        if "://" not in value:
            match = re.fullmatch(r"((?:[A-Za-z0-9_-]+@)?[A-Za-z0-9.-]+):(.+)", value)
            if not match:
                raise ValueError()
            value = "ssh://" + match[1] + "/" + match[2]
        url = urlsplit(value)
        if (url.scheme not in ("https", "ssh") or not url.hostname or url.query or url.fragment
                or url.password or (url.scheme == "https" and url.username) or "%" in url.path):
            raise ValueError()
        host = url.hostname.lower()
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ValueError()
        port = url.port
        # SCP relative paths may be user-home-relative on arbitrary SSH servers.
        # Only this documented provider form is safe to map to HTTPS automatically.
        if url.scheme == "ssh" and (host != "github.com" or url.username != "git" or port not in (None, 22)):
            raise ValueError()
        default_port = 22 if url.scheme == "ssh" else 443
        if port and port != default_port:
            host += ":" + str(port)
        path = url.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = path.split("/")
        if len(parts) < 2 or any(part in ("", ".", "..", "~") for part in parts) or "\\" in path:
            raise ValueError()
        return "https://" + host + "/" + path
    except (ValueError, TypeError):
        raise MemoryError(ErrorCode.SCOPE_UNRESOLVED) from None


def resolve_scope(cwd: Path, project_scope: str | None = None, *,
                  namespace_mapping: NamespaceMapping | None = None) -> Scope:
    root = resolve_project_root(cwd)
    try:
        if project_scope is not None:
            if re.fullmatch(r"local:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project_scope):
                identity = project_scope
            else:
                identity = canonical_remote(project_scope)
        else:
            names = _git(root, "remote").splitlines()
            chosen = ("origin",) if "origin" in names else tuple(names)
            identities = set()
            for name in chosen:
                for remote in _git(root, "remote", "get-url", "--all", name).splitlines():
                    identities.add(canonical_remote(remote))
            if len(identities) != 1:
                raise ValueError()
            identity = identities.pop()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        user = "user-v1-" + hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()
        if namespace_mapping is None:
            app, project, legacy = "experience-v1-" + digest, "project-v1-" + digest, ()
        else:
            if not isinstance(namespace_mapping, NamespaceMapping):
                raise ValueError()
            app, project, legacy = namespace_mapping.app_id, namespace_mapping.project_id, namespace_mapping.legacy_project_ids
        return Scope(identity, app, project, legacy, user)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise MemoryError(ErrorCode.SCOPE_UNRESOLVED) from None
