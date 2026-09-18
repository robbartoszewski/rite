# Changelog

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
