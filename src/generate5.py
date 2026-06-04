"""Direction 3 v3 - DESIGN-FIRST decoration. Build the navigable space (open town
zones + clear road corridors), then fill only the WILDERNESS between with decoration,
density ramping up with distance from the open network. Decoration is the negative
space of the path design, not a texture on every tile.
"""
import json, glob, random, collections, os, sys, copy, math
from collections import deque
sys.path.insert(0,"src"); import vmaplib, vmapwrite, render, generate3 as G3, generate4 as G4
ROOT="/mnt/data/workspace/vcmi-mapgen"
PAL=json.load(open(f"{ROOT}/out/palette.json")); INT2CODE=G3.INT2CODE; HD=G4.HD

def clearance_and_dist(W,H,towns,roads,RZONE=9,RROAD=2):
    clear=[[False]*W for _ in range(H)]
    def stamp(cx,cy,r):
        for dy in range(-r,r+1):
            for dx in range(-r,r+1):
                if max(abs(dx),abs(dy))<=r:
                    x,y=cx+dx,cy+dy
                    if 0<=x<W and 0<=y<H: clear[y][x]=True
    for (tx,ty) in towns: stamp(tx,ty,RZONE)        # open zone around each town
    for (rx,ry) in roads: stamp(rx,ry,RROAD)        # clear corridor along roads
    dist=[[10**9]*W for _ in range(H)]; q=deque()
    for y in range(H):
        for x in range(W):
            if clear[y][x]: dist[y][x]=0; q.append((x,y))
    while q:
        x,y=q.popleft()
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nx,ny=x+dx,y+dy
            if 0<=nx<W and 0<=ny<H and dist[ny][nx]>dist[y][x]+1:
                dist[ny][nx]=dist[y][x]+1; q.append((nx,ny))
    return clear,dist

def place_wilderness(g, clear, dist, occ, counter, rnd, sweeps=2, RAMP=11, ALPHA=0.0, SCALE=2.0):
    H=len(g); W=len(g[0]); D=[[None]*W for _ in range(H)]; objs=[]
    field=G3.value_noise(W,H,max(6,W//8),rnd)        # clumpy intensity: forest patches vs clearings
    def density(t,x,y):
        if clear[y][x]: return 0.0
        f=(field[y][x]**2)*2.6                        # contrast: peaks dense, troughs ~empty (avg ~1)
        return min(0.22, SCALE*HD.get(t,0)/1000.0 * min(1.0, dist[y][x]/RAMP) * f)  # cap: never fully pack (no grid)
    for s in range(sweeps):
        order=[(x,y) for y in range(H) for x in range(W)]; rnd.shuffle(order)
        for (x,y) in order:
            if (x,y) in occ or D[y][x] is not None or clear[y][x]: continue
            t=g[y][x]; pool=PAL["decor_by_terr"].get(str(t))
            if not pool: continue
            p=density(t,x,y)
            if p<=0: continue
            b0=p/max(1e-6,1-p); totw=sum(tp["weight"] for tp in pool)
            scores=[]; cand=[]
            contrib=collections.Counter()
            for dy in range(-6,7):
                for dx in range(-6,7):
                    if (dx or dy):
                        nx,ny=x+dx,y+dy
                        if 0<=nx<W and 0<=ny<H and D[ny][nx] is not None:
                            contrib[(D[ny][nx],0 if max(abs(dx),abs(dy))<=2 else 1)]+=1
            for tpl in pool:
                T=tpl["type"]; lp=math.log(b0)+math.log(tpl["weight"]/totw)
                for (nt,b),cnt in contrib.items():
                    l=G4.self_lift(T,b) if nt==T else G4.lift(G4.cls(T),G4.cls(nt),b)
                    lp+=ALPHA*cnt*math.log(l)
                scores.append(lp); cand.append(tpl)
            scores.append(0.0); cand.append(None)
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
            objs.append({"instanceName":f"d{counter[0]}","l":0,"subtype":chosen.get("subtype","object"),
                         "type":chosen["type"],"template":chosen.get("template",{}),"x":x,"y":y})
    return objs

if __name__=="__main__":
    rnd=random.Random(11); W=H=72
    M4=G3.MK.learn4(0)
    g=G3.macro_terrain(W,H,rnd); G3.markov_blend(g,M4,rnd,sweeps=2)
    towns=G3.place_towns(g,K=5,rnd=rnd,minsep=20); road=G3.carve_roads(g,towns,rnd)
    clear,dist=clearance_and_dist(W,H,towns,road)
    base_town=None
    for p in glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
        for o in vmaplib._relaxed(__import__('zipfile').ZipFile(p).read('objects.json').decode('utf-8','replace')):
            if o.get("type")=="town": base_town=o; break
        if base_town: break
    occ=set(); town_objs=[]
    for i,(x,y) in enumerate(towns):
        to=copy.deepcopy(base_town); to.update({"x":x,"y":y,"l":0,"instanceName":f"town_{i}"}); town_objs.append(to)
        for cc in G3.mask_blocked(to.get("template",{}),x,y): occ.add(cc)
    counter=[0]; decor=place_wilderness(g,clear,dist,occ,counter,rnd)
    clearpct=sum(1 for y in range(H) for x in range(W) if clear[y][x])*100//(W*H)
    print(f"towns={len(town_objs)}  decoration={len(decor)} ({1000*len(decor)//(W*H)}/1000)  open/clear={clearpct}% of map")
    header,_,_,_=vmapwrite.read_raw(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0])
    for i,(pid,pl) in enumerate(list(header.get("players",{}).items())):
        pl["mainTown"]={"generateHero":True,"l":0,"x":towns[i][0],"y":towns[i][1]} if i<len(towns) else None
    terr=[[PAL["fill"].get(INT2CODE[t],"gr52_") for t in row] for row in g]
    out=f"{ROOT}/out/gen5_design.vmap"
    vmapwrite.write_vmap(out, header, [terr], town_objs+decor, name="gen5 design")
    print("wrote",out)
