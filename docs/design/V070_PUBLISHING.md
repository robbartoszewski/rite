# PB1: publishing strategies, the build design

Status: **accepted by Robert 2026-09-29 (Q1–Q5 in §8), being built in five PRs.**
Written 2026-09-29 against `origin/main` @ `6c97075`.
It implements the settled design in `V070_RELEASE_PLAN.md` § Track PB and does
not reopen it. This note covers what the plan leaves open: where publishing
runs, when config is read, what a mid-run change does, and how a refusal
reaches the Manager.

## 0. What the code has today (the ground this stands on)

- **rite has no publish path.** The Worker pushes, opens the PR and merges on
  its own, because its `CLAUDE.md` tells it to (`workspace/manage.py:806-826`,
  `templates/commands/ticket.md:117-130`, `cli/init/claude_gen.py:509-515`).
- **SPEC §5.1.1 forbids what PB1 requires.** It says rite never pushes, `gh`
  never touches `pr`, and every mutating git verb runs only in
  `workers/<w>/`. `tests/test_blast_radius.py` asserts that. PB1 needs a
  **spec amendment**; details in §7.
- **No `publish:` block exists.** The only related setting is `publish_gate`.
- **The "path from the sandbox copy into the local repository"** that PB1
  depends on does not exist. Today work leaves the sandbox only by being
  pushed.
- **The 200-character cut is in `broker.honour`** (`tail[-1][:200]`), on the
  Worker-request path. `tell_manager`, `mailbox` and `delivered` do not
  truncate.
- **The refinement "read once" is a board snapshot (TR4), not a config
  snapshot.** No refinement config block is built. The precedent I follow is
  the principle, not a mechanism: read once, and pass the bytes that were
  checked.

## 1. Config

```yaml
# .rite/config.yaml
publish:
  strategy: pull_request   # commit | push | pull_request | push_to_shared
  squash: false
  auto_merge: false        # only valid when the EFFECTIVE strategy is pull_request

# .rite/modules.yaml
modules:
  svc:
    path: svc/
    url: git@github.com:org/svc.git
    publish:               # every key optional; overrides the project key by key
      strategy: commit
      squash: true
      shared_repo: …       # see §1.2
```

- **Parsing** follows the existing pattern: a `PublishConfig` dataclass is
  added to `_CONFIG_SECTIONS`, a `ModulePublish` is added to `Module`, and
  unknown keys are refused. It is **refused as a `ParseError`**, not narrowed
  to defaults. A malformed `publish:` block that fell back to
  `pull_request` would be exactly the "setting its author believes they made".
- **Resolution** is `effective(project, module) -> Effective(strategy, squash,
  auto_merge, shared_repo, source={key: "project"|"module"})`. It is one pure
  function, and every reader calls it.
- **`rite doctor`** prints one line per module, e.g. `svc: pull_request
  (project default), squash off, auto_merge off`. The plan requires that line
  to be observed changing when an override is added and removed.
- `auto_merge: true` whose effective strategy is not `pull_request` is
  refused at parse.

### 1.1 `push_to_shared`: defined, refused

The value parses. **Any module whose effective strategy is `push_to_shared`
refuses at start**, at `rite start`, at `rite sandbox start` and in
`rite doctor`:

> `svc: publish strategy push_to_shared is not available until rite v0.8.0. Set publish.strategy to commit, push or pull_request.`

Nothing in the publish path handles it. If it ever reaches the publisher (it
should not), the publisher refuses with the same text. That second refusal is
a guard, not a feature.

### 1.2 `shared_repo`: parsed and refused, its rules deferred to PB2 (Q1)

**Decided: the three `shared_repo` rules (no default, missing refuses, same
remote as `origin` refuses, compared as resolved remotes) are built with
`push_to_shared` in v0.8.0, not now.** Building resolution logic for a
strategy that is refused would be speculative work against a design that may
change by then.

