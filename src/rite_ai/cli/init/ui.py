"""Terminal UX primitives for the init questionnaire.

Conventions borrowed deliberately (SPEC.md §9.2) from npm init, create-next-app,
gh repo create: defaults shown in brackets, one section visible at a time,
`[n/total]` progress rather than a bar, arrow-key single-select where the
terminal supports it.

Every prompt here degrades to a plain, linearly-readable fallback when stdin
or stdout is not a real TTY (piped input, `click.testing.CliRunner`, CI). The
fallback is not a lesser feature — it is what makes the questionnaire
testable at all (SPEC.md §9.2: "A questionnaire that cannot be automated
cannot be tested").
"""

from __future__ import annotations

import sys
import time

import click

try:
    import fcntl
    import struct
    import termios
except ImportError:  # not a POSIX terminal
    termios = None

ARROW_UP = ("\x1b[A", "k")
ARROW_DOWN = ("\x1b[B", "j")
ENTER = ("\r", "\n")
CTRL_C = "\x03"


def section(title: str, index: int, total: int) -> None:
    click.echo()
    dashes = "─" * max(3, 44 - len(title))
    counter = f"[{index}/{total}]"
    click.echo(f"─── {title} {dashes} {counter}")


def _supports_arrow_ui() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def select(question: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """Single-select prompt. `options` is a list of (value, label).

    Returns the chosen value. Uses an arrow-key menu on a real terminal,
    falling back to a numbered list read via `click.prompt` otherwise (which
    is what makes it exercisable through CliRunner's `input=`).
    """
    if not options:
        raise ValueError("select() requires at least one option")
    default = max(0, min(default, len(options) - 1))

    if _supports_arrow_ui():
        try:
            return _select_arrow(question, options, default)
        except Exception:
            pass
    return _select_fallback(question, options, default)


def _render_options(options: list[tuple[str, str]], current: int) -> None:
    for i, (_, label) in enumerate(options):
        marker = "▸" if i == current else " "
        click.echo(f"  {marker} {label}")


def _select_arrow(question: str, options: list[tuple[str, str]], default: int) -> str:
    click.echo(question)
    click.echo()
    current = default
    _render_options(options, current)
    while True:
        key = click.getchar()
        if key in ENTER:
            click.echo()
            return options[current][0]
        if key == CTRL_C:
            raise KeyboardInterrupt
        moved = False
        if key in ARROW_UP:
            current = (current - 1) % len(options)
            moved = True
        elif key in ARROW_DOWN:
            current = (current + 1) % len(options)
            moved = True
        if moved:
            # Move cursor up len(options) lines and redraw in place.
            click.echo(f"\x1b[{len(options)}A", nl=False)
            _render_options(options, current)


def _select_fallback(
    question: str, options: list[tuple[str, str]], default: int
) -> str:
    click.echo(question)
    for i, (value, label) in enumerate(options):
        marker = "▸" if i == default else " "
        click.echo(f"  {marker} {i + 1}. {label}")
    default_value = options[default][0]
    while True:
        raw = click.prompt("Choose", default=str(default + 1), show_default=True)
        raw = raw.strip()
        if not raw:
            return default_value
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for value, label in options:
            if raw.lower() in (value.lower(), label.lower()):
                return value
        click.echo(
            f"Please enter a number 1-{len(options)}, or press Enter for the default."
        )


def text(question: str, default: str = "", required: bool = False) -> str:
    """Free-text prompt. Empty input returns `default` unless `required`."""
    shown_default = default if default else "[]"
    prompt_text = f"{question} [{shown_default}]" if default else f"{question} []"
    while True:
        raw = click.prompt(prompt_text, default="", show_default=False)
        raw = raw.strip()
        if not raw:
            if required and not default:
                click.echo("This field is required.")
                continue
            return default
        return raw


def text_list(question: str, default: list[str] | None = None) -> list[str]:
    """Comma-separated free-text list prompt."""
    default = default or []
    raw = text(question, default=", ".join(default))
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def confirm(question: str, default: bool = True, suffix: str = ": ") -> bool:
    return click.confirm(question, default=default, prompt_suffix=suffix)


def line(prompt_text: str, default: str = "") -> str:
    """One line, with the prompt shown exactly as written — no bracket or
    colon added — for copy that was written word for word."""
    raw = click.prompt(prompt_text, default="", show_default=False, prompt_suffix=" ")
    return raw.strip() or default


# How long a paste is given to arrive after its first line is read.
PASTE_SETTLE_SECONDS = 0.05


def _terminal_fd() -> int | None:
    if termios is None:
        return None
    try:
        if sys.stdin.isatty():
            return sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        pass
    return None


def _queued_bytes(fd: int) -> int:
    try:
        raw = fcntl.ioctl(fd, termios.FIONREAD, b"\0\0\0\0")
    except OSError:
        return 0
    return struct.unpack("i", raw)[0]


def paragraph(prompt_text: str = ">") -> str:
    """Free text that may run to several lines.

    A pasted block is the expected input here, so each complete line still
    queued after the first is read and joined rather than left to answer
    nothing — or to reach the shell once init exits. A pasted last line with
    no newline is invisible to the terminal until it is submitted (measured
    on macOS), so it is not recorded; the number of lines taken is shown, so
    a short one gets noticed."""
    first = click.prompt(prompt_text, default="", show_default=False, prompt_suffix=" ")
    lines = [first]
    fd = _terminal_fd()
    if fd is not None:
        time.sleep(PASTE_SETTLE_SECONDS)
        while _queued_bytes(fd) > 0:
            lines.append(sys.stdin.readline().rstrip("\n"))
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except termios.error:
            pass
        if len(lines) > 1:
            note(f"recorded {len(lines)} lines")
    return "\n".join(piece.rstrip() for piece in lines).strip()


def repeat_until_blank(question: str, default: str = "") -> list[str]:
    """Repeat a text prompt until the user gives an empty answer."""
    items: list[str] = []
    while True:
        value = text(question, default=default)
        if not value:
            break
        items.append(value)
    return items


def note(message: str) -> None:
    click.secho(f"  {message}", dim=True)


def plain(message: str) -> None:
    """A line printed as written — no indent added, no styling.

    `note` adds its own two spaces, which wrecks a column-aligned block.
    """
    click.secho(message, dim=True)


def warn(message: str) -> None:
    """A line that must not be skimmed past.

    `note` is dim, which is right for "here is something to know" and
    wrong for "a file of yours was moved". Colour only ever decorates —
    the words carry the meaning, so this reads the same piped to a file
    (see `rite_ai.label` for the same reasoning about the project dot)."""
    click.secho(f"  ! {message}", fg="yellow", bold=True)


def created(path: str) -> None:
    click.echo(f"✓ Created {path}")


def generated(path: str) -> None:
    click.echo(f"✓ Generated {path}")
