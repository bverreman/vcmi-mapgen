"""Dependency-graph extractor.

A HoMM3 map is not a field of locally-placed objects -- it is a rooted graph of
gated progression: you start at a town, and access to everything else is gated by
guards (monster stacks), keys (border gate + keymaster), garrisons, quest guards,
and bridged across distance by portals (monoliths / subterranean gates). The
spatial layout exists to enforce that graph.

This module recovers that graph from a real map:
  1. passability grid  (land terrain minus blocking object footprints)
  2. guarded set       (tiles a monster controls)
  3. FREE regions      (connected components you can roam without fighting)
  4. gate edges        (each guard/key/garrison/quest/portal bridging regions)
  5. rooted graph      (BFS from the start town's region -> depth per region)
  6. per-region value  (sum of reward-object weights) and per-gate difficulty

Run as a script to dump one map's graph; import extract_map() for the corpus pass.
"""
import sys, os, glob, collections, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m

# --- object taxonomy --------------------------------------------------------
MONSTER = {54, 71, 72, 73, 74, 75, 162, 163, 164}      # monster + random tiers
PORTAL  = {43, 44, 45, 103, 111}                       # monolith 1way in/out, 2way, subt gate, whirlpool
GATE_KEY      = {212}                                  # border gate (needs keymaster key)
GATE_GARRISON = {33, 219}                              # garrison
GATE_QUEST    = {215}                                  # quest guard

# reward weight by object class (everything unlisted -> 0 = decoration / structural)
VALUE = {
    98: 10, 77: 10,                                    # town / random town
    53: 6, 220: 6,                                     # mine / abandoned mine
    17: 7, 18: 7, 19: 7, 20: 7, 216: 7, 217: 7, 218: 7,  # dwellings (+ random)
    25: 6, 16: 6, 84: 6, 24: 6, 85: 6, 108: 6, 63: 6,  # banks: utopia/bank/crypt/derelict/shipwreck/tomb/pyramid
    36: 8,                                             # grail
    5: 5, 64: 5, 65: 5, 66: 5, 67: 5, 68: 5, 69: 5,    # artifact + random tiers
    6: 5, 93: 4,                                       # pandora, spell scroll
    104: 3, 7: 3, 113: 3, 102: 3, 88: 3, 89: 3, 90: 3, # university/black market/witch/tree/shrines
    81: 3, 39: 3, 105: 3, 41: 3,                       # scholar/lean-to/wagon/marletto-type
    101: 2, 12: 2, 79: 2, 76: 2,                       # treasure chest/campfire/resource
    28: 1, 82: 1, 59: 1, 86: 1,                        # flotsam/sea chest/bottle/survivor
}
GATE_TYPE = {}
for c in MONSTER:       GATE_TYPE[c] = "guard"
for c in PORTAL:        GATE_TYPE[c] = "portal"
for c in GATE_KEY:      GATE_TYPE[c] = "key"
for c in GATE_GARRISON: GATE_TYPE[c] = "garrison"
for c in GATE_QUEST:    GATE_TYPE[c] = "quest"

WATER, ROCK = 8, 9
NB8 = [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]


def blocked_tiles(o, tmpl):
    """Map tiles blocked by an object's footprint. Anchor (x,y) is the
    bottom-right cell (row 5, col 7) of the 6x8 block mask; bit clear = blocked."""
    for r in range(6):
        b = tmpl.block_mask[r]
        for c in range(8):
            if not (b >> (7 - c)) & 1:
                yield o.x - (7 - c), o.y - (5 - r)


