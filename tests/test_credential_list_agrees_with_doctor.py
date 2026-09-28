"""`rite credential list` and `rite doctor` give one answer about the
Workers' GitHub token.

They used to be two readers of one property with two rules. On a sandboxed
project with a GitHub module and no token, doctor reported a problem and
`rite sandbox start` refused the Worker, while `credential list` printed
"nothing missing" (seen on pingr, 2026-09-28). A user who checked the list
was told they were ready.

The test is the agreement itself, across the configurations that decide
it, so a future change to either rule alone fails here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

GITHUB = "https://github.com/acme/app.git"


def _project(tmp_path: Path, *, origin: str, sandboxed: bool, worker: bool) -> None:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        f"modules:\n  app:\n    path: app/\n    url: {origin}\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        f"sandbox:\n  enabled: {'true' if sandboxed else 'false'}\n"
        "  backend: seatbelt\n"
    )
    app = tmp_path / "app"
    app.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=app, check=True)
    if worker:
        w = tmp_path / "workers" / "alpha"
        w.mkdir(parents=True)
        (w / "worker.yml").write_text(
            "worker:\n  name: alpha\n  manager: ''\n  modules: [app]\n"
        )


def _answers(tmp_path: Path, monkeypatch) -> tuple[bool, bool, str, str]:
    monkeypatch.chdir(tmp_path)
    doctor = CliRunner().invoke(cli, ["doctor"]).output
    listing = CliRunner().invoke(cli, ["credential", "list"]).output
    flagged = "workers: no GitHub token" in doctor
    # The names under "missing — set each with:", as `rite credential set`
    # takes them; `github` is the documented short name for `github_token`.
    missing: set[str] = set()
    if "missing — set each with:" in listing:
        block = listing.split("missing — set each with:", 1)[1].split("\n\n")[0]
        missing = {
            line.split("rite credential set ", 1)[1].split()[0]
            for line in block.splitlines()
            if "rite credential set " in line
        }
    listed_missing = bool(missing & {"github", "github_token"})
    return flagged, listed_missing, doctor, listing


@pytest.mark.parametrize(
    "case, origin, sandboxed, worker, env, expected",
    [
        ("no token, no Worker yet", GITHUB, True, False, {}, True),
        ("no token, a Worker", GITHUB, True, True, {}, True),
        ("project token", GITHUB, True, True, {"RITE_GITHUB_TOKEN": "t"}, False),
        (
            "the Worker's own token only",
            GITHUB,
            True,
            True,
            {"RITE_SANDBOX_TOKEN_ALPHA": "t"},
            False,
        ),
        ("not on GitHub", "/srv/app.git", True, True, {}, False),
        ("Workers not sandboxed", GITHUB, False, True, {}, False),
    ],
)
def test_both_readers_give_one_answer(
    tmp_path, monkeypatch, case, origin, sandboxed, worker, env, expected
):
    for key in ("RITE_GITHUB_TOKEN", "GITHUB_TOKEN", "RITE_SANDBOX_TOKEN_ALPHA"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _project(tmp_path, origin=origin, sandboxed=sandboxed, worker=worker)

    flagged, listed_missing, doctor, listing = _answers(tmp_path, monkeypatch)

    assert flagged == listed_missing, f"{case}: disagree\n{doctor}\n{listing}"
    assert flagged is expected, f"{case}\n{doctor}\n{listing}"


def test_nothing_missing_is_not_said_while_a_worker_could_not_push(
    tmp_path, monkeypatch
):
    """The sentence that misled, checked by name."""
    for key in ("RITE_GITHUB_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RITE_CLAUDE_TOKEN", "t")
    _project(tmp_path, origin=GITHUB, sandboxed=True, worker=True)
    monkeypatch.chdir(tmp_path)

    listing = CliRunner().invoke(cli, ["credential", "list"]).output

    assert "nothing missing" not in listing, listing
