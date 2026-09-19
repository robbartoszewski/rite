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
# WHAT IT REFUSES TO DO. It does not retry the suite, skip the gate, or push
# with `--force`. A rejected push is rebased onto origin and RE-VERIFIED from
# the top, because the commits it landed on are not the ones the suite just
# ran against.
set -u

step() { printf '\n== %s\n' "$1"; }

for attempt in 1 2 3 4 5; do
  step "suite (attempt $attempt)"
  uv run pytest -q > /tmp/rite-verify-pytest.log 2>&1; rc=$?
  tail -1 /tmp/rite-verify-pytest.log
  if [ $rc -ne 0 ]; then
    echo "SUITE FAILED (exit $rc) — not pushing"
    grep -E "^(FAILED|ERROR)" /tmp/rite-verify-pytest.log | head -20
    echo "full log: /tmp/rite-verify-pytest.log"
    exit 1
  fi

  step "publish gate"
  uv run rite publish check > /tmp/rite-verify-gate.log 2>&1; rc=$?
  tail -1 /tmp/rite-verify-gate.log
  if [ $rc -ne 0 ]; then
    echo "GATE FAILED (exit $rc) — not pushing"
    tail -20 /tmp/rite-verify-gate.log
    exit 1
  fi

  step "push"
  git push origin HEAD:main > /tmp/rite-verify-push.log 2>&1; rc=$?
  if [ $rc -eq 0 ]; then
    echo "PUSHED"
    git log --oneline -3
    echo
    echo "NOT YET VERIFIED: the Linux run for this commit. Check it:"
    echo "  gh run list -L 4 --json headSha,status,conclusion,url"
    exit 0
  fi
  echo "push rejected (exit $rc):"
  tail -3 /tmp/rite-verify-push.log

  step "rebase onto origin/main, then verify again from the top"
  git pull --rebase origin main > /tmp/rite-verify-rebase.log 2>&1; rc=$?
  if [ $rc -ne 0 ]; then
    echo "REBASE FAILED (exit $rc) — resolve by hand, nothing was pushed"
    tail -20 /tmp/rite-verify-rebase.log
    exit 1
  fi
  echo "rebased onto $(git rev-parse --short origin/main)"
done

echo "gave up after 5 attempts without pushing"
exit 1
