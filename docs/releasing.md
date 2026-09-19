# Cutting a release

Written down because two of these steps are invisible until they are missed,
and both of them are about what existing projects receive.

1. **Bump the version** in `VERSION`, `install.sh` (the `VERSION=` default and
   the three URLs in its header), `README.md`'s install line, and
   `docs/install-notes.md`. `tests/test_cli.py` asserts the version the CLI
   reports, so the suite catches a half-done bump.
2. **Write the changelog entry.** Both halves — what is new and what was
   fixed — and never bury the fixes, because a tester upgrading reads that
   section first.
3. **Suite, lint, format and `rite publish check` green**, on the commit that
   will be tagged, not on one near it.

   ```sh
   bash tools/verify-and-push.sh
   ```

   Use the script rather than typing the commands. It reads each exit code
   directly; the hand-typed version of this step piped `pytest` to `tail`,
   which made `tail`'s 0 the verdict, and would have pushed a red suite
   under a summary line reading "3404 passed". On a rejected push it
   rebases and re-runs the suite from the top, because the commits it
   landed on are not the ones the suite just ran against.

   **Green locally is half the answer.** The script says so when it
   finishes: CI is Linux-only and the local run is macOS-only, so check the
   Linux run for the tagged commit before step 4.
4. **Tag and push the tag.**
5. **Run the two history tools, after the tag exists, and commit what they
   write:**

   ```sh
   uv run python tools/template_history.py
   uv run python tools/section_history.py
   ```

   They format what they write, so the commit is gate-clean. They run each
   tag's own generator out of git and record what that release
   wrote — file checksums and per-section hashes and patterns. `rite update
   --files-only` uses that record to tell a file this release wrote from one a
   user edited. Skip this and the release ships, works, and quietly cannot
   deliver anything to projects created by it: every section it wrote looks
   like a user's edit to the next release's refresh.
   `tests/test_update_refresh.py` fails when this version's static sections are
   missing from the record, which is the tripwire for forgetting. It did not
   fire for v0.4.0 — the tag was pushed and neither tool was run, and the gap
   was found two hours later by someone reading the file rather than by the
   suite. Treat the tripwire as a backstop, not the reminder.
6. **Publish the GitHub release** with the notes and the `install.sh` checksum
   (`uv run python tools/release_notes.py`).
7. **State how the release was verified, and where it was not.** Name the
   platform and Python version the suite actually ran on, and say plainly
   what is therefore unconfirmed — "3361 passed on macOS/py3.14" is a fact;
   "the suite is green" implies a matrix that may not have run. If CI did not
   run at all, say so and rank what is least confirmed.

   **Check the CI run for the tagged commit before tagging, and do not tag on
   red.** `gh run list --repo <repo> --limit 5` answers it in one command.
   This sentence exists because its absence cost something: an earlier draft
   of the 0.5.0 notes asserted there was no CI at all — the repository is
   public, so the Actions allowance was never consumed — and four commits went
   onto a red `main` while the local run was reported as verification.

   This is a step rather than a courtesy because the failure it prevents has
   already happened here: three defects this week were invisible on macOS and
   caught only by the Linux run, one of them a platform split inside an
   error-string comparison. A release that implies coverage it does not have
   is the same defect class as a check whose exit code nobody reads — absence
   of a complaint standing in for evidence. So is a premise nobody checked.
8. **Say what a tester has to run to upgrade**, in the release notes:
   upgrading rite does not update a project's files, `rite update --files-only
   --dry-run` shows what would change, and `rite update --files-only` applies
   it.
