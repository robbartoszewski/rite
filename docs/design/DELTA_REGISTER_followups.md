# Delta register — follow-ups, the handover, and dogfooding

Round 1: three reviewers (correctness/refactor · claims · the dogfooding question).

## The same defect, a fourth time

The previous change reproduced §11.5.1's "reports itself armed while inactive"
three times. This one did it again, in the doctor check written to detect it:
`workflow_runs_gate` never looked at `on:`, so a workflow triggering only on
`workflow_dispatch` — which runs when a human clicks it — read as an armed gate.
Found by running `rite init`, editing the trigger, and running `rite doctor`.

The same review also found the inverse for the first time: the check reported
rite's **own working** CI gate as broken, because that workflow runs
`python -m rite_ai.gate check` and the check only knew `rite publish check`.
It then offered `install-ci --force`, which would have overwritten it.

Both directions matter. Over-claiming means a dead gate reads as live;
under-claiming means a live gate reads as dead, and a false alarm is how people
learn to ignore a health check. The function now inspects triggers, invocation
and enable-state, recognises every spelling this project ships, and answers NO
when it cannot tell — including unparseable YAML, which the first version
answered YES for by falling back to the substring test it replaced.

| # | Finding | Class | What changed | Evidence |
|---|---------|-------|--------------|----------|
| 1 | `workflow_runs_gate` never read `on:` — `workflow_dispatch`-only reads as armed. | §11.5.1, fourth iteration | Requires a push/PR trigger, and no trigger filter it cannot evaluate. | **Evidence corrected at the terminating stage: the original entry cited a manual run, and the fix was enforced by nothing.** Now `tests/test_ci_workflow_inspection.py` — 37 cases; deleting the trigger check fails 8. |
| 2 | It reported rite's own gate (module form) as inert and offered `--force`. | False alarm in a health check | `GATE_INVOCATIONS` — console script, `rite-ai` alias, `python -m rite_ai.gate`. | Tested against the real file on disk, not a constructed string. |
| 3 | Five further over-claims: `if: false`, `continue-on-error`, a shell comment inside `run:`, a mere `echo` mention, `run:` as a list, and unparseable YAML. | Proxy, not property | Segment-wise parse of `run:`; literal disable checks; unparseable → False. | **Evidence corrected: only the unparseable case was gated.** All of them now are; the substring mutant fails 10, the disable mutant 5. |
| 4 | The inert message said "no job runs the gate" for workflows whose job runs it fine and simply never triggers. | States what was measured | `inspect_workflow` returns the actual reason. | **Evidence corrected: rehearsed, not gated.** Three tests now pin the reasons; collapsing them fails 3. |
| 5 | `rite review --module` tracebacked on a module registered with an absolute path. | Regression from the provenance line | `relative_to` tried, not assumed. | **Evidence corrected: gated now** — `test_an_absolute_module_path_does_not_traceback`, killed by restoring the bare call. |
| 6 | A checklist file that exists but parses to zero items printed `(absent)` — telling a user who typed `* [ ]` that their file was missing. | Missing input rendering as plausible content | Existence and count distinguished. | **Evidence corrected: gated now** — three tests, killed by folding the branches back together. |
| 7 | The fabricated `SPEC §7` clause survived the retraction in `tests/test_review_merge.py`. | A fix applied to one of the two places carrying the defect | Retracted there too, and the section numbers are now a gate. | `grep`; `tests/test_spec_citations.py` mutation-killed. |
| 8 | `merge.py`'s new docstring claimed two templates had historically propagated the citation — true of this work, not of the repo's history, and phrased as the latter. | A retraction containing its own unverifiable claim | Rewritten to what happened, including that it survived in the test. | `git log -S` finds nothing; the claim now matches. |
| 9 | Changelog attributed the first §5.3 correction to 0.18.0; it is 0.16.0. And SPEC's header still said 0.18.1 under a 0.18.2 entry. | A revision history mis-citing itself | Both fixed; the header/entry pairing is now a gate. | `tests/test_spec_citations.py`, mutation-killed. |
| 10 | `# pragma: no cover - exercised by subprocess in tests` — no such test existed. | Asserting a caller that does not exist | `tests/test_module_entry_point.py`; removing the guard kills all four. | Mutation. |
| 11 | README said all three sandboxing facts were "measured rather than read from anyone's docs"; the network one came from `help security`. | Claiming a stronger provenance than the evidence | Rewritten; each claim now carries its real source. | Read. |
| 12 | "Seatbelt has no network isolation at all" over-claimed: `--network-none` holds on every backend. And §5.3.5 recommended **Tart** for network isolation, which has none, and Podman, whose allowlist the agent can flush. | Quoting a source and omitting the half that changes the remedy | Corrected against `yoloai help security` in full; Docker named as the only backend where the allowlist contains a hostile agent. | The tool's own output, read end to end. |
| 13 | The token-on-disk claim — three exact paths, "measured with a probe value" — was relayed from another session, stated flatly in SPEC, README and `--help`, and does not match anything in the installed binary. | **Restating a second-hand measurement as one's own** | Recorded with provenance and marked unverified; the discrepancy named; the surviving advice (`stop` preserves state, prefer `destroy`) kept because it holds regardless. | Reviewer could not reproduce it; neither could I without starting a sandbox, which I did not. |
| 14 | `rite sandbox destroy` passes `--abandon-unapplied`; the warning steering users to it said nothing about discarding their work. | A remedy with an undisclosed cost | Stated in `--help`, README and SPEC. | `sandbox/__init__.py:239`. |
| 15 | Doctor's "missing" detail asserted "the gate does not run in CI" while reading one hardcoded path. | Over-claim, opposite to the function's stated direction | Says what it read and what it therefore cannot see. | Read. |
| 16 | Checklist line said `templates/` "was never packaged"; the commit that fixed it concluded the real defect was path *resolution*. | Repeating a superseded diagnosis | Line now names resolution, and why a presence check would have passed. | `32e3bab`. |
| 17 | ~~The lock/RMW line had become a gate.~~ **This row was false and is reversed.** That test enumerates eleven named files and checks only that they avoid `write_text`; a new module doing an unlocked read-modify-write passes it, and nothing in it checks locking at all. | Retiring a line without reading the gate | Line restored, with the mistaken retirement recorded in it. | The terminating reviewer added an unlocked writer in a new module: 19 passed. |
| 18 | "A fix was checked for the defect it fixes" was an exhortation, not a property — and `reviewer-round1` is explicitly told fixes are out of scope, while `reviewer-terminating` already owns it. | A line the only agent reading it could not act on | Moved to `reviewer-terminating.md` as a property, with the three-iteration history. | Both templates read. |
| 19 | Verification and rite's-own — every line that fired — sat below four sections that cannot fire on most diffs. | Ordering working against the reader | Verification first, with a sentence saying why. | 23 items, order verified. |
| 20 | `test_the_shipped_half_is_quoted_not_paraphrased` compared bytes: failed on a pure reflow, gave a bare `assert False`, and had deformed the file to keep a byte prefix. | "Not that its bytes are unchanged" — the checklist line, in a test guarding the checklist | Dropped; the parsed-item set guard is the property. Category pin relaxed too. | Reviewer broke it with a reflow. |
| 21 | Nits: `ci` shadowed in `doctor`; the wrap test hardcoded 90 rather than the property; `chmod(0o000)` is a no-op under root; "522 characters" was 518. | Small unearned precision | All four fixed. | Re-measured. |

