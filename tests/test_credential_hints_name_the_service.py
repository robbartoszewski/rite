"""Every hint rite prints for a missing credential names the same command.

Measured in the v0.7.0 dogfood: one `rite doctor` report said `rite credential
set github_token` for the Workers' token, while `rite credential list` said
`rite credential set github` for the same gap. Both work, but a person told two
commands for one thing cannot tell whether they are the same thing. The
service is the form a person can act on without knowing rite's key names
(SPEC §10.5), so every hint uses `services.how_to_set`, and a key that is not
a service's single-line field stays a key.
"""

from __future__ import annotations

import re
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.config.models import Module
from rite_ai.credentials.services import SERVICES, how_to_set, service_key
from rite_ai.sandbox import CloneRemote, push_access_refusal, remote_access_refusal

SRC = Path(__file__).resolve().parents[1] / "src" / "rite_ai"

# Keys a prompt can set, so their hint must name the service.
PROMPTABLE = {
    service_key(s.name, f.name): s.name
    for s in SERVICES.values()
    for f in s.secrets
    if not f.multiline
}

# The one place a key-form example is deliberate: `rite credential set`'s own
# help, showing that a single field can still be replaced by its key.
ALLOWED = {"rite credential set jira_token              # just the one field"}


def test_a_promptable_key_is_set_through_its_service():
    assert PROMPTABLE["github_token"] == "github"
    assert PROMPTABLE["claude_token"] == "claude"
    for key, service in PROMPTABLE.items():
        assert how_to_set(key) == service


def test_a_multiline_key_and_a_worker_token_stay_keys():
    """`set github_app` refuses a private key and sends the person to
    `set github_app_key --stdin`, so suggesting the service would be wrong."""
    assert how_to_set("github_app_key") == "github_app_key"
    assert how_to_set("sandbox_token_alpha") == "sandbox_token_alpha"


def _joined(text: str) -> str:
    """Source with implicitly concatenated string literals joined, so a hint
    split over two lines (`"...set "` / `"github_token..."`) is still seen."""
    return re.sub(r"([\"'])\s*\n\s*[rRfFbBuU]*([\"'])", "", text)


def test_no_source_hint_names_a_promptable_key():
    """Structural, over every module: a new hint that hard-codes `set
    github_token` fails here, wherever it is added."""
    # `--stdin` reads one value, which only the key form takes (the service
    # form refuses a value), so a from-a-file hint names the key rightly.
    keys = "|".join(map(re.escape, PROMPTABLE))
    pattern = re.compile(rf"credential set ({keys})\b(?! --stdin)")
    found = []
    for path in sorted(SRC.rglob("*.py")):
        text = _joined(path.read_text())
        for m in pattern.finditer(text):
            line = text[text.rfind("\n", 0, m.start()) + 1 : text.find("\n", m.end())]
            if line.strip() not in ALLOWED:
                found.append(f"{path.relative_to(SRC)}: {line.strip()}")
    assert found == []


def test_the_scan_sees_a_hint_split_over_two_literals():
    """The control for the scan above: the shape the sandbox's hint had."""
    split = 'x = (\n    "Run `rite credential set "\n    "github_token` here"\n)\n'
    assert "credential set github_token" in _joined(split)


# --- the hints themselves, as a person reads them -------------------------------


def test_doctor_names_the_service_for_the_workers_token(
    tmp_path: Path, monkeypatch, capsys
):
    from rite_ai.cli.main import _doctor_worker_github_token

    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text(
        "credentials:\n  namespace: acme-1a2b3c\n"
    )
    monkeypatch.chdir(tmp_path)
    module = Module(
        name="app",
        path="app/",
        url="https://github.com/acme/app.git",
        branch="main",
        description="",
    )
    problems: list[str] = []
    _doctor_worker_github_token(tmp_path, [module], problems)

    out = capsys.readouterr().out
    assert problems, out  # the precondition: doctor did report the gap
    assert "`rite credential set github`" in out
    assert "github_token`" not in out


def _remotes() -> list[CloneRemote]:
    return [CloneRemote(Path("app"), "git@github.com:acme/app.git", ("acme", "app"))]


def test_the_no_token_refusal_names_the_service():
    refusal = remote_access_refusal("alpha", _remotes(), None, "/usr/bin/gh")
    assert "`rite credential set github`" in refusal


def test_the_cannot_push_refusal_names_the_service_and_the_workers_own():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("u", 403, "no", {}, None),
    ):
        refusal = push_access_refusal("alpha", _remotes(), "tok")
    assert "`rite credential set github`" in refusal
    assert "`rite credential set sandbox_token_alpha`" in refusal


def test_deliver_names_the_service(tmp_path: Path, monkeypatch):
    from rite_ai.config.models import ProjectConfig
    from rite_ai.publishing import deliver

    monkeypatch.setattr(
        "rite_ai.sandbox.resolve_worker_token", lambda worker, creds: (None, "")
    )
    module = Module(
        name="app",
        path="app/",
        url="https://github.com/acme/app.git",
        branch="main",
        description="",
    )
    why = deliver._remote_environment(tmp_path, "alpha", module, ProjectConfig())
    assert isinstance(why, str)
    assert "`rite credential set github`" in why


@pytest.mark.parametrize("key, service", [("jira_token", "jira")])
def test_the_board_s_missing_credential_names_the_service(key, service):
    from rite_ai.tickets import _missing_credential

    message = _missing_credential(key, None)
    assert f"rite credential set {service}\n" in message
    assert f"rite credential set {service} --global\n" in message
