"""Object-distance signature: capture the SPATIAL arrangement of objects, not just
their counts. For each ordered pair of purposes (A,B), the median over A-objects
of the euclidean distance to the nearest B-object.

  - diagonal (A,A): clustering of a purpose (small = clumped, large = dispersed)
  - off-diagonal (A,B): spatial coupling (e.g. REWARD->GUARD small == guarded loot)

Comparing two maps' signatures measures whether the generated map places objects
in the same euclidean relationships as the real one -- the "adjacency distribution".
"""
import math, statistics, collections

PURPOSES = ["TOWN", "GUARD", "MINE", "DWELLING", "BANK", "REWARD_PICKUP",
            "RESOURCE_PILE", "TRANSPORT", "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL"]


def signature(pts, purposes=PURPOSES):
    """pts: list of (x, y, purpose). Returns {(A,B): median nearest-neighbour dist}."""
    by = collections.defaultdict(list)
    for (x, y, p) in pts:
        if p in purposes:
            by[p].append((x, y))
    sig = {}
    for A in purposes:
        AA = by.get(A)
        if not AA:
            continue
        for B in purposes:
            BB = by.get(B)
            if not BB:
                continue
            ds = []
            for (ax, ay) in AA:
                best = None
                for (bx, byy) in BB:
                    if A == B and bx == ax and byy == ay:
                        continue
                    d = (ax - bx) ** 2 + (ay - byy) ** 2
                    if best is None or d < best:
                        best = d
                if best is not None:
                    ds.append(math.sqrt(best))
            if ds:
                sig[(A, B)] = statistics.median(ds)
    return sig


def compare(sa, sb):
    """Mean absolute nearest-neighbour-distance error (tiles) over shared pairs,
    plus the most divergent pairs."""
    keys = set(sa) & set(sb)
    if not keys:
        return 0.0, []
    diffs = [(k, sa[k], sb[k], abs(sa[k] - sb[k])) for k in keys]
    mean = sum(d[3] for d in diffs) / len(diffs)
    diffs.sort(key=lambda d: -d[3])
    return mean, diffs
