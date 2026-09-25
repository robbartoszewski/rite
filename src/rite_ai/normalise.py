"""Text normalisation: the agent sees what a human reviewer sees (N1, D-98).

⚠ **CORRECTNESS, NOT SECURITY.** This does not make ticket text safe, and it
must never be described as sanitising, cleaning or checking it. SPEC §6.6.3:
agent-directed attacks read as ordinary requirements, and no text filter
catches them. What this fixes is narrower. A tracker's UI does not show
some text that its API returns, so an agent could act on words a reviewer
never saw.

Three kinds of such text, each handled so the change is VISIBLE, never
silent:

* **Invisible characters are removed**, and counted by name in `note`. They
  are format characters (category Cf: zero-width spaces, bidi controls, the
  BOM), characters Unicode itself names as fillers or blanks (the Hangul
  fillers §9.15.3's sweep found), and control characters other than tab and
  newline. Derived by rule rather than listed, for the reason
  `V060_ANCHOR_LEGIBILITY.md` records: a list misses the next codepoint.
  ⚠ The ZERO WIDTH JOINER and NON-JOINER are KEPT between two non-ASCII
  characters, where emoji sequences and Indic or Persian shaping use them,
  and removed anywhere else. Between ASCII letters they only hide things.
* **Unicode tag characters are DECODED and shown.** They spell ASCII that
  renders as nothing, so a hidden instruction becomes
  `[hidden tag characters, decoded: "…"]`. The one legitimate use, a
  subdivision flag (🏴 followed by tag characters), is left alone.
* **HTML comments are SURFACED**, as `[HTML comment, not shown in the
  tracker: "…"]`, because a tracker renders them as nothing and its API
  returns them whole.

Not handled, and stated: combining marks and variation selectors, which
ordinary text needs. Homoglyphs, which look like the letter they imitate,
so a reviewer already sees what the agent sees.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import cache

_TAG_FIRST, _TAG_LAST = 0xE0000, 0xE007F
_BLACK_FLAG = "\U0001f3f4"
# Spelled as code points, never as literals or `\u` escapes: the formatter
# turns an escape into the literal character, and this module exists because
# invisible characters in text are invisible to a reviewer.
_ZWNJ, _ZWJ = chr(0x200C), chr(0x200D)
_JOINERS = {_ZWNJ, _ZWJ}
_LINE_BREAKS = {chr(0x2028), chr(0x2029)}
_COMMENT = re.compile(r"<!--(.*?)(?:-->|\Z)", re.S)


@cache
def _invisible(ch: str) -> bool:
    """Does this character render as nothing, by category or by its name?"""
    if ch in "\t\n\r":
        return False
    category = unicodedata.category(ch)
    if category in ("Cf", "Cc"):
        return True
    name = unicodedata.name(ch, "")
    return any(word in name for word in ("FILLER", "BLANK"))


@dataclass
class Normalised:
    text: str
    removed: Counter = field(default_factory=Counter)
    decoded: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed or self.decoded or self.comments)

    def note(self, what: str = "this text") -> str:
        """One line saying what was changed, or "" when nothing was."""
        if not self.changed:
            return ""
        parts = []
        if self.removed:
            total = sum(self.removed.values())
            names = ", ".join(f"{name} ×{n}" for name, n in self.removed.most_common(3))
            more = "" if len(self.removed) <= 3 else ", …"
            parts.append(f"removed {total} invisible character(s) ({names}{more})")
        if self.decoded:
            parts.append(f"decoded {len(self.decoded)} hidden tag-character run(s)")
        if self.comments:
            parts.append(f"surfaced {len(self.comments)} HTML comment(s)")
        return (
            f"[rite normalised {what} so it reads as the tracker shows it: "
            + "; ".join(parts)
            + ". This is not a safety check.]"
        )


def normalise(text: str) -> Normalised:
    """`text` as a reviewer sees it, with every change recorded."""
    out = Normalised(text="")
    text = _COMMENT.sub(lambda m: _surface(m, out), text or "")
    chars = list(text)
    kept: list[str] = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        cp = ord(ch)
        if _TAG_FIRST <= cp <= _TAG_LAST:
            j = i
            while j < len(chars) and _TAG_FIRST <= ord(chars[j]) <= _TAG_LAST:
                j += 1
            run = chars[i:j]
            if kept and kept[-1] == _BLACK_FLAG and run[-1] == "\U000e007f":
                kept.extend(run)  # a subdivision flag, e.g. England's
            else:
                decoded = "".join(
                    chr(ord(c) - _TAG_FIRST)
                    for c in run
                    if 0xE0020 <= ord(c) <= 0xE007E
                )
                if decoded:
                    out.decoded.append(decoded)
                    kept.append(f'[hidden tag characters, decoded: "{decoded}"]')
                else:
                    out.removed["TAG CHARACTER"] += len(run)
            i = j
            continue
        if ch in _JOINERS:
            before = kept[-1] if kept else ""
            after = chars[i + 1] if i + 1 < len(chars) else ""
            if _shaping(before) and _shaping(after):
                kept.append(ch)
            else:
                out.removed[unicodedata.name(ch)] += 1
            i += 1
            continue
        if ch in _LINE_BREAKS:
            kept.append("\n")
        elif _invisible(ch):
            out.removed[unicodedata.name(ch, f"U+{cp:04X}")] += 1
        else:
            kept.append(ch)
        i += 1
    out.text = "".join(kept)
    return out


def _shaping(ch: str) -> bool:
    """A neighbour that a joiner legitimately sits beside: visible, not ASCII."""
    return bool(ch) and ord(ch[-1]) > 0x7F and not _invisible(ch[-1])


def _surface(match: re.Match, out: Normalised) -> str:
    body = " ".join(match.group(1).split())
    out.comments.append(body)
    return f'[HTML comment, not shown in the tracker: "{body}"]'
