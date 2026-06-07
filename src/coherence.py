"""Bottom-up coherence metric: is each object JUSTIFIED by what's around it?

Coherence is not a global histogram (that's the object-distance signature, which a
shuffled map can match in aggregate). It is a PER-OBJECT property: a reward with a
guard on its approach is coherent; the same reward alone in open field is not. We
learn, unsupervised, the distribution of each object's LOCAL RELATIONAL CONTEXT from
the corpus, then score how typical an object's context is.

Per-object fingerprint (within its own level):
  - for each gameplay purpose Q: euclidean distance to the nearest Q-object (capped)
  - distance to the nearest TOWN
This is the relational neighbourhood that design rules act on (guard<->reward,
mine<->guard, dwelling<->town, value<->distance-to-town). We do NOT name those rules
(bottom-up): we learn the joint cloud of fingerprints per purpose and score an
object by how typical its fingerprint is (diagonal-Gaussian z-score density).

A map's coherence = mean per-object typicality. The metric is validated control-
relative: real maps must score higher than the SHUFFLED control (same objects, random
positions). If shuffling barely changes the score, the metric isn't measuring
structure and must be fixed before any generator is built.
"""

import sys, os, json, glob, math, random, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON

PURPOSES = [
    "TOWN", "GUARD", "MINE", "DWELLING", "BANK", "REWARD_PICKUP", "RESOURCE_PILE",
    "TRANSPORT", "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL", "QUEST_GATE",
]
CAP = 40.0  # distance cap (tiles): "no Q within 40" saturates here


def objects_from_map(m):
    """[(l, x, y, purpose)] for gameplay objects."""
    out = []
    for o in m["objects"]:
        p = ON.resolve(o["class"], o["subclass"]).get("purpose", "UNKNOWN")
        if p in PURPOSES:
            out.append((o.get("l", 0), o["x"], o["y"], p))
    return out


def objects_from_tokengrid(grid):
    """[(l, x, y, purpose)] from a tokenized obj grid (purpose per cell)."""
    out = []
    for l in range(grid["levels"]):
        for y in range(grid["H"]):
            for x in range(grid["W"]):
                tok = grid["obj"][l][y][x]
                if tok in PURPOSES:
                    out.append((l, x, y, tok))
    return out


def _expected_nn(pts):
    """Per-(level,purpose) expected nearest-neighbour distance under random placement
    at the observed density: E[d] ~= 0.5*sqrt(A/n). Used to density-normalize so the
    fingerprint measures STRUCTURE, not how crowded the map is."""
    by = collections.defaultdict(lambda: collections.Counter())  # level -> purpose count
    lvls = set()
    for (l, x, y, p) in pts:
        by[l][p] += 1
        lvls.add(l)
    A = 72.0 * 72.0  # corpus maps are 72x72 per level
    exp = {}
    for l in lvls:
        for q in PURPOSES:
            n = by[l][q]
            exp[(l, q)] = 0.5 * math.sqrt(A / n) if n > 0 else CAP
    return exp


def _fingerprint(pts, i, exp):
    """Density-normalized relational fingerprint of object i (same-level only):
    for each purpose Q, (nearest Q distance) / (expected NN distance at Q's density)."""
    l0, x0, y0, _ = pts[i]
    nearest = {q: None for q in PURPOSES}
    for j, (l, x, y, p) in enumerate(pts):
        if j == i or l != l0:
            continue
        d = math.hypot(x - x0, y - y0)
        if nearest[p] is None or d < nearest[p]:
            nearest[p] = d
    out = []
    for q in PURPOSES:
        e = exp.get((l0, q), CAP)
        if nearest[q] is None:
            out.append(3.0)  # no Q on this level: maximally "far" in normalized units
        else:
            out.append(min(nearest[q] / e, 3.0))
    return out


def fingerprints_of(pts):
    """(purpose, density-normalized fingerprint-vector) for every object."""
    exp = _expected_nn(pts)
    return [(pts[i][3], _fingerprint(pts, i, exp)) for i in range(len(pts))]


