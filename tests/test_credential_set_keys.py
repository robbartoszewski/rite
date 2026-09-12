"""`rite credential set` took any string as a key and reported success.

The reported defect, verbatim:

    $ rite credential set someone@example.com
    Value:
    Repeat for confirmation:
    stored 'someone@example.com' in keychain

The user was setting up JIRA. `set` takes a credential KEY, so the address
became a key name; `jira_email` stayed unset; and the success line read
equally well as "your secret is stored". Nothing surfaced the mistake until
a later failure naming a key the user had never typed. Same class as the
`credential check` exit-0 bug: the tool answering confidently while the
user's actual intent silently failed.

Every test here patches `keyring` — the real keychain is never touched.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.credentials.store import (
    is_known_name,
    looks_like_email,
    remove,
    suggest_name,
)

THE_REPORTED_INVOCATION = "someone@example.com"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Registry writes go to a temp dir, never ~/.rite/ — and PROJECT writes
    go to a temp dir, never this checkout.

    The `chdir` is the second half and was missing. `RITE_HOME_DIR` covers
    the cross-project registry; it does nothing about the project root,
    which `_find_project_root` resolves by walking up from the CWD and
    falling back to it. Under `CliRunner` the CWD was the repository, so
    `credential set` ran `_ensure_namespace`, which writes `.rite/config.yaml`
    BEFORE storing anything — by design, so the name survives — and the file
    landed in rite's own checkout with a namespace derived from whatever the
    directory happened to be called.

    That is how a `wt2-…`/`probe-…` namespace kept appearing at the top of
    this repo and getting swept into commits by `git add -A`: three times,
    each removed by hand afterwards. Nothing in the suite was asserting on
    the repository, so nothing noticed.
    """
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    for var in ("RITE_JIRA_EMAIL", "RITE_JIRA_TOKEN", "RITE_GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class TestTheReportedDefect:
    def test_an_email_address_is_refused_not_stored(self, isolated_home):
        """The exact invocation. It must not reach the keychain at all."""
        with patch("keyring.set_password") as mock_set:
            result = CliRunner().invoke(
                cli,
                ["credential", "set", THE_REPORTED_INVOCATION],
                input="secret\nsecret\n",
            )
        assert result.exit_code != 0, result.output
        mock_set.assert_not_called()

    def test_the_refusal_names_the_command_the_user_wanted(self, isolated_home):
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", THE_REPORTED_INVOCATION],
                input="secret\nsecret\n",
            )
        # §10.5: the refusal now names the SERVICE, not the key. Pointing
        # someone at `set jira_email` still required them to know that JIRA
        # means two keys and what each is called — the knowledge whose
        # absence produced this invocation in the first place.
        assert "rite credential set jira" in result.output

    def test_an_email_gets_its_own_message_not_just_unknown_key(self, isolated_home):
        """An email is unambiguously a VALUE — no key rite reads could look
        like one — so it earns a more specific diagnosis than a typo would."""
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", THE_REPORTED_INVOCATION],
                input="secret\nsecret\n",
            )
        assert "looks like a credential value" in result.output

    def test_the_secret_is_not_prompted_for_before_the_key_is_validated(
        self, isolated_home
    ):
        """Only visible against the real CLI: validating after the prompt
        made the reporter type and confirm their secret before being told
        the key was wrong. No stdin is supplied here — if the command
        still prompts first, it fails on empty input instead of refusing
        the key cleanly."""
        with patch("keyring.set_password") as mock_set:
            result = CliRunner().invoke(
                cli, ["credential", "set", THE_REPORTED_INVOCATION], input=""
            )
        assert result.exit_code == 2, result.output
        assert "looks like a credential value" in result.output
        mock_set.assert_not_called()

    def test_the_valid_keys_are_listed(self, isolated_home):
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", THE_REPORTED_INVOCATION],
                input="secret\nsecret\n",
            )
        for key in ("jira_email", "jira_token", "github_token"):
            assert key in result.output


