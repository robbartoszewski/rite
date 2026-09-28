"""Every path a Manager's boundary grants has a written reason, or the suite fails.

🔴 **A grant with no recorded reason is a defect in itself** (DEFECT_CLASSES.md,
class 17). `~/.rite` was granted readable to every Manager with B9, as one line in
a batch of tool paths, with no reason written anywhere. It turned out to be
needed by nothing inside a boundary, and for a release it exposed every
Manager's mail and every registered project's path — for a firm, its client
list. Review could not catch it: there was no claim to check it against.

So the grants are ENUMERATED FROM THE CODE, not typed from memory: this reads
what `_tool_paths`, `_engine_state_paths` and `_system_paths` actually return
(Landlock reuses the first two) and fails on any path without an entry below.
A new grant cannot land without its reason, and a reason for a grant that is
gone fails too, so this list cannot drift into describing a profile that no
longer exists.

⚠ **A reason is what the grant is FOR and what breaks without it** — ideally
measured. "Inherited" is allowed, but it says so, which is a debt in plain
sight rather than one nobody can see.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.managers import enclosure

HOME_GRANTS = {
    ".local/bin": "where `uv tool install` puts `rite` and the engines' "
    "launchers; the instructions run rite by absolute path",
    ".local/share/uv": "rite's interpreter under uv dlopens libpython from here; "
    "measured `Abort trap: 6` with only `tools` granted",
    ".config/goose": "Goose reads its config here, and panics at start without it "
    "(measured, Linux observation pass)",
    ".local/share/cursor-agent": "Cursor's `agent` launcher and its bundled node; "
    "without it a Cursor launch exits 126 (measured, CU1 section 4). Read-only, "
    "no credential in it",
    ".gitconfig": "git identity for the Manager's commits; read-only, and denied "
    "write by name so a Manager cannot change what commits claim",
    ".config/git": "git's XDG config, same reason and same write deny as `.gitconfig`",
}

ENGINE_STATE_GRANTS = {
    ".local/share/goose": "Goose's session store, which holds the conversation "
    "handle every resumed cycle names; written",
    ".local/state/goose": "Goose's state directory, written during a session",
}

# ⚠ INHERITED means: the yoloAI Worker profile's set, adopted whole for B9 and
# not measured one path at a time. Named so, rather than dressed up.
INHERITED = "inherited from yoloAI's Worker set (B9), not individually measured"
SYSTEM_GRANTS = {
    "/usr/lib": "shared libraries every program loads",
    "/usr/bin": "system binaries a shell and git call",
    "/usr/sbin": INHERITED,
    "/usr/share": INHERITED,
    "/usr/local": "Intel Homebrew and local installs of tools rite calls",
    "/bin": "the shell itself (`/bin/sh` runs every cycle)",
    "/sbin": INHERITED,
    "/System": "macOS frameworks every program loads",
    "/Library": INHERITED,
    "/private/etc": "resolver and TLS configuration; https fails without it",
    "/opt/homebrew": "Apple-silicon Homebrew: tmux, gh, gitleaks",
    "/Applications": INHERITED,
    "/dev": "`/dev/null` and ttys; every `cmd >/dev/null` fails without it "
    "(measured), and it is granted writable as well",
    "/private/var/run": "resolver and daemon sockets; https fails at transport "
    "without it (measured, curl exit 56)",
    "/private/var/db": "resolver and daemon state, granted with /private/var/run "
    "for the same measured failure",
}


def _home_relative(paths, home: Path) -> set[str]:
    return {str(p.relative_to(home)) for p in paths}


def test_every_home_grant_has_a_reason():
    home = Path("/nonexistent-home")
    granted = _home_relative(enclosure._tool_paths(home), home)
    assert granted == set(HOME_GRANTS), (
        f"granted without a reason: {sorted(granted - set(HOME_GRANTS))}; "
        f"reason for a grant that is gone: {sorted(set(HOME_GRANTS) - granted)}"
    )


def test_every_engine_state_grant_has_a_reason():
    home = Path("/nonexistent-home")
    granted = _home_relative(enclosure._engine_state_paths(home), home)
    assert granted == set(ENGINE_STATE_GRANTS), sorted(
        granted ^ set(ENGINE_STATE_GRANTS)
    )


def test_every_system_grant_has_a_reason():
    granted = {str(p) for p in enclosure._system_paths()}
    assert granted == set(SYSTEM_GRANTS), sorted(granted ^ set(SYSTEM_GRANTS))


def test_rite_home_is_not_among_them():
    """The instance that made the rule, pinned by name."""
    assert ".rite" not in HOME_GRANTS
    assert ".rite" not in _home_relative(enclosure._tool_paths(Path("/h")), Path("/h"))


def test_every_reason_says_something():
    for table in (HOME_GRANTS, ENGINE_STATE_GRANTS, SYSTEM_GRANTS):
        for path, reason in table.items():
            assert len(reason.split()) >= 4, f"{path}: {reason!r} is not a reason"