def extract_map(m):
    """Recover the dependency graph from a parsed H3Map. Returns a dict."""
    W, H = m.width, m.height
    L = 2 if m.two_level else 1

    # --- passability: True = standable land tile ---------------------------
    passable = [[[False] * W for _ in range(H)] for _ in range(L)]
    for l in range(L):
        for y in range(H):
            for x in range(W):
                t = m.terrain[l][y][x].terrain
                passable[l][y][x] = t != WATER and t != ROCK

    # objects block their footprint (gates included: the gate tile is not free)
    gates = []   # {class,type,x,y,l,strength}
    rewards = [] # {class,x,y,l,value}
    starts = []  # (l,x,y) of player-owned towns / heroes
    for o in m.objects:
        tmpl = m.templates[o.template_index]
        for (bx, by) in blocked_tiles(o, tmpl):
            if 0 <= bx < W and 0 <= by < H:
                passable[o.l][by][bx] = False
        cls = o.obj_class
        if cls in GATE_TYPE:
            strength = o.extra.get("count", 0) if cls in MONSTER else 0
            gates.append({"cls": cls, "type": GATE_TYPE[cls], "x": o.x, "y": o.y,
                          "l": o.l, "sub": o.obj_subclass, "strength": strength})
        v = VALUE.get(cls, 0)
        if v:
            rewards.append({"cls": cls, "x": o.x, "y": o.y, "l": o.l, "value": v})
        if cls in (98, 77, 34) and o.extra.get("owner", 255) != 255:
            starts.append((o.l, o.x, o.y))

    # --- guarded set: tiles within Chebyshev 1 of a monster ----------------
    guarded = [[[False] * W for _ in range(H)] for _ in range(L)]
    for g in gates:
        if g["type"] != "guard":
            continue
        for dx, dy in NB8 + [(0, 0)]:
            x, y, l = g["x"] + dx, g["y"] + dy, g["l"]
            if 0 <= x < W and 0 <= y < H and passable[l][y][x]:
                guarded[l][y][x] = True

    # --- FREE regions: passable & not guarded, 8-connected -----------------
    label = [[[-1] * W for _ in range(H)] for _ in range(L)]
    region_tiles = []
    nid = 0
    for l in range(L):
        for y in range(H):
            for x in range(W):
                if not passable[l][y][x] or guarded[l][y][x] or label[l][y][x] >= 0:
                    continue
                stack = [(x, y)]; label[l][y][x] = nid; size = 0
                while stack:
                    cx, cy = stack.pop(); size += 1
                    for dx, dy in NB8:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and passable[l][ny][nx] \
                           and not guarded[l][ny][nx] and label[l][ny][nx] < 0:
                            label[l][ny][nx] = nid; stack.append((nx, ny))
                region_tiles.append(size); nid += 1
    NR = nid

    def regions_near(x, y, l, R=2):
        """Distinct FREE region labels within Chebyshev R of a point."""
        s = set()
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and label[l][ny][nx] >= 0:
                    s.add(label[l][ny][nx])
        return s

    # --- assign rewards & determine each gate's connected regions ----------
    region_value = [0] * NR
    region_reward_n = [0] * NR
    for rw in rewards:
        rs = regions_near(rw["x"], rw["y"], rw["l"], R=2)
        if rs:
            rid = min(rs, key=lambda r: -region_tiles[r])  # the largest adjacent region owns it
            region_value[rid] += rw["value"]; region_reward_n[rid] += 1

    edges = []  # {a,b,type,strength,cls}
    for g in gates:
        rs = sorted(regions_near(g["x"], g["y"], g["l"], R=2))
        g["regions"] = rs
        if len(rs) >= 2:
            # connect consecutive pairs (a gate sitting between 2+ regions joins them)
            for i in range(len(rs) - 1):
                edges.append({"a": rs[i], "b": rs[i + 1], "type": g["type"],
                              "strength": g["strength"], "cls": g["cls"]})

    # portals join the regions of same-subtype endpoints across distance
    portals = collections.defaultdict(list)
    for g in gates:
        if g["type"] == "portal":
            portals[(g["cls"], g["sub"])].append(g)
    for key, ps in portals.items():
        reg = []
        for g in ps:
            rs = list(regions_near(g["x"], g["y"], g["l"], R=2))
            if rs: reg.append(rs[0])
        for i in range(len(reg) - 1):
            if reg[i] != reg[i + 1]:
                edges.append({"a": reg[i], "b": reg[i + 1], "type": "portal",
                              "strength": 0, "cls": key[0]})

    # --- root at start regions, BFS depth over the region graph ------------
    adj = collections.defaultdict(list)
    for e in edges:
        adj[e["a"]].append((e["b"], e))
        adj[e["b"]].append((e["a"], e))
    start_regions = set()
    for (l, x, y) in starts:
        start_regions |= regions_near(x, y, l, R=2)
    if not start_regions and NR:
        # fall back: largest region is the root
        start_regions = {max(range(NR), key=lambda r: region_tiles[r])}

    depth = {r: 0 for r in start_regions}
    parent_gate = {}                                   # region -> gate edge crossed to reach it
    q = collections.deque(start_regions)
    while q:
        r = q.popleft()
        for nb, e in adj[r]:
            if nb not in depth:
                depth[nb] = depth[r] + 1; parent_gate[nb] = e; q.append(nb)

    # branching: children per region in the BFS tree
    children = collections.Counter()
    for r, e in parent_gate.items():
        par = e["a"] if depth.get(e["a"], 1e9) < depth.get(e["b"], 1e9) else e["b"]
        children[par] += 1

    return {
        "name": m.name, "W": W, "H": H, "levels": L,
        "n_regions": NR, "n_reachable": len(depth),
        "region_tiles": region_tiles, "region_value": region_value,
        "region_reward_n": region_reward_n,
        "edges": edges, "depth": depth, "parent_gate": parent_gate,
        "children": dict(children), "start_regions": sorted(start_regions),
        "n_gates": len(gates),
        "gate_types": collections.Counter(g["type"] for g in gates),
        "label": label, "gates": gates, "rewards": rewards, "starts": starts,
    }


