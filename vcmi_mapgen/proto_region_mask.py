"""Structural mask of a region: white = free/passable, red = occupied (blocking), yellow = visitable
tile (an 'A' anchor a hero stands on). Renders the REAL region next to OUR generation so the layout
structure is visible without the distraction of sprites.

    uv run python -m vcmi_mapgen.proto_region_mask --map "All for One" --zone 11
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import zone_engine as ZE        # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

BG = (35, 35, 40)
FREE = (240, 240, 240)
OCC = (205, 45, 45)
VIS = (235, 215, 45)
SCALE = 11


def _masks(objs, ts):
    blocked, visit = set(), set()
    for o in objs:
        purpose = o.get("_purpose") or OR.purpose_of(o)
        gp = purpose not in ("DECORATION", "TERRAIN_MODIFIER")
        for cx, cy, b in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if (cx, cy) in ts:
                if b:
                    blocked.add((cx, cy))
                elif gp:                          # non-blocking anchor of a gameplay object
                    visit.add((cx, cy))
    return blocked, visit


def _draw(ts, blocked, visit, bbox):
    x0, y0, x1, y1 = bbox
    W, H = (x1 - x0 + 1), (y1 - y0 + 1)
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    d = ImageDraw.Draw(img)
    for (x, y) in ts:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        if (x, y) in blocked:
            col = OCC
        elif (x, y) in visit:
            col = VIS
        else:
            col = FREE
        px, py = (x - x0) * SCALE, (y - y0) * SCALE
        d.rectangle([px, py, px + SCALE - 1, py + SCALE - 1], fill=col)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=11)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    fm = OR.load_faithful(args.map)
    W, H = fm["width"], fm["height"]
    lvl = fm["terrain"][0]
    zones, zl, canon = ZE._segment_level(lvl)
    z = zones[args.zone]
    ts = set(z["tiles_set"])
    tn = ZE.TNAME.get(z["terrain_type"])
    xs = [x for x, y in ts]
    ys = [y for x, y in ts]
    bbox = (min(xs), min(ys), max(xs), max(ys))

    real = [o for o in fm["objects"] if o.get("l", 0) == 0
            and any((tx, ty) in ts for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]

    grammar = ZE._load_or_build_grammar()
    anchored = {OR.purpose_of(o) for o in real
                if 0 <= o["x"] < W and 0 <= o["y"] < H and zl[o["y"]][o["x"]] == args.zone
                and OR.purpose_of(o) not in ("DECORATION", "TERRAIN_MODIFIER")}
    rg = (grammar.get(tn) or {}).get(ZE._role_of(anchored)) or (grammar.get(tn) or {}).get("passage")
    pas = ZE._zone_passages(zones, zl, W, H)
    gen = ZE.synthesize_zone(
        z, canon[args.zone], rg, sorted(pas.get(args.zone, set())),
        args.seed ^ (args.zone * 2654435761 & 0xFFFFFFFF), 0, set(),
        veg="grammar", strict_terrain=False, terr_grid=lvl,
        catmrf=(grammar.get("_catmrf") or {}).get(tn),
        groups=(grammar.get("_groups") or {}).get(tn), gameplay=True)

    def stats(objs, tag):
        bl, vi = _masks(objs, ts)
        print(f"{tag}: free={len(ts) - len(bl)} occ={len(bl)} ({len(bl) / len(ts):.0%}) visit={len(vi)}")
        return bl, vi
    rb, rv = stats(real, "REAL")
    gb, gv = stats(gen, "GEN ")

    a = _draw(ts, rb, rv, bbox)
    b = _draw(ts, gb, gv, bbox)
    combo = Image.new("RGB", (a.width + b.width + 10, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0))
    combo.paste(b, (a.width + 10, 0))
    out = os.path.join(ZE.ROOT, "out", "render", f"mask_{args.map.replace(' ', '_')}_z{args.zone}.png")
    combo.save(out)
    print(f"mask compare (real | gen) -> {out}")


if __name__ == "__main__":
    main()
