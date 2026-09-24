# The journal anchor rule: the ASCII floor stays — decided 0.6.0

**Status: DECIDED 2026-09-24 (Robert): keep the ASCII floor.** This is the
rule, not a stopgap for one. An anchor must contain at least one ASCII letter
or digit, and an anchor written wholly in a non-Latin script is refused.

**Why, in one sentence, for the next person:** "the anchor renders" is the
property actually wanted, and as far as has been established it **is not
computable from Unicode data** — four rules about characters have now been
tried, and every one that described characters lost to a character nobody
listed. Start from that, not from a fifth rule.

## What ships, and what it costs

`rite journal observe --anchor` requires the value to contain at least one
**ASCII letter or digit** (`journal.py:_is_blank`). Verified by running it:

    accepted  6a8a5b2                      RT-412      src/модуль.py:88
    REFUSED   U+3164 HANGUL FILLER         U+2800 BRAILLE PATTERN BLANK
    REFUSED   é                            U+FF21 FULLWIDTH LATIN A
    REFUSED   модуль строка   (wholly Cyrillic)

The cost, accepted knowingly: an anchor written **entirely** in a non-Latin
script, with no line number, SHA, path separator or ticket id, is refused
although a reader could check it. Only a value with *no* ASCII character at
all is affected. Every anchor form §9.15.3 names — a SHA, a `file:line`, a
command with its output, a ticket id, a log file with a timestamp — carries
ASCII, so the refusal says so and tells the Manager how to satisfy it.

## ⚠ Read this before replacing it: five rules, four of them defeated

Whoever reopens this will otherwise walk the same path.

| attempt | rule | defeated by |
|---|---|---|
| 1 | `str.strip()` | U+200B, U+200C, U+2800 — `strip()` removes Python-whitespace only |
| 2 | blacklist of Unicode categories (Cf, Zs, Cc) | **U+2800**, category `So` |
| 3 | `str.isalnum()` — a positive rule | **U+3164 HANGUL FILLER**, U+115F, U+1160, U+FFA0 — category `Lo`, alphanumeric **and** invisible |
| 4 (**ships, decided**) | at least one ASCII alphanumeric | nothing — ASCII is a closed set; cost stated above |
| 5 (evaluated 0.6.0, **rejected before building**) | accept any letter or number category (L\*, N\*) in any script; refuse format, control, separator | **its own observation** — see below |

### Attempt 5, and why it failed on paper

Proposed as the category-shaped version of the positive rule that worked:
accept any character in a letter or number category, in any script. Its
definition of done required that the Hangul fillers and U+2800 still be
refused. Measured on Python 3.14.3, Unicode 16.0.0:

    U+3164  Lo  HANGUL FILLER                   isalnum=True
    U+115F  Lo  HANGUL CHOSEONG FILLER          isalnum=True
    U+1160  Lo  HANGUL JUNGSEONG FILLER         isalnum=True
    U+FFA0  Lo  HALFWIDTH HANGUL FILLER         isalnum=True
    U+13441 Lo  EGYPTIAN HIEROGLYPH FULL BLANK  isalnum=True
    U+13442 Lo  EGYPTIAN HIEROGLYPH HALF BLANK  isalnum=True
    U+2800  So  BRAILLE PATTERN BLANK

**All six invisible characters are category `Lo` — a letter category.** So
"accept any letter" accepts exactly the characters the rule exists to
refuse. It is attempt 3 again at the category level, because `isalnum()` is
itself defined over those categories. (U+2800 would be refused correctly;
it is `So`.)

**Python's `unicodedata` exposes nothing that separates them.** Its whole
interface is `category`, `bidirectional`, `combining`, `decimal`, `digit`,
`numeric`, `east_asian_width`, `mirrored`, `decomposition`, `name`,
`lookup`, `normalize` and `is_normalized`. None of them says "renders as
nothing".

**The two ways to rescue it, and why neither was taken:**

1. **Vendor Unicode's `Default_Ignorable_Code_Point` data** (from
   `DerivedCoreProperties.txt`, which Python does not ship) and refuse those.
   It catches the four Hangul fillers. **It does not catch the Egyptian
   blanks**, which are not default-ignorable — so it is incomplete on the
   day it lands, and it adds a data file to keep in step with Unicode.
2. **Refuse a character whose Unicode NAME marks it blank** (`FILLER`,
   `BLANK`, …). It catches all six today. It is the character-list pattern
   in a different coat — the shape that lost as attempts 2 and 3 — and it is
   built from the same criterion as the sweep test below, so the test could
   only confirm the rule agrees with itself.

## The property actually wanted, and the standing position

**That the anchor RENDERS** — that a reader looking at the entry sees
something they can act on. Rendering depends on font, terminal and combining
behaviour; "has a visible glyph" is not a property the Unicode data exposes,
and three rules that approximated it from character properties each lost to
a character their author had not met.

So the standing position is **the ASCII floor, deliberately**: it does not
approximate rendering at all, it requires the one closed set every §9.15.3
anchor form already uses. Reopen it only with a way to compute "renders"
that does not come from a list of characters — and run the sweep below
against it before anything else.

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
