# Where the credential work stopped — resume notes

**Stopped deliberately on 2026-09-12 for the soft-publish/squash window.**
Nothing below is half-built: every item is either fully landed or design-only.
There is no partially-applied schema change anywhere in the tree.

## Landed and pushed (`e1d13b7`)

- Per-project credential namespacing (SPEC §10.2–10.4)
- Service-validated `rite credential set <service>` with per-service field
  lists, split into secrets (keychain) and config (`config.yaml`) — SPEC §10.5
- Case-insensitive service names (`JIRA` works), typo suggestions (`jria` →
  `jira`)
- Worker fungibility: every worker gets every project credential (SPEC §5.3.4),
  with the old narrow-scoping claim removed from the spec
- Auth-failure audit: rejected / empty / unreachable distinguished on both
  backends, verified with real bad credentials

## NOT built — design complete, approval pending

### 1. Credential instances (pairs) — `.docs/CREDENTIAL_INSTANCES_DESIGN.md`

Robert's finding: `jira_email` and `jira_token` are independent entries with
nothing binding them, so two JIRA accounts cannot be held coherently and a
mismatched pair fails as an indistinguishable auth rejection.

Design is complete and was reported. Four conclusions, two of which came back
against expectation:

- **Storage:** one keychain entry per instance (`<ns>/jira/<instance>`, JSON),
  NOT one per field. Demonstrated: per-field rotation interrupted between
  writes leaves a mismatched pair on disk; one entry per instance cannot.
- **Fungibility does NOT simply hold.** Two JIRA instances both want
  `JIRA_API_TOKEN`. Recommendation: default instance keeps the plain env name,
  others suffixed (`JIRA_API_TOKEN__CLIENT_X`). Residual cost: a worker must be
  told which instance covers which module.
- **The mismatched-pair state is NOT detectable** for JIRA. Measured: real
  email + garbage token and garbage email + garbage token return byte-identical
  401s. rite must name the instance and identity it used rather than imply
  detection.
- **Migration is automatic.** Loose `jira_email` / `jira_token` fold into a
  `default` instance on first read; nobody retypes. This holds for a token set
  through the OLD command too — so setting it now costs nothing later.

### 2. `custom` category

Specified as (service name, username, value). Not built. Storage location
depends on the instance model above, which is why it waited.

### 3. Worker credential injection

`worker_environment()` exists and `rite sandbox start` delivers every project
credential. What is NOT built is per-instance env disambiguation (item 1).

## Guide edits queued for the next pass

Not applied — the guide is `.docs/`, and the squash window is not the moment.

- Step 5: drop "pending Robert's answer". Sprint 1 is settled:
  w1 = BEN-86→87→88→89→90 sequentially, w2 = BEN-12, w3 = BEN-10, w4 = BEN-85.
- **`BEN-85`'s LOW label stays and must not be read as an error.** LOW means
  non-preempting, not forbidden: it constrains ordering, not eligibility. It is
  worked because nothing higher-priority was free for that worker. If a
  higher-priority ticket appears mid-sprint, **w4 moving to it is correct and
  expected**, not a disruption, and whoever picks it up must not infer the label
  was wrong.
- **`BEN-11` must leave the queue before w3 starts** — byte-identical duplicate
  of `BEN-10`. Leaving it in means the work is done twice, which is the exact
  cost collapsing w1's five tickets was meant to avoid.
- Re-verify the guide against the post-squash tree, since `ruff format` will
  have touched ~50 files.

## Recurring hazard worth fixing later

`.rite/config.yaml` has now been committed by accident **twice**, both times by
the same mechanism: `_ensure_namespace` writes it when a `rite credential`
command runs inside this checkout, and a later `git add -A` sweeps it up. Its
contents give it away each time (a namespace derived from a scratch directory).
Worth deciding whether rite's own repo should carry a real one — a real
decision with a live JIRA project behind it — rather than continuing to delete
an accidental one.