What v0.7.0 does: `shared_repo` is a module key that parses (so a config
written for v0.8.0 is refused by name, not as a typo), and **its presence
refuses at start under every strategy**, saying it belongs to
`push_to_shared` and naming v0.8.0. The refusal is tested at every entry
point; the door is provably shut rather than merely unimplemented.

## 2. Where publishing runs

**Workers stop pushing.** Their instructions change from "push after every
commit" to "commit on branch `<ticket-id>`; never push; rite publishes." Under
every strategy the Worker's job ends at a commit, so **a Worker's
instructions do not depend on the strategy.** That is what makes a mid-run
change survivable (§3).

**The Manager asks, and rite acts on the host.** This mirrors the broker
exactly, for the same reason: a sandboxed Manager cannot push or run
`rite`, and D10 measured that it cannot even commit there.

- The Manager writes `{"worker": "<w>", "ticket": "<id>"}` into
  `.rite/managers/<m>/publish-requests/`. This is its own directory, and
  those two keys are the only ones allowed.
- The supervisor takes the requests each cycle and calls
  `publish.honour(root, request)` **in process**. It does not parse a
  subprocess's last line.
- A person runs the same function as **`rite deliver <worker> --ticket <id>`**.
  The name is `deliver` because `rite publish` is already the gate's command
  group.

**`honour` is one function, and its steps are:**
1. **Check the request**: the Worker is declared, the ticket has a valid
   shape and is on the board, and there is no board means refuse (the
   broker's rules).
2. **Stop the Worker's sandbox, or refuse** if it cannot be stopped or its
   state cannot be read. Collecting from a copy an agent is still writing is
   a race. Stopping removes it, and when it cannot be stopped, rite fails
   closed. The same pattern is used in
   `_destroy_when_only_pushed_work_is_unapplied`.
3. **Collect.** For each module clone in the sandbox copy with commits on
   `<ticket-id>` that the project's module checkout does not have:
   - Run `git fetch <copy-clone> <ticket-id>:<ticket-id>` into **the
     project's module checkout**.
   - This only creates or fast-forwards a ref. It leaves the working tree
     alone, and git itself refuses if that branch is checked out or if the
     update is not a fast-forward. Both refusals are passed through with a
     remedy.
   - **Uncommitted changes in the copy are refused, not auto-committed.** The
     refusal names the files, so the Manager can send the Worker back to
     commit them. Auto-committing an unverified half-state would publish
     something nobody decided was finished. This is the one place I go
     against "rite commits whatever the model does": **rite preserves,
     rather than commits.** The copy is never destroyed with uncommitted
     work (existing behaviour, kept), so nothing is lost. It is only not
     yet published.
4. **Squash**, if the effective setting is on. The squash builds a new
   commit on the base with a message summarising the whole change. The
   original branch is kept as `<ticket-id>-unsquashed`, so the squash is
   reversible and `rebase -i` stays easy.
5. **Stop here for `commit`.** Nothing is pushed.
   *(TR10's hook, not built here, by Robert's sequencing: a refusal of work
   whose ticket is not REFINED belongs at step 1, where the ticket is already
   checked against the board. It lands after the RC run, so the RC measures
   the behaviour designed before it.)*
6. **The floor before anything leaves the machine**: rite's publish gate
   (`rite_ai.gate check`) runs against the branch head. If it does not pass,
   nothing is pushed.
7. **`push`**: `git push origin <sha>:refs/heads/<module.branch>`, with no
   force. A rejection (not a fast-forward, or branch protection) is passed
   through with its remedy. rite never rebases on its own, because a rebased
   tree is not the one that was verified.
8. **`pull_request`**:
   - `git push origin <ticket-id>`.
   - `gh pr create --base <module.branch> --head <ticket-id> --body-file …`.
     The body is written by the Manager into the request directory as a
     separate file. It is only ever passed as a file, never as an argument,
     and it goes through the gate's content scan.
   - The PR number and head SHA are recorded in the publish record.
9. **`auto_merge`** runs on later cycles, in §4.

Every outcome, successful or not, is **written to `events.jsonl`, said to
the terminal and told to the Manager** (§5).

**Credentials for 7 and 8** are the Worker's resolved GitHub token (the same
tiers as today), applied on the host with `sandbox_git_environment`'s helper
settings. A Manager's App token is not used: the push belongs to the Worker's
work, and the Worker's token is what `push_access_refusal` already checks.
**That pre-start check becomes strategy-aware**: a module whose effective
strategy is `commit` needs no push access. An on-premise project with no
reachable remote must be able to run `commit`.

