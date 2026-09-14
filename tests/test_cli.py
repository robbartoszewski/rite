import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.tickets.interface import BackendError, Ticket
from tests.gate_helpers import commit_all, init_repo, write


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "rite" in result.output
    assert "0.2.0" in result.output


def test_doctor_with_no_project(tmp_path, monkeypatch):
    """No `.rite/` directory means nothing to check — this is not the
    same as "ok", which used to be printed unconditionally regardless of
    whether a project even existed (the exact "reports success on
    nothing checked" class this project's own review checklist warns
    about).

    Runs in `tmp_path`, not the invoking cwd: `doctor` resolves `.rite/`
    relative to wherever it is run, so without this the test passes only
    while rite's own checkout happens to have no `.rite/` in it — which
    stopped being true once the publish-gate suppression file landed."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    # Non-zero settles SPEC §9.11's open question. "Nothing was checked"
    # and "nothing is wrong" are different answers, and `rite doctor &&
    # rite claim ...` succeeded in a directory where rite was never set
    # up — the same shape as `rite credential check` printing `not_found`
    # and exiting 0, which §9.11 already calls a defect.
    assert result.exit_code == 1
    assert "no .rite/ directory found" in result.output
    assert "not a rite project" in result.output
    assert "ok" not in result.output


def test_doctor_healthy_project(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)
    # Sandboxing defaults on, and a sandboxed project without a Claude login
    # for its sandboxes is not healthy.
    monkeypatch.setenv("RITE_CLAUDE_TOKEN", "sk-ant-oat-test")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    # Parsed rather than stat'd: the row now names what it read.
    assert "brief.yaml: ok (" in result.output


def test_doctor_reports_schedule_problems(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "schedule:\n  windows:\n    - hours: '09:00-18:00'\n      workers: 3\n"
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code != 0
    assert "timezone" in result.output


def test_doctor_reports_missing_module_repo(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text(
        "modules:\n  backend:\n    path: backend\n    description: ''\n"
    )
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code != 0
    assert "not a git repository" in result.output


def test_claim_and_release(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()

    result = runner.invoke(cli, ["claim", "src/a.ts", "-w", "w1", "-t", "T-1"])
    assert result.exit_code == 0
    assert "claimed 1" in result.output

    result = runner.invoke(cli, ["claim", "src/a.ts", "-w", "w2"])
    assert result.exit_code != 0

    from rite_ai.coordination_cost import read_counts

    assert read_counts(tmp_path).refused_claims == 1

    result = runner.invoke(cli, ["release", "-w", "w1"])
    assert result.exit_code == 0
    assert "released" in result.output


def test_release_force(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    runner.invoke(cli, ["claim", "src/a.ts", "-w", "w1"])
    result = runner.invoke(
        cli,
        [
            "release",
            "--force",
            "src/a.ts",
            "--by",
            "ops",
            "--reason",
            "worker crashed",
        ],
    )
    assert result.exit_code == 0
    assert "force-released 1" in result.output

    result = runner.invoke(cli, ["claim", "src/a.ts", "-w", "w2"])
    assert result.exit_code == 0, "path should be free after force-release"


def test_release_force_requires_by_and_reason(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["release", "--force", "src/a.ts"])
    assert result.exit_code != 0
    assert "--by and --reason" in result.output


def test_release_without_worker_or_force_refuses(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["release"])
    assert result.exit_code != 0


def test_context_add_list_remove(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    (rite_dir / "context").mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["context", "add", "database.md", "Before migrations", "Postgres notes"],
    )
    assert result.exit_code == 0
    assert (rite_dir / "context" / "database.md").is_file()

    result = runner.invoke(cli, ["context", "list"])
    assert result.exit_code == 0
    assert "database.md" in result.output

    result = runner.invoke(cli, ["context", "remove", "database.md"])
    assert result.exit_code == 0
    assert not (rite_dir / "context" / "database.md").is_file()


def test_watchdog_ok(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["watchdog"])
    assert result.exit_code == 0
    assert "nothing needs attention" in result.output


def test_watchdog_blocker_exits_nonzero(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    from rite_ai.reporting.outbox import enqueue

    enqueue(tmp_path, "blocker", {"detail": "no staging creds"})

    runner = CliRunner()
    result = runner.invoke(cli, ["watchdog"])
    assert result.exit_code == 1
    assert "no staging creds" in result.output


def test_budget_report_with_no_configured_quota(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["budget"])
    assert result.exit_code == 0, result.output
    assert "average since" in result.output
    assert "0 tokens/hour" in result.output
    assert "% of weekly_token_budget" not in result.output
    assert "this MACHINE, all projects" in result.output


def test_budget_report_prints_no_percentage_against_a_per_project_budget(
    tmp_path, monkeypatch
):
    import json
    from datetime import UTC, datetime

    from rite_ai.budget import default_transcripts_dir

    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nbudget:\n  weekly_token_budget: 100\n"
    )
    monkeypatch.chdir(tmp_path)

    transcripts_dir = default_transcripts_dir()
    proj_dir = transcripts_dir / "some-project"
    proj_dir.mkdir(parents=True)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    (proj_dir / "s1.jsonl").write_text(
        json.dumps(
            {
                "timestamp": now,
                "sessionId": "s1",
                "message": {"usage": {"input_tokens": 1000}},
            }
        )
        + "\n"
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["budget"])
    assert result.exit_code == 0, result.output
    # 1000 machine-wide tokens against a 100-token per-project budget: the
    # same shape as the reported 2.15-billion-over-1-million reading, which
    # printed `used: 215126.9% of weekly_token_budget` and a confident
    # exhaustion warning. Neither number was a quantity — the numerator is
    # every session on the machine, the denominator is one project's target.
    assert "% of weekly_token_budget" not in result.output, result.output
    assert "warning:" not in result.output, result.output
    assert "exhaust" not in result.output, result.output
    assert "no percentage" in result.output
    # The key the user set is named rather than silently dropped.
    assert "weekly_token_budget is set to 100" in result.output


def test_budget_report_unrecognised_timezone_refuses_cleanly(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nschedule:\n  timezone: Not/A_Real_Zone\n"
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["budget"])
    assert result.exit_code == 1
    assert "cannot resolve week start" in result.output


def test_pool_status_with_no_pool_ever_filled(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["pool", "status"])
    assert result.exit_code == 0, result.output
    assert "0/2 live" in result.output  # default coordinator_standby


def test_pool_fill_missing_tmux_refuses_cleanly(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with patch("rite_ai.pool.shutil.which", return_value=None):
        result = runner.invoke(cli, ["pool", "fill"])
    assert result.exit_code == 1
    assert "tmux not found" in result.output


def test_pool_fill_starts_sessions_up_to_target(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\npool:\n  coordinator_standby: 2\n"
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with (
        patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux"),
        patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
        patch("rite_ai.pool.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(cli, ["pool", "fill", "--command", "sleep 1"])
    assert result.exit_code == 0, result.output
    assert "pool at 2/2" in result.output
    assert mock_run.call_count == 2


def test_init_yes_flag_is_fully_non_interactive(tmp_path, monkeypatch):
    """`rite_ai.cli.init`'s own module docstring has documented `--config`/
    `--yes` as init's scriptable path since it was written; the CLI
    command never exposed either flag until now — found by running
    `rite init --help` by hand and seeing neither option listed."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".rite" / "brief.yaml").is_file()


