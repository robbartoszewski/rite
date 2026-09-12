"""Regression tests for the defects found by rehearsal round 2.

Round 2 drove the installed CLI as a new user would — every command with
no arguments, with wrong arguments, and against an empty project — over
the command groups round 1 did not reach: board, credential, scheduler,
sandbox, pool. Each test below reproduces the ORIGINAL SYMPTOM, so it
fails against the code as it was, not merely passes against the fix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.help import preserve_indented_blocks
from rite_ai.cli.main import cli
from rite_ai.reporting.outbox import list_pending
from rite_ai.scheduler import run_tick
from rite_ai.tickets import PAGE_LIMIT, BackendError, GitHubBackend
from rite_ai.tickets.jira import JiraBackend, JiraConfig, normalise_site


def _command_starts(line: str) -> list[str]:
    """Where an example command begins on this line. Matched on a word
    boundary, so the "rite " inside "write --ticket" is not one."""
    return re.findall(r"(?:^|\s)rite\s", line)


def _project(tmp_path: Path, workers: list[str] | None = None, **config) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    body = "ticket_backend:\n  type: none\n"
    body += "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    if "watchdog_interval" in config:
        body += f"watchdog:\n  interval_minutes: {config['watchdog_interval']}\n"
    (rite_dir / "config.yaml").write_text(body)
    for name in workers or []:
        worker_dir = tmp_path / "workers" / name
        worker_dir.mkdir(parents=True, exist_ok=True)
        (worker_dir / "worker.yml").write_text(
            f'worker:\n  name: "{name}"\n  modules: []\n'
        )
    return tmp_path


# --- scheduler-tick: the outbox echo loop -----------------------------------


class TestSchedulerTickDoesNotEchoItself:
    """`rite scheduler-tick` recorded a blocker built from the watchdog's
    reasons — and the watchdog's reasons include the blockers it reads back
    out of that same outbox. Every tick therefore re-reported the previous
    tick's report: one outbox file per tick, each roughly twice the size of
    the last. At the 5-minute cadence `rite scheduler install` registers,
    an unattended stall filled the disk within hours."""

    def test_repeated_ticks_do_not_multiply_outbox_entries(self, tmp_path: Path):
        root = _project(tmp_path, workers=["alpha"])  # no heartbeat => stalled

        for _ in range(5):
            run_tick(root)

        blockers = [m for m in list_pending(root) if m.kind == "blocker"]
        assert len(blockers) == 1, (
            f"one standing stall produced {len(blockers)} outbox entries — "
            "each tick recorded the problem again"
        )

    def test_recorded_detail_does_not_grow_with_each_tick(self, tmp_path: Path):
        """Measured on the NEWEST entry and on the outbox as a whole — the
        oldest entry was always small, which is exactly why the growth was
        invisible until the disk filled."""
        root = _project(tmp_path, workers=["alpha"])

        run_tick(root)
        first = _outbox_bytes(root)
        for _ in range(6):
            run_tick(root)
        after = _outbox_bytes(root)

        assert after == first, (
            f"the outbox grew from {first} to {after} bytes over six ticks "
            "with one unchanging problem"
        )
        assert "blocker in outbox" not in _blocker_details(root)[-1], (
            "the tick wrote its own previous report back into the outbox — "
            "this is the doubling"
        )

    def test_recorded_detail_names_the_stalled_worker(self, tmp_path: Path):
        root = _project(tmp_path, workers=["alpha"])
        run_tick(root)
        assert "alpha" in _blocker_details(root)[0]


def _outbox_bytes(root: Path) -> int:
    return sum(len(d) for d in _blocker_details(root))


def _blocker_details(root: Path) -> list[str]:
    return [
        m.payload.get("detail", "") for m in list_pending(root) if m.kind == "blocker"
    ]


class TestSchedulerTickSaysWhatItFound:
    """The tick reported `needs attention — 1 reason(s)` and dropped the
    reasons on the floor, and said nothing at all on a clean cycle. This is
    the command cron/launchd runs into `.rite/scheduler.log`: a count with
    no reason is unactionable, and an empty log is indistinguishable from a
    scheduler that never fired."""

    def test_messages_carry_the_reason_not_a_count(self, tmp_path: Path):
        root = _project(tmp_path, workers=["alpha"])
        result = run_tick(root)

        joined = "\n".join(result.messages)
        assert "alpha" in joined, f"the stalled worker is not named: {joined!r}"
        assert "reason(s)" not in joined, (
            f"reported a count instead of the reason: {joined!r}"
        )

    def test_clean_cycle_still_prints_something(self, tmp_path: Path):
        root = _project(tmp_path)
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["scheduler-tick"])

        assert result.exit_code == 0
        assert result.output.strip(), "a healthy tick printed nothing at all"


# --- scheduler install: the configured cadence ------------------------------


class TestSchedulerInstallUsesConfiguredInterval:
    """`rite scheduler install` hardcoded a 5-minute cadence, ignoring
    `watchdog.interval_minutes`. A user who set 30 in config.yaml got an
    agent firing every 5 minutes and was told nothing."""

    def test_install_reads_watchdog_interval_from_config(self, tmp_path: Path):
        root = _project(tmp_path, watchdog_interval=30)
        captured = {}

        def fake_install(root_arg, interval_minutes, backend):
            captured["interval"] = interval_minutes
            return MagicMock(ok=True, message="installed")

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            with patch("rite_ai.scheduler.install", side_effect=fake_install):
                result = CliRunner().invoke(cli, ["scheduler", "install"])

        assert result.exit_code == 0, result.output
        assert captured["interval"] == 30, (
            f"config said 30 minutes, installer used {captured['interval']}"
        )

    def test_explicit_flag_still_wins(self, tmp_path: Path):
        root = _project(tmp_path, watchdog_interval=30)
        captured = {}

        def fake_install(root_arg, interval_minutes, backend):
            captured["interval"] = interval_minutes
            return MagicMock(ok=True, message="installed")

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            with patch("rite_ai.scheduler.install", side_effect=fake_install):
                result = CliRunner().invoke(
                    cli, ["scheduler", "install", "--interval-minutes", "7"]
                )

        assert result.exit_code == 0, result.output
        assert captured["interval"] == 7

    def test_install_states_the_cadence_it_registered(self, tmp_path: Path):
        root = _project(tmp_path, watchdog_interval=30)
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            with patch(
                "rite_ai.scheduler.install",
                return_value=MagicMock(ok=True, message="installed /some/path"),
            ):
                result = CliRunner().invoke(cli, ["scheduler", "install"])

        assert "30" in result.output, (
            f"nothing in the output says how often it will run: {result.output!r}"
        )


# --- pool: real sessions, reported by name ----------------------------------


class TestPoolNamesTheSessionsItStarts:
    """`rite pool fill` launches real detached tmux sessions running
    `claude` and reported only `pool at 2/2` — never that anything was
    started, never the names, and §2.5.1's takeover path is a human
    attaching to one by name."""

    def test_fill_message_names_each_started_session(self, tmp_path: Path):
        from rite_ai.config.models import PoolConfig
        from rite_ai.pool import fill

        with patch("rite_ai.pool._tmux_binary", return_value="/usr/bin/tmux"):
            with patch("rite_ai.pool.is_tmux_session_alive", return_value=False):
                with patch(
                    "rite_ai.pool.subprocess.run",
                    return_value=MagicMock(returncode=0, stderr="", stdout=""),
                ):
                    result = fill(tmp_path, PoolConfig(coordinator_standby=2))

        assert result.ok
        assert len(result.started) == 2
        for name in result.started:
            assert name in result.message, (
                f"started session {name!r} is not named in the output: "
                f"{result.message!r}"
            )
        assert "tmux attach" in result.message, (
            "no way to reach a session that was just started"
        )

    def test_status_lists_live_slot_names(self, tmp_path: Path):
        root = _project(tmp_path)
        state = root / ".rite" / "pool.json"
        state.write_text(
            json.dumps(
                {
                    "slots": [
                        {
                            "name": "rite-pool-x-0",
                            "created_at": 1.0,
                            "last_live_at": None,
                        },
                    ]
                }
            )
        )
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            with patch("rite_ai.pool.is_tmux_session_alive", return_value=True):
                result = CliRunner().invoke(cli, ["pool", "status"])

        assert "rite-pool-x-0" in result.output, (
            f"live slots were counted but not named: {result.output!r}"
        )


# --- board: the silent 100-ticket cap ---------------------------------------


class TestBoardListingsDeclareTruncation:
    """Both backends page at 100 and said nothing about it. A board with
    900 open issues rendered exactly 100 rows and stopped — a number framed
    so it reads as "this is the board"."""

    def _gh_json(self, count: int) -> str:
        return json.dumps(
            [
                {"number": i, "title": f"issue {i}", "state": "OPEN"}
                for i in range(count)
            ]
        )

    def test_github_list_flags_a_truncated_page(self):
        backend = GitHubBackend("owner/repo")
        with patch.object(backend, "_gh", return_value=self._gh_json(PAGE_LIMIT + 1)):
            page = backend.list_tickets()

        assert len(page) == PAGE_LIMIT
        assert page.truncated is True

    def test_github_list_does_not_cry_wolf_on_a_complete_page(self):
        backend = GitHubBackend("owner/repo")
        with patch.object(backend, "_gh", return_value=self._gh_json(7)):
            page = backend.list_tickets()

        assert len(page) == 7
        assert page.truncated is False

    def test_cli_says_there_are_more(self):
        from rite_ai.tickets import paginate
        from rite_ai.tickets.interface import Ticket

        page = paginate(
            [Ticket(id=str(i), title=f"t{i}") for i in range(PAGE_LIMIT + 1)]
        )
        backend = MagicMock()
        backend.list_tickets.return_value = page

        with patch("rite_ai.cli.main._ticket_backend", return_value=(backend, None)):
            result = CliRunner().invoke(cli, ["board", "list"])

        assert "there are more" in result.output, (
            "a truncated listing looked identical to a complete one"
        )

    def test_jira_search_never_asks_for_more_than_a_page(self):
        """Truncation must not be inferred from the row count.

        This test used to assert the opposite — that the search asked for
        `PAGE_LIMIT + 1` so a full page proved there was more. Verified
        against a real JIRA Cloud instance, that technique is unsound: the
        server is free to cap `maxResults`, and being handed 100 rows after
        asking for 101 is indistinguishable from a board holding exactly
        100. Ask for a page, and let the API say whether there is another.
        """
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("params", {}))
            return {
                "issues": [{"key": f"P-{i}"} for i in range(PAGE_LIMIT)],
                "isLast": True,
            }

        with patch.object(backend, "_request", side_effect=fake_request):
            page = backend.list_tickets()

        assert captured["maxResults"] == PAGE_LIMIT
        assert len(page) == PAGE_LIMIT
        assert page.truncated is False

    def test_jira_search_reports_truncation_from_the_next_page(self):
        """A full page plus a continuation token that actually yields a row
        is the only thing that proves there is more."""
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append(kwargs.get("params", {}))
            if len(calls) == 1:
                return {
                    "issues": [{"key": f"P-{i}"} for i in range(PAGE_LIMIT)],
                    "isLast": False,
                    "nextPageToken": "tok",
                }
            return {"issues": [{"key": "P-overflow"}], "isLast": True}

        with patch.object(backend, "_request", side_effect=fake_request):
            page = backend.list_tickets()

        assert len(page) == PAGE_LIMIT
        assert page.truncated is True
        assert calls[1]["nextPageToken"] == "tok"

    def test_jira_full_page_with_an_empty_next_page_is_complete(self):
        """Measured on the real API: a page holding every remaining issue
        still comes back `isLast: false` with a token, and following that
        token yields nothing. Trusting `isLast` alone would report every
        exactly-full board as truncated."""
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append(kwargs.get("params", {}))
            if len(calls) == 1:
                return {
                    "issues": [{"key": f"P-{i}"} for i in range(PAGE_LIMIT)],
                    "isLast": False,
                    "nextPageToken": "tok",
                }
            return {"issues": [], "isLast": True}

        with patch.object(backend, "_request", side_effect=fake_request):
            page = backend.list_tickets()

        assert page.truncated is False

    def test_jira_search_uses_the_endpoint_that_still_exists(self):
        """`GET /rest/api/3/search` was removed by Atlassian (CHANGE-2046)
        and answers 410 for everyone. Every list and query in rite went
        through it, and passed against mocks the whole time."""
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))
        paths = []

        def fake_request(method, path, **kwargs):
            paths.append(path)
            return {"issues": [{"key": "P-1"}], "isLast": True}

        with patch.object(backend, "_request", side_effect=fake_request):
            backend.list_tickets()
            backend.query("project = P")

        assert paths == ["/search/jql", "/search/jql"]

    def test_empty_result_under_bad_credentials_is_an_error_not_an_empty_board(self):
        """The real API answers a search with `200` and zero issues when
        the token is bad, because JQL matches nothing the caller can see.
        Reporting that as an empty board is a silent wrong answer."""
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))

        def fake_request(method, path, **kwargs):
            if path == "/myself":
                return BackendError("JIRA GET /myself → 401")
            return {"issues": [], "isLast": True}

        with patch.object(backend, "_request", side_effect=fake_request):
            result = backend.list_tickets()

        assert isinstance(result, BackendError)
        assert "rejected the credentials" in result.message

    def test_empty_board_with_good_credentials_stays_empty(self):
        """The other half: a genuinely empty board must not be reported as
        a credential failure."""
        backend = JiraBackend(JiraConfig(site="x.atlassian.net", email="", token=""))

        def fake_request(method, path, **kwargs):
            if path == "/myself":
                return {"accountId": "abc"}
            return {"issues": [], "isLast": True}

        with patch.object(backend, "_request", side_effect=fake_request):
            result = backend.list_tickets()

        assert not isinstance(result, BackendError)
        assert len(result) == 0


class TestGitHubStatusVocabularyIsShared:
    """`rite board list --status "In Progress"` — the example printed in
    that command's own `--help` — passed the status straight to
    `gh --state`, which accepts only open/closed/all. The user got a dump
    of gh's usage text, while `rite board move` accepted the same word."""

    def test_list_accepts_a_status_move_accepts(self):
        backend = GitHubBackend("owner/repo")
        captured = {}

        def fake_gh(args):
            captured["args"] = args
            return "[]"

        from rite_ai.tickets import TicketFilter

        with patch.object(backend, "_gh", side_effect=fake_gh):
            result = backend.list_tickets(TicketFilter(status="In Progress"))

        assert not isinstance(result, BackendError), result
        args = captured["args"]
        assert "--state" in args
        assert args[args.index("--state") + 1] == "open", (
            "'In Progress' was not mapped onto a state gh understands"
        )

    def test_unknown_status_is_refused_by_rite_not_by_gh(self):
        backend = GitHubBackend("owner/repo")
        from rite_ai.tickets import TicketFilter

        with patch.object(backend, "_gh", side_effect=AssertionError("gh was called")):
            result = backend.list_tickets(TicketFilter(status="Bogus"))

        assert isinstance(result, BackendError)
        assert "Bogus" in result.message
        assert "open" in result.message  # says what IS recognised


