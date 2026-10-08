"""SCRUM-82: `rite credential check` accepts what `rite credential set` takes.

`rite credential set claude` takes a SERVICE name and stores the key
`claude_token`. `check` resolved whatever it was handed as a KEY, so

    $ rite credential check claude
    claude: not set
      run `rite credential set claude`, …

— advising the command the operator had just run. Measured 2026-10-08 with the
token genuinely stored: `list` said set, `check claude_token` said set, and
`check claude` said not set. Three commands, two vocabularies, and the one
whose whole job is to answer yes-or-no spoke the wrong one.

⚠ The key is built with `services.service_key`, never by joining here: a
`Field.name` is the SUFFIX of the stored key (`<service>_<name>`), so claude's
field is `token` and the key is `claude_token`. The first attempt at this fix
joined nothing and reported "token: not set".
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.credentials.services import SERVICES, service_key


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    return tmp_path.resolve()


def _check(name, stored):
    """`credential check <name>` with `stored` the keys that resolve."""

    class Found:
        def __init__(self, ok):
            self.found = ok

        def describe(self):
            return "set (this project)" if self.found else "not set"

    import rite_ai.credentials.store as store

    real = store.resolve
    try:
        store.resolve = lambda key, _creds=None: Found(key in stored)
        return CliRunner().invoke(cli, ["credential", "check", name])
    finally:
        store.resolve = real


def test_a_service_name_resolves_to_the_key_that_set_stores(project):
    result = _check("claude", {"claude_token"})
    assert result.exit_code == 0, result.output
    assert "claude_token" in result.output, result.output
    assert "set" in result.output


def test_the_key_it_looked_up_is_the_one_services_defines(project):
    """Not a string this test joined either — asked of the registry."""
    expected = service_key("claude", SERVICES["claude"].secrets[0].name)
    assert expected == "claude_token", expected
    assert expected in _check("claude", {expected}).output


def test_a_missing_service_credential_still_exits_non_zero(project):
    """The CONTROL that keeps the fix from being "always say yes".

    A check that reports failure through exit code 0 is not a check.
    """
    result = _check("claude", set())
    assert result.exit_code != 0, result.output
    assert "not set" in result.output


def test_a_bare_key_name_still_works(project):
    """`check jira_token` is what the command documents; it must not regress."""
    result = _check("claude_token", {"claude_token"})
    assert result.exit_code == 0, result.output
    assert "claude_token" in result.output


def test_an_unknown_name_is_treated_as_a_key_and_not_invented(project):
    """Something that is neither a service nor stored must not come back set."""
    result = _check("not_a_service_or_key", set())
    assert result.exit_code != 0, result.output