def test_init_config_flag_uses_preset_values(tmp_path, monkeypatch):
    config_file = tmp_path / "preset.yaml"
    config_file.write_text(
        "project:\n  name: acme-platform\n  role: owner\nwhat:\n  kind: backend\n"
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(config_file), "--yes"])
    assert result.exit_code == 0, result.output
    brief = (tmp_path / ".rite" / "brief.yaml").read_text()
    assert "acme-platform" in brief
    assert "backend" in brief


def test_init_config_file_not_found_errors_instead_of_silently_proceeding(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", "does-not-exist.yaml", "--yes"])
    assert result.exit_code != 0


def test_init_declining_the_wipe_prompt_prints_a_message_not_silence(
    tmp_path, monkeypatch
):
    """Previously the CLI wrapper discarded `run_init`'s return value
    entirely — re-running `rite init` on an already-initialised
    directory and declining the wipe prompt exited with NO output at
    all, indistinguishable from a hang or a crash."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["init", "--yes"])
    assert (tmp_path / ".rite").is_dir()

    result = runner.invoke(cli, ["init"], input="n\nn\n")
    assert "already exists" in result.output
    assert "left untouched" in result.output


def test_prepare_worker_workspace(tmp_path, monkeypatch):
    """SPEC §2.1's workspace-prep script existed fully built and tested
    with zero CLI callers — this is the wiring, exercised end to end
    against a real git repo (not mocked)."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    init_repo(project_root)
    write(project_root, "app.py", "print('v1')\n")
    commit_all(project_root, "v1")

    rite_dir = project_root / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text(
        "modules:\n  backend:\n    path: .\n    description: ''\n"
    )
    monkeypatch.chdir(project_root)

    runner = CliRunner()
    result = runner.invoke(cli, ["add", "worker", "alpha"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["prepare", "--worker", "alpha"])
    assert result.exit_code == 0, result.output
    assert "backend" in result.output


def test_add_worker_with_scoped_token_provisions_a_token(tmp_path, monkeypatch):
    """`rite add worker` existed with no sandbox-token step at all — this
    is that wiring (§5.3.3, §5.3.4), exercised through the CLI rather than
    only against the underlying `rite_ai.sandbox`/`rite_ai.credentials` modules.

    Asked for with `--scoped-token`. It used to fire off `sandbox.enabled`,
    which made turning sandboxing on silently change the credential model
    too."""
    from unittest.mock import patch

    project_root = tmp_path / "project"
    project_root.mkdir()
    init_repo(project_root)
    write(project_root, "app.py", "print('v1')\n")
    commit_all(project_root, "v1")

    # A REAL remote, on this machine. It used to read
    # `https://github.com/acme/widgets.git`, which `add worker` then tried to
    # clone for real: `acme/widgets` does not exist, so git asked for a
    # GitHub username and the suite BLOCKED on an interactive prompt. On a
    # machine with no credential helper that is a hang; on a stranger's
    # clone it is the first thing rite ever does.
    #
    # A bare repo under `acme/widgets.git` keeps every assertion below
    # meaningful — the clone genuinely runs and genuinely succeeds, and the
    # path still ends in `acme/widgets`, which is what the token-scope line
    # names. Not a mock: mocking the clone would have asserted nothing about
    # the step this test exists to cover.
    remote = tmp_path / "remotes" / "acme" / "widgets.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True)
    (seed / "f.txt").write_text("x\n")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        cwd=seed,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=seed,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=seed, check=True)

    rite_dir = project_root / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text(
        f"modules:\n  backend:\n    path: .\n    url: {remote}\n    description: ''\n"
    )
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
    )
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))

    runner = CliRunner()
    with (
        patch("keyring.get_password", return_value=None),
        patch("keyring.set_password") as mock_set,
        # no `gh` on PATH — skips the check
        patch("rite_ai.sandbox.shutil.which", return_value=None),
    ):
        result = runner.invoke(
            cli,
            ["add", "worker", "alpha", "--scoped-token"],
            input="y\nsecret-token\nsecret-token\n",
        )
    assert result.exit_code == 0, result.output
    assert "acme/widgets" in result.output

    # The clone actually happened, and from the REMOTE rather than by the
    # local-path fallback `add_worker` drops to when a clone fails. This is
    # what makes the local bare repo load-bearing: with the old
    # `github.com/acme/widgets.git` URL the clone failed, the fallback ran,
    # and every other assertion here still passed — so the step this test
    # names was never actually covered, and the only visible symptom was a
    # credential prompt.
    cloned = project_root / "workers" / "alpha" / "backend"
    origin_url = subprocess.run(
        ["git", "-C", str(cloned), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert origin_url == str(remote), (
        f"workspace was not cloned from the module's remote: {origin_url!r}"
    )
    # Namespaced per §10.2 — the account carries the project's namespace,
    # while the KEY the user types stays `sandbox_token_alpha`.
    assert "/sandbox_token_alpha'" in result.output
    # The SERVICE is still `rite`; the ACCOUNT now carries this project's
    # namespace, which is the whole of §10.2. Asserted by shape rather than
    # by a literal, because the namespace's suffix is random by design.
    (service, account, secret), _ = mock_set.call_args
    assert service == "rite"
    assert account.endswith("/sandbox_token_alpha")
    assert secret == "secret-token"


def test_add_worker_sandbox_disabled_skips_token_provisioning(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    init_repo(project_root)
    write(project_root, "app.py", "print('v1')\n")
    commit_all(project_root, "v1")

    rite_dir = project_root / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text(
        "modules:\n  backend:\n    path: .\n    description: ''\n"
    )
    monkeypatch.chdir(project_root)

    runner = CliRunner()
    result = runner.invoke(cli, ["add", "worker", "alpha"])
    assert result.exit_code == 0, result.output
    assert "sandbox" not in result.output.lower()


def test_sandbox_start_passes_provisioned_token_through(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        "  backend: seatbelt\n"
    )
    (tmp_path / "workers" / "alpha").mkdir(parents=True)
    (tmp_path / "workers" / "alpha" / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_SANDBOX_TOKEN_ALPHA", raising=False)

    runner = CliRunner()
    with (
        patch("keyring.get_password", return_value="the-stored-token"),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run") as mock_run,
    ):
        # `ls` and `new` answered separately. One blank answer for every
        # call used to work only because an unreadable `yoloai ls` was
        # read as "zero sandboxes running" — the worker-cap fail-open.
        def _run(args, *a, **kw):
            stdout = '{"sandboxes": []}' if "ls" in args else ""
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        mock_run.side_effect = _run
        result = runner.invoke(cli, ["sandbox", "start", "alpha"])
    assert result.exit_code == 0, result.output
    args = mock_run.call_args[0][0]
    assert "--backend" in args
    assert args[args.index("--backend") + 1] == "seatbelt"
    env_values = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
    assert "GITHUB_TOKEN=the-stored-token" in env_values


def _sandbox_project(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        "  backend: seatbelt\n"
    )
    (tmp_path / "workers" / "alpha").mkdir(parents=True)
    (tmp_path / "workers" / "alpha" / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
    )
    monkeypatch.chdir(tmp_path)


def test_sandbox_start_ticket_becomes_the_opening_prompt(tmp_path, monkeypatch):
    """`--ticket` is how a sandboxed Worker learns what to do: nothing in
    rite can type into the session once it is running."""
    _sandbox_project(tmp_path, monkeypatch)
    seen: dict = {}

    def _run(args, *a, **kw):
        if "new" in args:
            seen["prompt"] = Path(args[args.index("--prompt-file") + 1]).read_text()
            seen["args"] = args
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=_run),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--ticket", "ABC-12"]
        )
    assert result.exit_code == 0, result.output
    assert seen["prompt"] == "Work ticket ABC-12.\n"
    assert "yoloai attach rite-" in result.output


def test_sandbox_start_refuses_ticket_and_prompt_together(tmp_path, monkeypatch):
    _sandbox_project(tmp_path, monkeypatch)
    with patch("rite_ai.sandbox.subprocess.run") as mock_run:
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--ticket", "ABC-12", "--prompt", "x"]
        )
    assert result.exit_code == 2, result.output
    assert "not both" in result.output
    mock_run.assert_not_called()


def test_sandbox_start_refuses_a_blank_ticket(tmp_path, monkeypatch):
    _sandbox_project(tmp_path, monkeypatch)
    with patch("rite_ai.sandbox.subprocess.run") as mock_run:
        result = CliRunner().invoke(cli, ["sandbox", "start", "alpha", "--ticket", " "])
    assert result.exit_code == 2, result.output
    assert "needs a ticket ID" in result.output
    mock_run.assert_not_called()


def test_sandbox_start_refuses_an_unprepared_workspace_and_says_what_to_do(
    tmp_path, monkeypatch
):
    """The sandbox copies the workspace and cannot run `rite prepare`, so a
    Worker started on a dirty or blocked checkout would work on stale code
    silently. Start prepares first and refuses, naming the way out."""
    from rite_ai.workspace.prepare import ModulePrepResult, WorkspacePrepResult

    _sandbox_project(tmp_path, monkeypatch)
    blocked = WorkspacePrepResult(
        ok=False,
        modules=[
            ModulePrepResult("app", "dirty", False, "uncommitted changes present")
        ],
    )
    with (
        patch("rite_ai.workspace.prepare_workspace", return_value=blocked),
        patch("rite_ai.sandbox.subprocess.run") as mock_run,
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--ticket", "ABC-1"]
        )
    assert result.exit_code == 1, result.output
    assert "not starting 'alpha'" in result.output
    assert "commit and push, or stash" in result.output
    mock_run.assert_not_called()


def test_sandbox_start_prepares_before_it_starts(tmp_path, monkeypatch):
    from rite_ai.workspace.prepare import WorkspacePrepResult

    _sandbox_project(tmp_path, monkeypatch)
    order: list[str] = []

    def prep(*a, **kw):
        order.append("prepare")
        return WorkspacePrepResult(ok=True, modules=[])

    def run(args, *a, **kw):
        if "new" in args:
            order.append("new")
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.workspace.prepare_workspace", side_effect=prep),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--ticket", "ABC-1"]
        )
    assert result.exit_code == 0, result.output
    assert order == ["prepare", "new"]


