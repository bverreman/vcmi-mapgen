"""Corpus vegetation statistics for the marked-point-process layer (spec M0).

Implements docs/specs/marked-point-process-generation.md §2.5–2.6: one pass over the corpus
extracts, per terrain, the DECORATION anchor pattern —

  - first-order intensity  lam[cat][edge_bin]   (anchors per tile, by distance-to-rim bin),
  - multitype pair correlation  g[a][b][r]      (Chebyshev rings r = 0..RMAX; r=0 measures how
    often footprints STACK — overlap is corpus-legal for vegetation, so it is learned, not banned),
  - the mark mix              anim_w[cat][anim] (corpus frequency of each sprite within a category
    — identity itself always resolves through the ontology, the corpus contributes counts only),
  - the budget target         veg_blocked_frac  (fraction of zone tiles under a decoration's
    blocking cell — the Boolean-model coverage the sampler must reproduce),
  - corpus run-length histogram of the veg-only open field (the M1 validation yardstick).

Categories are the ontology's decoration types (`veg_categories`); water features
(`kit.terrain_lookup.EXCLUDE_DECOR_TYPES`) are dropped everywhere. Cached per terrain in
``data/pp/veg_<terrain>.json``.

    uv run python -m vcmi_mapgen.steps.vegetation.stats --report grass
"""
import argparse
import collections
import json
import math
import os

from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen.kit.terrain_lookup import TNAME, EXCLUDE_DECOR_TYPES
from vcmi_mapgen.kit.segmentation import _segment_level
from vcmi_mapgen import zone_field as ZF
from vcmi_mapgen.kit.paths import project_root

ROOT = project_root()
PP_DIR = str(ROOT / "data" / "pp")
RMAX = 6                        # pair-correlation rings 0..RMAX (Chebyshev)
EBINS = ZF.EBINS                # edge-distance bins, shared with the field module
MIN_AREA = 60                   # same zone-size floor as the field learner
CELL = 6                        # coarse-cell size for the overdispersion (Cox field) statistic
LAND = ("dirt", "sand", "grass", "snow", "swamp", "rough", "subterr", "lava")


def _cat_names():
    return ON.veg_categories()


def _anchors_of_zone(fm, ts):
    """[(x, y, cat_name, anim)] for DECORATION objects anchored inside the zone, excluded
    water-feature categories dropped. Category via the ontology (single source of truth)."""
    cats = _cat_names()
    out = []
    for o in fm["objects"]:
        if o.get("l", 0) != 0 or (o["x"], o["y"]) not in ts:
            continue
        if OR.purpose_of(o) != "DECORATION":
            continue
        anim = (o.get("animation") or "").lower().removesuffix(".def")
        ci = ON.category_of(anim)
        if ci is None:
            continue
        cat = cats[ci]
        if cat in EXCLUDE_DECOR_TYPES:
            continue
        out.append((o["x"], o["y"], cat, anim))
    return out


def _ring_offsets(r):
    """The 8r lattice offsets at Chebyshev distance exactly r (r >= 1)."""
    offs = []
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if max(abs(dx), abs(dy)) == r:
                offs.append((dx, dy))
    return offs


_OFFS = {r: _ring_offsets(r) for r in range(1, RMAX + 1)}


def _pair_denominator(ts):
    """D[r] = number of ORDERED in-zone tile pairs at Chebyshev distance exactly r (numpy shifts).
    This is the translation-corrected normalizer of the lattice pair-correlation estimator."""
    import numpy as np
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    x0, y0 = min(xs), min(ys)
    W = max(xs) - x0 + 1 + 2 * RMAX
    H = max(ys) - y0 + 1 + 2 * RMAX
    m = np.zeros((H, W), dtype=bool)
    for (x, y) in ts:
        m[y - y0 + RMAX, x - x0 + RMAX] = True
    D = [len(ts)]                                     # r=0: a tile pairs with itself
    for r in range(1, RMAX + 1):
        tot = 0
        for dx, dy in _OFFS[r]:
            tot += int(np.count_nonzero(m & np.roll(np.roll(m, dy, axis=0), dx, axis=1)))
        D.append(tot)
    return D


