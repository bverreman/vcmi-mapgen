"""Distil a corpus-average prior the generator uses BY DEFAULT (no target needed):
per-purpose density, water fraction, land-biome proportions, and the object-distance
signature (median nearest-neighbour distance per purpose pair). With this, a plain
generate produces a corpus-typical map; passing a specific target still overrides it.
"""

import sys, os, json, glob, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m, deps, vcmi_ids, deps_realize as R, deps_spatial

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def points_real(m):
    pts = []
    for o in m.objects:
        if o.l != 0:
            continue
        r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        if r:
            pts.append((o.x, o.y, R.TYPE2PURPOSE.get(r[0], "?")))
    return pts


def build(paths):
    dens = collections.defaultdict(list)
    terr_frac = collections.defaultdict(list)
    water = []
    sig_pairs = collections.defaultdict(list)
    n = 0
    for pth in paths:
        try:
            m = h3m.parse_file(pth)
        except Exception:
            continue
        tiles = m.width * m.height * (2 if m.two_level else 1)
        tc = collections.Counter()
        for lvl in m.terrain:
            for row in lvl:
                for t in row:
                    tc[t.terrain] += 1
        for code in range(10):
            terr_frac[code].append(tc.get(code, 0) / tiles)
        water.append(tc.get(8, 0) / tiles)
        pc = collections.Counter()
        for o in m.objects:
            r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
            if r:
                pc[R.TYPE2PURPOSE.get(r[0], "?")] += 1
        for p in R.TYPE2PURPOSE.values():
            pass
        for p, c in pc.items():
            dens[p].append(1000.0 * c / tiles)
        sig = deps_spatial.signature(points_real(m))
        for k, v in sig.items():
            sig_pairs[k].append(v)
        n += 1

    # land-biome proportions (codes 0..7), renormalised
    land = {t: statistics.mean(terr_frac[t]) for t in range(8)}
    ls = sum(land.values()) or 1
    biome_weights = {t: land[t] / ls for t in range(8)}
    prior = {
        "n_maps": n,
        "density": {p: round(statistics.mean(v), 3) for p, v in dens.items() if p != "?"},
        "water_frac": round(statistics.mean(water), 3),
        "biome_weights": {str(t): round(w, 3) for t, w in biome_weights.items()},
        "sig": [[a, b, round(statistics.median(v), 1)] for (a, b), v in sig_pairs.items()],
    }
    out = f"{ROOT}/out/spatial_prior.json"
    json.dump(prior, open(out, "w"), indent=1)
    return prior, out


if __name__ == "__main__":
    paths = sorted(
        glob.glob(
            "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m",
            recursive=True,
        )
    )
    prior, out = build(paths)
    print(f"prior over {prior['n_maps']} maps -> {out}")
    print(f"  water_frac={prior['water_frac']}  biomes={prior['biome_weights']}")
    print(f"  density purposes: {len(prior['density'])}   signature pairs: {len(prior['sig'])}")
