---
title: Plan review now needs a different model
description: Until 0.7.0a10, rite stamped its own approval and nobody read the plan. Now the approver must not be the author, must run a different model, and fails closed when rite can't place the author.
date: 2026-10-08
category: changelog-note
author: "Rob Bartoszewski"
appliesTo: v0.7.0a10
draft: false
sources:
  - label: CHANGELOG.md — 0.7.0a10
    href: https://github.com/robbartoszewski/rite/blob/main/CHANGELOG.md
  - label: src/rite_ai/local/stage.py — stage transitions
    href: https://github.com/robbartoszewski/rite/blob/main/src/rite_ai/local/stage.py
  - label: src/rite_ai/local/gates.py — per-stage artifact gates
    href: https://github.com/robbartoszewski/rite/blob/main/src/rite_ai/local/gates.py
  - label: "src/rite_ai/local/approve.py — the independence check (RL-6)"
    href: https://github.com/robbartoszewski/rite/blob/main/src/rite_ai/local/approve.py#L148-L161
---

Until this release, rite stamped its own approval, and nobody read the plan. As of `0.7.0a10`, approval is a check in rite's code, and the approver has to be a different model from the one that wrote the plan.

## What changed

Two commands now decide whether a plan moves on:

```
rite plan approve <ticket>
rite plan reject <ticket> --reason-file <path>
```

Before an approval is accepted, rite checks three things about the approver:

- it holds the plan-review duty;
- it is **not** the plan's author;
- it runs a **different model** from the author.

If rite cannot place the author, the check fails closed. An unknown author is treated as a failed check, not a pass.

## What a rejection does

A rejection sends the plan back to be re-authored, with the reasons from the file you passed. That loop is bounded, so a plan cannot loop between author and reviewer forever.

## Why it lives in code

A `CLAUDE.md` that says "review before you open a PR" is a request. Deep into a long session, it is a suggestion. Plan review is now a stage in the pipeline that rite's own code drives — definition, plan, plan review, approach, work, recomposition verify, delivery — and a model cannot skip or reorder a stage.

The stage is persisted, so a restart resumes where the work is, not where a session remembers being. Every move goes through one guard, and a stage can't be entered until its own artifact already exists in the state the stage claims. The log is append-only.

## The target doesn't move

The signed refinement record a Worker was started on is snapshotted. The plan is written against that snapshot and the finished work is checked against it, so a ticket edited mid-flight cannot move the target. Nothing is delivered until the composed work passes the ticket's own agreed check — each piece passing its own check is not the ticket working.

## What this does not do

rite's code checks *who* approves: the duty, the author and the model. rite also has no opinion on your code quality. Beyond the ticket's own agreed check, rite does not read your test or lint results.

The wider caveats still apply. In particular, one sandboxed Worker has not yet carried a ticket all the way to a merged PR in one run. [What works, what doesn't](/what-works) has the full list.