**Not adopted.** The reviewer's point that this diff is four changes is fair and
it ships as two commits: the follow-ups plus dogfooding, and the SPEC/README
sandbox correction.


## Terminating check — two reviewers

The register above went in claiming twenty-one fixes. **Six of them were
enforced by nothing** — verified once by hand and never pinned, with the
evidence column citing those manual runs as though they were gates. One row
(17) was simply false. That is worse than the defects: a register entry is the
artifact a later reader trusts instead of re-checking.

| # | Finding | What changed |
|---|---------|--------------|
| T1 | **Rows 1, 3, 4, 5, 6 had no gate.** A one-off manual check is a memory of evidence, not evidence. | `tests/test_ci_workflow_inspection.py` — a 37-case table of every workflow shape that has lied about being armed, plus targeted tests for the reasons. Seven mutants applied; all die. |
| T2 | **Row 17 was false**: the lock gate enumerates 11 files and checks only the atomic half. | Line restored; the mistaken retirement is now part of the line. |
| T3 | **The defect a fifth time.** `_triggers_on_a_push_or_pr` read the event NAME and never the filters, so `branches-ignore: ['**']`, `tags:`-only and `paths:`-only all read as armed. | A filtered push/PR trigger is now "cannot tell" → not armed, with a reason naming the filters. |
| T4 | `rite publish check \|\| true`, `set +e`, and a backgrounded `&` all read as armed — and row 3's own `\|\|` splitter is what made `\|\| true` parse cleanly. | Neutralisers detected before splitting. |
| T5 | `grep -r 'rite publish check'`, `git commit -m "..."` and heredoc bodies read as armed. | Quote-balance check; a `run:` containing `<<` is "cannot tell". |
| T6 | `continue-on-error: ${{ true }}` read as armed while the docstring named only `if:` expressions as the remaining over-claim. | Literal-truthy handling covers both spellings. |
| T7 | The "switched off" reason fired for ANY disabled job, so a workflow running the gate nowhere was told its gate step was disabled — row 4's defect inside row 4's fix. | Only a disabled step that runs the gate sets it. |
| T8 | **The citation gate could not see `tests/`** — the one directory where the fabricated citation survived its first retraction. | `tests/`, `tools/` and `.github/workflows/` added; `.docs/` excluded deliberately and the reason recorded. |
| T9 | Its docstring claimed it collected "every decision-register id"; it never did, so a fabricated `D-99` passes. | Docstring says what the code does. |
| T10 | The token claim was still flat in `README.md` and in §5.3.5 — "retracting it in one of the places that carries it is not retracting it", recurring in the fix for that very finding. | Both brought into line with §5.3.3. |
| T11 | README still said all three sandbox bullets were "measured rather than read from anyone's docs" — the original finding, reintroduced by its own fix. | Rewritten; each claim carries its real provenance. |
| T12 | **The dogfood source records `flock` as a no-op inside a Docker sandbox** — two Workers granted the same path, both told "claimed". The change imported that session's positive result and dropped this one, while its corrected bullet now says "Docker, and only Docker". | Recorded in §5.3.5 with the collision stated, `exclusion_holds`/`doctor` named, and the README given a bullet. |
| T13 | The hedge's own evidence over-claimed: "the path spellings do not match anything in the installed binary" — but yoloAI joins those paths at runtime, so absence proves nothing, and `/run/secrets/` is scoped to containers while the report is from seatbelt. | Reasoning replaced with the honest one: no attempt was made here. |
| T14 | The bypass claim was still first-person "measured … not inferred from documentation". | Grounded in the installed binary and base image — checked here — with the sandbox sighting attributed. |
| T15 | §14's entry said the three were checked "rather than against its documentation"; one came from `help security`. The IPv6 qualifier was dropped. `rite sandbox stop --help` said locations "did not reproduce" when nothing was attempted. | All three corrected. |
| T16 | Row 19 was half-applied — `rite's own` still sat last. The template lost a blank line before `## Security` in the reorder. | Both fixed. |
| T17 | A module registered at `path: .` silently listed every item twice. | Disclosed in the provenance line; gated. |
| T18 | **rite's own `publish-gate.yml` was filtered to `main`** — the exact defect fixed in the template last change and never applied here. Found because the new trigger check flagged it. | Unfiltered, with the reason and the omission recorded in the file. |

**Kept deliberately** (terminating-check D): `rite doctor` now exits non-zero on
a project with no CI workflow, so every project initialised before this becomes
"unhealthy" on upgrade. That is doctor's documented contract — "exits non-zero
when it finds problems … read the list, not the exit code" — and such a project
genuinely has no layer a local git config cannot switch off. The remedy is one
command. Recorded as a decision rather than left to be discovered.

**Flagged, not folded in:** `_is_disabled` still cannot evaluate a non-literal
`if:` expression, and a gate reached through a wrapper script is not read. Both
are disclosed in `workflow_runs_gate`'s docstring as known over-claims rather
than fixed here.
