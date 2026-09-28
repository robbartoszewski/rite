"""What needs the person stays pending until it reaches one (RP1 piece 2).

Robert, 2026-09-28: "did this actually reach a human" must be observable. A
question is not confirmed by being written, or by being posted to Slack: a
post reaches a channel. It is confirmed by the Owner's reply in its thread,
their reaction (when the app may read reactions), or by being shown to a
person by `rite replies`. Until then it is listed at every check-in, and its
thread is read past the relay's usual 10-thread and 24-hour limits.
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import checkins, pending
from rite_ai.managers.mailbox import OUTBOX, QUESTION, REPLY, send
from rite_ai.managers.slack import THREADS_MAX, Listener

OWNER = "U0WNER"
DAY = 86400.0


class Slack:
    """A fake Slack: posts, thread replies, and reactions."""

    def __init__(self, reactions: str = "ok"):
        self.posts: list[dict] = []
        self.replies: dict[tuple[str, str], list[dict]] = {}
        self.reacted: dict[tuple[str, str], list[dict]] = {}
        self.reactions = reactions
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        self.calls.append((method, dict(args)))
        if method == "auth.test":
            return {"ok": True, "user_id": "UR1TE"}
        if method == "chat.postMessage":
            self.posts.append(args)
            target = args["channel"]
            channel = (
                "D1" if target.startswith("U") else "C1" if target[0] == "#" else target
            )
            return {"ok": True, "channel": channel, "ts": f"{100 + len(self.posts)}.0"}
        if method == "conversations.history":
            return {"ok": True, "messages": []}
        if method == "conversations.replies":
            oldest = float(args.get("oldest") or 0)
            got = self.replies.get((args["channel"], args["ts"]), [])
            return {"ok": True, "messages": [m for m in got if float(m["ts"]) > oldest]}
        if method == "reactions.get":
            if self.reactions == "missing":
                return {
                    "ok": False,
                    "error": "missing_scope",
                    "needed": "reactions:read",
                }
            key = (args["channel"], args["timestamp"])
            return {"ok": True, "message": {"reactions": self.reacted.get(key, [])}}
        raise AssertionError(method)


class Clock:
    def __init__(self, now: float = 150.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    return tmp_path


def _relay(root: Path, slack: Slack, clock: Clock, owner: str = OWNER) -> Listener:
    """A relay past its first run, with tracking already started (as `rite
    start` starts it before the first session)."""
    pending.sync(root, "lead")
    listener = Listener(
        token="t",
        manager="lead",
        owner=owner,
        broadcast="#all-rite",
        project=root,
        clock=clock,
    )
    listener.open(call=slack)
    listener.post_replies(call=slack)
    return listener


def _read_threads(listener: Listener, slack: Slack, clock: Clock, rounds: int = 40):
    """Poll until every due thread has been read, stepping the clock."""
    got = []
    for _ in range(rounds):
        got.extend(listener.poll(call=slack))
        clock.now += 31
    return got


def _waiting(root: Path) -> list[str]:
    pending.sync(root, "lead")
    return [i.first for i in pending.waiting(root, "lead")]


# --- what is tracked ---------------------------------------------------------


def test_a_question_is_pending_a_reply_is_not(tmp_path):
    root = _project(tmp_path)
    pending.sync(root, "lead")
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    send(root, "lead", OUTBOX, "no kind at all")
    pending.sync(root, "lead")
    assert _waiting(root) == ["which schema", "no kind at all"]


def test_a_checkin_is_pending_only_when_it_holds_questions(tmp_path):
    root = _project(tmp_path)
    pending.sync(root, "lead")
    checkins._deliver_checkin(root, "lead")
    assert _waiting(root) == []
    checkins.defer(root, "lead", "rename the flag?", "doing ticket 14")
    checkins._deliver_checkin(root, "lead")
    assert [w.startswith("Check-in") for w in _waiting(root)] == [True]


def test_on_upgrade_what_is_already_there_predates_tracking_and_is_said(tmp_path):
    root = _project(tmp_path)
    send(root, "lead", OUTBOX, "an old question", kind=QUESTION)
    said = pending.sync(root, "lead")
    assert "1 message(s) already in 'lead''s outbox predate it" in said
    assert _waiting(root) == []
    [item] = pending.items(root, "lead")
    assert item.confirmed_how == pending.PREDATES
    send(root, "lead", OUTBOX, "a new question", kind=QUESTION)
    assert pending.sync(root, "lead") == ""
    assert _waiting(root) == ["a new question"]


def test_rite_start_starts_tracking_before_the_first_session(tmp_path):
    """So in a NEW project the first question is tracked, not "predates"."""
    import inspect

    from rite_ai.managers import supervise as sup

    source = inspect.getsource(sup._supervise)
    assert "pending.sync(root, manager)" in source
    assert source.index("pending.sync(root, manager)") < source.index("while True:")


# --- posting is not reaching ---------------------------------------------------


def test_posting_does_not_confirm_it(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    assert (item.channel, item.ts) == ("D1", f"{100 + len(slack.posts)}.0")
    _read_threads(listener, slack, clock)
    assert _waiting(root) == ["which schema"]


def test_the_owners_reply_in_its_thread_confirms_it_and_says_so(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    slack.replies[("D1", item.ts)] = [{"user": OWNER, "text": "v2", "ts": "500.0"}]
    got = _read_threads(listener, slack, clock)
    assert _waiting(root) == []
    [done] = pending.items(root, "lead")
    assert done.confirmed_how == pending.BY_THREAD_REPLY and done.confirmed_at == 500.0
    assert any("reached the person" in line for line in listener.news())
    assert any("> v2" in m for m in got), "the answer reaches the Manager too"


def test_someone_elses_reply_does_not_confirm_it(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    slack.replies[("D1", item.ts)] = [{"user": "U0THER", "text": "v2", "ts": "500.0"}]
    _read_threads(listener, slack, clock)
    assert _waiting(root) == ["which schema"]


def test_with_no_owner_any_persons_reply_confirms_it(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock, owner="")
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    slack.replies[("C1", item.ts)] = [{"user": "U0THER", "text": "v2", "ts": "500.0"}]
    _read_threads(listener, slack, clock)
    assert _waiting(root) == []


def test_the_owners_reaction_confirms_it_when_reactions_can_be_read(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    slack.reacted[("D1", item.ts)] = [{"name": "eyes", "users": [OWNER]}]
    _read_threads(listener, slack, clock)
    [done] = pending.items(root, "lead")
    assert done.confirmed_how == pending.BY_REACTION


def test_without_reactions_read_it_is_said_once_and_only_a_reply_confirms(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(reactions="missing"), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    _read_threads(listener, slack, clock)
    said = [line for line in listener.news() if "reactions:read" in line]
    assert len(said) == 1
    assert _waiting(root) == ["which schema"]
    assert sum(1 for m, _ in slack.calls if m == "reactions.get") == 1


# --- a pending thread outlives the relay's limits ----------------------------


def test_a_pending_thread_outlives_threads_max_and_the_24_hour_horizon(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    for n in range(THREADS_MAX + 2):
        send(root, "lead", OUTBOX, f"progress {n}", kind=REPLY)
    listener.post_replies(call=slack)
    clock.now += 3 * DAY
    assert any(r.ts == item.ts for r in listener.roots)
    slack.replies[("D1", item.ts)] = [
        {"user": OWNER, "text": "v2", "ts": f"{clock.now}"}
    ]
    got = _read_threads(listener, slack, clock, rounds=20)
    assert _waiting(root) == []
    assert any("> v2" in m for m in got)


def test_an_old_pending_thread_is_read_slowly(tmp_path):
    from rite_ai.managers.slack import PENDING_SLOW_SECONDS

    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    clock.now += 3 * DAY
    before = len([c for c in slack.calls if c[0] == "conversations.replies"])
    for _ in range(int(PENDING_SLOW_SECONDS // 31) * 2):
        listener.poll(call=slack)
        clock.now += 31
    reads = len([c for c in slack.calls if c[0] == "conversations.replies"]) - before
    assert 1 <= reads <= 3, reads


def test_a_restart_keeps_watching_what_is_pending(tmp_path):
    """From the ledger, even with the relay's own thread record gone."""
    root, slack, clock = _project(tmp_path), Slack(), Clock()
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    [item] = pending.waiting(root, "lead")
    listener._state_path.unlink()

    again = Listener(
        token="t",
        manager="lead",
        owner=OWNER,
        broadcast="#all-rite",
        project=root,
        clock=clock,
    )
    again.open(call=slack)
    assert any(r.ts == item.ts and r.item == item.name for r in again.roots)
    slack.replies[("D1", item.ts)] = [{"user": OWNER, "text": "v2", "ts": "500.0"}]
    _read_threads(again, slack, clock)
    assert _waiting(root) == []


