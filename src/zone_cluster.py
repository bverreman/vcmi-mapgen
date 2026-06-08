"""Cluster corpus zones into layout archetypes (center / danger / rich / ...).

Each corpus zone (terrain-flood-filled region, level 0) gets a LAYOUT DESCRIPTOR
that captures what kind of zone it is — its object composition, density, and the
radial arrangement of guards vs loot. We k-means cluster these descriptors; a human
then labels the clusters (start / treasure / buffer / economy / junction / ...).

Two feature sets are stored per zone:
  - layout features  : include object composition (defines the archetype)
  - terrain features : terrain-only (area, terrain, dist-to-town, chokepoints, shape)
                       — what a generation-time classifier has to work with, since
                       at generation there are no objects yet.

Outputs out/zone_archetypes.json:
  {
    "k": K,
    "layout_feat_names": [...], "terrain_feat_names": [...],
    "clusters": { cluster_id: { "n": int, "label": "",          # human fills label
                                "purpose_mean_count": {P: float},
                                "radial_profile": {P: [mean, std]},
                                "density": float, "has_town_frac": float,
                                "dist_town_mean": float, "area_mean": float,
                                "terrain_centroid": [...] } },
    "zones": [ {map, zone_id, cluster, area, terrain, layout: [...], terrain_feat: [...]} ]
  }

Usage:
    uv run python src/zone_cluster.py --k 6
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology as ON
import terrain_segment as TS

# 12 gameplay purposes (matches coherence.PURPOSES)
PURPOSES = [
    "TOWN", "GUARD", "MINE", "DWELLING", "BANK", "REWARD_PICKUP", "RESOURCE_PILE",
    "TRANSPORT", "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL", "QUEST_GATE",
]
PIDX = {p: i for i, p in enumerate(PURPOSES)}

MIN_ZONE_AREA = 12     # ignore tiny noise zones

# area_log is deliberately EXCLUDED from the clustered layout descriptor: zone SIZE
# is not a role (a small and a large treasure zone share an archetype). Size lives
# only in the terrain features. The layout descriptor is role-only: composition +
# density + town/mine presence + dist-to-town + radial guard/reward arrangement.
LAYOUT_FEAT_NAMES = (
    [f"frac_{p}" for p in PURPOSES] +
    ["density", "has_town", "has_mine", "dist_town",
     "guard_edge_bias", "reward_edge_bias", "n_adj", "choke_density"]
)
TERRAIN_FEAT_NAMES = (
    [f"terr_{t}" for t in range(10)] +
    ["area_log", "dist_town", "n_adj", "choke_density", "openness", "elongation"]
)


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------

def _all_map_names() -> list[str]:
    d = os.path.join(ROOT, "out", "maps")
    return [os.path.splitext(f)[0] for f in sorted(os.listdir(d)) if f.endswith(".json")]


def _load_map(name: str) -> dict:
    return json.load(open(os.path.join(ROOT, "out", "maps", f"{name}.json")))


def _resolve_purpose(o: dict) -> str:
    return ON.resolve(o["class"], o["subclass"]).get("purpose", "UNKNOWN")


# ---------------------------------------------------------------------------
# Per-zone descriptor
# ---------------------------------------------------------------------------

def _town_centroids(m: dict, W: int, H: int) -> list:
    """Centroids (x,y) of all surface towns on the map."""
    pts = []
    for o in m["objects"]:
        if o.get("l", 0) != 0:
            continue
        x, y = o["x"], o["y"]
        if 0 <= x < W and 0 <= y < H and _resolve_purpose(o) == "TOWN":
            pts.append((x, y))
    return pts


def describe_zones(m: dict):
    """Yield (zone_id, layout_vec, terrain_vec, meta) for each non-tiny zone."""
    if not m["terrain"]:
        return
    terr_l0 = m["terrain"][0]
    H = len(terr_l0)
    W = len(terr_l0[0])
    diag = max(math.hypot(W, H), 1.0)

    zones, zone_label = TS.segment(terr_l0, subdivide=True)
    sf = TS.compute_static_features(terr_l0, zones, zone_label)  # for openness/dist_bnd

    # Objects per zone, with their normalized dist-to-boundary (feat index 20)
    zone_objs = collections.defaultdict(lambda: collections.defaultdict(list))
    for o in m["objects"]:
        if o.get("l", 0) != 0:
            continue
        x, y = o["x"], o["y"]
        if not (0 <= x < W and 0 <= y < H):
            continue
        p = _resolve_purpose(o)
        if p not in PIDX:
            continue
        zid = zone_label[y][x]
        if zid == -1:
            continue
        dist_bnd = float(sf[y, x, 20])     # BFS dist to boundary / sqrt(area)
        zone_objs[zid][p].append(dist_bnd)

    towns = _town_centroids(m, W, H)

    for zid, zone in zones.items():
        area = zone["area"]
        if area < MIN_ZONE_AREA:
            continue

        objs = zone_objs.get(zid, {})
        total = sum(len(v) for v in objs.values())

        # composition fractions
        frac = [0.0] * len(PURPOSES)
        if total:
            for p, lst in objs.items():
                frac[PIDX[p]] = len(lst) / total

        density = total / area
        has_town = 1.0 if objs.get("TOWN") else 0.0
        has_mine = 1.0 if objs.get("MINE") else 0.0

        # dist to nearest town (zone centroid -> nearest town), normalized
        cx, cy = zone["centroid"]
        if towns:
            dist_town = min(math.hypot(cx - tx, cy - ty) for tx, ty in towns) / diag
        else:
            dist_town = 1.0

        # radial bias: mean normalized dist-to-boundary of guards vs rewards
        guard_edge = (sum(objs.get("GUARD", [])) / len(objs["GUARD"])
                      if objs.get("GUARD") else 0.0)
        reward_edge = (sum(objs.get("REWARD_PICKUP", [])) / len(objs["REWARD_PICKUP"])
                       if objs.get("REWARD_PICKUP") else 0.0)

        area_log = math.log(area + 1)
        n_adj = len(zone["adjacent_zones"])
        bnd_len = max(len(zone["boundary_tiles"]), 1)
        choke_density = len(zone["chokepoints"]) / bnd_len

        layout_vec = frac + [density, has_town, has_mine, dist_town,
                             guard_edge, reward_edge, n_adj, choke_density]

        # terrain-only features (what generation sees)
        terr_oh = [0.0] * 10
        tt = zone["terrain_type"]
        if 0 <= tt < 10:
            terr_oh[tt] = 1.0
        # mean openness over zone tiles
        opn = float(np.mean([sf[y, x, 25] for (x, y) in zone["tiles"]]))
        # elongation: bbox aspect ratio
        xs = [x for x, y in zone["tiles"]]
        ys = [y for x, y in zone["tiles"]]
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        elong = max(bw, bh) / max(min(bw, bh), 1)
        terrain_vec = terr_oh + [area_log, dist_town, n_adj, choke_density, opn, elong]

        # radial: per-purpose list of normalized dist-to-boundary (for rule profiles)
        meta = {"area": area, "terrain": tt, "total_objs": total,
                "objs": {p: len(v) for p, v in objs.items()},
                "radial": {p: list(v) for p, v in objs.items()}}
        yield zid, layout_vec, terrain_vec, meta


# ---------------------------------------------------------------------------
# k-means (numpy, deterministic)
# ---------------------------------------------------------------------------

def _kmeans(X: np.ndarray, k: int, seed: int = 0, iters: int = 200):
    rng = np.random.RandomState(seed)
    n = X.shape[0]
    # k-means++ init
    centers = [X[rng.randint(n)]]
    for _ in range(k - 1):
        d2 = np.min(np.stack([np.sum((X - c) ** 2, axis=1) for c in centers]), axis=0)
        s = d2.sum()
        probs = d2 / s if s > 0 else np.ones(n) / n
        centers.append(X[rng.choice(n, p=probs)])
    centers = np.array(centers, dtype=np.float64)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        dists = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
        new_labels = dists.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            mask = labels == j
            if mask.any():
                centers[j] = X[mask].mean(axis=0)
    inertia = float(np.sum((X - centers[labels]) ** 2))
    return labels, centers, inertia


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Cluster corpus zones into layout archetypes.")
    ap.add_argument("--k", type=int, default=6, help="number of archetypes")
    ap.add_argument("--maps", type=int, default=0, help="limit to first N maps (0=all)")
    args = ap.parse_args()

    names = _all_map_names()
    if args.maps:
        names = names[:args.maps]

    # Collect descriptors
    zones_data = []   # (map, zid, layout, terrain, meta)
    for name in names:
        m = _load_map(name)
        for zid, lay, terr, meta in describe_zones(m):
            zones_data.append((name, zid, lay, terr, meta))

    print(f"collected {len(zones_data)} zones from {len(names)} maps")
    if not zones_data:
        sys.exit("no zones found")

    X = np.array([z[2] for z in zones_data], dtype=np.float64)  # layout features

    # Standardize (z-score) for clustering
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xz = (X - mu) / sd

    labels, centers, inertia = _kmeans(Xz, args.k, seed=0)
    print(f"k={args.k}  inertia={inertia:.1f}")

    # Per-cluster aggregate stats for labeling + later rule writing
    clusters = {}
    for j in range(args.k):
        idx = [i for i in range(len(zones_data)) if labels[i] == j]
        if not idx:
            continue
        n = len(idx)
        # mean count per purpose
        pcount = collections.defaultdict(float)
        radial = collections.defaultdict(list)   # purpose -> all normalized dist_bnd
        count_lists = collections.defaultdict(list)  # purpose -> per-zone counts
        densities = []
        has_town = 0
        dist_towns = []
        areas = []
        guard_edges = []
        reward_edges = []
        for i in idx:
            meta = zones_data[i][4]
            for p, c in meta["objs"].items():
                pcount[p] += c
                count_lists[p].append(c)
            for p, lst in meta.get("radial", {}).items():
                radial[p].extend(lst)
            lay = zones_data[i][2]
            densities.append(lay[PIDX_LEN])       # density   (idx 12)
            has_town += int(lay[PIDX_LEN + 1] > 0)  # has_town (idx 13)
            dist_towns.append(lay[PIDX_LEN + 3])    # dist_town(idx 15)
            guard_edges.append(lay[PIDX_LEN + 4])   # guard_edge_bias (idx 16)
            reward_edges.append(lay[PIDX_LEN + 5])  # reward_edge_bias(idx 17)
            areas.append(meta["area"])
        # radial profile per purpose: gather normalized dist-to-boundary
        # (recompute from zones_data meta is not stored; use guard/reward bias proxies)
        # radial profile per purpose: [mean, std] of normalized dist-to-boundary
        radial_profile = {}
        for p in PURPOSES:
            if radial.get(p):
                arr = np.array(radial[p], dtype=np.float64)
                radial_profile[p] = [round(float(arr.mean()), 4),
                                     round(float(arr.std()), 4)]
        # count distribution per purpose: histogram of per-zone counts (incl. zeros)
        # zeros = zones in this cluster that had none of P (so the rule can decide
        # whether a zone of this archetype even gets a P).
        count_dist = {}
        for p in PURPOSES:
            counts = count_lists.get(p, [])
            # pad with zeros for zones lacking this purpose
            counts = counts + [0] * (n - len(counts))
            if any(counts):
                count_dist[p] = counts

        clusters[j] = {
            "n": n,
            "label": "",
            "purpose_mean_count": {p: round(pcount[p] / n, 2) for p in PURPOSES if pcount[p]},
            "radial_profile": radial_profile,
            "count_dist": count_dist,
            "density": round(float(np.mean(densities)), 4),
            "has_town_frac": round(has_town / n, 2),
            "dist_town_mean": round(float(np.mean(dist_towns)), 3),
            "guard_edge_bias": round(float(np.mean(guard_edges)), 3),
            "reward_edge_bias": round(float(np.mean(reward_edges)), 3),
            "area_mean": round(float(np.mean(areas)), 0),
            "terrain_centroid": [round(float(v), 3) for v in
                                 (centers[j] * sd + mu).tolist()],
        }
        clusters[j]["label"] = auto_label(clusters[j])

    # ---- print labeling report ----
    print("\n" + "=" * 78)
    print("CLUSTER LABELING REPORT")
    print("=" * 78)
    for j in sorted(clusters):
        c = clusters[j]
        print(f"\n── cluster {j}  [{c['label']}]  (n={c['n']} zones, "
              f"{100*c['n']/len(zones_data):.0f}%) ──")
        print(f"   area_mean={c['area_mean']:.0f}  density={c['density']:.4f}  "
              f"has_town={c['has_town_frac']:.0%}  dist_town={c['dist_town_mean']:.2f}")
        print(f"   guard_edge_bias={c['guard_edge_bias']:.2f}  "
              f"reward_edge_bias={c['reward_edge_bias']:.2f}  "
              f"(higher = more toward zone interior)")
        # top purposes by mean count
        top = sorted(c["purpose_mean_count"].items(), key=lambda kv: -kv[1])[:6]
        print("   top purposes (mean count/zone): " +
              "  ".join(f"{p}={v:.1f}" for p, v in top))

    # ---- write artifact ----
    out = {
        "k": args.k,
        "layout_feat_names": LAYOUT_FEAT_NAMES,
        "terrain_feat_names": TERRAIN_FEAT_NAMES,
        "feat_mean": mu.tolist(),
        "feat_std": sd.tolist(),
        "clusters": {str(j): clusters[j] for j in clusters},
        "zones": [
            {"map": zones_data[i][0], "zone_id": int(zones_data[i][1]),
             "cluster": int(labels[i]), "area": zones_data[i][4]["area"],
             "terrain": zones_data[i][4]["terrain"],
             "layout": [round(v, 4) for v in zones_data[i][2]],
             "terrain_feat": [round(v, 4) for v in zones_data[i][3]]}
            for i in range(len(zones_data))
        ],
    }
    out_path = os.path.join(ROOT, "out", "zone_archetypes.json")
    json.dump(out, open(out_path, "w"), indent=2)
    print(f"\nsaved: {out_path}")
    print("→ inspect the report above, then fill in each cluster's \"label\".")


def auto_label(c: dict) -> str:
    """Derive a stable archetype label from a cluster's statistics (not its id),
    so labels survive re-clustering. Thresholds match the inspected k=6 profiles."""
    mc = c["purpose_mean_count"]
    total = sum(mc.values()) or 1.0
    has_town = c["has_town_frac"]
    density = c["density"]
    if has_town >= 0.5:
        return "MAIN" if density >= 0.07 else "TOWN_OUTSKIRTS"
    spell = mc.get("SPELL_SKILL", 0.0)
    mine = mc.get("MINE", 0.0)
    guard = mc.get("GUARD", 0.0)
    transport = mc.get("TRANSPORT", 0.0)
    if spell >= 1.0 and spell >= 0.3 * total:
        return "SHRINE"
    if transport >= 1.0 and transport >= 0.3 * total:
        return "JUNCTION"
    if mine >= 1.0 and (mine + guard) >= 0.5 * total:
        return "ECONOMY"
    if total >= 10.0:
        return "TREASURE"
    return "WILDERNESS"


# index where composition fractions end in the layout vector
PIDX_LEN = len(PURPOSES)

if __name__ == "__main__":
    main()
