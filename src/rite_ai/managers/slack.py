"""Reading a Slack conversation, and posting to one (A3b).

**`rite start` does the listening. There is no daemon and no hosting**, which
is what Robert asked for — the supervisor already wakes every
`POLL_SECONDS = 2.0` and already calls `mail_waiting` there, so an outbound
HTTPS call fits without new machinery.

**Transport decided and measured** (`docs/design/spikes/A3a-slack-transport.md`):
`conversations.history` with an `oldest` timestamp. It is the only transport
needing neither an inbound listener nor a held-open connection. Measured
2026-09-25: a posted message was readable **0.24 s** later.

⚠ **AUTHORITY COMES FROM THE CHANNEL (SPEC §9.16.2, D-95).** Instructions
are read ONLY from the Owner's DM with the app, which is one-to-one by
construction, and it is found from the Owner's user id rather than configured
as a channel — so it cannot be pointed at a conversation others can post in.
The broadcast channel is where status goes; nothing typed there instructs.

⚠ **Two scopes for a PUBLIC CHANNEL, and `channels:read` is NOT one of
them.** `channels:history` to read and `chat:write` to post. The claim is
scoped to that case deliberately, because the DM is a different one. All
measured 2026-09-25:

* **reading the Owner's DM needs `im:history`** — Slack names exactly that
  scope and nothing else;
* **posting a DM needs nothing extra** — `chat.postMessage` to a user id
  returns the `D…` channel;
* **reading a thread needs nothing extra** — `conversations.replies` works on
  `channels:history`;
* **`auth.test` needs no scope at all**, so rite can learn its own bot id, and
  therefore which `<@U…>` mention to look for, for free.

⚠ **BUT `conversations.history` DOES NOT RETURN THREAD REPLIES. MEASURED.** A
reply posted into a thread did not appear in the channel's history at all; the
thread ROOT carried `reply_count: 1` instead. So polling history alone makes
**every reply to a status update invisible**, and a threading relay has to
poll `conversations.replies` for each recent root as well — **one call per
active thread per tick**, not one call.

⚠ **AND A DISTINCTION THAT MUST NOT BLUR.** `@rite` decides whether a message
is ADDRESSED to rite. It does not decide WHO MAY COMMAND IT — anyone in the
workspace can type it. Authority comes from the channel (SPEC §9.16.2, D-95):
the Owner's DM, or the local machine. A mention is a noise filter and must
never be written up as an access control.

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
        return Heard(problem=f"cannot read the conversation: {refusal(got)}")

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


@dataclass(frozen=True)
class Posted:
    """What one post did. `channel` is the id Slack resolved the target to —
    a `D…` for a user id, a `C…` for a `#name` — which is how rite learns ids
    it has no scope to look up (A3a, measured)."""

    channel: str = ""
    ts: str = ""
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def _post(
    channel: str, token: str, text: str, *, thread: str = "", call=None
) -> Posted:
    """Post `text` to a channel, a channel name, or a user id. Never raises.

    ⚠ **`thread` is SHAPE until A4.** Robert's threading design has the User
    reply in the thread of a status update, so a thread root becomes a
    correlation id rite gets for free rather than inventing one. Measured
    2026-09-25 — posting into a thread needs no scope beyond `chat:write`,
    only a `thread_ts`.
    """
    if not channel or not token or not text.strip():
        return Posted()
    caller = call or _call
    payload = {"channel": channel, "text": text}
    if thread:
        payload["thread_ts"] = thread
    try:
        got = caller("chat.postMessage", token, None, payload)
    except Exception as e:  # noqa: BLE001
        return Posted(problem=f"{type(e).__name__}: {e}")
    if not got.get("ok"):
        return Posted(problem=refusal(got))
    return Posted(channel=str(got.get("channel") or ""), ts=str(got.get("ts") or ""))


_ADVICE = {
    "not_in_channel": "the app is not a member — `/invite @rite` in that channel",
    "channel_not_found": (
        "no such conversation, or a private one the app is not in — check the "
        "name, and that it is spelled as Slack writes it"
    ),
    "invalid_auth": "the bot token is not valid — `rite credential set slack`",
    "not_authed": "no bot token reached Slack — `rite credential set slack`",
    "account_inactive": "the app was uninstalled — reinstall, store the new token",
    "user_not_found": "slack.owner_user is not a member of this workspace",
}


def refusal(got: dict) -> str:
    """Slack's own error, NAMED, with what to do about it.

    ⚠ The name is kept verbatim (`missing_scope`, `not_in_channel`, …)
    because it is what a person searches Slack's documentation for; the
    advice after it is rite's reading of it. For `missing_scope`, Slack's own
    `needed` field names the scope, which is better than any guess here.
    """
    error = str(got.get("error") or "unknown_error")
    if error == "missing_scope":
        needed = got.get("needed") or "?"
        return (
            f"missing_scope — the app needs {needed}: add it under OAuth & "
            "Permissions → Bot Token Scopes, then reinstall the app"
        )
    advice = _ADVICE.get(error)
    return f"{error} — {advice}" if advice else error


@dataclass(frozen=True)
class Probe:
    """One target `rite doctor` checked."""

    target: str
    ok: bool
    detail: str


def probe(owner: str, broadcast: str, token: str, *, call=None) -> list[Probe]:
    """Can rite post to and read each target? For `rite doctor` (A6).

    ⚠ **This POSTS one line to each target.** Without `im:write` there is no
    way to learn the Owner's DM id except by posting to the user id (measured:
    `conversations.open` is `missing_scope`, `conversations.history` on a user
    id is `channel_not_found`), and without `channels:read` a `#name` is only
    resolved the same way. The line says what it is, so the Owner seeing it is
    itself part of the check.

    Reading is probed separately because it needs a different scope: posting
    to the DM works on `chat:write` alone, and reading it needs `im:history`.
    A probe that only posted would pass a relay that can never hear.
    """
    caller = call or _call
    out: list[Probe] = []
    for label, target in (
        ("command channel (the Owner's DM)", owner),
        ("broadcast channel", broadcast),
    ):
        if not target:
            continue
        where = f"{label} {target}"
        sent = _post(
            target,
            token,
            "rite doctor: checking that rite can reach this conversation.",
            call=caller,
        )
        if not sent.ok:
            out.append(Probe(where, False, f"cannot post — {sent.problem}"))
            continue
        try:
            got = caller(
                "conversations.history", token, {"channel": sent.channel, "limit": 1}
            )
        except Exception as e:  # noqa: BLE001
            out.append(Probe(where, False, f"cannot read — {type(e).__name__}: {e}"))
            continue
        if not got.get("ok"):
            out.append(Probe(where, False, f"cannot read — {refusal(got)}"))
            continue
        out.append(Probe(where, True, f"posts and reads ({sent.channel})"))
    return out


@dataclass
class Listener:
    """One Manager's Slack position, across the cycles of a run.

    Holds the cursor so the supervisor does not have to. `open` posts a start
    line to each target, which is also how the conversation ids are learned
    (see `probe`), and sets the cursor to that post — so a run starts hearing
    from the moment it said it was listening, rather than re-reading the last
    fifty messages of the DM as new instructions on every start.
    """

    token: str
    manager: str
    owner: str = ""
    broadcast: str = ""
    dm: str = ""
    """The Owner's DM id, learned by `open`. Empty means no command channel."""
    broadcast_id: str = ""
    since: str = ""
    problems: list[str] = field(default_factory=list)
    _unsaid: list[str] = field(default_factory=list)

    def news(self) -> list[str]:
        """Problems not yet said, for the supervisor to print — once each.

        ⚠ A poll that fails every 2 seconds for an hour is one line, not
        1,800: only a CHANGE of problem is news. Recorded problems used to go
        nowhere at all, so a relay refused on every poll looked, from the
        terminal, exactly like a quiet DM.
        """
        out, self._unsaid = self._unsaid, []
        return out

    def _problem(self, problem: str) -> None:
        if not self.problems or self.problems[-1] != problem:
            self._unsaid.append(f"slack: {problem}")
        self.problems.append(problem)

    def open(self, *, call=None) -> list[str]:
        """Say this Manager is listening, and learn where. Returns lines for
        the terminal; a failure is one of them, never a raise."""
        lines: list[str] = []
        if self.owner:
            sent = _post(
                self.owner,
                self.token,
                f"rite: Manager `{self.manager}` is running. What you send in "
                "this DM is an instruction to it, delivered at the start of "
                "its next turn.",
                call=call,
            )
            if sent.ok:
                self.dm, self.since = sent.channel, sent.ts
                lines.append(
                    f"slack: instructions come from the Owner's DM "
                    f"({self.owner}) only, delivered at the start of the next "
                    "turn."
                )
            else:
                lines.append(
                    f"slack: cannot reach the Owner's DM ({self.owner}): "
                    f"{sent.problem}. Nothing from Slack will instruct this "
                    "run; `rite doctor` checks each target."
                )
        else:
            lines.append(
                "slack: broadcast-only — no slack.owner_user is configured, so "
                "there is no command channel and nothing typed in Slack is an "
                "instruction."
            )
        if self.broadcast:
            sent = _post(
                self.broadcast,
                self.token,
                f"rite: Manager `{self.manager}` is running. Status is posted "
                "here. Nothing typed here instructs it — only the Owner's DM "
                "with rite does.",
                call=call,
            )
            if sent.ok:
                self.broadcast_id = sent.channel
                lines.append(f"slack: status is posted to {self.broadcast}.")
            else:
                lines.append(f"slack: cannot post to {self.broadcast}: {sent.problem}")
        return lines

    def poll(self, *, call=None) -> tuple[str, ...]:
        heard = _hear(self.dm, self.token, since=self.since, call=call)
        if not heard.ok:
            # ⚠ Recorded, not raised, and not retried here. The loop's next
            # tick is the retry; a Slack outage must not end a Manager's run.
            self._problem(heard.problem)
            return ()
        if heard.newest:
            self.since = heard.newest
        return heard.texts
