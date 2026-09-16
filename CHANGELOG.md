# Changelog

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
