import json,glob,collections,os,sys
sys.path.insert(0,"src"); import ontology as ON, vmaplib, render
ROOT="/mnt/data/workspace/vcmi-mapgen"

def purpose_density(maps, resolver):
    """return dict purpose-> per-1000-tiles density, averaged over maps, + decoration share"""
    per=collections.defaultdict(list)
    for m in maps:
        tiles=sum(1 for lvl in m["terrain"] for row in lvl for _ in row)
        cnt=collections.Counter()
        for o in m["objects"]:
            cnt[resolver(o)]+=1
        for p,c in cnt.items(): per[p].append(1000*c/tiles)
        for p in PURP:           # ensure zeros counted
            if p not in cnt: per[p].append(0.0)
    return {p:(sum(v)/len(maps)) for p,v in per.items()}

PURP=["TOWN","MINE","RESOURCE_PILE","REWARD_PICKUP","GUARD","DWELLING","BANK","STAT_PERMANENT",
      "BONUS_TEMP","MANA","SPELL_SKILL","INFO","QUEST_GATE","TRANSPORT","WATER_TRANSPORT",
      "TERRAIN_MODIFIER","HERO","SPECIAL","DECORATION"]

human=[json.load(open(f)) for f in glob.glob(f"{ROOT}/out/maps/*.json")]
rmg=[vmaplib.load(p) for p in glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")]

H=purpose_density(human, lambda o: ON.resolve(o["class"],o["subclass"])["purpose"])
R=purpose_density(rmg,   lambda o: o["purpose"])

print(f"Human maps: {len(human)}   RMG maps: {len(rmg)}   (density = objects per 1000 tiles, avg per map)\n")
print(f"{'purpose':<18}{'HUMAN':>9}{'RMG':>9}{'RMG/HUMAN':>11}")
for p in PURP:
    h=H.get(p,0); r=R.get(p,0)
    ratio = (r/h) if h>1e-9 else (float('inf') if r>0 else 1)
    flag = "  <-- RMG overuses" if ratio>1.6 and r>0.05 else ("  <-- RMG underuses" if ratio<0.6 and h>0.05 else "")
    rs = f"{ratio:>9.2f}x" if ratio!=float('inf') else "      inf"
    print(f"{p:<18}{h:>9.3f}{r:>9.3f}{rs}{flag}")

# render the RMG maps
os.makedirs(f"{ROOT}/out/render",exist_ok=True)
for m in rmg:
    outp=f"{ROOT}/out/render/RMG_{m['name'].replace('.vmap','')}.png"
    render.render_map(m,outp)
print("\nrendered",len(rmg),"RMG maps to out/render/RMG_*.png")