class TestLinkDirectionIsOnlyClaimedForBlocks:
    """The confirmation line said "A is blocked by B" whatever `--type`
    was given — stating a relationship that had not been created."""

    def test_non_blocks_type_does_not_claim_blocking(self):
        backend = MagicMock()
        backend.link.return_value = None
        with patch("rite_ai.cli.main._ticket_backend", return_value=(backend, None)):
            result = CliRunner().invoke(
                cli, ["board", "link", "RW-1", "RW-2", "--type", "Relates"]
            )

        assert "is blocked by" not in result.output, (
            f"claimed a blocking relationship for a Relates link: {result.output!r}"
        )
        assert "Relates" in result.output

    def test_blocks_type_still_reads_in_the_right_direction(self):
        backend = MagicMock()
        backend.link.return_value = None
        with patch("rite_ai.cli.main._ticket_backend", return_value=(backend, None)):
            result = CliRunner().invoke(cli, ["board", "link", "RW-1", "RW-2"])

        assert "RW-1 is blocked by RW-2" in result.output


# --- JIRA site: the pasted URL ----------------------------------------------


class TestJiraSiteAcceptsWhatUsersActuallyHave:
    """`site` was interpolated as `https://{site}`. Pasting the URL from
    the browser bar — the value a person actually has to hand — produced
    `https://https://team.atlassian.net/rest/api/3` and a DNS error naming
    neither the URL nor the setting that caused it."""

    @pytest.mark.parametrize(
        "given",
        [
            "team.atlassian.net",
            "https://team.atlassian.net",
            "http://team.atlassian.net",
            "https://team.atlassian.net/",
            "https://team.atlassian.net/jira/software/projects",
            "  https://team.atlassian.net  ",
        ],
    )
    def test_every_form_resolves_to_one_base_url(self, given: str):
        backend = JiraBackend(JiraConfig(site=given, email="a@b.c", token="t"))
        assert backend._base == "https://team.atlassian.net/rest/api/3", (
            f"{given!r} built {backend._base!r}"
        )

    def test_normalise_site_is_idempotent(self):
        once = normalise_site("https://team.atlassian.net/")
        assert normalise_site(once) == once

    def test_connection_failure_names_the_url_and_the_setting(self):
        import httpx

        backend = JiraBackend(JiraConfig(site="nope.invalid", email="", token=""))
        with patch.object(
            backend._client, "request", side_effect=httpx.ConnectError("boom")
        ):
            result = backend._request("GET", "/search")

        assert isinstance(result, BackendError)
        assert "https://nope.invalid/rest/api/3/search" in result.message
        assert "ticket_backend.site" in result.message

    def test_rejected_credentials_say_so_instead_of_dumping_html(self):
        backend = JiraBackend(JiraConfig(site="team.atlassian.net", email="", token=""))
        response = MagicMock(status_code=401, text="<html><title>Unauthorized</title>")
        with patch.object(backend._client, "request", return_value=response):
            result = backend._request("GET", "/search")

        assert isinstance(result, BackendError)
        assert "<html" not in result.message
        assert "credential" in result.message.lower()


