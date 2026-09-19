# Spec digest — claimable tickets

Decomposed for the dogfood run: **off-path work, completely independent of the
CAS/election chain**, so workers are not all queued behind `P2-1b`.

Design: `SPEC_DIGEST_DESIGN.md`. Measurements: `.docs/spec-digest-analysis/`.

**Sizing rule applied:** one worker, one sitting. Every ticket below owns its own
module and its own test file, so most are claim-disjoint and can run in parallel.

---

## Collisions with the spec-pointer work (`b1a3b28`) — by design, flagged

Checked, and there are exactly two. Both are real and both are worth having as
contention tests rather than avoiding:

**1. The `rite spec` command group already exists.** `b1a3b28` added
`rite spec add` / `rite spec remove` in `src/rite_ai/cli/main.py`. Every digest
subcommand (`index`, `status`, `slice`, `verify`) lands in the same group in the
same file. **S-6 and S-9 both claim `src/rite_ai/cli/main.py` and will contend
with each other and with any Phase 2 ticket touching that file** — which is most
of them.

**2. `SpecConfig` already exists** with `paths` and `convention`. S-8 extends it,
touching the four files that any config field touches (`models.py`, `parse.py`,
`scaffold.py`'s serialiser, and the round-trip gate).

**Good news on the seam.** The design asked Reserve E to record the spec path in
config rather than only in generated prose. **It already did** — `spec.paths` is
exactly that, so the digest consumes it and does not re-implement detection.
One detail for S-1: `paths` may name a *directory* (pointers hand a directory
over whole), so the parser must expand directories to files.

---

## Startable immediately — no dependencies

These three can be claimed at minute zero. They are the answer to the run's
missing parallelism.

### S-1 — Spec unit parser
`src/rite_ai/spec/units.py`, `tests/test_spec_units.py`

Parse a source file into addressable units: numbered heading → `5.3.3`;
unnumbered heading → path-qualified slug (`2.4/promotion`) — **measured: 14 of
132 headings in rite's own SPEC are unnumbered, so this branch is required, not
defensive**. Each unit carries id, source path, line range, heading text, and a
content hash of its body. Expands directory entries in `spec.paths` to files.

Done when: `SPEC.md` parses to 118 numbered + 14 slugged units, and re-parsing
an unedited file produces identical hashes.

### S-7 — Insufficiency instrumentation
`src/rite_ai/spec/telemetry.py`, `tests/test_spec_telemetry.py`

The metric the whole design's honesty rests on. Records each retrieval, and
exposes a rate over a window. Pure module — the handover field that feeds it is
S-7b, split out because it touches shared code.

Done when: retrievals and fallbacks can be recorded and the rate computed, with
"no data yet" distinguishable from "zero fallbacks" (they are opposite readings
and only one is good news).

### S-10 — `/spec-digest` slash command
`templates/commands/spec-digest.md`

The judgement half (§9 CLI charter, D-40): drives `rite spec index`, writes unit
bodies for whatever is stale, runs **round 1 vertical** (unit vs its own source
range) and **round 2 horizontal** (unit vs unit; unit vs source outside its
range), then requires `rite spec verify` to pass. Prose only; no Python.

Done when: a reviewer can follow it without reading the design doc, and it
states that round 2 was neighbourhood-scoped when it was.

---

## Second wave — one dependency each

### S-2 — Reference graph and classification  ← S-1
`src/rite_ai/spec/graph.py`, `tests/test_spec_graph.py`

Edges from `§N.N` citations (reuse `tests/test_spec_citations.py`'s regex) plus
structural parent edges. Compute in/out degree. Classify **hub** (high
in-degree, small body), **index** (high out-degree), **ordinary**.

Done when, against `SPEC.md`: §2 *Roles* classifies as a hub (in=29, 5 lines)
and §13 *Decisions register* as an index (out=38).

### S-5 — Index artifact  ← S-1
`src/rite_ai/spec/index_file.py`, `tests/test_spec_index_file.py`

Read/write `.rite/spec/index.json` atomically (`state.write_atomic`). Distinguish
absent from unreadable — a corrupt index must not read as "no units", which is
the failure `CountUnavailable` and the handover work both exist to prevent.

### S-8 — Digest configuration  ⚠ collides
`config/models.py`, `config/parse.py`, `cli/init/scaffold.py`, round-trip test

Extend `SpecConfig`: `extra_units` (opt-in patterns, e.g. `D-\d+` rows),
`pin_count`, `slice_depth`, `refuse_above`. Defaults chosen so rite's own spec
decomposes with no configuration.

---

## Third wave

### S-3 — Slice computation  ← S-2
`src/rite_ai/spec/slice.py`, `tests/test_spec_slice.py`

Unit + depth-1 references + pinned preamble, minus index sections. Returns the
slice and its **ratio**. Not transitive — a test should pin that, because
transitive is the obvious "improvement" someone will later make, and it is
measured at 70.5%.

### S-4 — Decomposition report  ← S-3
`src/rite_ai/spec/report.py`, `tests/test_spec_report.py`

**"rite tells you before you pay" as a first-class output, not a log warning.**
No LLM, no tokens: unit count, hubs pinned, preamble cost, projected p50/p90
slice, and a verdict — decomposes, or **refuse and use pointers for this
project**.

Done when it prints the verdict for `SPEC.md` (✓, p90 9.3%) and for a
synthetic densely-linked spec (refuse).

### S-7b — `spec_fallback` in the handover  ← S-7
`src/rite_ai/handover/__init__.py`, `cli/main.py` (`handover write`)

One optional field so a worker that had to read the whole document records it in
the snapshot it already writes on a schedule. Small; touches shared files.

---

## Fourth wave — CLI surface  ⚠ both collide on `cli/main.py`

### S-6 — `rite spec index` / `status` / `slice`  ← S-3, S-5
### S-9 — `rite spec verify`  ← S-5, S-8

The gate: coverage by identifier, no dangling coverage, freshness by
`source_sha`, no tampering by `body_sha`. Mechanical and token-free, so it can
join the publish gate.

**Deliberately last.** The domain modules are disjoint and parallel; the CLI is
where they converge, and doing it last keeps the contention to two tickets
instead of six.

---

## Dependency graph

```
S-1 ─┬─ S-2 ── S-3 ─┬─ S-4
     │               └─ S-6 ⚠
     └─ S-5 ─────────┴─ S-9 ⚠
S-7 ── S-7b                        S-8 ⚠ ── S-9
S-10 (independent)
```

**Startable at minute zero: S-1, S-7, S-10.** Three tickets, three workers, no
dependencies, no contention between them — they own disjoint paths.

Then S-2, S-5 and S-8 open as S-1 lands, so the second wave is also three-wide.

## What this is worth as a dogfood test

- **Three-way parallelism from the start**, which the Phase 2 chain cannot give.
- **Deliberate contention, late and known**: S-6 and S-9 both want
  `cli/main.py`, as does most of Phase 2. That is the claim system's real test,
  and it happens at a point where the work behind it is already done.
- **The work is genuinely wanted** — this is the large-spec fix, not a fixture.
