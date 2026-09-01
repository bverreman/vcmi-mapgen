"""Extract a corpus zone's SKELETON: the open space carved into rooms (gameplay-seeded geodesic
territories) joined by necks (chokepoints), with each room tagged by its Part C archetype.

This is the read-only extraction we argued for: it does NOT learn marginals -- it keeps the joint
configuration (room shapes + relative layout + neck geometry) of a real zone intact. Run it to see
whether real zones actually carve into sensible hub+pocket structure.

    uv run python -m vcmi_mapgen.research.zone_skeleton --map "All for One" --zone 11      # render one zone
    uv run python -m vcmi_mapgen.research.zone_skeleton --map "All for One"                 # all land zones
    uv run python -m vcmi_mapgen.research.zone_skeleton --verdict 30                        # does-it-exist stats
"""
import argparse
import collections
import os

from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.kit.terrain_lookup import TNAME
from vcmi_mapgen.kit.segmentation import _segment_level

WATER, ROCK = 8, 9
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

# anchor purpose -> room role
ROLE = {"TOWN": "hub", "MINE": "mine_pocket", "DWELLING": "dwelling_pocket",
        "BANK": "treasure_pocket", "REWARD_PICKUP": "artifact_pocket",
        "RESOURCE_PILE": "resource_pocket", "STAT_PERMANENT": "stat_pocket",
        "SPELL_SKILL": "skill_pocket", "BONUS_TEMP": "bonus_pocket", "MANA": "mana_pocket"}


def _zone_objects(fm, zid, zl, ts, level=0):
    """Gameplay + guard objects anchored in the zone (with _purpose set)."""
    out = []
    W, H = fm["width"], fm["height"]
    for o in fm["objects"]:
        if o.get("l", 0) != level:
            continue
        x, y = o["x"], o["y"]
        if not (0 <= x < W and 0 <= y < H) or (x, y) not in ts:
            continue
        o = dict(o, _purpose=OR.purpose_of(o))
        out.append(o)
    return out


def open_set(fm, ts, level=0):
    """Navigable tiles of the zone: in the zone, not under any object's B/X blocking cell."""
    blocked = set()
    for o in fm["objects"]:
        if o.get("l", 0) != level:
            continue
        for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk:
                blocked.add((cx, cy))
    return {t for t in ts if t not in blocked}


def distance_transform(openset):
    """Chebyshev clearance: dt[t] = #steps (8-conn) from t to the nearest NON-open tile.
    1 = against a wall / in a 1-wide neck; higher = deeper in a clearing."""
    dt = {}
    q = collections.deque()
    for (x, y) in openset:                       # seed from open tiles touching a non-open neighbour
        if any((x + dx, y + dy) not in openset for dx, dy in NB8):
            dt[(x, y)] = 1
            q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx, dy in NB8:
            n = (x + dx, y + dy)
            if n in openset and n not in dt:
                dt[n] = dt[(x, y)] + 1
                q.append(n)
    return dt


def _seed_tile(o, openset):
    """Where a cluster's anchor seeds its room: its 'A' visit tile if open, else nearest open tile."""
    for t in ZE._mask_anchor_cells(o["mask"], o["x"], o["y"]):
        if t in openset:
            return t
    best, bd = None, 1e9
    ax, ay = o["x"], o["y"]
    for (x, y) in openset:
        d = abs(x - ax) + abs(y - ay)
        if d < bd:
            best, bd = (x, y), d
    return best


def _components(openset):
    seen = set()
    comps = []
    for s in openset:
        if s in seen:
            continue
        comp = []
        q = collections.deque([s]); seen.add(s)
        while q:
            x, y = q.popleft(); comp.append((x, y))
            for dx, dy in NB4:
                n = (x + dx, y + dy)
                if n in openset and n not in seen:
                    seen.add(n); q.append(n)
        comps.append(comp)
    return comps