# --- credential check: a check that could not be checked --------------------


class TestCredentialCheckExitCode:
    """`rite credential check jira_token` printed `jira_token: not_found`
    and exited 0, so `rite credential check X && run_it` walked straight
    into the failure it was written to prevent."""

    def test_missing_credential_exits_non_zero(self):
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value = MagicMock(
                name="jira_token", found=False, describe=lambda: "not found"
            )
            info.return_value.name = "jira_token"
            result = CliRunner().invoke(cli, ["credential", "check", "jira_token"])

        assert result.exit_code == 1, (
            "a missing credential reported success through the exit code"
        )

    def test_present_credential_exits_zero(self):
        # `source` rather than `found`: `credential check` resolves through
        # `store.resolve`, which asks `info()` WHERE a credential is (§10.2's
        # project / machine-global tiers) rather than just whether it exists.
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value = MagicMock(source="keychain", describe=lambda: "env")
            info.return_value.name = "jira_token"
            result = CliRunner().invoke(cli, ["credential", "check", "jira_token"])

        assert result.exit_code == 0

    def test_missing_credential_says_what_to_do_next(self):
        with patch("rite_ai.credentials.store.info") as info:
            info.return_value = MagicMock(found=False, describe=lambda: "not found")
            info.return_value.name = "jira_token"
            result = CliRunner().invoke(cli, ["credential", "check", "jira_token"])

        assert "rite credential set jira_token" in result.output
        assert "RITE_JIRA_TOKEN" in result.output

    def test_output_is_prose_not_an_internal_enum(self):
        from rite_ai.credentials.store import CredentialInfo

        assert CredentialInfo("jira_token", "not_found").describe() == "not found"


