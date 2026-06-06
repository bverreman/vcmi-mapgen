"""Deterministic benchmark for the map generator -- the fitness function the
research gates check against. Three scores, each control-relative:

  COVERAGE     (obj 1): can we fit real maps? mean object-distance + %<=3 over N
                        corpus maps, vs a SHUFFLED control (same objects, random
                        positions) which must be far worse.
  VALIDITY     (obj 2): do generated maps make sense? pass-rate over K seeds of
                        {all reachable, per-purpose density within [0.5,2]x corpus}.
  RECONSTRUCT  (obj 3): best fit-distance to a named target map over a seed search,
                        vs the shuffled control.

Run:  python3 src/benchmark.py [--maps N] [--seeds K] [--target "All for One"]
Writes out/benchmark.json and prints a table. Stdlib + repo modules only.
"""
import sys, os, json, glob, random, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_fit as F, deps_realize as R, deps_spatial, traverse as T
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps"
OBJDIST_BAR = 3.0


def _corpus(n):
    paths = sorted(glob.glob(f"{MAPS}/**/*.h3m", recursive=True))
    if n and n < len(paths):                       # evenly-spaced slice for stability
        step = len(paths) / n
        paths = [paths[int(i * step)] for i in range(n)]
    return paths


def _shuffled_objdist(fm, sig_real):
    """Control: same generated objects, positions reassigned to random free tiles."""
    free = [(x, y) for y, row in enumerate(fm["terrain"][0])
            for x, c in enumerate(row) if c["t"] != 8]
    pts = []
    for o in fm["objects"]:
        if o.get("l", 0) != 0:
            continue
        x, y = random.choice(free)
        pts.append((x, y, R.TYPE2PURPOSE.get(o["type"], "?")))
    return deps_spatial.compare(sig_real, deps_spatial.signature(pts))[0]


def coverage(n, seeds):
    rows = []
    for p in _corpus(n):
        try:
            m, tgt, params, sig, best = F.fit(p, seeds=seeds)
        except Exception as e:
            print("  fit FAIL", os.path.basename(p), repr(e)[:50]); continue
        ctrl = _shuffled_objdist(best[5], sig)
        rows.append({"map": os.path.basename(p).replace(".h3m", ""),
                     "objdist": round(best[2], 2), "total": round(best[0], 1),
                     "control_objdist": round(ctrl, 2)})
    od = [r["objdist"] for r in rows]
    return {"n": len(rows),
            "mean_objdist": round(sum(od) / max(1, len(od)), 2),
            "pct_within_bar": round(100 * sum(1 for v in od if v <= OBJDIST_BAR) / max(1, len(od))),
            "mean_control_objdist": round(sum(r["control_objdist"] for r in rows) / max(1, len(rows)), 2),
            "rows": rows}


def validity(seeds):
    RATE = {p: 1000.0 * sum(v["counts"]) / sum(R.FAC["proportions"].values())
            for p, v in R.FAC["budget"].items()}
    npass = 0; rows = []
    for seed in range(seeds):
        fm, tree, em = R.realize(seed=seed)
        rr = T.traverse(fm, em)
        cnt = collections.Counter(R.TYPE2PURPOSE.get(o["type"], "?") for o in fm["objects"])
        tiles = sum(1 for lvl in fm["terrain"] for row in lvl for _ in row)
        bad = []
        for pur, rate in RATE.items():
            if rate < 0.2:
                continue
            got = 1000.0 * cnt.get(pur, 0) / tiles
            if not (0.5 * rate <= got <= 2.0 * rate):
                bad.append(pur)
        ok = rr["ok"] and not bad
        npass += ok
        rows.append({"seed": seed, "reachable": rr["ok"], "density_off": bad[:5]})
    return {"seeds": seeds, "pass_rate": round(npass / max(1, seeds), 2), "rows": rows}


def reconstruct(target, seeds):
    p = f"{MAPS}/{target}.h3m"
    if not os.path.isfile(p):
        return {"target": target, "error": "map not found"}
    m, tgt, params, sig, best = F.fit(p, seeds=seeds)
    ctrl = _shuffled_objdist(best[5], sig)
    return {"target": target, "best_total": round(best[0], 1),
            "best_objdist": round(best[2], 2), "control_objdist": round(ctrl, 2),
            "within_bar": best[2] <= OBJDIST_BAR}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--target", default="All for One")
    a = ap.parse_args()
    out = {"coverage": coverage(a.maps, a.seeds),
           "validity": validity(a.seeds),
           "reconstruct": reconstruct(a.target, a.seeds)}
    json.dump(out, open(f"{ROOT}/out/benchmark.json", "w"), indent=1)
    c, v, r = out["coverage"], out["validity"], out["reconstruct"]
    print("\n=== BENCHMARK ===")
    print(f"COVERAGE   : mean obj-dist {c['mean_objdist']} ({c['pct_within_bar']}% <= {OBJDIST_BAR}) "
          f"over {c['n']} maps   [control {c['mean_control_objdist']}]")
    print(f"VALIDITY   : pass-rate {v['pass_rate']} over {v['seeds']} seeds (reachable + density-in-tolerance)")
    print(f"RECONSTRUCT: '{r.get('target')}' best obj-dist {r.get('best_objdist')} total {r.get('best_total')} "
          f"[control {r.get('control_objdist')}]")
    print("wrote out/benchmark.json")