def test_sandbox_start_allow_dirty_skips_prepare_and_says_so(tmp_path, monkeypatch):
    _sandbox_project(tmp_path, monkeypatch)

    def run(args, *a, **kw):
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.workspace.prepare_workspace") as mock_prep,
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "ABC-1"]
        )
    assert result.exit_code == 0, result.output
    assert "not preparing workers/alpha/" in result.output
    mock_prep.assert_not_called()


def test_sandbox_start_warns_about_instructions_from_before_sandboxing(
    tmp_path, monkeypatch
):
    _sandbox_project(tmp_path, monkeypatch)
    claude_md = next(tmp_path.rglob("workers/alpha")) / "CLAUDE.md"
    claude_md.write_text("# Worker alpha\n\nOld instructions.\n")

    def run(args, *a, **kw):
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "ABC-1"]
        )
    assert "was written before sandboxed Workers" in result.output
    assert "rite remove worker alpha" in result.output


def test_sandbox_destroy_force_reaches_destroy_worker(tmp_path, monkeypatch):
    from rite_ai.sandbox import SandboxResult

    _sandbox_project(tmp_path, monkeypatch)
    with patch(
        "rite_ai.sandbox.destroy_worker", return_value=SandboxResult(True, "gone")
    ) as destroy:
        result = CliRunner().invoke(cli, ["sandbox", "destroy", "alpha", "--force"])
    assert result.exit_code == 0, result.output
    assert destroy.call_args.kwargs["force"] is True


