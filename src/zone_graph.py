"""Zone-graph EXTRACTOR -- the representation that makes meaningful map features
first-class and learnable (the chosen rethink direction).

A HoMM3 map is not a per-tile terrain texture; it is a GRAPH OF ZONES:
  node = one town-anchored zone, attributes: faction, area, per-purpose object
         budget, mine/resource economy.
  edge = a passage between two adjacent zones, attribute: guard presence.

The terrain-only CNN cannot represent any of this (its input is local terrain, its
output per-tile density, trained on rotated crops) so it provably cannot learn town
count, per-player symmetry, the per-zone economic recipe, guard gradients, or
connectivity. This module extracts that graph from every real map and prints the
corpus distributions, so we can SEE whether the meaningful features are cleanly
learnable before building the generator on top of them.

Run:  uv run python src/zone_graph.py            # corpus distributions
      uv run python src/zone_graph.py --render "All for One"   # one graph, drawn
"""

import sys, os, json, glob, math, collections, argparse
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON
import zone_structure as ZS
import render

ROOT = "/mnt/data/workspace/vcmi-mapgen"
WATER, ROCK = 8, 9
TILE = 9
BOUNDARY_GUARD_DIST = 3          # a guard within this many tiles of a zone boundary = passage guard


def load_map(name):
    return json.load(open(f"{ROOT}/out/maps/{name}.json"))


def surface_objs(m):
    """Resolved surface (level 0) objects: (x, y, purpose, name, subtype)."""
    out = []
    for o in m["objects"]:
        if o.get("l", 0) != 0:
            continue
        r = ON.resolve(o["class"], o["subclass"])
        out.append((o["x"], o["y"], r["purpose"], r["name"], r["subtype"]))
    return out


def extract(name):
    """Return the zone graph for one map (surface level)."""
    m = load_map(name)
    terr = [[c["t"] for c in row] for row in m["terrain"][0]]
    W, H = m["width"], m["height"]
    objs = surface_objs(m)

    towns = [(x, y, sub) for (x, y, p, nm, sub) in objs if p == "TOWN"]
    town_xy = [(x, y) for (x, y, _) in towns]
    label, passable = ZS.zones_by_town(terr, town_xy, W, H)
    passable_cnt = sum(1 for row in passable for v in row if v)

    nz = len(towns)
    zones = []
    for zi, (tx, ty, fac) in enumerate(towns):
        area = sum(1 for y in range(H) for x in range(W) if label[y][x] == zi)
        zones.append({
            "id": zi, "town": (tx, ty), "faction": fac, "area": area,
            "budget": collections.Counter(), "mines": collections.Counter(),
        })

    def zone_at(x, y):
        return label[y][x] if 0 <= x < W and 0 <= y < H else -1

    # assign each gameplay object to its zone; record per-zone budget + mine economy
    out_of_zone = 0
    guards = []
    for (x, y, p, nm, sub) in objs:
        if p in ("DECORATION", "TOWN"):
            continue
        z = zone_at(x, y)
        if p == "GUARD":
            guards.append((x, y, z))
        if z < 0:
            out_of_zone += 1
            continue
        zones[z]["budget"][p] += 1
        if p == "MINE":
            zones[z]["mines"][sub] += 1

    # connection graph: zones sharing a 4-adjacent passable boundary, + boundary length
    boundary = collections.Counter()       # frozenset({i,j}) -> shared-edge length
    boundary_tiles = collections.defaultdict(list)
    for y in range(H):
        for x in range(W):
            zi = label[y][x]
            if zi < 0:
                continue
            for dx, dy in ZS.NB4:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H:
                    zj = label[ny][nx]
                    if zj >= 0 and zj != zi:
                        key = frozenset((zi, zj))
                        boundary[key] += 1
                        boundary_tiles[key].append((x, y))

    # guard near a boundary => a passage guard (vs interior treasure guard)
    bset = set()
    for tiles in boundary_tiles.values():
        bset.update(tiles)
    boundary_guards = 0
    for (gx, gy, gz) in guards:
        near = any(abs(gx - bx) + abs(gy - by) <= BOUNDARY_GUARD_DIST for (bx, by) in bset) if bset else False
        if near:
            boundary_guards += 1

    edges = [{"zones": sorted(k), "border": v,
              "guards": sum(1 for (gx, gy, gz) in guards
                            if any(abs(gx-bx)+abs(gy-by) <= BOUNDARY_GUARD_DIST
                                   for (bx, by) in boundary_tiles[k]))}
             for k, v in boundary.items()]

    return {
        "name": name, "W": W, "H": H, "passable": passable_cnt,
        "n_towns": nz, "zones": zones, "edges": edges,
        "out_of_zone": out_of_zone, "n_guards": len(guards),
        "boundary_guards": boundary_guards,
    }, label, terr


