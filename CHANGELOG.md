# Changelog

## 0.5.0 (unreleased)

### How this release was verified

**Two platforms, and neither covers the other.**

- **Linux, CPython 3.11–3.13**, by CI on every commit (`ubuntu-latest`).
- **macOS 15 (darwin 25.2.0), CPython 3.14.3**, by a local run.

CI is Linux-only, so macOS rests entirely on the local run; the local run is
one Python, so 3.11–3.13 rest entirely on CI. Read either alone and you will
overstate the coverage.

⚠ **At the time of writing, CI is RED on Linux** and has been for several
commits — `tests/test_spec_5_3_4_is_what_the_cli_says.py` fails there because
`rite init` does not enable sandboxing on a runner (yoloAI's backends are
macOS-only), so the credential list a test expects is empty. **Do not tag
until the Linux run for the tagged commit is green.** That is a checkable
instruction rather than a reminder to be careful.

The accurate sentence for the local half is "3365 passed on macOS/py3.14",
not "the suite is green" — the second implies a matrix, and here the matrix
exists and disagrees.

**Why the two-platform split earns its place on this codebase.** Three
defects this week were invisible on macOS and caught only by the Linux run:
the coordination state layer classified a lost ref race as a permanent
refusal, because Linux git and macOS git report the same condition with
different strings; two doctor tests asserted exit 0 while their fixtures left
sandboxing enabled; and the credential-list failure above. All three are the
same shape — a value whose vocabulary you only know from the platform in
front of you.

**How this boundary was found, which is the part worth keeping.** It was not
designed. An earlier draft of this section asserted there was no CI at all —
the Actions allowance was exhausted for the private repositories in the same
workspace, and nobody checked that this one is public and therefore
unaffected. Four commits went onto a red `main` while a local macOS run was
reported as verification, because a premise nobody checked reads exactly like
a fact. The two-platform split above is what was found by getting it wrong.

A release that documents how its verification boundary was discovered is more
useful than one stating it as though it had always been known: the second
invites you to trust the boundary, the first tells you what kind of mistake
produced it and therefore what kind to look for next.

**Least confirmed, in order:** the tmux loop lifecycle (`rite loop
start/status/stop`), whose tests use the real binary and therefore *skip*
where tmux is absent, so on a runner without tmux they silently do not run;
claim exclusion, which rests on `flock` and varies by filesystem; and sandbox
capacity counting, which parses `yoloai ls --json` from a macOS-first tool.
Most of the rest is filesystem and JSON handling with no platform surface —
**which is a reason to expect it holds rather than evidence that it does.**

## 0.4.0 (2026-09-18)

### Enhancements

- **Several machines can run one project (Phase 2).** They elect a single
  Owner through the coordination repository, and the role moves on its own
  when a machine stops: the Owner holds a lease it has to renew, the next
  Manager in priority order promotes once that lease expires, and a returning
  higher-priority Manager asks for the role back rather than seizing it, so
  work is never interrupted mid-operation.
- **The state layer is substitutable, and that is demonstrated rather than
  claimed.** Compare-and-swap is per key against an opaque version, with no
  git vocabulary in the interface — no oids, refs, or fetch-then-push — so
  another store can take git's place. The same conformance suite runs
  unchanged against three backends: the git remote, a local filesystem, and a
  socket-served key-value store with no trees, refs or merges, standing in for
  Redis. Git remains the default because it needs nothing a team does not
  already have; nothing in the design requires it.
- **Claims are safe across machines.** `rite claim` checks what other machines
  hold before granting a path and publishes what it granted, so two Workers on
  different machines cannot edit the same file. Releasing publishes too, so a
  finished path stops blocking the fleet immediately rather than when
  something else happens to notice.
- **A stalled machine no longer strands its work.** When a Manager's heartbeat
  lapses, the Owner hands its tickets back and expires the claims it held —
  once, with an audit record on the coordination log.
- **`rite doctor` shows the fleet:** who holds the Owner role and until when,
  every Manager as alive, stalled or unknown, a handover that has been asked
  for and not happened, and the last few coordination events. It also reports
  coordination settings that cannot work — managers with no remote, a
  duplicate name that makes priority ambiguous, a lease already expired when
  written — rather than leaving a half-configured block to do nothing quietly.
- **`rite status` says what this machine's last coordination pass concluded**,
  stamped with its age, read from local state without touching the network.
