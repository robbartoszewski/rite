"""Built-in scan patterns that ship with the gate, always on.

SPEC §11.3 draws a line between two classes of default finding: project
markers the *user* declares (customer names, internal codenames, account
or record identifiers — see `publish_gate.scan_patterns`) and hardcoded
local paths, which rite must always check because no user declares them.
A path like `/Users/exampleuser/dev/client-x/` is not client data, but it
exposes a username and machine layout to a public repo — SPEC's own worked
example (§11.1) is four pushed commits carrying exactly this.

These are content patterns, not filename patterns — they match a path-shaped
string appearing inside a file (source, docs, comments), not the name of the
file itself. `gate.pattern_scan` runs the same pattern list against both file
content and file names, so a pattern only needs to be declared once here.
"""

from __future__ import annotations

from rite_ai.config.models import ScanPattern

BUILTIN_PATH_PATTERNS: list[ScanPattern] = [
    ScanPattern(
        type="path",
        pattern=r"/Users/[A-Za-z0-9._-]+/",
        description="Hardcoded macOS home directory path",
    ),
    ScanPattern(
        type="path",
        pattern=r"/home/[A-Za-z0-9._-]+/",
        description="Hardcoded Linux home directory path",
    ),
    ScanPattern(
        type="path",
        # Matches both `C:\Users\name\` and the escaped form as it appears in
        # source (`C:\\Users\\name\\`) — the literal backslash count in the
        # file differs by encoding, so match one-or-more backslashes.
        pattern=r"[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+\\+",
        description="Hardcoded Windows home directory path",
    ),
]
