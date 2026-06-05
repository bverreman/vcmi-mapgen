"""Per-purpose density residuals for one target map.

Fits the generator to a real map, then prints the per-purpose object-density
(objects per 1000 tiles) for real vs best-generated, sorted by absolute residual.
Use it to find which purposes drive the density distance in deps_fit.py and to
check whether a scatter/guard tweak closed the gap.

    python3 src/deps_density.py "King of Pain"        # by map name
    python3 src/deps_density.py /full/path/to/Map.h3m # by path
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_fit as F

MAPS = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps"


def residuals(path, seeds=6):
    m, tgt, params, sig, best = F.fit(path, seeds=seeds)
    total, dist, sp_mean, sp_diffs, seed, fm, tree, g, sig_gen = best
    rows = []
    for p in F.PURPOSES:
        r, ge = tgt["density"][p], g["density"][p]
        rows.append((abs(r - ge), p, r, ge))
    rows.sort(reverse=True)
    return best, rows


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "King of Pain"
    path = arg if os.path.exists(arg) else f"{MAPS}/{arg}.h3m"
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    best, rows = residuals(path, seeds)
    total, dist, sp_mean = best[0], best[1], best[2]
    name = os.path.basename(path).replace(".h3m", "")
    print(f"=== density residuals: '{name}' (best seed={best[4]}) ===")
    print(f"total={total:.1f}  density={dist[1]:.1f}  terrain={dist[2]:.1f}  "
          f"struct={dist[3]:.1f}  object-distance={sp_mean:.1f}")
    print(f"\n{'purpose':16} {'real/1k':>9} {'gen/1k':>9} {'|resid|':>9}")
    for d, p, r, ge in rows:
        flag = "  <-- worst" if d == rows[0][0] and d > 1 else ""
        print(f"  {p:14} {r:9.1f} {ge:9.1f} {d:9.1f}{flag}")
