# Spec digest — design, for approval before building

**Status: design only, nothing built.**

Robert wants rite to ingest an existing project spec, digest it into an
addressable "rite spec" set, and run two rounds of review verifying the derived
set covers the original with no discrepancies.

The motivation holds and rite's own SPEC is the proof case: **3,772 lines, 132
headings, 51 numbered decisions in one document.** A worker picking up one
ticket needs the relevant unit, not the whole thing.

---

## 0. The point: a worker must load a small slice, not a smaller monolith

Robert's clarification — **this exists to fix the large-spec problem.** Success
is that a worker loads only what its ticket needs and that slice is small.
Coverage and fidelity are constraints on getting there, not the goal.

That makes **granularity and closure the central question**, so §1 answers it
with measurements against rite's own SPEC rather than argument. The short
version:

| | measured on `SPEC.md` (3,773 lines, 118 numbered units) |
|---|---|
| a unit on its own | **0.6%** of the spec (median 23 lines) |
| unit + full transitive dependencies | **70.5%** — the monolith, rebuilt |
| unit + depth-1, hubs pinned | **2.3%** median, **9.3%** p90 |
| the pinned preamble every worker carries | **8.4%** |

**Transitive closure is not viable and this is not a judgement call — it is
70% of the document at the median.** Everything in §2 follows from that number.

**The pointer approach is a stopgap and should be described as one.** Pointing a
worker at a 2,600-line spec does not solve the problem; it moves the cost from
rite's context into the worker's. Worth shipping as the unblocker (§8), but it
does not retire this.

---

## 1. The constraint that shapes the command surface

§9: *"The CLI covers the **non-AI surface**."* D-40 settled the same question for
`rite ticket` — the mechanical pieces are subcommands, the orchestration that
needs judgement is a Dispatch slash command.

Digesting a spec and judging fidelity are judgement. Mapping identifiers and
comparing hashes is not. **So the split the CLI charter already forces is
exactly the split constraint 2 asks for**, which is a good sign:

| | where | what |
|---|---|---|
| **mechanical** | `rite spec …` subcommands | index the source, hash it, assert coverage, detect drift and tampering |
| **judgement** | `/spec-digest` slash command | produce the unit bodies, run the two review rounds |

The gate is the mechanical half, and it is the half that can run in CI and in
the publish gate because it spends no tokens.

---

## 2. Granularity and closure — the crux, measured

### 2.1. Unit size is not the problem

Heading-level units on rite's SPEC: **median 23 lines, p90 61, max 221.** Only 4
of 118 exceed 100 lines. A unit is already 0.6% of the document.

So the temptation to cut finer should be resisted — cutting finer buys nothing
against a median of 23 lines and makes the link graph denser, which is the
failure Robert named: *"the links between them become the real spec."*

**Granularity is settled by measurement: headings are the right cut.** The
problem is entirely on the other side.

### 2.2. Closure is the problem, and transitive closure is fatal

Following every reference transitively pulls **70.5% of the spec at the
median** (p90 71.4%). A worker asking for one decision would receive
two-thirds of the document. That is Robert's "rebuilt the monolith with extra
steps", and it is measured rather than feared.

The reason is structural, and visible in the graph: out-degree is low (median
2) but a handful of units are cited by everything, so within two or three hops
every path converges on them and then fans back out across the whole document.

### 2.3. Three classes of reference, distinguished mechanically

The fix is that **not every reference is a dependency**, and the three kinds are
separable by graph shape alone — no annotation, no judgement:

| class | test | measured on SPEC | treatment |
|---|---|---|---|
| **hub** | high in-degree, small body | §2 Roles (in=29, **5 lines**), §9 CLI (in=17, 10 lines), §2.7, §5.3, §2.5, §3.5, §8, §9.10 | **pinned**: always loaded, never traversed into |
| **index** | high out-degree | §13 Decisions register (out=38), §14 Revision history (out=29), §9.1 Command reference (out=21) | **not traversable, and not a retrieval target** |
| **ordinary** | everything else | 107 of 118 | traversed, depth-1 |

The hubs are mostly small parent sections — §2 *Roles* is **five lines** and is
cited 29 times. Pinning it costs almost nothing and removes a huge number of
edges. **Pinning the top 8 hubs drops median transitive closure from 70.5% to
10.3%**, which is the single highest-leverage move in this design.

