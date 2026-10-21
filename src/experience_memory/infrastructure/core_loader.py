from pathlib import Path, PureWindowsPath, PurePosixPath

from ..domain.errors import ErrorCode, MemoryError


def load_core(project_root: Path, relative_paths: tuple[str, ...], *, budget_bytes: int = 8192) -> str:
    try:
        if type(relative_paths) is not tuple or type(budget_bytes) is not int or not 1 <= budget_bytes <= 8192:
            raise ValueError()
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError()
        texts = []
        remaining = budget_bytes
        for name in relative_paths:
            if not isinstance(name, str) or not name or any(ch in name for ch in "\x00\r\n:"):
                raise ValueError()
            windows, posix = PureWindowsPath(name), PurePosixPath(name)
            if windows.root or windows.drive or posix.is_absolute() or ".." in windows.parts or ".." in posix.parts:
                raise ValueError()
            target = (root / name).resolve(strict=True)
            target.relative_to(root)
            # Bound reads, avoid loading oversized files into memory.
            with target.open("rb") as stream:
                raw = stream.read(remaining + 1)
            if len(raw) > remaining:
                raise MemoryError(ErrorCode.CORE_OVER_BUDGET)
            text = raw.decode("utf-8")
            if texts:
                remaining -= 2
            remaining -= len(raw)
            if remaining < 0:
                raise MemoryError(ErrorCode.CORE_OVER_BUDGET)
            texts.append(text)
        return "\n\n".join(texts)
    except (OSError, ValueError, UnicodeError):
        raise MemoryError(ErrorCode.CORE_INVALID) from None


class FileCoreLoader:
    def load(self, project_root, relative_paths, budget_bytes=8192):
        return load_core(project_root, relative_paths, budget_bytes=budget_bytes)
