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
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://slack.com/api/"
TIMEOUT_SECONDS = 15.0
"""Short. This runs inside the supervisor's 2-second wait loop, and a Slack
call that takes longer than a cycle boundary is one the loop should abandon
rather than wait behind."""

HISTORY_PAGES = 10
"""At most this many pages per read — 500 messages. Past that, the read is
reported as incomplete rather than looping inside a 2-second tick."""

READ_LIMIT = 50
"""Per poll. Enough that a burst is not lost between two ticks, small enough
that a long-idle channel does not return its whole history on the first
read."""

THREAD_SECONDS = 30.0
"""How often each open thread is re-read. ⚠ **A budget, not a latency
choice.** `conversations.history` does not return thread replies (measured,
A3a), so every thread costs one `conversations.replies` call per read. Tier 3
is 50+/min per method; at most one replies call is made per tick, and a
thread read every 30 s is 2/min each, so ten threads stay well inside it."""

THREADS_MAX = 10
"""The most recent roots whose threads are read; older ones are dropped."""

POSTED_KEPT = 200
"""How many posts the relay remembers. Only the recent ones are ever thread
roots (`THREADS_MAX`), and the map is for finding a post again, not an
archive."""

READER = "slack"
"""This relay's name as an outbox reader. `rite connect` has its own, so each
sees every reply (A1, Decision 1)."""

THREAD_HOURS = 24.0
"""A root older than this is no longer read. A check-in a day makes three
roots a day (V060_CHECKINS), so without a horizon the set grows without
limit."""

ANSWER_WINDOW_SECONDS = 30 * 60.0
"""How long after being given the Owner's instruction a Manager's reply is
posted under it rather than into the day's notes (SCRUM-56).

**Bounded, and generous inside the bound, because the two mistakes do not
cost the same.** A reply misfiled as notes is an answer the Owner asked for
and never sees — the defect. A status line misfiled under their question is
noise in a thread they already opened, and the DM's top-level scan list is
untouched either way. So the window is wide enough to cover several of a
Manager's cycles rather than tuned to one.

⚠ **It is not cleared by the first reply**, deliberately: a Manager that
answers in two `rite reply` calls would otherwise have its second half filed
as notes, which is half an answer — the same failure in a smaller place. A
newer Owner instruction supersedes it, so the window never outlives the
question it belongs to."""

HOLD_MAX_SECONDS = 24 * 3600.0
"""The longest a message for READING is held for the next check-in (RP1 piece
3). Past it, it goes under the day's notes root instead: a project whose
Manager rarely runs in a check-in window must not hold its reading forever."""

PENDING_SLOW_SECONDS = 300.0
"""How often the thread under a PENDING item (RP1 piece 2) is read once it is
older than `THREAD_HOURS`. ⚠ **A pending item is exempt from the horizon and
from `THREADS_MAX`**: the Owner may answer a question days later, and a
thread nobody reads is an answer nobody sees. It is read more slowly once
old, so a pile of them cannot starve the fresh threads of the one read a
tick. They are bounded by what is pending, which the check-in lists."""


@dataclass(frozen=True)
class Heard:
    """What one poll found. Never raises — a Slack outage must not end a run."""

    messages: tuple[dict, ...] = ()
    """What a person said, oldest first — bot posts and events removed."""
    newest: str = ""
    """The `ts` to use as the next poll's `oldest`, or "" when nothing was
    read. Carried forward rather than derived from the clock, because a clock
    comparison would re-read or skip around latency."""
    problem: str = ""
    more: str = ""
    """Slack's cursor for the next page, when there is one."""
    skipped: bool = False
    """True when a gap was longer than `HISTORY_PAGES` could read."""

    @property
    def ok(self) -> bool:
        return not self.problem

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple((m.get("text") or "").strip() for m in self.messages)


SCOPES_HEADER = "x-oauth-scopes"


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
        got = json.loads(response.read())
        # The token's scopes travel in a response HEADER, not the body (RS3).
        # Kept under the header's own name, which no Slack body uses, so the
        # one reader (`_scopes_of`) goes through the same `call` as every
        # other request, and a fake Slack, which sends none, reads as "Slack
        # did not say", never as a pass.
        scopes = response.headers.get(SCOPES_HEADER)
        if isinstance(got, dict) and scopes is not None:
            got[SCOPES_HEADER] = scopes
        return got


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
    params = {"channel": channel, "limit": READ_LIMIT}
    if since:
        params["oldest"] = since
    # ⚠ PAGED, because a gap is where a burst lives (A5). History comes back
    # newest first, `READ_LIMIT` at a time, so a Manager that was stopped
    # while sixty messages arrived would otherwise hear the newest fifty and
    # have its cursor moved past the other ten — lost, silently.
    messages: list[dict] = []
    newest = since
    for _ in range(HISTORY_PAGES):
        heard = _read("conversations.history", params, token, call=call)
        if not heard.ok:
            return heard
        messages.extend(heard.messages)
        if _as_ts(heard.newest) > _as_ts(newest):
            newest = heard.newest
        cursor = heard.more
        if not cursor:
            return Heard(messages=tuple(sorted(messages, key=_ts_of)), newest=newest)
        params = {**params, "cursor": cursor}
    return Heard(
        messages=tuple(sorted(messages, key=_ts_of)),
        newest=newest,
        problem="",
        skipped=True,
    )


PERSON_SUBTYPES_IN_A_THREAD = frozenset({"thread_broadcast"})
"""A reply that was also sent to the channel. It is a person's message, and
it is skipped in the channel's history (it carries a subtype there too), so
the thread read is the one place it can be heard."""


def _read(
    method: str,
    params: dict,
    token: str,
    *,
    call=None,
    allowed: frozenset[str] = frozenset(),
) -> Heard:
    caller = call or _call
    try:
        got = caller(method, token, params)
    except Exception as e:  # noqa: BLE001 - an outage is a result, not a crash
        return Heard(problem=f"{type(e).__name__}: {e}")
    if not got.get("ok"):
        return Heard(problem=f"cannot read the conversation: {refusal(got)}")

    # Slack returns history newest first and replies oldest first; a Manager
    # should be told things in the order they were said, whichever it was.
    messages = sorted(got.get("messages") or [], key=_ts_of)
    kept, newest = [], str(params.get("oldest") or "")
    for message in messages:
        stamp = str(message.get("ts") or "")
        if stamp and _as_ts(stamp) > _as_ts(newest):
            newest = stamp
        subtype = message.get("subtype")
        if message.get("bot_id") or (subtype and subtype not in allowed):
            continue
        if (message.get("text") or "").strip():
            kept.append(message)
    more = ""
    if got.get("has_more"):
        more = str((got.get("response_metadata") or {}).get("next_cursor") or "")
    return Heard(messages=tuple(kept), newest=newest, more=more)


def _ts_of(message: dict) -> float:
    return _as_ts(message.get("ts"))


