"""Keep `.rite/scheduler.log` from growing forever.

Every tick writes at least one line — "nothing to report" is deliberate, so
an idle scheduler cannot be mistaken for a dead one — and nothing ever
removed any of it. At the default five-minute cadence that is ~105,000 lines
a year, on a machine meant to run for months.

**Copy-and-truncate, not rename.** rite does not own the file handle here.
cron appends through a shell redirect (`>> scheduler.log`) and launchd holds
the path open as `StandardOutPath`. Renaming the file out from under an open
descriptor is the classic way to produce a log that appears rotated while the
writer keeps filling the renamed inode — from the operator's side, rotation
that silently stopped working. Copying the contents aside and truncating the
original in place keeps the inode, so both writers carry on into the file
they already hold. Both paths are covered because they are the same file
under both backends; there is no separate launchd-only path to miss.

The trade is a narrow window: a line written between the copy and the
truncate is lost. That is acceptable for a progress log and is not acceptable
for anything else, which is why this lives here and not in `rite_ai.state` —
durable state uses `write_atomic`, which loses nothing.
"""

from __future__ import annotations

from pathlib import Path

LOG_FILENAME = "scheduler.log"

# 1 MiB per file, the live file plus three archives — about 4 MiB total, or
# roughly a year of ticks at the default cadence before the oldest is
# dropped. Small enough that nobody has to think about it, large enough that
# an incident is still in the file when it is looked at.
MAX_BYTES = 1024 * 1024
KEEP = 3


def log_path(root: Path) -> Path:
    return root / ".rite" / LOG_FILENAME


def rotate_if_needed(
    path: Path, max_bytes: int = MAX_BYTES, keep: int = KEEP
) -> str | None:
    """Rotate when `path` is at or over `max_bytes`. Returns a one-line
    description when rotation happened, so the tick can report it, or None
    when there was nothing to do — which is almost always.

    Never raises: a scheduler must not die because it could not tidy its own
    log. A failure here leaves the log oversized, which is the condition we
    started from, not a worse one.
    """
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return None

        size = path.stat().st_size

        # Shift the archives down, oldest first so nothing is overwritten
        # before it has been moved. `.{keep}` falls off the end.
        oldest = path.with_suffix(path.suffix + f".{keep}")
        oldest.unlink(missing_ok=True)
        for i in range(keep - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            if src.is_file():
                src.replace(path.with_suffix(path.suffix + f".{i + 1}"))

        first = path.with_suffix(path.suffix + ".1")
        first.write_bytes(path.read_bytes())

        # Truncate in place — same inode, so cron's redirect and launchd's
        # StandardOutPath keep writing to the file they already hold open.
        with open(path, "r+") as f:
            f.truncate(0)

        return f"rotated scheduler.log ({size:,} bytes) to {first.name}"
    except OSError:
        return None
