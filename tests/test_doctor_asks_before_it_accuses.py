"""`rite doctor` keeps "could not ask" apart from "the answer is no".

v0.7.0a4 lane doctor: S28 and S32.

**S28.** Doctor answered "is there a GitHub token?" and stopped, so a
read-only fine-grained PAT passed the report and then `rite sandbox start`
refused the Worker — the check a person reads BEFORE starting said nothing
about the thing that would stop them. The probe that settles it already
existed (`push_access_refusal`), but it collapses "could not ask GitHub" into
a refusal. That is right where the choice is whether to START a Worker, and
wrong in a report: it would tell someone offline to reissue a credential that
was fine. So the report uses `push_access`, which keeps three answers apart,
and the live probe is opt-in (`--network`) so a plain `rite doctor` stays fast
and works offline.

**S32.** Declaring a Manager on a single-machine project made doctor say the
machine "is not enrolled — it will not publish a heartbeat, stand for Owner,
or take over another machine's work". Every clause is about other machines,
and a solo project has none. `coordination.managers` alone defeated the
Phase-1 early return, so the warning arrived the moment declaring a Manager
became an ordinary thing to do.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import _doctor_worker_push_access, cli
from rite_ai.config.models import CoordinationConfig
from rite_ai.coordination.identity import enrolment
from rite_ai.sandbox import (
    CloneRemote,
    PushAccess,
    push_access,
    push_access_refusal,
)

# --- S32: a solo project is not an un-enrolled one ---------------------------

SCARE = "not enrolled"


def _machine(root: Path, text: str | None) -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    if text is not None:
        (root / ".rite" / "machine").write_text(text)
    return root


SOLO = CoordinationConfig(managers=["lead"], remote="")
TEAM = CoordinationConfig(managers=["lead"], remote="git@example.com:t/c.git")


@pytest.mark.parametrize("machine", [None, "lead\n"])
def test_a_single_machine_project_with_a_manager_is_never_scolded(tmp_path, machine):
    """The whole point of S32: a solo setup that declared a Manager is a
    correct setup, with or without `.rite/machine`."""
    root = _machine(tmp_path / "solo", machine)

    assert enrolment(root, SOLO) is None


def test_a_multi_machine_project_with_no_machine_file_still_is(tmp_path):
    """The control. Where there ARE other machines, not being enrolled is
    exactly the thing worth saying."""
    root = _machine(tmp_path / "team", None)

    problem = enrolment(root, TEAM)

    assert problem and SCARE in problem


def test_a_project_coordinating_with_nobody_is_still_silent(tmp_path):
    assert enrolment(_machine(tmp_path / "p", "lead\n"), CoordinationConfig()) is None


# --- S28: three answers from the probe ---------------------------------------


class _Answer:
    def __init__(self, status, content_type):
        self.status, self.headers = status, {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _remotes():
    return [
        CloneRemote(
            clone="app", url="https://github.com/acme/app.git", github=("acme", "app")
        )
    ]


RECEIVE = "application/x-git-receive-pack-advertisement"


def _http_error(code):
    import urllib.error

    return urllib.error.HTTPError("u", code, "no", {}, None)


VERDICTS = [
    ("ok", _Answer(200, RECEIVE), None),
    # GitHub's verdict on the token: these three, and only these three.
    ("cannot_push", None, _http_error(401)),
    ("cannot_push", None, _http_error(403)),
    ("cannot_push", None, _http_error(404)),
    # Not a verdict — the question was not answered.
    ("unknown", None, TimeoutError("timed out")),
    ("unknown", None, _http_error(500)),  # GitHub is down
    ("unknown", None, _http_error(503)),
    ("unknown", None, _http_error(429)),  # rate limited
    ("unknown", _Answer(200, "text/html"), None),  # a proxy, not GitHub
]


@pytest.mark.parametrize(("kind", "answer", "raises"), VERDICTS)
def test_the_probe_gives_three_answers(kind, answer, raises):
    """⚠ An OUTAGE IS NOT A VERDICT. A 500, a 503, a 429 or a 200 from a proxy
    all used to sort as `cannot_push`, so `rite doctor --network` during a
    GitHub incident would report "token cannot push" about a token that is
    fine, and send someone to reissue a working credential — the exact false
    accusation S28 exists to prevent."""
    with patch(
        "urllib.request.urlopen",
        **({"side_effect": raises} if raises else {"return_value": answer}),
    ):
        got = push_access(_remotes(), "tok")

    assert got.kind == kind
    # ⚠ The invariant S28 exists for: only a KNOWN refusal is a fault.
    assert got.is_a_problem is (kind == "cannot_push")


@pytest.mark.parametrize(("kind", "answer", "raises"), VERDICTS)
def test_start_still_collapses_them_to_two(kind, answer, raises):
    """The control on the OTHER caller: `rite sandbox start` must keep
    refusing an unchecked token. Splitting the probe must not relax it."""
    with patch(
        "urllib.request.urlopen",
        **({"side_effect": raises} if raises else {"return_value": answer}),
    ):
        refusal = push_access_refusal("alpha", _remotes(), "tok")

    assert (refusal is None) is (kind == "ok")
    if kind == "unknown":
        assert "refused rather than trusted" in refusal


# --- S28: what the REPORT does with each answer, on and off the network ------


def _project_with_a_worker(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    (root / "workers" / "alpha").mkdir(parents=True)
    (root / "workers" / "alpha" / "worker.yml").write_text("worker:\n  name: alpha\n")
    return root


@pytest.fixture
def doctor_probe(monkeypatch):
    """Everything the check needs, with the probe counted rather than run."""
    import rite_ai.credentials.store as store
    import rite_ai.sandbox as sb

    monkeypatch.setattr(store, "store_is_readable", lambda: True)
    monkeypatch.setattr(sb, "resolve_worker_token", lambda w, c=None: ("tok", "t"))
    monkeypatch.setattr(sb, "clone_remotes", lambda d: _remotes())
    calls: list[str] = []

    def probe(remotes, token, timeout=30):
        calls.append(token)
        return probe.verdict

    probe.verdict = PushAccess("ok")
    monkeypatch.setattr(sb, "push_access", probe)
    return probe, calls


KINDS = [
    PushAccess("ok"),
    PushAccess("cannot_push", owner="acme", repo="app", why="read but not write"),
    PushAccess("unknown", owner="acme", repo="app", reason="TimeoutError"),
]


@pytest.mark.parametrize("verdict", KINDS, ids=lambda v: v.kind)
@pytest.mark.parametrize("network", [False, True], ids=["no-flag", "--network"])
def test_the_report_across_every_verdict_and_both_modes(
    tmp_path, capsys, doctor_probe, verdict, network
):
    """THE invariant. Over verdict {ok, cannot_push, unknown} x probe
    {off, on}: the live probe fires only under `--network`, and
    could-not-check is NEVER counted as a problem."""
    probe, calls = doctor_probe
    probe.verdict = verdict
    root = _project_with_a_worker(tmp_path)
    problems: list[str] = []

    _doctor_worker_push_access(root, [], None, problems, network)
    out = capsys.readouterr().out

    # 1. The probe is opt-in, in every verdict.
    assert (len(calls) == 1) is network, f"probe fired={calls} network={network}"
    if not network:
        assert "not checked" in out and "--network" in out
        assert problems == [], "an unrun check is not a fault"
        return
    # 2. Only a known refusal is counted.
    assert (problems != []) is (verdict.kind == "cannot_push"), problems
    # 3. And each answer is said in its own words.
    if verdict.kind == "ok":
        assert "can push" in out and "CANNOT" not in out
    elif verdict.kind == "cannot_push":
        assert "CANNOT push" in out and "acme/app" in out
    else:
        assert "could not check" in out and "TimeoutError" in out
        assert "CANNOT" not in out, "an unreachable network is not a bad token"


def test_a_project_with_no_workers_says_nothing_either_way(
    tmp_path, capsys, doctor_probe
):
    probe, calls = doctor_probe
    root = tmp_path / "bare"
    (root / ".rite").mkdir(parents=True)

    _doctor_worker_push_access(root, [], None, [], True)

    assert capsys.readouterr().out == "" and calls == []


# --- doctor MAY post, and only when asked ------------------------------------


@pytest.mark.parametrize("network", [False, True], ids=["no-flag", "--network"])
def test_the_slack_delivery_post_happens_only_under_network(
    tmp_path, monkeypatch, capsys, network
):
    """⚠ Posting is allowed HERE and nowhere else in setup. `rite credential
    set slack` must never put a message in a channel; `rite doctor --network`
    is the opposite case — the person ASKED whether Slack works, and the only
    answer that settles it is a message that arrives. Still opt-in.

    ⚠ And it must be a POST. `_call` sends a GET when it is given `params`
    and a JSON POST when it is given `payload`; a first draft passed the
    message as `params`, so the check issued a GET with the text in the query
    string. Nothing says Slack honours `chat.postMessage` that way, so the
    check could have reported a delivery that never happened. The fake below
    tells the two apart — an earlier one did not, which is why the draft
    passed.
    """
    import rite_ai.managers.slack as slack_mod
    from rite_ai.cli.main import _doctor_slack

    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        "slack:\n  owner_user: U0C4HK552HF\n  broadcast_channel: '#all-rite'\n"
    )
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-1")
    posts: list[dict] = []
    gets: list[str] = []

    def call(method, token, params=None, payload=None):
        if payload is None:
            # This is what `_call` turns into a GET.
            gets.append(method)
            return {"ok": True}
        posts.append({"method": method, **payload})
        return {"ok": True, "channel": "C1", "ts": "1.0"}

    monkeypatch.setattr(slack_mod, "_call", call)
    monkeypatch.setattr(slack_mod, "probe", lambda *a, **k: [])
    problems: list[str] = []

    _doctor_slack(root, problems, network=network)
    out = capsys.readouterr().out

    assert (len(posts) == 1) is network, f"posts={posts} gets={gets}"
    assert not [m for m in gets if m == "chat.postMessage"], (
        "chat.postMessage was sent as a GET"
    )
    if network:
        assert posts[0]["channel"] == "#all-rite"
        assert posts[0]["text"]
        assert "slack delivery: ok" in out
    else:
        assert "slack delivery" not in out
    assert problems == []


def test_a_slack_refusal_of_the_test_post_is_a_problem(tmp_path, monkeypatch, capsys):
    """And it is counted, unlike could-not-check: Slack ANSWERED and said no."""
    import rite_ai.managers.slack as slack_mod
    from rite_ai.cli.main import _doctor_slack

    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        "slack:\n  broadcast_channel: '#all-rite'\n"
    )
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-1")
    monkeypatch.setattr(
        slack_mod,
        "_call",
        lambda m, t, params=None, payload=None: {
            "ok": False,
            "error": "channel_not_found",
        },
    )
    monkeypatch.setattr(slack_mod, "probe", lambda *a, **k: [])
    problems: list[str] = []

    _doctor_slack(root, problems, network=True)

    assert "slack delivery: FAILED" in capsys.readouterr().out
    assert problems and "slack delivery" in problems[0]


# --- S32, end to end: the only measure that would have caught it -------------


def _solo_project(tmp_path, *, managers: list[str], remote: str = "") -> Path:
    """A project with nothing wrong but the thing under test. Sandbox off, so
    a machine without a stored Claude token is not a second finding."""
    from rite_ai.cli.init import run_init

    root = tmp_path / "solo"
    root.mkdir()
    run_init(root, yes=True)
    config = root / ".rite" / "config.yaml"
    text = config.read_text().replace("enabled: true", "enabled: false", 1)
    roles = "\n".join(f"    - name: {m}\n      preset: lead" for m in managers)
    text += (
        "coordination:\n"
        f"  managers: [{', '.join(managers)}]\n"
        + (f"  remote: {remote}\n" if remote else "")
        + f"  manager_roles:\n{roles}\n"
    )
    config.write_text(text)
    return root


def test_doctor_exits_zero_on_a_solo_project_that_declared_a_manager(
    tmp_path, monkeypatch
):
    """⚠ **The assertion that would have caught the half-fix.** The first S32
    test asserted the absence of ONE sentence, and passed while `rite doctor`
    still exited 1 on the very setup the finding is about: a second path
    (`coordination/config_check`) called a declared Manager with no `remote`
    "N manager(s) listed but no `remote` … no election can ever happen".

    Reproduced by hand before this was written: `rite init --yes`, `rite add
    manager lead --preset lead`, `rite doctor` -> exit 1. What S32 asks for is
    an exit code, so that is what is asserted.
    """
    import rite_ai.sandbox as sb

    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_CREDENTIAL_DIR", str(tmp_path / "creds"))
    root = _solo_project(tmp_path, managers=["lead"])
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert "coordination:" not in result.output, result.output
    assert result.exit_code == 0, result.output


def test_a_remote_with_nobody_eligible_is_still_a_problem(tmp_path, monkeypatch):
    """The control on the other side. Removing the solo warning must not take
    the real one with it: a `remote` and an empty `managers` list is still
    nobody eligible to be Owner."""
    from rite_ai.coordination.config_check import coordination_problems

    said = coordination_problems(
        CoordinationConfig(managers=[], remote="git@example.com:t/c.git")
    )

    assert any("eligible to be Owner" in p for p in said), said


def test_two_managers_with_no_remote_is_still_reported():
    """The boundary, and the existing invariant this must not take with it.
    A reader who listed several Managers is describing a fleet, and a fleet
    with nowhere to coordinate through cannot elect. Only the solo case went
    quiet. `test_coordination_config_check` asserts this too, and caught a
    first fix that widened too far."""
    from rite_ai.coordination.config_check import coordination_problems

    said = coordination_problems(CoordinationConfig(managers=["lead", "helper"]))

    assert any("no election can ever happen" in p for p in said), said
