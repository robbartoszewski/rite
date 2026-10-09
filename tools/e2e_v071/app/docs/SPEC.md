# Tally Spec

How this library does the things its tickets ask for. Every subtask of a
decomposed ticket must cite a unit here (RL-63), so this file is what makes the
gate's tickets decomposable at all.

## tally-amounts — parsing and formatting money

`tally` represents an amount as a `decimal.Decimal` and never as a float:
binary floating point cannot hold `0.01`, and a money library that rounds
differently from the ledger it reconciles is worse than no library.

- `parse_amount(text: str) -> Decimal` accepts a decimal string and raises
  `ValueError` for anything else, including a non-finite value. It does not
  accept `None` or `bytes`.
- `format_amount(value: Decimal) -> str` quantizes to two places with
  `ROUND_HALF_UP` and groups thousands with a comma. The sign belongs to the
  whole number, not to the digits: `Decimal('-0.5')` formats as `-0.50`.
- Formatting never raises for a finite `Decimal`.

## tally-slugs — turning heading text into a slug

`slugify(text: str) -> str` normalizes free-form heading text into a URL slug.
Runs of characters outside `[a-z0-9]` separate words; the result is lowercase
and joined with single dashes, with no leading or trailing dash.

- Input with no word characters yields the empty string rather than a dash.
- Case and surrounding whitespace are not significant.

## tally-tests — how this library is checked

Tests live in `tests/` and run under the project's own environment:
`uv run pytest -q tests/<file>`. `pytest` is a declared dev dependency, so no
substitution is needed and no test command invents an interpreter that is not
on the path.
