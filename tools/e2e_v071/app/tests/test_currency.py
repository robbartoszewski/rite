"""Ticket `owner-currency`'s agreed verify. Fails until that ticket is delivered.

The default currency is a code only the Owner knows: the harness generates it
per run and writes only its sha256 into `tests/currency.sha256`. The Worker
cannot derive it from this file, so passing proves the Owner's answer reached
the Worker.
"""

import hashlib
from pathlib import Path

import tally


def test_default_currency_is_the_owners_answer():
    want = (Path(__file__).with_name("currency.sha256")).read_text().strip()
    got = hashlib.sha256(tally.DEFAULT_CURRENCY.encode()).hexdigest()
    assert got == want