class TestUnknownKeys:
    def test_unknown_key_is_refused(self, isolated_home):
        with patch("keyring.set_password") as mock_set:
            result = CliRunner().invoke(
                cli, ["credential", "set", "jra_tokn"], input="s\ns\n"
            )
        assert result.exit_code != 0
        mock_set.assert_not_called()

    def test_a_near_miss_suggests_the_real_key(self, isolated_home):
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli, ["credential", "set", "jira_emai"], input="s\ns\n"
            )
        assert "jira_email" in result.output

    def test_allow_unknown_opts_back_in(self, isolated_home):
        """Arbitrary keys stay possible — behind a flag, not by default."""
        with patch("keyring.set_password") as mock_set:
            result = CliRunner().invoke(
                cli,
                ["credential", "set", "my_custom_key", "--allow-unknown"],
                input="s\ns\n",
            )
        assert result.exit_code == 0, result.output
        mock_set.assert_called_once()


class TestKnownKeysStillWork:
    @pytest.mark.parametrize(
        "key", ["jira_email", "jira_token", "github_token", "sandbox_token_alpha"]
    )
    def test_known_key_is_stored(self, isolated_home, key):
        with patch("keyring.set_password") as mock_set:
            result = CliRunner().invoke(cli, ["credential", "set", key], input="s\ns\n")
        assert result.exit_code == 0, result.output
        mock_set.assert_called_once()

    def test_success_line_says_the_argument_was_the_key(self, isolated_home):
        """ "stored 'X' in keychain" read equally well as "your secret X is
        stored", which is how a mistyped key went unnoticed."""
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli, ["credential", "set", "jira_email"], input="s\ns\n"
            )
        assert "under key 'jira_email'" in result.output

    def test_success_reports_what_is_still_unset(self, isolated_home):
        """The useful question after setting one credential is "am I done?"."""
        with (
            patch("keyring.set_password"),
            patch("keyring.get_password", return_value=None),
        ):
            result = CliRunner().invoke(
                cli, ["credential", "set", "jira_email"], input="s\ns\n"
            )
        assert "credential status:" in result.output
        assert "jira_token" in result.output
        assert "not set" in result.output

    def test_the_secret_is_never_echoed(self, isolated_home):
        with patch("keyring.set_password"):
            result = CliRunner().invoke(
                cli,
                ["credential", "set", "jira_token"],
                input="hunter2-do-not-print\nhunter2-do-not-print\n",
            )
        assert "hunter2-do-not-print" not in result.output


class TestClassificationHelpers:
    @pytest.mark.parametrize("value", ["someone@example.com", "a.b@c.co.uk", "x@y.dev"])
    def test_emails_are_recognised(self, value):
        assert looks_like_email(value)

    @pytest.mark.parametrize(
        "value", ["jira_email", "sandbox_token_alpha", "github_token", "not@anemail"]
    )
    def test_key_names_are_not_mistaken_for_emails(self, value):
        assert not looks_like_email(value)

    def test_sandbox_tokens_are_known_by_prefix(self):
        assert is_known_name("sandbox_token_alpha")
        assert not is_known_name("sandbox_token_")

    def test_a_project_configured_token_key_is_accepted(self):
        """`ticket_backend.credential` renames the JIRA token key, so
        validation must not reject the name the project is set up to use."""
        assert is_known_name("acme_jira_pat", extra=("acme_jira_pat",))
        assert not is_known_name("acme_jira_pat")

    def test_suggestion_for_a_typo(self):
        assert suggest_name("jira_emai") == "jira_email"

    def test_no_suggestion_for_something_unrelated(self):
        assert suggest_name("zzzzzzzz") is None


