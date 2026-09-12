"""`WatchdogResult.unreadable`, and the merge hazard that motivates it.

Two lines of work fixed the same defect independently — a corrupt handover
snapshot read as "nothing to report". They were reconciled onto `_load`'s
approach (an unreadable file comes back as a SNAPSHOT carrying `unreadable`,
so every caller sees it) and the other approach — a separate scanner testing
`_load(...) is None` — was deleted rather than merged, because its premise
stopped being true the moment `None` was reserved for a vanished file.

Git found no conflict between them. A scanner keyed on `is None` would have
auto-merged, compiled, and returned `[]` forever.

`test_the_field_is_populated_not_silently_empty` is the guard for exactly
that class of failure: it asserts the STRUCTURED field, so a future change to
how corruption is detected cannot leave the reported prose intact while the
data behind it quietly empties. Asserting only on the printed line would not
have caught it — the user-facing text had a second source.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.handover import write_snapshot
from rite_ai.watchdog import run_watchdog_check


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return tmp_path


def _corrupt(root: Path, worker: str = "w1") -> Path:
    write_snapshot(
        root,
        ticket="BEN-86",
        progress="mid-task",
        next_step="",
        blockers=["should the column be nullable?"],
        worker=worker,
    )
    path = root / ".rite" / "handover" / f"{worker}.json"
    path.write_text("NOT JSON{")
    return path


def test_the_field_is_populated_not_silently_empty(tmp_path):
    """THE MERGE HAZARD. Structured data, not the sentence about it."""
    root = _project(tmp_path)
    path = _corrupt(root)

    result = run_watchdog_check(root)

    assert result.unreadable, (
        "reasons may still name the file while the structured field is empty — "
        "that is precisely the state a merge could have produced"
    )
    workers = [w for w, _why, _age in result.unreadable]
    assert workers == ["w1"]
    why = result.unreadable[0][1]
    assert str(path) in why
    assert "invalid JSON" in why


def test_it_is_empty_when_every_snapshot_reads(tmp_path):
    """The other half: a field that is always populated is as useless as one
    that never is."""
    root = _project(tmp_path)
    write_snapshot(
        root, ticket="BEN-1", progress="p", next_step="", blockers=[], worker="w1"
    )
    assert run_watchdog_check(root).unreadable == []


def test_it_still_needs_attention_and_is_not_an_answerable_question(tmp_path):
    """Unreadable is "investigate", not "reply": there is no question to
    answer until someone can read the file. The exit-code branch derives
    that from `len(reasons) != len(blocked)`, so this pins the inputs."""
    root = _project(tmp_path)
    _corrupt(root)

    result = run_watchdog_check(root)

    assert result.needs_attention
    assert len(result.reasons) > len(result.blocked)