## 3. Your three questions

### 3.1 A Worker started under `pull_request` and finishing under `commit`

**At Worker start, rite snapshots the effective publish settings for each of
the Worker's modules** into a host-side publish record,
`.rite/publish/<worker>.json`, together with the ticket and the start time.
It is written by `rite sandbox start`, in the same parse that start already
does. Neither the Worker's sandbox nor a Manager's enclosure may write it; I
will confirm that against the enclosure profile, not assume it.

**At publish, rite parses the config once more, only to detect divergence.**
It compares the two per module:
- **They agree**: publish under them.
- **They differ, in any key**, or **the current config cannot be read** (a
  failed read is not "unchanged"): **collect and commit locally, and do
  nothing else.** The refusal names both values, and the remedy is for the
  User:

  > `NOT pushed svc/KAN-8: publish changed pull_request→commit since the Worker started; committed locally only. Ask the User to run: rite deliver alpha --ticket KAN-8`

**Why commit-only, and not "the start value" or "the current value".** A
change in either direction is someone's decision, and I cannot tell which way
they meant it:
- Honouring the start value after a switch to `commit` pushes code the User
  just said they do not want pushed.
- Honouring the current value after a switch to `push` publishes work that
  was started when it would have been reviewed.

`commit` is the one action common to every strategy and it moves nothing off
the machine, so it is the only outcome that is safe in both directions and
loses nothing. **The User, at a terminal, is the authority on current
config**, which is why the remedy is `rite deliver`. It re-runs under the
current config, and its record says that is what it did. A Manager cannot
"confirm" on the User's behalf. There is deliberately no request key for
that.

The same rule covers a squash disagreement: collect **without** squashing.
Squashing can be done later, but un-squashing cannot.

### 3.2 Read once, or per publish?

**Read once, at Worker start. That snapshot is authoritative.** The single
publish-time read can only **take permission away**; it can never add any.
The principle is: *a config change can revoke from in-flight work, never
grant to it.*

Auto-merge is the case that matters. It acts minutes later, on another
cycle. A person who turns `auto_merge` off while a PR waits for checks
expects it not to merge. So **every auto-merge attempt requires the snapshot
AND the config parsed at that attempt to both say `true`.** Turning it off
takes effect on the next cycle, and turning it on never reaches a PR already
opened without it. The read uses one parse per attempt, and a parse that
fails means no merge.

**The remaining race** is a config edit landing between that parse and the
merge call. It cannot be removed, because config is a file a person edits. It
is bounded to one cycle, and the terminal and events log say so. It is not
silent: the merge outcome is told, and it names the config version (the
file's hash) it was decided from.

### 3.3 How a refusal reaches the Manager

- **Every outcome goes through `tell_manager`** (`about="work you asked rite
  to publish"`), besides `say` and `events.record`. Asynchronous outcomes
  (checks failed, merged, merge refused, PR closed) are told on the cycle
  they are seen.
- **The Manager's instructions promise this**, in the text `publish.instructions()`
  adds next to `broker.instructions()`. A test ties the promise to the
  delivery, as DF13's test does: for each outcome kind, drive `honour` or the
  merge tick and assert that a note for that Manager is in its inbox.
- **No 200-character limit applies here, checked rather than assumed.** The
  only cuts near this path are `broker.honour`'s `tail[-1][:200]` (the
  start-a-Worker path, which parses a subprocess's last line) and
  `session._what_tmux_said`'s (tmux's own stderr). `tell_manager` →
  `mailbox.send` → the inbox → the instruction cuts nothing. Publishing calls
  `honour` in process, so neither cut is on its path.
  - **The property tested is the real one: the remedy reaches the Manager's
    instruction intact.** A test drives each outcome kind through `honour`,
    takes the inbox the way the supervisor does, and asserts the note's
    `fix` text is in what the Manager is given.
  - **Its control** is the 280-character refusal from §3.1 (a 64-character
    ticket id, named twice) sent through a delivery patched to cut at 200:
    the test must go red, proving it would see a truncation if one were
    ever added to this path.
  - Notes are still one line, `NOT <verb> <module>/<ticket>: <why>. <fix>`,
    with the fix last-but-never-cut, and full detail (git's stderr, file
    lists) in the events log and the terminal.