def summarize(d):
    bydepth = collections.defaultdict(lambda: {"val": [], "diff": [], "n": 0})
    for r, dep in d["depth"].items():
        bydepth[dep]["val"].append(d["region_value"][r]); bydepth[dep]["n"] += 1
    for r, e in d["parent_gate"].items():
        bydepth[d["depth"][r]]["diff"].append(e["strength"])
    print(f"{d['name']:<28} {d['W']}x{d['H']}x{d['levels']}  "
          f"regions={d['n_regions']} reachable={d['n_reachable']}  gates={d['n_gates']} {dict(d['gate_types'])}")
    maxd = max(d["depth"].values()) if d["depth"] else 0
    print(f"  max depth={maxd}  branching(avg children)="
          f"{(sum(d['children'].values())/max(1,len(d['children']))):.2f}")
    for dep in range(maxd + 1):
        b = bydepth[dep]
        mv = sum(b["val"]) / max(1, len(b["val"]))
        md = sum(b["diff"]) / max(1, len(b["diff"])) if b["diff"] else 0
        print(f"    depth {dep}: regions={b['n']:3d}  mean_value={mv:5.1f}  mean_guard={md:6.0f}")


def aggregate(paths):
    """Corpus-wide distributions the generator samples to build a tree."""
    import json
    per_map = []        # one record per map: shape summary
    edge_rows = []      # one row per TREE edge: (depth,type,strength,sub,child_value,child_tiles)
    branch_by_depth = collections.defaultdict(list)
    regions_per_karea = []
    depth_hist = collections.Counter()
    gate_mix = collections.Counter()
    commons_tiles = []; pocket_tiles = []

    for p in paths:
        try:
            d = extract_map(h3m.parse_file(p))
        except Exception as e:
            print("FAIL", os.path.basename(p), repr(e)[:60]); continue
        area = d["W"] * d["H"] * d["levels"]
        if not d["depth"]:
            continue
        maxd = max(d["depth"].values())
        regions_per_karea.append(1000.0 * d["n_reachable"] / area)
        # tree edges: child region keyed by parent_gate
        for r, e in d["parent_gate"].items():
            dep = d["depth"][r]
            depth_hist[dep] += 1
            gate_mix[e["type"]] += 1
            edge_rows.append({"depth": dep, "type": e["type"], "strength": e["strength"],
                              "cls": e["cls"], "child_value": d["region_value"][r],
                              "child_tiles": d["region_tiles"][r]})
        for par, n in d["children"].items():
            branch_by_depth[d["depth"].get(par, 0)].append(n)
        # commons vs pocket by tile size (commons = depth-0 / largest)
        for r in range(d["n_regions"]):
            if r not in d["depth"]:
                continue
            (commons_tiles if d["depth"][r] == 0 else pocket_tiles).append(d["region_tiles"][r])
        per_map.append({"name": d["name"], "area": area, "max_depth": maxd,
                        "n_reachable": d["n_reachable"], "n_gates": d["n_gates"],
                        "gate_types": dict(d["gate_types"])})

    def pct(xs, ps=(10, 50, 90)):
        if not xs: return {}
        s = sorted(xs); n = len(s)
        return {f"p{p}": s[min(n - 1, int(p / 100 * n))] for p in ps}

    # value/difficulty gradient: per depth, mean child value + strength distribution
    grad = {}
    bd = collections.defaultdict(lambda: {"val": [], "str": [], "n": 0})
    for e in edge_rows:
        g = bd[e["depth"]]; g["val"].append(e["child_value"]); g["n"] += 1
        if e["strength"]: g["str"].append(e["strength"])
    for dep, g in sorted(bd.items()):
        grad[dep] = {"n_edges": g["n"],
                     "mean_child_value": round(sum(g["val"]) / max(1, len(g["val"])), 1),
                     "guard_strength": pct(g["str"]) if g["str"] else {},
                     "frac_known_strength": round(len(g["str"]) / max(1, g["n"]), 2)}

    tot = sum(gate_mix.values()) or 1
    agg = {
        "n_maps": len(per_map),
        "regions_per_1000tiles": pct(regions_per_karea),
        "max_depth": pct([m["max_depth"] for m in per_map]),
        "gate_type_mix": {k: round(v / tot, 3) for k, v in gate_mix.most_common()},
        "branching_by_depth": {d: round(sum(v) / len(v), 2) for d, v in sorted(branch_by_depth.items()) if v},
        "branching_overall": pct([n for v in branch_by_depth.values() for n in v]),
        "commons_tiles": pct(commons_tiles), "pocket_tiles": pct(pocket_tiles),
        "gradient_by_depth": grad,
        "per_map": per_map,
    }
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "deps.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(agg, open(out, "w"), indent=1)
    return agg, out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--corpus":
        paths = sorted(glob.glob('/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m', recursive=True))
        agg, out = aggregate(paths)
        import json
        view = {k: v for k, v in agg.items() if k != "per_map"}
        print(json.dumps(view, indent=1))
        print(f"\n{agg['n_maps']} maps -> {out}")
    else:
        if len(sys.argv) > 1:
            paths = [sys.argv[1]]
        else:
            paths = sorted(glob.glob('/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m', recursive=True))[:6]
        for p in paths:
            summarize(extract_map(h3m.parse_file(p)))
