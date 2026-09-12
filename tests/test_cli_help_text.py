"""`--help` is documentation, and nothing else in the suite reads it.

Two commands shipped with their own development history in the text a
user reads. `rite init --help` — the first command anyone runs —
explained that init "never exposed either flag until now — the same
'built, never wired to the CLI' class as several other gaps in this
codebase", and `rite credential rotate --help` pointed at
`rite_ai.credentials.store.store`, an import path no user can type.
Commit 8c25ea3 removed exactly this kind of prose from `context add` and
`prepare` by hand and missed both, because every existing CLI test
asserts on exit codes and command output — none of them on help text.

So these walk every registered command rather than a list someone has to
remember to extend: a command added tomorrow is covered the moment it is
registered.
"""

import re

import click
import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


def _leaf_commands(command, path=()):
    """Every runnable command, as the argv a user would type."""
    if isinstance(command, click.Group):
        for name, sub in sorted(command.commands.items()):
            yield from _leaf_commands(sub, path + (name,))
    else:
        yield path


ALL_COMMANDS = sorted(_leaf_commands(cli))
COMMAND_IDS = [" ".join(p) for p in ALL_COMMANDS]


# Phrases that describe this codebase's own development rather than the
# user's task. Deliberately narrow: each one is a statement about when or
# how the code was written, which is never something a user needs.
INTERNAL_COMMENTARY = (
    "never wired",
    "until now",
    "this codebase",
    "this session",
    "near-stub",
    "todo",
    "fixme",
    "xxx",
)

# Help text naming an internal Python module path — `rite_ai.foo.bar` —
# is offering the reader something they cannot act on; the user-facing
# name is the command, not the module that implements it.
INTERNAL_MODULE_PATH = re.compile(r"rite_ai\.[A-Za-z0-9_.]+")


def test_every_command_is_covered():
    """Guards the walk itself: if `_leaf_commands` silently stopped
    recursing into groups, every test below would vacuously pass."""
    assert len(ALL_COMMANDS) > 40
    assert ("init",) in ALL_COMMANDS
    assert ("credential", "rotate") in ALL_COMMANDS


@pytest.mark.parametrize("command", ALL_COMMANDS, ids=COMMAND_IDS)
def test_help_renders(command):
    result = CliRunner().invoke(cli, [*command, "--help"])
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "help text is empty"


@pytest.mark.parametrize("command", ALL_COMMANDS, ids=COMMAND_IDS)
def test_help_carries_no_internal_commentary(command):
    output = CliRunner().invoke(cli, [*command, "--help"]).output
    found = [p for p in INTERNAL_COMMENTARY if p in output.lower()]
    assert not found, (
        f"`rite {' '.join(command)} --help` reads like a changelog entry "
        f"rather than documentation — found {found}:\n\n{output}"
    )


@pytest.mark.parametrize("command", ALL_COMMANDS, ids=COMMAND_IDS)
def test_help_names_no_internal_module_path(command):
    output = CliRunner().invoke(cli, [*command, "--help"]).output
    found = INTERNAL_MODULE_PATH.findall(output)
    assert not found, (
        f"`rite {' '.join(command)} --help` points the reader at "
        f"{found} — an import path, not something a user can run:\n\n{output}"
    )
