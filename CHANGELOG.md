# Changelog

## 0.4.0 (unreleased)

### Enhancements

- `rite update --files-only` brings an already-initialised project's generated
  files up to this version: both `CLAUDE.md` files, the `.claude/` commands and
  agents, the review checklist, the CI workflow and its install pin, and rite's
  `.gitignore` lines. `--dry-run` shows every change and diff first, and
  `--take-rite "<section or file>"` takes rite's version of one thing you were
  asked about. Nothing under `.rite/` is written, ever: a project's
  architecture, plan and decisions are its own.
- Generated sections now carry a marker recording what rite wrote, so "did you
  edit this?" is answerable per section rather than per file. An edited section
  is kept byte-for-byte and reported; an untouched one is refreshed.
- Projects created before markers existed are covered too. A section some
  release wrote identically for every project is matched by hash; a section
  that is rite's guidance around a line of the project's own details is matched
  against what that release wrote with those runs left open. This is what
  carries 0.3.0's worker-assignment correction into a project built by 0.1.0,
  0.2.0 or 0.3.0.
- `rite start` says when a project's instructions are behind the installed
  rite, and `rite doctor` counts what is out of date and what you changed.
- `rite update` refreshes the project with the rite it has just installed,
  rather than with the version it replaced — so one upgrade is one step.
- A command or agent a past release shipped and this one no longer does is
  named by the refresh rather than left to sit in `.claude/` unmaintained. It is
  never deleted, and a command your own team wrote is never named.
- `/spec-digest` gives a Worker the one part of the spec its ticket is about,
  with what has drifted since that part was written, instead of the whole
  document. The spec is parsed into addressable units, the references between
  them are graphed, and a digest that no longer matches the spec refuses to
  call itself current.
- A Worker can record that the spec slice it was given was not enough, and say
  which part it needed.
- `rite doctor` says which build of rite this is, including the tag and commit
  it was installed from.

### Bug fixes

- Fixed the file Workers actually read still telling them they own modules —
  0.3.0 corrected the Owner's copy only, and there was no way to deliver either
  correction to an existing project. Both now arrive with `rite update`.
- Fixed a machine without gitleaks getting no publish-gate scan at all. The
  gate still refuses to vouch for a tree it could not fully scan, but rite's
  own rules — the built-in hardcoded-path rules, your declared patterns and
  the kb/ cross-reference — now run and report what they find. A broken
  `.rite/gitleaksignore` no longer hides every finding either.
- Fixed one Worker's malformed `worker.yml` stopping the whole refresh: the
  project and every other Worker received nothing, under a message that
  blamed `.rite/` config. A Worker whose manifest will not parse now still
  receives its copied commands and agents.
- Fixed a stray code fence in a spec quietly deleting the sections after it.
- Fixed `rite init` on an already-initialised directory saying only that it
  refused: it now says what else the directory can be given.
- Fixed a spec digest calling itself current after the spec moved on.
- Fixed unknown keys in `brief.yaml`, `config.yaml` and `worker.yml` being
  accepted in silence.
- Fixed `install.sh` reporting the rite already on PATH rather than the one it
  had just installed.
- Fixed a worker nobody has started being reported as stalled.
- Fixed `rite doctor` not saying when `ANTHROPIC_API_KEY` is exported, and not
  counting a missing gitleaks as a problem.

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
