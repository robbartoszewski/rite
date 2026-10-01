"""Did Ollama cut a local Manager's prompt during a cycle? Said, or "can't tell".

WHY THIS EXISTS. Observed 2026-09-28 (plan, Track MS) with `qwen3:8b`
pinned to 40,960 tokens: a prompt of 79,296 tokens was cut to 20,482, half
the window, FROM THE FRONT. Ollama reported a normal finish, the model
answered from the fragment, and Goose exited 0 with a wrong answer.
Everything in the chain returned success. One line in Ollama's own log was
the only record:

    level=WARN source=llama_server.go:318 msg="truncating input prompt"
    limit=20482 prompt=79296 keep=4 new=20482

So this reads that log and says what it found. It answers exactly one of:

- **cut**: the log records a truncation inside the cycle, with its numbers;
- **clean**: established, not assumed (below);
- **can't tell**, with why.

⚠ **"Clean" is claimed only on positive evidence**, because a detector that
fails silent is the failure it exists to fix. Every one of these must hold,
or the answer is "can't tell":

1. the endpoint is on THIS machine (a remote server's log is on that
   machine, and saying "clean" would imply a look that never happened);
2. the log file is readable and its span covers the whole cycle (its first
   timestamped line is no later than the cycle's start; a log rotated during
   the cycle does not);
3. the server reports a version the line format was VERIFIED against
   (`VERIFIED_OLLAMA`). On any other version, a missing truncation line might
   only mean the wording changed;
4. the log records at least one model request completing INSIDE the cycle
   (Ollama's `[GIN] … POST "/v1/chat/completions"` line), so it is this
   server's live log and not a stale file. `ollama serve` started in a
   terminal logs to stdout, and `server.log` then records nothing;
5. no line inside the cycle mentions truncation in a form the pattern does
   not recognise. Such a line makes the whole answer "can't tell", loudly.
   A line with no timestamp of its own (llama.cpp's slot lines) is taken to be
   at the time of the line before it, so it is checked too.

⚠ **A CUT IS THE SERVER'S, NOT THE MANAGER'S.** Nothing in the log says who
sent a prompt: the truncation line has no model, request id or port, and the
request line has only `127.0.0.1` and the path, the same for every local
client. Checked in the real log, 2026-09-28. Several Managers sharing one
Ollama server is the ordinary case since 0.6.0, and their lines interleave.
So a cut is reported as "on this server during this Manager's cycle", with the
other Managers in the project on the same endpoint named. Anything else using
the server is invisible to rite. Telling Manager A its prompt was cut when it
was B's would be a confident wrong answer, worse than silence.

⚠ **THE LOG'S STAMPS ARE WALL-CLOCK ONLY.** `time=` carries a zone offset;
the `[GIN]` request line is local time with no zone. A monotonic clock cannot
be compared with them, so the cycle is bounded in wall-clock time. It is
CHECKED against a monotonic measurement of the same cycle: if the two elapsed
times disagree (the clock stepped: NTP, sleep, a manual change), or the local
offset changed during the cycle (daylight saving, which makes the zone-less
`[GIN]` stamps ambiguous), the answer is "cannot tell". The same hazard made a
test fail for one minute every night (#40).

⚠ **Linux** usually runs Ollama under systemd, which logs to the journal,
not a file. Unless `RITE_OLLAMA_LOG` names a file, the answer there is "can't
tell".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

VERIFIED_OLLAMA = frozenset({"0.34.2"})
"""Ollama server versions whose truncation line this module was checked
against, by running one: 0.34.2 on macOS, 2026-09-28. Add a version here only
after seeing its line match `_CUT` in a real log. An upgrade that is not
listed makes every clean answer "can't tell", which is the intended failure:
loud, not a quiet return to no detection."""

LOG_ENV = "RITE_OLLAMA_LOG"
_DEFAULT_LOG = Path.home() / ".ollama" / "logs" / "server.log"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CLOCK_TOLERANCE_SECONDS = 2.0
"""How far the wall-clock and monotonic lengths of a cycle may differ before
the wall-clock window is not trusted. Scheduling jitter is milliseconds; a
clock step is seconds or hours."""
_READ_LIMIT = 32 * 1024 * 1024
"""The tail read from the log. If the cycle started before the oldest line
in it, the answer is "can't tell", not a partial "clean"."""

_TIME = re.compile(r"\btime=(\S+)")
_GIN = re.compile(r"^\[GIN\] (\d{4}/\d\d/\d\d - \d\d:\d\d:\d\d) \|")
_MODEL_REQUEST = re.compile(r'POST\s+"/(?:v1/chat/completions|api/chat|api/generate)"')
_CUT = re.compile(
    r'msg="truncating input prompt"\s+'
    r"limit=(\d+)\s+prompt=(\d+)\s+keep=(\d+)\s+new=(\d+)"
)
_MENTIONS = re.compile(r"truncat", re.IGNORECASE)
# llama.cpp's own per-slot report, "stop processing: ... truncated = 0". It
# says nothing about Ollama's prompt truncation, so it is not a line this
# module fails to recognise; it is a line it knows is irrelevant.
_IRRELEVANT = re.compile(r"slot\s+release:.*truncated = \d+")


@dataclass(frozen=True)
class Cut:
    at: float
    sent: int
    """Tokens in the prompt as sent."""
    kept: int
    """Tokens the model was given."""


@dataclass
class Verdict:
    known: bool
    cuts: list[Cut] = field(default_factory=list)
    why: str = ""
    """Why it cannot tell, when `known` is False."""

    @property
    def cut(self) -> bool:
        return self.known and bool(self.cuts)


def is_local(endpoint: str) -> bool:
    return (urlsplit(endpoint).hostname or "") in _LOCAL_HOSTS


def _log_path() -> Path:
    override = os.environ.get(LOG_ENV)
    return Path(override) if override else _DEFAULT_LOG


def _default_get(url: str):
    """The real fetch. A module function, not a closure, so the test suite
    can replace it and no test ever reaches a real Ollama server."""
    import httpx

    return httpx.get(url, timeout=5.0)


def _server_version(endpoint: str, get) -> str:
    base = endpoint.rstrip("/").removesuffix("/v1")
    try:
        return str(get(base + "/api/version").json().get("version") or "")
    except Exception:  # noqa: BLE001 - any failure is "cannot tell which"
        return ""


def _offset(at: float):
    return datetime.fromtimestamp(at).astimezone().utcoffset()


def _when(line: str) -> float | None:
    """The line's own time: `time=<ISO>` on Ollama's lines, or the `[GIN]`
    request log's local-time stamp. None for a line with neither."""
    found = _TIME.search(line)
    if found:
        try:
            return datetime.fromisoformat(found.group(1)).timestamp()
        except ValueError:
            return None
    gin = _GIN.search(line)
    if gin:
        try:
            # Local time with no zone, as Ollama writes it; read as local.
            return datetime.strptime(gin.group(1), "%Y/%m/%d - %H:%M:%S").timestamp()
        except ValueError:
            return None
    return None


