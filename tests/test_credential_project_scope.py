"""The flat-namespace collision, and the scoping that fixes it (SPEC §10.2).

`SERVICE_NAME = "rite"` keyed by a bare credential name was a FLAT namespace:
the stored name was the credential KEY, so every project on a machine addressed
the same keychain entry. Two projects could not hold two JIRA identities, and
on a machine running a client-privileged project beside anything else, the
client's token was the entry every other project read.

`test_flat_namespace_collides` reproduces that defect directly against the
pre-scoping naming rule. It is the regression this module exists for: if
scoping is ever reverted or bypassed, that test's scoped counterpart fails
while the flat one keeps passing, which is the signature of the bug returning.
"""

from __future__ import annotations

from rite_ai.config.models import CredentialsConfig
from rite_ai.credentials.store import (
    GLOBAL,
    PROJECT,
    is_valid_namespace,
    make_namespace,
    namespaced,
    project_account,
    resolve,
)

KEY = "jira_token"


class FakeKeychain:
    """The keychain as a dict of stored-name -> value, which is exactly the
    shape the real one has: ONE namespace per service, addressed by account.
    Collisions are therefore a property of the NAMES, and that is what these
    tests are about — no keyring, no machine state, no ordering luck."""

    def __init__(self):
        self.entries: dict[str, str] = {}

    def store(self, name: str, value: str) -> None:
        self.entries[name] = value


def test_flat_namespace_collides():
    """THE DEFECT. Under the old rule the stored name IS the key, so a second
    project storing its own JIRA token overwrites the first project's."""
    kc = FakeKeychain()

    # Two projects, two different JIRA identities, the old naming rule.
    kc.store(KEY, "client-privileged-token")
    kc.store(KEY, "side-project-token")

    assert len(kc.entries) == 1, "flat namespace should have exactly one entry"
    assert kc.entries[KEY] == "side-project-token"
    # The client-privileged token is simply gone, and — the worse half —
    # whichever survives is what BOTH projects now read.


def test_scoped_names_do_not_collide():
    """The fix: distinct scopes produce distinct stored names, so both
    identities coexist and each project reads its own."""
    kc = FakeKeychain()
    a = CredentialsConfig(namespace="client-3f9a2c")
    b = CredentialsConfig(namespace="side-7c1b04")

    name_a = project_account(KEY, a)
    name_b = project_account(KEY, b)
    assert name_a != name_b

    kc.store(name_a, "client-privileged-token")
    kc.store(name_b, "side-project-token")

    assert len(kc.entries) == 2
    assert kc.entries[name_a] == "client-privileged-token"
    assert kc.entries[name_b] == "side-project-token"


def test_scoped_name_shape():
    assert namespaced("acme-1a2b3c", KEY) == "acme-1a2b3c/jira_token"
    # No scope recorded yet => the pre-scoping layout, unchanged. This is
    # what keeps an un-migrated project working.
    assert namespaced("", KEY) == KEY


def test_make_namespace_is_unique_per_call():
    """Two projects with the SAME name must not collide — the suffix is why
    the scope is generated once and recorded, not recomputed."""
    assert make_namespace("backend", entropy="aaaaaa") != make_namespace(
        "backend", entropy="bbbbbb"
    )
    assert is_valid_namespace(make_namespace("backend"))
    # Names that are not already slug-shaped still produce a valid scope.
    for raw in ("Acme Lex!", "  ", "wygrane/sprawy", "A" * 80):
        assert is_valid_namespace(make_namespace(raw)), raw


def test_resolution_prefers_project_then_falls_back_to_global(monkeypatch):
    """Option 3's fallback, and the migration path in one: an existing
    machine-wide entry keeps working, and is reported AS a fallback rather
    than passed off as this project's own."""
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    creds = CredentialsConfig(namespace="acme-1a2b3c")
    present: set[str] = set()

    def fake_info(name):
        from rite_ai.credentials.store import NOT_FOUND, CredentialInfo

        return CredentialInfo(
            name=name, source="file store" if name in present else NOT_FOUND
        )

    monkeypatch.setattr("rite_ai.credentials.store.info", fake_info)

    # Only the global entry exists — the state a machine set up before scoping is in.
    present.add("jira_token")
    r = resolve(KEY, creds)
    assert r.tier == GLOBAL
    assert r.account == "jira_token"
    assert "machine-global" in r.describe()

    # Once a project-scoped entry exists it wins, without the global
    # entry being touched or removed.
    present.add("acme-1a2b3c/jira_token")
    r = resolve(KEY, creds)
    assert r.tier == PROJECT
    assert r.account == "acme-1a2b3c/jira_token"