def mine(nmaps=159, force=False):
    """One corpus pass -> per-terrain stats dict; cached in data/pp/veg_<terrain>.json."""
    os.makedirs(PP_DIR, exist_ok=True)
    paths = {t: os.path.join(PP_DIR, f"veg_{t}.json") for t in LAND}
    if not force and all(os.path.exists(p) for p in paths.values()):
        return {t: json.load(open(p)) for t, p in paths.items()}

    acc = {t: {"tiles_per_ebin": [0] * EBINS,
               "anch": collections.defaultdict(lambda: [0] * EBINS),
               "pairN": collections.defaultdict(lambda: [0] * (RMAX + 1)),
               "pairD": [0] * (RMAX + 1),
               "anim_w": collections.defaultdict(collections.Counter),
               "blocked": 0, "tiles": 0, "nzones": 0, "nanch": 0,
               "cells_n": 0, "cells_sum": 0, "cells_sum2": 0,
               "runs": collections.Counter()}
           for t in LAND}

    names = OR.all_map_names()[:nmaps]
    for i, nm in enumerate(names):
        try:
            fm = OR.load_faithful(nm)
        except Exception:
            continue
        zones, zl, _ = _segment_level(fm["terrain"][0])
        for zid, z in zones.items():
            terr = TNAME.get(z["terrain_type"])
            if terr not in acc or z["area"] < MIN_AREA:
                continue
            a = acc[terr]
            ts = set(z["tiles_set"])
            anchors = _anchors_of_zone(fm, ts)
            edist = ZF.edge_dist(ts)

            a["nzones"] += 1
            a["tiles"] += len(ts)
            a["nanch"] += len(anchors)
            for t in ts:
                a["tiles_per_ebin"][min(edist[t], EBINS - 1)] += 1
            pos = collections.defaultdict(collections.Counter)   # (x,y) -> cat counts
            for (x, y, cat, anim) in anchors:
                a["anch"][cat][min(edist[(x, y)], EBINS - 1)] += 1
                a["anim_w"][cat][anim] += 1
                pos[(x, y)][cat] += 1

            # pair counts: ordered (a,b) pairs per ring, incl. r=0 stacking
            for p, ca in pos.items():
                for cat_a, n_a in ca.items():
                    for cat_b, n_b in ca.items():
                        n = n_a * n_b - (n_a if cat_a == cat_b else 0)
                        if n > 0:
                            a["pairN"][f"{cat_a}|{cat_b}"][0] += n
                for r in range(1, RMAX + 1):
                    for dx, dy in _OFFS[r]:
                        cb = pos.get((p[0] + dx, p[1] + dy))
                        if not cb:
                            continue
                        for cat_a, n_a in ca.items():
                            for cat_b, n_b in cb.items():
                                a["pairN"][f"{cat_a}|{cat_b}"][r] += n_a * n_b
            D = _pair_denominator(ts)
            for r in range(RMAX + 1):
                a["pairD"][r] += D[r]

            # coarse-cell anchor counts -> overdispersion (Fisher index) for the Cox field:
            # only cells FULLY inside the zone, so cell area is constant
            xs = [x for x, _ in ts]; ys2 = [y for _, y in ts]
            for cy0 in range(min(ys2), max(ys2) - CELL + 2, CELL):
                for cx0 in range(min(xs), max(xs) - CELL + 2, CELL):
                    cell_tiles = [(cx0 + dx, cy0 + dy) for dy in range(CELL) for dx in range(CELL)]
                    if all(t in ts for t in cell_tiles):
                        n = sum(sum(pos[t].values()) for t in cell_tiles if t in pos)
                        a["cells_n"] += 1
                        a["cells_sum"] += n
                        a["cells_sum2"] += n * n

            # budget target + corpus veg-only run lengths
            blocked = set()
            for (x, y, _c, anim) in anchors:
                for cx, cy, blk in OR.mask_cells(ON.mask_of(anim), x, y):
                    if blk and (cx, cy) in ts:
                        blocked.add((cx, cy))
            a["blocked"] += len(blocked)
            a["runs"].update(ZF.run_lengths(ts, ts - blocked))
        if (i + 1) % 40 == 0:
            print(f"  mined {i + 1}/{len(names)} maps")

    out = {}
    for terr, a in acc.items():
        tot_tiles = max(a["tiles"], 1)
        lam = {}
        for cat, per_e in a["anch"].items():
            lam[cat] = [(per_e[e] + 0.25) / (a["tiles_per_ebin"][e] + 0.5)
                        for e in range(EBINS)]                       # Laplace-smoothed
        lam_tot = {cat: sum(a["anch"][cat]) / tot_tiles for cat in a["anch"]}
        g = {}
        for key, N in a["pairN"].items():
            ca, cb = key.split("|")
            la, lb = lam_tot.get(ca, 0), lam_tot.get(cb, 0)
            if la <= 0 or lb <= 0:
                continue
            g[key] = [(N[r] / a["pairD"][r]) / (la * lb) if a["pairD"][r] else 0.0
                      for r in range(RMAX + 1)]
        mean_blk = {cat: (sum(sum(1 for row in ON.mask_of(an) for ch in row if ch in "BX") * c
                              for an, c in cnt.items()) / max(sum(cnt.values()), 1))
                    for cat, cnt in a["anim_w"].items()}
        runs_tot = sum(a["runs"].values()) or 1
        out[terr] = {
            "terrain": terr,
            "nzones": a["nzones"], "tiles": a["tiles"], "nanchors": a["nanch"],
            "tiles_per_ebin": a["tiles_per_ebin"],
            "anch": {c: v for c, v in a["anch"].items()},
            "lam": lam,
            "lam_tot": lam_tot,
            "g": g,
            "pairN": {k: v for k, v in a["pairN"].items()},
            "pairD": a["pairD"],
            "anim_w": {c: dict(v) for c, v in a["anim_w"].items()},
            "mean_blk_cells": mean_blk,
            "cell": {"size": CELL, "n": a["cells_n"], "sum": a["cells_sum"],
                     "sum2": a["cells_sum2"]},
            "veg_blocked_frac": a["blocked"] / tot_tiles,
            "runs": {str(k): v / runs_tot for k, v in sorted(a["runs"].items())[:12]},
        }
        json.dump(out[terr], open(paths[terr], "w"))
    print(f"mined {len(names)} maps -> {PP_DIR}/veg_<terrain>.json")
    return out