def test_sandbox_pane_never_prints_an_injected_credential(tmp_path, monkeypatch):
    """The pane is read by Claude sessions. A stored credential that appears
    on screen — wrapped across lines, and not in an export statement — must
    not reach the output."""
    _sandbox_project(tmp_path, monkeypatch)
    for name in (
        "RITE_JIRA_TOKEN",
        "JIRA_API_TOKEN",
        "GITHUB_TOKEN",
        "RITE_GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    known = "ghp_KnownStoredValue0123456789abcdef"
    screen = f"$ printenv GITHUB_TOKEN\n{known[:15]}\n{known[15:]}\n$ "

    def run(args, *a, **kw):
        if "ls" in args:
            return MagicMock(returncode=0, stdout='{"sandboxes": []}', stderr="")
        return MagicMock(returncode=0, stdout=screen, stderr="")

    with (
        patch("keyring.get_password", return_value=known),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
    ):
        result = CliRunner().invoke(cli, ["sandbox", "pane", "alpha"])
    assert result.exit_code == 0, result.output
    assert known not in result.output.replace("\n", "")
    assert "[redacted]" in result.output


def _doctor_with_sandbox(tmp_path, monkeypatch, extra_env=None):
    from types import SimpleNamespace

    _sandbox_project(tmp_path, monkeypatch)
    monkeypatch.delenv("RITE_CLAUDE_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    ok = SimpleNamespace(
        ok=True, installed=True, backend="seatbelt", detail="ok", elapsed_ms=1
    )
    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.verify_sandbox", return_value=ok),
    ):
        return CliRunner().invoke(cli, ["doctor"])


