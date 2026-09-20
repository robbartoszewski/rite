# What rite must guarantee if a token passes through it

**Assessment only. Nothing here is built, and no launch path was touched.**

New scope created by Finding B's Option 1: `claude -p` authenticates only
from `CLAUDE_CODE_OAUTH_TOKEN` in the environment — the keychain path does
not work under `-p` — so choosing Option 1 makes a token a *requirement*
for unattended runs, and a credential then passes through rite.

---

## What is ALREADY true, because it shortens the work

Five things exist. Whoever builds this should not rebuild them.

1. **`redact_secrets` (`sandbox/__init__.py:1631`) already has the right
   shape.** Two passes: every `export NAME='value'` value **whatever the
   name**, then each known secret value anywhere else. It tolerates a line
   break or ANSI escape between any two characters, because a pane wraps
   and colours. Values under eight characters are skipped so `false` and
   `5` do not destroy the capture.

   ⚠ **Pass 1 is structural and pass 2 is a list.** The structural pass is
   the one that survives a secret nobody named; the list pass is the one
   that loses to it. Any extension should grow pass 1.

2. **It is applied where a pane is read for a human** — `sandbox pane`,
   two call sites (`:1720`, `:1729`). That surface exists to be read by
   Claude sessions, so unredacted it would route live credentials into
   model context.

3. **The Manager pane is already safe by a different mechanism.**
   `managers/session.py:_pane_text` reads the pane to DETECT an auth
   failure and **never returns it to a user** — `_why_the_engine_died`
   emits rite's own words. A test feeds a poisoned pane containing a
   token-shaped string and asserts neither it nor the engine's wording
   reaches the output. That is "don't handle it" rather than "redact it",
   and it is the stronger of the two.

4. **The publish gate scans commit messages, not only the diff** — rite
   relays messages through gitleaks deliberately, because gitleaks does not.
   This matters more than the file scan: a secret in a file is fixed by a
   commit; a secret in a commit **message** is fixed only by rewriting
   history, which rite is forbidden from doing.

5. **`CLAUDE_CODE_OAUTH_TOKEN` is the one credential that already avoids
   argv.** The sandbox docstring records why: yoloAI reads that specific
   variable from its own environment, where every other credential is
   passed as `--env KEY=VAL` on the command line and is therefore readable
   by `ps -ww` from **any local account** for the seconds a Worker starts.
   That is already a logged known issue.

   **So the credential Option 1 needs is the one rite is best placed to
   handle safely — by not handling it.**

---

## What Option 1 adds that is NOT covered

**The token has to reach the engine's process.** That is the whole of the
new risk, and the dangerous answer is already precedented in the code:

    session.py:405
    argv = [binary, "new-session", "-d", "-e", f"{MANAGER_ENV}={manager}"]

⚠ **rite already passes environment into the pane via `tmux -e`, on the
command line.** A Manager name there is harmless. **A token there would be
on the tmux argv and visible to `ps` for every local account** — the exact
exposure already logged for sandbox credentials, reintroduced on a path
that currently has none.

**The safe alternative needs no mechanism at all:** a tmux pane inherits
the environment of the server, and `rite start` runs in the user's own
shell. If the token is already in rite's environment it reaches the engine
by inheritance, and **rite never holds the value, never puts it on a
command line, and has nothing to redact.**

That also matches the compliance constraint already recorded in §9.14.7 —
consume `CLAUDE_CODE_OAUTH_TOKEN` from the environment only; never read,
persist, log or transmit it.

---

## Where rite captures output that could carry a credential

| surface | today | risk under Option 1 |
|---|---|---|
| `sandbox pane` | redacted (`redact_secrets`) | unchanged |
| Manager pane (`_pane_text`) | detect-only, never emitted | unchanged — provided it stays detect-only |
| **tmux stderr in refusals** — `session.py:455, 810, 896` embed `(done.stderr or done.stdout)[:200]` into user-facing messages | raw | ⚠ **if a token is ever on tmux's argv, tmux can echo the command line in an error, and it lands in a message** |
| **journal entries** — `journal.py` | **no redaction at all** | ⚠ a Manager is *instructed* to record "a command with its output" as an anchor. Nothing stops that output containing a token, and journal files are written to disk and meant to be shared |
| commit messages | gate scans them | unchanged |
| scheduler logs | not assessed here | unassessed |

**The two new ones are the truncated-stderr refusals and the journal.** The
journal is the more serious: item 9 explicitly asks a Manager to paste
command output, and §9.15.3a tells the operator to zip the directory and
send it.

---

## The positive rule

Today defeated a blacklist twice — a category blacklist missed U+2800, a
`isalnum()` rule missed the Hangul fillers — and the lesson transfers
exactly. **A list of things to redact loses to the thing nobody listed.**

Three rules, strongest first:

1. **Do not hold it.** A credential rite never possesses cannot leak from
   rite. Environment inheritance achieves this for
   `CLAUDE_CODE_OAUTH_TOKEN`; a file path that rite opens and passes
   straight to a child without keeping the value is the next best.
   **Prefer this over any redaction.**
2. **Do not capture where one could be.** The Manager pane already does
   this — read to decide, never to relay. It costs nothing and needs no
   pattern.
3. **Where capture is unavoidable, redact by STRUCTURE, not by name.**
   Pass 1 of `redact_secrets` is the model: anything shaped like an
   assignment is redacted whatever it is called. A name list is a
   blacklist and will miss the next variable.

⚠ **And the constraint that binds any of this**, carried from the anchor
rule: a guard that makes the honest path harder than the dishonest one
produces dishonest paths. If redaction makes a journal entry useless, the
Manager writes the file directly. Whatever is built has to leave the
correct action the easy one.

---

## What I would NOT build

- **A regex that recognises tokens.** That is a blacklist wearing a
  pattern, and it fails on the next credential format.
- **Redaction as the primary defence.** It is a mitigation — the existing
  docstring says so about itself, and names its own hole: a value truncated
  by a scrolled pane is not matched.

## What would show this wrong

- A token reaches a journal entry in a dogfood run — the surface nobody
  guarded because the Manager, not rite, put it there.
- Option 1 is chosen and implemented with `tmux -e`, and the exposure
  already logged for sandbox credentials appears on the Manager path.
