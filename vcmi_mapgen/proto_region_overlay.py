"""Passability overlay: the free/occupied/visitable mask drawn TRANSLUCENT over the real sprite
render (the way the editor shows passability), so we can see whether the mask actually lands on the
objects. Real region | our generation, cropped to the zone.

    uv run python -m vcmi_mapgen.proto_region_overlay --map "All for One" --zone 11
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import render_editor as RE      # noqa: E402
import zone_engine as ZE        # noqa: E402
from PIL import Image           # noqa: E402

COLORS = {                      # by mask char (the 4 real states + free)
    "B": (235, 40, 40, 130),    # blocked - red
    "X": (245, 140, 25, 165),   # blocked + visitable (building action tile) - orange
    "A": (245, 225, 40, 175),   # passable + visitable (stand-on) - yellow
    "V": (60, 230, 90, 60),     # passable overlay/overhang - faint green
    None: (60, 230, 90, 60),    # free ground - faint green
}
_PRIO = {"X": 4, "B": 3, "A": 2, "V": 1}  # if objects overlap a tile, show the most salient state


def _char_at(fm, ts):
    """{(x,y): mask char} over the zone tiles, keeping the highest-priority char per tile."""
    out = {}
    for o in fm["objects"]:
        mask = o["mask"]
        hh = len(mask)
        for r, row in enumerate(mask):
            for c, ch in enumerate(row):
                if ch == " ":
                    continue
                # anchor = bottom-right tile; masks read right-to-left so col 0 is the rightmost
                # (anchor) tile -> tx = x - c. Matches obj_resolve.mask_cells (see its column-flip note).
                t = (o["x"] - c, o["y"] - (hh - 1 - r))
                if t in ts and _PRIO.get(ch, 0) > _PRIO.get(out.get(t), 0):
                    out[t] = ch
    return out


def _overlay(fm, ts, bbox, out):
    T = RE.TILE
    tmp = out + ".full.png"
    ZE.render_fm(fm, tmp, title="")
    base = Image.open(tmp).convert("RGBA")
    ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
    px = ov.load()
    chars = _char_at(fm, ts)
    for (x, y) in ts:
        col = COLORS.get(chars.get((x, y)), COLORS[None])
        for yy in range(y * T, y * T + T):
            for xx in range(x * T, x * T + T):
                px[xx, yy] = col
    base.alpha_composite(ov)
    x0, y0, x1, y1 = bbox
    crop = base.crop((x0 * T, y0 * T, (x1 + 1) * T, (y1 + 1) * T)).convert("RGB")
    crop.save(out)
    os.remove(tmp)
    return crop


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
    real_fm = {"width": W, "height": H, "twoLevel": False, "players": 0,
               "terrain": [lvl], "objects": real, "name": "real"}

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
    gen_fm = {"width": W, "height": H, "twoLevel": False, "players": 0,
              "terrain": [lvl], "objects": gen, "name": "gen"}

    rdir = os.path.join(ZE.ROOT, "out", "render")
    a = _overlay(real_fm, ts, bbox, os.path.join(rdir, f"ov_{args.map.replace(' ', '_')}_z{args.zone}_real.png"))
    b = _overlay(gen_fm, ts, bbox, os.path.join(rdir, f"ov_{args.map.replace(' ', '_')}_z{args.zone}_gen.png"))
    combo = Image.new("RGB", (a.width + b.width + 10, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0))
    combo.paste(b, (a.width + 10, 0))
    out = os.path.join(rdir, f"ov_{args.map.replace(' ', '_')}_z{args.zone}.png")
    combo.save(out)
    print(f"overlay (real | gen) -> {out}")


if __name__ == "__main__":
    main()
