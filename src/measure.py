"""Step 2: measure generation-target factors from the faithful corpus.
Outputs:
  out/factors.json  : terrain Markov (4-nbr) per level-kind, decoration density per terrain,
                      gameplay budget (per-purpose per-map counts), terrain proportions
  out/objlib.json   : object library {purpose: {terrain: [ {type,subtype,animation,mask,weight} ]}}
                      = the REAL object palette, so generation reuses authentic objects.
"""

import glob, json, collections, sys

sys.path.insert(0, "src")
import ontology as ON, faithful

ROOT = "/mnt/data/workspace/vcmi-mapgen"
maps = [faithful.load(f) for f in glob.glob(f"{ROOT}/out/faithful/*.json")]


def purpose(o):
    return ON.resolve(o["cls"], o["sub"])["purpose"]


# ---- terrain Markov (4-neighbour) for surface(0) and underground(1) ----
def learn4(level):
    full = collections.defaultdict(collections.Counter)
    horiz = collections.defaultdict(collections.Counter)
    vert = collections.defaultdict(collections.Counter)
    marg = collections.Counter()
    for m in maps:
        if level >= len(m["terrain"]):
            continue
        g = [[c["t"] for c in row] for row in m["terrain"][level]]
        H = len(g)
        W = len(g[0])
        for y in range(H):
            for x in range(W):
                marg[g[y][x]] += 1
        for y in range(1, H - 1):
            for x in range(1, W - 1):
                c = g[y][x]
                l = g[y][x - 1]
                u = g[y - 1][x]
                r = g[y][x + 1]
                d = g[y + 1][x]
                full[f"{l},{u},{r},{d}"][c] += 1
                horiz[f"{l},{r}"][c] += 1
                vert[f"{u},{d}"][c] += 1
    return {
        "full": {k: dict(v) for k, v in full.items()},
        "horiz": {k: dict(v) for k, v in horiz.items()},
        "vert": {k: dict(v) for k, v in vert.items()},
        "marg": dict(marg),
    }


# ---- object library + density + budget ----
objlib = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
keymeta = {}
tiles = collections.Counter()
decor_on = collections.Counter()
budget = collections.defaultdict(list)
for m in maps:
    for lvl in m["terrain"]:
        for row in lvl:
            for c in row:
                tiles[c["t"]] += 1
    permap = collections.Counter()
    for o in m["objects"]:
        if not o["type"]:
            continue
        p = purpose(o)
        l = o["l"]
        if l >= len(m["terrain"]):
            continue
        try:
            t = m["terrain"][l][o["y"]][o["x"]]["t"]
        except:
            continue
        key = f"{o['type']}|{o['subtype']}|{o['animation']}"
        objlib[p][t][key] += 1
        keymeta[key] = {
            "type": o["type"],
            "subtype": o["subtype"],
            "animation": o["animation"],
            "mask": o["mask"],
        }
        permap[p] += 1
        if p == "DECORATION":
            decor_on[t] += 1
    for p, c in permap.items():
        budget[p].append(c)

# affinity-threshold the library (drop boundary leakage <15% of a key's terrain share)
lib_out = {}
for p, bt in objlib.items():
    keytot = collections.Counter()
    for t, kc in bt.items():
        for k, c in kc.items():
            keytot[k] += c
    lib_out[p] = {}
    for t, kc in bt.items():
        items = []
        for k, c in kc.items():
            if c / keytot[k] >= 0.15 or kc.most_common(1)[0][0] == k:
                items.append({**keymeta[k], "weight": c})
        if items:
            lib_out[p][str(t)] = sorted(items, key=lambda d: -d["weight"])

density = {int(t): (1000.0 * decor_on[t] / tiles[t] if tiles[t] else 0) for t in tiles}
budget_stats = {p: {"counts": v} for p, v in budget.items()}
viewcnt = collections.defaultdict(collections.Counter)
for m in maps:
    for lvl in m["terrain"]:
        for row in lvl:
            for c in row:
                viewcnt[c["t"]][c["view"]] += 1
terrain_view = {int(t): vc.most_common(1)[0][0] for t, vc in viewcnt.items()}
roadview = collections.Counter()
for m in maps:
    for lvl in m["terrain"]:
        for row in lvl:
            for c in row:
                if c.get("ot"):
                    roadview[c["od"]] += 1
road_dir = roadview.most_common(1)[0][0] if roadview else 0
proportions = {int(t): tiles[t] for t in tiles}
json.dump(
    {
        "markov_surface": learn4(0),
        "markov_under": learn4(1),
        "density": density,
        "budget": budget_stats,
        "proportions": proportions,
        "terrain_view": terrain_view,
        "road_dir": road_dir,
    },
    open(f"{ROOT}/out/factors.json", "w"),
)
json.dump(lib_out, open(f"{ROOT}/out/objlib.json", "w"))
print("factors + objlib written")
print(
    "decoration density/1000 by terrain:",
    {k: round(v) for k, v in sorted(density.items())},
)
print(
    "purpose budget (mean per map):",
    {p: round(sum(v) / len(maps), 1) for p, v in budget.items()},
)
print("objlib purposes:", {p: sum(len(x) for x in t.values()) for p, t in lib_out.items()})