# --- at the terminal ---------------------------------------------------------


def test_rite_replies_shown_to_a_person_confirms_it_as_seen(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    pending.sync(root, "lead")
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    CliRunner().invoke(cli, ["replies", "lead", "--peek"])
    assert _waiting(root) == ["which schema"], "--peek marks nothing"
    CliRunner().invoke(cli, ["replies", "lead", "--reader", "owner-boss"])
    assert _waiting(root) == ["which schema"], "a machine's reader is not a person"
    CliRunner().invoke(cli, ["replies", "lead"])
    [done] = pending.items(root, "lead")
    assert done.confirmed_how == pending.BY_TERMINAL


# --- it comes back at every check-in -----------------------------------------


def test_every_checkin_lists_what_is_still_waiting_until_it_is_confirmed(tmp_path):
    root = _project(tmp_path)
    pending.sync(root, "lead")
    path = send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    checkins._deliver_checkin(root, "lead")
    checkins._deliver_checkin(root, "lead")
    texts = [m for m in _outbox_texts(root) if m.startswith("Check-in")]
    assert len(texts) == 2
    assert all("Still waiting for you (1)" in t and "which schema" in t for t in texts)
    assert "not posted to Slack" in texts[-1]

    pending.confirm(root, "lead", path.name, pending.BY_TERMINAL, at=1.0)
    checkins._deliver_checkin(root, "lead")
    assert "Still waiting for you" not in _outbox_texts(root)[-1]


def _outbox_texts(root: Path) -> list[str]:
    from rite_ai.managers.mailbox import read

    return [m.text for m in read(root, "lead", OUTBOX)]


# --- the ledger under concurrent writers ---------------------------------------


def _confirm_one(args) -> None:
    root, name, barrier = Path(args[0]), args[1], args[2]
    barrier.wait()
    pending.sync(root, "lead")
    pending.confirm(root, "lead", name, pending.BY_TERMINAL, at=1.0)


def test_concurrent_confirmations_are_all_kept(tmp_path):
    """The relay, the check-in and `rite replies` write from different
    processes. Six confirmations released together, 10 rounds: none lost."""
    racers = 6
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager, ctx.Pool(racers) as pool:
        for n in range(10):
            root = _project(tmp_path / f"r{n}")
            pending.sync(root, "lead")
            names = [
                send(root, "lead", OUTBOX, f"q{k}", kind=QUESTION).name
                for k in range(racers)
            ]
            barrier = manager.Barrier(racers)
            pool.map(_confirm_one, [(str(root), x, barrier) for x in names])
            assert _waiting(root) == [], n
