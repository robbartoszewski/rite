"""`yoloai ls --json` is read ONCE, and every "I could not ask" survives it.

🔴 **The SCRUM-64 follow-up review's cost finding.** `reconcile` called
`worker_sandbox_status` once PER WORKER, and each call is a subprocess with a
30-second timeout — the very per-start cost the SCRUM-64 commit message gives
as its reason for keeping the self-test reap off the `rite start` path, added
back by the thing that replaced it.

So the parse moved into `list_sandboxes`, one call, and `SandboxListing`
answers per Worker from it. This file tests that parser directly, because the
four "I could not ask" answers are exactly the part a second reader of that
output gets wrong — `sandbox_status_named`'s own docstring says so — and a
listing that conflated "could not ask" with "there is no sandbox" would make
`reconcile` release a live Worker's claim.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from rite_ai.sandbox import (
    SandboxListing,
    list_sandboxes,
    sandbox_status_named,
)


def _yoloai(monkeypatch, *, stdout="", returncode=0, stderr="", binary="/bin/yoloai"):
    monkeypatch.setattr("rite_ai.sandbox._yoloai_binary", lambda: binary)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _listing(*names_and_statuses):
    return json.dumps(
        {
            "sandboxes": [
                {"environment": {"name": n}, "status": s} for n, s in names_and_statuses
            ]
        }
    )


# --- 1. the matching is EXACT ----------------------------------------------------


def test_a_name_the_listing_does_not_hold_is_not_found():
    """⚠ Not "whatever is in the listing". A fleet has several sandboxes, and
    answering about the wrong one is how a live Worker's claim gets released."""
    listing = SandboxListing({"rite-alpha": "active", "rite-beta": "idle"})
    assert listing.status_of("rite-alpha").value == "active"
    assert listing.status_of("rite-beta").value == "idle"
    assert listing.status_of("rite-gamma").value == "not found"
    assert listing.status_of({"rite-gamma", "rite-delta"}).value == "not found"


def test_an_empty_listing_is_not_found_for_everything():
    assert SandboxListing({}).status_of("anything").value == "not found"


def test_either_name_matches_so_a_legacy_sandbox_is_still_seen():
    """`worker_sandbox_status` asks under both the current and the legacy
    name; a sandbox started by an older rite must not read as gone."""
    listing = SandboxListing({"legacy-alpha": "active"})
    assert listing.status_of({"rite-alpha", "legacy-alpha"}).value == "active"


# --- 2. "I could not ask" survives the listing ----------------------------------


def test_a_listing_that_could_not_be_taken_is_known_False_for_every_name():
    """🔴 The answer that must not be flattened. `known=False` is not "no
    sandbox exists": `reconcile` releases a claim only on `not found`, and a
    listing that answered `not found` here would release the claims of every
    live Worker the moment yoloAI could not be reached."""
    listing = SandboxListing({}, known=False, unknown="yoloai not found")
    got = listing.status_of("rite-alpha")
    assert got.known is False
    assert got.value == "yoloai not found"


def test_yoloai_not_on_PATH(monkeypatch):
    _yoloai(monkeypatch, binary=None)
    got = list_sandboxes()
    assert got.known is False
    assert got.by_name == {}
    assert "yoloai not found" in got.unknown


def test_a_non_zero_exit_is_not_an_empty_listing(monkeypatch):
    _yoloai(monkeypatch, returncode=2, stderr="daemon unreachable")
    got = list_sandboxes()
    assert got.known is False, "a failed `yoloai ls` is not 'there are none'"
    assert got.by_name == {}
    assert "exited 2" in got.unknown and "daemon unreachable" in got.unknown
    assert got.status_of("rite-alpha").known is False


def test_output_that_is_not_json(monkeypatch):
    _yoloai(monkeypatch, stdout="not json at all")
    got = list_sandboxes()
    assert got.known is False and "did not return JSON" in got.unknown


