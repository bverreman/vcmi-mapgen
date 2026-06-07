"""Discover which relations carry coherence (bottom-up).

For each (object-purpose P -> neighbour-purpose Q) fingerprint dimension, compare the
density-normalized nearest-Q distance in REAL maps vs SHUFFLED. A relation where real
is reliably TIGHTER than shuffled (positive effect size) is a coupling the designers
actually enforce -- a coherent relation -- discovered from data, not named by us.

Effect size = (mean_shuffled - mean_real) / pooled_std, per (P,Q).
"""

import sys, os, json, math, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coherence as CO
from coherence import PURPOSES


def main():
    names = CO.all_map_names()
    sample = names[::4]  # ~40 maps, evenly spaced
    # accumulate normalized distances per (P, Q) for real and shuffled
    real = collections.defaultdict(list)
    shuf = collections.defaultdict(list)
    for name in sample:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        pts = CO.objects_from_map(m)
        for P, fp in CO.fingerprints_of(pts):
            for qi, Q in enumerate(PURPOSES):
                real[(P, Q)].append(fp[qi])
        for s in range(2):
            sp = CO.shuffle_positions(pts, seed=s)
            for P, fp in CO.fingerprints_of(sp):
                for qi, Q in enumerate(PURPOSES):
                    shuf[(P, Q)].append(fp[qi])

    def stats(v):
        n = len(v); mu = sum(v) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in v) / n) + 1e-9
        return mu, sd, n

    rows = []
    for key in real:
        if real[key] == [] or shuf.get(key) in (None, []):
            continue
        mr, sr, nr = stats(real[key])
        ms, ss, _ = stats(shuf[key])
        pooled = math.sqrt((sr ** 2 + ss ** 2) / 2)
        eff = (ms - mr) / pooled            # >0: real tighter than random => coupling
        rows.append((eff, key[0], key[1], mr, ms, nr))

    # Persist per-(purpose,dim) weights = |effect size|, so the metric can emphasise
    # the relations that actually separate real from random.
    weights = {P: [0.0] * len(PURPOSES) for P in PURPOSES}
    for eff, P, Q, mr, ms, nr in rows:
        weights[P][PURPOSES.index(Q)] = abs(eff)
    json.dump({"purposes": PURPOSES, "weights": weights},
              open(f"{ROOT}/out/coherence_weights.json", "w"), indent=1)

    rows.sort(reverse=True)
    print("TOP coherent couplings (real tighter than random):")
    print(f"  {'obj':14s} -> {'neighbour':14s}  effect  real_norm  shuf_norm")
    for eff, P, Q, mr, ms, nr in rows[:18]:
        if P == Q:
            tag = " (self-clustering)"
        else:
            tag = ""
        print(f"  {P:14s} -> {Q:14s}  {eff:+.3f}   {mr:.3f}     {ms:.3f}{tag}")
    print("\nBOTTOM (real LOOSER than random / anti-coupling):")
    for eff, P, Q, mr, ms, nr in rows[-6:]:
        print(f"  {P:14s} -> {Q:14s}  {eff:+.3f}   {mr:.3f}     {ms:.3f}")


if __name__ == "__main__":
    main()
