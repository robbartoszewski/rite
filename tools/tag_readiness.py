#!/usr/bin/env python3
"""Count v0.6.0 readiness from COMMITTED state, so a number can be recomputed
rather than re-argued.

Usage:  python3 tools/tag_readiness.py [git-ref]      (default: origin/main)

⚠ **Reads the doc with `git show <ref>:`, never the working tree.** A working
tree can hold a row nobody has merged, which is how a number gets inflated.

⚠ **"Done" is BOTH PLATFORMS.** Robert's definition this weekend is a
functional Claude *and* local multi-Manager setup, driven through Slack,
working on macOS *and* Linux. A row green on macOS and open on Linux is not
done, so the per-platform percentages are reported separately and the
both-platforms figure is the headline.

⚠ **"reported" is not "observed".** The doc's own header says rows marked
"reported" come from the Linux session's findings as relayed and were not
re-measured. They are counted as `reported`, not `obs`.
"""

from __future__ import annotations

import re
import subprocess
import sys

DOC = "docs/design/V060_TAG_READINESS.md"

# Rows that block the tag, each because the definition of done names it.
# Stated as data so the LIST can be disputed without re-doing the arithmetic.
BLOCKS_TAG = {
    "D1": "a Manager must run at all, inside a boundary — everything else sits on it",
    "D2": "the release advertises a boundary; an open escape contradicts it",
    "D3": "'Claude ... multi-Manager' — the Claude half of the definition",
    "D4": "'and local' — the local half of the definition",
    "D5": "'multi-Manager' — two Managers in one root is the feature itself",
    "D7": "'driven through Slack' — named in the definition",
    "D12": "CI green is release hygiene; a red gate cannot be tagged through",
    "D13": "credentials found headless, without which unattended and Slack do not run",
    "D18": "the notes claim Workers are boundaried; on Linux by default they are not",
}

# ⚠ **VERIFIED OVERRIDES.** A cell's leading marker is the doc's summary, and
# twice it is more optimistic than the cell's own prose. Counting the marker
# would inflate the number, which is the failure this script exists to stop.
# Each override cites what in the cell contradicts its marker.
OVERRIDES = {
    ("D5", "macos"): (
        "partial",
        'marked ✅ but the cell says "with stub engines" and "Not yet with a '
        'real Claude Owner" — and the definition of done names Claude',
    ),
    ("D9", "linux"): (
        "partial",
        'marked ✅ for the Slack clause, but the same cell carries "⏳ The '
        "board clause waits on the VM's GitHub login\" — half the row",
    ),
}

# ⚠ **D6's Linux cell is deliberately NOT overridden.** Its signal half is
# observed and its tmux half is open, but that hole is the same one D2 counts.
# Overriding here would count one defect twice and make Linux look worse than
# it is, so it is counted once, at D2.

STATES = ("obs", "reported", "test", "partial", "blocked", "none")


def classify(cell: str) -> str:
    """The state of one platform cell, from the legend the doc defines."""
    c = cell.strip()
    if c in ("—", "-", ""):
        return "none"
    if c.startswith("❌"):
        return "blocked"
    if c.startswith(("⏳", "⚠")):
        # ⏳ explicitly waiting; ⚠ is observed-but-degraded (D17, D18), which
        # is not the property the row claims.
        return "partial"
    if c.startswith("🧪"):
        return "test"
    if c.startswith("✅"):
        # The doc's own caveat: "reported" was relayed, not re-measured.
        if re.search(r"\*\*reported", c) or c.lstrip("✅ *").startswith("reported"):
            return "reported"
        return "obs"
    if c.startswith("✋"):
        return "reported"
    return "unknown"


def rows(ref: str):
    text = subprocess.run(
        ["git", "show", f"{ref}:{DOC}"], capture_output=True, text=True, check=True
    ).stdout
    for line in text.splitlines():
        if not re.match(r"^\| D\d+ \|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rid = cells[0]
        mac, lin = classify(cells[2]), classify(cells[3])
        if (rid, "macos") in OVERRIDES:
            mac = OVERRIDES[(rid, "macos")][0]
        if (rid, "linux") in OVERRIDES:
            lin = OVERRIDES[(rid, "linux")][0]
        yield {
            "id": cells[0],
            "what": re.sub(r"\*", "", cells[1])[:46],
            "macos": mac,
            "linux": lin,
            "closes": cells[4] if len(cells) > 4 else "",
        }


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    sha = subprocess.run(
        ["git", "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()
    data = list(rows(ref))
    total = len(data)
    blocking = [r for r in data if r["id"] in BLOCKS_TAG]

    print(f"v0.6.0 readiness — {ref} = {sha[:7]}   ({total} rows in §1)")
    print(f"done = observed on BOTH platforms. blocking rows: {len(blocking)}\n")

    for label, group in (("ALL ROWS", data), ("BLOCKING ONLY", blocking)):
        n = len(group)
        both = sum(1 for r in group if r["macos"] == "obs" and r["linux"] == "obs")
        print(f"{label}  (n={n})")
        for plat in ("macos", "linux"):
            counts = {s: sum(1 for r in group if r[plat] == s) for s in STATES}
            obs = counts["obs"]
            print(
                f"  {plat:6} observed {obs}/{n} = {pct(obs, n):>4}   "
                + "  ".join(f"{s}={counts[s]}" for s in STATES if counts[s])
            )
        print(f"  BOTH   observed {both}/{n} = {pct(both, n):>4}\n")

    print("NOT DONE (not observed on both), and what each waits on:")
    for r in data:
        if r["macos"] == "obs" and r["linux"] == "obs":
            continue
        flag = "BLOCKS" if r["id"] in BLOCKS_TAG else "      "
        gap = []
        if r["macos"] != "obs":
            gap.append(f"macOS={r['macos']}")
        if r["linux"] != "obs":
            gap.append(f"linux={r['linux']}")
        print(f"  {flag} {r['id']:4} {r['what']:46} {', '.join(gap)}")
        if r["closes"]:
            print(f"         waits on: {r['closes'][:150]}")
    if OVERRIDES:
        print("Rows whose marker was corrected against their own text:")
        for (rid, plat), (state, why) in OVERRIDES.items():
            print(f"  {rid} {plat} -> {state}: {why}")
        print()
    print("Blocking set (dispute the list, not the arithmetic):")
    for k, why in BLOCKS_TAG.items():
        print(f"  {k:4} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
