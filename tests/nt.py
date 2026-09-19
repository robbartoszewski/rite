"""The CLI stopped advertising the credential model §5.3.4 retired.

§5.3.4: "Every Worker on a project receives every credential the project
holds. Not a per-Worker subset." §5.3.4.1 keeps `rite add worker
--scoped-token` but says what it now is: "an option a person chooses, not
the model, and not something any other part of the design assumes is in
force."

Two places still assumed it was in force, and both spoke to a NEW user at
the moment they were deciding how to set the project up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


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

    def test_a_worker_token_that_exists_is_still_listed(self, project, monkeypatch):
        """The other half: somebody who DID opt in still needs its status
        and its rotation. Asserted as a difference from the case above, so
        this cannot pass on a build that simply lists nothing."""
        import rite_ai.credentials.store as store

        assert CliRunner().invoke(cli, ["add", "worker", "alpha"]).exit_code == 0
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

        out = CliRunner().invoke(cli, ["credential", "list"]).output

        assert "sandbox_token_alpha" in out, out


class TestTheGlobalTokenWarning:
    """It fired on every `rite sandbox start` for the configuration §5.3.4
    calls correct, said the token was "machine-global" when it was the
    project's own, and prescribed the retired per-Worker token as the
    remedy."""

    def test_project_tier_does_not_warn_and_global_does(self, monkeypatch):
        """The difference, at the source the warning reads. A warning that
        fires for the intended configuration teaches the reader to ignore
        it — which is the one thing this warning must not become."""
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
            lambda k, c=None: store.Resolved(k, store.PROJECT, "a", "p", k),
        )
        assert sb.resolve_worker_token("w1")[1] == "project"

        monkeypatch.setattr(
            store,
            "resolve",
            lambda k, c=None: store.Resolved(k, store.GLOBAL, "a", "p", k),
        )
        assert sb.resolve_worker_token("w1")[1] == "global"
