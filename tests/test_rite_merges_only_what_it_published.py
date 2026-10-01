"""`auto_merge`, and watching the pull requests rite opened (PB1 piece 5).

The properties:
- rite merges only when the settings the Worker started under AND the config
  read at that attempt both allow it: a change can revoke, never grant;
- only through `merge_gate` (tested shape by shape in `test_merge_gate.py`
  and observed against GitHub there), and only pinned to the head it pushed;
- a merge seen on GitHub releases the Worker's claims (D-41), a close does
  not, and the Manager hears each once, not every cycle.

Real: the watch file, the claims ledger, config parsing, `merge_gate.refusal`
and, in the last test, `gh` as a subprocess (a fake binary). Supplied: the
facts GitHub would return, through `tick`'s `read` seam.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.managers.mailbox import INBOX, delivery_note
from rite_ai.managers.mailbox import take as take_mail
from rite_ai.publishing import merging
from rite_ai.publishing.merge_gate import GATE_CHECK, Check, Facts

HEAD = "a" * 40
TIP = "c" * 40
MANAGER = "lead"


def _project(tmp_path: Path, auto_merge: bool = True) -> Path:
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "modules.yaml").write_text(
        "modules:\n  svc:\n    path: svc/\n    url: git@github.com:acme/svc.git\n"
    )
    _set(root, auto_merge)
    return root


def _set(root: Path, auto_merge: bool) -> None:
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\npublish:\n  strategy: pull_request\n"
        f"  auto_merge: {str(auto_merge).lower()}\n"
    )


def _watch(root: Path, *, at_start: bool = True, manager: str = MANAGER) -> None:
    merging.watch(
        root,
        merging.Watched(
            worker="alpha",
            ticket="KAN-8",
            module="svc",
            repo="acme/svc",
            number=7,
            head=HEAD,
            manager=manager,
            auto_merge_at_start=at_start,
        ),
    )


def _facts(**changes) -> Facts:
    green = Facts(
        number=7,
        state="open",
        merged=False,
        head_sha=HEAD,
        base_ref="main",
        base_tip=TIP,
        behind_by=0,
        mergeable=True,
        mergeable_state="clean",
        strict=True,
        checks=[
            Check("tests", HEAD, "completed", "success"),
            Check(GATE_CHECK, HEAD, "completed", "success"),
        ],
    )
    return dataclasses.replace(green, **changes)


class _GitHub:
    def __init__(self, facts: Facts):
        self.facts = facts
        self.merged: list[merging.Watched] = []

    def read(self, repo, number):
        return self.facts

    def merge(self, entry):
        self.merged.append(entry)
        return True, "merged"


def _tick(root: Path, gh: _GitHub, manager: str = MANAGER) -> list[str]:
    said: list[str] = []
    merging.tick(root, manager, said.append, read=gh.read, merge=gh.merge)
    return said


def _told(root: Path) -> str:
    return delivery_note(take_mail(root, MANAGER, INBOX))


def _watched(root: Path) -> list[merging.Watched]:
    found = merging._load(root)
    assert not isinstance(found, str)
    return found


# --- the conjunction ----------------------------------------------------------------


def test_a_green_pr_is_merged_when_both_reads_allow_it(tmp_path):
    root = _project(tmp_path, auto_merge=True)
    _watch(root, at_start=True)
    gh = _GitHub(_facts())
    _tick(root, gh)
    assert [e.head for e in gh.merged] == [HEAD]
    assert f"merged by rite on head {HEAD[:7]}" in _told(root)


def test_turning_auto_merge_off_mid_run_stops_the_next_attempt(tmp_path):
    root = _project(tmp_path, auto_merge=True)
    _watch(root, at_start=True)
    _set(root, auto_merge=False)
    gh = _GitHub(_facts())
    _tick(root, gh)
    assert gh.merged == []
    assert _watched(root), "still watched: the User merges, and rite sees it"


def test_turning_auto_merge_on_mid_run_never_reaches_this_pr(tmp_path):
    root = _project(tmp_path, auto_merge=True)
    _watch(root, at_start=False)
    gh = _GitHub(_facts())
    _tick(root, gh)
    assert gh.merged == []


def test_an_unreadable_config_at_the_attempt_is_not_a_yes(tmp_path):
    root = _project(tmp_path, auto_merge=True)
    _watch(root, at_start=True)
    (root / ".rite" / "config.yaml").write_text("publish: [\n")
    gh = _GitHub(_facts())
    _tick(root, gh)
    assert gh.merged == []


def test_a_stale_green_is_refused_and_told_once_per_reason(tmp_path):
    root = _project(tmp_path)
    _watch(root)
    gh = _GitHub(_facts(behind_by=2))
    _tick(root, gh)
    _tick(root, gh)
    first = _told(root)
    assert gh.merged == []
    assert first.count("stale-green shape 4") == 1, first
    gh.facts = _facts(mergeable=False, mergeable_state="dirty")
    _tick(root, gh)
    assert "shape 2" in _told(root)


# --- merged, closed, unreadable -----------------------------------------------------


def _claim(root: Path) -> ClaimsLedger:
    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    assert ledger.claim(["svc/a"], "alpha", ticket="KAN-8").ok
    return ledger


def test_a_merge_seen_on_github_releases_the_claims(tmp_path):
    root = _project(tmp_path, auto_merge=False)
    ledger = _claim(root)
    _watch(root, at_start=False)
    _tick(root, _GitHub(_facts(state="closed", merged=True)))
    assert ledger.claims_for("alpha") == []
    assert "is merged; released 1 claim(s)" in _told(root)
    assert _watched(root) == []


def test_a_close_without_merge_keeps_the_claims_and_says_how(tmp_path):
    root = _project(tmp_path)
    ledger = _claim(root)
    _watch(root)
    _tick(root, _GitHub(_facts(state="closed", merged=False)))
    assert len(ledger.claims_for("alpha")) == 1
    assert "rite release --worker alpha" in _told(root)
    assert _watched(root) == []


def test_github_unreadable_is_said_once_and_the_pr_stays_watched(tmp_path):
    root = _project(tmp_path)
    _watch(root)

    class Down(_GitHub):
        def read(self, repo, number):
            raise RuntimeError("gh api: exit 1")

    gh = Down(_facts())
    _tick(root, gh)
    _tick(root, gh)
    assert _told(root).count("could not read it from GitHub") == 1
    assert len(_watched(root)) == 1


def test_another_managers_pr_is_left_alone_and_a_persons_is_watched(tmp_path):
    root = _project(tmp_path)
    _watch(root, manager="other")
    gh = _GitHub(_facts(state="closed", merged=True))
    _tick(root, gh)
    assert len(_watched(root)) == 1  # not lead's
    merging.watch(root, dataclasses.replace(_watched(root)[0], manager="", number=8))
    said = _tick(root, gh)
    assert any("rite deliver: PR #8" in s for s in said)
    assert [e.manager for e in _watched(root)] == ["other"]


def test_an_unreadable_watch_file_is_never_overwritten(tmp_path):
    root = _project(tmp_path)
    _watch(root)
    path = merging._path(root)
    path.write_text("{not json")
    with pytest.raises(OSError):
        _watch(root)
    assert path.read_text() == "{not json"
    said = _tick(root, _GitHub(_facts()))
    assert any("cannot be read" in s for s in said)


# --- the real merge call ------------------------------------------------------------


def test_the_merge_is_pinned_to_the_head_rite_pushed(tmp_path, monkeypatch):
    log = tmp_path / "gh.log"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{gh.parent}:{os.environ['PATH']}")
    entry = merging.Watched("alpha", "KAN-8", "svc", "acme/svc", 7, HEAD, "", True)
    ok, _ = merging._merge(entry)
    assert ok
    assert log.read_text().split() == [
        "pr", "merge", "7", "--repo", "acme/svc", "--merge",
        "--match-head-commit", HEAD,
    ]  # fmt: skip
