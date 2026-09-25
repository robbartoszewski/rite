"""The Slack bot token goes through the store rite already has (A2).

⚠ **A2 WAS REPORTED AS BLOCKED ON A CREDENTIAL-MODEL DECISION. IT WAS NOT**,
and the over-reading is worth recording because it nearly cost Robert a
question he did not need to answer. The reasoning was that three invariants
could not all hold: every secret must name a destination env var; every
secret naming one is injected into every Worker; and a known key must come
from a `SERVICES` entry.

All three hold. `claude_token` is the precedent — it names
`CLAUDE_CODE_OAUTH_TOKEN` and is injected into every Worker — and §5.3.4
already decided every Worker gets every credential the project holds, with
`worker_environment`'s docstring recording that a larger return is "a real
increase in blast radius, which is the cost the decision accepted". Slack is
a credential the project holds. Nothing new was needed.

**What the store buys, and why this is a ticket rather than a line:** a
per-project namespace, keychain storage, `rite credential check`, redaction,
and the guarantee these tests exist for — that the value never reaches a
command line.
"""

from __future__ import annotations

import keyring
import pytest

from rite_ai.credentials.services import SERVICES, service_key
from rite_ai.credentials.store import (
    ENV,
    GLOBAL,
    KNOWN_CREDENTIALS,
    SERVICE_NAME,
    is_known_name,
    resolve,
    worker_environment,
)

KEY = "slack_bot_token"
TOKEN = "xoxb-0000-1111-nottherealone"


class TestItIsAnOrdinaryCredential:
    def test_it_is_a_known_key_so_a_typo_is_refused_rather_than_stored(self):
        """⚠ The failure `KNOWN_CREDENTIALS` exists for: `set` used to accept
        any string and report success, so somebody who typed their email
        address believed JIRA was configured."""
        assert is_known_name(KEY)
        assert KNOWN_CREDENTIALS[KEY]

    def test_the_key_is_generated_by_the_service_not_hand_written(self):
        """The invariant I thought this broke. A known key must come from a
        service, and it does: `slack` + `bot_token`."""
        assert service_key("slack", "bot_token") == KEY

    def test_the_description_says_what_the_value_looks_like(self):
        """Which is what tells a user they copied the right one of Slack's
        several token kinds."""
        assert "xoxb" in KNOWN_CREDENTIALS[KEY].lower()

    def test_it_resolves_from_the_keychain_like_any_other(self, monkeypatch):
        monkeypatch.delenv(f"RITE_{KEY.upper()}", raising=False)
        keyring.set_password(SERVICE_NAME, KEY, TOKEN)
        assert resolve(KEY).tier == GLOBAL

    def test_the_environment_tier_reaches_a_sandboxed_reader(self, monkeypatch):
        """Tier 1, first deliberately: a sandboxed Worker cannot read the
        keychain at all, so an env var is the only channel that reaches it."""
        monkeypatch.setenv(f"RITE_{KEY.upper()}", TOKEN)
        resolved = resolve(KEY)
        assert resolved.tier == ENV
        assert resolved.account == f"RITE_{KEY.upper()}"

    def test_it_is_unset_when_nothing_provides_it(self, monkeypatch):
        monkeypatch.delenv(f"RITE_{KEY.upper()}", raising=False)
        assert resolve(KEY).tier not in (ENV, GLOBAL)


class TestTheSecretNeverBecomesAnArgument:
    def test_the_field_names_a_destination_rather_than_being_passed_around(self):
        """§10.5's boundary: rite stores and injects, and does not interpret.
        A name here is what stops a caller inventing one."""
        field = next(f for f in SERVICES["slack"].secrets if f.name == "bot_token")
        assert field.env == "SLACK_BOT_TOKEN"
        assert field.secret is True

    def test_it_is_not_on_the_tmux_argv_allowlist(self):
        """⚠ C6. `-e NAME=value` puts a value on tmux's argv where `ps` shows
        it to every local account. A mode name may travel that way; a token
        may not, and the allowlist is what keeps the distinction."""
        from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV, _on_tmux_argv

        assert "SLACK_BOT_TOKEN" not in ALLOWED_ON_TMUX_ARGV
        with pytest.raises(ValueError, match="readable by every local account"):
            _on_tmux_argv("SLACK_BOT_TOKEN", TOKEN)


class TestTheConsequenceIsNamedNotDiscovered:
    def test_every_worker_receives_it_because_every_worker_receives_everything(
        self, monkeypatch
    ):
        """⚠ **Slack is the first credential in that set a Worker cannot
        use** — the relay runs in the supervisor, on the host. §5.3.4 decided
        Workers are fungible and get everything, and accepted the blast
        radius; this is the first case where that rule costs without buying.
        Pinned so it is a known consequence rather than a surprise."""
        monkeypatch.setenv(f"RITE_{KEY.upper()}", TOKEN)
        assert worker_environment(None).get("SLACK_BOT_TOKEN") == TOKEN

    def test_the_service_note_says_so(self):
        note = SERVICES["slack"].note.lower()
        assert "workers" in note and "no use for it" in note
