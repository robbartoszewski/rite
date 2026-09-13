"""Machine-level answers `rite init` should not ask twice.

Almost everything `rite init` asks is per-project and should be asked
again for the next project. One thing is not: whether to install yoloAI.
That is a fact about this machine, and the same answer serves every
project on it.

So the two questions are stored differently on purpose. "Run Workers in
sandboxes?" is written to that project's `config.yaml` and asked afresh
every time, because a person may genuinely want it on for one project and
off for another. "Install yoloAI?" is recorded here, once, because asking
someone to re-decline a machine-wide install in every new project is how a
tool starts feeling like it is nagging rather than paying attention.

Declining suppresses the OFFER, never the sandbox question itself — the
setting can still be turned on, and `rite doctor` will then say the
sandbox is not working until yoloAI is there. Someone who installs it
later is simply in the ordinary case again and never meets this file.

Delete `~/.rite/init-prefs.json` to be asked again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FILENAME = "init-prefs.json"
_DECLINED_KEY = "yoloai_install_declined_at"


def _path() -> Path:
    from rite_ai.credentials.store import default_rite_home

    return default_rite_home() / FILENAME


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def yoloai_install_declined() -> bool:
    return _DECLINED_KEY in _read()


def record_yoloai_install_declined() -> None:
    """Remember the decline. Never raises: failing to record a preference
    must not fail the `rite init` it was asked during — the cost is being
    asked again, which is the status quo anyway."""
    path = _path()
    data = _read()
    data[_DECLINED_KEY] = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass
