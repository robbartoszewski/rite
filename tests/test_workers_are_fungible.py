"""Every Worker gets every credential the project holds (SPEC §5.3.4).

This is a deliberate trade, not an oversight, and the test exists because it
looks like one. §5.3.3 used to specify per-Worker scoping and call it least
privilege; the decision was to drop the repo bound between Workers so that
Workers stay **fungible** — a Worker that lacked a credential another had would
differ in CAPABILITY, and assignment would then have to reason about which
Worker *can* do a job rather than which one is free. That is an assignment
engine, and it is a large thing to build to hold a bound that buys little once
the permission bound (contents + pull requests) is already in place.

What survives, and is asserted here: one credential per Worker rather than a
shared one, and a scope bounded by the PROJECT's repos rather than by
everything the person can reach.
"""

from __future__ import annotations

from unittest.mock import patch

from rite_ai.config.models import CredentialsConfig
from rite_ai.credentials.store import worker_environment

NS = "acme-1a2b3c"


def _resolves(mapping):
    """Patch the scoped lookup so only `mapping`'s keys exist."""
    return patch(
        "rite_ai.credentials.store.get_scoped",
        side_effect=lambda key, creds=None: mapping.get(key),
    )


class TestEveryWorkerGetsEveryCredential:
    def test_all_project_credentials_are_delivered(self):
        with _resolves(
            {
                "jira_email": "me@example.com",
                "jira_token": "JT",
                "github_token": "GT",
            }
        ):
            env = worker_environment(CredentialsConfig(namespace=NS))

        assert env == {
            "JIRA_EMAIL": "me@example.com",
            "JIRA_API_TOKEN": "JT",
            "GITHUB_TOKEN": "GT",
        }

    def test_the_env_names_come_from_the_service_definition(self):
        """rite delivers `JIRA_API_TOKEN`, the name the service declares —
        not a name of rite's own invention. §10.5: store and inject, do not
        interpret."""
        from rite_ai.credentials.services import SERVICES

        declared = {f.env for s in SERVICES.values() for f in s.secrets}
        with _resolves({"jira_email": "e", "jira_token": "t", "github_token": "g"}):
            env = worker_environment(CredentialsConfig(namespace=NS))
        assert set(env) <= declared

    def test_a_credential_the_project_does_not_have_is_simply_absent(self):
        """Not an empty string — absent. A worker seeing `JIRA_TOKEN=` would
        treat it as configured and fail somewhere further away."""
        with _resolves({"github_token": "GT"}):
            env = worker_environment(CredentialsConfig(namespace=NS))
        assert env == {"GITHUB_TOKEN": "GT"}
        assert "JIRA_API_TOKEN" not in env

    def test_the_workers_own_git_token_wins_over_the_machine_global_one(self):
        """Still one token per Worker (§5.3.3) — the fungibility decision
        changed the SCOPE they share, not the count. A compromise stays
        attributable to one Worker."""
        with _resolves({"github_token": "MACHINE-GLOBAL"}):
            env = worker_environment(
                CredentialsConfig(namespace=NS), worker_token="THIS-WORKERS-OWN"
            )
        assert env["GITHUB_TOKEN"] == "THIS-WORKERS-OWN"

    def test_two_workers_on_one_project_receive_identical_sets(self):
        """THE PROPERTY. Fungibility means any worker can take any ticket, so
        their credential sets must not differ — if this ever fails, assignment
        has acquired a matching problem."""
        creds = CredentialsConfig(namespace=NS)
        with _resolves({"jira_email": "e", "jira_token": "t", "github_token": "g"}):
            w1 = worker_environment(creds, worker_token="w1-token")
            w2 = worker_environment(creds, worker_token="w2-token")
        assert set(w1) == set(w2)
        # Identical in what they can REACH; distinct only in whose token it is.
        assert w1["GITHUB_TOKEN"] != w2["GITHUB_TOKEN"]
        assert {k: v for k, v in w1.items() if k != "GITHUB_TOKEN"} == {
            k: v for k, v in w2.items() if k != "GITHUB_TOKEN"
        }


class TestTheProvisioningPromptAsksForProjectScope:
    def test_it_names_every_project_module_not_the_workers_subset(
        self, tmp_path, monkeypatch
    ):
        """The prompt is what the user actually acts on when creating the
        PAT, so a prompt still asking for the worker's subset would produce
        exactly the narrow token §5.3.4 says not to make."""
        import click.testing

        from rite_ai.cli.main import cli

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
        (rite / "modules.yaml").write_text(
            "modules:\n"
            "  alpha: {path: alpha/, url: https://example.com/alpha.git}\n"
            "  beta:  {path: beta/,  url: https://example.com/beta.git}\n"
        )
        (rite / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        )
        monkeypatch.chdir(tmp_path)

        result = click.testing.CliRunner().invoke(
            cli,
            ["add", "worker", "w1", "--modules", "alpha", "--scoped-token"],
            input="n\n",
        )

        # Created with ONE module...
        assert "1 module(s)" in result.output
        # ...but the token must cover BOTH, because any worker may take any
        # ticket.
        assert "alpha.git" in result.output
        assert "beta.git" in result.output, (
            "the prompt asked for a token scoped to the worker's subset — "
            "that is the narrow token §5.3.4 says not to create"
        )


class TestSandboxEnabledDoesNotScopeCredentials:
    """`sandbox.enabled` governs whether Workers run in a sandbox. It is
    not a statement about how their credentials are scoped.

    While the two were wired together, turning sandboxing on also made
    `rite add worker` provision a per-Worker token — so flipping a sandbox
    default would have changed the credential model without anyone asking
    for it.
    """

    def test_enabling_sandboxing_does_not_prompt_for_a_per_worker_token(
        self, tmp_path, monkeypatch
    ):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        root = tmp_path / "p"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text(
            "project:\n  name: p\n  role: manager\n"
        )
        (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        )
        monkeypatch.chdir(root)
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))

        result = CliRunner().invoke(cli, ["add", "worker", "solo"])
        assert result.exit_code == 0, result.output
        assert "scoped to THIS PROJECT" not in result.output
        assert "store the token now?" not in result.output