# --- help text: the mangled Examples blocks ---------------------------------


class TestHelpExamplesStayOnTheirOwnLines:
    """Click reflows a docstring paragraph, so every `Examples:` block in
    the CLI rendered as one run-on line — `Examples:   rite board create
    "Fix the bug"   rite board create "New feature" --role board` — which
    is not a command anyone can copy."""

    @pytest.mark.parametrize(
        "command",
        [
            ["board", "create", "--help"],
            ["board", "list", "--help"],
            ["pool", "fill", "--help"],
            ["credential", "rotate", "--help"],
            ["scheduler", "install", "--help"],
            ["sandbox", "start", "--help"],
            ["init", "--help"],
        ],
    )
    def test_each_example_is_its_own_line(self, command: list[str]):
        output = CliRunner().invoke(cli, command).output
        assert "Examples:" in output, f"no examples block in {command}"

        example_lines = [
            line.strip()
            for line in output.split("Examples:", 1)[1].split("\n\n")[0].splitlines()
            if line.strip()
        ]
        assert example_lines, f"empty examples block in {command}"
        for line in example_lines:
            assert len(_command_starts(line)) <= 1, (
                f"{command}: two examples collapsed onto one line: {line!r}"
            )

    def test_every_command_with_examples_renders_them_unwrapped(self):
        """The whole CLI, not a sample of it — the formatting is applied
        once at the group level precisely so a new command cannot miss it."""
        offenders = []
        runner = CliRunner()

        def walk(command, path):
            # The RENDERED help, not the docstring: the docstring always had
            # its examples on separate lines — click's wrapping is what put
            # them back together.
            if "Examples:" in (command.help or ""):
                output = runner.invoke(cli, path[1:] + ["--help"]).output
                block = output.split("Examples:", 1)[1].split("\n\n")[0]
                for line in block.splitlines():
                    if len(_command_starts(line)) > 1:
                        offenders.append(" ".join(path))
            for name, sub in getattr(command, "commands", {}).items():
                walk(sub, path + [name])

        walk(cli, ["rite"])
        assert offenders == [], f"examples collapsed in: {sorted(set(offenders))}"

    def test_prose_paragraphs_are_still_wrapped(self):
        """The fix must not turn every docstring into pre-formatted text."""
        text = (
            "A long prose paragraph that click should still reflow.\n\n"
            "Examples:\n  rite thing\n  rite other"
        )
        marked = preserve_indented_blocks(text)
        assert marked.startswith("A long prose paragraph")
        assert "\b\nExamples:" in marked


