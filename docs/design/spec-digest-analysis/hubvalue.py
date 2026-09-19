"""Do the two thin-slice causes travel together?

`unlinked_share` already warns when units cite nothing. This asks a different
question: how much TEXT the pinned hubs actually carry. A hub that is a bare
heading pins a line and no context, so a slice can be thin for a second,
independent reason — and if the two always co-fire, one warning is enough.
"""
import sys
from pathlib import Path
from rite_ai.spec.report import decomposition_report
from rite_ai.spec.units import parse_file

print(f"{'document':<34}{'unlinked':>10}{'hub lines':>11}{'hub share':>11}{'per hub':>9}")
print("-" * 75)
for arg in sys.argv[1:]:
    p = Path(arg)
    parsed = parse_file(p, source=p.name)
    r = decomposition_report(parsed)
    share = r.preamble_lines / r.total_lines if r.total_lines else 0
    per = r.preamble_lines / len(r.hubs) if r.hubs else 0
    print(f"{p.name[:33]:<34}{r.unlinked_share:>9.0%}{r.preamble_lines:>11}"
          f"{share:>10.1%}{per:>9.1f}")
