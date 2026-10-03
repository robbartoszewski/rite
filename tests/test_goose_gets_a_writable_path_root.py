"""`GOOSE_PATH_ROOT` is a Worker value and must stay off a Manager (OL2).

Measured in `docs/design/spikes/OL1-ollama-inside-a-worker-sandbox.md`: inside a
seatbelt Worker `~/.local` is granted READ and not WRITE, so Goose panics in
`session_manager.rs` before it reaches the model. One variable moves its config,
session sqlite and logs together.

⚠ The asymmetry is the point. A local Manager already has writable state — its
profile grants `~/.config/goose` and `~/.local/share/goose` by exact path rather
than redirecting `HOME` (`ENGINE_HOME_IS_THE_OPERATORS`) — and that store holds
the conversation handle each resumed cycle names. Setting a root there would
hand a running Manager an empty session store.
"""

from __future__ import annotations

from rite_ai.local.goose_agent import goose_environment

ENDPOINT = "http://localhost:11434"
MODEL = "qwen3.8:latest"


def test_a_manager_gets_no_path_root():
    # The default, because `supervise` must not move a Manager's session store.
    env = goose_environment(ENDPOINT, MODEL, context_limit=32768)
    assert "GOOSE_PATH_ROOT" not in env


def test_a_worker_gets_the_root_it_asks_for():
    env = goose_environment(ENDPOINT, MODEL, path_root="/sbx/rite/goose")
    assert env["GOOSE_PATH_ROOT"] == "/sbx/rite/goose"


def test_the_root_does_not_disturb_the_rest():
    # The three that tell Goose which model to run, and where, are what the
    # 2026-09-25 mismatch turned on; a fourth key must not shift them.
    plain = goose_environment(ENDPOINT, MODEL, context_limit=32768)
    rooted = goose_environment(
        ENDPOINT, MODEL, context_limit=32768, path_root="/sbx/rite/goose"
    )
    assert {k: v for k, v in rooted.items() if k != "GOOSE_PATH_ROOT"} == plain
    assert plain["GOOSE_PROVIDER"] == "ollama"
    assert plain["GOOSE_MODEL"] == MODEL
    assert plain["OLLAMA_HOST"] == ENDPOINT
    assert plain["GOOSE_CONTEXT_LIMIT"] == "32768"


def test_an_empty_root_is_not_placed_as_an_empty_variable():
    # `GOOSE_PATH_ROOT=""` would point Goose at a relative path rather than
    # leaving it alone, so empty means absent.
    assert "GOOSE_PATH_ROOT" not in goose_environment(ENDPOINT, MODEL, path_root="")