class TestHelpTextIsWrittenForUsers:
    """Round 1 removed two `--help` strings that narrated the codebase's
    own history; the fix for one of them introduced another. This guards
    the whole surface rather than the two known instances."""

    @pytest.mark.parametrize(
        "phrase",
        ["this codebase", "never wired", "thin wrapper", "until now"],
    )
    def test_no_internal_commentary_anywhere_in_help(self, phrase: str):
        found = []

        def walk(command, path):
            if phrase.lower() in (command.help or "").lower():
                found.append(" ".join(path))
            for name, sub in getattr(command, "commands", {}).items():
                walk(sub, path + [name])

        walk(cli, ["rite"])
        assert found == [], f"{phrase!r} appears in help for: {found}"


# --- the read-only probe that wrote, and the refusal that reported success ---


class TestPoolStatusDoesNotCreateAProject:
    """`rite pool status` is documented as a read-only, zero-token probe,
    but persisted state unconditionally — so running it in any directory
    created a `.rite/` holding only `pool.json`. `rite init` then refused
    that directory as "already initialised"."""

    def test_probe_on_a_bare_directory_writes_nothing(self, tmp_path: Path):
        from rite_ai.config.models import PoolConfig
        from rite_ai.pool import probe

        probe(tmp_path, PoolConfig(coordinator_standby=2))

        assert not (tmp_path / ".rite").exists(), (
            "a read-only probe created .rite/ in a directory that is not a rite project"
        )

    def test_probe_still_records_liveness_for_a_real_pool(self, tmp_path: Path):
        from rite_ai.config.models import PoolConfig
        from rite_ai.pool import probe

        state = tmp_path / ".rite" / "pool.json"
        state.parent.mkdir(parents=True)
        state.write_text(
            json.dumps(
                {"slots": [{"name": "s0", "created_at": 1.0, "last_live_at": None}]}
            )
        )
        with patch("rite_ai.pool.is_tmux_session_alive", return_value=True):
            probe(tmp_path, PoolConfig(coordinator_standby=1), now=1234.0)

        assert json.loads(state.read_text())["slots"][0]["last_live_at"] == 1234.0


