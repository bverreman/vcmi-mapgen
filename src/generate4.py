"""Direction 3 - lift-driven decoration. Reuses gen3 terrain/towns/roads, but places
decoration by CONDITIONAL SAMPLING on the measured pairwise lift Phi (out/lift.json):
P(type T at tile) grows with same/related decoration nearby per the learned lift, so
clustering EMERGES (pine groves, mountain ranges) instead of a cosmetic noise field.
"""
import json, glob, random, math, collections, os, sys, copy
sys.path.insert(0,"src"); import vmaplib, vmapwrite, render, generate3 as G3
ROOT="/mnt/data/workspace/vcmi-mapgen"
PAL=json.load(open(f"{ROOT}/out/palette.json"))
LIFT=json.load(open(f"{ROOT}/out/lift.json"))
INT2CODE=G3.INT2CODE; HD=G3.HD

# vmap decoration type -> Phi entity (curated names get type-specific lift; rest -> DECORATION)
VMAP2ENT={"pineTrees":"PINE_TREES","oakTrees":"OAK_TREES","mountain":"MOUNTAIN","rock":"ROCK",
          "lake":"LAKE","mushrooms":"MUSHROOMS","flowers":"FLOWERS"}
def cls(vtype): return VMAP2ENT.get(vtype,"DECORATION")
def _clamp(v): return min(max(v,0.1),12.0)
def lift(a,b,band):
    row=LIFT["lift"].get(a,{}).get(b)
    v=row[band] if row else None
    return _clamp(v) if v else 1.0
# data-driven generic decoration self-attraction profile (mean curated self-lift per band)
_DECO=["PINE_TREES","OAK_TREES","MOUNTAIN","ROCK","LAKE","MUSHROOMS","FLOWERS","RANDOM_RESOURCE"]
def _selfprof():
    out=[]
    for b in (0,1):
        vals=[LIFT["lift"][e][e][b] for e in _DECO if LIFT["lift"].get(e,{}).get(e) and LIFT["lift"][e][e][b]]
        out.append(sum(vals)/len(vals) if vals else 1.5)
    return out
GEN_SELF=_selfprof()
def self_lift(vtype,b):
    e=VMAP2ENT.get(vtype)
    if e:
        row=LIFT["lift"].get(e,{}).get(e)
        if row and row[b]: return _clamp(row[b])
    return GEN_SELF[b]

def place_lift_decor(g, level, occ, counter, rnd, sweeps=4, radius=6):
    """sequential conditional sampler; returns placed object dicts."""
    H=len(g); W=len(g[0]); D=[[None]*W for _ in range(H)]; objs=[]
    # sparse seed rate; same-type lift grows groves around seeds (capped at target count)
    target=sum(HD.get(g[y][x],0)/1000.0 for y in range(H) for x in range(W))
    ALPHA=0.5                          # soften clustering (real maps are spread, not tight groves)
    def base(t):
        p=0.6*HD.get(t,0)/1000.0       # density ~ human; type mix from human weights below
        return p/max(1e-6,1-p)
    for s in range(sweeps):
        if len(objs)>=target: break
        order=[(x,y) for y in range(H) for x in range(W)]; rnd.shuffle(order)
        for (x,y) in order:
            if len(objs)>=target: break
            if (x,y) in occ or D[y][x] is not None: continue
            t=g[y][x]; pool=PAL["decor_by_terr"].get(str(t))
            if not pool: continue
            # neighbour decoration by EXACT type x band
            contrib=collections.Counter()
            for dy in range(-radius,radius+1):
                for dx in range(-radius,radius+1):
                    if dx==0 and dy==0: continue
                    nx,ny=x+dx,y+dy
                    if 0<=nx<W and 0<=ny<H and D[ny][nx] is not None:
                        cheb=max(abs(dx),abs(dy)); b=0 if cheb<=2 else 1
                        contrib[(D[ny][nx],b)]+=1
            b0=base(t); totw=sum(tp["weight"] for tp in pool)
            scores=[]; cand=[]
            for tpl in pool:
                T=tpl["type"]; lp=math.log(b0)+math.log(tpl["weight"]/totw)   # human-frequency weight
                for (nt,b),cnt in contrib.items():
                    l=self_lift(T,b) if nt==T else lift(cls(T),cls(nt),b)
                    lp+=ALPHA*cnt*math.log(l)                                  # softened clustering
                scores.append(lp); cand.append(tpl)
            scores.append(0.0); cand.append(None)            # empty option (weight 1)
            mx=max(scores); ex=[math.exp(v-mx) for v in scores]; tot=sum(ex)
            r=rnd.random()*tot; acc=0; chosen=None
            for i,e in enumerate(ex):
                acc+=e
                if r<=acc: chosen=cand[i]; break
            if chosen is None: continue
            cells=list(G3.mask_blocked(chosen.get("template",{}),x,y))
            if any((cx,cy) in occ or not(0<=cx<W and 0<=cy<H) for cx,cy in cells): continue
            for cc in cells: occ.add(cc)
            D[y][x]=chosen["type"]; counter[0]+=1
            objs.append({"instanceName":f"d{counter[0]}","l":level,"subtype":chosen.get("subtype","object"),
                         "type":chosen["type"],"template":chosen.get("template",{}),"x":x,"y":y})
    return objs

