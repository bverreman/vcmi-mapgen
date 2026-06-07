"""Produce ONE coherent, playable VCMI map.

Pipeline: generate N seeds with the reachability-validated realize() generator (the
one that passed G2: every zone/town/mine reachable on foot, density in band), keep
only the seeds that pass the real BFS traverse, score each with the learned coherence
metric, and emit the most-coherent reachable map as an editor-loadable .vmap.
"""

import sys, os, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_realize as R
import traverse as T
import faithful
import coherence as CO
from deps_realize import TYPE2PURPOSE


def fm_points(fm):
    """fm objects -> [(l,x,y,purpose)] for coherence scoring."""
    out = []
    for o in fm["objects"]:
        p = TYPE2PURPOSE.get(o.get("type"))
        if p in CO.PURPOSES:
            out.append((o.get("l", 0), o["x"], o["y"], p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--out", default=f"{ROOT}/out/Coherent-Map.vmap")
    args = ap.parse_args()

    print("fitting coherence model on corpus...")
    model = CO.fit(CO.all_map_names()[::2])  # ~80 maps for mean/std

    best = None
    rows = []
    for seed in range(args.seeds):
        fm, tree, em = R.realize(seed=seed)
        rr = T.traverse(fm, em)
        pts = fm_points(fm)
        score = CO.score_objects(model, pts)
        shuf = sum(CO.score_objects(model, CO.shuffle_positions(pts, seed=s))
                   for s in range(2)) / 2 if pts else None
        rows.append((seed, rr["ok"], score, shuf))
        flag = "reachable" if rr["ok"] else "UNREACHABLE"
        print(f"  seed {seed:2d}: {flag:11s}  coherence={score:+.3f}  (own-shuffle {shuf:+.3f}, Δ={score-shuf:+.3f})")
        if rr["ok"] and (best is None or score > best[1]):
            best = (seed, score, fm, shuf)

    if best is None:
        print("No reachable map produced — increase --seeds.")
        return
    seed, score, fm, shuf = best
    corpus_mean = sum(r[2] for r in rows) / len(rows)
    faithful.to_vmap(fm, args.out, name=f"Coherent (seed {seed})")
    n_obj = sum(1 for o in fm["objects"] if o.get("type"))
    print(f"\nBEST reachable map: seed {seed}")
    print(f"  coherence {score:+.3f}  vs its own shuffle {shuf:+.3f}  (Δ={score-shuf:+.3f} — higher = more structured than random)")
    print(f"  {n_obj} objects, reachability OK")
    print(f"  wrote: {args.out}")


if __name__ == "__main__":
    main()
