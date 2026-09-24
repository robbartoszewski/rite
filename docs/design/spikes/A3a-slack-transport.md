# A3a — which Slack transport, and what it costs

**Read from Slack's own documentation on 2026-09-24**, not from reputation.
`api.slack.com` now redirects to `docs.slack.dev`; every citation below is
from there.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

---

## ⚠ CORRECTED 2026-09-24 — the requirement I was given was not the requirement

**The first version of this note was decided against "no daemon, no
hosting". Robert never said that.** What he said, on 2026-09-21, verbatim:

> "rite start X process (for the current Owner) will be the thing that
> listens for Slack changes."

That is a statement about **which process listens** — the supervisor rather
than a separate service. It was relayed to me as "no daemon, no hosting", I
decided on the strict reading, and the strict reading was doing real work in
the recommendation.

**On the actual requirement, Socket Mode qualifies.** A WebSocket held open
inside `rite start X` *is* the supervisor listening, in exactly the process
he named. So the two transports were re-weighed on equal footing.

⚠ **The recommendation did not change. The reasons did, and the reason that
used to carry it turns out to carry nothing.**

---

## The constraint, restated correctly

**Whatever listens must be the `rite start X` process**, not a service
somebody else runs. The supervisor already wakes every `POLL_SECONDS = 2.0`
and already calls `mail_waiting` there, so an outbound call fits without new
machinery — but a held-open socket in that same loop would also satisfy the
requirement as stated.

## Three transports, not two

| | inbound listener | persistent connection | fits "no daemon, no hosting" |
|---|---|---|---|
| Events API over HTTP | **required** | no | ❌ ruled out |
| Socket Mode | no | **required** | ⚠ no hosting, but a long-lived socket |
| **Web API polling** | no | no | ✅ **the only one needing neither** |

⚠ **The Events API is ruled out by the requirement, not by preference.** It
needs a publicly reachable HTTPS URL that Slack POSTs to, and that URL must
be verified before the subscription can be saved. That is hosting.

## Recommendation, re-derived: **Web API polling** — same answer, different reasons

⚠ **The reason it used to win is gone.** "Only transport that needs neither a
listener nor a held-open connection" was decided against a requirement Robert
did not set, and against a distribution cost that turns out to be shared.

**It still wins, on three things that survive the correction:**

1. **Half the setup and half the credentials.** One `xoxb-` token against
   two, and no Socket Mode toggle. A2 is already blocked on a
   credential-model question; Socket Mode doubles it.
2. **Per-Manager isolation.** Slack explicitly does not guarantee which
   socket a payload lands on, so multi-Manager under Socket Mode means every
   Manager receives every Manager's traffic and filters. Polling gives each
   Manager its own channel read, with nothing shared.
3. **Fewer failure modes in a loop that must stay simple.** A failed HTTPS
   call is reported and retried on the next 2-second tick. A dropped socket
   is a reconnect state machine, acknowledgement bookkeeping, and a
   connection budget — inside the supervisor.

**And what Socket Mode would have bought is worth nothing here:** push
latency does not help a Manager that only reads between turns, and the rate
limit it avoids is not binding once every user has their own app — which both
transports require.

⚠ **If any of these changes, revisit:** if rite ever distributes one shared
Slack app, polling becomes impossible (1/min) and Socket Mode becomes
impossible too (the socket would have to be central) — **the requirement
itself would have to move**, not the transport.

---

### The mechanics

`conversations.history` with an `oldest` timestamp, called from the loop that
already exists.

**Why, in one line:** it is the only transport that requires neither an
inbound listener nor a held-open connection, which is what "no daemon"
actually means once it is made mechanical.

Supporting facts, all documented:

- `GET conversations.history` takes `oldest` — *"Only messages after this
  Unix timestamp will be included in results"* — and a `cursor` for
  pagination. That is a resumable read with no server state of our own.
- Scopes are small: **`channels:history`** to read a channel the bot is in,
  **`chat:write`** to post. Posting is a plain outbound HTTPS call with the
  bot token and nothing else.
- Tier 3 is **50+ requests per minute**. A 2-second poll is 30/min. Slack
  separately recommends *"a limit of 1 request per second for any given API
  call"*; 0.5/s is inside that.
- `chat.postMessage` is a special tier: *"no more than one message per second
  per channel"*. The mailbox emits replies at cycle boundaries, minutes
  apart, so this is not a constraint rite can reach.

## ⚠ The cost that turned out to be shared, not a cost of polling

The first version of this note made this polling's decisive disadvantage.
**It is not a disadvantage at all, because the other option requires the same
thing.**

`conversations.history` drops to **1 request per minute, 15 objects** for
apps distributed outside the Marketplace; Tier 3 (50+/min) applies to
Marketplace and **internal customer-built** apps. So polling needs each user
to have their own workspace app.

**So does Socket Mode**, and this is the finding that settles it. The socket
is opened with an **app-level token**, and that token is app-scoped:

