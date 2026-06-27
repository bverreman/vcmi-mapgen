"""Generate ONE coherent region over a REAL corpus terrain, and compare it to what was really there.

This isolates the smallest honest test: take a real map's zone (its terrain shape + entrances),
throw away its objects, run our generator on that exact canvas, and render the real region next to
the generated one — cropped to the zone — so we can see, region by region, where our synthesis is
incoherent. No terrain generation, no markov: the canvas is real.

    uv run python -m vcmi_mapgen.proto_region_real --map "All for One" --zone 11
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import render_editor as RE      # noqa: E402
import zone_engine as ZE        # noqa: E402
from PIL import Image           # noqa: E402


def _render_crop(terr, objs, bbox, title, out):
    fm = {"name": title, "width": len(terr[0]), "height": len(terr),
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

    fm = OR.load_faithful(args.map)
    W, H = fm["width"], fm["height"]
    lvl = fm["terrain"][0]
    zones, zl, canon = ZE._segment_level(lvl)
    zid = args.zone
    z = zones[zid]
    ts = z["tiles_set"]
    terr_name = ZE.TNAME.get(z["terrain_type"])
    xs = [x for x, y in ts]
    ys = [y for x, y in ts]
    bbox = (max(min(xs) - 2, 0), max(min(ys) - 2, 0), min(max(xs) + 2, W - 1), min(max(ys) + 2, H - 1))

    # --- REAL region: the zone's actual objects (footprint-overlap, so its rim ring comes too) ---
    real_objs = [o for o in fm["objects"] if o.get("l", 0) == 0
                 and any((tx, ty) in ts for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]
    real_gp = [o for o in real_objs if OR.purpose_of(o) not in ("DECORATION", "TERRAIN_MODIFIER")]

    # --- GENERATED region: our synthesizer on the SAME terrain shape (objects discarded) ---
    grammar = ZE._load_or_build_grammar()
    anchored = {OR.purpose_of(o) for o in real_gp
                if 0 <= o["x"] < W and 0 <= o["y"] < H and zl[o["y"]][o["x"]] == zid}
    role = ZE._role_of(anchored)
    gt = grammar.get(terr_name) or {}
    rg = gt.get(role) or gt.get("passage")
    passages = ZE._zone_passages(zones, zl, W, H)
    gen_objs = ZE.synthesize_zone(
        z, canon[zid], rg, sorted(passages.get(zid, set())),
        args.seed ^ (zid * 2654435761 & 0xFFFFFFFF), 0, set(),
        veg="grammar", strict_terrain=False, terr_grid=lvl,
        catmrf=(grammar.get("_catmrf") or {}).get(terr_name),
        groups=(grammar.get("_groups") or {}).get(terr_name), gameplay=True)
    gen_gp = [o for o in gen_objs if o.get("_purpose") not in ("DECORATION", "TERRAIN_MODIFIER")]

    rdir = os.path.join(ZE.ROOT, "out", "render")
    os.makedirs(rdir, exist_ok=True)
    base = f"region_{args.map.replace(' ', '_')}_z{zid}"
    a = _render_crop(lvl, real_objs, bbox, "real", os.path.join(rdir, base + "_real.png"))
    b = _render_crop(lvl, gen_objs, bbox, "gen", os.path.join(rdir, base + "_gen.png"))
    # side-by-side
    combo = Image.new("RGB", (a.width + b.width + 8, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0))
    combo.paste(b, (a.width + 8, 0))
    cpath = os.path.join(rdir, base + "_compare.png")
    combo.save(cpath)
    print(f"zone {zid} {terr_name} role={role} area={z['area']}: "
          f"real objs={len(real_objs)} (gp {len(real_gp)})  |  gen objs={len(gen_objs)} (gp {len(gen_gp)})")
    print(f"  compare -> {cpath}")


if __name__ == "__main__":
    main()
