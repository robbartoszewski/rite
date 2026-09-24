# A3b — can rite ship ONE Slack app, or must every user create their own?

**Robert's question:** can rite create and ship a Slack app that users add to
their workspace easily, rather than each user creating their own?

**Answer: no, rite cannot distribute one app and keep polling — but the
middle path works, and it is nearly as easy.** Ship a YAML manifest and a
one-click link; each user creates their own app in their own workspace,
which keeps the Tier 3 rate limits the transport needs.

**Read from Slack's own documentation on 2026-09-24**, fetching the `.md`
source of each page rather than a rendering, so the quotations below are
Slack's words. `api.slack.com` redirects to `docs.slack.dev`. This was read
rather than recalled, because reasoning from what is generally known about
Slack has been wrong twice on this project.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

---

## 1. The 1/min limit is scoped per app PER WORKSPACE — and that does not rescue polling

The question that decides everything: is the reduced limit a single bucket
shared across every installation, or one bucket each?

**Quoted**, from the rate limits page:

> "Broadly, you'll encounter limits like these, applied on a "per API method
> per workspace/team per app" basis."

and from the same page's Web API section:

> "Your app's requests to the Web API are evaluated per method, per
> workspace. Rate limit windows are per minute."

So a distributed rite app would **not** put every user in one shared bucket.
Each installing workspace gets its own.

⚠ **It makes no difference, and the reason is worth stating plainly: per-app
scoping would have been catastrophic, and per-workspace scoping is merely
fatal.** Each user's own bucket is still *one request per minute*. That is
the poll interval, not an allowance to divide. rite polls every two seconds —
30 requests a minute — against a limit of one.

⚠ **There is a second consequence, and it is a correctness problem rather
than a latency one. Marked as INFERRED, not stated by Slack.** The same
change caps the `limit` parameter at 15 objects. One call per minute
returning at most 15 messages cannot read a channel busier than 15
messages/minute at all: draining the backlog needs pagination, pagination
needs more calls, and more calls are refused. A slower rite would be a
tolerable product; a rite that silently drops messages is not.

## 2. The Marketplace route is closed to rite, on three independent grounds

Any one of these would be enough on its own.

**It describes rite's feature as unsuitable.** From the list of apps
unsuitable for the Slack Marketplace — apps that:

> "enable remote execution on a server via a downloadable third party script
> e.g terminal commands from Slack."

That is what rite's Slack relay does: a message in a channel reaches a
Manager that runs commands on the user's machine. Treat this as the binding
one; it is not a technicality rite could design around without removing the
feature.

**It has a floor rite cannot reach.** Also unsuitable — apps that:

> "are installed on less than 10 active workspaces and have less than 10
> weekly active users."

And apps that "are in private beta, still being built, or have not been fully
tested", which the Slack track currently is.

**And submission requires the hosting the constraint rules out.** Listing is
gated on public distribution first:

> "If you haven't enabled public distribution for your app, follow our guide
> to distributing apps publicly. This is a pre-requisite for submitting your
> app for Slack Marketplace review"

and public distribution requires a hosted endpoint:

> "Slack apps open to installation by other workspaces have additional
> security requirements. Your app must support SSL for all of the following
> URLs: OAuth redirect URLs..."

So the Marketplace route reintroduces a service somebody must host, **before
review even begins** — the thing the requirement exists to avoid.

⚠ **Nothing in the guidelines excludes open-source or CLI tools as a
category.** The obstacle is specific to what rite does, not to what rite is.
A reader looking for "are we the wrong kind of project" will not find that;
they should look at the remote-execution line instead.

## 3. The manifest route is real, documented, and is the recommendation

**The shareable link is Slack's own documented pattern**, not a trick found
by experiment:

> ```
> https://api.slack.com/apps?new_app=1&manifest_yaml=<manifest_here>
> ```
> "You can use this URL in any link or button you want — the URL will direct
> users right into the app creation flow."

**The user experience, assembled from Slack's two step-lists** (the manifest
guide and the app-settings quickstart):

1. Click rite's link → "Choose to create an app **from a manifest**" → pick a
   workspace → **Next** → review → **Create**
2. **OAuth & Permissions** → **Install to Workspace** → **Allow**
3. Copy the access token from **OAuth & Permissions**
4. `/invite @rite` into the channel

The manifest pre-declares the scopes, so the fiddliest step — hunting through
the scope picker for `channels:history` and `chat:write` — disappears. Steps
2 to 4 are unavoidable in **any** route that ends with rite holding a token,
including a Marketplace install, so the manifest route's extra cost over a
directory install is one click and a workspace picker.

**And it needs no hosting**, which is the whole point:

> "Once created, your app can be installed to its associated workspace
> **without any code for handling authorization**. The one-click install for
> an unlisted single-workspace app generates an access token, which can then
> be used to authenticate API requests, but only within that associated
> workspace."

**The rate limit it preserves is generous**, and Slack states it twice:

> "Internal customer-built applications are not impacted by these changes and
> continue to have a rate limit of 1,000 messages per request at 50+ requests
> per minute."

> "For **Custom apps**, the `conversations.history` and `conversations.replies`
> API methods are limited to 50+ requests per minute. The maximum and default
> values for the limit parameter are 1,000 objects."