The index sections are the other insight: **nobody's ticket is "implement §14
Revision history".** They are navigation. Excluding them as retrieval targets
removes the worst outliers, because their large slice was never a real query.

### 2.4. The rule, and what it costs

**Retrieval = the unit + depth-1 references + the pinned preamble.** Not
transitive. Measured:

```
slice:    p50 2.3%   p90 9.3%   (5 of 118 units exceed 15%, and 3 of those are indexes)
preamble: 8.4%  (the 8 pinned hubs, ~315 lines)
typical worker load: ~11% of the spec;  p90 ~18%
```

**Depth-1 is a deliberate under-approximation.** A depth-2 dependency exists and
is not loaded. That is the trade: §2.2 shows the alternative is 70%. Rather than
pretend depth-1 is complete, the design makes insufficiency *countable* (§2.6)
so the depth can be tuned against evidence instead of taste.

### 2.5. Some specs will not decompose, and rite must say so before spending tokens

The decomposition quality is computable from `rite spec index` alone — **no LLM,
no digest, no cost.** So rite can tell a project whether this will work *before*
paying for it:

```
rite spec index
  118 units, median 23 lines
  8 hubs pinned (8.4% preamble), 3 index sections excluded
  projected slice: p50 2.3%, p90 9.3%
  ✓ this spec decomposes
```

**The refusal case is real and should be a supported outcome, not a failure.**
If after hub-pinning the p90 slice still exceeds ~25%, the spec is
irreducibly interconnected and splitting it would produce units that are not
independently useful. rite should say so and **degrade to the pointer approach
for that project** — which is exactly why §10's pointer must ship anyway and
must not be a temporary scaffold.

A flat document with no headings is the same answer arrived at sooner: refuse,
do not invent structure.

### 2.6. Two numbers, so success is not a judgement call

**Slice ratio** — retrieved lines ÷ total spec lines. Computed by
`rite spec index` at build time as a projection, and recorded per real
retrieval. Target: p90 under 15%. Current projection: 9.3%.

**Insufficiency rate** — how often a slice was not enough. This one needs
instrumentation, because the failure is a worker going and reading the whole
document, which is otherwise invisible:

- `rite spec show <id>` records the retrieval.
- A worker that had to fall back records it in its handover — one field,
  `spec_fallback: <unit-id>`, which is cheap because the handover is already
  written on a schedule.
- `rite spec status` reports the rate over the recorded window.

**That number is what tunes the depth.** A rising insufficiency rate is the
evidence for depth-2 or for pinning another hub; without it, the depth is a
guess defended by argument. It is also the honest measure of whether this
feature worked at all.

---

## 3. Command surface

```
rite spec index                # parse source -> unit inventory + hashes. No LLM.
rite spec status               # what is stale, new, removed, tampered. No LLM.
rite spec verify [--strict]    # THE GATE. Exit non-zero on any failure. No LLM.
rite spec show <unit-id>       # print one derived unit and its source pointer
rite spec units [--covers X]   # list derived units, optionally by what they cover
rite spec slice <unit-id>      # THE RETRIEVAL PATH (§2.4): unit + depth-1 +
                               #   pinned preamble — what a worker actually
                               #   loads. Prints its slice ratio, so the number
                               #   success is judged on is visible at use.
```

Digesting itself is `/spec-digest` in Dispatch, which calls `rite spec index`,
writes unit bodies for whatever `rite spec status` reports stale, then runs the
two rounds and finishes by requiring `rite spec verify` to pass.

`rite init` gains nothing here — the source spec is found by Reserve E's
detection, and this consumes that (see §7).

---

## 4. Identifying a source unit

**Units are headings by default**, because that is the structure every spec
already has and it needs no annotation from the author.

Measured on rite's own SPEC: **118 of 132 headings are numbered**, 14 are not
(`Promotion`, `Graceful demotion`, `Distribution`, …). So the identifier scheme
must handle both:

- numbered heading → its number: `5.3.3`
- unnumbered heading → slug of its heading path: `2.4/promotion`

Path-qualified, so two `Files` headings under different sections do not collide
— the same reason §8.10 puts the project in a name.