def test_doctor_calls_a_missing_sandbox_login_a_problem(tmp_path, monkeypatch):
    """Without it a sandboxed Worker starts and silently does nothing."""
    result = _doctor_with_sandbox(tmp_path, monkeypatch)
    assert result.output.count("credential claude_token: not set") == 1
    assert "no Claude login is stored for sandboxes" in result.output
    assert "rite credential set claude" in result.output
    assert result.exit_code != 0


def test_doctor_is_quiet_about_the_login_once_it_is_stored(tmp_path, monkeypatch):
    """The same project with the credential present: the problem goes, and
    only that problem — asserted as a difference, not a bare exit code."""
    result = _doctor_with_sandbox(
        tmp_path, monkeypatch, {"RITE_CLAUDE_TOKEN": "sk-ant-oat-test"}
    )
    assert "no Claude login is stored for sandboxes" not in result.output
    assert "sk-ant-oat-test" not in result.output


def test_credential_list_names_the_claude_login_when_sandboxing_is_on(
    tmp_path, monkeypatch
):
    _sandbox_project(tmp_path, monkeypatch)
    with patch("keyring.get_password", return_value=None):
        result = CliRunner().invoke(cli, ["credential", "list"])
    assert "claude_token" in result.output, result.output


def test_prepare_unknown_worker_refuses(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["prepare", "--worker", "ghost"])
    assert result.exit_code != 0
    assert "no such worker" in result.output


def test_projects_add_list_remove(tmp_path, monkeypatch):
    dispatch_dir = tmp_path / "dispatch"
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(dispatch_dir))
    project_dir = tmp_path / "acme"
    project_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["projects", "add", "acme", str(project_dir)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["projects", "list"])
    assert result.exit_code == 0
    assert "acme" in result.output

    result = runner.invoke(cli, ["projects", "remove", "acme"])
    assert result.exit_code == 0

    result = runner.invoke(cli, ["projects", "list"])
    assert "no projects registered" in result.output


def test_start_resolves_a_registered_alias(tmp_path, monkeypatch):
    dispatch_dir = tmp_path / "dispatch"
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(dispatch_dir))
    project_dir = tmp_path / "acme"
    rite_dir = project_dir / ".rite"
    rite_dir.mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")

    runner = CliRunner()
    runner.invoke(cli, ["projects", "add", "acme", str(project_dir)])

    result = runner.invoke(cli, ["start", "acme"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output


def test_status_aggregation_mode_outside_any_project(tmp_path, monkeypatch):
    dispatch_dir = tmp_path / "dispatch"
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(dispatch_dir))
    project_dir = tmp_path / "acme"
    (project_dir / ".rite").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)  # NOT inside any project — no .rite/ here

    runner = CliRunner()
    runner.invoke(cli, ["projects", "add", "acme", str(project_dir)])

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "acme" in result.output
    assert "registered project" in result.output