class TestRemovalIsPossible:
    """`set` could create an entry no rite command could delete — so a key
    typed by mistake stayed in the user's keychain permanently."""

    def test_a_stored_credential_can_be_removed(self, isolated_home):
        with (
            patch("keyring.set_password"),
            patch("keyring.delete_password") as mock_del,
        ):
            CliRunner().invoke(
                cli,
                ["credential", "set", "sandbox_token_alpha"],
                input="s\ns\n",
            )
            result = CliRunner().invoke(
                cli, ["credential", "remove", "sandbox_token_alpha", "--yes"]
            )
        assert result.exit_code == 0, result.output
        mock_del.assert_called_once()

    def test_removal_drops_the_registry_entry(self, isolated_home):
        from rite_ai.credentials.store import list_for_rotation, store

        with patch("keyring.set_password"):
            store("sandbox_token_alpha", "s")
        assert "sandbox_token_alpha" in {e.name for e in list_for_rotation()}

        with patch("keyring.delete_password"):
            remove("sandbox_token_alpha")
        assert "sandbox_token_alpha" not in {e.name for e in list_for_rotation()}

    def test_removing_something_not_stored_fails_loudly(self, isolated_home):
        result = CliRunner().invoke(
            cli, ["credential", "remove", "never_stored", "--yes"]
        )
        assert result.exit_code != 0
        assert "no credential stored" in result.output

    def test_an_unrecognised_key_can_still_be_removed(self, isolated_home):
        """The recovery path for the reported defect: an entry created
        before this validation existed must still be removable."""
        from rite_ai.credentials.store import list_for_rotation, store

        with patch("keyring.set_password"):
            store(THE_REPORTED_INVOCATION, "s")
        with patch("keyring.delete_password"):
            result = CliRunner().invoke(
                cli, ["credential", "remove", THE_REPORTED_INVOCATION, "--yes"]
            )
        assert result.exit_code == 0, result.output
        assert THE_REPORTED_INVOCATION not in {e.name for e in list_for_rotation()}


class TestListing:
    def test_listing_needs_no_prompts(self, isolated_home):
        """Previously the only way to see stored credentials was to start
        `rotate`, a mutating interactive flow."""
        from rite_ai.credentials.store import store

        with patch("keyring.set_password"):
            store("jira_email", "s")
        result = CliRunner().invoke(cli, ["credential", "list"])
        assert result.exit_code == 0, result.output
        assert "jira_email" in result.output

    def test_an_unrecognised_key_is_flagged_in_the_listing(self, isolated_home):
        from rite_ai.credentials.store import store

        with patch("keyring.set_password"):
            store(THE_REPORTED_INVOCATION, "s")
        result = CliRunner().invoke(cli, ["credential", "list"])
        assert "not a credential key rite reads" in result.output
        assert f"rite credential remove {THE_REPORTED_INVOCATION}" in result.output

    def test_empty_listing_says_so(self, isolated_home):
        result = CliRunner().invoke(cli, ["credential", "list"])
        assert result.exit_code == 0
        assert "no credentials stored" in result.output


class TestTheSuiteDoesNotWriteIntoThisRepository:
    """The leak above, asserted against the repository itself.

    Kept separate from the fixture fix because the fix is one line and the
    property is the point: a test run must leave rite's own checkout exactly
    as it found it. `.rite/config.yaml` is deliberately NOT ignored here —
    it records the credential namespace a fresh clone needs — so anything
    that creates it is one `git add -A` away from being committed.
    """

    def test_credential_set_does_not_create_a_config_in_the_checkout(
        self, isolated_home
    ):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        config = repo_root / ".rite" / "config.yaml"
        existed = config.exists()

        with patch("keyring.set_password"), patch("keyring.get_password") as get:
            get.return_value = None
            CliRunner().invoke(
                cli,
                ["credential", "set", "jira_token"],
                input="secret-value\nsecret-value\n",
            )

        assert config.exists() == existed, (
            f"the suite wrote {config} into rite's own checkout — it is "
            "un-ignored on purpose, so the next `git add -A` commits a "
            "namespace derived from this directory's name"
        )
