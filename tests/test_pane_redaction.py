"""`rite sandbox pane` exists to be read by Claude sessions. yoloAI's launch
line on Seatbelt carries every injected secret as `export NAME='value'`, so an
unredacted pane routes live credentials into model context and transcripts."""

from unittest.mock import MagicMock, patch

from rite_ai.sandbox import redact_secrets, worker_pane

TOKEN = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
JIRA = "ATATT3xFfGF0-known-jira-token=7C8C1E8E"


def _launch_screen(width: int = 60) -> str:
    """A launch line wrapped the way a terminal snapshot wraps it."""
    line = (
        "% source ~/.swift-wrapper.sh && export ANTHROPIC_BASE_URL='http://127.0.0.1:5"
        f"6723'; export GITHUB_TOKEN='{TOKEN}'; export GIT_CONFIG_VALUE_2='false'; "
        f"export JIRA_API_TOKEN='{JIRA}'; export ODD='it'\\''s'; cd '/w' && exec "
        "agent-run.sh claude --dangerously-skip-permissions"
    )
    wrapped = "\n".join(line[i : i + width] for i in range(0, len(line), width))
    return wrapped + "\n\n  Claude Code\n> Work ticket DEF-9.\n"


def _joined(text: str) -> str:
    return text.replace("\n", "")


def test_a_wrapped_launch_line_prints_no_injected_value():
    screen = _launch_screen()
    assert TOKEN not in screen  # wrapped: the value is split across lines
    assert TOKEN in _joined(screen)
    out = redact_secrets(screen)
    assert TOKEN not in _joined(out)
    assert JIRA not in _joined(out)
    assert "export GITHUB_TOKEN='[redacted]'" in _joined(out)
    assert "Work ticket DEF-9." in out
    assert "exec agent-run.sh claude" in _joined(out)


def test_a_known_secret_is_redacted_wherever_it_appears():
    screen = f"$ env | grep TOKEN\nGITHUB_TOKEN={TOKEN[:20]}\n{TOKEN[20:]}\n"
    out = redact_secrets(screen, [TOKEN])
    assert TOKEN not in _joined(out)
    assert "[redacted]" in out


def test_short_values_are_not_searched_for():
    assert redact_secrets("commit.gpgsign false", ["false"]) == "commit.gpgsign false"


@patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
def test_worker_pane_never_returns_an_injected_value(_which):
    with patch("rite_ai.sandbox.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=_launch_screen(), stderr="")
        capture = worker_pane("alpha", secrets=[TOKEN, JIRA])
    assert capture.ok
    assert TOKEN not in _joined(capture.text)
    assert JIRA not in _joined(capture.text)
