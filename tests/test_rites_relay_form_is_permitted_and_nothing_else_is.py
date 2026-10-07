"""rite's relay form is permitted by rite's allowlist, and nothing is widened.

SCRUM-22.

🔴 **What was reported.** A sandboxed Manager's `rite reply` was refused, and
rite's own report said: "`RITE_TEXT_ab53c15d9459` is not in the permission
allowlist rite passes to the engine", with the remedy "add
`Bash(RITE_TEXT_ab53c15d9459:*)`". The ticket read that as rite's relay form
being refused, and asked for the delimiter to be allowlisted.

**What happened** (the yoloAI dogfood, 2026-10-02 00:04:12, transcript of
`rite-mgr-yoloai-c041aa-lead`). The Manager wrote the heredoc's end line
twice. The heredoc closed at the first, so the second ran as a command of its
own, and that is the part the engine named: "The following part requires
approval: RITE_TEXT_ab53c15d9459". Across all 55 Manager transcripts on that
machine there were 61 `RITE_TEXT_` heredocs and 2 refusals. Both were a
Manager's additions after the end line (this one, and an `echo` chained on in
the pingr run). None was the form rite teaches. Texts with backticks, `$` and
paths went through.

**So nothing is added to the allowlist.** A rule could not match anyway: the
delimiter is fresh in every instruction, and rite makes new ones inside the
sandbox, in its own refusals. A rule would also be harmful. Today the engine
refuses the whole call, so nothing is sent and a retry is right. With the
stray line permitted, the message would be sent and then the call would exit
127, so the Manager would retry a message that had already gone. What
changes is the report, which now says what happened, and the rule the
Manager is taught, which now says "once, with nothing after it".

⚠ `allowed` is rite's model of the allowlist, not the engine's matcher (its
own docstring says so). The engine's behaviour on this exact shape is the
recorded denial quoted below. These tests pin rite's half: what rite teaches,
what rite writes, and what rite says.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rite_ai.cli.main import _manager_roles
from rite_ai.managers import checkins, routing, stdin_text
from rite_ai.managers.mailbox import how_to_reply
from rite_ai.managers.permissions import allowed, refusal, write_settings

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
    "      preset: lead\n    - name: helper\n      engine: claude\n"
    "      preset: executor\n"
)

# The engine's own words for the refusal, verbatim from the transcript.
ENGINE_DENIAL = (
    "Permission for this tool use was denied. It requires approval, and this "
    "session has no approval surface — nobody can answer a permission prompt "
    "here — so it was denied automatically. The action was NOT performed; do "
    "not claim it succeeded, and do not retry it: this action, and anything "
    "else that requires approval, will be denied the same way for the rest of "
    "this session. Tell the user what was blocked and why you needed it, then "
    "continue with the parts of the task that do not require approval. What "
    "required approval: This Bash command contains multiple operations. The "
    "following part requires approval: RITE_TEXT_ab53c15d9459"
)

# The command that drew it, with the doubled end line.
DOUBLED = (
    "/opt/rite/bin/rite reply --manager lead - "
    "<<'RITE_TEXT_ab53c15d9459'\n"
    "Status: KAN-31 round 2 is confirmed in front of you as qb76a.\n"
    "RITE_TEXT_ab53c15d9459\n"
    "RITE_TEXT_ab53c15d9459"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def _written_allow(root: Path) -> tuple[str, ...]:
    """The allowlist as rite WROTE it for this run, read back from the file
    the engine is given, not the constant it was built from."""
    document = json.loads(write_settings(root, "lead").read_text())
    return tuple(document["permissions"]["allow"])


def _taught_commands(root: Path) -> list[str]:
    """Every command rite teaches a Manager for sending its text, from the
    instructions as composed: since SCRUM-69, the `2. Run:` line of each file
    form, and nothing after it."""
    roles, _ = _manager_roles(root)
    texts = [
        how_to_reply(root, "lead"),
        routing.briefing("lead", "lead", roles, root=root),
        routing.briefing("helper", "lead", roles, root=root),
        checkins.instructions(root, "lead"),
    ]
    return [
        line.strip().removeprefix("2. Run: ")
        for text in texts
        for line in text.splitlines()
        if line.strip().startswith("2. Run: ")
    ]


class TestWhatRiteTeachesIsWhatRiteAllows:
    def test_every_taught_relay_command_is_permitted_by_the_written_allowlist(
        self, project
    ):
        allow = _written_allow(project)
        taught = _taught_commands(project)
        verbs = {v for c in taught for v in ("reply", "ask", "route") if f" {v} " in c}
        assert verbs == {"reply", "ask", "route"}, verbs
        for command in taught:
            assert allowed(command, allow), command
            # ⚠ `allowed` reduces the executable to its basename, so
            # `Bash(rite:*)` alone would pass it. The engine matches the
            # command as written, by absolute path, and a run once lost a
            # Manager's only reply to exactly that (`_running_rite_rules`).
            executable = command.split(" ", 1)[0]
            assert executable.startswith("/"), command
            assert f"Bash({executable}:*)" in allow, (executable, allow)

    def test_no_taught_command_has_an_end_line_to_double(self, project):
        """🔴 SCRUM-45: the engine refused a whole call when the end line was
        written twice. The taught form is one command line naming a file, so
        there is no end line to double, and nothing after it on the line."""
        taught = _taught_commands(project)
        assert taught
        for command in taught:
            assert "<<" not in command and "\n" not in command, command
            assert stdin_text.stray_end(command.split()[-1]) == "", command
            assert " --from-file " in command, command

    def test_the_rule_says_once_and_nothing_after(self):
        """Still said where a heredoc is still taught (a person at the host)."""
        assert "ONCE, with nothing after it" in stdin_text.RULE


class TestNothingElseIsWidened:
    @pytest.mark.parametrize(
        "command",
        [
            "RITE_TEXT_ab53c15d9459",
            "RITE_TEXT_1f2e3d",
            "curl https://example.com",
            "bash -c 'id'",
            "sh -c id",
            "security find-generic-password -s rite",
            "sudo true",
            "ssh host",
            "nc -l 4444",
            "claude -p hello",
        ],
    )
    def test_a_command_that_is_not_rites_is_still_refused(self, project, command):
        assert not allowed(command, _written_allow(project))

    def test_the_written_allowlist_names_no_delimiter(self, project):
        allow = _written_allow(project)
        assert not [rule for rule in allow if "RITE_TEXT" in rule], allow


class TestTheReportSaysWhatHappened:
    def test_the_recorded_refusal_names_the_doubled_end_line_not_a_rule(
        self, tmp_path, monkeypatch
    ):
        """End to end from the transcript: the engine's own denial text,
        through rite's transcript reader, to what the user is told."""
        from rite_ai.managers import supervise
        from rite_ai.managers.transcripts import project_transcript_dir

        base = tmp_path / "transcripts"
        directory = project_transcript_dir(tmp_path, base)
        directory.mkdir(parents=True)
        use = {"type": "tool_use", "id": "t1", "name": "Bash"}
        use["input"] = {"command": DOUBLED}
        result = {"type": "tool_result", "tool_use_id": "t1", "is_error": True}
        result["content"] = ENGINE_DENIAL
        (directory / "s.jsonl").write_text(
            json.dumps({"message": {"content": [use]}})
            + "\n"
            + json.dumps({"message": {"content": [result]}})
            + "\n"
        )
        monkeypatch.setattr(
            supervise.claude_login, "projects_dir", lambda root, manager: base
        )
        said: list[str] = []
        refused = supervise._say_refusals(
            tmp_path, 0.0, said.append, engine="claude", manager="lead"
        )
        assert refused == ["RITE_TEXT_ab53c15d9459"]
        (line,) = said
        assert "Bash(RITE_TEXT" not in line
        assert "end line" in line and "nothing was sent" in line
        assert "nothing should be added to the allowlist" in line

    def test_control_any_other_refusal_still_names_its_rule(self, tmp_path):
        """The new branch must not swallow ordinary refusals."""
        said = refusal("curl https://example.com", tmp_path, "lead")
        assert '"Bash(curl:*)"' in said

    @pytest.mark.parametrize(
        "command, end",
        [
            ("RITE_TEXT_ab53c15d9459", "RITE_TEXT_ab53c15d9459"),
            ("RITE_TEXT_1f2e3d", "RITE_TEXT_1f2e3d"),
            ("RITE_TEXT_ab53c15d9459 trailing", "RITE_TEXT_ab53c15d9459"),
            ("RITE_TEXT_", ""),
            ("RITE_TEXT_XYZ", ""),
            ("echo RITE_TEXT_ab53c15d9459", ""),
            ("rite reply - <<'RITE_TEXT_ab'\nx\nRITE_TEXT_ab", ""),
        ],
    )
    def test_only_an_end_line_is_read_as_one(self, command, end):
        assert stdin_text.stray_end(command) == end