def check_cycle(
    endpoint: str,
    started_at: float,
    ended_at: float,
    *,
    get=None,
    log: Path | None = None,
    monotonic_elapsed: float | None = None,
) -> Verdict:
    """What Ollama's log says about prompts it cut between the two times.
    Never raises.

    `monotonic_elapsed` is the same cycle's length on a monotonic clock. When
    given, a wall clock that moved during the cycle answers "cannot tell"."""
    if not is_local(endpoint):
        return Verdict(
            False,
            why=(
                f"the endpoint {endpoint} is not on this machine, and Ollama "
                "records cut prompts only in its own log, on the machine it "
                "runs on"
            ),
        )
    if monotonic_elapsed is not None and (
        abs((ended_at - started_at) - monotonic_elapsed) > CLOCK_TOLERANCE_SECONDS
    ):
        return Verdict(
            False,
            why=(
                f"the wall clock moved during the cycle (it measured "
                f"{ended_at - started_at:.0f}s where the cycle lasted "
                f"{monotonic_elapsed:.0f}s), and Ollama's log is stamped in "
                "wall-clock time, so which lines fall in the cycle is not known"
            ),
        )
    if _offset(started_at) != _offset(ended_at):
        return Verdict(
            False,
            why=(
                "the local time's offset changed during the cycle (daylight "
                "saving), and Ollama's request lines are stamped in local time "
                "with no zone, so which of them fall in the cycle is not known"
            ),
        )
    if get is None:
        get = _default_get

    version = _server_version(endpoint, get)
    if version not in VERIFIED_OLLAMA:
        return Verdict(
            False,
            why=(
                f"this Ollama server reports version {version or 'unknown'}, "
                f"and the log line rite reads was verified only on "
                f"{', '.join(sorted(VERIFIED_OLLAMA))}. A changed wording would "
                "look exactly like no cut, so rite does not read it as one"
            ),
        )

    path = log or _log_path()
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            handle.seek(max(size - _READ_LIMIT, 0))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as e:
        return Verdict(False, why=f"Ollama's log {path} cannot be read ({e})")

    lines = text.splitlines()
    if size > _READ_LIMIT and lines:
        lines = lines[1:]  # the first may be a partial line from the seek
    first = next((t for t in map(_when, lines) if t is not None), None)
    if first is None or first > started_at:
        return Verdict(
            False,
            why=(
                f"Ollama's log {path} does not reach back to the start of the "
                "cycle (it was rotated, or rite read only its tail), so a cut "
                "early in the cycle would not be in it"
            ),
        )

    cuts: list[Cut] = []
    unrecognised: list[str] = []
    requests = 0
    at: float | None = None
    for line in lines:
        at = _when(line) or at
        if at is None or not (started_at <= at <= ended_at):
            continue
        if _GIN.search(line) and _MODEL_REQUEST.search(line):
            requests += 1
        matched = _CUT.search(line)
        if matched:
            cuts.append(
                Cut(at=at, sent=int(matched.group(2)), kept=int(matched.group(4)))
            )
        elif _MENTIONS.search(line) and not _IRRELEVANT.search(line):
            unrecognised.append(line.strip()[:200])
    if unrecognised:
        return Verdict(
            False,
            why=(
                "Ollama's log mentions truncation in a form rite does not "
                f"recognise, so it cannot say what was cut: {unrecognised[0]}"
            ),
        )
    if not requests and not cuts:
        return Verdict(
            False,
            why=(
                f"Ollama's log {path} records no model request during the "
                "cycle, so it may not be this server's log (`ollama serve` in "
                "a terminal logs to the terminal, not to this file)"
            ),
        )
    return Verdict(True, cuts=cuts)


def describe(
    manager: str, window: int, verdict: Verdict, sharing: tuple[str, ...] = ()
) -> str:
    """The line the supervisor says, or "" for an established clean cycle.

    `sharing` names the project's other Managers on the same endpoint. A cut
    is the SERVER'S (module docstring), so the line says whose it might be
    rather than whose it is."""
    if not verdict.known:
        return f"cannot tell whether Ollama cut {manager!r}'s prompts: {verdict.why}"
    if not verdict.cuts:
        return ""
    worst = max(verdict.cuts, key=lambda c: c.sent - c.kept)
    others = (
        f"{', '.join(repr(m) for m in sharing)} in this project also use this "
        "endpoint, so it may be theirs"
        if sharing
        else "no other Manager in this project uses this endpoint"
    )
    return (
        f"⚠ Ollama's log records {len(verdict.cuts)} prompt(s) CUT on this "
        f"server during {manager!r}'s cycle, with no error: the largest was "
        f"{worst.sent:,} tokens, of which {worst.kept:,} were kept, from the "
        f"end, so the start was dropped. The log does not say which caller "
        f"sent it: {others}, and anything else using this Ollama server is "
        f"invisible to rite. If it was {manager!r}'s, its {window:,}-token "
        "window is too small for what it was given."
    )