def skeleton(fm, zid, zones, zl):
    z = zones[zid]
    ts = set(z["tiles_set"])
    terr = TNAME.get(z["terrain_type"])
    if z["terrain_type"] in (WATER, ROCK):
        return None
    O = open_set(fm, ts)
    if not O:
        return None
    dt = distance_transform(O)

    objs = _zone_objects(fm, zid, zl, ts)
    gameplay = [o for o in objs if o["_purpose"] not in ("DECORATION", "TERRAIN_MODIFIER", "UNKNOWN")]
    guards = [o for o in objs if o["_purpose"] == "GUARD"]

    # --- room nuclei: Part C gameplay clusters with an eligible anchor, + a hub per open component
    seeds = []   # (tile, role, anchor_purpose, cluster)
    for cl in ZE._cluster_objects(gameplay):
        a = ZE._group_anchor(cl)
        if a is None:
            continue
        st = _seed_tile(a, O)
        if st is None:
            continue
        seeds.append((st, ROLE.get(a["_purpose"], "pocket"), a["_purpose"], cl))
    seeded_comp = set()
    for st, *_ in seeds:
        pass
    # ensure each open component has at least one seed (a hub of pure travel space)
    by_tile_comp = {}
    comps = _components(O)
    for ci, comp in enumerate(comps):
        for t in comp:
            by_tile_comp[t] = ci
    have = {by_tile_comp[st] for st, *_ in seeds if st in by_tile_comp}
    for ci, comp in enumerate(comps):
        if ci not in have:
            hub = max(comp, key=lambda t: dt.get(t, 0))
            seeds.append((hub, "hub", None, []))
    # if no town hub anywhere, promote the deepest seedless... already handled; mark a hub role
    if not any(r == "hub" for _, r, _, _ in seeds):
        # promote the most-central seed's COMPONENT to also carry a hub of travel space
        big = max(comps, key=len)
        hub = max(big, key=lambda t: dt.get(t, 0))
        seeds.append((hub, "hub", None, []))

    # --- geodesic partition: each open tile -> nearest seed through open space (4-conn)
    label = {}
    q = collections.deque()
    for i, (st, *_rest) in enumerate(seeds):
        if st in O and st not in label:
            label[st] = i; q.append(st)
    while q:
        x, y = q.popleft()
        for dx, dy in NB4:
            n = (x + dx, y + dy)
            if n in O and n not in label:
                label[n] = label[(x, y)]; q.append(n)

    rooms = []
    for i, (st, role, ap, cl) in enumerate(seeds):
        tiles = [t for t, l in label.items() if l == i]
        rooms.append({"id": i, "role": role, "anchor": ap, "seed": st,
                      "area": len(tiles), "tiles": tiles,
                      "members": [o["_purpose"] for o in cl]})

    # --- necks: widest passage on the boundary between two adjacent territories
    boundary = collections.defaultdict(list)     # (a,b) -> [neck-candidate tiles]
    for t, l in label.items():
        x, y = t
        for dx, dy in NB4:
            n = (x + dx, y + dy)
            if n in label and label[n] != l:
                a, b = sorted((l, label[n]))
                boundary[(a, b)].append(t)
    edges = []
    for (a, b), tiles in boundary.items():
        neck = max(tiles, key=lambda t: dt.get(t, 0))
        width = dt.get(neck, 1)
        gated = any(abs(g["x"] - neck[0]) + abs(g["y"] - neck[1]) <= 2 for g in guards)
        edges.append({"a": a, "b": b, "neck": neck, "width": width, "gated": gated})

    return {"map": fm["name"], "zone": zid, "terrain": terr, "zone_area": len(ts),
            "open_area": len(O), "rooms": rooms, "edges": edges, "dt": dt, "open": O,
            "guards": [(g["x"], g["y"]) for g in guards]}


def thin(openset, x0, y0, W, H):
    """Zhang-Suen thinning -> the medial axis (1-px skeleton) of the open web. Returns a set of
    skeleton tiles in absolute coords."""
    import numpy as np
    g = np.zeros((H + 2, W + 2), np.uint8)
    for (x, y) in openset:
        g[y - y0 + 1, x - x0 + 1] = 1
    NB = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]  # P2..P9 clockwise
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            ys, xs = np.where(g == 1)
            todel = []
            for yy, xx in zip(ys, xs):
                p = [g[yy + dy, xx + dx] for dy, dx in NB]
                B = sum(p)
                if B < 2 or B > 6:
                    continue
                seq = p + [p[0]]
                A = sum(1 for i in range(8) if seq[i] == 0 and seq[i + 1] == 1)
                if A != 1:
                    continue
                if step == 0:
                    ok = p[0] * p[2] * p[4] == 0 and p[2] * p[4] * p[6] == 0
                else:
                    ok = p[0] * p[2] * p[6] == 0 and p[0] * p[4] * p[6] == 0
                if ok:
                    todel.append((yy, xx))
            if todel:
                changed = True
                for yy, xx in todel:
                    g[yy, xx] = 0
    out = set()
    ys, xs = np.where(g == 1)
    for yy, xx in zip(ys, xs):
        out.add((xx + x0 - 1, yy + y0 - 1))
    return out


def web_graph(skel):
    """degree of each skeleton tile (8-conn). endpoints = deg 1 (dead-end spurs), junctions = deg>=3."""
    deg = {}
    for (x, y) in skel:
        deg[(x, y)] = sum(1 for dx, dy in NB8 if (x + dx, y + dy) in skel)
    endpoints = [t for t, d in deg.items() if d == 1]
    junctions = [t for t, d in deg.items() if d >= 3]
    return deg, endpoints, junctions


# ---------------------------------------------------------------------------- render
_PAL = [(70, 130, 220), (220, 120, 60), (90, 190, 90), (200, 80, 160), (210, 200, 70),
        (120, 200, 200), (180, 110, 200), (160, 160, 100), (230, 150, 150), (110, 160, 230)]


