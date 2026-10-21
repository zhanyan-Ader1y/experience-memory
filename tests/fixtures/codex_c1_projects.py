"""Create fresh synthetic Git roots only; never installs host configuration."""
import json
import os
from pathlib import Path
import subprocess


def make_projects(directory: Path, nonce: str) -> dict[str, Path]:
    roots = {}
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    for project in ("a", "b"):
        root = directory / project
        root.mkdir(parents=True, exist_ok=False)
        subprocess.run(["git", "-C", str(root), "init", "--initial-branch=c1-synthetic"],
                       env=env, check=True, capture_output=True, timeout=10)
        (root / ".codex").mkdir()
        marker = {"schema_version": 1, "purpose": "experience-memory-c1-synthetic",
                  "run_nonce": nonce, "project": project}
        (root / ".codex/c1-fixture.json").write_text(json.dumps(marker), encoding="utf-8")
        roots[project] = root.resolve(strict=True)
    return roots
