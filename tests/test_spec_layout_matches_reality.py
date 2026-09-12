"""SPEC §8's `.rite/` layout, checked against the files rite writes.

That block is not decoration. Its own comment says the runtime half is
"Listed because 'what is this file?' is otherwise unanswerable" — so the
block IS the answer to that question, and a name missing from it, or a name
in it that rite never writes, is the failure the block exists to prevent.

It had drifted both ways at once. `handover.json` was listed and is the
LEGACY unkeyed path — `rite_ai.handover` still reads it and has not written
it since the per-session `handover/<name>.json` replaced it, so the one
entry the block offered for handover state named the file a user would not
find, while the directory they WOULD find was absent. Five more real files
were missing outright.

Derived from the modules' own filename constants rather than from a list
kept here, for the reason `scaffold.py` gives about the gitignore block it
replaced: a second list is a second thing to forget. A new runtime file
gets a constant, and this test asks for it by name the day it appears.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _layout_block() -> str:
    """SPEC §8's `.rite/` tree — the fenced block after its lead-in."""
    spec = (REPO_ROOT / "SPEC.md").read_text()
    lead = "`.rite/` holds configuration files and two knowledge directories:"
    start = spec.index(lead)
    fence = spec.index("```", start)
    end = spec.index("```", fence + 3)
    return spec[fence + 3 : end]


def _runtime_names() -> dict[str, str]:
    """The durable names the code itself defines, by the constant that
    defines them. Import-time values, so a rename moves the requirement."""
    from rite_ai.coordination_cost import _FILENAME as COORDINATION_COST
    from rite_ai.handover import SNAPSHOT_DIRNAME
    from rite_ai.pool import ARCHIVE_FILENAME, CLAIMS_FILENAME
    from rite_ai.pool import STATE_FILENAME as POOL_STATE
    from rite_ai.scheduler import LAST_TICK_FILENAME
    from rite_ai.scheduler import STATE_FILENAME as SCHEDULE_STATE
    from rite_ai.scheduler.lock import LOCK_FILENAME
    from rite_ai.scheduler.logfile import LOG_FILENAME
    from rite_ai.update import SCHEMA_VERSION_FILE

    return {
        "pool.CLAIMS_FILENAME": CLAIMS_FILENAME,
        "pool.STATE_FILENAME": POOL_STATE,
        "pool.ARCHIVE_FILENAME": ARCHIVE_FILENAME,
        "handover.SNAPSHOT_DIRNAME": SNAPSHOT_DIRNAME + "/",
        "scheduler.STATE_FILENAME": SCHEDULE_STATE,
        "scheduler.LAST_TICK_FILENAME": LAST_TICK_FILENAME,
        "scheduler.lock.LOCK_FILENAME": LOCK_FILENAME,
        "scheduler.logfile.LOG_FILENAME": LOG_FILENAME,
        "coordination_cost._FILENAME": COORDINATION_COST,
        "update.SCHEMA_VERSION_FILE": SCHEMA_VERSION_FILE,
    }


@pytest.mark.parametrize(("constant", "filename"), sorted(_runtime_names().items()))
def test_every_file_rite_writes_is_named_in_the_layout(constant, filename):
    assert filename in _layout_block(), (
        f"{filename} ({constant}) is written into `.rite/` and SPEC §8's "
        f"layout does not name it — the block whose stated job is answering "
        f"'what is this file?'"
    )


def test_the_layout_does_not_offer_the_legacy_handover_path():
    """The drift that motivated this file, asserted in the direction a
    presence check cannot see. `handover.json` is still a real constant —
    `rite_ai.handover` READS it so a project written by an older rite keeps
    working — so a test that merely looked for the string would pass on the
    stale block. What must not happen is the layout offering it as the
    place handover state lives, with no mention of the directory that
    actually holds it."""
    from rite_ai.handover import SNAPSHOT_DIRNAME, SNAPSHOT_FILENAME

    block = _layout_block()
    assert f"{SNAPSHOT_DIRNAME}/" in block, "the written path is absent"

    # It may be MENTIONED, but only as the legacy path it is. Checked over
    # a window rather than a line: this is a wrapped comment inside an
    # ASCII tree, so "legacy" and the filename need not share a line.
    for match in re.finditer(re.escape(SNAPSHOT_FILENAME), block):
        window = block[max(0, match.start() - 200) : match.end() + 200]
        assert "legacy" in window.lower(), (
            f"SPEC offers {SNAPSHOT_FILENAME} as a current path, near: "
            f"{window.strip()!r}"
        )


def test_the_lock_sidecars_are_accounted_for():
    """`state.locked()` puts a `<name>.lock` beside every durable file it
    guards, so a project contains several and none was named. A user who
    finds one and reads SPEC to learn what it is needs the block to say."""
    assert ".lock" in _layout_block(), (
        "the sidecars `rite_ai.state.locked()` creates are unexplained"
    )


def test_the_block_is_still_a_tree_and_not_prose():
    """Cheap shape guard: the edits above are hand-made, and a mangled
    fence would make every assertion here vacuous."""
    block = _layout_block()
    assert block.count("├──") + block.count("└──") > 10
    assert re.search(r"^\.rite/$", block, re.MULTILINE)