- **A spec too large to hold can be read a part at a time.** `rite spec index`
  turns a registered spec into addressable units — numbered sections, slug
  paths, and each row of a decision register — and says whether splitting it is
  worth anything before a token is spent: on rite's own 4,000-line spec a
  Worker loads 9% of it at p90, and a short or densely interlinked spec is
  REFUSED, because reading it whole costs less. `rite spec slice <unit>` prints
  what a ticket needs: the unit, what it cites, and the sections everything
  depends on. `/spec-digest` writes a reviewed unit per section under
  `.rite/spec/`, `rite spec show` hands one to a Worker, and `rite spec verify`
  is the gate that refuses to call a digest current when the spec has moved
  under it.
- **A slice that was not enough is counted, because otherwise it is invisible.**
  A Worker that had to read the whole spec anyway records it with
  `rite handover write --spec-fallback <unit>`, and `rite spec status` reports
  fallbacks against retrievals. No data is reported as no data, never as 0%: a
  feature nobody used and a feature that always worked are opposite readings.
  The verdict is also qualified when most units cite nothing — measured across
  nine specs written without a citation gate, slices come out SMALLER there,
  and that is an empty reference graph rather than a good decomposition.

### Notes for existing projects

- **Nothing changes for a single-machine project.** Coordination stays off
  until `coordination.managers` and `coordination.remote` are set and this
  machine names itself in `.rite/machine`. Without that it does not publish,
  elect, or touch another machine's work.
- The coordination cache under `.rite/` collects its own garbage and stays
  bounded. A self-hosted coordination remote holds roughly a fortnight of
  unreachable objects before git's own housekeeping clears them.

## 0.3.0 (2026-09-16)

### Enhancements

- `/spec` writes the project's `SPEC.md` from its brief: it asks the few
  follow-ups the brief warrants, writes Problem, Scope and Non-goals,
  Architecture, Decisions and Open questions, and registers what it wrote. The
  decision register is mandatory, so a ticket saying "implement per D-3"
  resolves to something.
- `rite start` ends by saying what phase the project is in and what the next
  step is, and every generated `CLAUDE.md` carries the phase table itself
  rather than pointing at one it does not contain.
- `rite spec add` and `rite spec remove` rewrite the spec section of the
  `CLAUDE.md` files rite generated, so a spec added after `rite init` reaches
  the Owner and the Workers created before it.
- Follow-up answers go into the spec rather than an `enriched:` block in
  `brief.yaml` that nothing ever read; `rite doctor` reports one it finds so
  earlier projects can move theirs.

### Bug fixes

- Fixed `rite doctor` crashing when yoloAI is installed but will not run. No
  check can end the command in a traceback now: each one is reported as a
  problem instead.
- Fixed `rite init` not asking whether this machine is the Owner or a Manager
  when the project already has a spec or source code.
- Fixed the assignment guidance an Owner session reads: Workers are
  interchangeable and map to a workspace, not to a module.
- Fixed a missing `gh` being reported neutrally when Workers are sandboxed and
  every push from a sandbox depends on it.

## 0.2.0 (2026-09-14)

### Enhancements

- Added a new `rite init` path for projects with an existing spec or source
  code: it asks about both first, and offers the branch, project kind,
  description and frameworks it detects.
- Added per-module commands in `modules.yaml`, and detection for Swift and
  Xcode projects and Node's package manager.
- Sandboxed Workers now take part in coordination — they claim, send
  heartbeats and push their branch — and receive their ticket when started
  with `rite sandbox start --ticket`. Sandboxing is on by default where a
  verified backend exists, and `rite doctor` checks it by running a sandbox.
- Workers are pointed at the project's existing spec (`rite spec add`,
  `rite spec remove`).
- Added `rite credential set claude` to store the Claude login a sandboxed
  Worker uses, and `rite board show` to read a single ticket.

### Bug fixes

- Fixed project resolution when a module is itself a rite project; a bare
  `.rite/` directory no longer counts as a project.
- Fixed credentials appearing in `rite sandbox pane`.
- Fixed JIRA access from sandboxed Workers.
- Fixed `sandbox.enabled` changing how Worker credentials are scoped.
- Fixed README and guide claims that didn't match shipped behaviour.

## 0.1.0 (2026-09-13)

- First public release.
