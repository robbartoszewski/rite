import re, pathlib, statistics as st
from collections import Counter
text=pathlib.Path("SPEC.md").read_text(); lines=text.split("\n")
hdr=re.compile(r'^(#{2,6})\s+(\d+(?:\.\d+)*)\.\s+(.*)$')
marks=[]
for i,l in enumerate(lines):
    m=hdr.match(l)
    if m: marks.append((i,m.group(2),m.group(3)))
units={};titles={}
for k,(i,num,t) in enumerate(marks):
    end=marks[k+1][0] if k+1<len(marks) else len(lines)
    units[num]=(i,end); titles[num]=t
sizes={u:(e-s) for u,(s,e) in units.items()}; ids=set(units); tot=len(lines)
CIT=re.compile(r'§\s*(\d+(?:\.\d+)*)')
def parents(u):
    p=u.split("."); return [".".join(p[:i]) for i in range(1,len(p)) if ".".join(p[:i]) in ids]
edges={}
for u,(s,e) in units.items():
    body="\n".join(lines[s:e])
    refs={m.group(1) for m in CIT.finditer(body) if m.group(1) in ids}
    edges[u]=(refs-{u})|set(parents(u))
indeg=Counter()
for u,vs in edges.items():
    for v in vs: indeg[v]+=1
HUBS=frozenset(u for u,_ in indeg.most_common(8))

def closure(start,stop,depth=None):
    seen={start};frontier={start};d=0
    while frontier and (depth is None or d<depth):
        nxt=set()
        for u in frontier:
            if u in stop and u!=start: continue
            nxt|=edges.get(u,set())
        nxt-=seen
        if not nxt: break
        seen|=nxt; frontier=nxt; d+=1
    return seen

print("=== practical config: hubs pinned, DEPTH-1 closure ===")
fr={u: sum(sizes[x] for x in closure(u,HUBS,1))/tot*100 for u in units}
v=sorted(fr.values())
print(f"slice %: p50={st.median(v):.1f}% p90={v[int(.9*len(v))]:.1f}% max={max(v):.1f}%  (+{sum(sizes[u] for u in HUBS)/tot*100:.1f}% pinned preamble)")
print(f"slices >15% of spec: {sum(1 for x in v if x>15)} of {len(v)}")
print("\n=== units that still explode under FULL transitive (the entangled core) ===")
tr={u: sum(sizes[x] for x in closure(u,HUBS))/tot*100 for u in units}
bad=sorted([(u,p) for u,p in tr.items() if p>50], key=lambda kv:-kv[1])
print(f"{len(bad)} of {len(units)} units reach >50% transitively")
for u,p in bad[:10]: print(f"  §{u:<7} {p:5.1f}%  {titles[u][:46]}")
tops=Counter(u.split('.')[0] for u,_ in bad)
print("  by top-level section:", dict(tops.most_common()))

print("\n=== the 5 slices >15% under the practical config ===")
for u,p in sorted(fr.items(), key=lambda kv:-kv[1])[:6]:
    print(f"  §{u:<8} {p:5.1f}%  out={len(edges[u]):<3} {titles[u][:44]}")
print("\n=== aggregator sections (huge out-degree = index, not dependency) ===")
for u,vs in sorted(edges.items(), key=lambda kv:-len(kv[1]))[:5]:
    print(f"  §{u:<8} out={len(vs):<3} {sizes[u]:>4}ln  {titles[u][:44]}")
AGG=frozenset(u for u,vs in edges.items() if len(vs)>=15)
fr2={u: sum(sizes[x] for x in closure(u,HUBS|AGG,1))/tot*100 for u in units}
v2=sorted(fr2.values())
print(f"\nwith {len(AGG)} aggregators also non-traversable: p50={st.median(v2):.1f}% p90={v2[int(.9*len(v2))]:.1f}% max={max(v2):.1f}%")
print(f"slices >15%: {sum(1 for x in v2 if x>15)} of {len(v2)}")
