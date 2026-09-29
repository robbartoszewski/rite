"""`rite deliver`: a Worker's commits leave its sandbox by rite, on the host.

The property (Robert, 2026-09-26): **"don't push" must never mean "don't
commit"**. A finished task's commits reach the project's own checkout of
each module, as a branch a person can rebase, and under `commit` nothing
reaches any remote. And, read once: **a publish setting changed since the
Worker started can take permission away, never give it.**

What is real here: git, every repository (the project's module checkout,
its origin, and a clone standing in for the sandbox's copy), the parser,
the start record and the claims ledger. What is not: yoloAI. Its status,
stop, copy location and destroy are replaced, because the copy is an
ordinary directory of clones and the question is what rite does with it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.config.parse import parse_config, parse_modules
from rite_ai.publishing import record
from rite_ai.publishing.deliver import Delivered, Refused, collected, deliver
from rite_ai.sandbox import SandboxResult, SandboxStatus

TICKET = "KAN-8"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=w@x",
            "-c",
            "user.name=w",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


class Project:
    """A rite project with one module `svc`, its origin, a Worker `alpha`,
    and a stand-in for alpha's sandbox copy holding a clone of `svc`."""

    def __init__(self, tmp_path: Path, strategy: str = "commit", squash: bool = False):
        self.root = tmp_path / "proj"
        self.origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True
        )
        rite = self.root / ".rite"
        rite.mkdir(parents=True)
        (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
        (rite / "modules.yaml").write_text(
            f"modules:\n  svc:\n    path: svc/\n    url: {self.origin}\n"
            "    branch: main\n"
        )
        self.set_publish(strategy, squash)
        worker = self.root / "workers" / "alpha"
        worker.mkdir(parents=True)
        (worker / "worker.yml").write_text(
            "worker:\n  name: alpha\n  manager: ''\n  modules: [svc]\n"
        )
        self.svc = self.root / "svc"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.svc)],
            check=True,
            capture_output=True,
        )
        _git(self.svc, "checkout", "-q", "-b", "main")
        self.base = _commit(self.svc, "app.txt", "v1\n", "base")
        _git(self.svc, "push", "-q", "origin", "main")
        # The sandbox's copy: a clone of the module, as yoloAI copies it.
        self.copy = tmp_path / "copy"
        self.copy.mkdir()
        self.clone = self.copy / "svc"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.clone)],
            check=True,
            capture_output=True,
        )
        _git(self.clone, "checkout", "-q", "-b", TICKET)
        self.destroyed = False

    def set_publish(self, strategy: str, squash: bool = False) -> None:
        (self.root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
            f"publish:\n  strategy: {strategy}\n  squash: {str(squash).lower()}\n"
        )

    def start(self) -> None:
        """What `rite sandbox start` records, from one parse."""
        config = parse_config(self.root / ".rite" / "config.yaml")
        modules = parse_modules(self.root / ".rite" / "modules.yaml")
        record.write(self.root, "alpha", TICKET, config, modules)

    def work(self, n: int = 2) -> list[str]:
        return [
            _commit(self.clone, f"f{i}.txt", f"{i}\n", f"step {i}") for i in range(n)
        ]

    def deliver(self, **kw) -> Delivered | Refused:
        def destroy(worker, root):
            self.destroyed = True
            return SandboxResult(True, "destroyed")

        with (
            patch(
                "rite_ai.sandbox.worker_sandbox_status",
                return_value=SandboxStatus("stopped"),
            ),
            patch(
                "rite_ai.sandbox.stop_worker",
                return_value=SandboxResult(True, "stopped"),
            ),
            patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
            patch("rite_ai.sandbox._sandbox_copy", return_value=self.copy),
            patch("rite_ai.sandbox.destroy_worker", side_effect=destroy),
        ):
            return deliver(self.root, "alpha", **kw)

    def branch(self, name: str) -> str | None:
        done = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
            cwd=self.svc,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip() or None

    def on_origin(self) -> list[str]:
        return _git(self.origin, "for-each-ref", "--format=%(refname)").splitlines()


# --- commit -------------------------------------------------------------------------


