# Changelog

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
