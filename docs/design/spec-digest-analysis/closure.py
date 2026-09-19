import re, pathlib, statistics as st
text = pathlib.Path("SPEC.md").read_text(); lines = text.split("\n")
hdr = re.compile(r'^(#{2,6})\s+(\d+(?:\.\d+)*)\.\s+(.*)$')
marks=[]
for i,l in enumerate(lines):
    m=hdr.match(l)
    if m: marks.append((i,m.group(2)))
units={}
for k,(i,num) in enumerate(marks):
    end = marks[k+1][0] if k+1<len(marks) else len(lines)
    units[num]=(i,end)
sizes={u:(e-s) for u,(s,e) in units.items()}
ids=set(units)

CIT=re.compile(r'§\s*(\d+(?:\.\d+)*)')
def parents(u):
    p=u.split("."); return [".".join(p[:i]) for i in range(1,len(p)) if ".".join(p[:i]) in ids]

edges={}
for u,(s,e) in units.items():
    body="\n".join(lines[s:e])
    refs={m.group(1) for m in CIT.finditer(body)}
    refs={r for r in refs if r in ids and r!=u}
    edges[u]=refs|set(parents(u))

od=[len(v) for v in edges.values()]
print(f"out-degree: p50={st.median(od)} p90={sorted(od)[int(.9*len(od))]} max={max(od)}")

def closure(start,depth=None):
    seen={start}; frontier={start}; d=0
    while frontier and (depth is None or d<depth):
        nxt=set()
        for u in frontier: nxt|=edges.get(u,set())
        nxt-=seen
        if not nxt: break
        seen|=nxt; frontier=nxt; d+=1
    return seen

tot=len(lines)
for d in (0,1,2,3,None):
    fr=[]
    for u in units:
        c=closure(u,d); fr.append(sum(sizes[x] for x in c)/tot*100)
    lbl = "transitive" if d is None else f"depth {d}"
    print(f"{lbl:>11}: slice %of spec  p50={st.median(fr):5.1f}%  p90={sorted(fr)[int(.9*len(fr))]:5.1f}%  max={max(fr):5.1f}%")
