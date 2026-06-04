"""Step 4: quality gate. (a) headless editor LOAD-test (structural validity),
(b) data-space CRITIC comparing generated stats to the real corpus factors."""
import json, subprocess, os, sys, collections, time, glob
sys.path.insert(0,"src"); import faithful
ROOT="/mnt/data/workspace/vcmi-mapgen"
MAPS=os.path.expanduser("~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps")
F=json.load(open(f"{ROOT}/out/factors.json")); LIB=json.load(open(f"{ROOT}/out/objlib.json"))
TYPE2PURPOSE={it["type"]:p for p,terr in LIB.items() for items in terr.values() for it in items}
TOT=sum(F["proportions"].values()); RATE={p:sum(v["counts"])/TOT for p,v in F["budget"].items()}

def load_test(vmap_path, wait=72):
    name="GATE_test"; dst=f"{MAPS}/{name}.vmap"
    subprocess.run(["cp",vmap_path,dst],check=True)
    log=f"/tmp/gate_{int(time.time())}.log"
    cmd=(f'Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb_g.log 2>&1 & XP=$!; sleep 2; '
         f'timeout {wait+25} dbus-run-session -- bash -c '
         f'\'export DISPLAY=:99; flatpak run --command=vcmieditor eu.vcmi.VCMI "{dst}" >{log} 2>&1 & sleep {wait}; echo done\' >/dev/null 2>&1; '
         f'kill $XP 2>/dev/null')
    subprocess.run(cmd,shell=True)
    txt=open(log).read() if os.path.exists(log) else ""
    fatal=any(s in txt for s in ("Failed to load file","Unknown entity","Cannot open map"))
    loaded=("Making object rects" in txt) and not fatal
    warn=txt.count("visitable but has no visitable directions")+txt.count("Animation  failed")
    return {"loaded":loaded,"fatal":fatal,"warnings":warn,
            "fatal_msg":next((l for l in txt.splitlines() if "Failed to load file" in l or "Unknown entity" in l),"")}

def critic(fm):
    W,H=fm["width"],fm["height"]; tiles=sum(1 for lvl in fm["terrain"] for row in lvl for _ in row)
    # per-purpose density (per 1000 tiles) generated vs corpus rate
    cnt=collections.Counter()
    terr_decor=collections.Counter(); terr_tiles=collections.Counter()
    for lvl in fm["terrain"]:
        for row in lvl:
            for c in row: terr_tiles[c["t"]]+=1
    for o in fm["objects"]:
        p=TYPE2PURPOSE.get(o["type"],"?"); cnt[p]+=1
        if p=="DECORATION":
            l=o["l"]
            try: terr_decor[fm["terrain"][l][o["y"]][o["x"]]["t"]]+=1
            except: pass
    dev=[]; score=0; npts=0
    for p,rate in RATE.items():
        target=1000*rate; got=1000*cnt.get(p,0)/tiles
        if target<0.05: continue
        ratio=got/target if target else 0; npts+=1
        ok=0.5<=ratio<=2.0; score+=ok
        if not ok: dev.append(f"{p}: {got:.1f} vs {target:.1f}/1k ({ratio:.2f}x)")
    return {"purpose_score":f"{score}/{npts}","deviations":dev[:12],
            "total_objs":len(fm["objects"]),"density_per_1k":round(1000*len(fm['objects'])/tiles)}

if __name__=="__main__":
    fm=faithful.load(f"{ROOT}/out/gen_engine.json")
    print("=== CRITIC (data-space vs corpus) ==="); 
    c=critic(fm); print(" ",c["purpose_score"],"purposes in [0.5,2]x of corpus; total density",c["density_per_1k"],"/1k")
    for d in c["deviations"]: print("   off:",d)
    print("=== LOAD-TEST (headless editor) ===")
    r=load_test(f"{ROOT}/out/gen_engine.vmap"); print(" ",r)
