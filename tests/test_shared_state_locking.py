"""Regression tests for the state files that share the claims ledger's
shape but were never part of its fix.

The ledger's defect was an `flock` held on a file that `write_atomic`
replaces. Hunting for anything else with that shape turned up five files
with no lock at all and a truncating `write_text`, each doing the same
read-whole-file / change-one-entry / write-whole-file:

    .rite/modules.yaml              `rite add module` / `remove module`
    ~/.rite/credentials.json        `rite credential set` / `add worker`
    ~/.rite/dispatch/projects.yaml  `rite projects add` / `remove`
    .rite/context/INDEX.md          `rite context add` / `remove`
    .rite/kb/INDEX.md               `rite kb add` / `refresh`

Measured through the public entry points, six concurrent callers over
five rounds: every file lost entries in every round, and the worst kept
one of six.

`rite_ai.state`'s own docstring says "**Every** `.rite/` state file was
written with `Path.write_text`" and describes that as the failure it
exists to remove. It was fixed for the ledger, the outbox, heartbeats,
the handover snapshot and the pool — and these five were left, including
one (`credentials.json`) that is machine-wide and one (`modules.yaml`)
whose tearing makes `load_project` fail, which is what sends a handover
to the outbox instead of the board.

Threads rather than processes: none of these files was locked at all, so
the race is between a read and a write with no exclusion of any kind,
which threads reproduce exactly and far more cheaply.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

CONCURRENT = 6


def _race(action, count: int = CONCURRENT) -> None:
    """Run `action(i)` in `count` threads that all start together."""
    gate = threading.Barrier(count)
    errors: list[BaseException] = []

    def run(index: int) -> None:
        try:
            gate.wait(timeout=10)
            action(index)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a writer never returned — deadlock?"
    assert not errors, f"writers raised: {errors[:3]}"


class _FakeKeyring:
    """`store()` imports keyring inside the function, so this can stand in
    for it without a real keychain write. The test is about the registry
    half; the keychain half is the platform's."""

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str) -> None:
        self.passwords[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        return self.passwords.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        del self.passwords[(service, name)]


class TestModulesYaml:
    def test_concurrent_registrations_all_survive(self, tmp_path: Path):
        from rite_ai.config.parse import parse_modules
        from rite_ai.workspace import add_module

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "modules.yaml").write_text("modules: {}\n")

        _race(lambda i: add_module(tmp_path, f"mod{i}"))

        registered = {m.name for m in parse_modules(rite / "modules.yaml")}
        assert registered == {f"mod{i}" for i in range(CONCURRENT)}

    def test_an_interrupted_write_leaves_the_previous_file_intact(
        self, tmp_path: Path, monkeypatch
    ):
        """Proves `write_atomic` is in the path, not just that a lock is.
        A truncating write killed here leaves a torn `modules.yaml`, and
        `load_project` failing on one is what sends a handover to the
        outbox instead of the board."""
        from rite_ai.config.models import Module
        from rite_ai.workspace.manage import _write_modules

        path = tmp_path / "modules.yaml"
        _write_modules(path, [Module(name="keep", path="keep/")])
        before = path.read_text()

        monkeypatch.setattr(
            "os.replace", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        with pytest.raises(KeyboardInterrupt):
            _write_modules(path, [Module(name="lost", path="lost/")])

        assert path.read_text() == before


class TestCredentialRegistry:
    @pytest.fixture(autouse=True)
    def _fake_keyring(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring())

    def test_concurrent_stores_all_appear_in_the_registry(self, tmp_path: Path):
        from rite_ai.credentials.store import _read_registry, store

        _race(lambda i: store(f"sandbox_token_w{i}", f"secret-{i}"))

        assert set(_read_registry()) == {
            f"sandbox_token_w{i}" for i in range(CONCURRENT)
        }

    def test_a_registry_that_will_not_parse_is_not_silently_replaced(
        self, tmp_path: Path
    ):
        """Read as `{}` and written back, a corrupt registry loses every
        entry it held at the moment the user was adding one. The secret
        still reaches the keychain — that is the part that matters — and
        the caller is told the registry did not take."""
        from rite_ai.credentials.store import _registry_path, store

        path = _registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")

        result = store("jira_token", "value")

        assert "keychain" in result
        assert "registry unreadable" in result
        assert path.read_text() == "{not json", "the unreadable file was rewritten"

    def test_rotation_refuses_to_report_an_unreadable_registry_as_empty(
        self, tmp_path: Path
    ):
        from rite_ai.credentials.store import (
            RegistryUnreadable,
            _registry_path,
            list_for_rotation,
        )

        path = _registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")

        with pytest.raises(RegistryUnreadable):
            list_for_rotation()

    def test_an_absent_registry_is_still_simply_empty(self, tmp_path: Path):
        from rite_ai.credentials.store import list_for_rotation

        assert list_for_rotation() == []


class TestDispatchRegistry:
    def test_concurrent_registrations_all_survive(self, tmp_path: Path):
        from rite_ai.dispatch import add_project, load_registry

        dispatch = tmp_path / "dispatch"
        _race(lambda i: add_project(dispatch, f"p{i}", tmp_path))

        assert set(load_registry(dispatch).projects) == {
            f"p{i}" for i in range(CONCURRENT)
        }

    def test_a_duplicate_alias_is_still_refused_under_contention(self, tmp_path: Path):
        """The duplicate check now runs inside the lock. If it did not,
        two simultaneous registrations of one alias would both pass it."""
        from rite_ai.dispatch import add_project, load_registry

        dispatch = tmp_path / "dispatch"
        results: list[bool] = []
        lock = threading.Lock()

        def register(_index: int) -> None:
            outcome = add_project(dispatch, "same", tmp_path)
            with lock:
                results.append(outcome.ok)

        _race(register)

        assert results.count(True) == 1, results
        assert set(load_registry(dispatch).projects) == {"same"}


class TestContextIndex:
    def test_concurrent_additions_all_appear_in_the_index(self, tmp_path: Path):
        from rite_ai.context.manage import add_context

        (tmp_path / ".rite" / "context").mkdir(parents=True)

        _race(
            lambda i: add_context(
                tmp_path, f"topic{i}.md", f"when {i}", f"desc {i}", content=f"body {i}"
            )
        )

        index = (tmp_path / ".rite" / "context" / "INDEX.md").read_text()
        for i in range(CONCURRENT):
            assert f"topic{i}.md" in index

    def test_the_lock_sidecar_is_not_reported_as_orphaned_knowledge(
        self, tmp_path: Path
    ):
        """The sidecar lives in `context/`, which is also scanned for
        files that have no index row. Reporting `INDEX.md.lock` as an
        orphan sends a user looking for a context file that is not one."""
        from rite_ai.context.manage import add_context, check_integrity

        (tmp_path / ".rite" / "context").mkdir(parents=True)
        add_context(tmp_path, "ok.md", "When needed", "Fine", content="ok")

        assert (tmp_path / ".rite" / "context" / "INDEX.md.lock").is_file()
        assert check_integrity(tmp_path) == []


def test_no_durable_state_writer_is_left_using_write_text():
    """A guard on the family rather than on its members.

    `rite_ai.state` exists because durable state was being written with a
    truncating `write_text`, and the fix reached six files while leaving
    five. This fails if a sixth arrives.

    No exemptions. An earlier draft excused "generated documents" —
    `CLAUDE.md`, a worker manifest — on the grounds that a torn one is a
    nuisance rather than a lost fact. Two of the three the guard then
    caught turned out to be read back and acted on: `worker.yml` is what
    the watchdog uses to decide which workers exist, so a torn one makes
    a worker invisible to stall detection, and a torn launchd plist is a
    file `rite scheduler status` calls installed and launchd never
    loaded. The list of things "nobody parses back" is not a list worth
    maintaining; atomic is one call.

    `cli/init/scaffold.py` is out of scope, and that is a line rather
    than an exemption: its writes CREATE a project that does not exist
    yet, so there is no previous content for a torn write to destroy, and
    a crash part-way through `rite init` leaves a visibly incomplete
    `.rite/` that re-running init writes over. This guard is about
    replacing state that already matters.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "rite_ai"
    guarded = {
        "claims/ledger.py",
        "context/manage.py",
        "credentials/store.py",
        "dispatch/__init__.py",
        "handover/__init__.py",
        "kb/manage.py",
        "pool/__init__.py",
        "reporting/heartbeat.py",
        "reporting/outbox.py",
        "scheduler/__init__.py",
        "workspace/manage.py",
    }
    offenders = []
    for relative in sorted(guarded):
        path = src / relative
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or ".write_text(" not in stripped:
                continue
            offenders.append(f"{relative}:{number}: {stripped}")
    assert not offenders, (
        "durable state written with a truncating write_text — use "
        f"rite_ai.state.write_atomic: {offenders}"
    )


def test_every_guarded_writer_actually_imports_the_atomic_helper():
    """The guard above passes trivially for a file that writes nothing.
    This asserts the files it names really do use the helper."""
    src = Path(__file__).resolve().parent.parent / "src" / "rite_ai"
    for relative in (
        "claims/ledger.py",
        "context/manage.py",
        "credentials/store.py",
        "dispatch/__init__.py",
        "kb/manage.py",
        "update/__init__.py",
        "workspace/manage.py",
    ):
        assert "write_atomic" in (src / relative).read_text(), relative


def test_json_registry_files_round_trip_through_the_lock(tmp_path: Path):
    """A last check that the sidecars land where expected rather than
    inside a directory something else enumerates."""
    from rite_ai.state import lock_path_for, locked, write_atomic

    target = tmp_path / "registry.json"
    with locked(target):
        write_atomic(target, json.dumps({"a": 1}))
    assert lock_path_for(target).name == "registry.json.lock"
    assert lock_path_for(target).parent == target.parent
    assert json.loads(target.read_text()) == {"a": 1}
    assert not os.path.exists(tmp_path / ".registry.json.tmp")


class TestKbIndex:
    def test_concurrent_additions_all_appear_in_the_index(self, tmp_path: Path):
        """Missed in the first sweep, and the same shape: `_append_index_row`
        and `_set_index_notes` each read the whole index, change one row and
        write it back."""
        from rite_ai.kb.manage import add_link, list_entries

        (tmp_path / ".rite").mkdir()

        _race(lambda i: add_link(tmp_path, f"https://example.com/{i}"))

        sources = {e.source for e in list_entries(tmp_path)}
        assert sources == {f"https://example.com/{i}" for i in range(CONCURRENT)}


class TestExclusionIsMeasuredNotAssumed:
    """Every safety property in this package rests on `flock` excluding.
    It does on a local disk; on a network mount or inside some VM shared
    folders it can be a silent no-op — and a no-op returns rite to the
    defect the sidecar lock removed, with no signal at all.

    So the property is probed rather than inferred from a filesystem
    name. A name-based allowlist has to be right about every filesystem
    anyone mounts; this asks the kernel the actual question."""

    def test_a_local_directory_excludes(self, tmp_path: Path):
        from rite_ai.state import exclusion_holds

        assert exclusion_holds(tmp_path) is True

    def test_the_probe_leaves_nothing_behind(self, tmp_path: Path):
        from rite_ai.state import exclusion_holds

        before = set(p.name for p in tmp_path.iterdir())
        exclusion_holds(tmp_path)
        assert set(p.name for p in tmp_path.iterdir()) == before

    def test_a_directory_that_cannot_be_probed_is_not_a_yes(self, tmp_path: Path):
        """ "I could not find out" is not "exclusion holds"."""
        from rite_ai.state import exclusion_holds

        unwritable = tmp_path / "ro"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        try:
            assert exclusion_holds(unwritable / "sub") is False
        finally:
            unwritable.chmod(0o700)

    def test_the_ledger_warns_when_locking_is_decoration(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """The warning is the whole point: silently offering a guarantee
        that is not held is the failure shape rite keeps finding."""
        import rite_ai.claims.ledger as ledger_module

        monkeypatch.setattr(ledger_module, "exclusion_holds", lambda _d: False)
        ledger_module._EXCLUSION_PROBED.clear()

        ledger_module.ClaimsLedger(tmp_path / ".rite" / "claims.json")

        warning = capsys.readouterr().err
        assert "file locking does not work" in warning
        assert "both told 'claimed'" in warning

    def test_a_working_filesystem_says_nothing(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        import rite_ai.claims.ledger as ledger_module

        ledger_module._EXCLUSION_PROBED.clear()
        ledger_module.ClaimsLedger(tmp_path / ".rite" / "claims.json")

        assert capsys.readouterr().err == ""


class TestTheExclusionProbeDoesNotRaceItself:
    """`exclusion_holds` used one fixed probe filename and deleted it when
    done. Concurrent probes of one directory then deleted each other's file
    mid-probe; the survivor reopened the path, got a fresh inode, was granted
    the lock, and reported that flock does not exclude — on a disk where it
    does. Found by P2-1a's state-layer conformance suite on its first run.

    It is not a theoretical interleaving: several workers running
    `rite claim` together each build a ledger, each probe `.rite/`, and one
    of them printed a warning telling the user to run one worker at a time.
    """

    def test_concurrent_probes_of_one_directory_all_say_it_excludes(self, tmp_path):
        import threading

        from rite_ai.state import exclusion_holds

        n, trials = 16, 10
        for trial in range(trials):
            barrier = threading.Barrier(n)
            results = [None] * n

            def probe(i):
                barrier.wait()
                results[i] = exclusion_holds(tmp_path)

            threads = [threading.Thread(target=probe, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert all(results), f"trial {trial}: false 'does not exclude': {results}"

    def test_the_probe_leaves_nothing_behind(self, tmp_path):
        """A unique file per call must still be cleaned up, or every claim
        would litter `.rite/` with probe files."""
        from rite_ai.state import exclusion_holds

        for _ in range(5):
            exclusion_holds(tmp_path)
        assert not list(tmp_path.glob(".rite-flock-probe*"))
