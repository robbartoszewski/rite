"""The rehearsal's multi-line paste finding, replayed.

The friction audit carried it as "not verified — the finding is the
rehearsal's". Replaying it through a real pty showed two defects, and the
first is not overflow at all:

1. Pasting three lines at `Project name?` answered the NEXT question with
   line two, without that question ever being shown, and left line three
   queued for the one after. Input silently ACCEPTED as answers nobody saw,
   which is worse than input silently dropped — the project is then
   configured from it.
2. A paste arriving in chunks further apart than one 50ms settle window
   recorded only its first line, and the rest reached the SHELL after init
   exited — the leak `paragraph`'s drain exists to prevent.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from rite_ai.cli.init import ui

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or ui.termios is None,
    reason="POSIX terminal handling",
)


class TestAOneLineAnswerStopsAtOneLine:
    def test_the_rest_of_a_paste_is_discarded_and_reported(self, monkeypatch, capsys):
        queued = ["linux-box", "third-line"]
        monkeypatch.setattr(ui, "_terminal_fd", lambda: 0)
        monkeypatch.setattr(ui, "_queued_bytes", lambda fd: len(queued))
        monkeypatch.setattr(ui.sys.stdin, "readline", lambda: queued.pop(0) + "\n")
        monkeypatch.setattr(ui.termios, "tcflush", lambda fd, q: None)

        dropped = ui.discard_pasted_remainder("acme", '"Project name?"')

        assert dropped == 2
        out = capsys.readouterr().out
        assert "2 further pasted line(s) ignored" in out
        assert "'acme'" in out, "the answer that was kept has to be shown"

    def test_a_prompt_that_repeats_itself_still_takes_the_whole_list(self, monkeypatch):
        """`repeat_until_blank` asks the same question again, so line two IS
        an answer to the question line one answered."""
        calls: list[bool] = []
        answers = iter(["alpha", "beta", ""])
        monkeypatch.setattr(ui.click, "prompt", lambda *a, **k: next(answers))
        monkeypatch.setattr(
            ui,
            "discard_pasted_remainder",
            lambda kept, what="": calls.append(True) or 0,
        )

        items = ui.repeat_until_blank("Link?")

        assert items == ["alpha", "beta"]
        assert not calls, "a pasted list was cut off at its first item"


class TestAPasteThatArrivesInChunks:
    def test_the_drain_waits_through_a_gap(self, monkeypatch):
        """One 50ms look was a sample, not a wait."""
        # Quiet for two windows, then two more lines arrive.
        queue: list[str] = []
        script = iter([0, 0, 2, 0, 0, 0, 0, 0, 0, 0])
        monkeypatch.setattr(ui, "_terminal_fd", lambda: 0)

        def queued(fd):
            try:
                n = next(script)
            except StopIteration:
                n = 0
            if n and not queue:
                queue.extend(["line two", "line three"])
            return len(queue)

        monkeypatch.setattr(ui, "_queued_bytes", queued)
        monkeypatch.setattr(ui.sys.stdin, "readline", lambda: queue.pop(0) + "\n")
        monkeypatch.setattr(ui.termios, "tcflush", lambda fd, q: None)
        monkeypatch.setattr(ui.click, "prompt", lambda *a, **k: "line one")

        text = ui.paragraph("Describe:")

        assert text == "line one\nline two\nline three", text


def _replay(child: str, chunks: list[tuple[bytes, float]]) -> str:
    """Run `child` under a pty and type `chunks` into it."""
    import pty

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs away
        os.environ["PYTHONPATH"] = "src"
        os.execv(sys.executable, [sys.executable, "-c", child])
    time.sleep(0.6)
    for data, pause in chunks:
        os.write(fd, data)
        time.sleep(pause)
    os.set_blocking(fd, False)
    out = b""
    deadline = time.time() + 6
    while time.time() < deadline and b"<<END>>" not in out:
        try:
            out += os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.05)
        except OSError:
            break
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    return out.decode(errors="replace")


def test_replayed_through_a_real_terminal():
    """The audit carried this as not replayed. This is the replay."""
    child = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "from rite_ai.cli.init import ui\n"
        "a = ui.text('Project name?')\n"
        "print(f'KEPT={a!r}')\n"
        "print('<<END>>')\n"
    )

    out = _replay(child, [(b"acme\nlinux-box\nthird-line\n", 0.5)])

    assert "KEPT='acme'" in out, out
    assert "further pasted line(s) ignored" in out, out


def test_a_slow_paste_does_not_reach_the_shell():
    child = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "from rite_ai.cli.init import ui\n"
        "p = ui.paragraph('Describe:')\n"
        "print(f'GOT={p!r}')\n"
        "print('<<END>>')\n"
    )

    out = _replay(
        child,
        [(b"line one\n", 0.2), (b"line two\n", 0.2), (b"line three\n", 0.2)],
    )

    assert "GOT='line one\\nline two\\nline three'" in out, out