def test_env_still_outranks_both(monkeypatch):
    """A sandboxed worker cannot read the keychain at all, so the env var
    has to stay the top tier — it is the only channel that reaches one."""
    from rite_ai.credentials.store import ENV

    monkeypatch.setenv("RITE_JIRA_TOKEN", "from-env")
    r = resolve(KEY, CredentialsConfig(namespace="acme-1a2b3c"))
    assert r.tier == ENV


class TestRemoveIsNotAmbiguous:
    """`rite credential remove jira_token` deleted the MACHINE-GLOBAL entry.

    Found by running the real installed CLI against a real keychain, which is
    the only way it could have been found: every unit test in the suite had
    exactly one of the two entries present, so the ambiguous case — a project
    entry AND a machine-global entry under the same key — never arose.

    What happened: `remove` matched the literal argument first. Inside a
    project that had just stored `<namespace>/jira_token`, typing the key
    deleted the bare `jira_token` that every OTHER project on the machine
    resolves through, and reported `removed credential 'jira_token'`, which
    reads as though the project's own copy had gone. A keychain delete does
    not come back.

    So the ambiguous case must REFUSE, not choose.
    """

    def _stored(self, monkeypatch, names):
        from rite_ai.credentials.store import RotationEntry

        monkeypatch.setattr(
            "rite_ai.credentials.store.list_for_rotation",
            lambda: [
                RotationEntry(name=n, last_set=None, source="file store") for n in names
            ],
        )

    def test_both_present_refuses_and_names_both(self, monkeypatch, tmp_path):
        import click.testing

        from rite_ai.cli.main import cli

        ns = "acme-1a2b3c"
        self._stored(monkeypatch, [f"{ns}/jira_token", "jira_token"])
        monkeypatch.setattr(
            "rite_ai.cli.main._project_credentials",
            lambda: CredentialsConfig(namespace=ns),
        )
        removed = []
        monkeypatch.setattr(
            "rite_ai.credentials.store.remove",
            lambda n: removed.append(n) or "removed",
        )

        result = click.testing.CliRunner().invoke(
            cli, ["credential", "remove", "jira_token", "--yes"]
        )

        assert result.exit_code == 2, result.output
        assert removed == [], f"refusing must delete NOTHING, deleted {removed}"
        assert f"{ns}/jira_token" in result.output
        assert "ambiguous" in result.output

    def test_only_the_project_entry_resolves_from_the_bare_key(
        self, monkeypatch, tmp_path
    ):
        """The convenience that made the bug worth having: with no global
        entry, the key still finds this project's account."""
        import click.testing

        from rite_ai.cli.main import cli

        ns = "acme-1a2b3c"
        self._stored(monkeypatch, [f"{ns}/jira_token"])
        monkeypatch.setattr(
            "rite_ai.cli.main._project_credentials",
            lambda: CredentialsConfig(namespace=ns),
        )
        removed = []
        monkeypatch.setattr(
            "rite_ai.credentials.store.remove",
            lambda n: removed.append(n) or "removed",
        )

        result = click.testing.CliRunner().invoke(
            cli, ["credential", "remove", "jira_token", "--yes"]
        )

        assert result.exit_code == 0, result.output
        assert removed == [f"{ns}/jira_token"]