def _as_ts(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


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


# --- how a post is shown (S29) ---------------------------------------------
#
# Live run (Robert): consecutive posts of different kinds stacked under one bot
# avatar and timestamp, so a question that needed his answer read as one more
# status paragraph. Each post now opens with a compact type tag, carries its
# author, and ends with a divider, so two posts never run together, even when
# Slack groups them under one sender. What needs the person is shown loud (a
# section); everything else muted (a context block, Slack's small grey text).
#
# `text` stays exactly what it was: it is what a notification and a client
# without blocks show. The blocks are what a person reading the conversation
# sees.

NEEDS_ANSWER = "needs-answer"
NEEDS_YOU = "needs-you"
STATUS = "status"
SYSTEM = "system"
DELIVERY = "delivery"
ANSWER = "answer"
"""A Manager's reply to something the Owner asked (SCRUM-56).

⚠ **Its own tag, because "Status" was the wrong word for it.** The point of
routing an answer into the Owner's DM is that it is not ambient status; a
post that lands in the right thread and then labels itself `ℹ️ Status`
unsays that. Muted rather than LOUD all the same: an answer is the end of an
exchange the Owner started, so it needs reading, not acting on — and putting
it in the loud set would also put it in the pile `pending` tracks as
awaiting them, which is the opposite of what it is."""
TAGS = {
    NEEDS_ANSWER: "❓ *Needs your answer*",
    NEEDS_YOU: "❗ *Needs you*",
    STATUS: "ℹ️ *Status*",
    SYSTEM: "⚙️ *rite*",
    DELIVERY: "⚠️ *Delivery*",
    ANSWER: "↩️ *Answer*",
}
LOUD = frozenset({NEEDS_ANSWER, NEEDS_YOU})
"""The kinds shown as sections. Exactly the ones `pending` tracks as needing
the person (`pending.kind_of`): a post never looks more, or less, urgent than
what rite waits on."""

SECTION_CHARS = 2900
"""Under Slack's 3000 for a section's or a context element's text."""
BLOCKS_MAX = 50
"""Slack's limit per message."""

_QUESTION_FIRST_LINE = re.compile(r"^(.+?) · q[0-9a-f]{4} · ")


def _ticket_in(text: str) -> str:
    """The ticket a question names on its first line, the line
    `asking.raise_to_person` writes (`<subject> · q1a2b · … · reply in this
    thread`), or ""."""
    found = _QUESTION_FIRST_LINE.match(text or "")
    return found.group(1).strip() if found else ""


def _chunks(text: str, size: int = SECTION_CHARS) -> list[str]:
    """`text` in pieces Slack will take, split at line ends where it can."""
    pieces: list[str] = []
    rest = text.strip() or " "
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        cut = cut if cut > 0 else size
        pieces.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    pieces.append(rest)
    return pieces


def _present(kind: str, body: str, *, author: str = "", ticket: str = "") -> list:
    """The blocks one post is shown as: its tag line (with the ticket and the
    author), its body, then a divider. Loud for what needs the person, muted
    for the rest. Never more than Slack takes: a body too long for the blocks
    says where the rest is (the fallback `text` carries it all)."""
    if kind not in TAGS:
        raise ValueError(f"a post is one of {sorted(TAGS)}, not {kind!r}")
    tag = " · ".join(
        part for part in (TAGS[kind], f"`{ticket}`" if ticket else "", author) if part
    )
    loud = kind in LOUD

    def block(text: str) -> dict:
        if loud:
            return {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

    parts = _chunks(body)
    room = BLOCKS_MAX - 2  # the tag line, and the divider
    if len(parts) > room:
        parts = parts[: room - 1] + [
            "_…the rest is too long to show here; `rite replies` has all of it._"
        ]
    return [block(tag), *(block(p) for p in parts), {"type": "divider"}]


def _post(
    channel: str,
    token: str,
    text: str,
    *,
    thread: str = "",
    call=None,
    kind: str = "",
    body: str | None = None,
    author: str = "",
    ticket: str = "",
) -> Posted:
    """Post `text` to a channel, a channel name, or a user id. Never raises.

    With `kind` (S29), the post is also sent as blocks (`_present`): `body`
    (default `text`) under its tag line, with a divider after it. `text` is
    then the notification's fallback.

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
    if kind:
        payload["blocks"] = _present(
            kind, text if body is None else body, author=author, ticket=ticket
        )
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
    """One target `rite doctor` checked.

    `ok` is True (verified), False (a problem, counted), or None (not checked:
    said as that, never as a pass, and not counted — S28's rule that "could
    not check" is a gap in the report, not a fault in the project)."""

    target: str
    ok: bool | None
    detail: str


REQUIRED_SCOPES = ("chat:write", "channels:history")
DM_SCOPES = ("im:history",)


def _scopes_of(token: str, *, call) -> frozenset[str] | None:
    """The bot token's scopes, as Slack reports them in the `x-oauth-scopes`
    header of a Web API response (`_call` keeps it), or None when it did not
    say.

    ⚠ The header is documented by Slack; it is NOT measured here against a
    live workspace. A missing header is None, which `probe` reports as "not
    checked", never as "the scopes are fine"."""
    try:
        got = call("auth.test", token, {})
    except Exception:  # noqa: BLE001 - no answer is not a scope list
        return None
    header = got.get(SCOPES_HEADER) if isinstance(got, dict) else None
    if not isinstance(header, str):
        return None
    return frozenset(s.strip() for s in header.split(",") if s.strip())


def remembered_targets(project: Path, managers: list[str]) -> dict:
    """The conversation ids the Owner's relay learned at its first start (RS1),
    from the first of `managers` whose relay state has any. {} when none does.

    ⚠ **READ ONLY.** A running `rite start` owns that file and rewrites it
    whole; `rite doctor` writing it would race it."""
    from rite_ai.managers import manager_dir

    for manager in managers:
        if not manager:
            continue
        try:
            path = manager_dir(project, manager) / "slack.json"
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        known = state.get("known") if isinstance(state, dict) else None
        if isinstance(known, dict) and known:
            return known
    return {}


def probe(
    owner: str,
    broadcast: str,
    token: str,
    *,
    status: str = "",
    known: dict | None = None,
    call=None,
) -> list[Probe]:
    """Can rite reach each target? For `rite doctor` (A6). **It posts nothing,
    anywhere.**

    It used to post "rite doctor: checking that rite can reach this
    conversation." to the Owner's DM and the broadcast channel on every run, a
    test artefact in a production conversation (RS3; Robert, 2026-09-29).
    Instead:

    * the app's **scopes**, from the header Slack sends with `auth.test`.
      Posting needs only `chat:write`, so the scopes settle it without a post;
    * the **DM and the broadcast channel**, by READING their history under the
      ids the Owner's relay remembered (`known`, see `remembered_targets`), or,
      for the DM, `conversations.open` where the app has `im:write` (opening
      shows nothing to anyone). Reading proves the id and the read scope,
      which is the half a post cannot: posting to the DM works on
      `chat:write` alone, so a probe that only posted would pass a relay that
      can never hear the Owner;
    * the **status channel** is output only and has no id to read until a
      post resolves its name, so it is "not checked" here; `rite doctor
      --network`, where the person asked for a message that arrives, posts
      its one line there.

    A target with no id yet is "not checked yet": the first `rite start`
    learns it."""
    caller = call or _call
    known = known or {}
    out: list[Probe] = []
    have = _scopes_of(token, call=caller)
    needed = REQUIRED_SCOPES + (DM_SCOPES if owner else ())
    if have is None:
        out.append(
            Probe(
                "app scopes",
                None,
                "Slack did not report the app's scopes",
            )
        )
    else:
        missing = [s for s in needed if s not in have]
        out.append(
            Probe(
                "app scopes",
                not missing,
                f"missing {', '.join(missing)} — add under OAuth & Permissions → "
                "Bot Token Scopes, then reinstall the app"
                if missing
                else ", ".join(needed) + " present",
            )
        )

    def read(label: str, channel: str) -> Probe:
        try:
            got = caller(
                "conversations.history", token, {"channel": channel, "limit": 1}
            )
        except Exception as e:  # noqa: BLE001
            return Probe(label, False, f"cannot read — {type(e).__name__}: {e}")
        if not got.get("ok"):
            return Probe(label, False, f"cannot read — {refusal(got)}")
        return Probe(label, True, f"reads ({channel})")

    if owner:
        label = f"command channel (the Owner's DM) {owner}"
        dm = known.get("dm") or {}
        channel = str(dm.get("channel") or "") if dm.get("user") == owner else ""
        if not channel:
            try:
                got = caller("conversations.open", token, {"users": owner})
            except Exception:  # noqa: BLE001 - no answer is not an id
                got = {}
            opened = got.get("channel") if got.get("ok") else None
            channel = str(opened.get("id") or "") if isinstance(opened, dict) else ""
        out.append(
            read(label, channel)
            if channel
            else Probe(
                label,
                None,
                "the DM's id is learned at the first `rite start` (or "
                "add `im:write` and doctor opens it)",
            )
        )
    if broadcast:
        label = f"broadcast channel {broadcast}"
        b = known.get("broadcast") or {}
        channel = str(b.get("channel") or "") if b.get("name") == broadcast else ""
        out.append(
            read(label, channel)
            if channel
            else Probe(
                label,
                None,
                "its id is learned at the first `rite start`; "
                f"`/invite @rite` into {broadcast} before then",
            )
        )
    if status:
        out.append(
            Probe(
                f"status channel {status}",
                None,
                "output only, so not without a post; `rite doctor --network` "
                "posts one line there",
            )
        )
    return out


@dataclass
class Root:
    """A message rite posted, whose thread is read for replies (A3).

    ⚠ Kept by the relay, never written into a mailbox message, which stays
    identity-free (Decision 1a)."""

    channel: str
    ts: str
    label: str
    """How a reply's header names what it answers — "rite's start line at
    14:02". A person replying under a message is talking about THAT message,
    and a Manager told only "a reply" cannot know which."""
    last: str = ""
    due: float = 0.0
    item: str = ""
    """The outbox name of a PENDING item this root carries (RP1 piece 2), or
    "". A reply here from the Owner, or their reaction, confirms it."""


class _Relayed(str):
    """A relayed message's text, carrying when it was SENT in Slack.

    A `str`, so everything that handles mailbox text handles it unchanged;
    `sent_at` lets the supervisor file it by send time (`mailbox.send`), so
    messages from two conversations reach the Manager in the order they were
    said rather than the order rite polled them.
    """

    sent_at: float | None = None
    source: tuple[str, str] | None = None
    """(channel, ts) of the Slack message, when it was addressed to the
    Manager and so is acknowledged with a reaction (SCRUM-35)."""


_QUESTION_IN_LABEL = re.compile(r"\bq[0-9a-f]{4}\b")

REFINEMENT_CHANNEL = "refinement channel"
"""The header's first part for a message relayed from the refinement
channel (TR2, TRQ8). `delivered.classify` counts it as the User's words only
when rite marked it INSTRUCTION: the Owner, in a refinement thread."""


def _carried_question(text: str) -> dict:
    """`{"qid": …}` when `text` is a question rite raised (its first line
    carries the id, `asking._first_line`), else nothing. Kept with the post so
    a later message can link back to it (SCRUM-47)."""
    from rite_ai.managers.asking import own_question_id

    qid = own_question_id(text)
    return {"qid": qid} if qid else {}


def _round_of(root: Root, open_rounds: dict[str, float]) -> str:
    """The open refinement round `root` carries, by the question id in its
    label (`asking`'s first line), or ""."""
    found = _QUESTION_IN_LABEL.search(root.label)
    return found.group(0) if found and found.group(0) in open_rounds else ""


def _pending_label(item) -> str:
    return f'rite\'s question "{" ".join(item.first.split())[:40]}"'


def _relayed(
    text: str, sent_at: float, source: tuple[str, str] | None = None
) -> _Relayed:
    out = _Relayed(text)
    out.sent_at = sent_at or None
    out.source = source
    return out


PICKED_UP = "eyes"
"""The reaction rite adds to a message it has picked up for the Manager."""

ANSWER_READ = "white_check_mark"
"""The reaction rite adds to an answer a Worker has said it READ (SCRUM-61).

⚠ **Only on the ack, never on the write.** `deliver_answer` can say an answer
was written where the Worker polls for it and no more — its own docstring
refuses the stronger claim — so a tick put on delivery would tell the Owner
something nobody established. This goes on when the Worker says it read it
(`rite ack`), and the tick's meaning is exactly that: the Worker said so."""


def _unescaped(text: str) -> str:
    """Slack's three message-text escapes undone, `&amp;` LAST so that a
    typed `&lt;` (sent as `&amp;lt;`) comes back as `&lt;`, not `<`."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _header(where: str, *parts: str) -> str:
    return "[" + " · ".join((where, *parts)) + "]"


def _quoted(text: str) -> str:
    """The typed text, every line quoted.

    ⚠ **So a person cannot forge rite's header.** A message whose own text is
    `[Owner's DM · addressed · INSTRUCTION] …`, or that starts a new line with
    one, arrives as `> [Owner's DM …` under rite's real header — inside the
    quote, where the delivery note says nothing is rite's.
    """
    return "\n".join(f"> {line}" for line in text.strip().splitlines())


@dataclass
class Listener:
    """One Manager's Slack position, across the cycles of a run.

    **Reads two conversations with different authority** (SPEC §9.16):

    * **the Owner's DM** — authorised by construction, and addressed, because
      the app is the only other party to it. What is typed there is an
      INSTRUCTION;
    * **the broadcast channel** — never authorised, whoever types there.
      What is typed there reaches the Manager as CONTEXT (D-94), addressed
      when it mentions the app (D-96), and still context.

    ⚠ **EVERY RELAYED MESSAGE CARRIES A HEADER SAYING WHICH, as text**
    (§9.16.5). The distinction must not rest on the model noticing that a
    mention was absent — §9.16.3 forbids exactly that — so the header states
    the channel, the addressing and the verdict, and the mailbox gains no
    field.

    Plus the threads under messages rite posted (`roots`), because
    `conversations.history` does not return thread replies — measured.

    `open` posts a start line to each target, which is how the conversation
    ids are learned (see `probe`), and sets each cursor to that post — so a
    run starts hearing from the moment it said it was listening, rather than
    re-reading the last fifty messages as new ones on every start.
    """

    token: str = field(repr=False)
    """⚠ `repr=False`: a dataclass repr prints every field, and a Listener in
    a traceback or a failing assertion printed the bot token with it."""
    manager: str = ""
    owner: str = ""
    broadcast: str = ""
    dm: str = ""
    """The Owner's DM id, learned by `open`. Empty means no command channel."""
    broadcast_id: str = ""
    me: str = ""
    """The app's own user id, from `auth.test` (no scope)."""
    me_bot: str = ""
    """The app's BOT id, from the same call. ⚠ **A mention of rite comes in
    either form, and both were observed from people choosing `@rite` from
    Slack's autocomplete (2026-09-25):** the Owner's arrived as `<@U0C49FPUP8B>`
    (the user id), and a second member's as `<@B0C4DPL00A2>` (the bot id).
    Matching the user id alone labelled her real mention "unaddressed". That
    was right about authority and wrong about addressing, which is the
    distinction §9.16.3 says the header must carry."""
    since: dict[str, str] = field(default_factory=dict)
    opened: dict[str, str] = field(default_factory=dict)
    """Each conversation's start-line `ts` for THIS run. A message older than
    it was sent while no Manager was listening (A5)."""
    roots: list[Root] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    clock: object = time.time
    project: Path | None = None
    """The project root, for the outbox and the relay's own state. None means
    this listener only listens — tests, and nothing else."""
    status: str = ""
    """The status channel (`slack.status`): where "started" and "stopped" are
    said. "" means nowhere in Slack; those lines stay on the terminal. Never
    the DM (Robert, 2026-09-29: "It's a spam anywhere else")."""
    project_name: str = ""
    """How a status line names its project, since one status channel may be
    shared by several projects' apps (D-114)."""
    _known: dict = field(default_factory=dict)
    """Conversation ids learned by an earlier run, so a start need not post to
    learn them: {"dm": {"user", "channel"}, "broadcast": {"name", "channel"}}.
    Each is used only while what it was learned for is unchanged, so a changed
    owner or broadcast name is relearned."""
    _saved_since: dict[str, str] = field(default_factory=dict)
    _started: bool = False
    """Has this relay ever posted for this Manager? An explicit flag rather
    than "the state file exists", because `open` writes that file before
    anything is posted — and that inference posted a month of old replies."""
    _turn: int = 0
    _unsaid: list[str] = field(default_factory=list)
    _notes: dict = field(default_factory=dict)
    """Today's notes root (RP1 piece 3): {"day", "channel", "ts"}."""
    _answered: dict = field(default_factory=dict)
    """question id -> {"channel", "ts"} of the Owner's reply that answered it
    (SCRUM-61), so a ✅ can go on THEIR message when the Worker says it read
    the answer.

    ⚠ **Kept here, not in the message.** The mailbox is identity-free by
    Decision 1a — `posted` exists for the same reason — and the relay that
    carries an answer into a sandbox reads the mailbox, where the Slack `ts`
    is long gone. This is recorded at the one moment both facts are in hand:
    `_confirm_if_answered`, which already holds the Owner's reply and the
    question it answered."""
    _ticked: dict = field(default_factory=dict)
    """question id -> when a ✅ was put on its answer, so one ack ticks once.
    A reaction Slack already has is not an error, but asking it every tick
    for the life of a project is a request per tick for nothing."""
    _awaiting: dict = field(default_factory=dict)
    """The Owner's DM message this Manager has been given and not yet replied
    to: {"channel", "ts", "at"} (SCRUM-56). `at` is when rite relayed it.

    ⚠ **This is a CORRELATION rite can make mechanically, and it is not the
    same claim as "this reply answers that question".** rite does not read
    the reply and does not ask the model what it is answering — the rule this
    file and `mailbox._needs_action` both hold is that a message is classed
    by the command that wrote it, never by its text. What rite knows is that
    the Owner addressed this Manager, and that the Manager then spoke inside
    the window below. That is the claim, and `ANSWER_WINDOW_SECONDS` says why
    it is a bounded one."""
    _reactions: str = ""
    """"" until tried; "read" when `reactions.get` works; "missing" once Slack
    said the app lacks `reactions:read`, which is said once and then only a
    thread reply confirms."""
    _reacting: str = ""
    """"" until tried, "missing" once Slack said the app lacks
    `reactions:write`: said once, and no 👀 is tried again (SCRUM-35)."""
    _links: dict = field(default_factory=dict)
    """(channel, ts) -> the permalink Slack gave for it (SCRUM-47)."""
    _linking: str = ""
    """"missing" once `chat.getPermalink` was refused for want of a scope."""
    refinement_channel: str = ""
    """The private channel refinement rounds go to (TR2, TRQ8:
    `refinement.questions_to: channel`), or "" for the Owner's DM."""
    refinement_id: str = ""
    """Its id, learned by `open` once rite has both posted there and read
    there. Empty means rounds go to the DM, and `open` said why."""

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

    @property
    def _where(self) -> str:
        if self.broadcast.startswith("#"):
            return self.broadcast
        return f"broadcast channel {self.broadcast}"

    def remember(self, channel: str, ts: str, label: str) -> None:
        """Read the thread under a message rite posted. Newest kept; the
        oldest beyond `THREADS_MAX` are dropped.

        ⚠ **One `Root` per `(channel, ts)`, the guard `watch` already had.**
        This appended unconditionally, which was harmless while every caller
        posted a fresh message — and stopped being harmless when the Owner's
        own message became a root an ANSWER is remembered under, because a
        two-part answer remembers the same ts twice. Measured by the
        terminating check: two roots for one message, the Owner's follow-up
        relayed into the Manager's inbox TWICE as an INSTRUCTION, and on
        restart the duplicate's `last` (reset to the message ts) winning the
        `channel:ts` key in `_save`, rewinding the cursor so every earlier
        reply in that thread is re-delivered as a fresh instruction."""
        if not channel or not ts:
            return
        if any(r.channel == channel and r.ts == ts for r in self.roots):
            return
        self.roots.append(Root(channel, ts, label, last=ts))
        self._bound_roots()

    def _bound_roots(self) -> None:
        """`THREADS_MAX` of the ordinary roots, plus every pending one and
        every one carrying an open refinement round."""
        pinned = self._open_rounds()
        ordinary = [r for r in self.roots if not r.item and not _round_of(r, pinned)]
        drop = {id(r) for r in ordinary[:-THREADS_MAX]}
        self.roots = [r for r in self.roots if id(r) not in drop]

    def watch(self, channel: str, ts: str, label: str, item: str) -> None:
        """Read the thread under a PENDING item until it is confirmed."""
        if not channel or not ts or item in {r.item for r in self.roots}:
            return
        for r in self.roots:
            if (r.channel, r.ts) == (channel, ts):
                r.item = item
                return
        self.roots.append(Root(channel, ts, label, last=ts, item=item))

    def open(self, *, call=None) -> list[str]:
        """Say this Manager is listening, and learn where. Returns lines for
        the terminal; a failure is one of them, never a raise."""
        caller = call or _call
        lines: list[str] = []
        self._restore()
        try:
            who = caller("auth.test", self.token, {})
            self.me = str(who.get("user_id") or "")
            self.me_bot = str(who.get("bot_id") or "")
        except Exception:  # noqa: BLE001 - without it, mentions read as unaddressed
            self.me = self.me_bot = ""
        if self.owner:
            self.dm = self._dm_without_posting(call=caller)
            if self.dm:
                # Known, or opened with `im:write`: a start is not news for the
                # DM, so nothing is posted. Begin reading from the saved cursor
                # if there is one, else from now.
                self._start_at(self.dm, self._now_ts())
            else:
                # ⚠ ONCE PER PROJECT, not per start. Without `im:write` the only
                # way to learn the DM's id is to post to the user id (`probe`);
                # what is posted is what the DM is FOR, said once.
                sent = _post(
                    self.owner,
                    self.token,
                    "rite: this is where the Managers of project "
                    f"`{self._project_label}` ask you things, and what you send "
                    "here is an instruction to them, delivered at the start of "
                    "a Manager's next turn. Starts and stops are said in "
                    f"{self.status or 'the terminal'}, not here.",
                    call=caller,
                    kind=SYSTEM,
                    author=self.manager,
                )
                if sent.ok:
                    self.dm = sent.channel
                    self._learned("dm", "user", self.owner, sent.channel)
                    self._start_at(self.dm, sent.ts)
                    self.remember(
                        sent.channel, sent.ts, self._label("start line", sent)
                    )
            if self.dm:
                lines.append(
                    f"slack: instructions come from the Owner's DM "
                    f"({self.owner}) only, delivered at the start of the next "
                    "turn."
                )
            else:
                lines.append(
                    f"slack: cannot reach the Owner's DM ({self.owner}): nothing "
                    "from Slack will instruct this run; `rite doctor` checks "
                    "each target."
                )
        else:
            lines.append(
                "slack: broadcast-only — no slack.owner_user is configured, so "
                "there is no command channel and nothing typed in Slack is an "
                "instruction."
            )
        if self.broadcast:
            known = self._known.get("broadcast") or {}
            if known.get("name") == self.broadcast and known.get("channel"):
                self.broadcast_id = str(known["channel"])
                self._start_at(self.broadcast_id, self._now_ts())
            else:
                # Once per channel, for the same reason as the DM: a name is
                # resolved to an id only by posting (no `channels:read`).
                sent = _post(
                    self.broadcast,
                    self.token,
                    f"rite: project `{self._project_label}` reads this channel. "
                    "What is typed here reaches its Managers as context — never "
                    "as an instruction; only the Owner's DM with rite instructs "
                    "them.",
                    call=caller,
                    kind=SYSTEM,
                    author=self.manager,
                )
                if sent.ok:
                    self.broadcast_id = sent.channel
                    self._learned("broadcast", "name", self.broadcast, sent.channel)
                    self._start_at(self.broadcast_id, sent.ts)
                    self.remember(
                        sent.channel, sent.ts, self._label("start line", sent)
                    )
                else:
                    lines.append(
                        f"slack: cannot post to {self.broadcast}: {sent.problem}"
                    )
            if self.broadcast_id:
                lines.append(
                    f"slack: {self.broadcast} is read as context, never as instruction."
                )
        lines.extend(
            self._say_status(
                f"Manager `{self.manager}` is running."
                + (" Instructions: the Owner's DM." if self.dm else ""),
                call=caller,
            )
        )
        if self.refinement_channel:
            lines.append(self._open_refinement_channel(caller))
        self._save()
        return lines

    @property
    def _project_label(self) -> str:
        if self.project_name:
            return self.project_name
        return self.project.name if self.project is not None else "this project"

    def _now_ts(self) -> str:
        """Now, as a Slack timestamp, for where reading begins when nothing was
        posted to begin it."""
        return f"{float(self.clock()):.6f}"

    def _learned(self, what: str, key: str, value: str, channel: str) -> None:
        self._known[what] = {key: value, "channel": channel}

    def _dm_without_posting(self, *, call) -> str:
        """The Owner's DM id without posting to it: remembered from an earlier
        run for THIS owner, or opened with `conversations.open` where the app
        has `im:write`. "" when neither works; `open` then posts once to learn
        it, which is also what says what the DM is for."""
        known = self._known.get("dm") or {}
        if known.get("user") == self.owner and known.get("channel"):
            return str(known["channel"])
        try:
            got = call("conversations.open", self.token, {"users": self.owner})
        except Exception:  # noqa: BLE001 - no answer is not an id
            return ""
        # Slack answers `{"channel": {"id": "D…"}}`. Anything else is not an id,
        # whatever `ok` says.
        opened = got.get("channel") if got.get("ok") else None
        channel = opened.get("id") if isinstance(opened, dict) else ""
        if channel:
            self._learned("dm", "user", self.owner, str(channel))
        return str(channel or "")

    def _say_status(self, text: str, *, call=None) -> list[str]:
        """Say a lifecycle line in the status channel, naming the project.

        ⚠ **Never the DM, never the broadcast channel.** The DM is for what
        needs the person, and the broadcast channel is read as context. A
        status channel that cannot be posted to leaves the line on the terminal
        and says so; it does not fall back to either, because that fallback IS
        the spam this exists to remove (Robert, 2026-09-29).
        """
        if not self.status:
            return [f"slack (no status channel): {text}"]
        sent = _post(
            self.status,
            self.token,
            f"rite · `{self._project_label}`: {text}",
            call=call,
            kind=SYSTEM,
            author=self.manager,
        )
        if sent.ok:
            return []
        return [
            f"slack: cannot post to the status channel {self.status} "
            f"({sent.problem}), so this stays here: {text} `rite doctor` says "
            "how to add the app to it."
        ]

    def _open_refinement_channel(self, caller) -> str:
        """Can rite post AND read in the refinement channel? Both, or rounds
        go to the DM (the note's part 3.12): a channel rite cannot post to
        cannot carry a question, and one it cannot read cannot carry his
        answer. Measured, not assumed: one post and one read, now."""
        where = self.refinement_channel
        sent = _post(
            where,
            self.token,
            f"rite: refinement questions from Manager `{self.manager}` come "
            "here. Only the Owner's replies in their threads answer them.",
            call=caller,
            kind=SYSTEM,
            author=self.manager,
        )
        if not sent.ok:
            return (
                f"slack: refinement questions go to the Owner's DM, not {where}: "
                f"rite cannot post there ({sent.problem}). Invite the app to the "
                "channel (`/invite @rite`) to use it."
            )
        heard = _hear(sent.channel, self.token, since=sent.ts, call=caller)
        if not heard.ok:
            return (
                f"slack: refinement questions go to the Owner's DM, not {where}: "
                f"rite can post there but cannot read it ({heard.problem}), so "
                "an answer there would be lost. A private channel needs the "
                "app's `groups:history` scope."
            )
        self.refinement_id = sent.channel
        return (
            f"slack: refinement questions go to {where}; only the Owner's "
            "replies in their threads answer them."
        )

    def _refinement_target(self, message) -> str:
        """The refinement channel for a refinement ROUND (by the question id
        in `asking`'s first line, known to the round ledger), else ""."""
        if not self.refinement_id or self.project is None:
            return ""
        found = _QUESTION_IN_LABEL.search(message.text.split("\n", 1)[0])
        if not found:
            return ""
        from rite_ai.refinement import rounds

        try:
            questions = rounds.all_questions(self.project, self.manager)
        except OSError:
            return ""
        return self.refinement_id if found.group(0) in questions else ""

    def _start_at(self, channel: str, start_line: str) -> None:
        """Where reading begins: where the LAST run stopped, if there was one.

        ⚠ **A5. A message sent while no Manager runs stays in Slack**, since
        there is no daemon (Decision 2). Remembering the position is what
        turns "stays in Slack" into "is delivered at the next start" rather
        than "is skipped because the new run began reading at its own start
        line". The first run ever begins at its start line, so switching
        Slack on does not replay the conversation's history as instructions.
        """
        self.opened[channel] = start_line
        self.since[channel] = self._saved_since.get(channel) or start_line

    def _label(self, what: str, sent: Posted) -> str:
        at = time.strftime("%H:%M", time.localtime(_as_ts(sent.ts)))
        return f"rite's {what} at {at}"

    def _read_history(self, *, call=None) -> list[str]:
        """One conversation's history, the next in turn."""
        out: list[str] = []
        channels = [c for c in (self.dm, self.broadcast_id) if c]
        if channels:
            channel = channels[self._turn % len(channels)]
            self._turn += 1
            heard = _hear(
                channel, self.token, since=self.since.get(channel, ""), call=call
            )
            if heard.ok:
                if heard.skipped:
                    self._problem(
                        f"more than {HISTORY_PAGES * READ_LIMIT} messages were "
                        "waiting; the oldest of them were NOT read"
                    )
                if heard.newest and _as_ts(heard.newest) > _as_ts(
                    self.since.get(channel, "")
                ):
                    self.since[channel] = heard.newest
                    self._save()
                self._say_the_gap(channel, heard.messages)
                out.extend(self._relay(channel, m) for m in heard.messages)
            else:
                # ⚠ Recorded, not raised, and not retried here. The loop's
                # next tick is the retry; an outage must not end a run.
                self._problem(heard.problem)
        return out

    def poll(self, *, call=None) -> tuple[str, ...]:
        """What has been said since the last poll, as mailbox texts, each
        with its header. At most one history read and one thread read per
        call — the budget in `THREAD_SECONDS`."""
        out = self._read_history(call=call)
        out.extend(self._read_a_thread(call=call))
        for heard in out:
            if getattr(heard, "source", None):
                self._picked_up(*heard.source, call=call)
        return tuple(out)

    def _picked_up(self, channel: str, ts: str, *, call=None) -> None:
        """👀 on a message rite has just picked up for the Manager (SCRUM-35).

        The person's first sign it was seen, long before any reply. Only on
        a message ADDRESSED to the Manager (`_relay` decides): an eyes on a
        bystander's line in the broadcast channel would say rite is acting on
        it. Once per message, because each is picked up once; a reaction
        Slack already has is not an error. Never fails the relay: a missing
        `reactions:write` is said once, anything else is a problem line.

        The mechanics are `_react`'s, shared with the ✅ (SCRUM-61) so the
        already-reacted tolerance and the say-once on a missing scope cannot
        hold in one place and not the other.
        """
        self._react(channel, ts, PICKED_UP, call=call)

    def tick_read_answers(self, *, call=None) -> list[str]:
        """✅ on the Owner's reply for each answer a Worker says it READ.

        Reads the acks Workers write on the host (`rite_ai.read_ack`) and
        reacts to the message remembered in `_answered`. Returns lines for
        the terminal, one per tick put on.

        ⚠ **The tick means "the Worker said it read this".** Nothing here,
        and nothing on this machine, observes a read — see `read_ack`. The
        reaction is chosen to say the exchange closed, and the words
        everywhere around it stop short of claiming rite watched it happen.

        Never raises into the supervisor: a Slack outage is a result, and the
        next tick tries again. An ack with no remembered message is NOT an
        error either — the Owner may have answered from the terminal, where
        there is no message to tick.
        """
        if self.project is None or self._reacting == "missing":
            return []
        from rite_ai import read_ack
        from rite_ai.config.parse import load_project

        project = load_project(self.project)
        if isinstance(project, list):
            return []
        lines: list[str] = []
        for worker in sorted(w.name for w in project.workers):
            acks = read_ack.read(self.project, worker)
            if acks is None:
                continue
            if not acks.known:
                # Said once, not every tick: a record nobody can parse is a
                # standing condition, and it is reported where the rest of a
                # Worker's unreadable state is.
                self._problem(f"cannot read {worker}'s read-acks: {acks.unreadable}")
                continue
            for qid in sorted(acks.read):
                if qid in self._ticked:
                    continue
                where = self._answered.get(qid)
                if not isinstance(where, dict):
                    # Answered from the terminal, or before this ran. Marked
                    # so it is not looked at again; there is nothing to tick.
                    self._ticked[qid] = self.clock()
                    continue
                if self._react(
                    str(where.get("channel") or ""),
                    str(where.get("ts") or ""),
                    ANSWER_READ,
                    call=call,
                ):
                    self._ticked[qid] = self.clock()
                    lines.append(
                        f"slack: ✅ on the answer to {qid} — Worker {worker!r} "
                        "says it read it"
                    )
            self._save()
        return lines

    def _react(self, channel: str, ts: str, name: str, *, call=None) -> bool:
        """One reaction, or False and a problem said. The shared half of
        `_picked_up` and `tick_read_answers`: both want the same
        already-reacted tolerance and the same say-once on a missing scope,
        and two copies of that would drift."""
        if not (channel and ts) or self._reacting == "missing":
            return False
        caller = call or _call
        try:
            got = caller(
                "reactions.add",
                self.token,
                None,
                {"channel": channel, "timestamp": ts, "name": name},
            )
        except Exception as e:  # noqa: BLE001 - an outage is a result, not a crash
            self._problem(f"cannot react with :{name}:: {type(e).__name__}: {e}")
            return False
        if got.get("ok") or got.get("error") == "already_reacted":
            return True
        if got.get("error") == "missing_scope":
            self._reacting = "missing"
            self._unsaid.append(
                "slack: no reactions on your messages — the app lacks "
                "reactions:write. Add the scope under OAuth & Permissions"
            )
            return False
        self._problem(f"cannot react with :{name}:: {refusal(got)}")
        return False

    def _say_the_gap(self, channel: str, messages) -> None:
        opened = self.opened.get(channel)
        if not opened:
            return
        waiting = [m for m in messages if _ts_of(m) < _as_ts(opened)]
        if waiting:
            where = "the Owner's DM" if channel == self.dm else self._where
            self._unsaid.append(
                f"slack: {len(waiting)} message(s) sent in {where} while "
                f"{self.manager!r} was not running — delivered at its next turn"
            )

    def _read_a_thread(self, *, call=None) -> list[str]:
        now = self.clock()
        horizon = now - THREAD_HOURS * 3600
        pinned = self._open_rounds()
        self.roots = [
            r
            for r in self.roots
            if r.item or _round_of(r, pinned) or _as_ts(r.ts) >= horizon
        ]
        due = [r for r in self.roots if r.due <= now]
        if not due:
            return []
        # NEWEST FIRST among those due. Found live: with ten roots, oldest
        # first, and one read per tick, a ~30-second cycle ended before it
        # reached the two threads a person had just replied in — the newest
        # roots, which are the ones anyone is replying under.
        root = min(due, key=lambda r: (r.due, -_as_ts(r.ts)))
        root.due = now + (
            PENDING_SLOW_SECONDS if _as_ts(root.ts) < horizon else THREAD_SECONDS
        )
        heard = _read(
            "conversations.replies",
            {"channel": root.channel, "ts": root.ts, "oldest": root.last},
            self.token,
            call=call,
            allowed=PERSON_SUBTYPES_IN_A_THREAD,
        )
        if not heard.ok:
            self._problem(heard.problem)
            return []
        fresh = [
            m
            for m in heard.messages
            if _as_ts(m.get("ts")) > _as_ts(root.last) and m.get("ts") != root.ts
        ]
        if root.item:
            self._confirm_if_answered(root, fresh, call=call)
        question = _round_of(root, pinned)
        if question and not fresh and now >= pinned[question]:
            # TR2, race 6: read past its deadline with nothing new under it.
            # A reply sent before the deadline would be in `fresh`.
            self._read_past_deadline(question, now)
        if heard.newest and _as_ts(heard.newest) > _as_ts(root.last):
            root.last = heard.newest
            self._save()
        return [self._relay(root.channel, m, under=root.label) for m in fresh]

    def _open_rounds(self) -> dict[str, float]:
        """The refinement rounds this Manager is waiting on, by question id
        (TR2). {} with no project, or when the ledger cannot be read: the
        threads are then bounded as before, and nothing is marked."""
        if self.project is None:
            return {}
        from rite_ai.refinement import rounds

        try:
            return rounds.open_questions(self.project, self.manager)
        except OSError:
            return {}

    def _read_past_deadline(self, question: str, now: float) -> None:
        from rite_ai.refinement import rounds

        try:
            if rounds.read_past_deadline(self.project, self.manager, question, at=now):
                self._unsaid.append(
                    f"slack: refinement question {question} had no answer by "
                    "its deadline; it waits for the person and is not asked "
                    "again until they are back"
                )
        except OSError as e:
            self._problem(f"cannot record a refinement deadline: {e}")

    def _counts_as_the_person(self, user: str) -> bool:
        """The Owner, when there is one; any person, when there is not (then
        the broadcast channel is the only place anything was asked)."""
        return bool(user) and (user == self.owner if self.owner else True)

    def _confirm_if_answered(self, root: Root, fresh, *, call=None) -> None:
        """RP1 piece 2: a reply in the thread from the person confirms the
        item; so does their reaction, when the app may read reactions."""
        from rite_ai.managers import pending

        if self.project is None:
            return
        how, at, said_at = "", 0.0, ""
        for m in fresh:
            if self._counts_as_the_person(str(m.get("user") or "")):
                how, at = pending.BY_THREAD_REPLY, _ts_of(m)
                # ⚠ The `ts` AS SLACK GAVE IT, kept as the string it is.
                # `reactions.add` matches a message by this exact value, and
                # a round trip through a float does not survive it: Slack's
                # "1791055851.0" came back "1791055851.000000" and would
                # have reacted to nothing. Caught by a test asserting the
                # reaction's arguments rather than that one was attempted.
                said_at = str(m.get("ts") or "")
                break
        if not how and self._reactions != "missing":
            how, at = self._reacted(root, call=call)
        if not how:
            return
        # ⚠ The Owner's own reply, remembered against the question it
        # answered, so a ✅ can go on THEIR message once the Worker says it
        # read the answer (SCRUM-61). Recorded here because this is the one
        # place that holds both: `root.label` carries the question id
        # `asking` wrote, and `fresh` carries the reply's `ts`. By the time
        # the answer is relayed into the sandbox it is read from the mailbox,
        # which keeps no Slack identity at all.
        answered = _QUESTION_IN_LABEL.search(root.label)
        if answered and how == pending.BY_THREAD_REPLY and said_at:
            self._answered[answered.group(0)] = {
                "channel": root.channel,
                "ts": said_at,
            }
        if pending.confirm(self.project, self.manager, root.item, how, at=at):
            self._unsaid.append(
                f"slack: {root.label} reached the person ({how}); it is no "
                "longer listed as waiting"
            )
        root.item = ""
        self._bound_roots()
        self._save()

    def _reacted(self, root: Root, *, call=None) -> tuple[str, float]:
        from rite_ai.managers import pending

        caller = call or _call
        try:
            got = caller(
                "reactions.get",
                self.token,
                {"channel": root.channel, "timestamp": root.ts},
            )
        except Exception as e:  # noqa: BLE001 - an outage is a result, not a crash
            self._problem(f"cannot read reactions: {type(e).__name__}: {e}")
            return "", 0.0
        if not got.get("ok"):
            if got.get("error") == "missing_scope":
                self._reactions = "missing"
                self._unsaid.append(
                    "slack: reactions are not read — the app lacks "
                    "reactions:read — so only a reply in a question's thread "
                    "confirms it reached you. Add the scope under OAuth & "
                    "Permissions for a reaction to count too"
                )
            else:
                self._problem(f"cannot read reactions: {refusal(got)}")
            return "", 0.0
        self._reactions = "read"
        message = got.get("message") or {}
        for reaction in message.get("reactions") or []:
            if any(
                self._counts_as_the_person(str(u)) for u in reaction.get("users") or []
            ):
                return pending.BY_REACTION, self.clock()
        return "", 0.0

    def _relay(self, channel: str, message: dict, *, under: str = "") -> _Relayed:
        """One message as the Manager will read it: rite's header, then the
        typed text quoted. SPEC §9.16.5."""
        author = str(message.get("user") or "")
        sent = _ts_of(message)
        # N1: inbound Slack text is normalised like ticket text (§9.16.5's
        # accepted inference). The change is said in rite's header, not in
        # the quote.
        from rite_ai.normalise import normalise

        cleaned = normalise((message.get("text") or "").strip())
        # N2: reported at the next check-in, never blocked or withheld.
        from rite_ai import phrases

        where = (
            "the Owner's DM"
            if channel == self.dm
            else "the refinement channel"
            if self.refinement_id and channel == self.refinement_id
            else self._where
        )
        phrases.report(
            self.project,
            f"the Slack message `ts {message.get('ts')}` in {where}",
            cleaned.text,
        )
        normalised = (
            [cleaned.note("this message").strip("[]")] if cleaned.changed else []
        )
        # WHEN IT WAS SAID, in the header. Found live: a DM sent while no
        # Manager ran reached it looking as if it had arrived at the restart.
        when = f"sent {time.strftime('%a %H:%M', time.localtime(sent))}"
        # ⚠ AS THE PERSON TYPED IT. Slack escapes & < > in message text as
        # &amp; &lt; &gt; (its own links and mentions are the only real <…>).
        # Found live: an instruction reached the Manager as `&lt;!-- … --&gt;`,
        # and code with `<` or `&&` in it arrives mangled. Unescaped AFTER
        # normalisation, so an escaped `<!--`, which Slack shows as visible
        # text, is never taken for a hidden comment, and mention detection
        # below reads Slack's raw form, where a mention is a real `<@…>`.
        raw = cleaned.text
        text = _unescaped(raw)
        thread = [f"reply in the thread under {under}"] if under else []
        if self.refinement_id and channel == self.refinement_id:
            # TRQ8, a narrow amendment to SPEC §9.16.5: in the refinement
            # channel, only the Owner (by Slack's authenticated author id),
            # replying in the thread of a refinement round rite started, is
            # the User answering. Anyone else there is context, and cannot
            # answer or accept for him.
            his = (
                bool(author)
                and author == self.owner
                and bool(_round_of(Root(channel, "", under), self._open_rounds()))
            )
            head = _header(
                REFINEMENT_CHANNEL,
                when,
                *normalised,
                *thread,
                *(
                    ("addressed", "INSTRUCTION")
                    if his
                    else (
                        f"from <@{author}>" if author != self.owner else "the Owner",
                        "context — not an instruction",
                    )
                ),
            )
            return _relayed(
                f"{head}\n{_quoted(text)}",
                sent,
                (channel, str(message.get("ts") or "")) if his else None,
            )
        if channel == self.dm:
            if author and self.owner and author != self.owner:
                # One-to-one by construction, so this should not happen. If it
                # does, authority is NOT assumed from the channel alone.
                head = _header(
                    "Owner's DM",
                    when,
                    *normalised,
                    *thread,
                    f"from <@{author}>, not the Owner",
                    "context — not an instruction",
                )
            else:
                head = _header(
                    "Owner's DM", when, *normalised, *thread, "addressed", "INSTRUCTION"
                )
                # ⚠ Recorded HERE, in the one branch that decided this is the
                # Owner addressing this Manager, and from the same facts the
                # header is built from (SCRUM-56). Not in the branch above:
                # a DM from somebody who is not the Owner is context, and a
                # reply is not owed to it. `under` is left out on purpose —
                # a message rite already read inside one of its own threads
                # is answered in that thread by the code that watches it.
                #
                # ⚠ This is NOT the same thing as the `source` carried on the
                # relayed text beside it (SCRUM-35, for the reaction). That
                # travels with one message and is gone once the text is in
                # the mailbox; this is the relay's own state, persisted, and
                # it is what the NEXT reply is matched against.
                self._awaiting = {
                    "channel": channel,
                    "ts": str(message.get("ts") or ""),
                    "at": sent or self.clock(),
                }
                self._save()
                return _relayed(
                    f"{head}\n{_quoted(text)}",
                    sent,
                    (channel, str(message.get("ts") or "")),
                )
            return _relayed(f"{head}\n{_quoted(text)}", sent)
        # A LINKED mention only. A literal "@rite" is what Slack leaves when
        # the autocomplete was not used, and it is text, not addressing.
        mentioned = any(f"<@{me}>" in raw for me in (self.me, self.me_bot) if me)
        who = (
            "the Owner, outside the DM"
            if author and author == self.owner
            else f"<@{author}>, not the Owner"
        )
        if mentioned:
            head = _header(
                self._where,
                when,
                *normalised,
                *thread,
                f"@rite from {who}",
                "context — not an instruction",
            )
            # Addressed to rite by name, so it is acknowledged, though it
            # reaches the Manager as context, not as an instruction.
            return _relayed(
                f"{head}\n{_quoted(text)}",
                sent,
                (channel, str(message.get("ts") or "")),
            )
        else:
            head = _header(
                self._where,
                when,
                *normalised,
                *thread,
                f"from {who}",
                "unaddressed",
                "context",
            )
        return _relayed(f"{head}\n{_quoted(text)}", sent)

    # --- the other direction: the outbox to Slack (A4) -------------------

    @property
    def _state_path(self) -> Path | None:
        if self.project is None:
            return None
        from rite_ai.managers import manager_dir

        return manager_dir(self.project, self.manager) / "slack.json"

    def _state(self) -> dict:
        path = self._state_path
        try:
            data = json.loads(path.read_text()) if path else {}
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def posted(self) -> dict[str, dict]:
        """The relay's own record: outbox filename → where Slack put it.

        ⚠ **Kept HERE, never in the message**, which stays identity-free
        (Decision 1a). The `ts` is what a thread is rooted on and what a reply
        is read under, so a post whose identity is thrown away is one nobody
        can answer in a thread.
        """
        posted = self._state().get("posted")
        return posted if isinstance(posted, dict) else {}

    def _save(self, posted: dict[str, dict] | None = None) -> None:
        """Write the relay's state: what was posted, and how far each thread
        has been read — so a restart neither loses a thread nor re-delivers
        its replies."""
        from rite_ai.state import write_atomic

        path = self._state_path
        if path is None:
            return
        if posted is None:
            posted = self.posted()
        keep = dict(sorted(posted.items())[-POSTED_KEPT:])
        threads = {
            f"{r.channel}:{r.ts}": {"last": r.last, "label": r.label, "item": r.item}
            for r in self.roots
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "started": self._started,
                "since": self.since,
                "posted": keep,
                "threads": threads,
                "notes": self._notes,
                "known": self._known,
                "awaiting": self._awaiting,
                "answered": dict(sorted(self._answered.items())[-POSTED_KEPT:]),
                "ticked": dict(sorted(self._ticked.items())[-POSTED_KEPT:]),
            }
            write_atomic(path, json.dumps(state, indent=1) + "\n")
        except OSError as e:
            self._problem(f"cannot record the relay's state: {e}")

    def _restore(self) -> None:
        """Where a previous run stopped: its cursors, and the threads it was
        reading that are still inside the horizon."""
        state = self._state()
        self._started = bool(state.get("started"))
        known = state.get("known")
        self._known = known if isinstance(known, dict) else {}
        notes = state.get("notes")
        if isinstance(notes, dict):
            self._notes = {
                k: str(v) for k, v in notes.items() if k in ("day", "channel", "ts")
            }
        # Restored so a supervisor that restarts between the Owner's question
        # and the Manager's answer still puts the answer under the question.
        # The window in `_answer_root` is what stops a stale one applying.
        # Restored so an ack that arrives after a supervisor restart still
        # finds the message to tick, and so a tick is not repeated.
        for key, into in (("answered", "_answered"), ("ticked", "_ticked")):
            saved = state.get(key)
            if isinstance(saved, dict):
                setattr(self, into, dict(saved))
        awaiting = state.get("awaiting")
        if isinstance(awaiting, dict):
            self._awaiting = {
                "channel": str(awaiting.get("channel") or ""),
                "ts": str(awaiting.get("ts") or ""),
                "at": awaiting.get("at") or 0.0,
            }
        since = state.get("since")
        if isinstance(since, dict):
            self._saved_since = {str(k): str(v) for k, v in since.items() if v}
        threads = state.get("threads")
        if not isinstance(threads, dict):
            threads = {}
        for key, value in threads.items():
            channel, _, ts = str(key).partition(":")
            if not (channel and ts and isinstance(value, dict)):
                continue
            self.roots.append(
                Root(
                    channel,
                    ts,
                    str(value.get("label") or "rite's message"),
                    last=str(value.get("last") or ts),
                    item=str(value.get("item") or ""),
                )
            )
        # Every pending item that was posted is watched, whatever the saved
        # threads say: the ledger is what must not be dropped (RP1 piece 2).
        if self.project is not None:
            from rite_ai.managers import pending

            for i in pending.waiting(self.project, self.manager):
                if i.ts:
                    self.watch(i.channel, i.ts, _pending_label(i), i.name)
        self.roots.sort(key=lambda r: _as_ts(r.ts))
        self._bound_roots()

    @property
    def _outward(self) -> str:
        """Where a reply goes: the Owner's DM, or the broadcast channel when
        there is no Owner. A reply to the Owner is not status for a channel
        the whole workspace reads."""
        return self.dm or self.broadcast_id

    def post_replies(self, *, call=None) -> list[str]:
        """Post what the Manager has said since this relay last looked.
        Returns what was posted, as lines for the terminal.

        ⚠ **The first time this relay runs for a Manager, earlier replies are
        NOT posted.** A new reader's cursor is "seen nothing", which is right
        for `rite connect` and wrong here: it would post a month of replies
        into the Owner's DM the moment Slack was switched on. So an absent
        state file means "start from now", said once.
        """
        from rite_ai.managers.mailbox import OUTBOX, action_label, mark_read, unread

        target = self._outward
        if self.project is None or not target:
            return []
        from rite_ai.managers import pending

        said = pending.sync(self.project, self.manager, now=self.clock())
        if said:
            self._unsaid.append(said)
        tracked = {i.name for i in pending.waiting(self.project, self.manager)}
        waiting = unread(self.project, self.manager, OUTBOX, READER)
        posted = self.posted()
        if not self._started:
            mark_read(self.project, self.manager, OUTBOX, READER, waiting)
            self._started = True
            self._save(posted)
            return (
                [
                    f"slack: {len(waiting)} earlier repl(ies) from "
                    f"{self.manager!r} are in `rite replies`, not posted — "
                    "Slack starts from now"
                ]
                if waiting
                else []
            )
        lines: list[str] = []
        from rite_ai.managers.checkins import is_checkin
        from rite_ai.managers.mailbox import _needs_action

        # ⚠ TWO DESTINATIONS (RP1 piece 3, Robert 2026-09-28). What needs the
        # person goes top-level into the DM and is NEVER held. What is for
        # reading goes into a thread: under the next check-in when check-in
        # windows are configured ("nothing between scheduled reports", for
        # the reading pile only), else under one notes root a day. So the DM's
        # top level is the scan list.
        #
        # ⚠ **So posts leave the outbox's order, and the cursor cannot say
        # what was posted.** It advances only over the run of messages from
        # the start that are ALL posted (below), and `posted` is what stops a
        # message being posted twice while the cursor waits behind a held one.
        holding = self._holds_reading()
        held: list = []
        for message in waiting:
            if message.path.name in posted:
                continue
            reading = not _needs_action(message)
            # ⚠ AN ANSWER IS NOT AMBIENT STATUS, and this is the distinction
            # `reading` alone could not make (SCRUM-56). `_needs_action` asks
            # "does this need the person", and an answer does not — nobody has
            # to act on it. So every answer the Owner asked for was filed as
            # reading, which here meant two things, both wrong: HELD until the
            # next check-in, for up to a day, and then posted into the day's
            # notes thread, whose own root line says "nothing in this thread
            # needs you". The Owner asked a question in their DM and the reply
            # went somewhere they had been told not to read.
            answering = self._answer_root(message) if reading else None
            if (
                reading
                and not answering
                and holding
                and self.clock() - message.timestamp < HOLD_MAX_SECONDS
            ):
                held.append(message)
                continue
            # ⚠ REDACTED ON THE WAY OUT. A Manager pastes command output into
            # its replies, and this posts them where people read — with no
            # Owner, into a channel the whole workspace reads. Same structural
            # rule as the journal (C7), plus the one secret the relay holds:
            # its own token. The outbox file itself is left as written, so
            # `rite connect` on this machine still sees exactly what was said.
            # And the Manager's live GitHub token (C6/C26), by EXACT value:
            # the structural rule misses `oauth_token: <t>`, which is what
            # printing the Manager's gh config shows (measured).
            # The same for its Claude login and its Cursor key.
            text = self._linked(self._outgoing(message.text), posted, call=call)
            if reading:
                # Under the Owner's own message when this answers one, else
                # today's notes. Both are threads, so the DM's top level —
                # the scan list — is unchanged either way.
                if answering and self._post_in_thread(
                    message, text, answering, posted, lines, call, watch_root=True
                ):
                    continue
                # ⚠ **A FAILED ANSWER POST FALLS THROUGH TO NOTES INSTEAD OF
                # STOPPING.** The answer root is the Owner's own message: a
                # ts rite never posted and cannot verify, which the Owner can
                # delete. Review measured the first version breaking the loop
                # on it — nothing marked read, `_awaiting` still set, so
                # every tick retried the same dead thread for the rest of the
                # window and NOTHING else was posted either, questions and
                # check-ins included. The Owner saw silence, which is the
                # failure A5 exists to prevent. The reply itself must still
                # reach them, so it goes to the pile that always can.
                if answering:
                    self._awaiting = {}
                    self._save(posted)
                root = self._notes_root(target, call=call)
                if root is None:
                    break
                if not self._post_in_thread(message, text, root, posted, lines, call):
                    break
                continue
            checkin = is_checkin(self.project, self.manager, message.path.name)
            if checkin and not self.dm:
                # ⚠ No Owner, so no command channel: an answer typed in
                # Slack reaches the Manager as context only. Said where the
                # questions are, not left to be discovered (D-95).
                text += (
                    "\n\n_No Owner DM is configured (slack.owner_user), so a "
                    "reply here reaches the Manager as context, not as an "
                    f"answer it acts on. Answer with `rite message {self.manager} "
                    '"…"` on this machine._'
                )
            label = action_label(message)
            shown = pending.kind_of(self.project, self.manager, message)
            sent = _post(
                self._refinement_target(message) or target,
                self.token,
                f"*{self.manager}*" + (f" ({label})" if label else "") + f": {text}",
                call=call,
                kind=STATUS if shown == pending.READING else shown,
                body=text,
                author=self.manager,
                ticket=_ticket_in(text) if shown == pending.NEEDS_ANSWER else "",
            )
            if not sent.ok:
                # Not marked read, so the next tick retries it — and the ones
                # after it wait, so replies are never posted out of order.
                self._problem(f"cannot post a reply: {sent.problem}")
                break
            posted[message.path.name] = {
                "channel": sent.channel,
                "ts": sent.ts,
                "posted_at": self.clock(),
                **_carried_question(message.text),
            }
            remembered = (
                self._label("check-in", sent)
                if checkin
                else self._label(f'reply "{" ".join(text.split())[:40]}"', sent)
            )
            self.remember(sent.channel, sent.ts, remembered)
            if message.path.name in tracked:
                # Posted is not answered: its thread is read until the person
                # replies there (or reacts), however long that takes.
                pending.posted(
                    self.project, self.manager, message.path.name, sent.channel, sent.ts
                )
                self.watch(sent.channel, sent.ts, remembered, message.path.name)
            lines.append(
                f"slack: posted {message.path.name} → {sent.channel} ts {sent.ts}"
            )
            if checkin and self.dm and self.broadcast_id:
                # ⚠ THE MIRROR (K5, D-94/D-95). The check-in's answer thread
                # is rooted in the Owner's DM, the command channel, because
                # an answer to a queued question is an instruction. The
                # broadcast copy is for everyone else to read, and a reply
                # under it reaches the Manager as context — the relay's
                # header says so, whoever typed it.
                mirror = _post(
                    self.broadcast_id,
                    self.token,
                    f"*{self.manager}* (check-in, mirrored from the Owner's DM; "
                    f"replies here are read as context): {text}",
                    call=call,
                    # Muted: an answer here is context, never the answer the
                    # DM copy waits for, so it must not look like one.
                    kind=STATUS,
                    body="_Mirrored from the Owner's DM; replies here are read "
                    f"as context._\n{text}",
                    author=self.manager,
                )
                if mirror.ok:
                    posted[message.path.name]["mirror"] = {
                        "channel": mirror.channel,
                        "ts": mirror.ts,
                    }
                    self.remember(
                        mirror.channel,
                        mirror.ts,
                        self._label("check-in (broadcast mirror)", mirror),
                    )
                    lines.append(
                        f"slack: mirrored the check-in → {mirror.channel} ts "
                        f"{mirror.ts}"
                    )
                else:
                    # The DM copy went out, which is the one that matters for
                    # answers; the mirror failing is said, not retried into a
                    # duplicate DM post.
                    self._problem(f"cannot mirror the check-in: {mirror.problem}")
            self._save(posted)
            if checkin and held:
                # The reading held since the last check-in goes in THIS one's
                # thread, oldest first.
                root = (sent.channel, sent.ts)
                for h in held:
                    if not self._post_in_thread(
                        h,
                        self._linked(self._outgoing(h.text), posted, call=call),
                        root,
                        posted,
                        lines,
                        call,
                    ):
                        break
                held = []
        # Past every message from the start that is posted, and no further:
        # a held one keeps its place for the check-in that carries it.
        done = []
        for message in waiting:
            if message.path.name not in posted:
                break
            done.append(message)
        mark_read(self.project, self.manager, OUTBOX, READER, done)
        return lines

    def _outgoing(self, text: str) -> str:
        from rite_ai.managers import claude_login, cursor_login, github_access
        from rite_ai.sandbox import redact_assignments

        return redact_assignments(
            text,
            (
                self.token,
                *github_access.manager_secrets(self.project, self.manager),
                *claude_login.manager_secrets(self.project, self.manager),
                *cursor_login.manager_secrets(self.project, self.manager),
            ),
        )

    def _linked(self, text: str, posted: dict, *, call=None) -> str:
        """`text` with each question id rite posted earlier rendered as a link
        to that post (SCRUM-47).

        ⚠ **At post time, because only Slack has a link.** A Manager's outbox
        is read in Slack and in a terminal (`rite replies`) alike, so the text
        names a question by its id, `q3f9a`, which means something in both.
        Here, on the way to Slack, an id this relay posted becomes
        `<permalink|q3f9a>`: the person taps through to the question instead of
        searching for it. An id rite never posted (or posted before posts
        recorded their id) stays as written, and so does a link Slack would
        not give. A question's own id, in the header `asking` writes, is left
        alone: that introduces it, it does not refer back to it."""
        where = {
            rec["qid"]: (rec.get("channel", ""), rec.get("ts", ""))
            for rec in posted.values()
            if isinstance(rec, dict) and rec.get("qid")
        }
        if not where:
            return text
        from rite_ai.managers.asking import own_question_id

        own = own_question_id(text)

        def link(found: re.Match) -> str:
            qid = found.group(0)
            if qid == own:
                return qid
            channel, ts = where.get(qid, ("", ""))
            url = self._permalink(channel, ts, call=call) if channel and ts else ""
            return f"<{url}|{qid}>" if url else qid

        return _QUESTION_IN_LABEL.sub(link, text)

    def _permalink(self, channel: str, ts: str, *, call=None) -> str:
        """Slack's own link to one message, or "" when it gives none.

        `chat.getPermalink`, not a URL built by hand: Slack's format for a link
        is Slack's to change, and the call needs no scope beyond seeing the
        conversation. Asked once per message and remembered for the run."""
        key = (channel, ts)
        if key in self._links:
            return self._links[key]
        if self._linking == "missing":
            return ""
        caller = call or _call
        try:
            got = caller(
                "chat.getPermalink", self.token, {"channel": channel, "message_ts": ts}
            )
        except Exception as e:  # noqa: BLE001 - a link is a convenience, never a failure
            self._problem(f"cannot link to an earlier post: {type(e).__name__}: {e}")
            return ""
        url = str(got.get("permalink") or "") if got.get("ok") else ""
        if not got.get("ok"):
            if got.get("error") == "missing_scope":
                self._linking = "missing"
                self._unsaid.append(
                    "slack: earlier posts are named by id, not linked — Slack "
                    "refused chat.getPermalink for want of a scope"
                )
            else:
                self._problem(f"cannot link to an earlier post: {refusal(got)}")
        self._links[key] = url
        return url

    def _holds_reading(self) -> bool:
        """Hold reading for the next check-in only when one will come."""
        from rite_ai.managers.checkins import windows

        return self.project is not None and windows(self.project).usable

    def _post_in_thread(
        self, message, text, root, posted, lines, call, *, watch_root=False
    ) -> bool:
        channel, ts = root
        sent = _post(
            channel,
            self.token,
            f"*{self.manager}*: {text}",
            thread=ts,
            call=call,
            # ⚠ An answer is not tagged as status. `STATUS` renders muted,
            # under "ℹ️ Status", and the premise of routing an answer to the
            # DM is that it is not ambient status — saying so in the thread
            # and then labelling it Status contradicts the move.
            kind=ANSWER if watch_root else STATUS,
            body=text,
            author=self.manager,
        )
        if not sent.ok:
            self._problem(f"cannot post a reply: {sent.problem}")
            return False
        posted[message.path.name] = {
            "channel": sent.channel,
            "ts": sent.ts,
            "thread": ts,
            "posted_at": self.clock(),
            **_carried_question(message.text),
        }
        if watch_root:
            # ⚠ **THE THREAD rite JUST ANSWERED IN HAS TO BE READ BACK.**
            # `_notes_root` watches its own root, so a reply under the notes
            # thread reaches the Manager. The Owner's own message was never
            # a `Root`, and `conversations.history` does not return thread
            # replies — so before this, routing the answer there created the
            # obvious place for the Owner to say "then deploy it" and made
            # it the one place nothing was listening. Review measured it:
            # `roots` held only the two start lines.
            # Labelled as the OWNER's message, not as one of rite's: the
            # label is what a relayed reply's header shows the Manager
            # ("reply in the thread under …"), and this root is theirs.
            at = time.strftime("%H:%M", time.localtime(_as_ts(ts)))
            self.remember(sent.channel, ts, f"the Owner's own message at {at}")
        self._save(posted)
        lines.append(
            f"slack: posted {message.path.name} → {sent.channel} in the thread of {ts}"
        )
        return True

    def _answer_root(self, message):
        """`(channel, ts)` of the Owner's message this reply answers, or None.

        None means "nothing says this is an answer", which is the day's notes
        — the right place for a Manager that is reporting rather than
        replying. See `_awaiting` for what this does and does not claim.
        """
        if getattr(message, "by_rite", False):
            # ⚠ rite's own narration, not the Manager answering. `kind`
            # records the command and `rite reply`'s kind is what
            # `refinement.protocol._tell_user` writes too — so without this,
            # rite's line about a refinement round was threaded under
            # whatever the Owner last asked, and exempted from the check-in
            # hold along with it. See `mailbox.Message.by_rite`.
            return None
        awaiting = self._awaiting
        channel = str(awaiting.get("channel") or "")
        ts = str(awaiting.get("ts") or "")
        if not (channel and ts):
            return None
        if channel != self.dm:
            # The Owner's DM changed (`slack.owner_user` was edited) since
            # this was recorded. An answer belongs to the person who asked,
            # and posting it into the former Owner's DM would disclose it to
            # somebody who is no longer the Owner.
            return None
        try:
            at = float(awaiting.get("at") or 0.0)
        except (TypeError, ValueError):
            return None
        if not at:
            return None
        # Written BEFORE the Owner's message reached the Manager: it cannot
        # be a reply to it. The ordering matters because the relay files an
        # inbox message by when it was SENT in Slack (`_Relayed.sent_at`),
        # which can be well behind the moment rite heard it.
        if message.timestamp < at:
            return None
        if self.clock() - at >= ANSWER_WINDOW_SECONDS:
            return None
        return (channel, ts)

    def _notes_root(self, target: str, *, call=None):
        """Today's top-level notes post, made on first use; (channel, ts), or
        None when it cannot be posted (said, and retried next tick)."""
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        if self._notes.get("day") == day and self._notes.get("ts"):
            return (self._notes["channel"], self._notes["ts"])
        shown = time.strftime("%a %d %b", time.localtime(self.clock()))
        sent = _post(
            target,
            self.token,
            f"*{self.manager}*: notes for {shown}, for reading. Nothing in "
            "this thread needs you; what does is posted on its own.",
            call=call,
            kind=STATUS,
            body=f"Notes for {shown}, for reading. Nothing in this thread needs "
            "you; what does is posted on its own.",
            author=self.manager,
        )
        if not sent.ok:
            self._problem(f"cannot post today's notes root: {sent.problem}")
            return None
        self._notes = {"day": day, "channel": sent.channel, "ts": sent.ts}
        self.remember(sent.channel, sent.ts, self._label(f"notes for {shown}", sent))
        self._save()
        return (sent.channel, sent.ts)

    def drain(self, *, call=None) -> tuple[str, ...]:
        """One last read of every conversation and every thread, at the end
        of a run — so what was said during its final cycle is in the inbox
        for the next start, rather than read only if the tick budget happened
        to reach it before the engine exited. Found live: it did not.

        Once per run, so outside the per-tick budget: at most two history
        reads and `THREADS_MAX` thread reads.
        """
        out: list[str] = []
        for _ in range(len([c for c in (self.dm, self.broadcast_id) if c])):
            out.extend(self._read_history(call=call))
        for root in list(self.roots):
            root.due = 0.0
        for _ in range(len(self.roots)):
            out.extend(self._read_a_thread(call=call))
        return tuple(out)

    def close(self, *, call=None, undelivered: str = "") -> list[str]:
        """The end of a run: post whatever the last cycle said, then say in
        Slack that nobody is listening.

        ⚠ The wait loop only runs while a cycle is alive, and a Manager's last
        `rite reply` is usually written just before its engine exits — so
        without the flush the final reply of every run reached `rite connect`
        and never reached Slack.

        ⚠ **A5: the silence is SAID, where the person is.** With no daemon a
        message sent now is not read until the next `rite start`, and a user
        who is not told reads that silence as "it is broken". The last thing
        in each conversation is therefore a line saying so. Only a killed
        process skips it — and then the last line is the start line, which
        is the one case this cannot cover.
        """
        lines = self.post_replies(call=call)
        lines.extend(
            self._say_status(
                f"Manager `{self.manager}` has stopped. What is sent to it now "
                f"waits in Slack and is delivered when `rite start "
                f"{self.manager}` next runs.",
                call=call,
            )
        )
        if undelivered and self.dm:
            # ⚠ WHERE THE PERSON TYPED IT, and the one stop line that stays in
            # the DM: a message rite already took from Slack but the Manager
            # never received reads, from here, exactly like one it acted on;
            # the terminal line alone reaches nobody on a phone (coordinator,
            # 2026-09-28). This needs the person; a plain stop does not.
            sent = _post(
                self.dm,
                self.token,
                f"rite: Manager `{self.manager}` has stopped.\n⚠ {undelivered}",
                call=call,
                # A message taken from Slack that the Manager never received is
                # a delivery problem, and shown as one.
                kind=DELIVERY,
                author=self.manager,
            )
            if not sent.ok:
                lines.append(
                    "slack: could not say in the DM that a message was not "
                    f"delivered: {sent.problem}"
                )
        return lines
