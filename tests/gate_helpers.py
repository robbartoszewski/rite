"""Shared helper for gate tests: build a real temp git repo.

Not named `conftest.py` on purpose — these are plain helper functions, not
fixtures, since different tests need very different repo shapes (some want
multiple commits to test history scanning, some want none).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sha.stdout.strip()


def write(root: Path, rel_path: str, content: str) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path
