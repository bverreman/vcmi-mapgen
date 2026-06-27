"""Figure/ground-INVERTED region synthesis, tested on a real corpus terrain.

Hypothesis: a region's morphology is fixed not by HOW MUCH decoration (density) nor by what's near
what (co-occurrence), but by treating OPEN as the ground and obstacles as the FIGURE — a small set
of discrete, sized masses placed into the open so that (1) density = sum of mass sizes, (2) the
masses stay distinct (don't merge), and (3) the open complement stays ONE connected field.

We test it the only honest way: take zone 11 of "All for One", measure the REAL obstacle-mass sizes,
reproduce that many masses of those sizes by the rule above, and render real-vs-ours on the same
terrain. Run:  uv run python -m vcmi_mapgen.proto_region_invert
"""
import argparse
import os
import random
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import ontology as ON           # noqa: E402
import render_editor as RE      # noqa: E402
import zone_engine as ZE        # noqa: E402
from PIL import Image           # noqa: E402

NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _components(cells):
    cs = set(cells)
    seen = set()
    out = []
    for s in cs:
        if s in seen:
            continue
        q = deque([s])
        seen.add(s)
        comp = [s]
        while q:
            x, y = q.popleft()
            for dx, dy in NB4:
                n = (x + dx, y + dy)
                if n in cs and n not in seen:
                    seen.add(n)
                    comp.append(n)
                    q.append(n)
        out.append(comp)
    return out


def _largest_open(open_tiles):
    comps = _components(open_tiles)
    return max((len(c) for c in comps), default=0)


def place_masses(open_tiles, sizes, rng, elongate=0.5):
    """Place contiguous obstacle masses of the given sizes into `open_tiles` so that masses never
    touch (4-adjacency) and the open complement stays one connected field. Returns list of tile sets;
    masses that cannot be placed within the connectivity budget are skipped (and reported)."""
    open_set = set(open_tiles)
    placed = set()                                # all mass tiles so far
    masses = []
    skipped = 0
    for size in sizes:
        free = open_set - placed
        # candidate seeds: free tiles not 4-adjacent to an existing mass
        seeds = [t for t in free if all((t[0] + dx, t[1] + dy) not in placed for dx, dy in NB4)]
        rng.shuffle(seeds)
        done = False
        for seed in seeds[:40]:
            blob = _grow_blob(seed, size, free, placed, rng, elongate)
            if len(blob) < max(2, int(size * 0.6)):
                continue
            remaining = open_set - placed - blob
            if not remaining:
                continue
            # the open complement must stay essentially one field (allow a few 1-tile crumbs)
            if _largest_open(remaining) >= len(remaining) - 3:
                placed |= blob
                masses.append(blob)
                done = True
                break
        if not done:
            skipped += 1
    return masses, skipped


def _grow_blob(seed, size, free, placed, rng, elongate):
    """Grow a contiguous blob from `seed` up to `size` tiles, staying inside `free`, never 4-adjacent
    to an existing mass. `elongate` biases growth along one axis so some masses read as ranges."""
    axis = rng.random() < elongate
    horiz = rng.random() < 0.5
    blob = {seed}
    frontier = [seed]
    while len(blob) < size and frontier:
        # weight frontier toward extending along the chosen axis (range) or compact (grove)
        cx, cy = frontier[rng.randrange(len(frontier))]
        cand = []
        for dx, dy in NB4:
            n = (cx + dx, cy + dy)
            if n in free and n not in blob and n not in placed and \
               all((n[0] + ax, n[1] + ay) not in placed for ax, ay in NB4):
                w = 1
                if axis:
                    w = 3 if ((dx != 0) == horiz) else 1
                cand += [n] * w
        if not cand:
            frontier.remove((cx, cy))
            continue
        nxt = cand[rng.randrange(len(cand))]
        blob.add(nxt)
        frontier.append(nxt)
    return blob


def _fill_masses(masses, rng):
    """Fill each mass with terrain-coherent blocking obstacles. Each mass picks ONE category (a grove
    of trees, a range of mountains) so it reads as a coherent feature with internal variety — not the
    monotone single-sprite field. Water-look obstacles are excluded on all terrain."""
    pool = ON.decor_pool("grass", blocking=True, exclude_types=ZE.EXCLUDE_DECOR_TYPES)
    by_cat = {}
    cats = ON.veg_categories()
    for i in pool:
        c = ON.category_of(i["animation"])
        by_cat.setdefault(cats[c] if c is not None else "?", []).append(i)
    big_cats = [c for c, lst in by_cat.items() if len(lst) >= 3]
    objs = []
    for mass in masses:
        cat = rng.choice(big_cats) if big_cats else None
        idents = by_cat.get(cat) or pool
        covered = set()
        for (x, y) in sorted(mass):
            if (x, y) in covered:
                continue
            ident = rng.choice(idents)
            cells = [(cx, cy) for cx, cy, b in OR.mask_cells(ident["mask"], x, y) if b]
            if all(c in mass and c not in covered for c in cells):   # obstacle stays inside the mass
                objs.append({**ident, "x": x, "y": y, "l": 0, "_purpose": "DECORATION"})
                covered.update(cells)
            else:
                one = rng.choice([i for i in idents if ON.footprint_size(i["animation"]) == 1] or idents)
                objs.append({**one, "x": x, "y": y, "l": 0, "_purpose": "DECORATION"})
                covered.add((x, y))
    return objs


