"""Every place the version is written says the same version.

`docs/releasing.md` step 1 lists five files to bump and says "the suite
catches a half-done bump". It catches two of them. `tests/test_cli.py`
hardcodes the string, so forgetting it fails; `test_init_ci_workflow.py`
checks `install.sh`'s `VERSION="${RITE_VERSION:-...}"` default against
`rite_ai.__version__`.

Nothing checked the rest. Measured at 0.4.0: `install.sh` carries FOUR
version references and only the default was guarded; `README.md` one and
`docs/install-notes.md` five, neither guarded at all. Nine of eleven
occurrences could go stale with a green suite.

The consequence is not cosmetic and lands on strangers. `install.sh`'s
header tells a reader to `git checkout v0.4.0` and to compare a checksum
against "the v0.4.0 release notes"; `docs/install-notes.md` gives the
`curl` URL that fetches the installer. A tag cut with those left behind
serves the previous release's instructions from the new release's page,
and the person who finds out is a new user following them literally.

Unlike the changelog check next door, this property has STRUCTURE — a
version is a token with a shape, not a judgement about prose — so it can
be asserted rather than approximated. Where a property formulation exists,
use it; the tripwire is for where it does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import rite_ai

ROOT = Path(__file__).resolve().parents[1]

# Where a version is written, and whether it is spelled with a leading `v`.
# `pyproject.toml` is absent on purpose: it reads VERSION dynamically.
BEARERS = (
    "install.sh",
    "README.md",
    "docs/install-notes.md",
)

ANY_VERSION = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")

# Only versions that must TRACK the release. A version string is not
# automatically one of those, and the first draft of this test failed on two
# that are correct:
#
#   install.sh: "# older one ahead of it made a fresh install end
#                'Done: rite, version 0.1.0'."   <- a note about a past bug
#   README.md:  "(these need rite v0.2.0 or later)"  <- a MINIMUM, which by
#                                                       definition does not move
#
# Both would have had to be edited every release to keep a check quiet, which
# is how a check teaches people to write worse documentation. So the property
# is narrower and structural: a version inside an instruction that FETCHES or
# CHECKS OUT something. Those are the ones a stranger follows literally, and
# the only ones a release can leave stale without saying anything.
FETCHES = (
    "robbartoszewski/rite",  # a raw/clone URL carrying the tag
    "RITE_VERSION",  # install.sh's own default
    "git checkout",  # "clone, then check out the tag"
    "release notes",  # "compare against the vX.Y.Z release notes"
)


def _instructions(text: str) -> list[tuple[int, str]]:
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if any(marker in line for marker in FETCHES)
    ]


def _current() -> str:
    return rite_ai.__version__


@pytest.mark.parametrize("relative", BEARERS)
def test_no_file_mentions_a_version_that_is_not_this_one(relative: str):
    path = ROOT / relative
    assert path.is_file(), f"{relative} is gone — update BEARERS or this test"

    current = _current()
    wrong: list[tuple[int, str]] = []
    for number, line in _instructions(path.read_text()):
        for found in ANY_VERSION.findall(line):
            if found != current:
                wrong.append((number, line.strip()[:110]))
    assert not wrong, (
        f"{relative} names a version that is not {current}:\n"
        + "\n".join(f"  line {n}: {text}" for n, text in wrong)
        + f"\n\n`VERSION` says {current}. A release cut with these left "
        "behind serves the previous release's instructions from the new "
        "release's page — see docs/releasing.md step 1 for the full list."
    )


def test_the_check_is_looking_at_something():
    """The premise. If these files stop carrying version strings, the test
    above passes while asserting nothing — and would keep passing after a
    rename or a rewrite that moved the install instructions elsewhere."""
    total = sum(
        len(ANY_VERSION.findall(line))
        for rel in BEARERS
        if (ROOT / rel).is_file()
        for _, line in _instructions((ROOT / rel).read_text())
    )
    assert total >= 5, (
        f"only {total} versioned fetch/checkout instructions across "
        f"{list(BEARERS)} — either they moved, or the markers in FETCHES no "
        "longer match how they are written, and this check is reading nothing"
    )


def test_it_agrees_with_the_version_the_cli_reports():
    """`rite_ai.__version__` reads the `VERSION` file, and every assertion
    above rests on that being the single source of truth SPEC §8.5 says it
    is. If those two ever diverge, this test is comparing files against
    something other than the release."""
    assert (ROOT / "VERSION").read_text().strip() == _current()
