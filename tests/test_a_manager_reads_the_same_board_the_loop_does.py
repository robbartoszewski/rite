"""`rite start <manager>` could never start a Manager, on any project.

⚠ **The verdict function was never given a board.** `_loop_verdict` called
`plan_cycle(root)` with no `board`, and `plan_cycle` never builds one —
`board` is injection-only. So at `loop/__init__.py`:

    if board is None:
        cycle.verdict = UNKNOWN
        cycle.detail = "no ticket backend is configured, so there is no
                        queue to read"

`unknown` is in `STOP_VERDICTS`, so every `rite start <manager>` returned
"stopped on 'unknown' after 0 session(s)" whatever the project was
configured with. The `rite loop` path one file away passes
`plan_cycle(root, board=board, sandbox_status=...)`; the Manager path
passed neither.

⚠ **Absent is not unreachable**, and this file pins that they stay
distinguishable. A project with no backend configured and a project whose
Jira is down both stop — that part is unchanged and deliberate, since
spending sessions on a project whose state could not be read is what
`unknown`-is-a-stop already means — but they must not say the same thing.
Conflating them is this codebase's signature defect.
"""

from __future__ import annotations

from click.testing import CliRunner

from rite_ai.cli.main import cli


def _project(tmp_path, backend: str):
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    (tmp_path / ".rite" / "config.yaml").write_text(
        "coordination:\n"
        "  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n" + backend
    )
    return tmp_path


class TestNoBoardStartsASetupSession:
    """Robert's decision: a project with nothing configured is not refused.
    The Manager starts, and its work that cycle is guiding the User through
    setting the missing pieces up."""

    def test_it_starts_rather_than_refusing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, ""))
        seen = {}
        import rite_ai.managers.supervise as sup_mod

        def fake_supervise(root, manager, **kw):
            seen.update(kw)
            return type("R", (), {"ok": True, "reason": "done", "cycles": []})()

        monkeypatch.setattr(sup_mod, "supervise", fake_supervise)
        result = CliRunner().invoke(
            cli, ["start", "lead", "--sessions", "3", "--minutes", "5"]
        )
        assert seen, f"the Manager was never started: {result.output}"
        assert "ticket_backend" in seen["prompt"], seen["prompt"][:200]
        assert "config.yaml" in seen["prompt"]

    def test_a_setup_session_does_not_resume(self, tmp_path, monkeypatch):
        """⚠ Only useful while somebody is there to be guided. Resuming it
        unattended spends the ceiling talking to an empty pane."""
        monkeypatch.chdir(_project(tmp_path, ""))
        seen = {}
        import rite_ai.managers.supervise as sup_mod

        def fake_supervise(root, manager, **kw):
            seen.update(kw)
            return type("R", (), {"ok": True, "reason": "done", "cycles": []})()

        monkeypatch.setattr(sup_mod, "supervise", fake_supervise)
        result = CliRunner().invoke(
            cli, ["start", "lead", "--sessions", "3", "--minutes", "5"]
        )
        assert seen["max_sessions"] == 1, (
            f"asked for 3 and got {seen['max_sessions']} — a setup session "
            f"must run once and stop"
        )
        assert "not resumed" in result.output or "not resumed" in str(
            result.stderr_bytes or b"", "utf-8"
        )

    def test_the_setup_instruction_is_not_the_ordinary_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_project(tmp_path, ""))
        seen = {}
        import rite_ai.managers.supervise as sup_mod

        monkeypatch.setattr(
            sup_mod,
            "supervise",
            lambda root, manager, **kw: (
                seen.update(kw),
                type("R", (), {"ok": True, "reason": "done", "cycles": []})(),
            )[1],
        )
        CliRunner().invoke(cli, ["start", "lead", "--sessions", "1", "--minutes", "5"])
        assert "no queue" in seen["prompt"].lower()


class TestAbsentAndUnreachableAreNotTheSame:
    def test_an_unreachable_backend_REFUSES_and_says_unreachable(
        self, tmp_path, monkeypatch
    ):
        """⚠ This project HAS a board. Telling this user to configure one
        would be telling them to fix something that is not broken."""
        import rite_ai.cli.main as main_mod

        monkeypatch.chdir(
            _project(
                tmp_path,
                "ticket_backend:\n  type: github\n  repo: someone/something\n",
            )
        )
        monkeypatch.setattr(
            main_mod,
            "_ticket_backend",
            lambda role="workers": (
                None,
                "github: could not connect to api.github.com",
            ),
        )
        result = CliRunner().invoke(
            cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
        )
        out = result.output + str(result.exception or "")
        assert "could not connect" in out, out
        assert "is starting to help you configure" not in out, (
            "an unreachable board was treated as an absent one"
        )
