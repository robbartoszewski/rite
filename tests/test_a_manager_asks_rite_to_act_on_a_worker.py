"""A Manager asks rite to start, deliver, stop, destroy, restart, report on or
gate a Worker, and rite does it on the host (SCRUM-59).

🔴 **What happened** (the a8 dogfood, 2026-10-04). A Worker's sandbox died
and the Manager could not stop, restart or destroy it, nor see why the gate
refused its delivery: those need yoloAI's state and the credential store,
which its boundary does not grant. And the one way it had to ask anything,
`echo '{…}' > …/$(date +%s).json`, was itself refused by the engine. A
person had to run host commands.

These pin:
- `rite request` writes the request in Python: no `$( )` or `>` is taught;
- the request carries op, worker and ticket, nothing else (no `force`);
- only the Manager rite started a Worker for may act on it;
- the supervisor honours it at the cycle boundary, never twice, and every
  outcome reaches the Manager's next instruction;
- the host side follows no link the Manager planted;
- a refused delivery carries the gate's findings.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import MANAGER_ENV, lifecycle
from rite_ai.managers.mailbox import INBOX, delivery_note
from rite_ai.managers.mailbox import take as take_mail
from rite_ai.tickets import Ticket

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
    "      preset: lead\n    - name: helper\n      engine: claude\n"
    "      preset: executor\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text(
        "modules:\n  svc:\n    path: svc/\n    url: ''\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(CONFIG)
    for worker in ("alpha", "beta"):
        where = tmp_path / "workers" / worker
        where.mkdir(parents=True)
        (where / "worker.yml").write_text(
            f"worker:\n  name: {worker}\n  manager: ''\n  modules: [svc]\n"
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    monkeypatch.setenv(MANAGER_ENV, "lead")
    return tmp_path


def _ask(*args: str):
    return CliRunner().invoke(cli, ["request", *args])


def _requests(root: Path, manager: str = "lead") -> list[dict]:
    where = lifecycle.lifecycle_dir(root, manager)
    return [json.loads(p.read_text()) for p in sorted(where.glob("*.json"))]


def _told(root: Path, manager: str = "lead") -> str:
    return delivery_note(take_mail(root, manager, INBOX))


def _honour(root: Path, act=None, manager: str = "lead") -> list[str]:
    said: list[str] = []
    lifecycle.honour_requests(
        root, manager, said.append, act=act or (lambda r, req, now: f"did {req.op}")
    )
    return said


# --- the command writes the request; nothing reaches the shell ---------------------


class TestTheCommand:
    @pytest.mark.parametrize("op", lifecycle.OPS)
    def test_each_lifecycle_op_is_written_as_three_fields(self, project, op):
        got = _ask(op, "alpha")
        assert got.exit_code == 0, got.output
        assert _requests(project) == [{"op": op, "worker": "alpha"}]

    def test_start_and_deliver_go_where_their_honour_steps_read(self, project):
        from rite_ai.managers import broker
        from rite_ai.publishing import requests as deliveries

        assert _ask("start", "alpha", "--ticket", "RT-12").exit_code == 0
        assert _ask("deliver", "alpha", "--ticket", "RT-12").exit_code == 0
        (start,) = broker.take_requests(project, "lead")
        # ⚠ `read_ticket` is not optional: SCRUM-73 made `decide` refuse a
        # request whose ticket STATUS it cannot read, because a Worker
        # started on finished work is how KAN-28 was worked twice. A live
        # ticket here, since this test is about where the request is written.
        decided = broker.decide(
            start[1],
            lambda w: True,
            lambda t: True,
            read_ticket=lambda t: Ticket(id=t, title="a ticket", status="To Do"),
        )
        assert decided.ok and decided.request.worker == "alpha", decided
        (deliver,) = deliveries.take(project, "lead")
        assert deliveries.decide(deliver) == deliveries.Request("alpha", "RT-12")
        assert _requests(project) == []

    def test_two_requests_in_one_tick_are_both_kept(self, project, monkeypatch):
        """`time.time_ns()` alone as the name: the second replaced the
        first, silently."""
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        assert _ask("stop", "alpha").exit_code == 0
        assert _ask("status", "beta").exit_code == 0
        assert len(_requests(project)) == 2

    @pytest.mark.parametrize(
        "args, why",
        [
            (["stop", "nobody"], "no Worker called"),
            (["stop", "../x"], "worker name"),
            (["start", "alpha"], "needs --ticket"),
            (["deliver", "alpha"], "needs --ticket"),
            (["gate", "alpha", "--ticket", "-x"], "letter or digit first"),
            (["gate", "alpha", "--ticket", "$(id)"], "not shaped like a ticket"),
            (["gate", "alpha", "--ticket", "a..b"], "not a branch name"),
        ],
    )
    def test_what_it_refuses_writes_nothing(self, project, args, why):
        got = _ask(*args)
        assert got.exit_code != 0
        assert why in got.output, got.output
        assert _requests(project) == []

    def test_force_cannot_be_asked_for(self, project):
        got = _ask("destroy", "alpha", "--force")
        assert got.exit_code != 0
        assert _requests(project) == []

    def test_from_a_persons_shell_it_points_at_the_direct_command(
        self, project, monkeypatch
    ):
        monkeypatch.delenv(MANAGER_ENV)
        got = _ask("stop", "alpha")
        assert got.exit_code == 1 and "rite sandbox stop alpha" in got.output


def test_no_manager_instruction_teaches_a_redirect_or_a_substitution(project):
    """🔴 The three prompt texts taught `echo '{…}' > …/$(date +%s).json`,
    and the engine refused it. Every way a Manager is told to ask is now a
    `rite request` command."""
    from rite_ai.managers import broker, prompt
    from rite_ai.publishing import requests as deliveries

    said = "\n".join(
        [
            prompt.for_manager("lead", root=project),
            broker.instructions(project, "lead"),
            deliveries.instructions(project, "lead"),
            lifecycle.instructions(project, "lead"),
        ]
    )
    assert "$(" not in said and "date +%s" not in said
    assert "> " + str(lifecycle.lifecycle_dir(project, "lead").parent) not in said
    for op in ("start", "deliver", *lifecycle.OPS):
        assert f" request {op} " in said, op


def test_the_taught_commands_are_on_the_allowlist(project):
    from rite_ai import own_command
    from rite_ai.managers.permissions import DEFAULT_ALLOW, _running_rite_rules, allowed

    allow = DEFAULT_ALLOW + _running_rite_rules()
    for line in (
        f"{own_command()} request start alpha --ticket RT-12",
        f"{own_command()} request stop alpha",
        f"{own_command()} request gate alpha --ticket RT-12",
    ):
        assert allowed(line, allow), line


# --- what a request may carry -------------------------------------------------------


@pytest.mark.parametrize(
    "raw, why",
    [
        ('{"op": "destroy", "worker": "alpha", "force": true}', "'force'"),
        ('{"op": "rm", "worker": "alpha"}', "not one of"),
        ('{"op": "stop", "worker": "nobody"}', "no Worker called"),
        ('{"op": "stop"}', "must be strings"),
        ("[]", "not a JSON object"),
        ("not json", "not JSON"),
        ('{"op": "stop", "worker": "alpha", "x": "' + "a" * 5000 + '"}', "larger"),
    ],
)
def test_decide_refuses_all_but_three_fields(raw, why):
    got = lifecycle.decide(raw, lambda w: w == "alpha")
    assert isinstance(got, str) and why in got, got


def test_control_decide_accepts_a_well_formed_request():
    got = lifecycle.decide(
        '{"op": "gate", "worker": "alpha", "ticket": "RT-12"}', lambda w: True
    )
    assert got == lifecycle.Request("gate", "alpha", "RT-12")


# --- only the Manager a Worker was started for may act on it ------------------------


class TestWhoseWorker:
    def test_a_worker_started_for_another_manager_is_refused(self, project):
        lifecycle.record_owner(project, "alpha", "helper")
        assert "started for Manager 'helper'" in lifecycle._may_act(
            project, "lead", "alpha"
        )

    def test_control_its_own_worker(self, project):
        lifecycle.record_owner(project, "alpha", "lead")
        assert lifecycle._may_act(project, "lead", "alpha") == ""

    def test_with_no_record_no_manager_may(self, project):
        """🔴 SCRUM-59 review, measured: the fallback was "the Manager that
        routes", read from `.rite/config.yaml`, which a Manager can write. A
        Worker rite did not start for a Manager is the person's."""
        for manager in ("lead", "helper"):
            said = lifecycle._may_act(project, manager, "alpha")
            assert "not yours to act on" in said and "rite sandbox" in said

    def test_a_record_rite_cannot_read_is_refused_not_ignored(self, project):
        path = lifecycle._owners_dir(project) / "alpha.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert "cannot tell" in lifecycle._may_act(project, "lead", "alpha")

    def test_the_record_is_where_no_profile_grants(self, project):
        from rite_ai.managers import manager_dir

        where = lifecycle._owners_dir(project)
        assert project not in where.parents
        assert manager_dir(project, "lead") not in where.parents

    def test_a_worker_rite_starts_for_a_manager_is_recorded_as_its(self, project):
        from rite_ai.managers import broker, supervise

        assert _ask("start", "alpha", "--ticket", "RT-12").exit_code == 0
        supervise._honour_worker_requests(
            project, "lead", lambda raw, manager="": (True, "started"), lambda s: None
        )
        assert lifecycle._owner_of(project, "alpha") == "lead"
        assert broker.take_requests(project, "lead") == []

    def test_the_honour_step_refuses_another_managers_worker(self, project):
        lifecycle.record_owner(project, "alpha", "helper")
        assert _ask("restart", "alpha").exit_code == 0
        acted = []
        said = _honour(project, act=lambda r, req, now: acted.append(req) or "x")
        assert acted == []
        assert "NOT done (restart alpha)" in said[0]
        assert "NOT done (restart alpha)" in _told(project)


