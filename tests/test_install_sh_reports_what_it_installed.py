"""`install.sh` reports the rite it installed, not the first `rite` on PATH.

It ran `rite --version` and printed "Done: " before whatever came back, so a
v0.3.0 install with an older rite ahead of it on PATH ended
"Done: rite, version 0.1.0" — which a first-time user reads as the install
having failed. These run the real script, with `uv` and `pipx` stubbed to
write a `rite` that reports a version into their bin directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TAG = "v9.9.9"


def _rite(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "rite, version {version}"\n')
    path.chmod(0o755)
    return path


def _setup(tmp_path: Path, installer: str, installs: str) -> tuple[Path, Path, Path]:
    """(install.sh, stub dir, the installer's bin dir). The stub installer puts
    a rite reporting `installs` into its bin dir."""
    origin = tmp_path / "origin"
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "one"], cwd=origin)
    subprocess.run([*git, "tag", "-a", TAG, "-m", "r"], cwd=origin, check=True)

    script = tmp_path / "install.sh"
    text = (REPO_ROOT / "install.sh").read_text()
    repo = 'REPO="https://github.com/robbartoszewski/rite.git"'
    assert repo in text
    script.write_text(text.replace(repo, f'REPO="{origin}"'))

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    bin_dir = tmp_path / f"{installer}-bin"
    rite = f'#!/bin/sh\\necho "rite, version {installs}"\\n'
    body = {
        "uv": (
            'case "$1 $2" in\n'
            f'  "tool dir") echo "{bin_dir}" ;;\n'
            f'  "tool install") mkdir -p "{bin_dir}";'
            f' printf \'{rite}\' > "{bin_dir}/rite"; chmod +x "{bin_dir}/rite" ;;\n'
            "esac\n"
        ),
        "pipx": (
            'case "$1" in\n'
            f'  environment) echo "{bin_dir}" ;;\n'
            f'  install) mkdir -p "{bin_dir}"; [ -e "{bin_dir}/rite" ] ||'
            f" {{ printf '{rite}' > \"{bin_dir}/rite\";"
            f' chmod +x "{bin_dir}/rite"; }} ;;\n'
            "esac\n"
        ),
    }[installer]
    stub = stubs / installer
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return script, stubs, bin_dir


def _run(script: Path, *path_dirs: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": ":".join([*map(str, path_dirs), "/usr/bin", "/bin"]),
        "HOME": str(script.parent),
        "RITE_VERSION": TAG,
        "PIPX_DEFAULT_PYTHON": sys.executable,
    }
    return subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, env=env, timeout=120
    )


def test_an_older_rite_first_on_path_does_not_become_the_reported_version(tmp_path):
    script, stubs, bin_dir = _setup(tmp_path, "uv", "9.9.9")
    older = _rite(tmp_path / "older" / "rite", "0.1.0").parent

    proc = _run(script, stubs, older, bin_dir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"Installed: rite, version 9.9.9  ({bin_dir}/rite)" in proc.stdout
    assert "Done: rite, version 0.1.0" not in proc.stdout
    assert "found first on your PATH is another one" in proc.stdout
    assert f"{older}/rite -> rite, version 0.1.0" in proc.stdout
    assert f"Put {bin_dir} ahead of it on your PATH" in proc.stdout


def test_the_installed_rite_first_on_path_is_just_done(tmp_path):
    script, stubs, bin_dir = _setup(tmp_path, "uv", "9.9.9")

    proc = _run(script, stubs, bin_dir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Installed: rite, version 9.9.9" in proc.stdout
    assert "Start with:  rite help" in proc.stdout
    assert "another one" not in proc.stdout


def test_not_on_path_still_says_so_after_the_version(tmp_path):
    script, stubs, bin_dir = _setup(tmp_path, "uv", "9.9.9")

    proc = _run(script, stubs)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Installed: rite, version 9.9.9" in proc.stdout
    assert "'rite' is not on your PATH yet." in proc.stdout
    assert "uv tool update-shell" in proc.stdout


def test_a_pipx_install_that_kept_the_old_version_is_not_reported_done(tmp_path):
    """pipx exits 0 without changing anything when rite-ai is already
    installed, so the old version is what is left — measured with pipx 1.8."""
    script, stubs, bin_dir = _setup(tmp_path, "pipx", "9.9.9")
    _rite(bin_dir / "rite", "0.1.0")

    proc = _run(script, stubs, bin_dir)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"Asked for {TAG}, but {bin_dir}/rite reports:" in proc.stdout
    assert "rite, version 0.1.0" in proc.stdout
    assert f"pipx install --force git+{tmp_path / 'origin'}@{TAG}" in proc.stdout
    assert "Installed:" not in proc.stdout


def test_the_pypi_rite_first_on_path_points_at_rite_ai(tmp_path):
    script, stubs, bin_dir = _setup(tmp_path, "uv", "9.9.9")
    other = tmp_path / "pypi"
    other.mkdir()
    (other / "rite").write_text("#!/bin/sh\necho 'Rite 2.0 static site generator'\n")
    (other / "rite").chmod(0o755)

    proc = _run(script, stubs, other, bin_dir)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Installed: rite, version 9.9.9" in proc.stdout
    assert "unrelated 'rite' package from PyPI" in proc.stdout
    assert f"{bin_dir}/rite-ai --version" in proc.stdout
