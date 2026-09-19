"""Limits that belong to THIS MACHINE, not to a project.

`sandbox.max_concurrent_workers` is per Manager by design (SPEC §2.5.9,
D-47 puts cross-project aggregation in the hub), and it lives in a
project's `config.yaml`, which is committed and shared. So it cannot
answer a different question: **how many sandboxes can this box run at
once, whoever is asking.** Four projects each correctly capped at five
give twenty Claude sessions on one laptop, and no project is at fault.

The same argument `.rite/machine` makes for Manager identity applies
here — a committed file cannot describe the machine it happens to be
checked out on — so this is machine-level state under `~/.rite/`, beside
`init-prefs.json`, which records the one other fact about this machine
rather than about a project.

**Unset means unbounded, and that is deliberate.** A default number here
would change the behaviour of every fleet already running on an upgrade,
silently, in the direction of refusing work. Whoever wants the bound sets
it; until then nothing is counted and nothing is refused, exactly as
today.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FILENAME = "machine.json"
MAX_SANDBOXES_KEY = "max_sandboxes"
MAX_SANDBOXES_ENV = "RITE_MAX_SANDBOXES"


def _path() -> Path:
    from rite_ai.credentials.store import default_rite_home

    return default_rite_home() / FILENAME


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def max_sandboxes() -> int | None:
    """The machine's sandbox bound, or None when nobody has set one.

    The environment wins, for the same reason it does everywhere else in
    this tool: it is the only channel that reaches a process which cannot
    read the file — and it is how a CI run or a one-off says "not on this
    box" without editing anyone's home directory.

    A value that is not a positive whole number is ignored rather than
    guessed at. A bound of 0 would mean "start nothing", which is a thing
    somebody might mean and a thing a typo produces, and the two are
    indistinguishable here; `rite doctor` is where an unusable setting
    should be argued about, not the path that starts work.
    """
    raw = os.environ.get(MAX_SANDBOXES_ENV)
    if raw is None:
        value = _read().get(MAX_SANDBOXES_KEY)
        raw = None if value is None else str(value)
    if raw is None:
        return None
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
