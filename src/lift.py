"""Pairwise lift matrix Phi (Epic 2 keystone).

Phi(A, B, band) = observed co-occurrence of entity B near entity A in a Chebyshev
distance band, divided by the TERRAIN-CONTROLLED expected count. >1 attract, <1 repel.

Terrain control: expected B near a center = (valid-B-terrain tiles in the band ring
around that center) * (global density of B per valid-B tile). So co-occurrence that is
merely terrain-coupled (both on water) is NOT counted as object attraction.

Entity = ontology name if it is a curated type we care about, else its purpose.
"""
import json, glob, collections, sys
import numpy as np
sys.path.insert(0,"src"); import ontology as ON
ROOT="/mnt/data/workspace/vcmi-mapgen"

CURATED={"MOUNTAIN","PINE_TREES","OAK_TREES","ROCK","LAKE","MUSHROOMS","FLOWERS",
         "MINE","RANDOM_RESOURCE","RESOURCE","TREASURE_CHEST","TOWN","RANDOM_TOWN",
         "MONOLITH_TWO_WAY","CREATURE_GENERATOR1"}
def entity(o):
    r=ON.resolve(o["class"],o["subclass"])
    return r["name"] if r["name"] in CURATED else r["purpose"]

BANDS=[(1,2),(3,6),(7,15)]     # Chebyshev rings (band excludes the center tile)
CAP=150                         # max sampled centers per entity per map
maps=[json.load(open(f)) for f in glob.glob(f"{ROOT}/out/maps/*.json")]

# ---- pass 1: counts, per-entity terrain distribution -> allowed set, density rho ----
ent_terr=collections.defaultdict(collections.Counter); ent_count=collections.Counter()
terr_total=collections.Counter()
for m in maps:
    for lvl in m["terrain"]:
        for row in lvl:
            for c in row: terr_total[c["t"]]+=1
    for o in m["objects"]:
        l=o["l"]
        if l>=len(m["terrain"]): continue
        try: t=m["terrain"][l][o["y"]][o["x"]]["t"]
        except: continue
        ent_terr[entity(o)][t]+=1; ent_count[entity(o)]+=1

allowed={}
for e,c in ent_terr.items():
    tot=sum(c.values()); top=c.most_common(1)[0][0]
    allowed[e]=sorted({k for k,v in c.items() if v/tot>=0.10}|{top})
ENT=sorted(ent_count); EI={e:i for i,e in enumerate(ENT)}; nE=len(ENT)
TERR=sorted(terr_total); TIx={t:i for i,t in enumerate(TERR)}; nT=len(TERR)
valid_tiles={e:sum(terr_total[t] for t in allowed[e]) for e in ENT}
rho=np.array([ent_count[e]/valid_tiles[e] if valid_tiles[e] else 0 for e in ENT])
ALLOWED=np.zeros((nE,nT))
for e in ENT:
    for t in allowed[e]: ALLOWED[EI[e],TIx[t]]=1.0

# ---- pass 2: observed + terrain-controlled expected via integral images ----
observed=np.zeros((nE,nE,len(BANDS))); expected=np.zeros((nE,nE,len(BANDS)))
def integral(mat): return np.pad(np.cumsum(np.cumsum(mat,0),1),((1,0),(1,0)))
def box(SII,x0,y0,x1,y1,W,H):
    x0=max(x0,0);y0=max(y0,0);x1=min(x1,W-1);y1=min(y1,H-1)
    if x1<x0 or y1<y0: return np.zeros(SII.shape[0])
    return SII[:,y1+1,x1+1]-SII[:,y0,x1+1]-SII[:,y1+1,x0]+SII[:,y0,x0]

for m in maps:
    for level,lvl in enumerate(m["terrain"]):
        grid=np.array([[c["t"] for c in row] for row in lvl]); H,W=grid.shape
        TII=np.stack([integral((grid==t).astype(np.float64)) for t in TERR])
        objmat=np.zeros((nE,H,W)); here=collections.defaultdict(list)
        for o in m["objects"]:
            if o["l"]!=level: continue
            x,y=o["x"],o["y"]
            if 0<=x<W and 0<=y<H:
                e=entity(o); objmat[EI[e],y,x]+=1; here[e].append((x,y))
        EII=np.stack([integral(objmat[i]) for i in range(nE)])
        for e,pos in here.items():
            ai=EI[e]
            cs=pos if len(pos)<=CAP else pos[::max(1,len(pos)//CAP)][:CAP]
            for cx,cy in cs:
                for bi,(lo,hi) in enumerate(BANDS):
                    obs=box(EII,cx-hi,cy-hi,cx+hi,cy+hi,W,H)-box(EII,cx-(lo-1),cy-(lo-1),cx+(lo-1),cy+(lo-1),W,H)
                    tcnt=box(TII,cx-hi,cy-hi,cx+hi,cy+hi,W,H)-box(TII,cx-(lo-1),cy-(lo-1),cx+(lo-1),cy+(lo-1),W,H)
                    observed[ai,:,bi]+=obs
                    expected[ai,:,bi]+=(ALLOWED@tcnt)*rho

# ---- lift + outputs ----
with np.errstate(divide='ignore',invalid='ignore'):
    lift=np.where(expected>0, observed/expected, np.nan)
MIN_OBS=20    # confidence floor on observed count
out={"entities":ENT,"bands":BANDS,
     "lift":{}, "observed":{}, "note":"lift>1 attract, <1 repel; nan/low-obs = sparse"}
for a in ENT:
    out["lift"][a]={b:[round(float(lift[EI[a],EI[b],k]),3) if observed[EI[a],EI[b],k]>=MIN_OBS else None
                       for k in range(len(BANDS))] for b in ENT}
json.dump(out, open(f"{ROOT}/out/lift.json","w"))

def L(a,b,k):
    o=observed[EI[a],EI[b],k]; return (round(float(lift[EI[a],EI[b],k]),2) if o>=MIN_OBS else None)
print(f"entities={nE}  (band0=1-2, band1=3-6, band2=7-15 tiles, Chebyshev)\n")
print("=== SANITY: known relationships (lift by band; >1 attract, <1 repel) ===")
for a,b in [("PINE_TREES","PINE_TREES"),("OAK_TREES","OAK_TREES"),("MOUNTAIN","MOUNTAIN"),
            ("GUARD","TREASURE_CHEST"),("GUARD","REWARD_PICKUP"),("TREASURE_CHEST","GUARD"),
            ("MINE","MINE"),("TOWN","TOWN"),("RANDOM_RESOURCE","RANDOM_RESOURCE"),
            ("MONOLITH_TWO_WAY","TOWN")]:
    if a in EI and b in EI: print(f"  {a:<16}~ {b:<16} {[L(a,b,k) for k in range(3)]}")
# top attract / repel at short range (band0), confident only
print("\n=== strongest SHORT-RANGE (0-2) attractions ===")
pairs=[(a,b,lift[EI[a],EI[b],0]) for a in ENT for b in ENT if observed[EI[a],EI[b],0]>=MIN_OBS]
for a,b,v in sorted(pairs,key=lambda r:-r[2])[:12]: print(f"  {a:<16}~ {b:<16} {v:.1f}x")
print("=== strongest SHORT-RANGE (0-2) repulsions ===")
for a,b,v in sorted(pairs,key=lambda r:r[2])[:12]: print(f"  {a:<16}~ {b:<16} {v:.2f}x")
print("\nwrote out/lift.json")
