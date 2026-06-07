"""Coverage test: can the generator REACH a real map?

If the generator is expressive, some setting of its knobs should produce a map
statistically close to a chosen real one -- i.e. the real map lies inside the
generator's reachable space. We extract the target's feature vector, set the
structural knobs from it, search seeds, and report how close the best match gets,
with a side-by-side render.
"""

import sys, os, json, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m, deps, vcmi_ids, deps_realize as R, deps_showcase, deps_render, deps_spatial
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PURPOSES = [
    "TOWN",
    "GUARD",
    "MINE",
    "DWELLING",
    "BANK",
    "REWARD_PICKUP",
    "RESOURCE_PILE",
    "TRANSPORT",
    "STAT_PERMANENT",
    "BONUS_TEMP",
    "SPELL_SKILL",
    "INFO",
    "MANA",
    "SPECIAL",
    "DECORATION",
]
TERRAINS = list(range(10))


def _vec(purpose_cnt, terr_cnt, tiles, struct):
    return {
        "density": {p: 1000.0 * purpose_cnt.get(p, 0) / tiles for p in PURPOSES},
        "terrain": {t: terr_cnt.get(t, 0) / max(1, sum(terr_cnt.values())) for t in TERRAINS},
        "struct": struct,
    }


def feats_real(m):
    tiles = m.width * m.height * (2 if m.two_level else 1)
    terr = collections.Counter()
    for lvl in m.terrain:
        for row in lvl:
            for t in row:
                terr[t.terrain] += 1
    pur = collections.Counter()
    for o in m.objects:
        r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        if r:
            pur[R.TYPE2PURPOSE.get(r[0], "?")] += 1
    d = deps.extract_map(m)
    ng = max(1, d["n_gates"])
    struct = {
        "regions_per_1000": 1000.0 * d["n_reachable"] / tiles,
        "max_depth": max(d["depth"].values()) if d["depth"] else 0,
        "portal_frac": d["gate_types"].get("portal", 0) / ng,
    }
    return _vec(pur, terr, tiles, struct)


def feats_gen(fm, tree):
    terr = collections.Counter()
    for lvl in fm["terrain"]:
        for row in lvl:
            for c in row:
                terr[c["t"]] += 1
    tiles = sum(terr.values())
    pur = collections.Counter(R.TYPE2PURPOSE.get(o["type"], "?") for o in fm["objects"])
    ne = max(1, len(tree["edges"]))
    struct = {
        "regions_per_1000": 1000.0 * tree["n_nodes"] / tiles,
        "max_depth": tree["max_depth"],
        "portal_frac": sum(1 for e in tree["edges"] if e["type"] == "portal") / ne,
    }
    return _vec(pur, terr, tiles, struct)


def distance(a, b):
    dens = sum(abs(a["density"][p] - b["density"][p]) for p in PURPOSES)
    terr = 100 * sum(abs(a["terrain"][t] - b["terrain"][t]) for t in TERRAINS)
    st = (
        abs(a["struct"]["regions_per_1000"] - b["struct"]["regions_per_1000"]) * 3
        + abs(a["struct"]["max_depth"] - b["struct"]["max_depth"]) * 2
        + abs(a["struct"]["portal_frac"] - b["struct"]["portal_frac"]) * 30
    )
    return dens + terr + st, dens, terr, st


def params_from_target(tgt, W, H, two_level=False):
    s = tgt["struct"]
    # surface biomes exclude subterranean (6) and rock (9), which live underground,
    # and water (8), which is carved separately.
    biome = {t: tgt["terrain"][t] for t in (0, 1, 2, 3, 4, 5, 7) if tgt["terrain"][t] > 0}
    # underground holds ~no water, so the surface water fraction is ~2x the per-map
    # (over-both-levels) fraction on a two-level map.
    water = tgt["terrain"].get(8, 0.0)
    params = {
        "n_target": max(6, round(s["regions_per_1000"] * W * H / 1000.0)),
        "max_depth": max(2, round(s["max_depth"])),
        "portal_frac": s["portal_frac"],
        "biome_weights": biome,
        "water_frac": min(0.6, water * 2) if two_level else water,
        "density": dict(tgt["density"]),
    }
    if two_level:
        params["two_level"] = True
        params["terrain_target"] = dict(tgt["terrain"])
    return params


def points_real(m):
    pts = []
    for o in m.objects:
        if o.l != 0:
            continue
        r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        if r:
            pts.append((o.x, o.y, R.TYPE2PURPOSE.get(r[0], "?")))
    return pts


def points_gen(fm):
    return [
        (o["x"], o["y"], R.TYPE2PURPOSE.get(o["type"], "?")) for o in fm["objects"] if o["l"] == 0
    ]


