# A3a — which Slack transport, and what it costs

**Read from Slack's own documentation on 2026-09-24**, not from reputation.
`api.slack.com` now redirects to `docs.slack.dev`; every citation below is
from there.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

---

## The constraint this decision serves

Robert's requirement, verbatim: **"no daemon, no hosting, no separate
service"** — `rite start <manager>` itself does the listening. The supervisor
already has a loop that wakes every `POLL_SECONDS = 2.0` and already calls
`mail_waiting` there, so the shape a transport has to fit is **an outbound
call from inside an existing loop**.

## Three transports, not two

| | inbound listener | persistent connection | fits "no daemon, no hosting" |
|---|---|---|---|
| Events API over HTTP | **required** | no | ❌ ruled out |
| Socket Mode | no | **required** | ⚠ no hosting, but a long-lived socket |
| **Web API polling** | no | no | ✅ **the only one needing neither** |

⚠ **The Events API is ruled out by the requirement, not by preference.** It
needs a publicly reachable HTTPS URL that Slack POSTs to, and that URL must
be verified before the subscription can be saved. That is hosting.

## Recommendation: **Web API polling**

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

## ⚠ The cost Robert has to see, and it is not latency

**Polling's rate limit depends on how the Slack app is DISTRIBUTED, not on
what rite does.** From the method's own page:

> "As of May 29, 2025, for new applications and installation commercially
> distributed outside of the Marketplace, this method is rate limited to **1
> request per minute**. The maximum and default values for the `limit`
> parameter have both been reduced to **15 objects**. For Marketplace and
> internal customer-built applications, this method has Tier 3 rate limits."

So:

- **Each user creates their own workspace app → internal custom app → Tier 3
  → a 2-second poll works.** This is the path that functions.
- **rite ships one distributed Slack app outside the Marketplace → 1 request
  per minute.** A 2-second poll is impossible; the best achievable is a
  once-a-minute check, and only 15 messages per read.
- **And the obvious escape is closed:** Socket Mode apps *"are not currently
  allowed in the public Slack Marketplace"*, so "use Socket Mode and get
  listed" is not available either.

⚠ **That makes this a distribution decision wearing a transport costume.**
Choosing polling commits rite to telling each user to create their own Slack
app. That is more setup for them, and it is the only shape in which the
latency is acceptable. **Flagged rather than chosen.**

⚠ **One claim deliberately NOT relied on.** A web search asserted that from
March 2026 existing non-Marketplace installations also lose Tier 3. The
string "2026" appears nowhere in the rate-limit guide, the method reference,
or either changelog entry, and the docs currently say the opposite —
*"Existing installations ... will not be subject to the new posted limits"*.
Recorded as unverified. **Re-check before shipping**, because if it is true
the distribution decision above gets sharper rather than softer.

## What Socket Mode would cost instead

Kept because it is Slack's own documented answer to "no public endpoint", and
because if the distribution constraint above is unacceptable it is the
fallback.

- Needs an **app-level token** (`xapp-`) *in addition to* the bot token, with
  the **`connections:write`** scope — so A2's credential question doubles.
- Holds a WebSocket open; **at most 10 concurrent connections per app**,
  which caps concurrent Managers at ten before anything of rite's does.
- *"No matter what, you'll need to handle connection refreshes once every few
  hours"* — reconnect logic, disconnect reasons, and a `hello` payload whose
  `approximate_connection_time` example is 3600s.
- **Every event must be acknowledged** by `envelope_id` or Slack retries it.

**None of that is hosting. All of it is a daemon's problem list** —
reconnection, liveness, acknowledgement, connection budget — arriving inside
a process whose selling point is that it is not one. That is the tension, and
it is Robert's to weigh rather than mine to settle.

⚠ Slack also recommends **HTTP for production** (*"the highest possible
reliability for application connectivity"*) and Socket Mode for local and
firewalled use. rite's requirement rules out their production recommendation,
so whichever of the two remaining is chosen is off Slack's preferred path.

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
