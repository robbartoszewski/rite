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

⚠ **The log line does not name the model.** A cut during the cycle is
reported as "while this Manager's cycle ran", not as this Manager's own. On a
server shared with other clients that is the honest limit of what the log
says.

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


def _server_version(endpoint: str, get) -> str:
    base = endpoint.rstrip("/").removesuffix("/v1")
    try:
        return str(get(base + "/api/version").json().get("version") or "")
    except Exception:  # noqa: BLE001 - any failure is "cannot tell which"
        return ""


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
) -> Verdict:
    """What Ollama's log says about prompts it cut between the two times.
    Never raises."""
    if not is_local(endpoint):
        return Verdict(
            False,
            why=(
                f"the endpoint {endpoint} is not on this machine, and Ollama "
                "records cut prompts only in its own log, on the machine it "
                "runs on"
            ),
        )
    if get is None:
        import httpx

        def get(url: str):
            return httpx.get(url, timeout=5.0)

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


def describe(manager: str, window: int, verdict: Verdict) -> str:
    """The line the supervisor says, or "" for an established clean cycle."""
    if not verdict.known:
        return f"cannot tell whether Ollama cut {manager!r}'s prompts: {verdict.why}"
    if not verdict.cuts:
        return ""
    worst = max(verdict.cuts, key=lambda c: c.sent - c.kept)
    return (
        f"⚠ Ollama CUT a prompt {len(verdict.cuts)} time(s) while {manager!r}'s "
        f"cycle ran, with no error: the largest was {worst.sent:,} tokens, of "
        f"which the model was given {worst.kept:,}, from the END. The start "
        f"(where the instructions are) was dropped, so what it did may rest on "
        f"a fragment. Its window is {window:,}; raise context_window, or give "
        "it less at once. (The log does not name the model; another client of "
        "this Ollama server could be the source.)"
    )