def fit(target_path, W=None, H=None, seeds=12):
    m = h3m.parse_file(target_path)
    # Use the real map's canvas size for small maps (< 72 tiles); cap at 72×72
    # for large maps.  Running a 36×36 real map on a 72×72 canvas spreads
    # objects over 4× the area: sig targets from the real map become unreachable
    # on the larger canvas, so _place_anchored falls back to next_spot() scatter
    # and the graph is no better than shuffled.  Using the real canvas size lets
    # sig values land where placement can satisfy them.
    if W is None:
        W = min(m.width, 72)
    if H is None:
        H = min(m.height, 72)
    tgt = feats_real(m)
    params = params_from_target(tgt, W, H, m.two_level)
    sig_real = deps_spatial.signature(points_real(m))
    # Real maps larger than the generator canvas produce sig distances proportional
    # to their size.  Scale distances down so placement targets are reachable.
    if m.width > W:
        _scale = W / m.width
        sig_target = {k: v * _scale for k, v in sig_real.items()}
    else:
        sig_target = sig_real
    params["sig"] = sig_target
    best = None
    for seed in range(seeds):
        fm, tree, em = R.realize(W, H, seed=seed, params=params)
        g = feats_gen(fm, tree)
        d = distance(tgt, g)  # (total, dens, terr, struct)
        sig_gen = deps_spatial.signature(points_gen(fm))
        sp_mean, sp_diffs = deps_spatial.compare(sig_target, sig_gen)
        # Quadratic over-bar penalty strongly penalizes high-sp seeds so a seed
        # with near-zero density error but sp_mean=3.5 can't beat one with
        # density_error=150 and sp_mean=2.36.  Without this, the wrong seed is
        # selected on dense/small maps (Dawn of War, Jihad, Elbow Room).
        total = d[0] + 90.0 * sp_mean + 600.0 * max(0.0, sp_mean - 2.5) ** 2
        if best is None or total < best[0]:
            best = (total, d, sp_mean, sp_diffs, seed, fm, tree, g, sig_gen)
    return m, tgt, params, sig_target, best


if __name__ == "__main__":
    path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m"
    )
    m, tgt, params, sig_real, best = fit(path)
    total, dist, sp_mean, sp_diffs, seed, fm, tree, g, sig_gen = best
    name = os.path.basename(path).replace(".h3m", "")
    print(f"=== fit generator to '{name}' ===")
    print(
        f"knobs set from target: n_regions={params['n_target']} max_depth={params['max_depth']} "
        f"portal_frac={params['portal_frac']:.2f} biomes={ {k: round(v, 2) for k, v in params['biome_weights'].items()} }"
    )
    print(
        f"best seed={seed}   total={total:.1f}  (density={dist[1]:.1f} terrain={dist[2]:.1f} "
        f"struct={dist[3]:.1f} object-distance={sp_mean:.1f} tiles)"
    )
    print(f"\n--- OBJECT-DISTANCE signature (median nearest-neighbour, tiles) ---")
    print(f"{'pair (A->nearest B)':32} {'real':>6} {'gen':>6} {'|d|':>6}")
    shown = [d for d in sp_diffs if d[0][0] != d[0][1]][:8] + [
        d for d in sp_diffs if d[0][0] == d[0][1]
    ][:5]
    for k, ra, ge, di in shown:
        print(f"  {k[0] + ' -> ' + k[1]:30} {ra:6.1f} {ge:6.1f} {di:6.1f}")
    print(f"\n{'purpose':16} {'real/1k':>9} {'gen/1k':>9}")
    for p in PURPOSES:
        print(f"  {p:14} {tgt['density'][p]:9.1f} {g['density'][p]:9.1f}")
    print(f"\n{'terrain':16} {'real%':>7} {'gen%':>7}")
    for t in TERRAINS:
        if tgt["terrain"][t] > 0.005 or g["terrain"][t] > 0.005:
            print(f"  code {t:11d} {100 * tgt['terrain'][t]:7.1f} {100 * g['terrain'][t]:7.1f}")
    s = tgt["struct"]
    gs = g["struct"]
    print(
        f"\nstructure   regions/1k real={s['regions_per_1000']:.1f} gen={gs['regions_per_1000']:.1f}  "
        f"depth real={s['max_depth']:.0f} gen={gs['max_depth']:.0f}  "
        f"portal_frac real={s['portal_frac']:.2f} gen={gs['portal_frac']:.2f}"
    )
    # render comparison
    R.faithful.save(fm, f"{ROOT}/out/deps_fit.json")
    R.faithful.to_vmap(fm, f"{ROOT}/out/deps_fit.vmap", name=f"fit_{name}")
    real_img = deps_showcase.render_real(m, 0, 8)
    gen_img = deps_render.render(fm, f"{ROOT}/out/render/_tmp_gen.png", scale=8)
    gen_img = Image.open(f"{ROOT}/out/render/_tmp_gen.png")
    combo = Image.new(
        "RGB",
        (real_img.width + gen_img.width + 14, max(real_img.height, gen_img.height)),
        (255, 255, 255),
    )
    combo.paste(real_img, (0, 0))
    combo.paste(gen_img, (real_img.width + 14, 0))
    out = f"{ROOT}/out/render/fit_{name.replace(' ', '_')}.png"
    combo.save(out)
    print(f"\nwrote {out}  (left: real | right: best generated match)")
