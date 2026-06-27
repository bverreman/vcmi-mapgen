"""Region-graph prototype (single zone) — placement driven by the NAVIGATION GRAPH instead of by
local object statistics. The point is to make the three things the corpus has and our markov/group
generator lacks *fall out of the graph*:

  * clear zone definition + a TRANSITION  -> a solid obstacle belt with a few GATE gaps to outside;
  * resource / artifact POCKETS           -> caches placed at the END of dead-end branches;
  * non-random MONSTERS                    -> one guard on every NECK (gate mouth + branch mouth).

It is deliberately standalone (no change to the main pipeline) so we can render it and judge whether
the topology reads visually before wiring it through `synthesize_zone`. Run:

    uv run python -m vcmi_mapgen.proto_region --seed 1
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON          # noqa: E402
import zone_engine as ZE       # noqa: E402

GRASS = 2
BORDER = 3                     # obstacle-belt thickness around the zone


def _carve_walk(rng, a, b):
    """A 4-connected wandering path from a to b (the trunk segments meander like real roads)."""
    (x, y), (x1, y1) = a, b
    path = [(x, y)]
    while (x, y) != (x1, y1):
        if x != x1 and (y == y1 or rng.random() < 0.5):
            x += 1 if x1 > x else -1
        else:
            y += 1 if y1 > y else -1
        path.append((x, y))
    return path


def build(seed=1, W=56, H=56):
    """Return (terrain_grid, nav_set, pockets, gates, objects) for one region-graph zone."""
    rng = random.Random(seed)

    def interior(x, y):
        return BORDER <= x < W - BORDER and BORDER <= y < H - BORDER

    nav = set()                                  # carved navigable tiles (the clearings)

    def carve(tiles, width=0):
        """Open `tiles`, optionally THICKENED by Chebyshev radius `width` so a road/clearing is an
        area, not a 1-tile line (real region paths are several tiles wide)."""
        for (tx, ty) in tiles:
            for dx in range(-width, width + 1):
                for dy in range(-width, width + 1):
                    if interior(tx + dx, ty + dy):
                        nav.add((tx + dx, ty + dy))

    centre = (W // 2, H // 2)

    # --- GATES: gaps in the belt, each with a short corridor through the belt to the map edge
    #     (a gate is where THIS zone would hand off to a neighbour — the transition). ---
    cand = [(W // 2, BORDER), (W // 2, H - BORDER - 1), (BORDER, H // 2), (W - BORDER - 1, H // 2)]
    gates = rng.sample(cand, 3)
    gate_mouths = []
    for gx, gy in gates:
        # corridor straight out through the belt to the edge
        if gy == BORDER:
            line = [(gx, yy) for yy in range(0, BORDER + 1)]
        elif gy == H - BORDER - 1:
            line = [(gx, yy) for yy in range(H - BORDER - 1, H)]
        elif gx == BORDER:
            line = [(xx, gy) for xx in range(0, BORDER + 1)]
        else:
            line = [(xx, gy) for xx in range(W - BORDER - 1, W)]
        for t in line:
            nav.add(t)                            # belt corridor is open even outside `interior`
        gate_mouths.append((gx, gy))

    # --- TRUNK: connect every gate to the centre (the zone's through-road network), 3 tiles wide ---
    trunk = set()
    for g in gates:
        trunk.update(_carve_walk(rng, g, centre))
    carve(trunk, width=1)                          # 3-wide main roads
    trunk = {t for t in trunk if interior(*t)}

    # --- BRANCHES: dead-end spurs off the trunk, each ending in a small POCKET clearing ---
    pockets = []
    trunk_list = sorted(trunk)
    used = set()
    for _ in range(rng.randint(5, 7)):
        start = rng.choice(trunk_list)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        rng.shuffle(dirs)
        made = None
        for dx, dy in dirs:
            L = rng.randint(4, 8)
            path, x, y, ok = [start], start[0], start[1], True
            for _i in range(L):
                nx, ny = x + dx, y + dy
                if not interior(nx, ny) or (nx, ny) in used:
                    ok = False
                    break
                x, y = nx, ny
                path.append((x, y))
            if not ok or len(path) < 4:
                continue
            end = path[-1]
            r = rng.randint(2, 3)                  # a pocket is a real clearing, not a dot
            disk = {(ex, ey) for ex in range(end[0] - r, end[0] + r + 1)
                    for ey in range(end[1] - r, end[1] + r + 1)
                    if interior(ex, ey) and abs(ex - end[0]) + abs(ey - end[1]) <= r}
            made = {"center": end, "tiles": disk, "neck": path[1], "spur": path}
            carve(path, width=1)                   # 2-3 wide branch
            carve(disk)
            used.update(path)
            used.update(disk)
            break
        if made:
            pockets.append(made)

    terrain = ZE.tile_terrain([[GRASS] * W for _ in range(H)], W, H)
    objects = _populate(rng, W, H, nav, pockets, gate_mouths, interior)
    return terrain, nav, pockets, gate_mouths, objects


def _populate(rng, W, H, nav, pockets, gate_mouths, interior):
    objs = []
    guard_pool = ON.gameplay_pool("grass", "GUARD")
    grp = (ZE._load_or_build_grammar().get("_groups") or {}).get("grass", {})
    occupied = set()

    def put(ident, x, y, purpose):
        o = {**ident, "x": x, "y": y, "l": 0, "_purpose": purpose}
        objs.append(o)
        occupied.add((x, y))
        return o

    def guard_at(t):
        if guard_pool and t not in occupied:
            put(rng.choice(guard_pool), t[0], t[1], "GUARD")

    # CACHE per pocket: an archetype anchored on a loot/visitable, members on the pocket tiles.
    loot_anchors = [p for p in ("REWARD_PICKUP", "RESOURCE_PILE", "MINE", "BANK") if p in grp]
    for pk in pockets:
        ap = rng.choice(loot_anchors) if loot_anchors else "REWARD_PICKUP"
        arch = grp.get(ap, {})
        cells = sorted(pk["tiles"])
        cx, cy = pk["center"]
        anchor_pool = (arch.get("idents") or {}).get("_anchor") or \
            [{"identity": i, "weight": 1} for i in ON.gameplay_pool("grass", ap)]
        if anchor_pool and (cx, cy) not in occupied:
            put(ZE._pick_identity(anchor_pool, rng), cx, cy, ap)
        # members: draw the archetype's member purposes onto the remaining pocket tiles
        members = [m for m, ms in sorted((arch.get("members") or {}).items(),
                                         key=lambda kv: -kv[1][0]) if ms[0] > 0.25]
        spots = [t for t in cells if t not in occupied]
        rng.shuffle(spots)
        for mp in members[:3]:
            if not spots:
                break
            pool = (arch.get("idents") or {}).get(mp) or \
                [{"identity": i, "weight": 1} for i in ON.gameplay_pool("grass", mp)]
            if pool:
                t = spots.pop()
                put(ZE._pick_identity(pool, rng), t[0], t[1], mp)
        guard_at(pk["neck"])                      # one monster on the branch mouth

    for g in gate_mouths:                         # one monster on each gate mouth
        guard_at(g)

    # FILL: pack the non-navigable area with real obstacles, LARGEST-FIRST, so it reads as a solid
    # mountain/forest mass (not sparse scatter) and the carved graph is the only open ground. Each
    # obstacle is placed only where its whole blocking footprint lands on free non-nav tiles.
    import obj_resolve as OR
    # exclude the water-look obstacles (lakes / river deltas / canals) on ALL terrain, exactly as the
    # main pipeline does — they are not real land obstacles and must never fill a grass zone.
    pool = sorted(ON.decor_pool("grass", blocking=True, exclude_types=ZE.EXCLUDE_DECOR_TYPES),
                  key=lambda i: -ON.footprint_size(i["animation"]))
    big = [i for i in pool if ON.footprint_size(i["animation"]) >= 4]
    small = [i for i in pool if ON.footprint_size(i["animation"]) == 1] or pool
    covered = set(nav) | set(occupied)

    def fits(ident, x, y):
        cells = [(cx, cy) for cx, cy, b in OR.mask_cells(ident["mask"], x, y) if b]
        if not cells:
            return None
        for cx, cy in cells:
            if not (0 <= cx < W and 0 <= cy < H) or (cx, cy) in covered:
                return None
        return cells

    for y in range(H):
        for x in range(W):
            if (x, y) in covered:
                continue
            placed = False
            for ident in (big if rng.random() < 0.6 else []):     # try a mountain/forest clump
                cells = fits(ident, x, y)
                if cells:
                    put(ident, x, y, "DECORATION")
                    covered.update(cells)
                    placed = True
                    break
            if not placed:                                        # 1-tile filler closes the gaps
                put(rng.choice(small), x, y, "DECORATION")
                covered.add((x, y))
    return objs


def render_schematic(W, H, nav, pockets, gates, objects, out, scale=10):
    """A flat topology map so the GRAPH structure is unambiguous: open clearings, walls, the pocket
    cells, gate mouths, cache anchors and guards — exactly the structures we are trying to produce."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W * scale, H * scale), (40, 70, 40))
    d = ImageDraw.Draw(img)
    occ = {(o["x"], o["y"]): o for o in objects}

    def cell(x, y, col):
        d.rectangle([x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1], fill=col)
    for y in range(H):
        for x in range(W):
            o = occ.get((x, y))
            if o and o["_purpose"] == "DECORATION":
                cell(x, y, (54, 54, 60))                 # wall / forest fill (dark)
            elif (x, y) in nav:
                cell(x, y, (200, 210, 170))              # open clearing (light)
    for pk in pockets:                                    # pocket cells tinted gold
        for (x, y) in pk["tiles"]:
            cell(x, y, (218, 190, 90))
    for (x, y), o in occ.items():                         # gameplay markers on top
        p = o["_purpose"]
        if p == "GUARD":
            col = (210, 50, 50)
        elif p == "DECORATION":
            continue
        else:
            col = (200, 60, 200)                          # cache anchor / member (magenta)
        d.ellipse([x * scale + 1, y * scale + 1, (x + 1) * scale - 2, (y + 1) * scale - 2], fill=col)
    for (gx, gy) in gates:                                 # gate mouths ringed cyan
        d.rectangle([gx * scale, gy * scale, (gx + 1) * scale - 1, (gy + 1) * scale - 1],
                    outline=(40, 220, 220), width=2)
    img.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--size", type=int, default=56)
    ap.add_argument("--sprite", action="store_true", help="also write the H3 sprite render")
    args = ap.parse_args()
    terrain, nav, pockets, gates, objects = build(args.seed, args.size, args.size)
    fm = {"name": f"ProtoRegion-s{args.seed}", "width": args.size, "height": args.size,
          "twoLevel": False, "players": 0, "terrain": [terrain], "objects": objects}
    rdir = os.path.join(ZE.ROOT, "out", "render")
    os.makedirs(rdir, exist_ok=True)
    schem = os.path.join(rdir, f"proto_region_s{args.seed}_schem.png")
    render_schematic(args.size, args.size, nav, pockets, gates, objects, schem)
    print(f"seed {args.seed}: nav={len(nav)} pockets={len(pockets)} gates={len(gates)} "
          f"objects={len(objects)} -> {schem}")
    if args.sprite:
        out = os.path.join(rdir, f"proto_region_s{args.seed}.png")
        ZE.render_fm(fm, out, title=f"region-graph proto: {len(pockets)} pockets, {len(gates)} gates")
        print(f"  sprite -> {out}")


if __name__ == "__main__":
    main()
