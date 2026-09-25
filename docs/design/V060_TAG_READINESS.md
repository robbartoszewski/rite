# v0.6.0 — tag readiness

**For Robert to tag from. Written 2026-09-26 against `main` at `c0523ff`.**
Re-check every line against `main` on the day. This project has shipped
functions that were correct and uncalled, and a list is only as current as
its last check. The release notes are the CHANGELOG's Unreleased section,
which `tools/release_notes.py` publishes from the tag, so nothing below may
be added to them unless it is observed.

## 1. Landed and observed — what the notes claim

| feature | observed how | where |
|---|---|---|
| Permission allowlist replaces skip-permissions | 5/5 benchmark on real Claude after a 0/5 that found the gap; `rite` allowed and `curl` refused, with a control | C4, C20 |
| Manager in a macOS sandbox, Workers through the broker | sandboxed Goose cycle, and a Worker started from its request | B9 `4ebbbd7` |
| Tmux and signal escapes closed | measured open, then closed; re-measured at `afa41e9` | `9862b59` |
| The Manager runs the `rite` that started it | pipx, checkout and uv tool, each through `rite start` | C27 `cc9abd5` |
| Local Manager on Goose | declared model in the pane; endpoint, model and 4096 checks stop the run; approval-ended session reported | `a56ecd1`, `90b0955`, `bb428ca` |
| Slack: DM instructions, broadcast as context, threads, posts, redaction, delivery while stopped | live with Robert, and a second person for the non-Owner case | A3–A6 |
| Two outbox readers, `rite reply`, outbox bound | through `rite reply` / `rite replies` | A1, C5, C23 |
| Check-in windows, standup from records, missed window | real CLI on real projects | K1, K4, K6 |
| Ticket text shown as the tracker shows it; phrases reported | real GitHub issues through `rite board show` / `rite start` | N1 (ticket half), N2 |

## 2. Landed, NOT observed — the notes say so, or stay silent

| item | state | notes say |
|---|---|---|
| Deferring and withdrawing, by a real model | stand-in engine only | "not yet observed" |
| Check-in posted to the DM, answers from its thread | stand-in Slack only | "stand-in for Slack" |
| N1's Slack half | needs a person to send the characters | "not yet observed" |
| `doctor` on an idle model | silent; `context_detail` never printed | known issue |
| `context_window.ensure_window` (derived model) | **no production caller** | nothing. Wire it after B8's decision, or record it as deliberately unwired |
| C14's blank-prompt guard | cannot fire from `supervise` | nothing (no live defect) |

## 3. Still work — blocks the tag

| # | work | why it blocks |
|---|---|---|
| W1 | **CI green on the commit to be tagged.** Red since `cecbbdd`. The last completed run failed 9 of 4425 on Linux (C28's list) | `releasing.md` step 8: do not tag on red |
| W2 | **C29: a Goose Manager restarts fresh on every `rite start`** | the local secondary in the two-Manager shape is a Goose Manager |
| W3 | **Two Managers (L1–L5): land, then observe on a real project.** Then add the paragraph in section 6 to the notes, saying what holds | nothing about it may ship in the notes before it is observed |
| W4 | **README vs notes.** The README says "Linux is implemented" and "Claude is the only agent rite drives today". The notes say a Manager cannot start on Linux, and document a Goose Manager. Both README lines must change before the tag (`releasing.md` step 3) | a published contradiction |
| W5 | C30: reserve the name `permissions`, or move the settings file | small, and a known issue if not done |
| W6 | Release steps 1, 4, 6–9: version bump, `tools/verify-and-push.sh`, the history tools after the tag, the verification statement naming platform and Python, the upgrade instructions | `docs/releasing.md` |

Should, not must, and a known issue if left: SB4 (narrow `~/.claude`), C1
(`delenv("TMUX")` in the fixture), B7 (print `context_detail`).

## 4. Robert's decisions, ordered by what they block

| # | decision | blocks | options |
|---|---|---|---|
| 1 | **C28: what does a Manager do on Linux?** | any Linux claim; part of W1 and W4 | unsandboxed and said on every run; refuse to start and say why; the Landlock backend, which leaves the tmux socket reachable (spike B9b) |
| 2 | **MMQ2: two Managers read one Owner's DM. Who acts?** | the two-Manager shape with Slack on. Today both act, and two relays are over Slack's 50/min floor | one app per Manager; per-Manager addressing; one Slack-reading Manager per project |
| 3 | **C6/C26: how does a credential reach a sandboxed Manager?** | a sandboxed Manager on a private GitHub board, and HTTPS push. Today it is anonymous | laid out in `CREDENTIAL_HANDLING…md` and C26; not yet reduced to options |
| 4 | **C25: an opt-out from the Manager sandbox?** | anyone whose hooks reach outside the profile. It also shapes option (a) of #1 | config key; `--no-sandbox`; widen per project |
| 5 | MMQ5: two standups per window in one DM | nothing; ships as built | keep; merge; the Owner composes |
| 6 | K6: confirm the missed-window behaviour as built | nothing | confirm; change |
| 7 | C24: a mid-cycle Worker starts at the boundary | nothing | keep; start at once |
| 8 | **C7: advertise `--record-issues`?** The redaction it waited for landed | nothing. The notes do not mention it | 0.6.0; 0.7.0; not yet |
| 9 | B8: may rite write a derived model into the operator's Ollama? | only `ensure_window` getting a caller | yes, reported and removable; no |

## 5. The Linux wording, ready for either outcome

- **No Linux backend lands:** keep the notes' known issue as written, and
  change the README's "Linux is implemented" to say a Manager cannot start
  there in 0.6.0.
- **The Landlock backend lands:** the known issue becomes that a Linux
  Manager runs inside a Landlock boundary that closes the signal escape and
  **not** the tmux socket (`connect(2)` is outside Landlock's filesystem
  rules, measured on Ubuntu 24.04). Say it in those words, and only after a
  Linux Manager has been observed completing a cycle.

## 6. The two-Manager paragraph, to add once W3 is observed

> **Two Managers on one machine.** A Claude Manager as Owner and a local
> secondary can run in one project. **What holds:** neither Manager can
> signal the other's processes or drive its tmux session (measured).
> **What does not:** each can write the other's state under
> `.rite/managers/`, a claim does not record which Manager holds it, and
> with Slack on, both act on every message in your DM. Several machines
> are not in this release.

Edit it to match what L1–L5 actually change. Its "what holds" sentence
rests on SPEC §5.4.8's table, re-measured at `afa41e9`.