def fit(map_names):
    """Per-purpose diagonal-Gaussian over fingerprints (mean, std per dimension)."""
    banks = collections.defaultdict(list)
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        for p, fp in fingerprints_of(objects_from_map(m)):
            banks[p].append(fp)
    model = {}
    D = len(PURPOSES)
    for p, rows in banks.items():
        n = len(rows)
        mu = [sum(r[k] for r in rows) / n for k in range(D)]
        var = [sum((r[k] - mu[k]) ** 2 for r in rows) / n for k in range(D)]
        sigma = [math.sqrt(v) + 1e-6 for v in var]
        model[p] = {"mu": mu, "sigma": sigma, "n": n}
    return model


# Optional discriminative weights (from coherence_discover.py): emphasise the
# relations that separate real from shuffled. Falls back to uniform if absent.
_WPATH = os.path.join(ROOT, "out", "coherence_weights.json")
_WEIGHTS = json.load(open(_WPATH))["weights"] if os.path.exists(_WPATH) else None


def score_object(model, purpose, fp):
    """Typicality of one object: negative weighted-mean squared z-score (0 = typical).
    Dimensions are weighted by how strongly that relation separates real from random,
    so coherence is dominated by the couplings designers actually enforce."""
    m = model.get(purpose)
    if not m:
        return None
    w = (_WEIGHTS or {}).get(purpose)
    z2 = [((fp[k] - m["mu"][k]) / m["sigma"][k]) ** 2 for k in range(len(fp))]
    if w and sum(w) > 0:
        return -sum(wk * zk for wk, zk in zip(w, z2)) / sum(w)
    return -sum(z2) / len(z2)


def score_objects(model, pts):
    """Mean per-object coherence over a map's object list."""
    if not pts:
        return None
    s = [score_object(model, p, fp) for p, fp in fingerprints_of(pts)]
    s = [v for v in s if v is not None]
    return sum(s) / len(s) if s else None


def shuffle_positions(pts, seed=0):
    """Within-map shuffled control: keep purposes + the set of occupied positions,
    randomly reassign which object sits where (per level)."""
    rng = random.Random(seed)
    bylvl = collections.defaultdict(list)
    for (l, x, y, p) in pts:
        bylvl[l].append((x, y, p))
    out = []
    for l, items in bylvl.items():
        positions = [(x, y) for (x, y, _) in items]
        purposes = [p for (_, _, p) in items]
        rng.shuffle(positions)
        for (x, y), p in zip(positions, purposes):
            out.append((l, x, y, p))
    return out


def all_map_names():
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(f"{ROOT}/out/maps/*.json"))


if __name__ == "__main__":
    names = all_map_names()
    eval_names = names[::8][:16]                 # evenly-spaced eval slice
    fit_names = [n for n in names if n not in set(eval_names)]
    print(f"fit on {len(fit_names)} maps; eval on {len(eval_names)} maps")
    model = fit(fit_names)
    print("per-purpose fingerprint counts:", {p: model[p]["n"] for p in sorted(model)})

    real_s, shuf_s = [], []
    for name in eval_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        pts = objects_from_map(m)
        r = score_objects(model, pts)
        sh = sum(score_objects(model, shuffle_positions(pts, seed=s)) for s in range(3)) / 3
        real_s.append(r); shuf_s.append(sh)
        print(f"  {name[:34]:34s} real={r:7.3f}  shuffled={sh:7.3f}  Δ={r-sh:+.3f}")
    mr = sum(real_s) / len(real_s); ms = sum(shuf_s) / len(shuf_s)
    print(f"\nMEAN  real={mr:.3f}  shuffled={ms:.3f}  separation Δ={mr-ms:+.3f}")
    wins = sum(1 for r, s in zip(real_s, shuf_s) if r > s)
    print(f"real beats shuffled on {wins}/{len(eval_names)} maps")