# ----------------------------------------------------------------------------- corpus
def corpus():
    names = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(f"{ROOT}/out/maps/*.json"))
    graphs = []
    for name in names:
        try:
            g, _, _ = extract(name)
        except Exception as e:
            print(f"  skip {name}: {e}")
            continue
        if g["n_towns"] >= 1:
            graphs.append(g)
    return graphs


def _fit_loglog(xs, ys):
    """towns ~ a * area^b  -> fit b, a in log space."""
    lx = [math.log(x) for x in xs]; ly = [math.log(y) for y in ys]
    n = len(lx); mx = sum(lx)/n; my = sum(ly)/n
    sxx = sum((v-mx)**2 for v in lx); sxy = sum((lx[i]-mx)*(ly[i]-my) for i in range(n))
    b = sxy/sxx if sxx else 0; a = math.exp(my - b*mx)
    return a, b


def report(graphs):
    multi = [g for g in graphs if g["n_towns"] >= 2]
    print(f"corpus: {len(graphs)} maps with >=1 town; {len(multi)} with >=2 (multiplayer-ish)\n")

    # 1) town count vs passable area -- the learned N(area) that replaces the flat rate
    xs = [g["passable"] for g in graphs if g["passable"] > 0]
    ys = [g["n_towns"] for g in graphs if g["passable"] > 0]
    a, b = _fit_loglog(xs, ys)
    print(f"[town count]   towns ~= {a:.3g} * passable_area^{b:.2f}")
    for lo, hi in [(0, 1500), (1500, 3000), (3000, 5000), (5000, 99999)]:
        band = [g["n_towns"] for g in graphs if lo <= g["passable"] < hi]
        if band:
            print(f"    area {lo:5d}-{hi:<5d}: {len(band):3d} maps, towns "
                  f"mean={sum(band)/len(band):.1f} range {min(band)}-{max(band)}")

    # 2) per-zone recipe -- is the purpose MIX conserved across zones? (symmetry signal)
    print("\n[per-zone recipe]  mean fraction of a zone's gameplay budget, by purpose")
    frac = collections.defaultdict(list)
    zone_totals = []
    for g in graphs:
        for z in g["zones"]:
            tot = sum(z["budget"].values())
            if tot < 3:
                continue
            zone_totals.append(tot)
            for p in ON.PURPOSE.values():
                frac[p].append(z["budget"].get(p, 0)/tot)
    order = sorted(frac, key=lambda p: -sum(frac[p])/len(frac[p]))
    for p in order:
        vals = frac[p]
        mean = sum(vals)/len(vals)
        if mean < 0.01:
            continue
        sd = (sum((v-mean)**2 for v in vals)/len(vals))**0.5
        print(f"    {p:16s} mean={mean:.2f}  sd={sd:.2f}  (cv={sd/mean:.2f})")
    if zone_totals:
        zt = sorted(zone_totals)
        print(f"    zone budget size: median={zt[len(zt)//2]}  range {zt[0]}-{zt[-1]}")

    # 3) economy: mines per zone & resource diversity
    mines_per_zone = [sum(z["mines"].values()) for g in graphs for z in g["zones"]]
    res_div = [len(z["mines"]) for g in graphs for z in g["zones"] if z["mines"]]
    if mines_per_zone:
        print(f"\n[economy]   mines/zone mean={sum(mines_per_zone)/len(mines_per_zone):.1f}; "
              f"distinct resources/zone mean={sum(res_div)/len(res_div):.1f}" if res_div else "")

    # 4) connectivity + guarded passages
    degs, ng, gboundary = [], 0, 0
    for g in multi:
        deg = collections.Counter()
        for e in g["edges"]:
            deg[e["zones"][0]] += 1; deg[e["zones"][1]] += 1
        degs += list(deg.values())
        ng += g["n_guards"]; gboundary += g["boundary_guards"]
    if degs:
        print(f"\n[connectivity]  zone degree mean={sum(degs)/len(degs):.1f} "
              f"(range {min(degs)}-{max(degs)})")
    if ng:
        print(f"[guards]   {gboundary}/{ng} guards sit within {BOUNDARY_GUARD_DIST} tiles of a "
              f"zone boundary ({100*gboundary/ng:.0f}% guard PASSAGES, rest guard interior loot)")

    # 5) reachability of the structure assumption
    inz = sum(sum(z["budget"].values()) for g in graphs for z in g["zones"])
    oz = sum(g["out_of_zone"] for g in graphs)
    print(f"\n[assumption]  {inz}/{inz+oz} gameplay objects fall inside a town-zone "
          f"({100*inz/(inz+oz):.0f}%)")