**Plus a second axis, configurable.** rite's SPEC carries 51 decision-register
rows (`D-31`), which are addressable units *inside* one heading and are exactly
what a ticket cites. A spec with numbered requirements (`REQ-14`, RFC-2119
`MUST` sentences) has the same shape. So:

```yaml
# .rite/config.yaml
spec:
  source: SPEC.md                  # from Reserve E's detection
  extra_units:
    - pattern: '^\| (D-\d+) \|'    # capture group 1 is the id
```

Headings always; extra patterns opt-in. **Deliberately not a general parser** —
a spec format guesser is a project of its own, and the failure mode of guessing
wrong is a coverage map that quietly omits things.

---

## 5. Artifact layout

```
.rite/spec/
  index.json           # source inventory: id -> path, lines, heading, source_sha
  units/<id>.md        # one derived unit, generated
  coverage.json        # unit -> [source ids it covers]   (written by the digest)
```

Each unit file opens with a banner and front-matter:

```markdown
<!-- GENERATED by `rite spec digest`. DO NOT EDIT.
     Edit SPEC.md §5.3.3 instead, then re-run the digest. -->
---
id: 5.3.3
covers: [5.3.3]
source: SPEC.md
source_lines: [1334, 1357]
source_sha: 8f2a1c…        # hash of the SOURCE unit this was derived from
body_sha: 41b0e7…          # hash of THIS file's body, for tamper detection
generated_at: 2026-09-13T…
---
```

`covers` is a list, not a single id, because a good split sometimes merges two
tiny adjacent sections into one unit, and sometimes splits one long section into
several. Coverage is a many-to-many map and pretending otherwise would force
distortion to satisfy the gate.

---

## 6. Where it lives, and why hand-editing is *detected* rather than discouraged

**Committed, not gitignored** — added to `scaffold.AUTHORED_CONFIG`'s
re-include list. A fresh clone and a second machine (Phase 2) must not each
re-spend the digest, which constraint 4 rules out.

But committed means editable, so constraint 1 needs teeth. Three layers,
increasing in force:

1. **The banner.** First line of every unit, naming the source location to edit
   instead. Cheap, and it catches the honest mistake.
2. **`body_sha` — the mechanism.** `rite spec verify` recomputes each unit's
   body hash. A hand-edit fails the gate, by name, with the source location to
   make the change in properly. **This is what makes hand-editing wrong rather
   than merely discouraged**, and it costs one hash per unit.
3. **`rite doctor` reports a tampered or stale set** the same way it reports
   anything else that is quietly not working.

The precedent is `.rite/kb/.cache/` — rite already distinguishes authored
content from fetched content and treats them differently. This is the same
distinction with the enforcement it needs, because unlike a link cache, a
derived spec is a thing workers *obey*.

---

## 7. What the gate asserts — `rite spec verify`

Four assertions, all mechanical, no LLM, fast enough for the publish gate:

1. **Coverage.** Every id in `index.json` appears in at least one unit's
   `covers`. Failure lists the uncovered ids. *This is constraint 2's mechanical
   half: coverage is checked by identifier, not by asking a model whether it
   feels complete.*
2. **No dangling coverage.** Every id in a `covers` list exists in the source.
   Catches a unit still claiming a section that was deleted.
3. **Freshness.** Each unit's `source_sha` matches the current hash of the
   source unit. Failure names the stale units — this is the regeneration
   trigger.
4. **No tampering.** Each unit's `body_sha` matches its body.

`--strict` additionally fails on units whose source ranges overlap in a way that
leaves a source line covered twice with different meanings — off by default
because a deliberate merge is legitimate.

**What it deliberately does NOT assert: semantic fidelity.** A mechanical check
that claimed to verify meaning would be the proxy defect this design exists to
avoid. Fidelity is round 1's job, and it is a human/agent judgement recorded as
a review, not a gate exit code.

---

## 8. The two rounds — different instruments, not two passes

From rite's own rehearsal history: rounds 1–6 varied the surface and the defect
count fell; round 7 varied the *instrument* and it rose again.

**Round 1 — vertical: does each unit faithfully represent its source?**
- mechanical: `rite spec verify` (§5)
- judgement: per changed unit, an agent reads the unit and *only* its source
  range and answers: does this distort, omit a qualifier, drop a ⚠, or turn a
  conditional into an absolute? Scoped to one unit, so it is cheap and the
  agent cannot be diluted by the whole document.