def test_status_with_no_dispatch_directory_and_no_project(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "no Dispatch directory found" in result.output


def test_schedule_set_upsert_and_show(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["schedule", "set", "09:00-18:00", "3"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["schedule", "set", "18:00-09:00", "0"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["schedule", "set-timezone", "Europe/Warsaw"])
    assert result.exit_code == 0

    result = runner.invoke(cli, ["schedule", "show"])
    assert result.exit_code == 0
    assert "Europe/Warsaw" in result.output
    assert "09:00-18:00" in result.output


def test_schedule_set_does_not_lock_itself_out(tmp_path, monkeypatch):
    """Regression: a window written before a timezone is set used to make
    config.yaml entirely unparseable, including to the very next
    `rite schedule set-timezone` call meant to fix it."""
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["schedule", "set", "09:00-18:00", "3"])
    assert result.exit_code == 0

    result = runner.invoke(cli, ["schedule", "set-timezone", "UTC"])
    assert result.exit_code == 0, result.output
    assert "config error" not in result.output


def test_schedule_set_refuses_over_cap_not_clamp(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("sandbox:\n  max_concurrent_workers: 5\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["schedule", "set", "09:00-18:00", "10"])
    assert result.exit_code != 0
    assert "exceeding" in result.output


def test_handover_write_and_show(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["handover", "show"])
    assert result.exit_code == 0
    assert "no handover snapshot" in result.output

    result = runner.invoke(
        cli,
        [
            "handover",
            "write",
            "--ticket",
            "ABC-9",
            "--progress",
            "wiring the CLI",
            "--next-step",
            "add tests",
            "--blocker",
            "waiting on x",
        ],
    )
    assert result.exit_code == 0

    result = runner.invoke(cli, ["handover", "show"])
    assert result.exit_code == 0
    assert "ABC-9" in result.output
    assert "waiting on x" in result.output


def test_status(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "no active claims" in result.output

    runner.invoke(cli, ["claim", "src/x.ts", "-w", "w1", "-t", "PROJ-5"])
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "w1" in result.output
    assert "src/x.ts" in result.output


def test_help_is_not_byte_for_byte_identical_to_bare_help(monkeypatch):
    """`rite help`'s own docstring promises "with examples" — previously
    it just called `cli.get_help(ctx)`, i.e. printed the exact same text
    as `rite --help`, delivering neither friendliness nor examples.
    Found by running `rite help` by hand and comparing it to `--help`."""
    runner = CliRunner()
    bare = runner.invoke(cli, ["--help"])
    friendly = runner.invoke(cli, ["help"])
    assert friendly.exit_code == 0
    assert friendly.output != bare.output
    assert "rite init" in friendly.output  # a real usage example
    assert "Getting started" not in bare.output  # bare --help has no such section


def test_credential_check_env(monkeypatch):
    monkeypatch.setenv("RITE_JIRA_TOKEN", "x")
    runner = CliRunner()
    result = runner.invoke(cli, ["credential", "check", "jira_token"])
    assert result.exit_code == 0
    assert "env" in result.output


def test_credential_rotate_with_nothing_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["credential", "rotate"])
    assert result.exit_code == 0
    assert "nothing to rotate" in result.output


def test_credential_rotate_walks_through_and_replaces(tmp_path, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    with patch("keyring.set_password"):
        from rite_ai.credentials.store import store

        store("jira_token", "old-value")

    runner = CliRunner()
    with (
        patch("keyring.get_password", return_value="old-value"),
        patch("keyring.set_password") as mock_set,
    ):
        result = runner.invoke(
            cli, ["credential", "rotate"], input="y\nnew-value\nnew-value\n"
        )
    assert result.exit_code == 0
    assert "jira_token rotated" in result.output
    mock_set.assert_called_once_with("rite", "jira_token", "new-value")


def test_credential_rotate_skip_leaves_it_untouched(tmp_path, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    with patch("keyring.set_password"):
        from rite_ai.credentials.store import store

        store("jira_token", "old-value")

    runner = CliRunner()
    with (
        patch("keyring.get_password", return_value="old-value"),
        patch("keyring.set_password") as mock_set,
    ):
        result = runner.invoke(cli, ["credential", "rotate"], input="n\n")
    assert result.exit_code == 0
    mock_set.assert_not_called()


def test_stop_accepts_explicit_ticket(tmp_path, monkeypatch):
    """The CLI-level piece of the fix that made `perform_handover`'s
    ticket-backend delivery reachable at all — `stop` used to have no way
    to pass a ticket through."""
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["stop", "--worker", "alpha", "--ticket", "ABC-9", "--reason", "eod"]
    )
    assert result.exit_code == 0
    assert "stopped" in result.output


def _rite_project_with_backend(tmp_path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: jira\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
    )
    return tmp_path


def test_publish_pre_push_nothing_to_scan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["publish", "pre-push"], input="")
    assert result.exit_code == 0
    assert "nothing to scan" in result.output


def test_board_create_no_backend_configured(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "create", "Fix the bug"])
    assert result.exit_code != 0
    assert "no ticket backend configured" in result.output


@patch("rite_ai.tickets.create_backend")
def test_board_create_routes_to_backend(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.create.return_value = Ticket(
        id="ABC-1", title="Fix the bug", url="https://x/ABC-1"
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "create", "Fix the bug"])
    assert result.exit_code == 0
    assert "ABC-1" in result.output
    backend.create.assert_called_once_with("Fix the bug", description="", labels=[])
    # default role is "workers" — the ABC board, per the sequencing override
    assert mock_create.call_args.kwargs["board_role"] == "workers"


@patch("rite_ai.tickets.create_backend")
def test_board_link_reports_backend_error(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.link.return_value = BackendError("no link support")

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "link", "ABC-1", "XYZ-1"])
    assert result.exit_code != 0
    assert "no link support" in result.output


@patch("rite_ai.tickets.create_backend")
def test_board_link_routes_to_board_role_by_default_workers(
    mock_create, tmp_path, monkeypatch
):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.link.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "link", "ABC-1", "XYZ-1"])
    assert result.exit_code == 0
    backend.link.assert_called_once_with("ABC-1", "XYZ-1", "Blocks")


