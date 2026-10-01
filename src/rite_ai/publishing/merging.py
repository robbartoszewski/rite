"""Watching the pull requests rite opened, and `auto_merge` (PB1 piece 5).

Every pull request `rite deliver` opens or finds is watched, once per
supervisor cycle, until it is merged or closed:

- **Merged**: the Manager is told, and the Worker's claims are released.
  D-41 holds a claim until the work lands, and under `pull_request` it lands
  here. This closes piece 2's gap, where nothing released them.
- **Closed unmerged**: the Manager is told. The claims stay held, because a
  closed PR is a person's decision and "abandon these paths" is not rite's
  inference to make.
- **Open, with `auto_merge`**: rite merges only when `merge_gate.refusal`
  allows it (a green on exactly the head rite pushed, which contains the
  base's live tip, on a base branch that requires up-to-date, with the
  publish gate checked by name), and then only with `gh pr merge
  --match-head-commit <that head>`, so GitHub refuses a head that moved in
  between.

🔴 **Read once, and a later read only takes away** (design §3.2). A PR is
merged only when BOTH the settings the Worker was started under AND the
config parsed at this attempt say `auto_merge` for that module. Turning it
off stops the next attempt; turning it on never reaches a PR opened without
it. A config that cannot be parsed means no merge.

**Told once per reason.** A PR waiting on checks is refused every cycle for
the same reason, so the Manager is told when the reason CHANGES, not every
two seconds. The reason told is kept with the entry.

**Where the state lives**: beside the start records (`record._records_dir`),
outside every Manager's grant, one JSON file per checkout. A file that
cannot be read watches nothing and says so every cycle, never silently
empty.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rite_ai.state import locked, write_atomic

WATCH_FILE = "watched.json"
ABOUT = "a pull request rite opened"


@dataclass
class Watched:
    worker: str
    ticket: str
    module: str
    repo: str  # owner/name
    number: int
    head: str  # the exact head rite pushed
    manager: str  # "" when a person ran `rite deliver`
    auto_merge_at_start: bool
    last_said: str = ""


def _path(root: Path) -> Path:
    from rite_ai.publishing.record import _records_dir

    return _records_dir(root) / WATCH_FILE


def _load(root: Path) -> list[Watched] | str:
    path = _path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Watched(**entry) for entry in raw["watched"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return f"{path} cannot be read ({e}); no pull request is being watched"


def _save(root: Path, entries: list[Watched]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"watched": [asdict(e) for e in entries]}
    write_atomic(path, json.dumps(body, indent=2, sort_keys=True) + "\n")


def watch(root: Path, entry: Watched) -> None:
    """Start watching `entry`'s PR, replacing any entry for the same PR.

    Under `state.locked`: a person's `rite deliver` and the supervisor's
    tick can write at once, and an unlocked read-modify-write loses one.
    An unreadable file is not overwritten: that would erase every PR it
    held. It raises, and the delivery's caller reports it."""
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        found = _load(root)
        if isinstance(found, str):
            raise OSError(found)
        entries = [e for e in found if (e.repo, e.number) != (entry.repo, entry.number)]
        _save(root, [*entries, entry])


def _auto_merge_now(root: Path, module: str) -> bool:
    """Does the config parsed NOW say auto_merge for `module`? False when it
    cannot be parsed: no answer is not a yes."""
    from rite_ai.config.parse import ParseError, parse_config, parse_modules
    from rite_ai.publishing.settings import effective

    config = parse_config(root / ".rite" / "config.yaml")
    modules = parse_modules(root / ".rite" / "modules.yaml")
    if isinstance(config, ParseError) or isinstance(modules, ParseError):
        return False
    for m in modules:
        if m.name == module:
            return effective(config.publish, m).merges
    return False


def _merge(entry: Watched) -> tuple[bool, str]:
    """`gh pr merge`, pinned to the head rite pushed."""
    import subprocess

    done = subprocess.run(
        [
            "gh",
            "pr",
            "merge",
            str(entry.number),
            "--repo",
            entry.repo,
            "--merge",
            "--match-head-commit",
            entry.head,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    said = (done.stderr or done.stdout or "").strip().splitlines()
    return done.returncode == 0, (said[-1] if said else f"exit {done.returncode}")


def _release_claims(root: Path, worker: str) -> str:
    from rite_ai.publishing.deliver import _release_claims as release

    return release(root, worker)


def tick(root: Path, manager: str, say, read=None, merge=None) -> None:
    """One pass over the PRs `manager` asked rite to deliver.

    `read` and `merge` replace `merge_gate.read_facts` and `_merge` in a
    test; everything else is the real path."""
    merge = merge or _merge
    root = Path(root)
    path = _path(root)
    if not path.exists():
        return
    with locked(path):
        _tick(root, manager, say, read, merge)


def _tick(root: Path, manager: str, say, read, merge) -> None:
    from rite_ai.managers.telling import tell_manager
    from rite_ai.publishing import merge_gate

    found = _load(root)
    if isinstance(found, str):
        say(found)
        return

    def tell(entry: Watched, text: str) -> None:
        say(f"{entry.manager or 'rite deliver'}: {text}")
        if entry.manager:
            try:
                tell_manager(root, entry.manager, ABOUT, text)
            except OSError as e:
                say(f"could not tell {entry.manager!r} about PR #{entry.number}: {e}")

    kept: list[Watched] = []
    for entry in found:
        # A PR a person delivered (`rite deliver`, no Manager) is watched by
        # whichever supervisor is running, and said to its terminal only:
        # otherwise nothing would ever release its claims.
        if entry.manager not in (manager, ""):
            kept.append(entry)
            continue
        where = f"PR #{entry.number} ({entry.worker}, {entry.ticket}, {entry.module})"
        try:
            if read is None:
                facts = merge_gate.read_facts(entry.repo, entry.number)
            else:
                facts = read(entry.repo, entry.number)
        except Exception as e:  # noqa: BLE001 — said, and the entry is kept
            reason = f"{where}: rite could not read it from GitHub ({e})"
            if reason != entry.last_said:
                tell(entry, reason)
                entry.last_said = reason
            kept.append(entry)
            continue
        if facts.merged:
            released = _release_claims(root, entry.worker)
            tell(entry, f"{where} is merged; {released}")
            continue
        if facts.state != "open":
            tell(
                entry,
                f"{where} was closed without merging. {entry.worker}'s claims "
                f"are still held: `rite release --worker {entry.worker}` if the "
                "work is abandoned",
            )
            continue
        allowed = entry.auto_merge_at_start and _auto_merge_now(root, entry.module)
        if not allowed:
            kept.append(entry)  # the User merges; rite watches for it
            continue
        refused = merge_gate.refusal(facts, entry.head)
        if refused:
            reason = f"{where} not merged yet: {refused}"
            if reason != entry.last_said:
                tell(entry, reason)
                entry.last_said = reason
            kept.append(entry)
            continue
        ok, said = merge(entry)
        if ok:
            # Merged by rite; the next pass sees `merged` and releases the
            # claims, from GitHub's own answer rather than from ours.
            tell(entry, f"{where} merged by rite on head {entry.head[:7]}")
            entry.last_said = "merged"
        else:
            tell(entry, f"{where}: GitHub refused the merge: {said}")
            entry.last_said = said
        kept.append(entry)
    _save(root, kept)
