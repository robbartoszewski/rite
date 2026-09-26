# v0.8.0 — release plan: feature-complete multi-machine

**Status: PLAN, filed 2026-09-26. Nothing in it is built.** It was split out
of `V070_RELEASE_PLAN.md` when Robert settled the scope of the next two
releases, so the file name stops claiming a release its contents are not in.
Every track below was moved here **verbatim**. The only edits are sentences
that named the release an item was in.

**Citation convention.** A bare `§` is a section of `SPEC.md`. This plan's
own parts are called "track MX" and so on, never `§`, because the citation
gate's regex is context-free. **"Part 0" in the tracks below means part 0 of
`V070_RELEASE_PLAN.md`**, where the boundary measurements live: they were
made for both plans and stay beside the tracks that still need them.

## Scope, as Robert set it on 2026-09-26

- **v0.6.0** ships now: Slack as a control channel, check-ins, several
  Managers on one machine in one root, the Manager sandbox on macOS and
  Linux, credentials, text handling (the v0.6.0 plan's part N), a local
  Goose Manager, and Linux support.
- **v0.7.0, "feature-complete single-machine"**: `V070_RELEASE_PLAN.md`.
- **v0.8.0, "feature-complete multi-machine"**, this plan: the
  **Remote-Worker**, **multi-machine hardening**, the **credential broker**,
  **egress confinement**, and the **`push_to_shared`** publishing strategy.

The line between the two: **v0.7.0 finishes what one machine needs, and
v0.8.0 is what only arises across machines**, plus egress, which Robert
placed here by name.

## What is filed here, and why

| item | from | why v0.8.0 |
|---|---|---|
| **MX1**, the Remote-Worker | track MX below | named by Robert |
| **MX2**, MX-P1 to MX-P4 measured across two machines | track MX below | named by Robert ("multi-machine hardening") |
| **CB1**, the credential broker | `V060_MANAGER_CREDENTIALS.md`, "The v0.7.0 end state: a credential broker" | named by Robert. Also what MX-P3 needs: which credential lives on which machine |
| **EG0–EG6**, and **EGQ1–EGQ5** | track EG below; SPEC §5.5 | named by Robert ("egress confinement") |
| **SB2**, `(allow network*)` in the Manager's profile | `V070_RELEASE_PLAN.md` track SB | its row already says EG3 closes it, so it moves with EG3. The row stays in track SB and says so |
| **PB2**, the `push_to_shared` strategy and the three `shared_repo` rules | `V070_RELEASE_PLAN.md` track PB (PB1 as first written) | named by Robert. Track PB also records it as "a collaboration mechanism for several Managers", and MX1 names it as its transport |
| **MM4** and **MMQ1**, per-instance configuration | `V070_RELEASE_PLAN.md` track MM | ⚠ **a judgement, not named by Robert.** Its done-when is "changes one machine's Manager and appears in no `git status`": configuration that differs by machine, which one machine does not need. EGQ3 depends on it, and EGQ3 is here. Robert may place it otherwise |
| **LS1**, Slack gated on the Owner lease | SPEC §9.16.7 ("Gating Slack on the lease is v0.7.0"); `cli/main.py` | the lease elects an Owner across machines. In one root the Owner is the `route` holder, which is built. The SPEC sentence is corrected to v0.8.0 with this plan |

**Dependencies that cross into v0.7.0:** EG0 measures Cursor's destinations
through CU1, and PB2 extends PB1's `publish:` design, which is v0.7.0's.

**Not filed here, and why** (listed in `V070_RELEASE_PLAN.md`, "Fits
neither release"): memory, the relay escalation chain and self-reflection
(`V080_RELAY_CHANNELS.md`, whose name still says 0.8.0), and a Linux
**Worker** sandbox. MX1 depends on the last one, so it has to exist by
v0.8.0 at the latest, but it is not multi-machine work in itself.

---

## Track MX — Multi-machine: deferred from v0.6.0, and the Remote-Worker

**Robert, 2026-09-26:** "Multi-machine hardening may be deferred to v0.7.0."
(He wrote "v0.7.9"; confirmed a typo for v0.7.0.)

This is not new scope leaving v0.6.0. Multi-Manager was already narrowed to
**one machine, one project root** for v0.6.0, and the full multi-machine
spec was cut. This pushed that deferred part into v0.7.0, and the
re-filing of 2026-09-26 (top of this plan) moved it on to v0.8.0. v0.6.0's release
notes say "one machine, one project root" in those words.

### What "multi-machine hardening" covers: the properties deferred

So a later reader knows what was deferred rather than inferring it:
- **MX-P1, separation between Managers on different machines.** P2 (one
  Manager cannot signal or drive another) is established only within one
  root on one machine: the sandbox's `(target same-sandbox)` signal rule
  and the denied tmux socket are per-machine mechanisms. Across machines
  nothing equivalent has been stated, let alone measured.
- **MX-P2, the mailbox and routing assume a shared filesystem.** Inboxes
  and outboxes are files under the project root. `rite route` is carried
  out by the Owner's supervisor writing the secondary's inbox on the same
  disk (`managers/routing.py`, "who may write a Manager's inbox"). Each
  reader's cursor and the Slack relay read those same files. None of that
  exists across machines.
- **MX-P3, the credential store is per machine**
  (`~/.config/rite/credential-store.json`). It is a genuine multi-machine
  property, not an inconvenience: it caused two misdiagnoses on
  2026-09-26. The App key existed only on the Mac, and the VM's
  `claude_token` had to be set again. Which credentials exist on which
  machine must be visible, and how they are distributed is a design
  question (the credential broker, track CB below; it was "the v0.7.0
credential broker" until the re-filing).
- **MX-P4, the 0.4.0/0.5.0 coordination layer** (Owner lease and CAS state
  over a git remote) is implemented and exercised by the suite, and has
  never run on two physical machines (README).
- Also per machine, and so part of the same work: the sandbox backend
  (seatbelt vs Landlock), the tmux server, and the per-user instance
  records.

### MX1: the Remote-Worker (Robert's proposal)

**His proposal:** a new entity, the **Remote-Worker**. It is
provider-agnostic, and the User starts it on the secondary machine. It
listens for instructions from the Manager it is paired with, and needs
pairing configuration.

**The shape:** one Manager on the main machine and one Worker per secondary
machine, rather than Managers spread across machines. It is cheaper than
multi-machine multi-Manager because **a Worker holds no authority**: it
does not poll Slack, does not route and does not decide, so MX-P2's mailbox
and routing assumptions do not apply to it. It also keeps prompt caches
warm: each Worker stays pinned to its own machine's Ollama, where one
Manager fanning out across several Ollama instances would re-process its
context on a cold instance every request.

🔴 **A design property to preserve, not an implementation detail: the
remote machine LISTENS; the Manager never reaches out.** The Manager
therefore needs no credentials, no SSH access and no remote-execution
capability against other machines.

**Open question 1, transport. Recommendation: poll the shared repository**
(the `push_to_shared` repository of Track PB), with results returning the
same way.
- It adds no listening port, no TLS and no new authenticated network
  surface. A socket or HTTP service would mean an authenticated network
  service on EVERY secondary machine, which is a new attack surface
  multiplied by the fleet, for a Worker whose whole appeal is that it holds
  no authority.
- rite already has this mechanism: the git compare-and-swap state layer
  (`coordination/git_backend.py`, "state is one commit on the `state`
  branch, rewritten with `--force-with-lease`; messages are ordinary
  commits") carries state between machines over a git remote with no
  service. Reuse it; do not build a second transport.
- Costs, stated: latency is the poll interval, the repository grows with
  message commits, and each machine needs push access to the shared
  repository (MX-P3 again).

**Open question 2, pairing. 🔴 An authority problem, not a configuration
problem.** A Remote-Worker executes what its Manager sends, so
impersonating the Manager gives code execution on every secondary machine.
Solve it the way the inbox was solved: structurally, never by trusting
content. The Owner does not write a secondary's inbox; it asks, and "who is
asking comes from the supervisor … never from the request"
(`managers/routing.py`).
- **Recommended shape:** at pairing, the User copies the Manager machine's
  PUBLIC key to the Remote-Worker's machine (a public key is not a secret,
  so MX-P3's distribution problem shrinks to copying something harmless).
  The Manager's supervisor signs each instruction commit, outside the
  sandbox, as it already mints tokens and launches Workers. The
  Remote-Worker refuses any instruction not signed by the paired key, and
  refuses replays (a sequence number inside the signed content).
- **Belt and braces:** scope write access to the instruction ref so only
  the Manager machine's credential can push it.
- **What NOT to do:** a shared secret in a config file. It would be copied
  onto every machine through a per-machine store, and anyone holding it
  could impersonate the Manager to every Worker.

**Open question 3, 🔴 sandboxing is a PREREQUISITE, not a follow-up.**
Today there is no Linux Worker sandbox by default (v0.6.0 readiness D18).
A Remote-Worker on Linux would be an unsandboxed process executing
instructions received over the network. **It must not ship in that
order.** Note that the obvious Linux sandbox, Docker, needs membership of
the `docker` group, which is root-equivalent on the host (the reasoning
that closed v0.6.0's D2), so a Linux Worker sandbox is itself an open
design problem, not a checkbox.

**Linked to Track PB:** `strategy: commit` cannot work for a Remote-Worker,
because its commits would be stranded on the wrong machine. **So
`push_to_shared` is load-bearing for MX1, not merely compatible with it**,
and the same shared repository is the transport recommended above.

**Naming: recommend its own verb, not `rite start X`.** `rite start X`
starts a MANAGER by name from `coordination.manager_roles`, and everything
behind it is Manager machinery: the Claude login, the Manager sandbox
profile, Slack, the supervisor loop and routing. Overloading it would make
the name decide whether a long-running process acts with a Manager's
authority or a Worker's none, and a typo or a name clash could start the
wrong kind. Something like `rite worker listen <name>` (the CLI already
groups by noun, as in `rite manager stop` and `rite sandbox start`) keeps
"this process holds no authority" visible in the command.

| # | work | done when | depends | size |
|---|---|---|---|---|
| MX1 | **The Remote-Worker**, as above | A Remote-Worker on a second physical machine, sandboxed, paired by public key, takes an instruction through the shared repository and returns a result the same way. An instruction signed by any other key is refused, and so is a replayed one. The Manager machine holds no credential for the Worker machine | a Linux Worker sandbox; Track PB's `push_to_shared` | design first; unsized |
| MX2 | **MX-P1 to MX-P4 stated and measured across two physical machines** | each property observed holding or recorded as not holding on two machines over a real network | — | design first; unsized |

---

## Track CB — the credential broker

**Recorded, not designed.** The end state is written in
`V060_MANAGER_CREDENTIALS.md` ("The v0.7.0 end state: a credential broker",
titled before this re-filing): the Manager **asks**, and something outside
the boundary performs the credential's operation or hands out a
per-request token, so the Manager never holds a credential. The Worker
broker (B9) is the precedent. Several open items name it as their fix:

- **SB9** (`V070_RELEASE_PLAN.md`): one of its three closing routes is
  "the credential broker, so that no token is held in the sandbox at all".
  SB9 stays in v0.7.0 on Robert's word ("fix it in v0.7.0"), so v0.7.0 has
  to close it by one of the other two routes, or it waits for this ticket.
- **Jira for a sandboxed Manager** (`V060_MANAGER_CREDENTIALS.md` option 3,
  readiness Q3): "the board call happens outside the sandbox".
- **MX-P3**: which credential exists on which machine.

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| CB1 | **The credential broker**, as recorded in `V060_MANAGER_CREDENTIALS.md` | not yet written: the note records an end state, not a done-when | — | design first; unsized |

## Track PB2 — `push_to_shared`

**The design is `V070_RELEASE_PLAN.md` track PB**, settled by Robert as one
`publish:` configuration, and it stays there whole. Only the fourth strategy
value is v0.8.0. Its clauses are taken verbatim from PB1 as first written:

| # | work | done when | depends | size |
|---|---|---|---|---|
| PB2 | **`push_to_shared`**: the fourth `strategy` value; `shared_repo` per module under the three rules (track PB, "Three rules on `shared_repo`") | `push_to_shared`: observed refusing with no `shared_repo`, and refusing when it is the module's `origin` in another spelling | PB1 (v0.7.0) | unsized |

⚠ **Open, and not decided here:** what a v0.7.0 build does with
`strategy: push_to_shared` in a config before PB2 lands. Refusing it by name,
with the release it arrives in, is the shape rite uses elsewhere
(`command_channel`, D-95), but that is Robert's call or PB1's builder's.

## Track MM, continued — per-instance configuration

Moved verbatim from `V070_RELEASE_PLAN.md` track MM.

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| MM4 | **A home for per-instance configuration.** Decided: gitignored. Location: see MMQ1 | Per MMQ1's answer: a key set in the instance file changes one machine's Manager and appears in no `git status` | MMQ1 | 1 sitting |

**MMQ1. Where does per-instance configuration live?** Decided that it is
gitignored; not decided where. Nothing like it exists today: `.rite/user/`
holds runtime records, not configuration a person writes.

| option | for | against |
|---|---|---|
| (a) `.rite/managers/<name>/config.yaml` | beside the Manager's own state, so the §5.4.8 boundary covers it | a person edits a file under a runtime directory |
| (b) `.rite/user/<name>.yaml` | beside the instance record it configures | `.rite/user/` has twice held a file that was not an instance record (C11, and the permission settings). A third kind of file there is the collision class again |
| (c) a `.rite/config.local.yaml` overlay, keyed by Manager | one file to edit, the familiar `*.local` pattern | an overlay invites any committed key to be overridden per machine, which reverses "profiles are shared" by the back door unless the overlay's keys are enumerated |

What turns on it: whether the per-instance keys are **enumerated** (profile
keys refused in the instance file) or merely layered. Layering is simpler.
Enumeration is what keeps "a team agrees what a `planner` is" true.

---

## Track EG — Egress control

**The spec is `SPEC.md` §5.5**, decided by Robert on 2026-09-25 in the brief
for this plan (D-99, D-100). It states the property positively, puts
enforcement at the network layer, and confines content scanning to allowed
destinations that publish. This track is how to get there, and what is still
open.

### What the enforcement points can and cannot do

| where | mechanism | what it can express | source |
|---|---|---|---|
| **Worker, docker backend** | yoloAI `--network-isolated` plus `--network-allow <domain>` | a domain allowlist, **tamper-resistant**: the rules are installed from a helper container and the sandbox has no `NET_ADMIN` | read, `yoloai help security` |
| Worker, apple/podman/containerd | same flags | a guardrail only: the sandbox holds `NET_ADMIN` and can flush the rules | read, same |
| **Worker, seatbelt (rite's default `sandbox.backend`)** | none | `--network-isolated` is **refused** | read, same. D-30 |
| every backend | `--network-none` | nothing leaves | read, same |
| **Manager** (seatbelt, on the host, B9) | its profile | **IP: everything, or loopback only.** A named host is rejected at load. **Local sockets: allowed or refused by path**, as the tmux socket is refused on `main` | **measured**, part 0.3 and 0.4 |

⚠ **Two consequences a reader must not miss:**

1. **A Worker on the default backend cannot be egress-controlled at all.**
   yoloAI refuses the flag rather than pretending, which is the right shape.
   So the spec's property holds for Workers only where the backend can enforce
   it, and rite must say which case a project is in (EG2).
2. **yoloAI's list is IPv4 only** ("IPv6 IS NOT FILTERED"), and a domain list
   enforced by iptables is enforced on the **addresses the domain resolved
   to**. A CDN address shared with other tenants admits them too. Both belong
   in what rite prints, not in a footnote.

### The Manager: loopback confinement plus a proxy outside the boundary

Part 0.4 rules out a list of **hosts** inside the profile. Part 0.3 shows the
profile *can* confine the Manager's IP traffic to loopback. The shape that
follows is a **forward proxy, run by the supervisor outside the boundary**,
listening on loopback. The Manager's profile allows only loopback for IP, and
the proxy allows only the sanctioned hosts. A client that ignores
`HTTPS_PROXY` cannot reach anything, which **fails closed**.

**The work divides by transport, and part 0.4 is why.** The profile can
already name local sockets by path, so **local-socket destinations are
decided in the profile** and never reach the proxy: the tmux socket is denied
there today. Others (the name resolver's socket, anything under
`/private/var/run`) can be allowed or refused the same way. The keychain is
not one of them: it is reached through `mach-lookup`, a different rule (SB5).
**Only IP traffic needs the proxy.** ⚠ Not measured: which local sockets a
Manager's engines and tools need once IP is loopback-only. Name resolution
is the obvious one. A client that goes through a CONNECT proxy should not
need to resolve names itself, but that is unverified for every client in
question.

⚠ **Loopback is not one destination.** `localhost:*` admits every listener
on the machine: the local model endpoint (which the local tier needs), but
also any database, dev server or admin port the operator runs. **And, with
several Managers, each other's proxies.** If each Manager's proxy enforces its
own list, Manager A could send through B's proxy and get B's list.
**Measured 2026-09-25: a profile can allow one loopback port and refuse the
rest.** With `(deny network-outbound)` and
`(allow network-outbound (remote ip "localhost:18765"))`, port 18765 answered
200 and port 18766 was refused (curl exit 7). So each Manager's profile can
admit only its own proxy's port, which closes the cross-proxy route and also
shuts out the operator's other listeners. The local model endpoint then has
to be admitted by its port, or go through the proxy.

This shape is **inferred from measurements, and is not a decision.** It is
option (a) of EGQ1. ⚠ The first version of this plan preferred it partly
because the loopback line would also close the tmux escape. That reason is
gone: `9862b59` closed the escape by denying the socket's path and left
`(allow network*)` alone (part 0.3). EG3 now stands on egress alone.

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| EG0 | **Measure first: which destinations do real runs reach?** `V070_EGRESS.md` Q5. The v0.5.1 and v0.6.0 acceptance runs, the benchmark, a Slack-connected run, and CU1 for Cursor. Derive the default list from what was observed, as C4's allowlist was derived from 14,981 recorded invocations | A committed data file of observed destinations per engine and role, and a test that requires every default entry to trace to it | — | 1–2 sittings |
| EG1 | **The list is rite's vocabulary** in `config.yaml`: destinations as hosts (plus ports where needed), grouped by what they are for. No proxy or yoloAI syntax (`V070_EGRESS.md` Q2) | `rite doctor` renders the list and validates it with rite's own error wording. A proxy- or yoloAI-shaped key is refused | EGQ3 | 1 sitting |
| EG2 | **Workers: enforce where the backend can, and say where it cannot.** Docker: pass the list as `--network-allow`. Seatbelt: rite states at start that Worker egress is **not controlled** on this backend, every run. It does not say "restricted" | A docker Worker is refused a destination off the list, and the refusal names it; a seatbelt project's start line says egress is uncontrolled | EG1, EGQ2 | 1–2 sittings |
| EG3 | **Manager enforcement** per EGQ1. If (a): profile to loopback, proxy in the supervisor, `HTTPS_PROXY` in the engine's environment. Local sockets decided in the profile by path (part 0.4) | A real Claude Manager and a real Goose Manager each complete a cycle, including a ticket read and a `git push` to the sanctioned remote. A request to an unlisted host is refused **and reported** (EG4). With two Managers running, each one's profile admits only its own proxy's loopback port: A's request to B's proxy port is refused. `tmux` from inside is refused | EG0, EG1, EGQ1 | 3–4 sittings |
| EG4 | **A refusal names the destination and the line that permits it**, the C21 shape (`V070_EGRESS.md` Q3). A client that fails on a refused CONNECT reports a network error that looks like an outage, so the report must come from rite's side, read from the proxy's log, not from the client's message | The refused host appears in the pane-side refusal line **and** in the next check-in digest, with the config line that would allow it | EG3 | 1 sitting |
| EG5 | **Content scanning, on allowed destinations that publish, only** (D-100). The structural credential rule of `redact_secrets`/C7, not a list of token formats. **Model calls never scanned** | A token pasted into a `gh issue create` body on the sanctioned repo is caught and reported. An ordinary model request is not scanned, which the proxy's own counters show | EG3, EGQ5 | 2 sittings |
| EG6 | **Demonstrable for the local tier.** With a local engine and an internal-only list, `rite doctor` shows the list and a live refusal of an outside host | On a local-tier project, doctor's output contains the policy and a refused probe to a public host | EG3 | ½ sitting |

### Open questions — Robert's

**EGQ1. How is the Manager's egress enforced?**

| option | what it is | turns on |
|---|---|---|
| (a) loopback profile + rite's proxy in the supervisor | inferred shape above | rite owns a security-relevant proxy: CONNECT only, no TLS interception, host allowlist. Needs every client used by a Manager to honour `HTTPS_PROXY`, which is **not measured** for `claude`, `goose`, `git`, `gh` or `uv` |
| (b) loopback profile + a third-party proxy rite configures | as (a), a dependency instead of code | an external binary's vocabulary must stay out of `config.yaml` (B0a's rule), and it becomes a pinned dependency |
| (c) a host firewall (`pf`) | system-level rules | ⚠ changes a system security setting and needs admin rights. It is also machine-wide, not per-Manager |
| (d) Manager unconfined; Workers only | ship EG2 alone | the injection case in §6.6.3 lands on the Manager, which reads every ticket, so the property would not hold where it matters most |

**EGQ2. What does a seatbelt-backed project get?** (a) Egress is Worker-
uncontrolled and said so on every start (EG2 as written); (b) make docker the
default backend when an egress list is configured; (c) refuse to start Workers
when a list is configured and the backend cannot enforce it. (c) follows
§5.1.1's fail-closed rule most closely, and it breaks every existing seatbelt
project that adds a list.

**EGQ3. Who edits the list, and is it profile or instance?** Committed
(a team agrees where code may go) or per-instance (MMQ1). The destinations are
a team policy, which argues for committed. A single developer's local endpoint
is per machine.

**EGQ4. Redirects and DNS** (`V070_EGRESS.md` Q4). Under a CONNECT proxy, a
redirect to another host is a new CONNECT and is judged on its own, so it falls
out. Under yoloAI's iptables, the rule is on resolved addresses. Decide whether
rite documents that difference or narrows Workers to docker-with-proxy too.

**EGQ5. Which allowed destinations "publish"?** The line between "allowed" and
"allowed but public" is where scanning applies. `gh issue create` on a public
repository publishes; on a private one it arguably does not. Options: (a) mark
destinations `public: true` by hand; (b) treat every non-model destination as
publishing; (c) ask the host (the repo's visibility) at scan time. (b) is
simplest, and the false positives land on legitimate pushes.

---

## Track LS — Slack and the Owner lease

**Recorded, not designed.** SPEC §9.16.7: in one root the Owner is the one
Manager holding `route`, and only it opens a Slack relay (built, 0.6.0).
"With a `remote`, none of this applies": the election's lease decides the
Owner, and until Slack is gated on that lease, a project with a remote
behaves as before §9.16.7, so every Manager opens a relay.

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| LS1 | **Gate the Slack relay on the Owner lease** when `coordination.remote` is set (SPEC §9.16.7; `cli/main.py`, `_slack_listener`) | not yet written | MX2 (the lease has never run on two physical machines, MX-P4) | unsized |

---

## Decisions needed for v0.8.0

| id | question | blocks |
|---|---|---|
| MMQ1 | where per-instance configuration lives (gitignored is decided) | MM4, EGQ3 |
| EGQ1 | how the Manager's egress is enforced | EG3 (and SB2) |
| EGQ2 | what a seatbelt-backed project gets when an egress list is configured | EG2 |
| EGQ3 | the egress list: committed, or per-instance | EG1 |
| EGQ4 | redirects and DNS under yoloAI's iptables allowlist | EG2's documentation |
| EGQ5 | which allowed destinations count as publishing | EG5 |
| MX1 Q1–Q3 | transport, pairing, and the sandbox prerequisite (track MX) | MX1 |

## Sequencing

**Measurements first:** EG0 and MX2 need no decision. **Egress cannot ship
without EG4** (track EG's own rule: a silent network refusal is the
stall-without-a-message class). **MX1 cannot ship before a Linux Worker
sandbox exists** (track MX, open question 3), and that is not filed in
either release yet.
