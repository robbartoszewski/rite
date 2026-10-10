---
title: Why rite won't start a Claude session when nobody is watching
description: Unattended dispatch of Claude sessions is refused on purpose, because it spends your quota with nobody watching. What the loop does instead, and where that leaves the north star.
category: design-decision
author: "Rob Bartoszewski"
appliesTo: v0.7.0
draft: true
sources:
  - label: "SPEC.md §9.12 — nothing rite runs unattended starts a Claude session"
    href: https://github.com/robbartoszewski/rite/blob/main/SPEC.md#L5305
  - label: README.md — quota and "Why you might not want it"
    href: https://github.com/robbartoszewski/rite/blob/main/README.md
---

<!-- OPEN (owner): publish date — add `date:` to the front matter when publishing. -->

<!-- DRAFT. Every fact below is taken from the verified fact sheet (brief §4); the one connecting sentence ("Put those together…") only combines two of them. The owner may add reasoning from SPEC §9.12 itself; do not add anything that is not in SPEC, README or the code. -->

The north star for rite is easy to state: set a fleet running, come back to reviewed, delivered work. It is a goal, not a capability. One piece of the gap is deliberate: rite does not dispatch Claude sessions when nobody is watching.

## What is refused, and why

Unattended dispatch of Claude sessions is refused on purpose (SPEC §9.12), because it spends your quota while nobody is watching. That is a judgment call, and this post is the reasoning behind it.

rite neither meters nor throttles. N Workers burn your Claude quota at roughly N times one session's rate, against a quota shared with the Claude apps on a rolling window. Pro exhausts sooner than Max. It also does not end gracefully: a Worker that runs out stops where it stands, claim still held until you release it.

Put those together: an unattended dispatcher would spend quota nobody is watching, and a Worker that runs out would leave its claim held until you release it.

## What runs instead

You start every run. A Manager runs **in your own foreground terminal**:

```
rite start <manager> --sessions N --minutes M
```

Ctrl-C ends the run. A bare `rite start` continues that Manager's last session; `--fresh` starts a new one.

TODO(owner): say what `--sessions N --minutes M` bound, and what happens when they run out — that is, how a capped run in your own terminal differs from unattended dispatch. Readers will ask.

`rite loop` watches the queue and reports, every couple of minutes, in one word, why the run is where it is: everyone busy, files held, schedule closed, nothing waiting, or **deadlocked**. On deadlocked, it stops and prints exactly what to release. It starts nothing.

## Where that leaves the north star

Today rite concentrates the hours that need you — settling requirements, making the calls, reading what came back — and keeps sessions moving in between. Perpetual unattended operation is still the direction. It is not what this release does, and [What works, what doesn't](/what-works) says so.

<!-- TODO(owner): if SPEC §9.12 records a condition under which unattended dispatch would be reconsidered, quote it here with a line link. Otherwise leave this out. -->
