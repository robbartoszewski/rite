"""Ticket refinement: the agreed definition of done, and whether a ticket has one.

Design: `docs/design/V070_TICKET_REFINEMENT.md` (track TR of the v0.7.0 plan).
This package is TR1: the signed record that lives on the board, and the one
predicate every consumer asks. Nothing here decides who may write a record;
that is TRQ10, and `accept` is not built until it is answered.

**One predicate, so no path keeps a judgement of its own** (the note's P6).
`status.evaluate` is the only place "is this ticket refined" is answered.
"""
