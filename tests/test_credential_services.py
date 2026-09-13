"""Setting credentials by SERVICE rather than by key (SPEC §10.5).

The interface took a key — `jira_email` — which is rite's vocabulary, and
required the user to know that JIRA means two keys and what each is called.
Someone reaching for their JIRA login typed their email address as the
argument; rite stored the ADDRESS as a key name and said "stored". Validating
the service and then asking the right questions makes that unavailable rather
than merely detectable, which is the difference this module pins.

`TestEveryServiceCarriesItsOwnFields` is the one to keep. A single hardcoded
username-then-token pair would pass a JIRA test and be wrong for GitHub, and
the pressure would then be to make every non-conforming service a `custom` —
which is how `custom` becomes the dumping ground for everything real.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.credentials.services import SERVICES, is_service, service_key
from rite_ai.credentials.store import KNOWN_CREDENTIALS


class TestEveryServiceCarriesItsOwnFields:
    def test_github_has_no_username_field(self):
        """A fine-grained PAT carries its own identity. Asking for a
        username would be asking for something GitHub does not use, and the
        user would have to invent an answer."""
        github = SERVICES["github"]
        assert [f.name for f in github.fields] == ["token"]

    def test_claude_is_one_secret_delivered_as_the_oauth_token(self):
        """What `claude setup-token` prints, injected under the name yoloAI
        reads for the claude agent."""
        claude = SERVICES["claude"]
        assert [(f.name, f.secret, f.env) for f in claude.fields] == [
            ("token", True, "CLAUDE_CODE_OAUTH_TOKEN")
        ]
        assert service_key("claude", "token") == "claude_token"

    def test_jira_asks_for_the_address_before_the_secret(self):
        jira = SERVICES["jira"]
        assert [f.name for f in jira.fields] == ["site", "email", "token", "board"]
        assert [f.name for f in jira.secrets] == ["email", "token"]

    def test_the_address_is_not_hidden_and_the_token_is(self):
        """Hiding the email makes the one field most likely to carry a typo
        impossible to check; hiding the token is the point."""
        by_name = {f.name: f for f in SERVICES["jira"].fields}
        assert by_name["email"].secret is False
        assert by_name["token"].secret is True

    def test_every_secret_names_its_destination_env_var(self):
        """§10.5's boundary: a credential is a name, fields, and a
        destination env var. rite must not need to know more than that.

        Config fields are exempt — they are not injected, they are written
        to config.yaml, which is the point of the split."""
        for svc in SERVICES.values():
            for f in svc.secrets:
                assert f.env, f"{svc.name}.{f.name} has no destination env var"
                assert f.env.isupper()

    def test_config_fields_are_not_secrets_and_name_a_config_path(self):
        """A JIRA site and board key are the same for the whole team and
        are not credentials. Sending them to the keychain would put shared
        config in a per-person store and leave the next clone to rediscover
        it."""
        for svc in SERVICES.values():
            for f in svc.config_fields:
                assert f.secret is False, f"{svc.name}.{f.name} is config, not secret"
                assert "." in f.config_path


class TestTheKeysAreTheOnesThatAlreadyExisted:
    """Migration is free, and that is a design constraint rather than luck:
    `<service>_<field>` was chosen because it reproduces the existing names.
    If this ever fails, someone's stored credentials just became unreachable
    under names nothing looks up."""

    def test_generated_keys_match_the_known_set_exactly(self):
        """SECRET fields only — config fields never become keychain keys."""
        generated = {
            service_key(s.name, f.name) for s in SERVICES.values() for f in s.secrets
        }
        assert generated == set(KNOWN_CREDENTIALS)


class TestSettingByService:
    def _project(self, tmp_path):
        rite = tmp_path / ".rite"
        rite.mkdir(parents=True)
        (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        (rite / "config.yaml").write_text(
            "ticket_backend:\n  type: jira\n  site: x\n"
            "credentials:\n  namespace: acme-1a2b3c\n"
        )
        return tmp_path

    def test_it_stores_every_field_scoped_to_the_project(self, tmp_path, monkeypatch):
        root = self._project(tmp_path)
        monkeypatch.chdir(root)
        stored = {}
        with patch(
            "keyring.set_password",
            side_effect=lambda svc, acct, val: stored.update({acct: val}),
        ):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", "jira"],
                # site, email, token (+confirm), board key
                input="team.atlassian.net\nsomeone@example.com\nSEKRIT-XYZ\nSEKRIT-XYZ\nBEN\n",
            )
        assert result.exit_code == 0, result.output
        # Secrets to the keychain...
        assert stored == {
            "acme-1a2b3c/jira_email": "someone@example.com",
            "acme-1a2b3c/jira_token": "SEKRIT-XYZ",
        }
        # ...and the non-secret half to the COMMITTED config, which is what
        # makes the next clone one command instead of two discoveries.
        written = (root / ".rite" / "config.yaml").read_text()
        assert "team.atlassian.net" in written
        assert "BEN" in written
        assert "SEKRIT-XYZ" not in written, "a secret reached config.yaml"

    def test_the_single_key_form_still_works(self, tmp_path, monkeypatch):
        """Scripts, the dogfood guide and `add worker`'s own skip message
        all name a single key. Taking a service must not retire that."""
        root = self._project(tmp_path)
        monkeypatch.chdir(root)
        stored = {}
        with patch(
            "keyring.set_password",
            side_effect=lambda svc, acct, val: stored.update({acct: val}),
        ):
            result = CliRunner().invoke(
                cli, ["credential", "set", "jira_token", "--value", "just-one"]
            )
        assert result.exit_code == 0, result.output
        assert stored == {"acme-1a2b3c/jira_token": "just-one"}

    def test_a_service_refuses_value_because_it_has_several_fields(
        self, tmp_path, monkeypatch
    ):
        root = self._project(tmp_path)
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["credential", "set", "jira", "--value", "x"])
        assert result.exit_code == 2
        assert "several fields" in result.output


class TestTheRefusalPointsAtAService:
    def test_an_email_is_answered_with_the_service_not_the_key(
        self, tmp_path, monkeypatch
    ):
        """THE REPORTED MISTAKE. Naming `jira_email` still required knowing
        that JIRA means two keys — the knowledge whose absence produced the
        invocation."""
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["credential", "set", "someone@example.com"])
        assert result.exit_code == 2
        assert "rite credential set jira" in result.output
        # And the listing leads with services.
        assert "Services rite can set up for you" in result.output
        assert result.output.index("Services rite can set up") < result.output.index(
            "Or one individual key"
        )

    def test_is_service_does_not_match_a_key(self):
        assert is_service("jira")
        assert not is_service("jira_token")
        assert not is_service("someone@example.com")


class TestWhatAPersonActuallyTypes:
    """Found by running the command cold, before handing it to the person
    who made the original mistake. Both of these failed on the first try.

    The interface exists so that nobody has to know rite's vocabulary. A
    service name rejected on a capital letter, or a one-character typo
    answered with a catalogue instead of a correction, puts that knowledge
    right back in the way."""

    def test_the_products_own_capitalisation_is_accepted(self):
        """`JIRA` is how Atlassian writes it and how this project's own
        docs write it, so it is what someone types from memory."""
        from rite_ai.credentials.services import canonical_service

        for typed in ("jira", "JIRA", "Jira", " jira ", "GitHub", "GITHUB"):
            assert canonical_service(typed) is not None, typed

    def test_the_canonical_form_is_what_gets_stored(self, tmp_path, monkeypatch):
        """Case-insensitive input must not produce case-varying keys — two
        spellings storing to two accounts would be worse than rejecting one."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        (rite / "config.yaml").write_text(
            "ticket_backend:\n  type: jira\n  site: ''\n  projects: {}\n"
            "credentials:\n  namespace: acme-1a2b3c\n"
        )
        monkeypatch.chdir(tmp_path)
        stored = {}
        with patch(
            "keyring.set_password",
            side_effect=lambda s, a, v: stored.update({a: v}),
        ):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", "JIRA"],
                input="team.atlassian.net\nme@x.com\nTOK\nTOK\nBEN\n",
            )
        assert result.exit_code == 0, result.output
        assert set(stored) == {
            "acme-1a2b3c/jira_email",
            "acme-1a2b3c/jira_token",
        }

    def test_a_typo_is_corrected_not_catalogued(self):
        from rite_ai.credentials.services import suggest_service

        assert suggest_service("jria") == "jira"
        assert suggest_service("githb") == "github"
        assert suggest_service("completely-unrelated") is None

    def test_the_typo_message_names_the_command_to_run(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["credential", "set", "jria"])
        assert result.exit_code == 2
        assert "rite credential set jira" in result.output
        assert "is not a service rite knows" in result.output


class TestSettingAServiceLeavesTheGlobalEntryAlone:
    """`jira_email` exists machine-wide from before namespacing. Setting the
    service inside a project must not consume it — losing the one credential
    that survived would be the worst possible first impression of this
    interface."""

    def test_the_scoped_write_does_not_touch_the_bare_key(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        (rite / "config.yaml").write_text(
            "ticket_backend:\n  type: jira\n  site: ''\n  projects: {}\n"
            "credentials:\n  namespace: acme-1a2b3c\n"
        )
        monkeypatch.chdir(tmp_path)
        keychain = {"jira_email": "PRE-EXISTING-GLOBAL"}
        with patch(
            "keyring.set_password",
            side_effect=lambda s, a, v: keychain.update({a: v}),
        ):
            CliRunner().invoke(
                cli,
                ["credential", "set", "jira"],
                input="team.atlassian.net\nnew@x.com\nTOK\nTOK\nBEN\n",
            )
        assert keychain["jira_email"] == "PRE-EXISTING-GLOBAL"
        assert keychain["acme-1a2b3c/jira_email"] == "new@x.com"
