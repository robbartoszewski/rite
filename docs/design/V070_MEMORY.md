# Memory, 0.7.0 — Robert's design, recorded

**Recorded, not derived.** This is Robert's shape for the 0.7.0 memory module,
written down before it gets lost in a transcript. Nothing here is built. The
analysis under each part is mine; it is aimed at what building will have to
decide, not at whether to build.

⚠ **UPDATED 2026-09-25: the status paragraph below is stale.** `.docs/` was
replaced by this directory, and every design note, `V070_MULTI_MANAGER.md`
included, is tracked here (see `README.md`). v0.7.0 planning, including the
open questions this note raises, is in `V070_RELEASE_PLAN.md`.

**Status of the file itself.** This note is committed under `docs/design/`, so
it reaches a fresh clone. The other design notes — `V070_MULTI_MANAGER.md`
included — are under `.docs/`, which `.gitignore` excludes and where **nothing
is tracked at all**: `git ls-files .docs` returns empty. V060 records that
ownership question as open and says that if it resolves by dropping `.docs/`
from the repo, the note "needs a home". `docs/design/` is that home. Nothing
has been moved: this file chooses a home for itself and leaves the wider
decision open. One correction to V060 in passing — it says `.docs/`'s
"committed copies are force-added and stale"; there are no committed copies,
so the situation is worse than it records, not better.

**Citation convention.** A bare `§` is a section of `SPEC.md`; a section of
any other document is written out as "<file> section N", never with a `§`: the
gate's regex is context-free, so `§6.4` in another document's numbering would
silently match a real SPEC section. And `DEFECT_CLASSES.md`'s numbered classes
are written "class N" rather than `§N`. This note's own sections are "Analysis
N", never `§N`, because `tests/test_spec_citations.py` names exactly that
collision as the reason it does not scan documents that number their own
sections.

---

## The shape

**A RAG built from the project's own material, regenerated periodically, with
a temporary store carrying everything since the last regeneration.**

Indexed material: source code, specs, the knowledge base, established
practices such as design patterns, and decisions made.

**The Owner owns regeneration.** Cadence is set by two inputs — wall-clock
time and the size of accumulated temporary context. The numbers are
deliberately not fixed here; they are for implementation.

**Between regenerations, a separate algorithm injects changes.** It decides
whether a given change concerns a particular Worker or Manager, and
categorises changes three ways:

- by **feature**;
- by **files affected**;
- as **universal**.

**Both stores live in a git repository** — the generated RAG and the
not-yet-incorporated temporary store — pulled regularly by Workers and
Managers.

**Injection is provider-agnostic.** Relevant material from both stores is
injected into prompts regardless of which engine is behind the session:
`claude`, `cursor`, or `local:<class>` in V060's vocabulary.

### Motivation, as given

Large projects, and local models especially. Plus a specific observation from
a colleague of Robert's: that Claude's architecture and implementation quality
degrades once the source files involved exceed roughly **50k lines in total**.
Memory is aimed at that ceiling.

### The extension: self-reflection

Self-reflection (0.8.0, see `V080_RELAY_CHANNELS.md`) builds on memory rather
than beside it:

- it reads memory's **git history**, not only reported events;
- it runs a self-improvement loop over what that history shows;
- it saves amendments back into memory like any other change.

---

## Analysis

### 0. This is the spec digest generalised, and that changes what is open

**rite already ships a retrieval system with staleness detection, and memory
is its generalisation from one document to the whole corpus.** §9.13.2 — the
spec digest — is built, measured, and gated, and most of what follows is a
question about extending it rather than a question about inventing something.

| What memory needs | What §9.13.2 already has |
|---|---|
| A retrieval unit | A numbered heading, an unnumbered heading's slug path, or **one decision-register row** — "a register that is one unit is the whole register on every retrieval" |
| A relevance rule | The unit, what it cites at depth 1, and the top 8 sections by in-degree. **Measured: 9.1% at p90, against 71.5% for transitive following** |
| Anchoring | `rite spec stamp` records a hash of the source covered **and of the derived body** |
| A staleness gate with no LLM in it | `rite spec verify`, exiting **3** when it could not run, so "no spec registered" cannot read as "nothing has drifted" |
| A refusal case | `spec.refuse_above` (0.25) — a spec a slice cannot help is refused before anything is written, and that is "a supported outcome rather than a failure to work around" |

