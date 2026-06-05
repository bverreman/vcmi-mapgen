"""Multi-map fit report: track fit distance across a slice of the corpus.

Fits the generator to N real maps and writes a markdown table to
`out/fit_report.md` (total / density / terrain / struct / object-distance per
map, plus column averages). Run it before/after a change to catch regressions
that a single held-out map (Dawn of War in verify.sh) would miss.

    python3 src/deps_report.py            # default N=12 maps, seeds=4
    python3 src/deps_report.py 20 6       # 20 maps, 6 seeds each
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_fit as F

MAPS = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "fit_report.md")


def sample_maps(n):
    """Deterministic, evenly-spaced slice of the sorted corpus (stable across runs)."""
    paths = sorted(f for f in os.listdir(MAPS) if f.endswith(".h3m"))
    if n >= len(paths):
        return [os.path.join(MAPS, p) for p in paths]
    step = len(paths) / n
    idx = sorted(set(int(i * step) for i in range(n)))
    return [os.path.join(MAPS, paths[i]) for i in idx]


def report(n=12, seeds=4):
    rows = []
    for path in sample_maps(n):
        name = os.path.basename(path).replace(".h3m", "")
        try:
            m, tgt, params, sig, best = F.fit(path, seeds=seeds)
            total, d, sp_mean = best[0], best[1], best[2]
            rows.append((name, total, d[1], d[2], d[3], sp_mean, best[4]))
            print(f"  {name:28} total={total:6.1f} obj-dist={sp_mean:.2f}")
        except Exception as e:                         # skip unparseable corpus maps
            print(f"  {name:28} SKIP ({type(e).__name__}: {e})")
    return rows


def write_md(rows, seeds):
    n = len(rows)
    cols = [1, 2, 3, 4, 5]                              # total, density, terrain, struct, obj-dist
    avg = [sum(r[c] for r in rows) / n for c in cols] if n else [0] * 5
    worst = max(rows, key=lambda r: r[5]) if rows else None
    lines = [
        "# Fit report",
        "",
        f"Generator fit to {n} corpus maps (seeds={seeds} each). "
        "Lower is better; **object-distance** is the core quality bar (gate: <= 3).",
        "",
        "| map | total | density | terrain | struct | obj-dist | seed |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, total, dens, terr, st, sp, seed in rows:
        lines.append(f"| {name} | {total:.1f} | {dens:.1f} | {terr:.1f} "
                     f"| {st:.1f} | {sp:.2f} | {seed} |")
    lines.append(f"| **average** | **{avg[0]:.1f}** | **{avg[1]:.1f}** "
                 f"| **{avg[2]:.1f}** | **{avg[3]:.1f}** | **{avg[4]:.2f}** | |")
    lines.append("")
    if worst:
        lines.append(f"Worst object-distance: **{worst[0]}** ({worst[5]:.2f}).")
        lines.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines))
    return avg


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    print(f"=== fit report: {n} maps, seeds={seeds} ===")
    rows = report(n, seeds)
    avg = write_md(rows, seeds)
    print(f"\nwrote {OUT}")
    print(f"averages: total={avg[0]:.1f} density={avg[1]:.1f} terrain={avg[2]:.1f} "
          f"struct={avg[3]:.1f} object-distance={avg[4]:.2f}")
