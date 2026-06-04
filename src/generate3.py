"""Direction 2/3 v2 - a COHERENT map: macro biome scaffold + Markov edge-texture
+ towns + roads connecting them + CLUSTERED decoration. Addresses the real feedback:
data-driven (Markov), and an actual map (towns + paths), not random vegetation.
"""
import json, glob, random, heapq, math, os, sys, collections, copy
sys.path.insert(0,"src"); import vmaplib, vmapwrite, render, markov_terrain as MK
ROOT="/mnt/data/workspace/vcmi-mapgen"
PAL=json.load(open(f"{ROOT}/out/palette.json"))
INT2CODE={0:"dt",1:"sa",2:"gr",3:"sn",4:"sw",5:"rg",6:"sb",7:"lv",8:"wt",9:"rc"}
HD={int(k):v for k,v in PAL["human_decor_density"].items()}
LAND=(0,1,2,3,4,5); WATER=8; LAVA=7

def value_noise(w,h,cell,rnd):
    gw,gh=w//cell+2,h//cell+2
    g=[[rnd.random() for _ in range(gw)] for _ in range(gh)]
    out=[[0.0]*w for _ in range(h)]
    for y in range(h):
        gy=y/cell; y0=int(gy); fy=gy-y0
        for x in range(w):
            gx=x/cell; x0=int(gx); fx=gx-x0
            a=g[y0][x0]*(1-fx)+g[y0][x0+1]*fx; b=g[y0+1][x0]*(1-fx)+g[y0+1][x0+1]*fx
            out[y][x]=a*(1-fy)+b*fy
    return out