def test_commit_brings_the_work_home_and_sends_nothing_anywhere(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    shas = p.work()
    head_before = _git(p.svc, "rev-parse", "HEAD")

    got = p.deliver()

    assert isinstance(got, Delivered) and got.ok, got
    assert p.branch(TICKET) == shas[-1]
    # A branch someone can rebase: it grows from the module's own branch.
    assert _git(p.svc, "merge-base", "main", TICKET) == p.base
    # Nothing reached the remote, not even the branch "for safety".
    assert p.on_origin() == ["refs/heads/main"]
    # The project's checkout gained a ref and nothing else.
    assert _git(p.svc, "rev-parse", "HEAD") == head_before
    assert _git(p.svc, "status", "--porcelain") == ""
    assert "nothing was pushed" in got.outcomes[0].note()
    assert p.destroyed


def test_the_work_survives_the_sandbox_it_came_from(tmp_path):
    """PB1's "done when": the work survives the Worker's next task. The copy
    is gone; the commits are still reachable in the project."""
    import shutil

    p = Project(tmp_path, "commit")
    p.start()
    shas = p.work()
    assert p.deliver().ok
    shutil.rmtree(p.copy)
    assert _git(p.svc, "log", "--format=%H", TICKET).splitlines()[:2] == shas[::-1]


def test_squash_leaves_one_commit_to_rework_and_keeps_the_history(tmp_path):
    p = Project(tmp_path, "commit", squash=True)
    p.start()
    shas = p.work(3)

    assert p.deliver().ok

    squashed = p.branch(TICKET)
    assert _git(p.svc, "rev-list", "--count", f"main..{TICKET}") == "1"
    assert _git(p.svc, "rev-parse", f"{TICKET}^") == p.base
    assert _git(p.svc, "rev-parse", f"{TICKET}^{{tree}}") == _git(
        p.svc, "rev-parse", f"{shas[-1]}^{{tree}}"
    )
    assert p.branch(f"{TICKET}-unsquashed") == shas[-1]
    message = _git(p.svc, "log", "-1", "--format=%B", squashed)
    assert all(f"step {i}" in message for i in range(3))


def test_uncommitted_work_is_refused_and_left_where_it_is(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work(1)
    (p.clone / "half.txt").write_text("half\n")

    got = p.deliver()

    assert not got.ok
    note = got.outcomes[0].note()
    assert "uncommitted in the sandbox" in note and "half.txt" in note
    assert p.branch(TICKET) is None
    assert not p.destroyed, "a copy holding uncommitted work was destroyed"


def test_a_branch_checked_out_in_the_project_is_not_moved(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work(1)
    _git(p.svc, "checkout", "-q", "-b", TICKET)
    got = p.deliver()
    assert not got.ok
    assert f"Switch svc/ off {TICKET}" in got.outcomes[0].note()


def test_a_worker_that_made_no_branch_is_said(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    _git(p.clone, "checkout", "-q", "main")
    _git(p.clone, "branch", "-D", TICKET)
    got = p.deliver()
    assert not got.ok and "made no branch" in got.outcomes[0].note()


# --- read once ----------------------------------------------------------------------


def test_a_strategy_changed_mid_run_commits_locally_and_names_both(tmp_path):
    """Robert's Q3: started under pull_request, finishing under commit. Commit
    locally, nothing else, name both values and the command."""
    p = Project(tmp_path, "pull_request")
    p.start()
    p.work()
    p.set_publish("commit")

    got = p.deliver()

    assert not got.ok
    note = got.outcomes[0].note()
    assert "pull_request→commit" in note
    assert "rite deliver alpha" in note
    assert p.branch(TICKET) is not None  # committed locally
    assert p.on_origin() == ["refs/heads/main"]  # and nothing else
    assert not p.destroyed, "the User's re-run needs the sandbox"


def test_a_change_towards_pushing_is_not_honoured_either(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work()
    p.set_publish("push")
    got = p.deliver()
    assert not got.ok and "commit→push" in got.outcomes[0].note()
    assert p.on_origin() == ["refs/heads/main"]


def test_a_squash_turned_on_mid_run_is_not_applied(tmp_path):
    """Squashing can be done later; un-squashing cannot."""
    p = Project(tmp_path, "commit", squash=False)
    p.start()
    shas = p.work(3)
    p.set_publish("commit", squash=True)
    got = p.deliver()
    assert "squash off→squash on" in got.outcomes[0].note()
    assert p.branch(TICKET) == shas[-1]
    assert p.branch(f"{TICKET}-unsquashed") is None


def test_no_start_record_is_not_no_change(tmp_path):
    p = Project(tmp_path, "commit")
    p.work()
    got = p.deliver(ticket=TICKET)
    assert not got.ok and "unrecorded→commit" in got.outcomes[0].note()


def test_an_unreadable_config_at_delivery_collects_and_goes_no_further(tmp_path):
    p = Project(tmp_path, "pull_request")
    p.start()
    p.work()
    (p.root / ".rite" / "config.yaml").write_text("publish: [\n")
    got = p.deliver()
    assert not got.ok and "unreadable" in got.outcomes[0].note()
    assert p.branch(TICKET) is not None


def test_the_user_at_a_terminal_delivers_under_the_config_as_it_is(tmp_path):
    p = Project(tmp_path, "pull_request")
    p.start()
    p.work()
    p.set_publish("commit")
    assert not p.deliver().ok
    got = p.deliver(by_user=True)
    assert got.ok and "nothing was pushed" in got.outcomes[0].note()


def test_push_to_shared_is_refused_again_at_delivery(tmp_path):
    """Refused at start (piece 1). If a record says it anyway, the publish
    step refuses it too, with the same text: the door is shut."""
    p = Project(tmp_path, "push_to_shared")
    p.start()
    p.work()
    got = p.deliver()
    assert not got.ok
    assert "not available until rite v0.8.0" in got.outcomes[0].note()
    assert p.branch(TICKET) is None


# --- the sandbox and the claims -----------------------------------------------------


def test_a_sandbox_nobody_can_ask_about_refuses(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    with patch(
        "rite_ai.sandbox.worker_sandbox_status",
        return_value=SandboxStatus("unknown — boom", known=False),
    ):
        got = deliver(p.root, "alpha")
    assert isinstance(got, Refused) and "could not ask yoloAI" in got.why


def test_the_copy_is_counted_saved_only_once_it_is_collected(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work()
    assert not collected(p.clone, p.svc)
    assert p.deliver().ok
    assert collected(p.clone, p.svc)


def test_the_destroy_guard_drops_only_collected_modules(tmp_path):
    from rite_ai.sandbox import _not_collected
    from rite_ai.workspace import unsaved_work

    p = Project(tmp_path, "commit")
    p.start()
    p.work()
    items = unsaved_work(p.copy)
    assert [i.module for i in items] == ["svc"]
    assert _not_collected(items, p.copy, p.root) == items
    assert p.deliver().ok
    assert _not_collected(unsaved_work(p.copy), p.copy, p.root) == []
    (p.root / ".rite" / "modules.yaml").write_text("modules: [\n")
    assert _not_collected(unsaved_work(p.copy), p.copy, p.root) != []


def _claim(p: Project) -> None:
    from rite_ai.claims.ledger import ClaimsLedger

    assert (
        ClaimsLedger(p.root / ".rite" / "claims.json")
        .claim(["svc/app.txt"], "alpha", ticket=TICKET)
        .ok
    )


def _held(p: Project) -> int:
    from rite_ai.claims.ledger import ClaimsLedger

    return len(ClaimsLedger(p.root / ".rite" / "claims.json").claims_for("alpha"))


def test_claims_are_released_when_the_work_has_landed_under_commit(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    p.work()
    _claim(p)
    got = p.deliver()
    assert "released 1 claim(s)" in got.sandbox
    assert _held(p) == 0


def test_claims_stay_held_until_the_merge_under_a_pull_request(tmp_path):
    from rite_ai.publishing.deliver import Outcome

    p = Project(tmp_path, "pull_request")
    p.start()
    p.work()
    _claim(p)
    opened = Outcome("svc", TICKET, True, "pushed; PR https://x/pull/1")
    with patch("rite_ai.publishing.deliver._publish", return_value=opened):
        assert p.deliver().ok
    assert _held(p) == 1


# --- the record ---------------------------------------------------------------------


def test_the_record_is_outside_every_managers_grant(tmp_path):
    p = Project(tmp_path, "commit")
    p.start()
    where = record._records_dir(p.root)
    assert not where.is_relative_to(p.root)
    body = json.loads((where / "alpha.json").read_text())
    assert body["modules"] == {
        "svc": {"strategy": "commit", "squash": False, "auto_merge": False}
    }


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"version": 99}',
        '{"version": 1, "modules": {}}',
        # Well formed in every field but the version: only the version check
        # can refuse it, so this case is what proves that check is connected.
        '{"version": 99, "worker": "alpha", "ticket": "KAN-8", '
        '"started_at": "x", "modules": {}}',
    ],
)
def test_a_damaged_record_is_unreadable_not_absent(tmp_path, text):
    p = Project(tmp_path, "commit")
    p.start()
    (record._records_dir(p.root) / "alpha.json").write_text(text)
    assert isinstance(record.read(p.root, "alpha"), record.Unreadable)


# --- the entry points ---------------------------------------------------------------


def test_sandbox_start_records_and_tells_the_worker_from_one_parse(
    tmp_path, monkeypatch
):
    """What the Worker is told (TICKET.md, Publishing) and what `rite deliver`
    compares against (the record) come from the same start."""
    from unittest.mock import MagicMock

    from click.testing import CliRunner

    from rite_ai.cli.main import cli
    from tests.test_cli import _sandbox_project, _with_a_board

    _sandbox_project(tmp_path, monkeypatch)
    config = tmp_path / ".rite" / "config.yaml"
    config.write_text(config.read_text() + "publish:\n  strategy: commit\n")

    def _run(args, *a, **kw):
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        _with_a_board(),
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=_run),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--ticket", "ABC-12"]
        )
    assert result.exit_code == 0, result.output
    told = (tmp_path / "workers" / "alpha" / "TICKET.md").read_text()
    publishing = told.split("## Publishing", 1)[1]
    assert "`app` (commit)" in publishing
    assert "Do not push, open a pull request or merge" in publishing
    started = record.read(tmp_path, "alpha")
    assert isinstance(started, record.Record) and started.ticket == "ABC-12"
    assert started.modules["app"]["strategy"] == "commit"


def test_a_commit_only_module_needs_no_push_access(tmp_path):
    """An on-premise project with no reachable remote and no token must be
    able to run `commit`: nothing is pushed."""
    from rite_ai.cli.main import _worker_cannot_deliver
    from rite_ai.config.models import Module, ProjectConfig, PublishConfig

    clone = tmp_path / "workers" / "alpha" / "app"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    _commit(clone, "a", "a\n", "a")
    _git(clone, "remote", "add", "origin", "https://github.com/acme/app.git")
    modules = [Module(name="app", path="app/")]
    worker = tmp_path / "workers" / "alpha"

    pushing = ProjectConfig(publish=PublishConfig(strategy="pull_request"))
    refused = _worker_cannot_deliver("alpha", worker, modules, None, pushing)
    assert refused and "token" in refused.lower()

    local = ProjectConfig(publish=PublishConfig(strategy="commit"))
    assert _worker_cannot_deliver("alpha", worker, modules, None, local) is None


def test_rite_deliver_prints_each_module_and_exits_by_the_outcome(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli
    from rite_ai.publishing.deliver import Outcome

    monkeypatch.chdir(Project(tmp_path).root)
    ok = Delivered([Outcome("svc", TICKET, True, "committed")], "sandbox removed")
    with patch("rite_ai.publishing.deliver.deliver", return_value=ok) as called:
        result = CliRunner().invoke(cli, ["deliver", "alpha"])
    assert result.exit_code == 0, result.output
    assert called.call_args.kwargs == {"by_user": True}
    assert f"Delivered svc/{TICKET}: committed." in result.output

    bad = Delivered([Outcome("svc", TICKET, False, "no", "Fix it")], "kept")
    with patch("rite_ai.publishing.deliver.deliver", return_value=bad):
        assert CliRunner().invoke(cli, ["deliver", "alpha"]).exit_code == 1
    with patch("rite_ai.publishing.deliver.deliver", return_value=Refused("why")):
        result = CliRunner().invoke(cli, ["deliver", "alpha"])
    assert result.exit_code == 1 and "not delivered: why" in result.output


def test_no_worker_instruction_says_to_merge_or_to_push_regardless():
    """The instruction text is where a Worker's merge came from; with `main`'s
    "require branches to be up to date" off, a self-merge accepts a stale
    green."""
    from pathlib import Path as P

    import rite_ai

    src = P(rite_ai.__file__).parent
    texts = {
        "workspace/manage.py": (src / "workspace" / "manage.py").read_text(),
        "cli/init/claude_gen.py": (src / "cli" / "init" / "claude_gen.py").read_text(),
        "templates/commands/ticket.md": (
            src.parent.parent / "templates" / "commands" / "ticket.md"
        ).read_text(),
    }
    for where, text in texts.items():
        flat = " ".join(text.split())
        assert "get it reviewed, and merge" not in flat, where
        assert "Merge once reviewed" not in flat, where
        assert "merges reviewed work" not in flat, where
        assert "Push that branch after every commit" not in flat, where
        assert "never merge" in flat.lower() or "never merges" in flat.lower(), where
