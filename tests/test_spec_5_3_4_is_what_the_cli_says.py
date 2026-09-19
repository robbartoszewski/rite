"""The CLI stopped advertising the credential model §5.3.4 retired.

§5.3.4: "Every Worker on a project receives every credential the project
holds. Not a per-Worker subset." §5.3.4.1 keeps `rite add worker
--scoped-token` but says what it now is: "an option a person chooses, not
the model, and not something any other part of the design assumes is in
force."

Two places still assumed it was in force, and both spoke to a NEW user at
the moment they were deciding how to set the project up.

⚠ This file shipped once as `tests/nt.py`, which pytest never collects
(`testpaths` with default `test_*.py`), so the `rite credential list` half
had no running coverage and a REMEDY NAMING A COMMAND THAT DOES NOT EXIST
went out in the warning text. Both are covered here, and the warning is
now exercised rather than described.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


def _enable_sandboxing(root: Path) -> None:
    """Say it rather than inherit it — see the call site."""
    config = root / ".rite" / "config.yaml"
    text = config.read_text()
    if "sandbox:" in text:
        config.write_text(
            "\n".join(
                "  enabled: true"
                if line.strip().startswith("enabled:")
                and "sandbox:" in text[: text.index(line)][-200:]
                else line
                for line in text.splitlines()
            )
            + "\n"
        )
    else:
        config.write_text(text.rstrip("\n") + "\nsandbox:\n  enabled: true\n")


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    import subprocess

    root = tmp_path / "p"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.chdir(root)
    assert CliRunner().invoke(cli, ["init", "--yes", "."]).exit_code == 0
    return root


class TestCredentialListDoesNotDemandTheRetiredModel:
    def test_an_unprovisioned_worker_token_is_not_reported_missing(
        self, project, monkeypatch
    ):
        """THE DEFECT. Sandboxing on, three Workers, and `rite credential
        list` named three `sandbox_token_*` under "missing — set each
        with:" — telling someone to provision per-Worker tokens on the
        command whose whole job is "what does this project need?"."""
        import rite_ai.credentials.store as store

        for w in ("alpha", "beta"):
            assert CliRunner().invoke(cli, ["add", "worker", w]).exit_code == 0
        monkeypatch.setattr(store, "keychain_is_readable", lambda: True)
        monkeypatch.setattr(
            store,
            "resolve",
            lambda k, c=None: store.Resolved(k, store.NOT_FOUND, "", k, k),
        )

        out = CliRunner().invoke(cli, ["credential", "list"]).output

        assert "sandbox_token_alpha" not in out, out
        assert "sandbox_token_beta" not in out

    def test_a_worker_token_that_exists_is_still_needed(self, project, monkeypatch):
        """The other half: somebody who DID opt in still needs its status
        and its rotation. Asserted as a difference from the case above, so
        this cannot pass on a build that simply lists nothing.

        Against `_keys_this_project_needs` rather than through `rite
        credential list`'s output, because the rendering depends on whether
        THIS machine has a readable keychain — and a CLI-level assertion
        here passed on macOS and failed on Linux CI, which is the same
        environment-dependence this file's other half exists to remove.
        """
        import rite_ai.credentials.store as store
        from rite_ai.cli.main import _keys_this_project_needs

        assert CliRunner().invoke(cli, ["add", "worker", "alpha"]).exit_code == 0
        # Sandboxing ON, explicitly. `rite init` decides it per platform —
        # yoloAI's backends are macOS-only — so a project created on Linux
        # has it off and needs no sandbox credential at all. Relying on the
        # default made this assert about the platform instead of about the
        # change: green here, `[]` on the Linux runner. Third time in this
        # area, and the same lesson as the doctor fixtures.
        _enable_sandboxing(project)
        monkeypatch.setattr(store, "keychain_is_readable", lambda: True)
        monkeypatch.setattr(
            store,
            "resolve",
            lambda k, c=None: store.Resolved(
                k,
                store.PROJECT if k == "sandbox_token_alpha" else store.NOT_FOUND,
                k,
                k,
                k,
            ),
        )

        assert "sandbox_token_alpha" in _keys_this_project_needs()


class TestTheWarningIsSilentOnlyForThisProjectsOwnToken:
    """The first cut asked "is the tier GLOBAL?" and called everything else
    "project". `resolve` has four tiers, and two of the others — a
    `RITE_GITHUB_TOKEN`, and the service's own `GITHUB_TOKEN` — are
    machine-wide by construction. So a token exported in a shell profile,
    which is the ordinary `gh` setup and usually reaches the whole account,
    was handed to a Worker with nothing printed: a warning the change
    REMOVED, on the case `resolve_worker_token`'s own docstring says must
    never become silent."""

    @staticmethod
    def _tier_for(monkeypatch, resolved_tier: str) -> str:
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(
            store,
            "get_scoped",
            lambda n, c=None: "TOKEN" if n == "github_token" else None,
        )
        monkeypatch.setattr(
            store,
            "resolve",
            lambda k, c=None: store.Resolved(k, resolved_tier, "a", "p", k),
        )
        return sb.resolve_worker_token("w1")[1]

    def test_an_environment_token_is_not_treated_as_this_projects(self, monkeypatch):
        """THE REGRESSION."""
        import rite_ai.credentials.store as store

        assert self._tier_for(monkeypatch, store.ENV) == "global"

    def test_an_unknowable_tier_warns_rather_than_stays_quiet(self, monkeypatch):
        """`get_scoped` and `resolve` are two separate lookups and do not
        walk identically; a locked keychain makes `resolve` answer
        NOT_FOUND while `get_scoped` still returns a value. Silence is the
        wrong direction to be wrong in."""
        import rite_ai.credentials.store as store

        assert self._tier_for(monkeypatch, store.NOT_FOUND) == "global"

    def test_the_projects_own_token_is_still_silent(self, monkeypatch):
        """The difference — without this the two above pass on a build that
        warns about everything, which is the state before the change."""
        import rite_ai.credentials.store as store

        assert self._tier_for(monkeypatch, store.PROJECT) == "project"


class TestTheWarningTextNamesRealCommands:
    """It named `rite credential adopt`, which does not exist. Nothing
    executed the warning, so nothing noticed — and `rite credential list`
    already printed the right command (`migrate`) for the same condition,
    so the two surfaces that diagnose one state disagreed."""

    def test_every_rite_command_it_suggests_exists(self, project, monkeypatch):
        """Read off the EMITTED warning, not the source: a scan of the file
        also picks up the comment explaining the old mistake, which is how
        this test first failed."""
        import re
        from unittest.mock import patch

        from rite_ai.cli.main import cli, credential
        from rite_ai.sandbox import SandboxResult

        assert CliRunner().invoke(cli, ["add", "worker", "w1"]).exit_code == 0
        runner = CliRunner()

        with (
            patch(
                "rite_ai.sandbox.resolve_worker_token",
                return_value=("TOKEN", "global"),
            ),
            patch(
                "rite_ai.sandbox.start_worker",
                return_value=SandboxResult(True, "started"),
            ),
        ):
            result = runner.invoke(cli, ["sandbox", "start", "w1"])

        named = set(re.findall(r"rite credential ([a-z-]+)", result.output))
        assert named, f"the warning suggested no command:\n{result.output}"
        missing = named - set(credential.commands) - set(cli.commands)
        assert not missing, (
            f"the warning names {sorted(missing)}, which `rite credential` "
            f"does not have: {sorted(credential.commands)}"
        )