# ---- 1. macro biome scaffold (isotropic blobs, controlled proportions) ----
def macro_terrain(W,H,rnd):
    weights={2:.34,0:.16,5:.12,3:.10,4:.08,1:.08,7:.06,8:.06}   # grass-heavy, some lakes
    types=list(weights); wts=[weights[t] for t in types]
    nseeds=max(10,W*H//500)
    seeds=[(rnd.randrange(W),rnd.randrange(H),rnd.choices(types,wts)[0]) for _ in range(nseeds)]
    nz=value_noise(W,H,max(6,W//10),rnd)
    g=[[0]*W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            best=1e18; bt=2
            for sx,sy,st in seeds:
                d=((x-sx)**2+(y-sy)**2)*(0.55+1.3*nz[y][x])
                if d<best: best=d; bt=st
            g[y][x]=bt
    return g

# ---- 2. Markov edge-texture: constrained Gibbs (only swap to a neighbour terrain) ----
def markov_blend(g, M4, rnd, sweeps=2, thresh=8):
    H=len(g); W=len(g[0])
    for s in range(sweeps):
        for y in range(1,H-1):
            for x in range(1,W-1):
                l,u,r,d=g[y][x-1],g[y-1][x],g[y][x+1],g[y+1][x]
                choices={l,u,r,d,g[y][x]}
                base=M4["full"][(l,u,r,d)]
                if sum(base.values())<thresh:
                    base=collections.Counter(); base.update(M4["horiz"][(l,r)]); base.update(M4["vert"][(u,d)])
                sub=collections.Counter({c:base.get(c,0)+1 for c in choices})   # restrict to local terrains
                g[y][x]=MK._sample(sub,rnd)
    return g

# ---- 3. towns on spaced land, away from water ----
def place_towns(g, K, rnd, minsep):
    H=len(g); W=len(g[0]); cand=[]
    for y in range(2,H-2):
        for x in range(2,W-2):
            if g[y][x] in (2,0,5) and all(g[y+dy][x+dx]!=WATER for dy in(-1,0,1) for dx in(-1,0,1)):
                cand.append((x,y))
    rnd.shuffle(cand); towns=[]
    for (x,y) in cand:
        if all((x-tx)**2+(y-ty)**2>=minsep*minsep for tx,ty in towns):
            towns.append((x,y))
            if len(towns)>=K: break
    return towns

# ---- 4. roads: connect towns (MST) via A* over passable terrain, carve to dirt ----
def astar(g,s,t):
    H=len(g); W=len(g[0])
    def cost(x,y): return {WATER:40,LAVA:12,5:3,9:30}.get(g[y][x],1)
    pq=[(0,s)]; dist={s:0}; prev={}
    while pq:
        c,(x,y)=heapq.heappop(pq)
        if (x,y)==t: break
        for dx,dy in((1,0),(-1,0),(0,1),(0,-1)):
            nx,ny=x+dx,y+dy
            if 0<=nx<W and 0<=ny<H:
                nc=c+cost(nx,ny)
                if nc<dist.get((nx,ny),1e18):
                    dist[(nx,ny)]=nc; prev[(nx,ny)]=(x,y); heapq.heappush(pq,(nc+abs(nx-t[0])+abs(ny-t[1]),(nx,ny)))
    path=[]; cur=t
    while cur in prev: path.append(cur); cur=prev[cur]
    path.append(s); return path

def carve_roads(g, towns, rnd):
    if len(towns)<2: return set()
    # MST over towns
    edges=sorted(((math.dist(a,b),i,j) for i,a in enumerate(towns) for j,b in enumerate(towns) if i<j))
    par=list(range(len(towns)))
    def find(a):
        while par[a]!=a: par[a]=par[par[a]]; a=par[a]
        return a
    road=set()
    for w,i,j in edges:
        if find(i)!=find(j):
            par[find(i)]=find(j)
            for (x,y) in astar(g,towns[i],towns[j]):
                if g[y][x] not in (WATER,):
                    g[y][x]=0; road.add((x,y))    # carve dirt path
    return road

# ---- 5. clustered decoration via a spatial density field (forests clump, clearings empty) ----
def mask_blocked(tpl,x,y):
    m=tpl.get("mask") or ["B"]; Hh=len(m); Ww=max(len(r) for r in m)
    for r in range(Hh):
        for c in range(len(m[r])):
            if m[r][c]=='B': yield x-(Ww-1-c), y-(Hh-1-r)
    yield x,y

def scatter_clustered(g, level, occ, counter, rnd):
    H=len(g); W=len(g[0]); objs=[]
    field=value_noise(W,H,max(5,W//14),rnd)          # spatial clumping field
    for y in range(H):
        for x in range(W):
            t=g[y][x]; pool=PAL["decor_by_terr"].get(str(t)) or []   # affinity-filtered by terrain
            if not pool: continue
            clump=field[y][x]**2.2*2.6                # peaks dense, troughs empty, ~avg 1
            dens=HD.get(t,0)/1000.0*clump
            if (x,y) in occ or rnd.random()>=dens: continue
            tpl=rnd.choice(pool); cells=list(mask_blocked(tpl.get("template",{}),x,y))
            if any((cx,cy) in occ or not(0<=cx<W and 0<=cy<H) for cx,cy in cells): continue
            for cc in cells: occ.add(cc)
            counter[0]+=1
            objs.append({"instanceName":f"d{counter[0]}","l":level,"subtype":tpl.get("subtype","object"),
                         "type":tpl.get("type"),"template":tpl.get("template",{}),"x":x,"y":y})
    return objs

if __name__=="__main__":
    rnd=random.Random(11); W=H=72
    print("learning terrain Markov..."); M4=MK.learn4(0)
    g=macro_terrain(W,H,rnd)
    markov_blend(g,M4,rnd,sweeps=2)
    hist=collections.Counter(t for row in g for t in row)
    print("terrain histogram:", {INT2CODE[k]:v for k,v in hist.most_common()})
    towns=place_towns(g, K=5, rnd=rnd, minsep=20)
    road=carve_roads(g, towns, rnd)
    print(f"towns={len(towns)} road tiles={len(road)}")
    # objects: clustered decoration + the towns themselves
    base_town=None
    for p in glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
        for o in vmaplib._relaxed(__import__('zipfile').ZipFile(p).read('objects.json').decode('utf-8','replace')):
            if o.get("type")=="town": base_town=o; break
        if base_town: break
    occ=set()
    town_objs=[]
    for i,(x,y) in enumerate(towns):
        to=copy.deepcopy(base_town); to["x"]=x; to["y"]=y; to["l"]=0; to["instanceName"]=f"town_{i}"
        town_objs.append(to)
        for cc in mask_blocked(to.get("template",{}),x,y): occ.add(cc)
    counter=[0]; decor=scatter_clustered(g,0,occ,counter,rnd)
    all_objs=town_objs+decor
    # header: single level, players' main towns = our towns
    header,_,_,_=vmapwrite.read_raw(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0])
    pls=list(header.get("players",{}).items())
    for i,(pid,pl) in enumerate(pls):
        pl["mainTown"]={"generateHero":True,"l":0,"x":towns[i][0],"y":towns[i][1]} if i<len(towns) else None
    terrain_str=[[ PAL["fill"].get(INT2CODE[t],"gr52_") for t in row] for row in g]
    out=f"{ROOT}/out/gen3_map.vmap"
    vmapwrite.write_vmap(out, header, [terrain_str], all_objs, name="gen3 coherent")
    print(f"towns={len(town_objs)} decoration={len(decor)} -> {out}")
    norm=vmaplib.load(out); render.render_map(norm, f"{ROOT}/out/render/gen3_map.png")
    print("rendered gen3_map.png")
