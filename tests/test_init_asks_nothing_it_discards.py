"""`rite init` does not ask a question whose answer nothing reads.

"Anything else the team should know?" was the last prompt init asked. The
answer went into `brief.yaml` under `what.notes`, was parsed back into
`ProjectBrief.notes` — and read by nothing else. It never reached CLAUDE.md
and no session ever saw it. A question that discards its input teaches people
their answers do not matter, and it was the last thing a stranger met on the
way in.
"""

from pathlib import Path

import click
import yaml
from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.config.models import ProjectBrief


@click.command()
@click.argument("directory")
def _init_cmd(directory: str) -> None:
    result = run_init(Path(directory))
    click.echo(f"STATUS:{result.status}")


# role, name, root branch, module name (blank), kind, features, platform,
# languages, frameworks, architecture — then "3" — then surplus blanks.
_ENTER_THROUGH = "\n".join([""] * 10 + ["3"] + [""] * 12) + "\n"


def test_init_does_not_ask_what_the_team_should_know(tmp_path: Path):
    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=_ENTER_THROUGH)
    assert result.exit_code == 0, result.output
    assert "Anything else the team should know?" not in result.output


def test_the_brief_carries_no_field_nothing_reads(tmp_path: Path):
    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=_ENTER_THROUGH)
    assert result.exit_code == 0, result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert "notes" not in brief["what"]
    assert "notes" not in ProjectBrief.__dataclass_fields__


def test_a_brief_written_before_still_parses(tmp_path: Path):
    from rite_ai.config.parse import parse_brief

    path = tmp_path / "brief.yaml"
    path.write_text(
        "project:\n  name: old\n  role: owner\n"
        'what:\n  kind: library\n  notes: "written by an earlier init"\n'
    )
    brief = parse_brief(path)
    assert isinstance(brief, ProjectBrief), brief
    assert brief.kind == "library"