def test_json_that_is_not_an_object(monkeypatch):
    _yoloai(monkeypatch, stdout="[1, 2, 3]")
    got = list_sandboxes()
    assert got.known is False and "unexpected JSON" in got.unknown


def test_a_long_error_is_bounded(monkeypatch):
    _yoloai(monkeypatch, returncode=1, stderr="x" * 5000)
    got = list_sandboxes()
    assert got.known is False
    assert len(got.unknown) < 400, "an unbounded error reaches a Manager's inbox"


# --- 3. the parse itself ---------------------------------------------------------


def test_every_sandbox_in_the_output_is_in_the_listing(monkeypatch):
    _yoloai(
        monkeypatch,
        stdout=_listing(
            ("rite-a", "active"), ("rite-b", "stopped"), ("rite-c", "idle")
        ),
    )
    got = list_sandboxes()
    assert got.known is True
    assert got.by_name == {"rite-a": "active", "rite-b": "stopped", "rite-c": "idle"}


def test_an_entry_with_no_name_is_skipped_not_fatal(monkeypatch):
    _yoloai(
        monkeypatch,
        stdout=json.dumps(
            {
                "sandboxes": [
                    {"environment": {}, "status": "active"},
                    {"status": "idle"},
                    {"environment": {"name": "rite-a"}, "status": "active"},
                ]
            }
        ),
    )
    got = list_sandboxes()
    assert got.known is True
    assert got.by_name == {"rite-a": "active"}


def test_an_entry_with_no_status_reads_unknown_not_missing(monkeypatch):
    """⚠ Present with an unreadable status is not absent. `reconcile` releases
    only on `not found`, and `unknown` is not that."""
    _yoloai(
        monkeypatch, stdout=json.dumps({"sandboxes": [{"environment": {"name": "r"}}]})
    )
    got = list_sandboxes()
    assert got.by_name == {"r": "unknown"}
    assert got.status_of("r").value == "unknown"


def test_no_sandboxes_key_is_an_empty_listing_that_IS_known(monkeypatch):
    """A yoloAI that answers properly and holds nothing is a real answer."""
    _yoloai(monkeypatch, stdout=json.dumps({"sandboxes": []}))
    got = list_sandboxes()
    assert got.known is True and got.by_name == {}


# --- 4. the one-call property ----------------------------------------------------


def test_the_listing_is_ONE_subprocess(monkeypatch):
    calls = _yoloai(monkeypatch, stdout=_listing(("rite-a", "active")))
    got = list_sandboxes()
    assert len(calls) == 1
    assert calls[0][1:] == ["ls", "--json"]
    # And answering about six Workers from it costs nothing more.
    for n in range(6):
        got.status_of(f"rite-{n}")
    assert len(calls) == 1


def test_sandbox_status_named_is_the_same_answer_through_one_call(monkeypatch):
    """The wrapper every existing caller uses is unchanged in behaviour — it
    is `list_sandboxes` plus `status_of`, so there is still ONE parse of that
    output in rite (`sandbox_status_named`'s own docstring)."""
    calls = _yoloai(monkeypatch, stdout=_listing(("rite-a", "active")))
    assert sandbox_status_named("rite-a").value == "active"
    assert sandbox_status_named("rite-zz").value == "not found"
    assert len(calls) == 2, "one call each, which is what a single asker wants"


@pytest.mark.parametrize(
    "status, gone",
    [("not found", True), ("stopped", False), ("active", False), ("idle", False)],
)
def test_reconcile_reads_gone_from_the_listing(tmp_path, monkeypatch, status, gone):
    """End to end against the thing that depends on it: only `not found` is
    gone, and a `stopped` sandbox still holds its copy."""
    from rite_ai.managers import reconcile
    from rite_ai.sandbox import sandbox_name

    by_name = {} if status == "not found" else {sandbox_name("alpha", tmp_path): status}
    assert (
        reconcile._sandbox_gone(tmp_path, "alpha", lambda: SandboxListing(by_name))
        is gone
    )