def render(sk, out, scale=7):
    from PIL import Image, ImageDraw
    O = sk["open"]
    xs = [x for x, _ in O]; ys = [y for _, y in O]
    # include the whole zone footprint for context
    allt = set(O)
    for r in sk["rooms"]:
        allt |= set(r["tiles"])
    x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
    W = (x1 - x0 + 3) * scale; H = (y1 - y0 + 3) * scale
    img = Image.new("RGB", (W, H), (28, 28, 30))
    d = ImageDraw.Draw(img)
    lab = {}
    for r in sk["rooms"]:
        for t in r["tiles"]:
            lab[t] = r["id"]

    def px(t):
        return ((t[0] - x0 + 1) * scale, (t[1] - y0 + 1) * scale)

    for t in O:                                  # open tiles colored by room
        rx, ry = px(t)
        col = _PAL[lab.get(t, 0) % len(_PAL)]
        d.rectangle((rx, ry, rx + scale - 1, ry + scale - 1), fill=col)
    for e in sk["edges"]:                         # necks
        rx, ry = px(e["neck"])
        c = (235, 40, 40) if e["gated"] else (255, 255, 255)
        d.rectangle((rx - 1, ry - 1, rx + scale, ry + scale), outline=c, width=2)
    for r in sk["rooms"]:                          # seeds + role label
        rx, ry = px(r["seed"])
        d.ellipse((rx, ry, rx + scale - 1, ry + scale - 1), fill=(15, 15, 15))
        d.text((rx + scale, ry - 2), r["role"].split("_")[0], fill=(255, 255, 255))
    for g in sk["guards"]:                          # guards
        rx, ry = px(g) if g in O else px(min(O, key=lambda t: abs(t[0]-g[0])+abs(t[1]-g[1])))
        d.text(px(g)[0:2] if g in lab else (rx, ry), "G", fill=(255, 80, 80))
    img.save(out)
    return out


def _summ(sk):
    pockets = [r for r in sk["rooms"] if r["role"] != "hub" and r["area"] > 0]
    chokes = [e for e in sk["edges"] if e["width"] <= 2]
    gated = [e for e in sk["edges"] if e["gated"]]
    return (f'{sk["map"][:22]:22s} z{sk["zone"]:<3d} {sk["terrain"]:6s} '
            f'area={sk["zone_area"]:4d} open={sk["open_area"]:4d} '
            f'rooms={len(sk["rooms"]):2d} pockets={len(pockets):2d} '
            f'edges={len(sk["edges"]):2d} chokepts={len(chokes):2d} gated={len(gated):2d}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=None)
    ap.add_argument("--verdict", type=int, default=0, help="run over N maps, print structure stats")
    args = ap.parse_args()
    rdir = os.path.join(ZE.ROOT, "out", "render", "skeleton")
    os.makedirs(rdir, exist_ok=True)

    if args.verdict:
        import glob
        maps = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(ZE.ROOT, "maps_json", "*.json")))[:args.verdict]
        agg = collections.Counter()
        nrooms = []; npockets = []; widths = []; ngz = 0; nz = 0
        for mp in maps:
            try:
                fm = OR.load_faithful(mp)
            except Exception:
                continue
            lvl = fm["terrain"][0]
            zones, zl, canon = _segment_level(lvl)
            for zid in zones:
                if zones[zid]["terrain_type"] in (WATER, ROCK) or zones[zid]["area"] < 30:
                    continue
                sk = skeleton(fm, zid, zones, zl)
                if not sk:
                    continue
                nz += 1
                pk = [r for r in sk["rooms"] if r["role"] != "hub" and r["area"] > 0]
                nrooms.append(len(sk["rooms"])); npockets.append(len(pk))
                widths += [e["width"] for e in sk["edges"]]
                if pk:
                    ngz += 1
                agg[len(pk)] += 1
        import statistics as st
        print(f"\nzones={nz}  with>=1 pocket={ngz} ({100*ngz/max(nz,1):.0f}%)")
        print(f"rooms/zone  mean={st.fmean(nrooms):.2f}  pockets/zone mean={st.fmean(npockets):.2f}")
        if widths:
            choke = sum(1 for w in widths if w <= 2)
            print(f"neck widths n={len(widths)}  <=2 (chokepoint)={100*choke/len(widths):.0f}%  "
                  f"median={st.median(widths)}")
        print("pocket-count histogram:", dict(sorted(agg.items())))
        return

    fm = OR.load_faithful(args.map)
    lvl = fm["terrain"][0]
    zones, zl, canon = _segment_level(lvl)
    zids = [args.zone] if args.zone is not None else [
        zid for zid in sorted(zones)
        if zones[zid]["terrain_type"] not in (WATER, ROCK) and zones[zid]["area"] >= 60]
    for zid in zids:
        sk = skeleton(fm, zid, zones, zl)
        if not sk:
            continue
        print(_summ(sk))
        out = os.path.join(rdir, f"{args.map.replace(' ', '_')}_z{zid}.png")
        render(sk, out)
    print(f"-> {rdir}/")


if __name__ == "__main__":
    main()