if __name__=="__main__":
    rnd=random.Random(11); W=H=72
    print("learning terrain Markov + loading lift Phi...")
    M4=G3.MK.learn4(0)
    g=G3.macro_terrain(W,H,rnd); G3.markov_blend(g,M4,rnd,sweeps=2)
    towns=G3.place_towns(g,K=5,rnd=rnd,minsep=20); road=G3.carve_roads(g,towns,rnd)
    # town objects + occupancy
    base_town=None
    for p in glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
        for o in vmaplib._relaxed(__import__('zipfile').ZipFile(p).read('objects.json').decode('utf-8','replace')):
            if o.get("type")=="town": base_town=o; break
        if base_town: break
    occ=set(); town_objs=[]
    for i,(x,y) in enumerate(towns):
        to=copy.deepcopy(base_town); to.update({"x":x,"y":y,"l":0,"instanceName":f"town_{i}"}); town_objs.append(to)
        for cc in G3.mask_blocked(to.get("template",{}),x,y): occ.add(cc)
    counter=[0]; decor=place_lift_decor(g,0,occ,counter,rnd,sweeps=6)
    print(f"towns={len(town_objs)}  lift-driven decoration={len(decor)}  ({1000*len(decor)/(W*H):.0f}/1000 tiles)")
    # ACHIEVED clustering: band0 self-lift for the top types (observed same-type nbrs / expected)
    bytype=collections.defaultdict(list)
    for o in decor: bytype[o["type"]].append((o["x"],o["y"]))
    print("achieved band0 self-lift (vs Phi target ~2-2.6; >1 = clustered):")
    for T,pts in sorted(bytype.items(),key=lambda kv:-len(kv[1]))[:6]:
        n=len(pts);
        if n<15: continue
        S=set(pts); obs=sum(1 for (x,y) in pts for dx in range(-2,3) for dy in range(-2,3)
                            if (dx or dy) and (x+dx,y+dy) in S)
        dens=n/(W*H); exp=obs and (n*24*dens)   # 24 tiles in 5x5 ring
        print(f"   {T:<16} n={n:<4} self-lift={obs/exp:.1f}" if exp else f"   {T}: n={n}")
    header,_,_,_=vmapwrite.read_raw(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0])
    for i,(pid,pl) in enumerate(list(header.get("players",{}).items())):
        pl["mainTown"]={"generateHero":True,"l":0,"x":towns[i][0],"y":towns[i][1]} if i<len(towns) else None
    terr=[[PAL["fill"].get(INT2CODE[t],"gr52_") for t in row] for row in g]
    out=f"{ROOT}/out/gen4_lift.vmap"
    vmapwrite.write_vmap(out, header, [terr], town_objs+decor, name="gen4 lift")
    render.render_map(vmaplib.load(out), f"{ROOT}/out/render/gen4_lift.png")
    # decoration type-cluster check: how clumped is each curated type?
    print("wrote", out, "and render gen4_lift.png")