A follow-up changelog on 3 June 2025 exists for no other purpose than to
restate it: *"**Any** internal customer-built apps will maintain their
existing rate limits and will not be subject to the new posted limits."*

30 requests a minute against 50+, and 1,000 objects where rite needs a
handful. The transport stops being constrained at all.

### ⚠ The load-bearing caveat: this step is ASSEMBLED, not quoted

**Slack never defines "internal customer-built application" in a single
sentence, anywhere I could find it.** Searched: the rate limits page, the
29 May changelog and its FAQ, the 3 June clarification, the developer policy,
the Marketplace guidelines, the Marketplace distribution guide, and the app
distribution page. The term is used repeatedly as an exempt category and
never bounded.

**So the claim "an app a user creates from rite's manifest and installs only
to their own workspace is internal customer-built" is assembled from three
facts, not read from one.**

1. Slack uses "**Custom apps**" interchangeably with the exempt category, in
   the same paragraph, giving both the same 50+/min and 1,000-object numbers.
2. The exemption is described as covering customers building custom
   applications **for internal use**.
3. An undistributed single-workspace app is, by Slack's own distribution
   page, a *different object* from a distributed one — it installs "without
   any code for handling authorization" and cannot reach another workspace.

Convergent, and I believe it. **But it is the one load-bearing point in this
note that is inferred rather than quoted, and it is the point a Slack
reviewer could disagree with.** If this is wrong, the manifest route provides
no rate-limit relief and the whole recommendation collapses to "the Slack
track cannot use polling". Anyone who can get Slack to say it in writing
should, and should replace this section with the quotation.

## 4. "Commercially distributed" is defined by PAYMENT — and rite escapes only by being free

⚠ **This section is a constraint on rite's future, not only on its Slack
integration. Somebody considering a paid tier should find it here.**

The API Terms of Service, quoted:

> "By "**Commercially Distribute**" we mean any situation where your
> Application integrates with Slack APIs and users could pay fees for your
> product, service, or features—whether it's a direct charge to access the
> App, a 'freemium' model where some features are gated, or a free App that
> connects to a paid product or service."

rite is MIT, free, gates nothing behind payment, and connects to no paid
service. On that definition it is not commercially distributed, and the
restriction does not bind.

⚠ **Slack explicitly anticipated the manifest workaround and closed it — for
commercial distribution.** Two quotations, and the second is the one that
names the pattern:

> "The restrictions in this section apply to all Applications that are
> Commercially Distributed, **regardless of whether you publish an
> off-the-shelf Application or you provide customers with a custom
> Application or Application template that connects to other products,
> services, or features.**"

> "the Slack Marketplace is the only appropriate channel for commercially
> distributing apps built with Slack APIs, whether those apps are "unlisted"
> (published outside of the Marketplace) or **provide a customer instructions
> for a templated custom app that connects to your product**."

**So the escape is NOT the manifest. The escape is that rite is free.** The
manifest is how the setup stays easy; it is not what makes it permitted.

**What would break it**, in Slack's own terms: a direct charge for rite, a
freemium tier with any gated feature, or rite connecting to a paid product or
service. Any of those makes the manifest route commercial distribution by the
text above, at which point the only compliant path is a Marketplace listing —
which section 2 of this note says rite would very likely not be granted. **A
paid rite and a Slack relay may not be able to coexist.** That is a product
decision, and it is Robert's.

### ⚠ The changelog is looser than the contract, and I relied on the contract

The 29 May changelog glosses the scope as:

> "This rate limit reduction will apply only to applications that are
> commercially distributed outside of the Marketplace (**also called
> "unlisted" apps**)."

Read literally, that parenthetical equates "commercially distributed" with
"unlisted" — which would sweep in *any* distributed non-Marketplace app
regardless of payment, and contradicts the Terms of Service definition above.

**I relied on the Terms of Service, and the reason is not that it gives the
answer I wanted.** The ToS is the contract rite would be held to, it defines
the term explicitly and narrowly, and the changelog is explanatory prose that
uses the phrase loosely in a parenthetical aside. A defined term in a
contract beats a gloss in an announcement.

⚠ **Recorded rather than smoothed over, because it is a real ambiguity and
the safe reading is the unfavourable one.** If Slack ever enforces on the
changelog's wording rather than the ToS's, a free rite distributing a
manifest could still be treated as commercially distributing. Nothing
observed says they do; nothing observed rules it out.

## What this settles, and what it does not

**Settled:** rite cannot ship one Slack app and keep the polling transport.
A3a's open question 1 is answered, and the answer **strengthens the transport
choice rather than changing it** — polling was chosen partly because the
distribution cost was shared with Socket Mode, and it turns out both
transports need every user to own their app for reasons that are now
documented rather than assumed.

**Not settled, and deliberately left so:**

- Whether Slack agrees that a manifest-created single-workspace app is
  "internal customer-built". See the assembled-caveat section.
- Whether rite will ever be commercial, which decides whether this route
  survives. Robert's.
- A3a's done-when — *"send a message in a Slack channel; a standalone script
  using the stored token prints it within 5s"* — remains unobserved. It needs
  a real workspace, an app and a bot token, none of which exist yet. **This
  note reduces the setup cost of making that observation; it does not make
  it.**
