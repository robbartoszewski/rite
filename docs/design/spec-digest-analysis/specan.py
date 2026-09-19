import re, pathlib, statistics as st
text = pathlib.Path("SPEC.md").read_text()
lines = text.split("\n")

# --- units: numbered headings ---
units = []  # (id, start, end, title)
hdr = re.compile(r'^(#{2,6})\s+(\d+(?:\.\d+)*)\.\s+(.*)$')
marks = []
for i, l in enumerate(lines):
    m = hdr.match(l)
    if m: marks.append((i, m.group(2), m.group(3)))
for k, (i, num, title) in enumerate(marks):
    end = marks[k+1][0] if k+1 < len(marks) else len(lines)
    units.append((num, i, end, title))

sizes = {u[0]: u[2]-u[1] for u in units}
tot = len(lines)
print(f"SPEC: {tot} lines, {len(units)} numbered units")
v = sorted(sizes.values())
print(f"unit lines: min={v[0]} p50={st.median(v)} p90={v[int(.9*len(v))]} max={max(v)} mean={st.mean(v):.0f}")
print(f"units >100 lines: {sum(1 for x in v if x>100)}   >200: {sum(1 for x in v if x>200)}")
big = sorted(sizes.items(), key=lambda kv:-kv[1])[:6]
print("largest:", [(k, n) for k,n in big])