@patch("rite_ai.tickets.create_backend")
def test_board_move(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.move.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "move", "ABC-1", "In Progress"])
    assert result.exit_code == 0
    backend.move.assert_called_once_with("ABC-1", "In Progress")


@patch("rite_ai.tickets.create_backend")
def test_board_list(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.list_tickets.return_value = [Ticket(id="ABC-1", title="Fix the bug")]

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "list", "--status", "In Progress"])
    assert result.exit_code == 0
    assert "ABC-1" in result.output
    filters = backend.list_tickets.call_args[0][0]
    assert filters.status == "In Progress"


@patch("rite_ai.tickets.create_backend")
def test_board_query(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.query.return_value = [Ticket(id="ABC-1", title="Fix the bug")]

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "query", "labels = scheduled"])
    assert result.exit_code == 0
    assert "ABC-1" in result.output
    backend.query.assert_called_once_with("labels = scheduled")


@patch("rite_ai.tickets.create_backend")
def test_board_label(mock_create, tmp_path, monkeypatch):
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.label.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "label", "ABC-1", "alpha", "scheduled"])
    assert result.exit_code == 0
    backend.label.assert_called_once_with("ABC-1", ["alpha", "scheduled"], remove=[])


@patch("rite_ai.tickets.create_backend")
def test_board_create_role_board_routes_to_board_project(
    mock_create, tmp_path, monkeypatch
):
    """The `--role` flag is what the sequencing override's board-linking
    scope depends on — confirm it actually reaches `create_backend`, not
    just that the default (`workers`) is passed."""
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.create.return_value = Ticket(id="XYZ-1", title="New feature")

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "create", "New feature", "--role", "board"])
    assert result.exit_code == 0
    assert mock_create.call_args.kwargs["board_role"] == "board"


def test_pool_archive_releases_a_dead_slots_orphaned_claim(tmp_path, monkeypatch):
    """The command a human runs after a session dies without releasing
    its claims — the claim is gone and the reason is on record."""
    import json
    import time

    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.pool import slot_name

    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\npool:\n  archive_after_minutes: 30\n"
    )
    dead = slot_name(tmp_path, 0)
    now = time.time()
    (rite_dir / "pool.json").write_text(
        json.dumps(
            {
                "slots": [
                    {
                        "name": dead,
                        "created_at": now - 7200,
                        "last_live_at": now - 7200,
                        "worker": dead,
                        "unreachable_since": now - 7200,
                    }
                ]
            }
        )
    )
    ledger = ClaimsLedger(rite_dir / "claims.json")
    ledger.claim(["src/payments"], dead, "PAY-1")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with (
        patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux"),
        patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
    ):
        result = runner.invoke(cli, ["pool", "archive"])

    assert result.exit_code == 0, result.output
    assert "released 1 orphaned claim(s)" in result.output
    assert "src/payments" in result.output
    assert ledger.list_claims() == []
    assert (rite_dir / "pool-archive.jsonl").is_file()


def test_pool_archive_without_tmux_refuses_rather_than_guessing(tmp_path, monkeypatch):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with patch("rite_ai.pool.shutil.which", return_value=None):
        result = runner.invoke(cli, ["pool", "archive"])
    assert result.exit_code == 1
    assert "cannot verify" in result.output


def _minimal_project(tmp_path, worker: str = "alpha"):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    workers = tmp_path / "workers" / worker
    workers.mkdir(parents=True)
    (workers / "worker.yml").write_text(f"worker:\n  name: {worker}\n  modules: []\n")
    return rite_dir


def test_registered_worker_without_a_beat_is_stalled(tmp_path, monkeypatch):
    """The state `rite heartbeat` exists to get out of: `write_heartbeat`
    had no caller anywhere, so every registered Worker read as stalled
    forever and `rite watchdog` exited 1 on every healthy project."""
    _minimal_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    assert "STALLED" in runner.invoke(cli, ["status"]).output
    assert runner.invoke(cli, ["watchdog"]).exit_code == 1