class TestInitRefusalIsNotSuccess:
    """`rite init` on an existing `.rite/` printed "left untouched" and
    exited 0. Under `--yes`, which never prompts, that is the only signal
    a script gets — and it said everything was fine."""

    def test_refusing_to_initialise_exits_non_zero(self, tmp_path: Path):
        (tmp_path / ".rite").mkdir()

        result = CliRunner().invoke(cli, ["init", str(tmp_path), "--yes"])

        assert result.exit_code == 1, "init created nothing and still reported success"
        assert "already exists" in result.output


class TestBoardBackendErrorSaysWhatToDo:
    """`no ticket backend configured (.rite/config.yaml: ticket_backend.type)`
    named a file that, outside a rite project, does not exist — and never
    said which values the key accepts."""

    def test_outside_a_project_it_says_to_run_init(self, tmp_path: Path):
        from rite_ai.cli.main import _ticket_backend

        with patch("rite_ai.cli.main._find_project_root", return_value=tmp_path):
            backend, err = _ticket_backend()

        assert backend is None
        assert "rite init" in err, err

    def test_inside_a_project_it_names_the_accepted_values(self, tmp_path: Path):
        from rite_ai.cli.main import _ticket_backend

        root = _project(tmp_path)
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            backend, err = _ticket_backend()

        assert backend is None
        assert "jira" in err and "github" in err, err
        assert str(root / ".rite" / "config.yaml") in err