Two consequences, both load-bearing for everything below:

- **D-52 has already been narrowed, by D-54**, which says: "`rite spec add`
  still only points (D-52); a spec too large to hold is additionally digested
  into addressable units, and a spec a slice cannot help is REFUSED before
  anything is written." So the question for memory is not whether to narrow
  D-52 — that happened — but whether memory **extends D-54 or replaces it**.
- **§9.13's normative sentence is the one memory actually breaks.**
  "**Pointers, never copies.** `spec.paths` in `config.yaml` holds
  repo-relative files and directories. rite does not read them, index them, or
  copy them anywhere." Memory indexes them. That sentence has to change, and
  it is a more direct contradiction than D-52's cost argument.

### 1. Git-backed is the strongest decision here

Versioned, diffable, pullable, and it reuses transport rite already has. The
state layer substituted since 0.4.0 is already git-or-filesystem-or-kv behind
one conformance suite (V060), so a git-backed memory store is not a new
transport — it is the existing one carrying a new payload.

It also buys three properties the rest of this design needs and could not get
from a generated blob:

| Property | What it gives memory |
|---|---|
| Every state has a SHA | Injected material can be **anchored** (Analysis 4) |
| Regeneration is a commit | The diff *is* the answer to "what changed about what I know" |
| History is readable | Self-reflection's input exists without building anything for it |

The third is worth naming: **self-reflection reading memory's git history is
not an extra feature, it is the payoff of the storage choice.** If memory were
a regenerated index in a cache directory, the self-improvement loop would have
to be given an event stream of its own. Git-backed, it already has one.

### 2. Staleness is the central risk, and it is this week's defect class

A RAG is **a document describing code**. `DEFECT_CLASSES.md` class 6 — "Prose
and code drifting, in the direction that flatters the tool" — is that exact
shape, and it is on the wrong list in that file's closing table: the classes
held "only by someone noticing". Its guards are real but narrow —
`test_spec_citations.py` for section numbers, `test_release_checksums.py` for
a stated line count — and the file says plainly what gets through: "every
claim about behaviour, which is most of them."

Memory generalises class 6 from documentation to **everything a session
believes**, with two aggravations:

- **A nightly regeneration is up to a day stale by construction.** That is not
  a defect to be fixed; it is the design's resting state.
- **Nobody can see that it is stale.** Injected context arrives inside the
  prompt with the same standing as the ticket.

