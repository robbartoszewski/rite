import sys
from pathlib import Path
from rite_ai.spec.graph import INDEX, build_graph, classify
from rite_ai.spec.units import parse_file

print(f"{'document':<34}{'units':>7}{'cite 0':>9}{'share':>8}{'islands':>9}")
print("-" * 67)
for arg in sys.argv[1:]:
    p = Path(arg)
    parsed = parse_file(p, source=p.name)
    g = build_graph(parsed)
    kinds = classify(g)
    ins = g.in_degree()
    body = [u for u in parsed.units if kinds.get(u.id) != INDEX]
    silent = [u for u in body if not g.citations.get(u.id)]
    # An island cites nothing AND nothing cites it: a slice of it is the unit
    # plus the pinned hubs, whatever its real dependencies are.
    islands = [
        u for u in silent
        if not any(u.id in (g.citations.get(o.id) or frozenset()) for o in body)
    ]
    share = len(silent) / len(body) if body else 0
    print(f"{p.name[:33]:<34}{len(body):>7}{len(silent):>9}{share:>7.0%}{len(islands):>9}")