- **One note per module**, so a two-module task under two strategies gets two
  short notes, not one long one that gets cut.
- **Filed separately, not fixed here:** the broker's `tail[-1][:200]` can
  cut a `rite sandbox start` refusal before its remedy today, and nothing
  checks that its refusals fit.

## 4. Auto-merge: green on the exact head, and the publish gate separately

Each auto-merge attempt is one pass per PR per cycle. It fails closed at
every step, and each step names the stale-green shape it rules out.

1. `gh pr view N --json state,headRefOid,baseRefName,mergeable,mergeStateStatus`
   is parsed as JSON. rite requires three things:
   - `state == OPEN`. **Shape 1**: a merged or closed PR has no checks to
     wait on, so that PR is reported, not merged.
   - `headRefOid ==` the SHA rite pushed. A head that moved is not what rite
     published.
   - `mergeable == MERGEABLE`. **Shape 2**: `CONFLICTING` fires no checks.
2. **The head must contain the current base tip.** rite reads the base ref's
   SHA and checks it is an ancestor of the head. **Shape 4**: a run whose SHA
   matched when it was read, while `main` had already moved, tested a
   different tree from the one a merge would make. If the head is behind,
   rite refuses with "update the branch". It does not rebase.
3. **Check runs are selected by `head_sha == headRefOid`**, never "the latest
   run". **Shape 3**: an older run's success. Two conditions must both hold:
   - **(a)** The check named by `publish_gate` (rite's own workflow,
     `publish-gate`) is present and `success`. It is checked **by name, on
     its own.** Its absence is not a pass.
   - **(b)** Every other check run and commit status on that SHA is
     `completed` + `success`, **and there is at least one.** Zero checks is
     "no answer", not green.
4. `mergeStateStatus == CLEAN` covers branch protection's own view: required
   checks, reviews, and whether the head is up to date.
5. The merge is `gh pr merge N --match-head-commit <sha>`. **GitHub refuses
   it if the head moved after step 1.** That makes the check-then-act race
   GitHub's to close atomically instead of ours to tolerate.
6. Auto-merge also needs **the snapshot AND the current config** to allow it
   (§3.2).