> "App-level tokens represent your app across organizations, including
> installations by all individual users on all workspaces in a given
> organization."
> "App-level tokens are obtained upon app creation. Find your app-level token
> in the Basic Information tab of the app settings."

**Nothing in Slack's documentation ever issues an `xapp-` token to an
installing workspace or user.** It lives in the developer's app settings. So
for a user's own `rite start` to hold the socket, that user must own the app.
The only documented distributed Socket Mode topology is the opposite — one
process run by the app owner, holding connections for every installation,
with an `InstallationStore` to look up each workspace's bot token. That is a
central service, which is the thing the requirement rules out.

⚠ **Therefore: every user creates their own Slack app under BOTH transports.**
Once that is true, the app is an internal customer-built application, Tier 3
applies, and **polling's rate limit stops being a constraint at all** — a
2-second poll is 30 requests a minute against a 50+/min allowance.

**The setup burden does not disappear under Socket Mode. It grows:**

| | polling | Socket Mode |
|---|---|---|
| create an app | yes | yes |
| tokens the user must handle | **one** (`xoxb-`) | **two** (`xoxb-` **and** `xapp-` with `connections:write`) |
| extra app settings | — | enable Socket Mode, generate the app-level token |

That doubles A2's credential question — which is currently blocked on a
credential-model decision — for no gain rite can use.

⚠ **One claim carried as ASSEMBLED, not quoted.** That all installations'
events arrive over one socket is nowhere stated in a single sentence. It is
assembled from three documented facts: the token is app-scoped; the `hello`
frame identifies the connection by `app_id` with no `team_id`; and the
official distributed pattern is one socket process plus an
`InstallationStore`. Strong and convergent, but assembled. **The decisive
fact — that `xapp-` is issued at app creation and never to an installer — is
quoted directly.**

## What Socket Mode costs, now that it is a live option

- **Two credentials instead of one** (above).
- **Reconnects.** *"No matter what, you'll need to handle connection
  refreshes once every few hours."* Disconnect reasons, a `hello` payload
  with an `approximate_connection_time`, and reconnect logic inside the
  supervisor's loop.
- **Per-event acknowledgement** by `envelope_id`, or Slack retries.
- ⚠ **The 10-connection cap is PER APP** — *"Slack limits the number of
  concurrent WebSocket connections to 10 per app"* — not per token and not
  per installation.
- ⚠ **And the multi-Manager consequence is worse than the cap.** Slack:
  *"When multiple connections are active, each payload may be sent to ANY of
  the connections. It's best not to assume any particular pattern for how
  payloads will be distributed across multiple open connections."*

  With several Managers under one user's app, each holding a socket, **a
  message meant for one Manager's channel can be delivered to another
  Manager's socket.** Every Manager would have to receive everything and
  filter — so every Manager sees every other Manager's Slack traffic. That is
  an isolation property rite would be giving up, and multi-Manager is v0.7.0.

  **Under polling each Manager reads its own channel independently and there
  is no cross-talk.** Nothing is shared and nothing has to filter.

**Does the 10-cap bind before rite's own limits?** Yes — rite has no Manager
count below ten, so Slack's cap arrives first. It is generous for realistic
use (one to three Managers), so it is a ceiling rather than a live
constraint; the payload-distribution behaviour above is the real cost, and it
bites at **two** Managers, not ten.

⚠ **What Socket Mode genuinely buys:** push latency instead of a poll
interval, and no Web API rate-limit exposure on the read path at all. It is
also Slack's documented recommendation for on-premise and firewalled cases.
**The latency is worth nothing here** — a Manager only reads between turns,
so a message noticed instantly is still delivered at the next cycle boundary,
which is where `mail_waiting` already delivers it.

## What polling buys, beyond fitting the constraint

- **The latency is already the product's latency.** A message is noticed
  within one poll and delivered at the next cycle boundary — the same
  boundary `mail_waiting` already feeds. A faster transport would not make a
  Manager answer sooner, because the Manager only reads between turns.
- **Failure is a failed HTTPS call**, which the loop can report and retry on
  its next tick, rather than a dropped socket with a reconnect state machine.
- **Nothing is held open**, so `rite start` stopping is the channel stopping,
  with no cleanup and no orphaned connection.

## ⚠ Status: the transport is chosen, the proof is NOT made

A3a's done-when is *"send a message in a Slack channel; a standalone script
using the stored token prints it within 5s"*. **That observation has not been
made** — it needs a real workspace, an app, and a bot token, none of which
exist yet.

**The ticket stays open.** What is settled is which transport to build and
why; what is outstanding is the measurement that shows it works, plus the two
questions above that belong to Robert:

1. Does each user create their own Slack app (Tier 3, works) or does rite
   distribute one (1/min, does not)?
2. Is the "no daemon" requirement about **hosting** — in which case Socket
   Mode qualifies — or about **holding a connection open**, in which case it
   does not?
