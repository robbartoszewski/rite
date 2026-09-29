"""The publish settings a Worker was started under: read ONCE, at start.

**Why a snapshot.** A Worker started under `pull_request` may finish after
someone changed the project to `commit`. Honouring the start value pushes
code the User just said not to push; honouring the current one publishes
work started when it would have been reviewed. rite cannot tell which way a
change was meant, so it records what was in force at start, and the publish
step compares (`deliver`, design §3.1): **a change can take permission away
from in-flight work, never give it.**

⚠ **Not in the project tree, which every Manager may write.** A Manager's
enclosure grants the project directory (`managers/enclosure.compose`). The
record lives beside the mailboxes, under no path any profile grants
(`mailbox._mail_home`), keyed by checkout the same way.

⚠ **Absent and unreadable are different answers, and neither is "no
change".** `read` returns a `Record`, `None` (no record: a Worker started by
an older rite, or never started), or `Unreadable`. The publish step treats
both of the last two as divergence: it cannot say what the Worker was
started under, so it does the one thing safe under every strategy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.config.models import Module, ProjectConfig
from rite_ai.publishing.settings import Effective, effective
from rite_ai.state import write_atomic

RECORD_VERSION = 1


@dataclass(frozen=True)
class Record:
    worker: str
    ticket: str
    started_at: str
    modules: dict[str, dict]  # module -> {strategy, squash, auto_merge}


@dataclass(frozen=True)
class Unreadable:
    reason: str


def records_dir(root: Path) -> Path:
    """`<mail home>/../publish/<checkout>/`: outside every Manager's grant."""
    from rite_ai.managers.mailbox import _checkout_key, _mail_home  # noqa: PLC2701

    return _mail_home().parent / "publish" / _checkout_key(Path(root))


def _path(root: Path, worker: str) -> Path:
    from rite_ai.names import name_problem

    problem = name_problem(worker, kind="worker name")
    if problem:
        raise ValueError(f"refusing to build a publish record path: {problem}")
    return records_dir(root) / f"{worker}.json"


def settings(e: Effective) -> dict:
    """The part of `Effective` that decides what is done, without `source`:
    where a value came from is not a change in what it is."""
    return {"strategy": e.strategy, "squash": e.squash, "auto_merge": e.auto_merge}


def write(
    root: Path,
    worker: str,
    ticket: str,
    config: ProjectConfig,
    modules: list[Module],
    now: datetime | None = None,
) -> Path:
    """Record what `worker` is started under, for `ticket`, per module."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "version": RECORD_VERSION,
        "worker": worker,
        "ticket": ticket,
        "started_at": stamp,
        "modules": {m.name: settings(effective(config.publish, m)) for m in modules},
    }
    path = _path(root, worker)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(body, indent=2, sort_keys=True) + "\n")
    return path


def read(root: Path, worker: str) -> Record | Unreadable | None:
    path = _path(root, worker)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Unreadable(f"{path}: {e}")
    if not isinstance(raw, dict) or raw.get("version") != RECORD_VERSION:
        return Unreadable(f"{path}: not a version-{RECORD_VERSION} publish record")
    modules = raw.get("modules")
    fields = ("worker", "ticket", "started_at")
    if not isinstance(modules, dict) or not all(
        isinstance(raw.get(k), str) for k in fields
    ):
        return Unreadable(f"{path}: missing fields")
    for value in modules.values():
        if not isinstance(value, dict) or set(value) != {
            "strategy",
            "squash",
            "auto_merge",
        }:
            return Unreadable(f"{path}: a module's settings are malformed")
    return Record(
        worker=raw["worker"],
        ticket=raw["ticket"],
        started_at=raw["started_at"],
        modules=modules,
    )
