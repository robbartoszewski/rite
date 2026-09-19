# Is the narrow-review finding real? — the claim, and what it actually supports

**The claim, as received:** the dogfood's last three tickets ran at roughly a
third the cost — *one Sonnet reviewer on a narrow brief* rather than *two or
three Opus agents* — and the narrow briefs consistently found more.

**It does not hold as stated, and the reason is in the sentence.** That
comparison changes three variables at once:

| | before | after |
|---|---|---|
| reviewer **count** | 2–3 | 1 |
| **model** | Opus | Sonnet |
| brief **width** | broad | narrow |

A third of the cost is fully explained by count and model alone, without brief
width contributing anything. And "found more" cannot be attributed to width by
this design either — a narrow brief that names the right target will beat a
broad one *on that target* whoever writes it, which is a statement about
aiming, not about width.

That matters because the three levers are not equally available. **rite can
set count and brief; it cannot set the model.** No template under
`templates/agents/` names one — they inherit whatever the session's default
subagent model is, which lives in the host's configuration, not in rite. So
"make one-Sonnet-narrow the default" is, in rite's own terms, only two thirds
expressible.

## What rite's default actually is

`templates/commands/review.md`:

- **Round 1:** two `reviewer-round1` agents for ordinary work, "at least
  three" for anything touching a gate, a shared contract, a migration,
  customer data or user-facing copy; **plus** `reviewer-decisions` once;
  **plus** `reviewer-seam` for anything crossing a module boundary.
- **Terminating:** two fresh `reviewer-terminating` agents.

**Five to seven agents per review**, then, and the count is where the money
is. `reviewer-round1.md` itself is a good brief — it demands a failure
scenario ("a finding without a failure scenario is a hunch"), forbids
inventing checklist items, and insists on the class over the instance. What it
does not have is a **target**: it reviews "the diff or the chunk of work you
were pointed at", against the whole checklist. Width is set by the caller,
and the template never asks the caller to narrow it.

## What this session's own data supports

Four review agents run tonight against real work, all four decisive:

| brief | model | outcome |
|---|---|---|
| loop plan — 5 areas, broad within each | default (Opus) | 3 critical + 4 major, all correct; killed the plan's premise |
| claim expiry R1 — 4 questions, named functions | Sonnet | found the guard that would have force-released live Workers |
| claim expiry R2 — 3 questions, named functions | Sonnet | found the auto-release path was dead code; found the audit swap was unsafe |
| sandbox capacity R1 — 3 questions, named functions | Sonnet | (in flight) |

**This does not show narrow beats broad.** The broad-ish Opus brief was the
most productive of the four. What it shows is that **narrow-plus-Sonnet was
sufficient** — three for three on findings that would otherwise have shipped,
two of which killed a design. That is the cost claim, and the cost claim is
the valuable one.

**The common factor in all four was not width.** Every brief named specific
files or functions, required `file:line` evidence, and explicitly permitted
brevity ("one line if a section is sound, no padding"). Two of them said what
*not* to re-litigate. None of that is about how much surface a reviewer
covers; it is about whether the reviewer knows what would count as an answer.

## Recommendation

**Change the count, not the character.** The defensible change, on the
evidence that exists:

1. `review.md`'s round 1 becomes **one `reviewer-round1` agent by default**,
   with the second and third reserved for the cases the file already
   enumerates — gate, shared contract, migration, customer data, user-facing
   copy. That is where the 2–3× is, and it is rite's to make.
2. The terminating check stays **two**, unchanged. It is the last gate before
   something ships, it reviews fixes rather than code, and nothing in the
   evidence touches it. Cutting the last check to save tokens is how a review
   stage becomes decorative.
3. `reviewer-round1.md` gains a **target** section: the caller must say what
   to examine and what would count as an answer. This is the part this
   session's data actually supports, and it costs nothing.

**Not recommended without measurement:** pinning a model in the templates.
rite does not control it today, and encoding "use the cheap one" in a
generated file would be an economy decision made on a sample of three,
inherited by every project, and invisible at the point it went wrong.

## How to settle it properly, when there is budget

One target, two reviewers, same round: a broad-brief agent and a narrow-brief
agent, same model, findings compared. That isolates width — the one variable
the original comparison cannot speak to — for the price of one extra agent on
one review. Until then, the honest statement is: **the saving is real and
attributable to count and model; "narrow finds more" is unproven and
plausible.**
