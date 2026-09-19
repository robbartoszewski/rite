#!/bin/bash
#
# Verify, then push — with every verdict taken from an EXIT CODE read
# directly. Run it from the repository root: `bash tools/verify-and-push.sh`.
#
# WHY THIS EXISTS. The suite and the gate both print a summary line, and a
# summary line is not a verdict. The loop this replaces was:
#
#     uv run pytest -q -x 2>&1 | tail -1
#     uv run rite publish check 2>&1 | tail -1
#     git push origin HEAD:main
#
# `$?` there is `tail`'s, which is 0 whether or not pytest passed — and
# nothing consumed it anyway, so the push ran unconditionally. A red suite
# would have been pushed under a line reading "3404 passed". That is EXC-1,
# committed inside the tooling written to avoid EXC-1: a check whose exit
# code nobody reads has stopped working and looks exactly like one passing.
#
# It caught a real stop on its first run. Not a red suite — a tree the run
# had modified underneath itself — but the distinction is the point: the
# piped version would have pushed and said "3404 passed" while doing it.
#
# WHAT IT CHECKS. The four things `docs/releasing.md` step 3 names: ruff
# check, ruff format --check, the suite, and `rite publish check` — the same
# four CI runs, in the order that fails cheapest first. It originally ran
# only two of them and still stood in for the whole step, which let a
# lint-only failure reach `main` after a clean local run. A tool that covers
# less than the sentence describing it is the defect this repository keeps
# finding in its own documentation.
#
# WHAT IT REFUSES TO DO. It does not retry the suite, skip the gate, or push
# with `--force`. A rejected push is rebased onto origin and RE-VERIFIED from
# the top, because the commits it landed on are not the ones the suite just
# ran against.
set -u

# Per-run log directory. The paths here used to be fixed — /tmp/rite-verify-
# pytest.log and friends — so two runs on one machine overwrote each other's
# evidence. The decisions were never wrong (`rc` comes from the command, not
# from a log) but the RECEIPT was: a concurrent run's summary could be
# printed under this run's verdict, and it was. A maintainer read PUSHED
# beneath somebody else's "1 error" line, concluded this script had let a
# failing suite through, and reported it as the night's sharpest finding. The
# guard had worked perfectly and its receipt lied, which for a tool whose
# whole job is producing a verdict a human can trust is worse than being
# visibly broken: it spends trust rather than time.
LOGS="$(mktemp -d "${TMPDIR:-/tmp}/rite-verify-XXXXXX")"
echo "logs: $LOGS"

step() { printf '\n== %s\n' "$1"; }

for attempt in 1 2 3 4 5; do
  # Lint and format FIRST: they take a second where the suite takes six
  # minutes, and CI runs them as their own steps. Leaving them out is how
  # this script let a lint-only failure reach `main` after a green local
  # run — `docs/` gained throwaway analysis scripts, ruff had 66
  # complaints about them, and nothing here asked. The runbook step this
  # script implements says "Suite, lint, format and `rite publish check`
  # green"; it was doing two of the four while standing in for all of it.
  step "lint (attempt $attempt)"
  uv run ruff check . > "$LOGS/lint.log" 2>&1; rc=$?
  if [ $rc -ne 0 ]; then
    echo "LINT FAILED (exit $rc) — not pushing"
    tail -20 "$LOGS/lint.log"
    exit 1
  fi
  echo "ruff check: clean"

  step "format"
  uv run ruff format --check . > "$LOGS/format.log" 2>&1; rc=$?
  if [ $rc -ne 0 ]; then
    echo "FORMAT FAILED (exit $rc) — not pushing"
    tail -20 "$LOGS/format.log"
    exit 1
  fi
  tail -1 "$LOGS/format.log"

  step "suite (attempt $attempt)"
  uv run pytest -q > "$LOGS/pytest.log" 2>&1; rc=$?
  tail -1 "$LOGS/pytest.log"
  if [ $rc -ne 0 ]; then
    echo "SUITE FAILED (exit $rc) — not pushing"
    grep -E "^(FAILED|ERROR)" "$LOGS/pytest.log" | head -20
    echo "full log: $LOGS/pytest.log"
    exit 1
  fi

  step "publish gate"
  uv run rite publish check > "$LOGS/gate.log" 2>&1; rc=$?
  tail -1 "$LOGS/gate.log"
  if [ $rc -ne 0 ]; then
    echo "GATE FAILED (exit $rc) — not pushing"
    tail -20 "$LOGS/gate.log"
    exit 1
  fi

  step "push"
  git push origin HEAD:main > "$LOGS/push.log" 2>&1; rc=$?
  if [ $rc -eq 0 ]; then
    echo "PUSHED"
    git log --oneline -3
    echo
    echo "NOT YET VERIFIED: the Linux run for this commit. Check it:"
    echo "  gh run list -L 4 --json headSha,status,conclusion,url"
    exit 0
  fi
  echo "push rejected (exit $rc):"
  tail -3 "$LOGS/push.log"

  step "rebase onto origin/main, then verify again from the top"
  git pull --rebase origin main > "$LOGS/rebase.log" 2>&1; rc=$?
  if [ $rc -ne 0 ]; then
    echo "REBASE FAILED (exit $rc) — resolve by hand, nothing was pushed"
    tail -20 "$LOGS/rebase.log"
    exit 1
  fi
  echo "rebased onto $(git rev-parse --short origin/main)"
done

echo "gave up after 5 attempts without pushing"
exit 1