**The closest precedent is §9.13, not §8.7.** §8.7 dates a *cache file a human
opens* (`fetched:` in frontmatter, "a session reading a cache file six months
old can tell it is stale"). §9.13 disclaims *injected instruction text*, which
is memory's situation exactly: "**rite cannot tell whether a spec is
current**", said in the Worker's own generated `CLAUDE.md` as *"rite cannot
tell whether this is current. If it contradicts the code, say so in the ticket
rather than silently implementing either."* §9.13 also names the coarse
derivable guard that already runs — "What rite **can** check is that the paths
still resolve, which `rite doctor` does on every run."

§9.13.2 then goes further than either, and Analysis 4 is about adopting that.

### 3. The classifier is a filter, and its two failure modes are not symmetric

"Does this change concern this Worker?" has two ways to be wrong, and they
cost wildly different amounts:

| Failure | What the session experiences | Visibility |
|---|---|---|
| **Wrongly excludes** | Proceeds on stale understanding | **None.** Nothing indicates the omission |
| **Wrongly includes** | Reads something irrelevant | Noise, and context cost |

**A filter that wrongly excludes is invisible, and that asymmetry should set
the default.** Over-inclusion is a cost in tokens, which §3.1.1 makes real and
which is at least *measurable*. Under-inclusion is a Worker confidently
working from a picture of the code that is no longer true, with no signal
anywhere that it happened.

**§9.13.2 has already decided this question, in the opposite direction from
"filter harder", and its reasoning transfers unchanged.** `rite spec show`
"prints a stale, hand-edited or unstamped unit rather than withholding it, and
exits non-zero saying which: **a Worker handed nothing cannot judge anything,
and one handed drifted text with no warning cannot either.**" That is the same
asymmetry, resolved as *hand it over, labelled*. Memory's classifier should
inherit the resolution, not re-open it.

The family resemblance to class 2 in `DEFECT_CLASSES.md` ("a zero, or an
absence, that means 'I could not look'") is real but should be cited
precisely. Class 2 is one of only two classes with a **derived-scope**
mechanism, and the mechanism is an *enumeration* check — `UNREADABLE_FIELDS`
against `dataclasses.fields`, so a new field fails the suite until it is
classified. The remedy shape the same file calls "the right shape but
conventions rather than types", and it notes the guard "keys on a naming
convention, and the convention is upheld by nobody — `boards_unreached` is
already an exception, excluded by hand."

**So the transferable property is the enumeration, and the caveat transfers
with it:** the classifier's category set should be checked against a derived
source — its own enum — so that a new category fails the suite until it is
routed, and it should key on a **type** rather than a name, because class 2's
guard keys on a name and that is where it leaks.

A concrete default that follows: the category set already contains
`universal`. Anything the classifier cannot confidently place should land
there rather than be dropped, and an injection should be able to report how
many changes it did not classify.

### 4. Injected material must be anchored, and stamped is better

Without an anchor, a session cannot distinguish **"the code says"** from
**"memory remembers"**. Those two have very different standing and identical
appearance once they are text in a prompt, and collapsing them is how
confident wrongness gets manufactured.

The minimum is the source path and the commit SHA it was derived at, plus —
for temporary-store entries — the SHA of the change that produced them.

**§9.13.2's stamp is strictly stronger than that and should be the model.**
`rite spec stamp` records a hash of the source *and* of the derived body, so a
hand-edited derived unit is caught as well as a drifted source. A bare source
SHA cannot tell you that the fragment itself was altered. §9.13.2 also
explains why stamping is a separate command rather than automatic, and the
reason applies to any scheduled regeneration: "an automatic restamp would
bless both a source change nobody had read and a hand edit nobody had made —
after which nothing would ever read as stale or tampered again."

**That last sentence is the sharpest constraint in this note.** Memory
regenerates unattended, on a schedule. Unattended regeneration *is* automatic
restamping unless something separates "this was regenerated" from "this was
read by someone". Whoever builds this owes that separation an answer.

### 5. The regeneration trigger: the property that matters is churn

Wall clock plus accumulated-context size is a reasonable pair, and neither
input measures the thing that determines whether the index is wrong.

**The property is churn — how much of the *indexed* material has changed.**
Wall clock is a proxy that holds only when commit rate is steady;
temporary-store size measures how much has been *observed*, not how much has
*moved*. Two ordinary cases separate them:

- **A quiet week.** Nothing changed; the clock fires anyway and the Owner pays
  full regeneration cost for a near-identical index.
- **A large refactor in one afternoon.** Churn is enormous, the clock has not
  fired, and the temporary store may still be small in bytes — a rename across
  400 files is a big change and a short description.

**The statistic, named so it can be argued with:** the fraction of files in
`.rite/spec/index.json`-style indexed inventory whose blob SHA differs from
the one recorded at the index commit. Not `git diff --stat`'s line counts — a
reformatting pass moves many lines and no meaning, and a one-line signature
change moves little and much. Blob-SHA-changed-over-indexed-files is cheap
(`git diff --name-only <index-sha>..HEAD` intersected with the inventory),
needs no LLM turn in the read path, and has an obvious denominator.

**The threshold is a per-project key**, beside `schedule:` in
`.rite/config.yaml` (§2.7), not a global constant — Analysis 7 gives the
reason. The initial value should come from replaying this repository's last
month of commits against a built index and looking at where the curve bends;
it should not be guessed, and this note does not guess it. Keep the clock as a
floor and a ceiling around it.

### 6. The sleep metaphor is evocative and may mislead

Periodic consolidation of a temporary store into a durable one invites the
comparison to sleep. It conveys the shape well in conversation and is a poor
guide to the mechanism: **consolidation in the metaphor is lossy and
reconstructive, and both of those are defects here** — the second is exactly
what Analysis 4 exists to prevent.

Make it mechanical rather than a matter of taste: **no section, config key,
command or field may be named `sleep`, `dream`, `consolidate` or `remember`.**
The vocabulary to use instead is §9.13.2's, which is about derived units,
stamps and staleness, and says nothing about minds.

### 7. Cost, and who pays

Regenerating a RAG over a large codebase is expensive, and the expense lands
very differently by tier:

| Tier | What regeneration costs |
|---|---|
| Metered provider (`claude`, `cursor`) | Real money, repeatedly, on a schedule |
| Local (`local:<class>`) | Electricity and wall-clock time |

**This looks self-consistent — the local tier exists to absorb high-volume,
low-judgement work, and a scheduled bulk re-index is close to the purest
example — but it is unmeasured.** No figure in this repository gives local
regeneration throughput over a corpus this size; `RITE_LOCAL_ESTIMATE.md` has
none. It gets measured in the harness spikes or it stays an assertion.

Two consequences worth stating rather than discovering:

- **The cadence must be a per-project key, not one global number.** A fleet
  whose Owner is metered and one whose Owner is local have different economics
  for an identical policy — which is the second argument for churn-triggered
  regeneration (Analysis 5) over a fixed clock.
- **Injection is the larger cost, and it recurs on every tier.** §3.1.1
  measures ~500K tokens re-read per turn in this project's own coordinator
  session, ~98% of spend, and names context concentration as the mechanism.
  Memory adds material to *every* prompt on *every* engine by design.

**And there is a measured precedent for exactly this failure.**
`RITE_LOCAL_DESIGN.md` section 6.4.1 (RL-49): "**Measured over 36 hours on
this fleet: re-sent instructions were 10.8% of all context — the single
largest avoidable cost**, ahead of anything the spec digest saves. It is also
silent." Its rule is that "an instruction is composed from the log, never from
the composer's recollection", carried by `coordination/message_log.py` through
the state layer (D-20). **Memory is a recollection channel injected into every
prompt**, and it owes an answer to why it is not the next 10.8% — the more so
because that cost was invisible until someone measured it.

### 8. Self-reflection inherits a closed channel, and must not reopen it

`RITE_LOCAL_DESIGN.md` section 5.4 — "Instructions flow downward only" —
records that **an earlier prototype's self-improvement loop injected learned
conventions into worker and reviewer spawns through one channel**, that a
learned rule like *"don't re-litigate style in review"* measurably cut review
rounds, and that it cut review *depth* at the same time, because the thing
being reviewed had written part of what the reviewer was told to care about.
The recorded fix is: **learned content reaches workers, never reviewers.**

Memory is a learned-content channel, and self-reflection saving amendments
into memory is that mechanism precisely.

**The classifier must not be what enforces this.** Section 5.4's own rule for
the learned-conventions row is "**Segregated at the channel, not by wording. A
reviewer's context is built from rite's templates, the project spec and the
artifacts-as-evidence — nothing a reviewed tier can write.**" A per-item
classifier deciding whether learned content reaches a reviewer is deciding by
wording, and its wrong-exclude failure is the invisible one from Analysis 3.
So memory needs **either two stores, or one store reviewers do not read at
all**. The classifier may route *within* the worker channel; it may never be
the thing that decides whether a reviewer sees something.

Section 5.4's metric trap applies unchanged: **fewer review rounds is what a
leaky channel produces first, and it reads as efficiency.** Any claim that
memory improved the fleet must be measured net of rework (D-39), or it cannot
be told apart from reviewers that stopped looking.

### 9. The 50k-line motivation is an anecdote, and it is a measurable one

The colleague's observation is a reasonable prompt for the feature and is
recorded above as what it is: **uninstrumented, second-hand, and a single
threshold with no stated measure.** "Source files involved" could mean files
opened, files in context, or repository size, and those differ by orders of
magnitude.

That is not a reason to discount it. It is a reason to measure it before the
design is finalised, because the answer changes what memory should retrieve.
`REVIEW_COST.md` is this repository's precedent for what happens when a
plausible claim changes several variables at once and gets adopted as a
default.

**The claim is about quality, so the instrument is §2.6.3, not §2.6.1.**
§2.6.3's metric is "**weekly work delivered, net of rework** — merged tickets
minus tickets reopened or redone", with the worked case of one component
shipped three times in states that passed typecheck, the full suite and two
review stages while being entirely non-functional. That is what D-39 applies.
The experiment: bucket closed tickets by total lines in the files touched, and
compare net-of-rework delivery across buckets.

§2.6.1 measures the **cost** half only, and even there "the instrumentation
already exists" is too breezy — it warns that summing transcript lines
overcounts by **80%** without deduplicating on `message.id`, and that cache
reads were 98.7% of the corpus.

---

## Open questions, for whoever builds it

Not objections. These are the places where building will decide something this
note does not, and several are Robert's to answer.

1. **What does the classifier fail toward?** Analysis 3 argues the default
   should be over-inclusion, that unclassifiable changes should land in
   `universal`, and that §9.13.2 already resolved the same asymmetry as *hand
   it over, labelled*. That is a recommendation, not a decision, and it trades
   a silent correctness failure for a visible cost one. Robert's call.

2. **Who writes the memory repository?** The design says Workers and Managers
   *pull* it, and does not say who pushes. §5.1.1 forbids rite's package every
   remote-writing git verb, enforced by `tests/test_blast_radius.py`, which
   enumerates every `["git", ...]` argument list rather than grepping text;
   `RITE_LOCAL_DESIGN.md` section 6.4 keeps local tiers off the remote for the
   same reason. rite may write **locally**; it may not push. Three candidates,
   and the design picks none:
   - **A duty, not the package.** `RITE_LOCAL_DESIGN.md` section 6.4 already
     names the shape — "Pushing and opening the PR is the **`integrate`
     duty**, held by a Claude session or a person." That is the precedent to
     start from.
   - **Through the state layer.** Note this is not an escape from the
     question: V060 defines the layer as "git remote, local filesystem, or a
     socket-served key-value store", so the git substitution *is* a remote.
   - **A narrow §5.1.1 exception.** §2.4.2 is the live precedent — the
     coordination repo already force-pushes a `state` branch with
     `--force-with-lease` as its compare-and-swap. The collision is therefore
     not new, and how §2.4.2 sits with §5.1.1 today is the thing to read
     before inventing a fourth answer.

3. **Does the memory store pass `rite publish check` (§11)?** This is the
   answerable form of "what must not leave". §11.2: the gate "scans **all
   committed content** — source code, documentation, comments, test fixtures,
   commit messages, and file names", and "the `kb/` directory is scanned
   **hardest**". A committed RAG built from source is duplicated content by
   construction and will trip §8.7's default rule on every run. §11.4 permits
   only per-finding suppression with a recorded reason — **never a global off
   switch**. So: a new §11 rule, a recorded exclusion, or a store the gate
   does not scan — and whoever decides records the reason.

4. **Does memory extend D-54's digest, or replace it?** D-54 already narrowed
   D-52, permitting retrieval for a document too large to read whole, with a
   measured refusal threshold. Memory's corpus is wider — source code, not
   just prose. Which register entry is amended, under §13's supersession
   convention, and **what is memory's refusal case**? D-54 has one and treats
   it as a supported outcome; a corpus-wide RAG with no refusal case is
   claiming something D-54 declined to claim.

5. **What is the retrieval unit for source code?** For prose §9.13.2 answers
   it — heading, slug path, register row — and scores relevance by depth-1
   citations plus the top 8 hubs by in-degree, measured at 9.1% p90. Source
   has no headings and no `§` graph. Whether the analogue is the symbol, the
   file, or the import graph is open, and it interacts with Analysis 4: a
   fragment small enough to be useful may be too small to stamp meaningfully.

6. **Does `rite spec verify`'s property extend to a source-derived corpus, and
   at what per-injection cost?** `spec verify` is already the non-LLM gate
   this note asks for — it exits 0 only when nothing is stale, hand-edited or
   unstamped, and exits **3** when it could not run at all. The derivable
   enumeration for memory is plausibly "every injected fragment's stamp still
   matches at its recorded SHA". Whether that is affordable per-injection,
   rather than per-verify, is not known here.

7. **Is the temporary store readable by a human mid-cycle?** Self-reflection
   reads memory's history; nothing says an operator can. Given that this
   material silently shapes every session on every engine, "what does the
   fleet currently believe" is a question someone will need answered at 3am,
   and it is much cheaper to design in than to add.
