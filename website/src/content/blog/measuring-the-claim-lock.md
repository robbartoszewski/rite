---
title: "Measuring the claim lock: six Workers, four minutes, 132,321 grants, zero lost"
description: A four-minute soak of six concurrent Workers against the new lock; the same harness ran ten seconds against the old one, which lost 309 updates.
category: measurement
author: "Rob Bartoszewski"
appliesTo: v0.7.0a10 # see the HTML comment below: the soak version is unconfirmed
draft: true
sources:
  - label: README.md — soak results, with their conditions
    href: https://github.com/robbartoszewski/rite/blob/main/README.md#L16-L18
---

<!-- OPEN (owner): publish date — add `date:` to the front matter when publishing. -->

<!-- DRAFT. The two numbers and their conditions are verified (brief §4.1). Everything about the harness — what a "grant" is counted as, how "lost" and "held twice" are detected, the machine it ran on — is TODO(owner). Do not fill it in from guesswork. -->

Each Worker claims the paths it is about to touch, and the second claim on the same path is refused:

```
$ rite claim backend/src/billing --worker alpha --ticket ABC-12
claimed 1 path(s) for alpha

$ rite claim backend/src/billing --worker beta --ticket ABC-19
claim failed: path contention
  backend/src/billing overlaps backend/src/billing (held by alpha)
```

That refusal only matters if it holds under concurrency, so it was measured rather than asserted.

## The numbers, with their conditions

- **New lock:** a four-minute soak of six concurrent Workers. **132,321 grants, zero lost and zero held twice.**
- **Previous lock, same harness:** **309 lost updates in ten seconds.**

## How the harness works

TODO(owner): describe the harness and link to it — what it does per iteration, what counts as a grant, how a lost update and a double hold are detected, and the hardware and OS it ran on.

## What the numbers don't cover

- **Docker.** On Docker, file locking does not lock: two Workers can be granted the same path. Run one Worker there.
- **Several machines.** Multi-machine claim sharing has existed since 0.4.0, with Owner election and failover. It is implemented and unproven, because it has not been run on two physical machines over a real network.
- **Dead holders.** A claim whose holder died is reported, never released. rite cannot tell a crashed session from one thinking hard, and releasing a path under a live Worker is worse than a stale claim.
