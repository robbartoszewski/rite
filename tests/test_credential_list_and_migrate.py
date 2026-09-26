"""Three defects in `rite credential list` / `migrate`, each with a test.

Two of these are worth more than their size.

`TestTheUnrecognisedKeyMarkerStillFires` guards a feature that exists
because of a mistake a real person made: `rite credential set
someone@example.com` stored the ADDRESS as a key name and reported
success, so JIRA looked configured and was not. The marker in `credential
list` is how that entry is identifiable afterwards. Namespacing gave every
account a "/", and a `or "/" in entry.name` short-circuit — added to stop
another project's accounts being called wrong — waved through everything
`credential set` now writes. The feature was still on screen and could no
longer fire.

`TestMigrateWritesNothingBeforeTheConfirm` guards the ordering rule this
project keeps rediscovering: do not perform the action before the question
that authorises it. `migrate` generated and PERSISTED a namespace inside
the confirm prompt's own argument list, so declining printed "left
unchanged" over a config file that had just been changed.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.credentials.store import RotationEntry

NS = "acme-1a2b3c"


def _project(tmp_path, *, namespace="", credential="", backend="jira"):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    body = f"ticket_backend:\n  type: {backend}\n  site: acme.atlassian.net\n"
    if credential:
        body += f"  credential: {credential}\n"
    body += "  projects:\n    workers: AC\n"
    if namespace:
        body += f"credentials:\n  namespace: {namespace}\n"
    (rite / "config.yaml").write_text(body)
    return tmp_path


def _stored(monkeypatch, names):
    monkeypatch.setattr(
        "rite_ai.credentials.store.list_for_rotation",
        lambda: [
            RotationEntry(name=n, last_set=None, source="file store") for n in names
        ],
    )


class TestListHonoursARenamedTokenKey:
    """`ticket_backend.credential` renames the JIRA token key, and it is
    the name `create_backend` actually reads. `credential list` hardcoded
    `jira_token`, so a project that had renamed its token was told it was
    missing one it holds — and told nothing about the one it really uses."""

    def test_the_renamed_key_is_the_one_reported(self, tmp_path, monkeypatch):
        root = _project(tmp_path, credential="acme_jira_token")
        monkeypatch.chdir(root)
        _stored(monkeypatch, [])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert result.exit_code == 0, result.output
        assert "acme_jira_token" in result.output
        # And it must not ALSO demand the default name the project renamed
        # away from — that is the half that reads as a second missing
        # credential.
        assert "rite credential set jira_token" not in result.output

    def test_the_default_still_applies_when_nothing_is_renamed(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        _stored(monkeypatch, [])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert "jira_token" in result.output


class TestTheUnrecognisedKeyMarkerStillFires:
    """The marker must survive namespacing. See the module docstring."""

    def test_a_value_typed_as_a_key_is_flagged_inside_this_project(
        self, tmp_path, monkeypatch
    ):
        """THE REGRESSION. `<ns>/someone@example.com` contains a "/", which
        is what the short-circuit waved through."""
        root = _project(tmp_path, namespace=NS)
        monkeypatch.chdir(root)
        _stored(monkeypatch, [f"{NS}/someone@example.com"])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert "not a credential key rite reads" in result.output, result.output
        assert f"rite credential remove {NS}/someone@example.com" in result.output

    def test_an_unnamespaced_value_is_still_flagged(self, tmp_path, monkeypatch):
        """An address stored before namespacing existed — the shape of the
        entry is what matters, not whose it was."""
        root = _project(tmp_path, namespace=NS)
        monkeypatch.chdir(root)
        _stored(monkeypatch, ["someone@example.com"])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert "not a credential key rite reads" in result.output

    def test_this_projects_real_keys_are_not_flagged(self, tmp_path, monkeypatch):
        root = _project(tmp_path, namespace=NS)
        monkeypatch.chdir(root)
        _stored(monkeypatch, [f"{NS}/jira_token", f"{NS}/jira_email"])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert "not a credential key rite reads" not in result.output

    def test_another_projects_account_is_named_not_condemned(
        self, tmp_path, monkeypatch
    ):
        """The case the short-circuit was reaching for, kept — but as its
        own state. We cannot validate another project's keys (we do not
        have its config), so calling them wrong would be a false accusation
        on every multi-project machine."""
        root = _project(tmp_path, namespace=NS)
        monkeypatch.chdir(root)
        _stored(monkeypatch, ["other-9f8e7d/jira_token"])
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value.source = "not_found"
            result = CliRunner().invoke(cli, ["credential", "list"])

        assert "not a credential key rite reads" not in result.output
        assert "another project (other-9f8e7d)" in result.output


class TestMigrateWritesNothingBeforeTheConfirm:
    """Declining must leave the disk alone. See the module docstring."""

    def _global_only(self, monkeypatch):
        from rite_ai.credentials.store import NOT_FOUND, CredentialInfo

        monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
        monkeypatch.setattr(
            "rite_ai.credentials.store.info",
            lambda n: CredentialInfo(
                name=n, source="file store" if n == "jira_token" else NOT_FOUND
            ),
        )
        monkeypatch.setattr(
            "rite_ai.credentials.store.get_account", lambda a: "THE-SECRET"
        )

    def test_declining_leaves_config_yaml_byte_identical(self, tmp_path, monkeypatch):
        """THE REGRESSION: a namespace was generated and persisted while
        the question was still on screen."""
        root = _project(tmp_path)  # no namespace yet — the case that wrote
        monkeypatch.chdir(root)
        config_path = root / ".rite" / "config.yaml"
        before = config_path.read_bytes()
        self._global_only(monkeypatch)
        stored = []
        monkeypatch.setattr(
            "rite_ai.credentials.store.store",
            lambda n, v: stored.append(n) or "file store",
        )

        result = CliRunner().invoke(
            cli, ["credential", "migrate", "jira_token"], input="n\n"
        )

        assert result.exit_code == 0, result.output
        assert "left unchanged" in result.output
        assert config_path.read_bytes() == before, (
            "declining rewrote config.yaml — 'left unchanged' was not true"
        )
        assert stored == []

    def test_the_prompt_discloses_the_config_write(self, tmp_path, monkeypatch):
        """If accepting also records a namespace, the question has to say
        so — that is a second effect being authorised."""
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        self._global_only(monkeypatch)
        monkeypatch.setattr(
            "rite_ai.credentials.store.store", lambda n, v: "file store"
        )

        result = CliRunner().invoke(
            cli, ["credential", "migrate", "jira_token"], input="n\n"
        )
        assert ".rite/config.yaml" in result.output

    def test_accepting_does_write_and_copy(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        config_path = root / ".rite" / "config.yaml"
        self._global_only(monkeypatch)
        stored = []
        monkeypatch.setattr(
            "rite_ai.credentials.store.store",
            lambda n, v: stored.append((n, v)) or "file store",
        )

        result = CliRunner().invoke(
            cli, ["credential", "migrate", "jira_token"], input="y\n"
        )

        assert result.exit_code == 0, result.output
        assert "credentials:" in config_path.read_text()
        assert len(stored) == 1
        account, value = stored[0]
        assert account.endswith("/jira_token")
        assert value == "THE-SECRET"

    def test_an_existing_namespace_names_the_real_target_in_the_prompt(
        self, tmp_path, monkeypatch
    ):
        """With a namespace already recorded there is nothing to invent, so
        the prompt shows the actual account rather than a placeholder."""
        root = _project(tmp_path, namespace=NS)
        monkeypatch.chdir(root)
        self._global_only(monkeypatch)
        monkeypatch.setattr(
            "rite_ai.credentials.store.store", lambda n, v: "file store"
        )

        result = CliRunner().invoke(
            cli, ["credential", "migrate", "jira_token"], input="n\n"
        )
        assert f"{NS}/jira_token" in result.output
        assert "<new namespace>" not in result.output


class TestStatusDoesNotRepeatTheWholeRemedy:
    """`rite status` asks each configured board role separately, and the
    missing-credential message is deliberately several lines — it names every
    account consulted and then the commands that fix it (§10.2).

    Multiplied by three roles that is fifteen lines to say one thing, and it
    pushed claims, workers and the handover line off the top of the report.
    Status says WHAT is wrong; the command that actually failed is where the
    remedy belongs."""

    def test_only_the_first_line_reaches_the_board_line(self):
        from rite_ai.reporting.status import BoardState, _format_boards

        multiline = (
            "jira_token not found — looked in: $RITE_JIRA_TOKEN, keychain "
            "'acme-1a2b3c/jira_token' (this project).\n"
            "  Set it for this project:  rite credential set jira_token\n"
            "  Or machine-wide:          rite credential set jira_token --global\n"
            "  What this project needs:  rite credential list"
        )
        boards = [
            BoardState(role=r, project_key="XYZ", error=multiline)
            for r in ("board", "workers", "testing")
        ]

        lines = _format_boards(boards)

        rendered = "\n".join(lines)
        assert "jira_token not found" in rendered
        # The remedy appears nowhere — not once, and certainly not three times.
        assert "rite credential set jira_token" not in rendered
        assert "What this project needs" not in rendered
        # One line per role, plus the section header.
        assert sum(1 for line in lines if "unavailable" in line) == 3
