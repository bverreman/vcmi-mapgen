"""Terrain-only archetype classifier, archetype assignment, and per-archetype rules.

At generation time there are no objects yet, so a sub-zone's archetype must be
predicted from TERRAIN features alone (terrain type, size, openness, shape,
connectivity). Two things are NOT predicted but decided by explicit rules:

  * MAIN (towns): a map has a fixed number of player towns, so MAIN is assigned to
    the N most town-suitable sub-zones (large, open, mutually spread), not predicted.
  * TOWN_OUTSKIRTS: assigned to sub-zones adjacent to a MAIN zone.

The remaining sub-zones get a non-town archetype (WILDERNESS / TREASURE / ECONOMY /
SHRINE) sampled from a multinomial logistic-regression classifier trained on the
corpus (out/zone_archetypes.json). seed=0 takes the argmax; seed=N samples.

Per-archetype rules (count distribution + radial loot profile) are read straight
from the clustering artifact — the cluster statistics ARE the rules.

Artefacts:
    out/archetype_clf.pkl   classifier weights + feature config + label map

Usage:
    uv run python src/archetype_model.py            # train + report
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ARCHETYPE_PATH = os.path.join(ROOT, "out", "zone_archetypes.json")
CLF_PATH = os.path.join(ROOT, "out", "archetype_clf.pkl")

# Index of dist_town inside the artifact's terrain_feat vector (circular — dropped
# from the generation-time classifier). Layout: terr_0..9 (0-9), area_log (10),
# dist_town (11), n_adj (12), choke_density (13), openness (14), elongation (15).
DIST_TOWN_IDX = 11

# Non-town archetypes the classifier chooses among (MAIN / TOWN_OUTSKIRTS handled
# by explicit rules).
TOWN_LABELS = {"MAIN", "TOWN_OUTSKIRTS"}


# ---------------------------------------------------------------------------
# Generation-time terrain features (must match artifact terrain_feat minus dist_town)
# ---------------------------------------------------------------------------

def gen_terrain_feat(zone: dict, sf: np.ndarray) -> list:
    """15-dim terrain-only feature vector for a zone, EXCLUDING dist_town.

    Order: terr_0..9, area_log, n_adj, choke_density, openness, elongation.
    `sf` is the static feature array from terrain_segment.compute_static_features
    (used only for the per-tile openness channel, index 25)."""
    terr_oh = [0.0] * 10
    tt = zone["terrain_type"]
    if 0 <= tt < 10:
        terr_oh[tt] = 1.0
    area = zone["area"]
    area_log = math.log(area + 1)
    n_adj = len(zone["adjacent_zones"])
    bnd_len = max(len(zone["boundary_tiles"]), 1)
    choke_density = len(zone["chokepoints"]) / bnd_len
    opn = float(np.mean([sf[y, x, 25] for (x, y) in zone["tiles"]]))
    xs = [x for x, y in zone["tiles"]]
    ys = [y for x, y in zone["tiles"]]
    bw = max(xs) - min(xs) + 1
    bh = max(ys) - min(ys) + 1
    elong = max(bw, bh) / max(min(bw, bh), 1)
    return terr_oh + [area_log, n_adj, choke_density, opn, elong]


def _artifact_terrain_feat_no_town(terrain_feat: list) -> list:
    """Drop dist_town from a stored artifact terrain_feat vector."""
    return [v for i, v in enumerate(terrain_feat) if i != DIST_TOWN_IDX]


# ---------------------------------------------------------------------------
# Multinomial logistic regression (numpy)
# ---------------------------------------------------------------------------

def _softmax(Z: np.ndarray) -> np.ndarray:
    Z = Z - Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=1, keepdims=True)


def _train_logreg(X: np.ndarray, y: np.ndarray, n_classes: int,
                  epochs: int = 400, lr: float = 0.2, l2: float = 1e-3):
    """Train softmax regression. X already standardized + bias column appended."""
    n, d = X.shape
    W = np.zeros((d, n_classes), dtype=np.float64)
    Y = np.zeros((n, n_classes), dtype=np.float64)
    Y[np.arange(n), y] = 1.0
    for _ in range(epochs):
        P = _softmax(X @ W)
        grad = X.T @ (P - Y) / n + l2 * W
        W -= lr * grad
    return W


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train() -> dict:
    art = json.load(open(ARCHETYPE_PATH))
    clusters = art["clusters"]   # {id: stats with "label"}
    id2label = {int(k): v["label"] for k, v in clusters.items()}

    # Non-town classes the classifier predicts among
    nontown_ids = sorted(cid for cid, lab in id2label.items() if lab not in TOWN_LABELS)
    cls_index = {cid: i for i, cid in enumerate(nontown_ids)}

    # Build training matrix from non-town zones only
    feats = []
    labels = []
    for z in art["zones"]:
        cid = int(z["cluster"])
        if id2label[cid] in TOWN_LABELS:
            continue
        feats.append(_artifact_terrain_feat_no_town(z["terrain_feat"]))
        labels.append(cls_index[cid])
    X = np.array(feats, dtype=np.float64)
    y = np.array(labels, dtype=np.int64)

    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xz = (X - mu) / sd
    Xb = np.hstack([Xz, np.ones((len(Xz), 1))])  # bias

    W = _train_logreg(Xb, y, len(nontown_ids))

    # train accuracy
    pred = (Xb @ W).argmax(axis=1)
    acc = float((pred == y).mean())

    # corpus proportions among non-town archetypes (drives distribution-matched
    # assignment, since terrain alone can't tell ECONOMY/SHRINE from TREASURE).
    import collections
    cnt = collections.Counter(int(yy) for yy in y)
    total = sum(cnt.values())
    nontown_props = {cid: cnt[cls_index[cid]] / total for cid in nontown_ids}

    model = {
        "W": W, "mu": mu, "sd": sd,
        "nontown_ids": nontown_ids,        # class index -> cluster id
        "id2label": id2label,
        "nontown_props": nontown_props,    # cluster id -> corpus fraction
        "train_acc": acc,
    }
    pickle.dump(model, open(CLF_PATH, "wb"))
    return model


def load() -> dict:
    if not os.path.exists(CLF_PATH):
        raise FileNotFoundError(f"{CLF_PATH} missing — run archetype_model.py first")
    return pickle.load(open(CLF_PATH, "rb"))


def load_rules() -> dict:
    """cluster_id (int) -> {label, count_dist, radial_profile, ...} from artifact."""
    art = json.load(open(ARCHETYPE_PATH))
    return {int(k): v for k, v in art["clusters"].items()}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_nontown(model: dict, terrain_feat_no_town: list) -> np.ndarray:
    """Return probability vector over model['nontown_ids'] for one zone."""
    x = (np.array(terrain_feat_no_town, dtype=np.float64) - model["mu"]) / model["sd"]
    xb = np.hstack([x, [1.0]])
    return _softmax((xb @ model["W"])[None, :])[0]


# ---------------------------------------------------------------------------
# Full archetype assignment over a map's sub-zones
# ---------------------------------------------------------------------------

def assign_archetypes(zones: dict, sf: np.ndarray, n_towns: int,
                      model: dict, seed: int, rng) -> dict:
    """Assign each sub-zone an archetype label.

    1. MAIN -> the n_towns most town-suitable sub-zones (large, open, spread apart).
    2. TOWN_OUTSKIRTS -> sub-zones adjacent to a MAIN zone (capped).
    3. remaining -> classifier over non-town archetypes (argmax at seed=0, else sample).

    Returns {zone_id: label}.
    """
    # --- 1. MAIN selection ---
    # town suitability: prefer larger, more open, more central zones, and spread them.
    H = sf.shape[0]
    W = sf.shape[1]
    cxc, cyc = W / 2.0, H / 2.0
    diag = max(math.hypot(W, H), 1.0)

    def suitability(z):
        area = z["area"]
        opn = float(np.mean([sf[y, x, 25] for (x, y) in z["tiles"]]))
        ccx, ccy = z["centroid"]
        centrality = 1.0 - math.hypot(ccx - cxc, ccy - cyc) / diag
        base = 0.5 * math.log(area + 1) + 1.0 * opn + 0.6 * centrality
        if seed != 0:
            base += rng.uniform(-0.4, 0.4)   # vary which zones become towns
        return base

    cand = sorted(zones.keys(), key=lambda zid: -suitability(zones[zid]))
    main_ids: list = []
    MIN_TOWN_AREA = 40
    min_sep = diag * 0.18   # spread towns apart
    for zid in cand:
        if len(main_ids) >= n_towns:
            break
        if zones[zid]["area"] < MIN_TOWN_AREA:
            continue
        ccx, ccy = zones[zid]["centroid"]
        ok = True
        for mid in main_ids:
            mx, my = zones[mid]["centroid"]
            if math.hypot(ccx - mx, ccy - my) < min_sep:
                ok = False
                break
        if ok:
            main_ids.append(zid)
    # if spread constraint starved us, fill remaining by suitability
    if len(main_ids) < n_towns:
        for zid in cand:
            if len(main_ids) >= n_towns:
                break
            if zid not in main_ids and zones[zid]["area"] >= MIN_TOWN_AREA:
                main_ids.append(zid)

    labels: dict = {zid: None for zid in zones}
    for zid in main_ids:
        labels[zid] = "MAIN"

    # --- 2. TOWN_OUTSKIRTS: zones adjacent to a MAIN zone ---
    main_set = set(main_ids)
    n_outskirts_cap = max(1, len(main_ids) // 3)   # outskirts are rare (~3% of corpus)
    outskirts = 0
    for zid in zones:
        if labels[zid] is not None:
            continue
        if zones[zid]["adjacent_zones"] & main_set and outskirts < n_outskirts_cap:
            labels[zid] = "TOWN_OUTSKIRTS"
            outskirts += 1

    # --- 3. distribution-matched allocation for the rest ---
    # Terrain alone can't distinguish ECONOMY/SHRINE from TREASURE, so we don't let
    # the classifier pick freely (it would make everything TREASURE). Instead we set
    # per-archetype TARGET counts from corpus proportions and greedily assign each
    # zone to the archetype it best fits (classifier prob) until targets are met.
    id2label = model["id2label"]
    nontown_ids = model["nontown_ids"]
    props = model["nontown_props"]

    remaining = [zid for zid in zones if labels[zid] is None]
    nR = len(remaining)
    if nR:
        # target count per archetype (rounded, remainder to the largest proportion)
        targets = {cid: int(math.floor(props[cid] * nR)) for cid in nontown_ids}
        leftover = nR - sum(targets.values())
        for cid in sorted(nontown_ids, key=lambda c: -props[c]):
            if leftover <= 0:
                break
            targets[cid] += 1
            leftover -= 1

        # probability of each (zone, archetype)
        probrows = {zid: classify_nontown(model, gen_terrain_feat(zones[zid], sf))
                    for zid in remaining}

        # candidate (score, zid, class_index) triples; seed!=0 jitters the score
        triples = []
        for zid in remaining:
            for ci, cid in enumerate(nontown_ids):
                score = float(probrows[zid][ci])
                if seed != 0:
                    score *= (0.5 + rng.random())   # break ties / add variation
                triples.append((score, zid, ci))
        triples.sort(reverse=True)

        cap = {cid: targets[cid] for cid in nontown_ids}
        for score, zid, ci in triples:
            if labels[zid] is not None:
                continue
            cid = nontown_ids[ci]
            if cap[cid] <= 0:
                continue
            labels[zid] = id2label[cid]
            cap[cid] -= 1
        # any zone left (caps exhausted by rounding) -> its argmax archetype
        for zid in remaining:
            if labels[zid] is None:
                ci = int(np.argmax(probrows[zid]))
                labels[zid] = id2label[nontown_ids[ci]]

    return labels


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    model = train()
    print(f"trained terrain->archetype classifier")
    print(f"  non-town classes: "
          + ", ".join(model["id2label"][cid] for cid in model["nontown_ids"]))
    print(f"  train accuracy: {model['train_acc']:.1%}")
    print(f"  saved: {CLF_PATH}")

    # Report per-class confusion-lite: predicted distribution
    art = json.load(open(ARCHETYPE_PATH))
    id2label = model["id2label"]
    import collections
    actual = collections.Counter()
    pred = collections.Counter()
    for z in art["zones"]:
        cid = int(z["cluster"])
        if id2label[cid] in TOWN_LABELS:
            continue
        actual[id2label[cid]] += 1
        feat = _artifact_terrain_feat_no_town(z["terrain_feat"])
        probs = classify_nontown(model, feat)
        pc = model["nontown_ids"][int(np.argmax(probs))]
        pred[id2label[pc]] += 1
    print("\n  archetype     actual   predicted")
    for lab in sorted(actual):
        print(f"  {lab:14s} {actual[lab]:6d}   {pred[lab]:6d}")


if __name__ == "__main__":
    main()