def learn_params(graphs=None):
    """Distill the corpus into the scalars/vectors the realizer samples from.
    Returns a dict: town-count power law, per-zone recipe (purpose->mean frac),
    budget/area slope, mines & resource-diversity per zone, passage-guard fraction."""
    graphs = graphs or corpus()
    xs = [g["passable"] for g in graphs if g["passable"] > 0]
    ys = [g["n_towns"] for g in graphs if g["passable"] > 0]
    a, b = _fit_loglog(xs, ys)

    frac = collections.defaultdict(list)
    ba = []                                   # (budget, area) per zone, for the slope
    mines, resdiv = [], []
    for g in graphs:
        for z in g["zones"]:
            tot = sum(z["budget"].values())
            if tot >= 3 and z["area"] > 0:
                ba.append((tot, z["area"]))
                for p in set(ON.PURPOSE.values()):
                    frac[p].append(z["budget"].get(p, 0) / tot)
            mines.append(sum(z["mines"].values()))
            if z["mines"]:
                resdiv.append(len(z["mines"]))
    recipe = {p: sum(v) / len(v) for p, v in frac.items() if sum(v) / len(v) >= 0.005}
    s = sum(recipe.values())
    recipe = {p: v / s for p, v in recipe.items()}            # renormalize to 1
    slope = sum(t for t, ar in ba) / sum(ar for t, ar in ba)  # budget per passable tile (pooled mean)
    # per-MAP gameplay density (objects / zone-area): a map is uniformly rich or sparse,
    # so we sample ONE density per generation from this empirical spread (preserves the
    # cross-zone symmetry AND lets a seed land on the dense end like All for One) instead
    # of always using the corpus mean (which under-counts dense maps).
    densities = []
    for g in graphs:
        tot = sum(sum(z["budget"].values()) for z in g["zones"])
        ar = sum(z["area"] for z in g["zones"])
        if ar > 0 and tot > 0:
            densities.append(tot / ar)
    # decoration density per passable tile (not per zone-area; decorations fill the whole map)
    deco_densities = []
    for g in graphs:
        m = json.load(open(f"{ROOT}/out/maps/{g['name']}.json"))
        pass_cnt = sum(1 for row in m["terrain"][0] for c in row if c["t"] not in (8, 9))
        deco_cnt = sum(1 for o in m["objects"] if o.get("l", 0) == 0
                       and ON.resolve(o["class"], o["subclass"]).get("purpose") == "DECORATION")
        if pass_cnt > 0 and deco_cnt > 0:
            deco_densities.append(deco_cnt / pass_cnt)
    ng = sum(g["n_guards"] for g in graphs)
    gb = sum(g["boundary_guards"] for g in graphs)
    return {
        "town_a": a, "town_b": b, "recipe": recipe, "budget_per_tile": slope,
        "densities": sorted(densities),
        "deco_densities": sorted(deco_densities),
        "mines_per_zone": sum(mines) / len(mines),
        "resources_per_zone": sum(resdiv) / len(resdiv) if resdiv else 0,
        "passage_guard_frac": gb / ng if ng else 0,
    }


def render_graph(name):
    g, label, terr = extract(name)
    W, H = g["W"], g["H"]
    img = Image.new("RGB", (W*TILE, H*TILE), (10, 10, 10))
    px = img.load()
    for y in range(H):
        for x in range(W):
            base = render.TERRAIN_RGB.get(terr[y][x], (0, 0, 0))
            z = label[y][x]
            col = (tuple((b+t)//2 for b, t in zip(base, ZS.ZONE_TINT[z % len(ZS.ZONE_TINT)]))
                   if z >= 0 else base)
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x*TILE+dx, y*TILE+dy] = col
    d = ImageDraw.Draw(img)
    cen = {z["id"]: (z["town"][0]*TILE+TILE//2, z["town"][1]*TILE+TILE//2) for z in g["zones"]}
    for e in g["edges"]:                      # draw edges, thickness ~ border, red if guarded
        i, j = e["zones"]
        w = 1 + min(4, e["border"]//8)
        col = (230, 90, 90) if e["guards"] else (160, 160, 160)
        d.line([cen[i], cen[j]], fill=col, width=w)
    for z in g["zones"]:                       # node: town, sized by budget, labeled faction
        cx, cy = cen[z["id"]]
        tot = sum(z["budget"].values())
        r = 5 + tot//6
        d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(255, 255, 255), outline=(0, 0, 0), width=2)
        d.text((cx-3, cy-4), str(tot), fill=(0, 0, 0))
    out = f"{ROOT}/out/render/zone_graph.png"
    img.save(out)
    print(f"{name}: {g['n_towns']} zones, {len(g['edges'])} connections, "
          f"{g['boundary_guards']}/{g['n_guards']} passage guards")
    for z in g["zones"]:
        print(f"  zone {z['id']} [{z['faction']:10s}] area={z['area']:4d} budget={sum(z['budget'].values()):3d}  "
              + " ".join(f"{k}={v}" for k, v in z["budget"].most_common(5)))
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", default=None, help="render one map's zone graph")
    args = ap.parse_args()
    if args.render:
        render_graph(args.render)
    else:
        report(corpus())


if __name__ == "__main__":
    main()
