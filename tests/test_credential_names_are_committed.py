"""Credential NAMES must survive a fresh clone (SPEC §10.2, constraint 1/4).

An auto-generated scope is only useful if it is recoverable. A random name in
a gitignored file is a credential nobody can find again: the secret sits in the
keychain under a name the next clone never derives, so it is unreachable
without being re-typed — and unreachable with no error saying so, because a
missing credential looks exactly like one that was never set.

The property is worth keeping deliberately for the other half too: because the
file holds names and never values, a person cloning the project learns which
credentials to set without anyone sharing a secret — the `.env.example`
pattern.

Two things therefore have to hold, and both have been wrong in this repo:

  1. `config_to_yaml` writes the `credentials` block and `parse_config` reads
     it back (the round-trip gate in `test_config_roundtrip_is_total` covers
     the general rule; this pins the specific section).
  2. `.rite/config.yaml` is not gitignored. It WAS — `.rite/*` swallowed it,
     with only `gitleaksignore` and `review-checklist.md` negated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rite_ai.cli.init.scaffold import config_to_yaml, gitignore_lines
from rite_ai.config.models import CredentialsConfig, ProjectConfig
from rite_ai.config.parse import ParseError, parse_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_namespace_round_trips(tmp_path):
    config = ProjectConfig(credentials=CredentialsConfig(namespace="acme-1a2b3c"))
    path = tmp_path / "config.yaml"
    path.write_text(config_to_yaml(config))

    back = parse_config(path)
    assert not isinstance(back, ParseError)
    assert back.credentials.namespace == "acme-1a2b3c"


def test_serialised_config_contains_no_secret_value(tmp_path):
    """The committed file records names. If a value ever reaches it, the
    `.env.example` property becomes a leak instead of a feature."""
    config = ProjectConfig(credentials=CredentialsConfig(namespace="acme-1a2b3c"))
    text = config_to_yaml(config)
    assert "acme-1a2b3c" in text
    # The block carries a namespace only — there is nowhere for a value
    # to live, which is what makes committing the file safe.
    back = parse_config_from_text(tmp_path, text)
    assert set(vars(back.credentials)) == {"namespace"}


def parse_config_from_text(tmp_path, text):
    path = tmp_path / "c.yaml"
    path.write_text(text)
    parsed = parse_config(path)
    assert not isinstance(parsed, ParseError)
    return parsed


def test_generated_gitignore_reincludes_config_yaml():
    """A project rite manages: `scaffold` must negate the file it just
    wrote the scope into."""
    lines = gitignore_lines(kb_commit=False)
    assert ".rite/*" in lines
    assert "!.rite/config.yaml" in lines


def test_this_repos_own_gitignore_does_not_swallow_config_yaml():
    """rite's OWN checkout, asked of git rather than of the file's text —
    the rule that was wrong here while `scaffold` had it right, which is
    exactly the split that let it go unnoticed."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", ".rite/config.yaml"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    # git check-ignore -q: 0 = ignored, 1 = NOT ignored.
    assert proc.returncode == 1, (
        ".rite/config.yaml is gitignored in rite's own checkout — the "
        "credential scope recorded there would not survive a fresh clone"
    )
