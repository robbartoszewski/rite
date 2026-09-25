"""Reading a Slack conversation, and posting to one (A3b).

**`rite start` does the listening. There is no daemon and no hosting**, which
is what Robert asked for — the supervisor already wakes every
`POLL_SECONDS = 2.0` and already calls `mail_waiting` there, so an outbound
HTTPS call fits without new machinery.

**Transport decided and measured** (`docs/design/spikes/A3a-slack-transport.md`):
`conversations.history` with an `oldest` timestamp. It is the only transport
needing neither an inbound listener nor a held-open connection. Measured
2026-09-25: a posted message was readable **0.24 s** later.

⚠ **READING AND POSTING ARE NOT THE SAME CONVERSATION, and that is an
authorisation property rather than tidiness.** Instructions are read ONLY from
`command_channel`. If the Manager also took instructions from wherever it
posts status, then everyone who can post in a team channel could direct it.
`broadcast_channel` is write-only from rite's side.

⚠ **Two scopes, and `channels:read` is NOT one of them.** `channels:history`
to read a public channel and `chat:write` to post. Reading a DM needs one
more, `im:history` — measured, Slack names it exactly. Posting a DM needs
nothing extra: `chat.postMessage` to a user id returns the `D…` channel.

⚠ **The token is read from the environment and never handled.** It goes in an
Authorization header and is never logged, never written, and never on an
argv — `SLACK_BOT_TOKEN` is deliberately absent from
`session.ALLOWED_ON_TMUX_ARGV`.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://slack.com/api/"
TIMEOUT_SECONDS = 15.0
"""Short. This runs inside the supervisor's 2-second wait loop, and a Slack
call that takes longer than a cycle boundary is one the loop should abandon
rather than wait behind."""

READ_LIMIT = 50
"""Per poll. Enough that a burst is not lost between two ticks, small enough
that a long-idle channel does not return its whole history on the first
read."""


@dataclass(frozen=True)
class Heard:
    """What one poll found. Never raises — a Slack outage must not end a run."""

    texts: tuple[str, ...] = ()
    newest: str = ""
    """The `ts` to use as the next poll's `oldest`, or "" when nothing was
    read. Carried forward rather than derived from the clock, because a clock
    comparison would re-read or skip around latency."""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def _call(
    method: str, token: str, params: dict | None = None, payload: dict | None = None
):
    if payload is not None:
        request = urllib.request.Request(
            API + method, data=json.dumps(payload).encode(), method="POST"
        )
        request.add_header("Content-Type", "application/json; charset=utf-8")
    else:
        request = urllib.request.Request(
            API + method + "?" + urllib.parse.urlencode(params or {})
        )
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def _hear(channel: str, token: str, *, since: str = "", call=None) -> Heard:
    """Messages posted to `channel` after `since`, oldest first.

    ⚠ Private: `Listener` is the public surface, because a caller that used
    this directly would have to carry the cursor itself and would eventually
    forget to. `test_no_dead_wiring` correctly flagged the public version —
    a public name whose only caller is its own module.

    ⚠ **Bot messages are skipped, and the reason is not cosmetic.** rite posts
    the Manager's replies to Slack; reading them back would feed a Manager its
    own words as a new instruction and loop. A message with `bot_id` is
    therefore not an instruction, whoever's bot it is.

    ⚠ **AND NEITHER IS ANYTHING WITH A `subtype`. MEASURED, NOT REASONED.**
    The first live run delivered two messages to a Manager, and both were
    `"<@U…> has joined the channel"` — `channel_join` events, authored by a
    real `user`, which the bot filter had no reason to drop. A Manager was
    being instructed by somebody joining a channel.

    A plain human message has **no `subtype` at all**, so the rule is an
    allowlist of one: no subtype, or it is not an instruction. That covers
    joins, leaves, topic changes, pins, file shares and every future subtype
    Slack adds — a list of subtypes to REFUSE would have missed the next one,
    which is the same argument `ALLOWED_ON_TMUX_ARGV` makes.
    """
    if not channel or not token:
        return Heard()
    caller = call or _call
    params = {"channel": channel, "limit": READ_LIMIT}
    if since:
        params["oldest"] = since
    try:
        got = caller("conversations.history", token, params)
    except Exception as e:  # noqa: BLE001 - an outage is a result, not a crash
        return Heard(problem=f"{type(e).__name__}: {e}")
    if not got.get("ok"):
        return Heard(problem=f"slack refused the read: {got.get('error')}")

    # Slack returns newest first; a Manager should be told things in the order
    # they were said.
    messages = list(reversed(got.get("messages") or []))
    texts, newest = [], since
    for message in messages:
        stamp = str(message.get("ts") or "")
        if stamp:
            newest = max(newest, stamp) if newest else stamp
        if message.get("bot_id") or message.get("subtype"):
            continue
        text = (message.get("text") or "").strip()
        if text:
            texts.append(text)
    return Heard(texts=tuple(texts), newest=newest)


def say(channel: str, token: str, text: str, *, call=None) -> str:
    """Post `text`. Returns "" on success or a problem to report.

    Write-only from rite's side: nothing is read back from here, so this may
    be a channel a whole team can post in without any of them being able to
    direct the Manager.
    """
    if not channel or not token or not text.strip():
        return ""
    caller = call or _call
    try:
        got = caller(
            "chat.postMessage", token, None, {"channel": channel, "text": text}
        )
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return "" if got.get("ok") else f"slack refused the post: {got.get('error')}"


@dataclass
class Listener:
    """One Manager's Slack position, across the cycles of a run.

    Holds the cursor so the supervisor does not have to. `since` starts empty,
    which means the FIRST poll reads recent history — deliberately, so a
    message sent while `rite start` was booting is not lost.
    """

    command_channel: str
    token: str
    since: str = ""
    problems: list[str] = field(default_factory=list)

    def poll(self, *, call=None) -> tuple[str, ...]:
        heard = _hear(self.command_channel, self.token, since=self.since, call=call)
        if not heard.ok:
            # ⚠ Recorded, not raised, and not retried here. The loop's next
            # tick is the retry; a Slack outage must not end a Manager's run.
            self.problems.append(heard.problem)
            return ()
        if heard.newest:
            self.since = heard.newest
        return heard.texts