class TestTheGlobalFallbackIsNeverSilent:
    """The fallback tier keeps existing installs working — and that is
    exactly why it has to announce itself.

    A project resolving through the shared machine-global entry while its
    owner believes it has its own is the flat namespace surviving under a
    new name: everything looks fixed, every project still reads one
    token. The warning is the only thing standing between those two
    states, so it is pinned here rather than left to a code reviewer to
    notice going missing.
    """

    def _only_global_exists(self, monkeypatch):
        from rite_ai.credentials.store import NOT_FOUND, CredentialInfo

        monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
        monkeypatch.setattr(
            "rite_ai.credentials.store.info",
            lambda n: CredentialInfo(
                name=n, source="file store" if n == "jira_token" else NOT_FOUND
            ),
        )

    def _fresh(self, monkeypatch):
        from rite_ai.credentials.store import _WARNED_GLOBAL

        monkeypatch.setattr(
            "rite_ai.credentials.store._WARNED_GLOBAL", set(_WARNED_GLOBAL) & set()
        )

    def test_it_warns_and_names_both_accounts(self, monkeypatch, capsys):
        from rite_ai.credentials.store import warn_if_global

        self._only_global_exists(monkeypatch)
        self._fresh(monkeypatch)

        message = warn_if_global(KEY, CredentialsConfig(namespace="acme-1a2b3c"))

        assert message, "the machine-global fallback was used and said nothing"
        err = capsys.readouterr().err
        assert "jira_token" in err
        # Both halves: what it USED, and what this project EXPECTED.
        assert "acme-1a2b3c/jira_token" in err
        assert "migrate" in err

    def test_it_says_nothing_when_the_project_has_its_own(self, monkeypatch, capsys):
        """Not a warning that cries wolf — tier 2 is silent."""
        from rite_ai.credentials.store import NOT_FOUND, CredentialInfo, warn_if_global

        monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
        monkeypatch.setattr(
            "rite_ai.credentials.store.info",
            lambda n: CredentialInfo(
                name=n,
                source="file store" if n == "acme-1a2b3c/jira_token" else NOT_FOUND,
            ),
        )
        self._fresh(monkeypatch)

        assert warn_if_global(KEY, CredentialsConfig(namespace="acme-1a2b3c")) is None
        assert capsys.readouterr().err == ""

    def test_an_un_namespaced_project_is_not_nagged(self, monkeypatch, capsys):
        """With no namespace there is no second account to move to, so the
        warning would name the entry it just used as the fix for itself."""
        from rite_ai.credentials.store import warn_if_global

        self._only_global_exists(monkeypatch)
        self._fresh(monkeypatch)

        assert warn_if_global(KEY, CredentialsConfig()) is None
        assert capsys.readouterr().err == ""


class TestAMissingCredentialSaysEnoughToAct:
    """A session finished five findings, could not file them, recorded "no
    Jira connector, CLI or credentials", and wrote them to a file. The
    credentials existed; the board existed; `rite board create` would have
    worked from where it was standing.

    Nothing it saw named which credential, where rite had looked, or what
    to type. This pins the three things that turn "no credentials" from a
    dead end into a next step — because the failure mode this project
    keeps producing is correct work landing in a file nobody reads.
    """

    def _error(self, credentials, monkeypatch):
        from rite_ai.tickets import BackendError, create_backend

        monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
        monkeypatch.delenv("RITE_JIRA_EMAIL", raising=False)
        monkeypatch.setattr(
            "rite_ai.credentials.store.get_scoped",
            lambda key, creds=None: (
                "someone@example.com" if key == "jira_email" else None
            ),
        )
        result = create_backend(
            "jira",
            site="x.atlassian.net",
            projects={"workers": "XYZ"},
            credentials=credentials,
        )
        assert isinstance(result, BackendError)
        return result.message

    def test_it_names_the_key_the_accounts_and_the_command(self, monkeypatch):
        message = self._error(CredentialsConfig(namespace="acme-7f3a9c21"), monkeypatch)
        # WHICH credential.
        assert "jira_token" in message
        # Under WHICH name — both accounts actually consulted. With §10.2
        # the account is no longer the key, so naming only the key sends
        # someone looking for an entry rite never asked for.
        assert "acme-7f3a9c21/jira_token" in message
        assert "machine-global" in message
        # The exact command, not a description of one.
        assert "rite credential set jira_token" in message
        assert "rite credential list" in message
        assert "RITE_JIRA_TOKEN" in message

    def test_an_un_namespaced_project_does_not_invent_an_account(self, monkeypatch):
        """It must report where it ACTUALLY looked. A project with no
        namespace looks under the bare key, and naming a scoped account it
        never consulted would send someone hunting for an entry that was
        never meant to exist."""
        message = self._error(CredentialsConfig(), monkeypatch)
        assert "keychain 'jira_token'" in message
        assert "/jira_token" not in message
