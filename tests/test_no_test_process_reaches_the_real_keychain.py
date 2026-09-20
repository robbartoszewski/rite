"""No test may reach this machine's login keychain.

WHY THIS EXISTS. Twice in one session a verification run stopped dead for
half an hour. `SecurityAgent` — the macOS keychain authorisation dialog —
came up at 12:21, the pytest log's last write was 12:21, and at 12:53 there
was still nothing. A run that takes 440 seconds took 1946. The suite was
waiting on a window nobody was looking at.

⚠ `credentials/store.py` wraps its keyring reads in `try/except Exception`,
which is correct and does not help. **A prompt is not an exception.** It is
the absence of an answer — no output, no timeout, no failure — which is the
same shape as every other defect this release has turned up.

WHAT THE FIX IS, and what it deliberately is NOT. `conftest` installs an
in-memory `keyring` BACKEND for every test. That substitutes the operating
system, not rite: `store.py` still runs its own resolution order, its
env-var fallback, its exception handling and its reporting, and the tests
still exercise every line of it. Mocking `store.get_credential` would have
removed the code under test and traded a hang for a suite that proves
nothing.

⚠ THIS FILE IS THE CHECK THAT THE FIX IS IN FORCE, and it is written to
fail rather than to pass. A suite that reaches no keychain because nobody
happened to click anything is indistinguishable, from its own output, from
a suite that cannot reach one. So these assert the backend is substituted,
not that no dialog appeared.
"""

from __future__ import annotations

import keyring

from rite_ai.credentials.store import SERVICE_NAME

# The real backends, by module prefix. A test process that has any of these
# active can raise a GUI prompt, block, or silently read the developer's own
# secrets — the third being the worst, because it passes.
_OS_BACKEND_MODULES = (
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.chainer",
)


def test_the_active_keyring_backend_is_not_the_operating_system():
    backend = keyring.get_keyring()
    module = type(backend).__module__
    assert not module.startswith(_OS_BACKEND_MODULES), (
        f"the active keyring backend is {module}.{type(backend).__name__}. A "
        "test reaching it can raise a macOS authorisation dialog, and the "
        "suite then blocks with no output, no timeout and no failure — "
        "measured twice at ~30 minutes each"
    )


def test_the_substitute_is_the_suite_s_own_and_not_a_disabled_backend():
    """`keyring` also ships a null backend that answers None to everything.

    That would stop the hang and would also make every credential test
    vacuous — reads returning None regardless of what was written. The
    substitute has to be a real store, so assert it round-trips.
    """
    backend = keyring.get_keyring()
    assert type(backend).__name__ == "_InMemoryKeyring", (
        f"expected the suite's in-memory backend, got {type(backend).__name__}"
    )
    backend.set_password(SERVICE_NAME, "probe-account", "probe-value")
    assert backend.get_password(SERVICE_NAME, "probe-account") == "probe-value", (
        "the substituted backend does not store what it is given, so every "
        "credential test above it is asserting against nothing"
    )


def test_rite_s_own_credential_store_still_runs_its_real_logic():
    """The fix must not have replaced the code under test.

    Written through rite's public API rather than the backend, so the
    resolution order in `store.py` is the thing being exercised.
    """
    from rite_ai.credentials import store

    keyring.get_keyring().set_password(SERVICE_NAME, "rite-probe-token", "s3cret")
    assert store._keychain_get("rite-probe-token") == "s3cret", (
        "`store._keychain_get` did not read through the substituted backend "
        "— either the substitution is at the wrong layer, or the store no "
        "longer uses keyring and this file is guarding the wrong thing"
    )


def test_each_test_starts_with_an_empty_store():
    """Function-scoped, so one test's credential cannot leak into another's
    resolution order. This is the paired half of the test below."""
    assert keyring.get_keyring().get_password(SERVICE_NAME, "leaked") is None
    keyring.get_keyring().set_password(SERVICE_NAME, "leaked", "from-the-first-test")


def test_the_previous_test_s_credential_did_not_survive():
    assert keyring.get_keyring().get_password(SERVICE_NAME, "leaked") is None, (
        "a credential written by another test is still readable. The "
        "substitute is shared across tests, which reintroduces exactly the "
        "cross-test coupling conftest exists to remove"
    )
