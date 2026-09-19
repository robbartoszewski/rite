# Delta register — publication path: install script and README cold read

Round 1: two reviewers (a timed cold read of the README; correctness of the
new tooling and whether the new gates gate).

| # | Finding | Class | What changed | Evidence |
|---|---------|-------|--------------|----------|
| 1 | **"pinned to a git tag … which makes it immutable"** — a tag is a movable pointer. A false security claim in the rationale of a secrets-gate tool. | Prose asserting a property the mechanism does not have | Rewritten as what each option buys; the tag protects against drift, not against the maintainer. | `git tag -f` moved a tag in a scratch clone. |
| 2 | The checksum verifies **`install.sh`, not rite** — the payload is fetched from the same movable tag, unverified. And option 3's `git checkout v0.1.0` is that same pointer. The mutability admission was scoped to option 1 alone. | Retracting a claim in one of the places that carries it | Stated for all three options; the commit SHA named as the one rung that pins what you got, and now published. | Read against `install.sh`'s own `SPEC_URL`. |
| 3 | `install.sh`'s header contradicted the README twice: "the URL cannot start serving you something else tomorrow" and "does not run rite afterwards" (it runs `rite --version`). **The file sceptics are told to read restated the overclaim as fact.** | Two copies of one claim, one corrected | Both fixed in `install.sh`. | `install.sh:16-17,23` vs `:86`. |
| 4 | "~90 lines" — it was 102. | Stale precision | "about 100 lines of sh", gated with a range. | `wc -l`. |
| 5 | "no files outside that environment" — both installers place a shim on your PATH. | Small false absolute | Stated. | How `uv tool`/`pipx` work. |
| 6 | The tool hashed **HEAD, not the tag**. After any post-tag commit it published the wrong digest with exit 0, then reported the correctly published digest as a mismatch. | Wrong in both directions | Resolves the tag (read out of `install.sh`'s own `VERSION=`), falls back to HEAD and says so. | Reproduced in a clone with a tag plus a later commit. |
| 7 | `--check ""` printed the block and exited **0**, so `--check "$UNSET"` read as a successful verification. | A failure path reporting success | `is not None` plus 64-hex validation; `sha256:` and full-`shasum`-line forms accepted. | Four malformed inputs gated. |
| 8 | `--check` was blocked by an unrelated dirty tree, though it only ever reads the committed blob — so editing for the next release made the last one unverifiable. | A guard applied where its own reason does not hold | Guard moved into the publish branch. | Runs dirty now. |
| 9 | Outside a git repo: a raw `CalledProcessError` with git's reason captured and discarded. | Exits non-zero, does not say why | `ToolError` carrying git's stderr. | Reproduced in a non-repo. |
| 10 | **The tool's headline property was ungated**: mutating it to hash the working copy left every test green, because the tree is clean when they run. | The check observes a proxy | A test that dirties the tree first; the mutant now dies. | Mutation. |
| 11 | **The config round-trip gate was vacuous for 7 of 24 fields** — every list, dict, float and `None`, including `expertise`, `scan_patterns` and `schedule.windows`: the three the serialiser builds by comprehension and the three most likely to be dropped. It claimed totality. | A gate weaker than the line it replaced | Fixture populated; `_mutate` raises `Indistinguishable` rather than silently comparing a value to itself. | All seven dropped individually — each now fails. |
| 12 | `test_the_runbook_points_at_the_tool` read a **gitignored** file, so it failed in every fresh clone — including the one the runbook's own step 3d tells you to make and run the suite in. | A test that goes red on the instruction to run it | Skips when `.docs/` is absent. | Reproduced in a clone. |
| 13 | With `.docs/` stripped, `tools/release_checksums.py` shipped with **zero callers** — the checklist line, on the artifact added while fixing another instance of it. | Built and nothing calls it | Referenced from `install.sh`, which ships and is the file whose digest it publishes. | Gated. |
| 14 | The README length assertion matched the code-block comment and not the prose, so updating the prose alone left the two contradicting each other, green. Range was ±30%. | Half-wired gate | Both forms asserted; range tightened. | Mutation. |
| 15 | The `STALLED` example was **staged** — `beta` had never beaten, so a reader reproducing it sees STALLED instantly and reads it as a bug. Prose said "the one that stopped answering". | Generated content stating more than was measured | Prose says what the output actually shows, including that a never-beaten worker reads STALLED. | `status.py`. |
| 16 | The trim list omitted the **coordination cost** block — the one section where two of three metrics announce they are not instrumented. Of everything trimmed, the one a sceptic would say was trimmed for looks. | Omission in a flattering direction | Named explicitly, and why it is named. | Real output. |
| 17 | No answer to **"how do I get rid of it?"** — `rite init` writes `.rite/`, `CLAUDE.md`, `.claude/`, a git hook and a CI workflow. The cold reader's first question. | A missing section, not a false one | "Getting rid of it", including `~/.rite/` and the keychain entries that survive it. | Ran the list against a real project: only `.gitignore` remains, as documented. |
| 18 | No statement that rite makes no network call of its own, on a tool whose audience is judging its supply chain. A true, checkable claim, absent. | A strong claim left unmade | Stated, with what the three real outbound calls are. | Grepped `httpx`/`urllib`; no telemetry of any kind. |
| 19 | The name-collision paragraph cost six lines in the fourth position of Install. | Attention spent where it is not earned | Compressed to four, reasoning kept parenthetically. | Cold read. |

**Structure, partly taken.** The reviewer's strongest structural point — 110
lines of supply-chain reasoning before any evidence the tool works — is
answered by "What it looks like" now sitting at line 24, and by a signpost
letting a reader skip the rationale. The rest of the reordering (Roadmap's
sandboxing bullets buried at line 300; "Handed a repo" mid-funnel; "What rite
deliberately doesn't do" answering objections nobody has raised) is recorded
and not taken: it is a restructure, not a correction, and this is the wrong
week for one.

**Not fixed, and not ours:** every install URL 404s today because the repo is
private and has no tags. That is publication steps 4 and 5, and the runbook
now says to verify all three paths from a clean machine afterwards — including
with `pipx`, which could not be exercised here at all.

---

## Terminating check — the README and install.sh as a sceptic reads them

Eight findings, and the shape of most of them is the same: **a correction that
swapped one false claim for another.** That is the failure mode this stage
exists for, and it caught it six times out of eight.

| # | Finding | What changed |
|---|---------|--------------|
| T1 | **Both keychain sentences were false.** Items are stored under service `rite` with names like `jira_token` — nothing begins with `rite-`, so a reader searching Keychain Access for it concludes rite stored nothing. And `rite credential remove` exists and does precisely what the README said it would not. | Service and real item names given; `rite credential list` / `remove` named. |
| T2 | **"It makes no network call of its own, and neither does rite"** — false twice. "It" is the installer, whose entire job is fetching over the network. And rite reaches PyPI on `rite update`, runs `git fetch`/`git clone` against module remotes on every `rite prepare`, and calls `gh api` when provisioning a sandbox token. | Narrowed to the claim that is true and checkable — no telemetry — with the real outbound calls enumerated rather than gestured at. |
| T3 | **A scheduler registration survives the uninstall list.** `rite scheduler install` writes a launchd agent or crontab line that keeps waking every few minutes against a project directory you have just emptied. The single most important omission for a section whose reader is asking "did it leave anything running?" | `rite scheduler uninstall` is now the first line, with the reason it must be first. |
| T4 | `rm -f .git/hooks/pre-push` was unconditional — but rite refuses to overwrite a hook it did not write, so that file may well be the reader's own. And under `core.hooksPath` rite never wrote there at all, so the line to remove is elsewhere. | Replaced with "open it, and delete it only if it contains `rite publish pre-push`", plus the redirected case. |
| T5 | **"about 100 lines" was stale again — and this change made it stale.** Round 1 flagged "~90" against 102; the replacement read "about 100" against 115. The gate's ±30% range could not see it. | Exact count in both places, gated exactly. It then caught a fourth instance immediately: a number written from a stale reading. |
| T6 | The trim list still omitted the `board state:` line and the version header — and `board state` is another not-configured admission, in the paragraph that stakes its credibility on naming exactly those. | Both named. |
| T7 | Compressing the name-collision paragraph dropped its only concrete test — "anything other than `rite, version …`" — leaving "something unexpected". The string then appeared nowhere in the README, and option 3's reader never runs `install.sh`, which is where the real check lives. | Restored. |
| T8 | "`~/.rite/` (… and a credentials file)" reads, in a security section, as "your secrets are in a plaintext file". The registry holds names and last-set timestamps, never values. | Said exactly that. |

**Also fixed:** the tool's own docstring still described it as hashing `git
show HEAD:install.sh` — the exact ref that register row 6 was about — and
`install.sh:26` broke the header's wrap for anyone doing `less install.sh`.

**A note on process, since it cost real time.** Two readings of
`install.sh`'s length disagreed (111, then 115) because a terminating reviewer
was mutating and restoring the tree while I was reading it. The line count is
now derived from `git show HEAD:install.sh` plus `git diff --numstat` rather
than a live `wc`, which cannot race. Worth remembering before editing files a
running reviewer is mutating.

---

## Terminating check A — evidence that was not a gate, and one real bug

**Three register rows cited a manual run as evidence.** Restoring each
original defect left all 1432 tests green. That is the same failure as the
previous round's — a one-off manual check recorded in the evidence column as
though it were a gate — and it recurred despite being named there.

| # | Finding | What changed |
|---|---------|--------------|
| A1 | **Row 6 ungated** — the headline fix of the tool. `_clean_clone` clones a repo with no tags, so the tag branch of `source_ref()` was never executed by any test. | `_tagged_clone`, and two tests: the tag is hashed rather than HEAD, and the tag's digest still verifies after a post-release commit. |
| A2 | **Row 9 ungated** — nothing ran the tool outside a git repo. | Two tests: git's own reason reaches the user, and a missing `install.sh` is reported rather than raised. |
| A3 | **Row 3 ungated** — nothing asserted `install.sh` and the README agree. It was the finding of the round, and a length-neutral edit putting the contradictions back stayed green. | A test asserting `install.sh` carries neither retracted sentence and does carry the corrected ones. |
| A4 | **The dirty-tree guard's own reason had become false** — row 8's defect reproduced inside row 6's fix. Once hashing moved to the tag, `block()` is correct whatever the working copy holds, but the guard still compared them: it refused on a **clean, fully committed** tree whose only sin was a commit after the release, and advised "tag first", which means `git tag -f` — the exact move this round's README fix calls the security hazard. | Guard applies to the HEAD-fallback branch only, where its reasoning actually holds. Gated both ways. |
| A5 | `--check` compared one digest against every entry of `RELEASE_ARTIFACTS`, so a correct digest would report MISMATCH the moment a second artifact was added — generality that reads as foresight and is wrong when used. | Collapsed to one artifact. Nothing publishes a second today. |
| A6 | `release_ref()` read the working copy with a bare `read_text()`, so an absent `install.sh` raised past the `ToolError` handler — the class the handler was added for, surviving in the one place that reads the working tree. Its regex also accepted a whitespace-only version. | Wrapped, and `\\S+`. |
| A7 | `test_the_tool_has_a_caller_in_the_tree_that_actually_ships` asserted a path string inside a comment, and called that a caller. | Renamed, and its docstring now says it asserts a reference; nothing in the tree invokes the tool, which is what a maintainer command looks like. |

**Not a finding:** A's note that the README's network enumeration omits `git
fetch`/`git clone` and `gh` was against the pre-fix text — it says itself the
tree moved underneath it mid-review. The settled paragraph lists both.

**The pattern worth keeping.** Across three rounds the same two defects
recur: a check that observes a proxy rather than the property, and an
evidence column recording a rehearsal as a gate. The second is the one that
compounds, because a false register entry is what the next reader trusts
instead of re-checking. Every row above is now killed by a named mutant, and
the mutant is what the evidence column cites.
