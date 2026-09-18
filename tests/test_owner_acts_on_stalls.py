"""The Owner acts on a Manager that has gone quiet (§2.3, P2-3b, P2-5c).

Surfacing a stall is half the job; the other half is that the stalled
machine's tickets stop being assigned to it and its claims stop blocking
everybody else. Both halves existed and neither had a caller — D-14's second
trigger and the claim expiry were complete, correct and never invoked.

Driven through `run_tick`, because the defect was the absence of a caller
and a test that called them directly would have been green throughout.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rite_ai.claims.ledger import Claim
from rite_ai.coordination.claims_state import (
    CLAIMS_KEY,
    publish_claims,
    published_claims,
)
from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.state_layer import Present
from rite_ai.scheduler import run_tick


def machine(tmp_path: Path, name: str, remote: str) -> Path:
    root = tmp_path / name
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: s\n"
        "  projects: {workers: A, board: B}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        f"coordination:\n  managers: [alpha, beta]\n  remote: '{remote}'\n"
    )
    (rite / "machine").write_text(f"{name}\n")
    return root


def lines(result) -> list[str]:
    return [m for m in result.messages if m.startswith("coordination")]


@pytest.fixture
def owner_and_stalled(tmp_path):
    """alpha is Owner; beta beat 40 minutes ago holding a claim."""
    remote = tmp_path / "coordination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    alpha = machine(tmp_path, "alpha", str(remote))
    layer = GitStateLayer(str(remote), tmp_path / "seed.git")
    long_ago = datetime.now(UTC) - timedelta(minutes=40)
    publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=long_ago)
    publish_claims(
        layer,
        "beta",
        [Claim(paths=["src/shared.py"], worker="w1", ticket="ABC-9", timestamp=1.0)],
        now=long_ago,
    )
    return alpha, str(remote), tmp_path


def claims_of(remote: str, tmp_path: Path, machine_name: str, tag: str):
    read = GitStateLayer(remote, tmp_path / f"read-{tag}.git").read_state(CLAIMS_KEY)
    assert isinstance(read, Present)
    return published_claims(json.loads(read.value.decode())).get(machine_name, [])


def test_the_owner_hands_over_a_stalled_managers_work(owner_and_stalled):
    alpha, _, _ = owner_and_stalled
    out = lines(run_tick(alpha))
    assert any("handed over beta's work" in line and "ABC-9" in line for line in out), (
        out
    )


def test_the_owner_expires_the_stalled_managers_claims(owner_and_stalled):
    """Otherwise the path stays blocked for every machine, for ever: the
    claim only expires on a lapsed heartbeat, which is exactly the state
    beta is in — and nothing was checking."""
    alpha, remote, tmp_path = owner_and_stalled
    run_tick(alpha)
    assert claims_of(remote, tmp_path, "beta", "after") == []


def test_it_happens_once_not_every_five_minutes(owner_and_stalled):
    """A stalled machine stays stalled. Without idempotency the Owner would
    post a handover comment on the same tickets every tick for as long as
    the machine is down — and the expiry is what provides it: the second
    tick finds no claims and says nothing."""
    alpha, _, _ = owner_and_stalled
    first = lines(run_tick(alpha))
    second = lines(run_tick(alpha))
    assert any("handed over" in line for line in first)
    assert not any("handed over" in line for line in second), second
    assert not any("expired" in line for line in second), second


def test_the_audit_log_does_not_grow_every_tick(owner_and_stalled):
    """The half the scheduler's own output cannot show. A stalled machine
    stays stalled, and the handover records its action on the message log
    BEFORE doing it — so without a guard the Owner appends an audit record
    every five minutes, for ever, about work it handed over once.

    Caught by a mutation target that did not match: the guard I thought I
    had added was never applied, the edit silently did nothing, and the
    test above still passed because the tick only PRINTS when there are
    tickets."""
    alpha, remote, tmp_path = owner_and_stalled
    layer = GitStateLayer(remote, tmp_path / "log-read.git")

    run_tick(alpha)
    after_first = len(layer.read_messages().items)
    run_tick(alpha)
    run_tick(alpha)
    assert len(layer.read_messages().items) == after_first, (
        "the Owner wrote an audit record for a handover it did not perform"
    )


def test_a_manager_that_is_merely_quiet_is_left_alone(tmp_path):
    """Cannot tell is not gone (D-58). A machine that has never published a
    heartbeat may be one nobody has set up, and taking its work would be a
    guess with consequences."""
    remote = tmp_path / "coordination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    alpha = machine(tmp_path, "alpha", str(remote))
    layer = GitStateLayer(str(remote), tmp_path / "seed.git")
    publish_claims(
        layer,
        "beta",
        [Claim(paths=["src/shared.py"], worker="w1", ticket="ABC-9", timestamp=1.0)],
        now=datetime.now(UTC),
    )
    out = lines(run_tick(alpha))
    assert not any("handed over" in line for line in out), out
    assert claims_of(str(remote), tmp_path, "beta", "quiet") != []


def test_a_manager_that_is_NOT_owner_does_not_act(owner_and_stalled):
    """Every Manager can see the same stall. If each acted, one stalled
    machine would collect a handover comment per machine per tick — the
    role exists to decide who acts."""
    alpha, remote, tmp_path = owner_and_stalled
    run_tick(alpha)  # alpha takes the role and does the duties

    gamma = machine(tmp_path, "gamma", remote)
    (gamma / ".rite" / "config.yaml").write_text(
        (gamma / ".rite" / "config.yaml")
        .read_text()
        .replace("managers: [alpha, beta]", "managers: [alpha, beta, gamma]")
    )
    publish_claims(
        GitStateLayer(remote, tmp_path / "reseed.git"),
        "beta",
        [Claim(paths=["src/again.py"], worker="w1", ticket="ABC-10", timestamp=1.0)],
        now=datetime.now(UTC) - timedelta(minutes=40),
    )
    out = lines(run_tick(gamma))
    assert not any("handed over" in line for line in out), out
    assert claims_of(remote, tmp_path, "beta", "notowner") != []