def test_heartbeat_clears_the_stall(tmp_path, monkeypatch):
    """The wiring itself, across the seam that was missing: the CLI beat
    is what `rite status` and `rite watchdog` read back."""
    _minimal_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    beat = runner.invoke(cli, ["heartbeat", "--worker", "alpha", "--ticket", "ABC-12"])
    assert beat.exit_code == 0
    assert "alpha" in beat.output

    status = runner.invoke(cli, ["status"])
    assert status.exit_code == 0
    assert "STALLED" not in status.output

    watchdog = runner.invoke(cli, ["watchdog"])
    assert watchdog.exit_code == 0
    assert "nothing needs attention" in watchdog.output


def test_doctor_flags_missing_yoloai_only_when_sandbox_enabled(tmp_path, monkeypatch):
    """`sandbox.is_available` documents `rite doctor` as its caller and had
    none. Sandboxing is optional (SPEC §5.3), so a missing binary is a
    problem only for a project that asked for it."""
    rite_dir = _minimal_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    with patch("rite_ai.sandbox._yoloai_binary", return_value=None):
        # Stated explicitly rather than relying on the default, which is
        # now on (D-51) — this test is about the severity following the
        # setting, not about what the setting happens to default to.
        (rite_dir / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nsandbox:\n  enabled: false\n"
        )
        off = runner.invoke(cli, ["doctor"])
        assert off.exit_code == 0
        assert "not required" in off.output

        (rite_dir / "config.yaml").write_text(
            "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        )
        on = runner.invoke(cli, ["doctor"])
        assert on.exit_code == 1
        # The row names the missing tool and where to get it; the severity
        # still follows `sandbox.enabled`, which is what this test is for.
        assert "yoloai is not installed" in on.output
        assert "yoloai.dev" in on.output


def test_pool_history_reads_back_what_archive_recorded(tmp_path, monkeypatch):
    """`read_archive` had no caller: `pool archive` wrote
    `.rite/pool-archive.jsonl` and nothing could read it, so the record of
    which claim was force-released and why was unreachable."""
    import json
    import time

    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.pool import slot_name

    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    # Declares a PROJECT, not just a `.rite/`. `_find_project_root` keys on
    # brief.yaml/modules.yaml so that a module which is itself a rite repo
    # does not capture its own workers — see test_nested_project_root.py.
    (rite_dir / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\npool:\n  archive_after_minutes: 30\n"
    )
    dead = slot_name(tmp_path, 0)
    now = time.time()
    (rite_dir / "pool.json").write_text(
        json.dumps(
            {
                "slots": [
                    {
                        "name": dead,
                        "created_at": now - 7200,
                        "last_live_at": now - 7200,
                        "worker": dead,
                        "unreachable_since": now - 7200,
                    }
                ]
            }
        )
    )
    ClaimsLedger(rite_dir / "claims.json").claim(["src/payments"], dead, "PAY-1")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    assert "nothing archived yet" in runner.invoke(cli, ["pool", "history"]).output

    with (
        patch("rite_ai.pool.shutil.which", return_value="/usr/local/bin/tmux"),
        patch("rite_ai.pool.is_tmux_session_alive", return_value=False),
    ):
        assert runner.invoke(cli, ["pool", "archive"]).exit_code == 0

    history = runner.invoke(cli, ["pool", "history"])
    assert history.exit_code == 0
    assert dead in history.output
    assert "src/payments" in history.output
    assert "PAY-1" in history.output
    assert "shutdown hook" in history.output


@patch("rite_ai.tickets.create_backend")
def test_board_assign_reaches_the_backend(mock_create, tmp_path, monkeypatch):
    """`TicketBackend.assign` and both its implementations had no caller:
    SPEC §6.1 puts `assign` in the backend interface and `rite board` is
    the mechanical CRUD surface for it, but no subcommand exposed it."""
    root = _rite_project_with_backend(tmp_path)
    monkeypatch.chdir(root)
    backend = mock_create.return_value
    backend.assign.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "assign", "ABC-1", "alpha"])
    assert result.exit_code == 0, result.output
    backend.assign.assert_called_once_with("ABC-1", "alpha")


def test_release_history_reads_back_the_force_release_audit(tmp_path, monkeypatch):
    """`ClaimsLedger.force_release_audit` had no caller: `force_release`
    wrote the audit trail and nothing could read it, so "why did my claim
    disappear?" had no answer on the CLI."""
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    empty = runner.invoke(cli, ["release", "--history"])
    assert "no force-releases recorded" in empty.output

    runner.invoke(
        cli, ["claim", "src/payments", "--worker", "alpha", "--ticket", "PAY-1"]
    )
    forced = runner.invoke(
        cli,
        [
            "release",
            "--force",
            "src/payments",
            "--by",
            "ops",
            "--reason",
            "alpha crashed",
        ],
    )
    assert forced.exit_code == 0, forced.output

    history = runner.invoke(cli, ["release", "--history"])
    assert history.exit_code == 0
    assert "by ops" in history.output
    assert "alpha crashed" in history.output
    assert "took from alpha [PAY-1]: src/payments" in history.output
