"""Gameplay-PLACEMENT comparison -- the structure that actually defines a HoMM3 map.

The footprint renderer shows decoration texture; this shows WHERE the gameplay
objects sit (towns, mines, guards, treasure, dwellings, banks...), colored by
purpose, with decorations faint. Real vs generated, side by side, so the
object-placement structure is judged directly -- not a coverage statistic.
"""

import sys, os, json, collections
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON
import vmapwrite, vmaplib, render

TILE = 9
LIB = json.load(open("/mnt/data/workspace/vcmi-mapgen/out/objlib.json"))
T2P = {it["type"]: p for p, terr in LIB.items() for items in terr.values() for it in items}
BIG = {"TOWN": 6, "MINE": 4, "DWELLING": 4, "BANK": 3, "TRANSPORT": 4, "QUEST_GATE": 3}


def panel(terr_l0, gp, deco, W, H, title):
    img = Image.new("RGB", (W * TILE, H * TILE + 16), (15, 15, 15))
    px = img.load()
    for y in range(H):
        for x in range(W):
            r, g, b = render.TERRAIN_RGB.get(terr_l0[y][x], (0, 0, 0))
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy + 16] = (r, g, b)
    d = ImageDraw.Draw(img)
    for (x, y) in deco:                       # decorations: faint grey speck
        if 0 <= x < W and 0 <= y < H:
            d.point((x * TILE + TILE // 2, y * TILE + TILE // 2 + 16), fill=(70, 70, 70))
    for (x, y, p) in gp:                       # gameplay: bold purpose-colored marker
        col = render.PURPOSE_RGB.get(p, (255, 255, 255))
        rad = BIG.get(p, 2)
        cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2 + 16
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col, outline=(0, 0, 0))
    d.text((3, 3), title, fill=(255, 255, 255))
    return img


def real_objs(name):
    m = json.load(open(f"/mnt/data/workspace/vcmi-mapgen/out/maps/{name}.json"))
    terr0 = [[c["t"] for c in row] for row in m["terrain"][0]]
    gp, deco = [], []
    for o in m["objects"]:
        if o.get("l", 0) != 0:
            continue
        p = ON.resolve(o["class"], o["subclass"]).get("purpose")
        if p == "DECORATION":
            deco.append((o["x"], o["y"]))
        elif p:
            gp.append((o["x"], o["y"], p))
    return terr0, gp, deco, m["width"], m["height"]


def gen_objs(vmap):
    h, surf, under, objs = vmapwrite.read_raw(vmap)
    H, W = len(surf), len(surf[0])
    terr0 = [[vmaplib.TERR.get(c[:2], 2) for c in row] for row in surf]
    gp, deco = [], []
    for o in objs:
        if o.get("l", 0) != 0:
            continue
        p = T2P.get(o["type"], "?")
        if p == "DECORATION":
            deco.append((o["x"], o["y"]))
        elif p and p != "?":
            gp.append((o["x"], o["y"], p))
    return terr0, gp, deco, W, H


def main():
    name = "All for One"
    vmap = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/data/workspace/vcmi-mapgen/out/DLGen-All_for_One-s0.vmap"
    out = "/mnt/data/workspace/vcmi-mapgen/out/render/gameplay_compare.png"

    rt, rg, rd, rW, rH = real_objs(name)
    gt, gg, gd, gW, gH = gen_objs(vmap)
    ri = panel(rt, rg, rd, rW, rH, f"REAL {name}: {len(rg)} gameplay objects")
    gi = panel(gt, gg, gd, gW, gH, f"GENERATED: {len(gg)} gameplay objects")
    gap = 16
    canvas = Image.new("RGB", (ri.width + gi.width + gap, max(ri.height, gi.height)), (0, 0, 0))
    canvas.paste(ri, (0, 0)); canvas.paste(gi, (ri.width + gap, 0))
    canvas.save(out)

    # per-purpose count + centroid comparison
    def by_p(gp):
        c = collections.Counter(p for _, _, p in gp)
        cen = {}
        for p in c:
            xs = [x for x, y, q in gp if q == p]; ys = [y for x, y, q in gp if q == p]
            cen[p] = (sum(xs) / len(xs), sum(ys) / len(ys))
        return c, cen
    rc, rcen = by_p(rg); gc, gcen = by_p(gg)
    print(f"{'purpose':16s} {'real':>5} {'gen':>5}  centroid_shift(tiles)")
    for p in sorted(set(rc) | set(gc), key=lambda p: -rc.get(p, 0)):
        shift = ""
        if p in rcen and p in gcen:
            dx = rcen[p][0] - gcen[p][0]; dy = rcen[p][1] - gcen[p][1]
            shift = f"{(dx*dx+dy*dy)**0.5:.1f}"
        print(f"{p:16s} {rc.get(p,0):5d} {gc.get(p,0):5d}  {shift}")
    print("wrote", out)


if __name__ == "__main__":
    main()
