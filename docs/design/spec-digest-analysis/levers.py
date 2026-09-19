"""What the two levers in `rite spec status` actually cost.

The advice shipped without these numbers: "spec.slice_depth: 2 follows
references one step further, and a larger spec.pin_count loads more of the
sections everything depends on". True, and useless if depth 2 turns out to be
most of the document — that is the monolith depth 1 exists to avoid.
"""
import statistics, sys
from pathlib import Path
from rite_ai.spec.graph import INDEX, build_graph, classify
from rite_ai.spec.slice import compute_slice, closure
from rite_ai.spec.units import parse_file

def pcts(vals):
    vals = sorted(vals)
    p90 = vals[min(int(0.9 * len(vals)), len(vals) - 1)]
    return statistics.median(vals), p90

for arg in sys.argv[1:]:
    p = Path(arg)
    parsed = parse_file(p, source=p.name)
    graph = build_graph(parsed)
    print(f"\n=== {p.name} ({parsed.total_lines} lines, {len(parsed.units)} units)")
    print(f"{'pin_count':>10}{'depth':>7}{'p50':>9}{'p90':>9}{'max':>9}")
    for pin in (0, 8, 16):
        kinds = classify(graph, pin_count=pin)
        targets = [u for u in graph.units if kinds[u] != INDEX]
        for depth in (1, 2):
            r = [compute_slice(graph, kinds, t, parsed.total_lines, depth).ratio
                 for t in targets]
            p50, p90 = pcts(r)
            print(f"{pin:>10}{depth:>7}{p50:>8.1%}{p90:>8.1%}{max(r):>8.1%}")
    # transitive, for the comparison depth 1 exists to avoid
    kinds = classify(graph, pin_count=8)
    targets = [u for u in graph.units if kinds[u] != INDEX]
    tr = []
    for t in targets:
        ids = closure(graph, kinds, t, depth=len(graph.units), respect_kinds=False)
        lines = sum(graph.units[i].lines for i in ids if i in graph.units)
        tr.append(min(lines / parsed.total_lines, 1.0))
    print(f"{'transitive':>10}{'-':>7}{statistics.median(tr):>8.1%}{pcts(tr)[1]:>8.1%}{max(tr):>8.1%}")
