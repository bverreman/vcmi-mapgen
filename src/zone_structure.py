"""Test the load-bearing assumption of the structure-first rethink:
do real gameplay objects organize into terrain-passability ZONES around towns?

Segment the real map's passable terrain by geodesic (4-connected, through passable
tiles only) nearest-town. If the approach is sound, mines/guards/treasure should fall
predominantly in the SAME zone as a town and cluster by distance-to-town -- i.e. the
map IS a set of town-anchored zones, which is exactly what a structure-first placer
would build. Renders the zones + real objects, and prints per-zone object budgets.
"""

import sys, os, json, collections
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON
import render

WATER, ROCK = 8, 9
TILE = 9
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
ZONE_TINT = [(200, 80, 80), (80, 160, 200), (90, 200, 120), (210, 180, 70),
             (180, 110, 200), (220, 140, 80), (120, 200, 200), (200, 120, 160),
             (150, 170, 90), (110, 130, 220)]


def load(name):
    m = json.load(open(f"/mnt/data/workspace/vcmi-mapgen/out/maps/{name}.json"))
    terr = [[c["t"] for c in row] for row in m["terrain"][0]]
    gp, towns = [], []
    for o in m["objects"]:
        if o.get("l", 0) != 0:
            continue
        p = ON.resolve(o["class"], o["subclass"]).get("purpose")
        if p == "TOWN":
            towns.append((o["x"], o["y"]))
        if p and p != "DECORATION":
            gp.append((o["x"], o["y"], p))
    return terr, gp, towns, m["width"], m["height"]


def zones_by_town(terr, towns, W, H):
    """Geodesic nearest-town label for every passable tile (multi-source BFS)."""
    passable = [[terr[y][x] not in (WATER, ROCK) for x in range(W)] for y in range(H)]
    label = [[-1] * W for _ in range(H)]
    dq = collections.deque()
    for zi, (tx, ty) in enumerate(towns):
        # seed from passable tiles around the town anchor
        for dx in range(-3, 1):
            for dy in range(-3, 1):
                x, y = tx + dx, ty + dy
                if 0 <= x < W and 0 <= y < H and passable[y][x] and label[y][x] == -1:
                    label[y][x] = zi
                    dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and passable[ny][nx] and label[ny][nx] == -1:
                label[ny][nx] = label[y][x]
                dq.append((nx, ny))
    return label, passable


def main():
    name = "All for One"
    terr, gp, towns, W, H = load(name)
    label, passable = zones_by_town(terr, towns, W, H)

    # render
    img = Image.new("RGB", (W * TILE, H * TILE), (10, 10, 10))
    px = img.load()
    for y in range(H):
        for x in range(W):
            base = render.TERRAIN_RGB.get(terr[y][x], (0, 0, 0))
            z = label[y][x]
            if z >= 0:
                tint = ZONE_TINT[z % len(ZONE_TINT)]
                col = tuple((b + t) // 2 for b, t in zip(base, tint))
            else:
                col = base
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy] = col
    d = ImageDraw.Draw(img)
    for (x, y, p) in gp:
        c = render.PURPOSE_RGB.get(p, (255, 255, 255))
        r = 5 if p == "TOWN" else 2
        cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c, outline=(0, 0, 0))
    out = "/mnt/data/workspace/vcmi-mapgen/out/render/zone_structure.png"
    img.save(out)

    # stats: do objects sit in a town zone, and how close to their zone's town?
    nz = len(towns)
    in_zone = 0
    out_zone = 0
    per_zone = collections.defaultdict(lambda: collections.Counter())
    for (x, y, p) in gp:
        if p == "TOWN":
            continue
        z = label[y][x] if 0 <= y < H and 0 <= x < W else -1
        if z >= 0:
            in_zone += 1
            per_zone[z][p] += 1
        else:
            out_zone += 1
    tot = in_zone + out_zone
    print(f"{name}: {nz} towns -> {nz} zones. passable tiles covered: "
          f"{sum(1 for y in range(H) for x in range(W) if label[y][x]>=0)}/"
          f"{sum(1 for y in range(H) for x in range(W) if passable[y][x])}")
    print(f"gameplay objects inside a town-zone: {in_zone}/{tot} "
          f"({100*in_zone/tot:.0f}%);  outside any zone (unreachable pockets): {out_zone}")
    print("per-zone gameplay budget (objects per zone):")
    for z in sorted(per_zone):
        c = per_zone[z]
        print(f"  zone {z}: total={sum(c.values()):3d}  " +
              " ".join(f"{k}={v}" for k, v in c.most_common(6)))
    print("wrote", out)


if __name__ == "__main__":
    main()
