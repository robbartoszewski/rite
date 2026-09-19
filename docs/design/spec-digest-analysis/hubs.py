import re, pathlib, statistics as st
from collections import Counter
text=pathlib.Path("SPEC.md").read_text(); lines=text.split("\n")
hdr=re.compile(r'^(#{2,6})\s+(\d+(?:\.\d+)*)\.\s+(.*)$')
marks=[]
for i,l in enumerate(lines):
    m=hdr.match(l)
    if m: marks.append((i,m.group(2),m.group(3)))
units={}; titles={}
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
print("top in-degree (hubs):")
for u,c in indeg.most_common(8):
    print(f"  §{u:<7} in={c:<3} {sizes[u]:>4}ln  {titles[u][:44]}")

def closure(start, stop=frozenset()):
    seen={start}; frontier={start}
    while frontier:
        nxt=set()
        for u in frontier:
            if u in stop and u!=start: continue
            nxt|=edges.get(u,set())
        nxt-=seen
        if not nxt: break
        seen|=nxt; frontier=nxt
    return seen
for n in (0,3,5,8,12):
    stop=frozenset(u for u,_ in indeg.most_common(n))
    fr=[sum(sizes[x] for x in closure(u,stop))/tot*100 for u in units]
    print(f"transitive, top-{n:<2} hubs held as always-loaded: p50={st.median(fr):5.1f}% p90={sorted(fr)[int(.9*len(fr))]:5.1f}%"
          f"  (+hub preamble {sum(sizes[u] for u,_ in indeg.most_common(n))/tot*100:4.1f}%)")