def load(terrain):
    p = os.path.join(PP_DIR, f"veg_{terrain}.json")
    if not os.path.exists(p):
        mine()
    return json.load(open(p))


def theta(stats, min_pairs=30, lo=-1.5, hi=2.0):
    """Pairwise log-potentials  theta[a][b][r] = clip(log ghat_ab(r))  (spec §2.6 counting fit).
    Rings with fewer than `min_pairs` observed pairs are neutral (0) — too sparse to trust."""
    th = {}
    for key, gr in stats["g"].items():
        N = stats["pairN"][key]
        th[key] = [max(lo, min(hi, math.log(gr[r]))) if N[r] >= min_pairs and gr[r] > 0 else 0.0
                   for r in range(RMAX + 1)]
    return th


def theta_local(stats, rint=2, base_r=4, min_pairs=30, lo=-1.5, hi=1.5):
    """LOCAL pair potentials, background-normalized:  theta[a][b][r] = log(g(r) / g(base_r)),
    r <= rint. The raw g(r) > 1 at ALL ranges because zones mix dense forest masses with
    clearings (large-scale inhomogeneity); fitting that as pair attraction makes the Gibbs
    process explosive. Dividing by the mid-range g isolates the genuinely LOCAL clumping /
    stacking excess; the large-scale part is carried by the Cox log-field (`cox_sigma`)."""
    th = {}
    for key, gr in stats["g"].items():
        N = stats["pairN"][key]
        base = gr[base_r] if (N[base_r] >= min_pairs and gr[base_r] > 0) else None
        row = []
        for r in range(rint + 1):
            if base and N[r] >= min_pairs and gr[r] > 0:
                row.append(max(lo, min(hi, math.log(gr[r] / base))))
            else:
                row.append(0.0)
        th[key] = row
    return th


def cox_sigma(stats):
    """Log-field std of the Cox modulation, fitted from coarse-cell overdispersion: for a
    log-Gaussian Cox process the Fisher index of cell counts is  F = 1 + m(e^{s^2}-1)  with
    m the mean count, so  s^2 = ln(1 + (F-1)/m)  (Møller & Waagepetersen 2004, ch. 5)."""
    c = stats.get("cell")
    if not c or c["n"] < 10 or c["sum"] <= 0:
        return 0.0
    m = c["sum"] / c["n"]
    var = c["sum2"] / c["n"] - m * m
    F = var / m
    return math.sqrt(max(0.0, math.log(1.0 + max(0.0, F - 1.0) / m)))


def report(terrain):
    st = load(terrain)
    print(f"== {terrain}: zones={st['nzones']} tiles={st['tiles']} anchors={st['nanchors']} "
          f"(density {st['nanchors'] / max(st['tiles'], 1):.3f}/tile) "
          f"veg_blocked_frac={st['veg_blocked_frac']:.3f}")
    lam_tot = st["lam_tot"]
    top = sorted(lam_tot, key=lam_tot.get, reverse=True)[:8]
    print("  intensity by edge bin (anchors/tile):")
    for cat in top:
        row = " ".join(f"{v:.3f}" for v in st["lam"][cat])
        print(f"    {cat:<18} tot={lam_tot[cat]:.4f}  by-ebin {row}")
    th = theta(st)
    print("  pair correlation g(r), r=0..6  (same-category):")
    for cat in top[:6]:
        key = f"{cat}|{cat}"
        if key in st["g"]:
            row = " ".join(f"{v:6.2f}" for v in st["g"][key])
            print(f"    {cat:<18} {row}")
    print("  corpus veg-only open run-length fractions:", st["runs"])
    return st, th


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", metavar="TERRAIN", default=None)
    ap.add_argument("--regen", action="store_true", help="force a fresh corpus pass")
    ap.add_argument("--nmaps", type=int, default=159)
    args = ap.parse_args()
    if args.regen or args.report is None:
        mine(nmaps=args.nmaps, force=args.regen)
    if args.report:
        report(args.report)


if __name__ == "__main__":
    main()