# --- the supervisor honours it once, and says what happened -------------------------


class TestTheHonourStep:
    def test_every_outcome_reaches_the_next_instruction(self, project):
        lifecycle.record_owner(project, "alpha", "lead")
        _ask("status", "alpha")
        said = _honour(project)
        assert said == ["'lead': did status"]
        assert "did status" in _told(project)
        assert _requests(project) == []

    def test_a_request_is_never_acted_on_twice(self, project):
        lifecycle.record_owner(project, "alpha", "lead")
        _ask("restart", "alpha")
        acted = []
        _honour(project, act=lambda r, req, now: acted.append(req.op) or "ok")
        _honour(project, act=lambda r, req, now: acted.append(req.op) or "ok")
        assert acted == ["restart"]

    def test_one_a_crashed_supervisor_took_is_said_not_retried(self, project):
        lifecycle.record_owner(project, "alpha", "lead")
        _ask("destroy", "alpha")
        (pending,) = lifecycle.lifecycle_dir(project, "lead").glob("*.json")
        pending.rename(pending.with_name(pending.name + lifecycle.TAKEN))
        acted = []
        said = _honour(project, act=lambda r, req, now: acted.append(req.op) or "x")
        assert acted == []
        assert "Whether it ran is not known" in said[0]
        assert not list(lifecycle.lifecycle_dir(project, "lead").iterdir())

    def test_a_supervisor_that_dies_mid_request_does_not_redo_it(self, project):
        """A restart or destroy is not idempotent. The supervisor is killed
        while acting (here: an interrupt, which nothing catches); the next
        cycle says the request was interrupted and does NOT act again."""
        lifecycle.record_owner(project, "alpha", "lead")
        _ask("restart", "alpha")

        def killed(r, req, now):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            _honour(project, act=killed)
        acted = []
        said = _honour(project, act=lambda r, req, now: acted.append(req.op) or "x")
        assert acted == []
        assert any("Whether it ran is not known" in line for line in said), said

    def test_a_failure_in_the_operation_is_said_not_raised(self, project):
        lifecycle.record_owner(project, "alpha", "lead")
        _ask("stop", "alpha")

        def boom(r, req, now):
            raise RuntimeError("yoloai vanished")

        said = _honour(project, act=boom)
        assert "rite failed acting on it" in said[0] and "yoloai vanished" in said[0]

    def test_a_linked_lifecycle_directory_is_not_followed(self, project, tmp_path):
        """The supervisor renames and removes `*.json` here outside every
        boundary; a `lifecycle` the Manager made a link would have it do so
        wherever the link pointed."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "precious.json").write_text('{"op": "stop", "worker": "alpha"}')
        own = lifecycle.lifecycle_dir(project, "lead")
        own.parent.mkdir(parents=True, exist_ok=True)
        own.symlink_to(elsewhere, target_is_directory=True)
        acted = []
        said = _honour(project, act=lambda r, req, now: acted.append(req) or "x")
        assert acted == []
        assert "cannot be opened as rite's own directory" in said[0]
        assert (elsewhere / "precious.json").exists()

    def test_a_linked_request_file_is_not_read(self, project, tmp_path):
        secret = tmp_path / "secret.json"
        secret.write_text('{"op": "stop", "worker": "alpha"}')
        own = lifecycle.lifecycle_dir(project, "lead")
        own.mkdir(parents=True)
        (own / "1.json").symlink_to(secret)
        acted = []
        _honour(project, act=lambda r, req, now: acted.append(req) or "x")
        assert acted == []
        assert secret.exists()


# --- the operations ---------------------------------------------------------------


class TestTheOperations:
    def test_destroy_is_never_forced(self, project):
        from rite_ai.sandbox import SandboxResult

        with patch(
            "rite_ai.sandbox.destroy_worker",
            return_value=SandboxResult(False, "refusing: it holds unpushed work"),
        ) as destroy:
            said = lifecycle._act(
                project, lifecycle.Request("destroy", "alpha"), time.time()
            )
        assert destroy.call_args.kwargs == {"force": False}
        assert said.startswith("NOT destroyed alpha: refusing")

    def test_restart_shares_recoverys_backoff(self, project):
        from rite_ai.managers import recovery

        now = 10_000.0
        restarts = []
        with patch.object(
            recovery,
            "_restart",
            side_effect=lambda root, w: restarts.append(w) or (True, "in place"),
        ):
            req = lifecycle.Request("restart", "alpha")
            first = lifecycle._act(project, req, now)
            second = lifecycle._act(project, req, now + 10)
        assert first == "Restarted alpha: in place"
        assert second.startswith("NOT restarted alpha") and "Ask again in" in second
        assert restarts == ["alpha"]
        assert recovery._read_ledger(project)["requested:alpha"].attempts == 1

    def test_restart_stops_at_recoverys_limit(self, project):
        from rite_ai.managers import recovery

        recovery._write_ledger(
            project,
            {"alpha": recovery.LedgerEntry(recovery.DEFAULT_MAX_RESTARTS, 1.0, 1.0)},
        )
        with patch.object(recovery, "_restart") as restart:
            said = lifecycle._act(project, lifecycle.Request("restart", "alpha"), 1e9)
        restart.assert_not_called()
        assert "rite's own recovery already restarted it" in said

    def test_gate_with_no_checkout_says_so(self, project):
        said = lifecycle._act(
            project, lifecycle.Request("gate", "alpha", "RT-12"), time.time()
        )
        assert "svc/ is not a git checkout here" in said

    def test_gate_with_nothing_collected_says_so(self, project):
        import subprocess

        subprocess.run(["git", "init", "-q", str(project / "svc")], check=True)
        said = lifecycle._act(
            project, lifecycle.Request("gate", "alpha", "RT-12"), time.time()
        )
        assert "no branch RT-12" in said and "nothing to gate" in said

    def test_gate_names_what_it_found(self, project):
        (project / "svc" / ".git").mkdir(parents=True)
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport

        report = GateReport(
            findings=[
                Finding(
                    "aws-key", "AWS key", "app.py", 3, "abcdef1234", "AKIA…", "gitleaks"
                )
            ]
        )
        with (
            patch("rite_ai.publishing.deliver._sha", return_value="abc"),
            patch("rite_ai.gate.gate.run_gate", return_value=report),
        ):
            said = lifecycle._act(
                project, lifecycle.Request("gate", "alpha", "RT-12"), time.time()
            )
        assert "gate fail" in said
        assert "[aws-key] app.py:3 (abcdef12) — AWS key" in said
        assert "AKIA" not in said, "the match itself never rides in a note"


def test_a_refused_delivery_carries_the_gates_findings(tmp_path):
    """🔴 The note said "Run `rite publish check`", a host command a
    sandboxed Manager cannot run: it learned what to do and not what was
    wrong."""
    from rite_ai.gate.findings import Finding
    from rite_ai.gate.gate import GateReport
    from rite_ai.publishing import deliver
    from tests.test_rite_delivers_a_finished_task import Project

    p = Project(tmp_path, "push")
    from rite_ai.config.parse import parse_modules

    (module,) = parse_modules(p.root / ".rite" / "modules.yaml")
    report = GateReport(
        findings=[Finding("private-key", "Private key", "k.pem", 1, None, "-----", "x")]
    )
    with patch("rite_ai.gate.gate.run_gate", return_value=report):
        got = deliver._publish(p.root, "alpha", module, "RT-12", "RT-12", "push", None)
    assert not got.ok
    assert "[private-key] k.pem:1 — Private key" in got.why
    assert "rite request gate alpha --ticket RT-12" in got.fix
    # The Manager's own command first; the host one stays, for a person.
    assert got.fix.index("rite request gate") < got.fix.index("rite publish check")


# --- it counts as a Manager's progress ---------------------------------------------


@pytest.mark.parametrize("op", ["stop", "destroy", "restart"])
def test_asking_to_change_a_worker_counts_as_progress(project, op):
    from rite_ai.managers.progress import footprint
    from rite_ai.managers.supervise import _session_was_idle

    before = footprint(project, "lead")
    assert _ask(op, "alpha").exit_code == 0
    changed = footprint(project, "lead").differs_from(before)
    assert changed == ["lifecycle"]
    assert not _session_was_idle(changed)


@pytest.mark.parametrize("op", ["status", "gate"])
def test_asking_only_to_look_is_not_progress(project, op):
    """🔴 SCRUM-59 review: a Manager asking `status` every turn while it
    waits would otherwise be started again and again, without end."""
    from rite_ai.managers.progress import footprint

    before = footprint(project, "lead")
    assert _ask(op, "alpha").exit_code == 0
    assert footprint(project, "lead").differs_from(before) == []


def test_an_unchanged_look_is_not_told_again(project):
    """The answer is mail, and mail wakes a session: the same `status` told
    every turn would wake the Manager every turn with nothing new."""
    lifecycle.record_owner(project, "alpha", "lead")
    for _ in range(2):
        _ask("status", "alpha")
        _honour(project, act=lambda r, req, now: "alpha: sandbox running")
    assert _told(project).count("alpha: sandbox running") == 1
    _ask("status", "alpha")
    _honour(project, act=lambda r, req, now: "alpha: sandbox stopped")
    assert "alpha: sandbox stopped" in _told(project)


def test_the_supervisor_honours_requests_between_deliveries_and_starts():
    """The order is load-bearing: a delivery may already have removed the
    sandbox a request names, and a destroy frees the slot a start in the
    same cycle needs."""
    import inspect

    from rite_ai.managers import supervise

    source = inspect.getsource(supervise)
    deliveries = source.index("honour_deliveries(root, manager, say, recorder)")
    lifecycles = source.index(
        "lifecycle.honour_requests(root, manager, say)", deliveries
    )
    starts = source.index(
        "_honour_worker_requests(root, manager, broker, say, recorder)", lifecycles
    )
    assert deliveries < lifecycles < starts


def test_request_names_sort_oldest_first():
    first = lifecycle.request_name()
    time.sleep(0.001)
    assert lifecycle.request_name() > first


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
def test_the_taught_command_works_inside_the_real_manager_profile(project):
    """The acceptance test the ticket names: from inside the profile `rite
    start` composes, the command the Manager is taught files the request.
    (The a8 Manager's `echo … > …/$(date +%s).json` was refused before it ran.)"""
    import shlex
    import subprocess

    from rite_ai import own_command
    from rite_ai.managers import manager_dir
    from rite_ai.managers.enclosure import _own_subdirs, compose
    from rite_ai.managers.mailbox import OUTBOX, mailbox_dir

    for box in (OUTBOX, INBOX):
        mailbox_dir(project, "lead", box).mkdir(parents=True, exist_ok=True)
    manager_dir(project, "lead").mkdir(parents=True, exist_ok=True)
    _own_subdirs(project, "lead")
    profile = project / "lead.sb"
    profile.write_text(compose(project, "lead"))
    line = f"{shlex.quote(own_command())} request restart alpha"
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/zsh", "-c", line],
        cwd=project,
        env={**os.environ, MANAGER_ENV: "lead"},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert _requests(project) == [{"op": "restart", "worker": "alpha"}]


# --- the review's findings, each pinned -------------------------------------------


@pytest.mark.parametrize(
    "branch",
    [
        "main --output=/tmp/x",
        "--output=/tmp/x",
        "-x",
        "a..b",
        "a b",
        "ma\u00efn",
        "x.lock",
    ],
)
def test_a_module_branch_that_is_not_a_branch_name_is_refused(project, branch):
    """🔴 SCRUM-59 security review, measured: a Manager can write
    `.rite/modules.yaml`, and the branch reached `git log` and gitleaks on the
    HOST, split on whitespace: one `rite request gate` made git write a file
    of the Manager's choosing outside every boundary."""
    from rite_ai.config.parse import ParseError, parse_modules

    path = project / ".rite" / "modules.yaml"
    path.write_text(
        "modules:\n  svc:\n    path: svc/\n    url: ''\n    branch: "
        + json.dumps(branch)
        + "\n"
    )
    got = parse_modules(path)
    assert isinstance(got, ParseError) and "'branch'" in got.message, got


@pytest.mark.parametrize("where", ["/etc", "~/x", "../other", "svc/../../x"])
def test_a_module_path_outside_the_project_is_refused(project, where):
    from rite_ai.config.parse import ParseError, parse_modules

    path = project / ".rite" / "modules.yaml"
    path.write_text(f"modules:\n  svc:\n    path: {json.dumps(where)}\n    url: ''\n")
    got = parse_modules(path)
    assert isinstance(got, ParseError) and "'path'" in got.message, got


@pytest.mark.parametrize("branch", ["main", "develop", "release/1.2", "v0_7"])
def test_control_ordinary_branches_still_parse(project, branch):
    from rite_ai.config.parse import parse_modules

    path = project / ".rite" / "modules.yaml"
    path.write_text(
        f"modules:\n  svc:\n    path: svc/\n    url: ''\n    branch: {branch}\n"
    )
    (module,) = parse_modules(path)
    assert module.branch == branch


def test_gate_runs_on_full_ref_names_with_the_projects_rules(project):
    """A tag named like the ticket would otherwise be what is gated; and the
    project's suppressions are its `config_root`'s."""
    from rite_ai.gate.gate import GateReport

    (project / "svc" / ".git").mkdir(parents=True)
    with (
        patch("rite_ai.publishing.deliver._sha", return_value="abc"),
        patch("rite_ai.gate.gate.run_gate", return_value=GateReport()) as gate,
    ):
        said = lifecycle._act(
            project, lifecycle.Request("gate", "alpha", "RT-12"), time.time()
        )
    assert gate.call_args.kwargs == {
        "rev_range": "refs/heads/main..refs/heads/RT-12",
        "config_root": project,
    }
    assert "no blocking findings" in said


def test_a_requested_restart_is_held_back_across_recoverys_prune(project):
    """🔴 SCRUM-59 correctness review, measured: recovery prunes the ledger
    of every Worker not stalled, in the same cycle, so six requests 10s apart
    made six restarts. Here recovery runs between the requests."""
    from rite_ai.managers import recovery

    restarts = []
    with patch.object(
        recovery,
        "_restart",
        side_effect=lambda r, w: restarts.append(w) or (True, "ok"),
    ):
        for i in range(6):
            t = 100_000.0 + i * 10
            lifecycle._act(project, lifecycle.Request("restart", "alpha"), t)
            recovery.recover_stalled_workers(
                project, "lead", lambda s: None, now=t, stalls_of=lambda: []
            )
    assert restarts == ["alpha"]


def test_the_cap_holds_within_the_window(project):
    from rite_ai.managers import recovery

    restarts, said = [], []
    with patch.object(
        recovery,
        "_restart",
        side_effect=lambda r, w: restarts.append(w) or (True, "ok"),
    ):
        t = 1_000_000.0
        for _ in range(5):
            t += 1300  # past every wait (at most 1200s here), inside the hour
            said.append(
                lifecycle._act(project, lifecycle.Request("restart", "alpha"), t)
            )
            recovery.recover_stalled_workers(
                project, "lead", lambda s: None, now=t, stalls_of=lambda: []
            )
    assert len(restarts) == recovery.DEFAULT_MAX_RESTARTS, said
    assert "the most rite does" in said[-1]


def test_the_restart_ledger_is_where_no_profile_grants(project):
    """🔴 SCRUM-59 security review, measured: in `.rite/` the Manager could
    zero it before each request and restart without limit."""
    from rite_ai.managers import manager_dir, recovery

    where = recovery._ledger_path(project)
    assert project not in where.parents
    assert manager_dir(project, "lead") not in where.parents


@pytest.mark.parametrize(
    "dirname, run",
    [
        ("requests", "start"),
        ("deliveries", "deliver"),
        ("chores", "chore"),
    ],
)
def test_the_older_request_directories_are_not_followed_either(
    project, tmp_path, dirname, run
):
    """The same host-side read, rename and delete, in the three directories
    that predate `lifecycle/` (SCRUM-59 review)."""
    from rite_ai.managers import broker, chores, manager_dir, supervise
    from rite_ai.publishing import requests as deliveries

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.json").write_text('{"worker": "alpha", "ticket": "RT-1"}')
    own = manager_dir(project, "lead") / dirname
    own.parent.mkdir(parents=True, exist_ok=True)
    own.symlink_to(elsewhere, target_is_directory=True)
    said: list[str] = []
    if run == "start":
        supervise._honour_worker_requests(
            project, "lead", lambda raw, manager="": pytest.fail("started"), said.append
        )
        assert broker.queued(project, "lead") is False
    elif run == "deliver":
        deliveries.honour_deliveries(project, "lead", said.append)
    else:
        chores.create_asked_for(project, "lead", None, said.append)
    assert any("cannot be opened as rite's own" in line for line in said), said
    assert (elsewhere / "precious.json").exists()


def test_a_requeued_request_is_never_written_through_a_link(project, tmp_path):
    from rite_ai.managers import broker, manager_dir

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    own = manager_dir(project, "lead") / "requests"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        broker.requeue(project, "lead", own / "1.json", "{}")
    assert list(elsewhere.iterdir()) == []


def test_status_says_a_question_and_unpushed_work(project):
    from rite_ai.sandbox import SandboxStatus
    from rite_ai.sandbox.questions import WorkerQuestion

    asked = WorkerQuestion("rite-x-alpha", "which schema?", "", 1.0, Path("q.json"))
    with (
        patch(
            "rite_ai.sandbox.worker_sandbox_status",
            return_value=SandboxStatus("running"),
        ),
        patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
        patch("rite_ai.sandbox.questions.pending_question", return_value=asked),
        patch("rite_ai.sandbox._work_only_in_sandbox", return_value="svc: 2 commits"),
    ):
        said = lifecycle._act(project, lifecycle.Request("status", "alpha"), 1.0)
    assert "in the Worker's words: 'which schema?'" in said
    assert "work only in the sandbox: svc: 2 commits" in said


def test_status_says_when_it_could_not_check_for_a_question(project):
    from rite_ai.sandbox import SandboxStatus
    from rite_ai.sandbox.questions import Unknown

    with (
        patch(
            "rite_ai.sandbox.worker_sandbox_status",
            return_value=SandboxStatus("running"),
        ),
        patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
        patch(
            "rite_ai.sandbox.questions.pending_question",
            return_value=Unknown("rite-x-alpha", "yoloai is gone"),
        ),
        patch("rite_ai.sandbox._work_only_in_sandbox", return_value=""),
    ):
        said = lifecycle._act(project, lifecycle.Request("status", "alpha"), 1.0)
    assert "could not check for a question (yoloai is gone)" in said


def test_a_destroy_releases_the_claims_and_forgets_the_owner(project):
    from rite_ai.sandbox import SandboxResult

    lifecycle.record_owner(project, "alpha", "lead")
    with (
        patch(
            "rite_ai.sandbox.destroy_worker", return_value=SandboxResult(True, "gone")
        ),
        patch(
            "rite_ai.publishing.deliver._release_claims",
            return_value="released 2 claim(s)",
        ),
    ):
        said = lifecycle._act(project, lifecycle.Request("destroy", "alpha"), 1.0)
    assert said == "Destroyed alpha: gone; released 2 claim(s)"
    assert lifecycle._owner_of(project, "alpha") == ""


def test_an_interrupted_claim_is_told_to_the_manager_too(project):
    _ask("destroy", "alpha")
    (pending,) = lifecycle.lifecycle_dir(project, "lead").glob("*.json")
    pending.rename(pending.with_name(pending.name + lifecycle.TAKEN))
    _honour(project)
    assert "Whether it ran is not known" in _told(project)


def test_requests_left_by_a_stopped_run_are_honoured_at_start():
    """Before the first session: the no-progress guard may hold the next
    session off indefinitely, and a request would wait with it."""
    import inspect

    from rite_ai.managers import supervise

    source = inspect.getsource(supervise._supervise)
    first_session = source.index("stalled: _Stalled | None = None")
    assert source.index("lifecycle.honour_requests(root, manager, say)") < first_session
