import json, glob, collections, os
ROOT="/mnt/data/workspace/vcmi-mapgen"
names={int(k):v for k,v in json.load(open(f"{ROOT}/src/objclass_names.json")).items()}
def nm(c): return names.get(c, f"CLASS_{c}")

# Decorative/passability-only "texture" classes (visual obstacles, no gameplay interaction)
DECOR_NAMES={"MOUNTAIN","OAK_TREES","PINE_TREES","ROCK","DEAD_VEGETATION","SHRUB","REEF",
"TREES","FLOWERS","CRATER","CACTUS","LAVA_FLOW","MUSHROOMS","LAKE","STUMP","HOLE","HEDGE",
"KELP","WILLOW_TREES","YUCCA_TREES","VOLCANO","SAND_DUNE","SAND_PIT","CANYON","MOSS","BUSH",
"PALM_TREE","PINE_TREE","PLANT","RIVER_DELTA","PINE","PINES","PINES_TREES","PINES_TREE",
"PINE_TREE","FROZEN_LAKE","PINE_TREES2","ROCKLAND"}

maps=[json.load(open(f)) for f in glob.glob(f"{ROOT}/out/maps/*.json")]
total_count=collections.Counter()
maps_present=collections.Counter()
total_land=0
for m in maps:
    seen=set()
    land=sum(1 for lvl in m["terrain"] for row in lvl for c in row)  # all tiles
    total_land+=land
    for o in m["objects"]:
        n=nm(o["class"]); total_count[n]+=1; seen.add(n)
    for n in seen: maps_present[n]+=1

NM=len(maps)
def density(n): return 1000.0*total_count[n]/total_land
rows=[]
for n,ct in total_count.items():
    rows.append((n, ct, maps_present[n], ct/NM, density(n), n in DECOR_NAMES))
rows.sort(key=lambda r:-r[1])

def show(title, pred):
    print(f"\n=== {title} ===")
    print(f"{'category':<26}{'total':>8}{'/159 maps':>11}{'per-map':>9}{'per 1k tiles':>13}")
    for n,ct,mp,pm,d,dec in rows:
        if not pred(dec): continue
        print(f"{n:<26}{ct:>8}{mp:>11}{pm:>9.1f}{d:>13.3f}")

print(f"corpus: {NM} maps, {total_land} total tiles, {sum(total_count.values())} objects")
show("GAMEPLAY objects (ranked by total)", lambda dec: not dec)
print("\n--- key 'connectivity' objects (the portal-overuse question) ---")
for n in ["MONOLITH_TWO_WAY","MONOLITH_ONE_WAY_ENTRANCE","MONOLITH_ONE_WAY_EXIT","SUBTERRANEAN_GATE","WHIRLPOOL"]:
    if n in total_count:
        print(f"  {n:<28} total {total_count[n]:>4}  in {maps_present[n]:>3}/159 maps  {density(n):.3f}/1k")
