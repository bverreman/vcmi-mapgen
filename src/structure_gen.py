"""Structure-first generator -- SLICE 1: place towns from terrain, then zone the map.

The old heatmap model placed towns on map edges (off-map main-town coords), too few,
and 7.6 tiles off the real layout. Here towns come FIRST from the terrain's passable
structure: N towns (N from corpus town-density x this map's passable area) spread by
farthest-point sampling over the largest passable region, constrained to valid
in-bounds interior anchors. Then geodesic zones, one per town. Render + compare town
spread to the real map. No object fill yet -- we verify town placement first.
"""

import sys, os, json, math, collections, argparse
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON
import render
import zone_structure as ZS

WATER, ROCK = 8, 9
TILE = 9
# town footprint is ~ up to 8 wide x 6 tall, anchor bottom-right -> keep the anchor far
# enough from the top/left that the footprint stays on the map, and off the bottom/right.
TOWN_MARGIN_L, TOWN_MARGIN_T, TOWN_MARGIN_BR = 6, 6, 2


def corpus_town_rate(names):
    """towns per passable surface tile, averaged over the corpus -> sets N for a map."""
    tot_t, tot_p = 0, 0
    for name in names:
        m = json.load(open(f"/mnt/data/workspace/vcmi-mapgen/out/maps/{name}.json"))
        terr = m["terrain"][0]
        p = sum(1 for row in terr for c in row if c["t"] not in (WATER, ROCK))
        nt = sum(1 for o in m["objects"] if o.get("l", 0) == 0
                 and ON.resolve(o["class"], o["subclass"]).get("purpose") == "TOWN")
        tot_t += nt
        tot_p += p
    return tot_t / max(1, tot_p)


def largest_component(passable, W, H):
    seen = [[False] * W for _ in range(H)]
    best = []
    for y0 in range(H):
        for x0 in range(W):
            if passable[y0][x0] and not seen[y0][x0]:
                dq = collections.deque([(x0, y0)])
                seen[y0][x0] = True
                comp = []
                while dq:
                    x, y = dq.popleft()
                    comp.append((x, y))
                    for dx, dy in ZS.NB4:
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < W and 0 <= ny < H and passable[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            dq.append((nx, ny))
                if len(comp) > len(best):
                    best = comp
    return best


def valid_town_anchor(x, y, W, H, passable):
    if not (TOWN_MARGIN_L <= x < W - TOWN_MARGIN_BR and TOWN_MARGIN_T <= y < H - TOWN_MARGIN_BR):
        return False
    # require the footprint box to be passable land (no water/rock under the keep)
    for dx in range(-TOWN_MARGIN_L, 1):
        for dy in range(-TOWN_MARGIN_T, 1):
            if not passable[y + dy][x + dx]:
                return False
    return True


def place_towns(terr, n, seed, W, H):
    """Farthest-point spread of n valid town anchors over the largest passable region."""
    import random
    rng = random.Random(seed)
    passable = [[terr[y][x] not in (WATER, ROCK) for x in range(W)] for y in range(H)]
    comp = set(largest_component(passable, W, H))
    cand = [(x, y) for (x, y) in comp if valid_town_anchor(x, y, W, H, passable)]
    if not cand:
        return [], passable
    # seed 1: the candidate farthest from the map border (most interior)
    def border_dist(p):
        x, y = p
        return min(x, y, W - 1 - x, H - 1 - y)
    towns = [max(cand, key=lambda p: (border_dist(p), rng.random()))]
    # farthest-point: each next town maximizes min-distance to chosen towns
    while len(towns) < n:
        best, bestd = None, -1
        for p in cand:
            d = min((p[0] - t[0]) ** 2 + (p[1] - t[1]) ** 2 for t in towns)
            if d > bestd:
                bestd, best = d, p
        if best is None:
            break
        towns.append(best)
    return towns, passable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    names = [os.path.basename(p)[:-5] for p in
             __import__("glob").glob("/mnt/data/workspace/vcmi-mapgen/out/maps/*.json")]
    rate = corpus_town_rate(names)

    m = json.load(open(f"/mnt/data/workspace/vcmi-mapgen/out/maps/{args.terrain}.json"))
    terr = [[c["t"] for c in row] for row in m["terrain"][0]]
    W, H = m["width"], m["height"]
    passable_cnt = sum(1 for row in terr for t in row if t not in (WATER, ROCK))
    n = max(2, round(rate * passable_cnt))

    real_towns = [(o["x"], o["y"]) for o in m["objects"] if o.get("l", 0) == 0
                  and ON.resolve(o["class"], o["subclass"]).get("purpose") == "TOWN"]
    towns, passable = place_towns(terr, n, args.seed, W, H)
    label, _ = ZS.zones_by_town(terr, towns, W, H)

    # render
    img = Image.new("RGB", (W * TILE, H * TILE), (10, 10, 10))
    px = img.load()
    for y in range(H):
        for x in range(W):
            base = render.TERRAIN_RGB.get(terr[y][x], (0, 0, 0))
            z = label[y][x]
            col = (tuple((b + t) // 2 for b, t in zip(base, ZS.ZONE_TINT[z % len(ZS.ZONE_TINT)]))
                   if z >= 0 else base)
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy] = col
    d = ImageDraw.Draw(img)
    for (x, y) in real_towns:                    # real towns: hollow yellow ring
        cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 230, 0), width=2)
    for (x, y) in towns:                          # placed towns: solid white
        cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2
        d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255), outline=(0, 0, 0))
    out = "/mnt/data/workspace/vcmi-mapgen/out/render/structure_towns.png"
    img.save(out)

    def min_pair(ts):
        return min((math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(ts)
                    for b in ts[i + 1:]), default=0)
    print(f"corpus town rate = 1 per {1/rate:.0f} passable tiles")
    print(f"{args.terrain}: passable={passable_cnt}  -> N towns = {n} (real has {len(real_towns)})")
    print(f"placed towns (white): {towns}")
    print(f"min pairwise spacing: placed={min_pair(towns):.1f}  real={min_pair(real_towns):.1f} tiles")
    edge = [t for t in towns if min(t[0], t[1], W-1-t[0], H-1-t[1]) < TOWN_MARGIN_T]
    print(f"placed towns too close to edge: {len(edge)} (was the old bug)")
    print("wrote", out)


if __name__ == "__main__":
    main()