**Round 2 — horizontal: does the SET contradict itself or the whole?**
A different question with a different failure mode, deliberately not a re-run:
- does any unit contradict another unit?
- does any unit contradict something the source says *outside* that unit's own
  range — the failure round 1 structurally cannot see, because round 1 never
  looks outside the range?
- did splitting separate a rule from the exception that qualifies it? *This is
  the specific damage splitting does, and it is invisible to any per-unit check.*

Round 2 is the expensive one and §8 is how it stays affordable.

---

## 9. Cost — incremental by construction

- **Per-unit source hashing** means only changed units are re-digested. A typo
  fix in one section re-digests one unit.
- **Round 1 scopes to changed units.** Unchanged unit, unchanged source, prior
  review still valid.
- **Round 2 cannot scope so simply** — a changed unit can newly contradict an
  unchanged one, so naive scoping is unsound and naive completeness is O(n²).

  **Use the citation graph.** rite's spec already carries explicit `§5.3.3` and
  `D-31` cross-references, and `tests/test_spec_citations.py` already parses
  them (`§\s*(\d+(?:\.\d+)*)`). Extracting a reference graph is reuse, not new
  machinery. Round 2 then runs over *changed units plus their graph
  neighbours* — the units that cite them and the units they cite.

  **State the residual honestly:** that is a heuristic, not a proof. A
  contradiction between two units with no citation between them is missed on an
  incremental run. So a **full round 2 runs on first build and on demand
  (`--full`)**, and the incremental run says in its output that it was
  neighbourhood-scoped. A review that silently narrowed its own scope is the
  thing this project keeps finding.

---

## 10. Sequencing — ship the pointer first. One seam.

**Agreed, and for a reason beyond size:** the pointer is useful with or without
the digest, and the digest is unreachable without it. There is no version of
this where the digest ships first.

The stronger argument is constraint 1. A half-built digest puts a *partial*
derived set in front of workers who are told to obey it — a spec set missing
the sections nobody got to is worse than no spec set, because its incompleteness
is invisible at the point of use. The pointer has no such failure mode: it
either finds the spec or does not.

**One seam Reserve E should leave**, and it is small: record the detected spec
path in config as `spec.source` (a list, since a project may have several), not
only in generated prose or a worker's CLAUDE.md. If the path lives only in
generated text, the digest re-implements detection and the two drift — the same
shape as the sandbox/pool naming divergence in §8.10.

Nothing else needs to be built together.

---

## 11. Open questions for Robert

1. **Does the derived set replace the source in a worker's context, or sit
   beside it?** This design assumes *beside*: the worker gets its unit plus a
   pointer to the source. Replacing would make the artifact authoritative in
   practice whatever the banner says.
2. **What happens when the source has no headings at all** — a flat document?
   The honest answer is that this design cannot split it usefully and should
   refuse rather than invent structure. Worth confirming that refusing is
   acceptable.
3. **Should `rite spec verify` join the publish gate?** It is fast and
   token-free, so it can. It would mean a spec edit without a re-digest blocks a
   push, which is either exactly right or too aggressive depending on how often
   the spec is edited mid-work.

---

## Appendix — how the numbers were obtained

Every figure in §2 was computed against `SPEC.md` at `6a821d3`, not estimated.
The scripts are in `.docs/spec-digest-analysis/` so the measurement can be
re-run when the spec changes, or against another project's spec to decide
whether it decomposes (§2.5):

| script | answers |
|---|---|
| `specan.py` | unit count and size distribution |
| `closure.py` | slice size at depth 0/1/2/3/transitive |
| `hubs.py` | in-degree ranking; effect of pinning the top N |
| `clusters.py` | the practical config, outliers, aggregator detection |

The graph is built from `§N.N` citations — the same regex
`tests/test_spec_citations.py` already uses — plus structural parent edges. That
reuse is the point of §9's cost story: the reference graph is not new machinery.

**One caveat on the numbers.** They describe *this* spec, which is unusually
well cross-referenced because a citation gate has been enforcing it. A spec with
no explicit references would show a sparse graph and tiny slices — flattering,
and misleading, because the dependencies would be real but invisible. §2.6's
insufficiency rate is what catches that case, and it is the reason that metric
exists rather than trusting the projection alone.