**Observation (the plan's "done when")**: on a scratch repository, each of the
four shapes is produced and seen REFUSED, then a clean green is seen merged,
with the head SHA matched in `jq` in the record.

## 5. Where it meets other tracks

- **TR10** (refuse to publish an unrefined ticket) belongs at exactly this
  chokepoint, as step 1 of `honour`. **Decided (Q2): after the RC run**, so
  the RC measures the behaviour designed before it; the hook is marked in §2.
- **Track MM**: the Owner reads `effective()` per module, and a secondary's
  outcome notes are per module. I build `effective()` and the notes, not
  MM's routing.
- **D10** (a Manager's sandbox cannot commit): this design sidesteps it.
  Every commit and push runs on the host, under the operator's own git
  config, where signing and hooks work.

## 6. Pieces, each a PR that merges on its own

1. **Config**: parse, override, `effective()`, doctor line, and the
   `push_to_shared` / `shared_repo` refusals at every start.
2. **Collect + `commit` + squash + snapshot + divergence**:
   - Worker instructions change to "never push".
   - Pre-start push access becomes strategy-aware.
   - `rite deliver`.
3. **Publish requests**: the supervisor honours them, `tell_manager`
   delivers every outcome, the remedy-reaches-the-instruction test with its
   200-character-cut control, and the
   promise-to-delivery test.
4. **`push` + `pull_request` + the gate floor**, with the SPEC §5.1.1
   amendment and `test_blast_radius` narrowed to allowlist one module.
5. **`auto_merge`.**

Each value is **observed** on a real project before its piece is called done,
as the plan requires. For 4 and 5, that needs a GitHub repository that rite
may push to (Q4).

## 6.1 Piece 2 as built: where it departs from §2, and why

- **Workers are told per start, in TICKET.md, not in CLAUDE.md.** CLAUDE.md
  is written once, when a Worker is added; a strategy can change between
  tickets. `rite sandbox start` writes a **Publishing** section per module
  from the same parse that writes the start record, so what the Worker was
  told and what `rite deliver` compares against are one read.
- **Keeping `main` coherent between pieces.** If piece 2 told every Worker
  "never push" before piece 4 teaches rite to push, `main` would silently
  stop producing PRs in between. So until piece 4:
  - under `commit` the Worker never pushes;
  - under `push` and `pull_request` it still pushes its ticket branch and
    opens the PR;
  - under **every** strategy it never merges.

  The last rule closes today's exposure (§4): with `main`'s strict setting
  off, a Worker's self-merge accepts a stale green. Piece 4 changes the push
  strategies to "never push" as well.
- **`rite deliver` never forces a destroy.** `--force` would also skip the
  check for a Worker's unanswered question. Instead the destroy guard
  counts a commit as saved when it is on a remote OR reachable (not merely
  present) in the project's checkout of that module (`_not_collected`). An
  unreadable `modules.yaml` drops nothing. The copy is destroyed only when
  every module was delivered. A divergence keeps it, so the User's
  `rite deliver` has something to deliver from.
- **Claims (D-41: held until the work lands).** Under `commit`, the work has
  landed once it is collected, so `rite deliver` releases the Worker's
  claims. Under the push strategies it lands at the merge, so claims stay
  held. Releasing them on an observed merge belongs to the tick that
  observes PRs (piece 5). **Until then, claims under `push` and
  `pull_request` are released by a person or by claim expiry**, and the
  Worker's instructions say only that they are released "after the merge",
  not that rite does it, because rite does not do it yet.
- **Every git call is a literal `["git", ...]` list.** A `["git", *args]`
  wrapper would have hidden every verb from `test_blast_radius`, which
  finds them by enumerating those lists.
- **The pre-start push-access check is strategy-aware.** Modules under
  `commit` are not asked for remote access, so an on-premise project with
  no reachable remote and no token can run.

## 7. The SPEC amendment (§5.1.1)

The current wording, "rite never writes to a remote", "`gh` never `pr`", and
every mutating verb only in `workers/<w>/", is replaced with:
- **Remote writes and `gh pr` happen in one module, `rite_ai/publish/`, and
  only under a configured strategy that permits them.**
- The project's module checkout gains refs (fetch / fast-forward) and
  nothing else. There are no working-tree changes there, no `--force`, no
  `reset --hard` and no rebase anywhere.
- `test_blast_radius` enumerates the argument lists as it does today, and
  allowlists `publish/` for `push` and `pr` only.
- Separately, its `gh` test is fixed. `... or "issue" in source` makes it
  pass whenever the word "issue" appears anywhere.

## 8. Decisions (Robert, 2026-09-29)

- **Q1: defer the `shared_repo` rules to PB2.** Test the refusal itself:
  accepted in config, refused at start, refused again at the publish step.
- **Q2: TR10 stays after the RC run.** Its chokepoint is written into §2.
- **Q3: yes.** On divergence: commit locally, nothing else, name both values
  and the command.
- **Q4: a private scratch repository under `robbartoszewski`**, obviously
  disposable, with required checks and a separate publish gate. Never `rite`
  itself.
- **Q5: `rite deliver`.**