def _place_gameplay(open_after, rng, n_groups=6):
    """Light gameplay so the region isn't empty: a few caches in the open field (the morphology, not
    the gameplay, is what this test is about). Identities from the ontology."""
    objs = []
    spots = list(open_after)
    rng.shuffle(spots)
    pools = {p: ON.gameplay_pool("grass", p) for p in ("RESOURCE_PILE", "REWARD_PICKUP", "MINE")}
    for _ in range(n_groups):
        if not spots:
            break
        cx, cy = spots.pop()
        for p, k in (("MINE", 1), ("RESOURCE_PILE", 2), ("REWARD_PICKUP", 1)):
            for _i in range(k):
                near = [t for t in open_after if abs(t[0] - cx) + abs(t[1] - cy) <= 2]
                if near and pools[p]:
                    t = rng.choice(near)
                    objs.append({**rng.choice(pools[p]), "x": t[0], "y": t[1], "l": 0, "_purpose": p})
    return objs


def _render_crop(terr, objs, bbox, out):
    fm = {"name": "x", "width": len(terr[0]), "height": len(terr),
          "twoLevel": False, "players": 0, "terrain": [terr], "objects": objs}
    tmp = out + ".full.png"
    ZE.render_fm(fm, tmp, title="")
    T = RE.TILE
    x0, y0, x1, y1 = bbox
    img = Image.open(tmp).crop((x0 * T, y0 * T, (x1 + 1) * T, (y1 + 1) * T))
    img.save(out)
    os.remove(tmp)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=11)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    fm = OR.load_faithful(args.map)
    W, H = fm["width"], fm["height"]
    lvl = fm["terrain"][0]
    zones, zl, canon = ZE._segment_level(lvl)
    z = zones[args.zone]
    ts = set(z["tiles_set"])
    xs = [x for x, y in ts]
    ys = [y for x, y in ts]
    bbox = (max(min(xs) - 2, 0), max(min(ys) - 2, 0), min(max(xs) + 2, W - 1), min(max(ys) + 2, H - 1))

    real = [o for o in fm["objects"] if o.get("l", 0) == 0
            and any((tx, ty) in ts for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]
    real_block = {(cx, cy) for o in real for cx, cy, b in OR.mask_cells(o["mask"], o["x"], o["y"])
                  if b and (cx, cy) in ts}
    target_sizes = sorted((len(c) for c in _components(real_block)), reverse=True)

    # INVERTED synthesis: place those masses into the open grass shape, fill, add light gameplay
    masses, skipped = place_masses(ts, target_sizes, rng)
    deco = _fill_masses(masses, rng)
    mass_tiles = set().union(*masses) if masses else set()
    gp = _place_gameplay(ts - mass_tiles, rng)
    gen = deco + gp

    # morphology of both
    def morph(block):
        block &= ts
        op = ts - block
        oc = sorted((len(c) for c in _components(op)), reverse=True)
        cc = sorted((len(c) for c in _components(block)), reverse=True)
        return len(block) / len(ts), oc, cc
    gen_block = {(cx, cy) for o in gen for cx, cy, b in OR.mask_cells(o["mask"], o["x"], o["y"])
                 if b and (cx, cy) in ts}
    rf, roc, rcc = morph(set(real_block))
    gf, goc, gcc = morph(gen_block)
    print(f"target masses: n={len(target_sizes)} sizes={target_sizes[:8]} (skipped {skipped})")
    print(f"REAL: frac={rf:.2f} open_top5={roc[:5]} ({len(roc)} comps) clumps_top5={rcc[:5]} ({len(rcc)})")
    print(f"GEN : frac={gf:.2f} open_top5={goc[:5]} ({len(goc)} comps) clumps_top5={gcc[:5]} ({len(gcc)})")

    rdir = os.path.join(ZE.ROOT, "out", "render")
    base = f"invert_{args.map.replace(' ', '_')}_z{args.zone}"
    a = _render_crop(lvl, real, bbox, os.path.join(rdir, base + "_real.png"))
    b = _render_crop(lvl, gen, bbox, os.path.join(rdir, base + "_gen.png"))
    combo = Image.new("RGB", (a.width + b.width + 8, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0))
    combo.paste(b, (a.width + 8, 0))
    cpath = os.path.join(rdir, base + "_compare.png")
    combo.save(cpath)
    print(f"compare -> {cpath}")


if __name__ == "__main__":
    main()
