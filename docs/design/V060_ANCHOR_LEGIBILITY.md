# The journal anchor rule is too blunt — 0.6.0

**Status: OPEN.** v0.5.1 ships an ASCII floor. It is correct about what it
refuses and wrong about some of what it refuses.

## What ships, and what it costs

`rite journal observe --anchor` requires the value to contain at least one
**ASCII letter or digit** (`journal.py:_is_blank`). Verified by running it:

    accepted  6a8a5b2                      RT-412
    REFUSED   U+3164 HANGUL FILLER         U+2800 BRAILLE PATTERN BLANK
    REFUSED   é                            U+FF21 FULLWIDTH LATIN A

The cost: an anchor written **entirely** in a non-Latin script, with no
line number, SHA, path separator or ticket id, is refused although a reader
could check it. Only a value with *no* ASCII character at all is affected —
`src/модуль.py:88` passes.

## ⚠ Read this before replacing it: three rules, two of them defeated

Whoever tightens this will otherwise walk the same path.

| attempt | rule | defeated by |
|---|---|---|
| 1 | `str.strip()` | U+200B, U+200C, U+2800 — `strip()` removes Python-whitespace only |
| 2 | blacklist of Unicode categories (Cf, Zs, Cc) | **U+2800**, category `So` |
| 3 | `str.isalnum()` — a positive rule | **U+3164 HANGUL FILLER**, U+115F, U+1160, U+FFA0 — category `Lo`, alphanumeric **and** invisible |
| 4 (ships) | at least one ASCII alphanumeric | nothing yet; too blunt |

**The pattern is the lesson.** Attempts 1–3 were rules about *characters*,
and each was beaten by a character its author had not met. `isalnum()` is
Unicode-aware, which is exactly why it failed — it says yes to the Hangul
fillers. ASCII is a closed set: Unicode cannot add a new ASCII
alphanumeric, which is the only reason attempt 4 has held.

## The property actually wanted

**That the anchor RENDERS** — that a reader looking at the entry sees
something they can act on.

That is not implemented because it is hard to define correctly, not because
nobody thought of it. Rendering depends on font, terminal, and combining
behaviour; "has a glyph" is not a property the standard exposes cleanly,
and `unicodedata` does not answer it. A replacement that gets this right is
a real piece of work and worth doing — this note exists so it is done
deliberately rather than by another guess.

## ⚠ The constraint that killed the stronger idea, and binds any replacement

A shape matcher was proposed — accept only a SHA, a `file:line`, a ticket
id, a command with output. **It was rejected, and the reason applies to
anything tighter than the floor:**

> A refusal the Manager cannot satisfy is worse than a weak floor, because
> the Manager routes around the command and hand-writes the markdown —
> which is exactly the bypass D-92 closed by making rite own the writing
> path.

§9.15.3 permits free-form anchors ("a command with its output"), so a
matcher would refuse legitimate values, and a Manager that cannot satisfy
the refusal will write the file directly and meet no refusal at all.

**Whoever tightens this has to keep the honest path easier than the
dishonest one.** A gate that makes the honest path harder produces
dishonest paths.

## The instrument: the test must survive the sweep

`TestNoInvisibleCharacterCanBeAnAnchor` in `tests/test_manager_journal.py`
is the check any replacement has to pass. It does not list cases — it
DERIVES them, by two criteria independent of the rule:

- every character whose Unicode **name** marks it blank (`FILLER`,
  `BLANK`, `SPACE`, `INVISIBLE`, `ZERO WIDTH`, `EMPTY`) — 122 of them;
- every character in an unrenderable **category** (Cc, Cf, Cs, Co, Zl, Zp,
  Zs, Mn, Me).

⚠ **That independence is what makes it useful.** Run against the old
`isalnum()` rule it names the four Hangul fillers *and two Egyptian
hieroglyph blanks nobody had mentioned* — characters no reviewer listed. A
test built from the same list as the rule can only confirm the rule agrees
with itself.

It also carries a **positive control** (`test_a_real_anchor_is_still_accepted`)
because both sweeps pass against a rule that refuses everything. Any
replacement must keep both halves: sweeps green AND the control green.

## What would show the replacement wrong

- A legitimate anchor is refused → the constraint above; the Manager will
  route around it.
- An invisible character is accepted → run the sweep; it will name it.
- The rule grows a list of exceptions → that is attempt 2 returning, and
  the list will miss the next codepoint.
