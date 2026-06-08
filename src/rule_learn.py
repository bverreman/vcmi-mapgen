"""Learn per-purpose tile-scoring functions from the 159-map corpus.

Algorithm: sequential replay of each corpus map in PLACEMENT_ORDER, collecting
(positive tile, sampled negative tiles) pairs per object, then fitting a
linear model per purpose via pairwise margin ranking (BPR-style) SGD.

Artefacts written
-----------------
out/rule_model.pkl    dict: {purpose: np.float32 array shape (DIM,)}
out/rule_calib.json   count distributions per (terrain_type, area_band, purpose)

Usage
-----
    uv run python src/rule_learn.py            # train + eval + save
    uv run python src/rule_learn.py --eval     # eval saved model only
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pickle
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology as ON
import terrain_segment as TS
from terrain_segment import DIM_STATIC

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Placement order (spec §3); DECORATION is handled separately in rule_gen.py.
PLACEMENT_ORDER = [
    "TOWN", "MINE", "DWELLING", "GUARD", "BANK", "QUEST_GATE",
    "REWARD_PICKUP", "RESOURCE_PILE", "STAT_PERMANENT", "SPELL_SKILL",
    "BONUS_TEMP", "MANA", "TRANSPORT", "TERRAIN_MODIFIER", "INFO",
]
PLACEMENT_ORDER_SET = set(PLACEMENT_ORDER)
PURPOSE_IDX = {P: i for i, P in enumerate(PLACEMENT_ORDER)}
N_PURPOSES = len(PLACEMENT_ORDER)

DIM_DYN = 5   # dist_town, dist_mine, dist_guard, dist_boundary_guard, is_occupied
DIM = DIM_STATIC + DIM_DYN  # 33

# SGD hyperparameters
N_EPOCHS = 8
N_NEG = 40         # negative samples per positive
LR = 2e-2
MARGIN = 1.0
L2 = 1e-5          # weight decay


# ---------------------------------------------------------------------------
# Maps / corpus helpers
# ---------------------------------------------------------------------------

def _all_map_names() -> list[str]:
    d = os.path.join(ROOT, "out", "maps")
    return [os.path.splitext(f)[0] for f in sorted(os.listdir(d)) if f.endswith(".json")]


def _load_map(name: str) -> dict:
    return json.load(open(os.path.join(ROOT, "out", "maps", f"{name}.json")))


def _resolve_purpose(o: dict) -> str:
    return ON.resolve(o["class"], o["subclass"]).get("purpose", "UNKNOWN")


def _area_band(area: int) -> int:
    return min(int(math.log2(max(area, 1))), 12)


# ---------------------------------------------------------------------------
# Dynamic feature arrays
# ---------------------------------------------------------------------------

def _nearest_dist(pts: list, X: np.ndarray, Y: np.ndarray,
                  diag: float, H: int, W: int) -> np.ndarray:
    """Min Euclidean distance from each tile to any point in pts, normalised by diag.
    Returns float32 array (H, W) in [0, 1]; all-ones if pts is empty."""
    if not pts:
        return np.ones((H, W), dtype=np.float32)
    cap = float(H + W)
    min_d = np.full((H, W), cap, dtype=np.float32)
    for px, py in pts:
        d = np.hypot(X - px, Y - py).astype(np.float32)
        np.minimum(min_d, d, out=min_d)
    return np.clip(min_d, 0.0, cap) / diag


def _dynamic_arrays(H: int, W: int, X: np.ndarray, Y: np.ndarray,
                    placed: dict, occupied: set,
                    chokepoint_set: set, diag: float) -> np.ndarray:
    """Compute 5-channel dynamic feature map, shape (5, H, W) float32.

    Channels:
      0  dist_nearest_town
      1  dist_nearest_mine
      2  dist_nearest_guard
      3  dist_nearest_boundary_guard
      4  is_occupied
    """
    boundary_guards = [(x, y) for (x, y) in placed.get("GUARD", [])
                       if (x, y) in chokepoint_set]
    occ_arr = np.zeros((H, W), dtype=np.float32)
    for ox, oy in occupied:
        if 0 <= oy < H and 0 <= ox < W:
            occ_arr[oy, ox] = 1.0
    return np.stack([
        _nearest_dist(placed.get("TOWN", []), X, Y, diag, H, W),
        _nearest_dist(placed.get("MINE", []), X, Y, diag, H, W),
        _nearest_dist(placed.get("GUARD", []), X, Y, diag, H, W),
        _nearest_dist(boundary_guards, X, Y, diag, H, W),
        occ_arr,
    ])  # (5, H, W)


# ---------------------------------------------------------------------------
# Pairwise ranking SGD
# ---------------------------------------------------------------------------

def _sgd_step(w: np.ndarray, pos_f: np.ndarray, neg_f: np.ndarray) -> None:
    """In-place SGD update: push pos_score > neg_score + MARGIN."""
    diff = float(w @ pos_f) - float(w @ neg_f)
    if diff < MARGIN:
        w += LR * (pos_f - neg_f)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(names: list[str]) -> tuple[np.ndarray, dict]:
    """Train linear scoring models on corpus maps.

    Returns
    -------
    weights : np.ndarray shape (N_PURPOSES, DIM)
    calib   : nested dict  terrain_type -> area_band -> purpose -> [counts]
    """
    rng = random.Random(42)
    weights = np.zeros((N_PURPOSES, DIM), dtype=np.float64)

    # calib[terrain_type][area_band][purpose] = list of per-zone counts
    calib: dict = collections.defaultdict(
        lambda: collections.defaultdict(
            lambda: collections.defaultdict(list)
        )
    )
    deco_counts: list = []   # (n_decorations, n_passable_tiles)

    # Pre-compute per-map data once (segment, static features, objects) so
    # subsequent epochs just do the SGD loop without re-parsing terrain.
    print(f"pre-computing features for {len(names)} maps...", flush=True)
    map_cache: list = []
    for name in names:
        m = _load_map(name)
        if not m["terrain"]:
            map_cache.append(None)
            continue
        terr_l0 = m["terrain"][0]
        H = len(terr_l0)
        W = len(terr_l0[0])
        diag = max(math.hypot(W, H), 1.0)

        zones, zone_label = TS.segment(terr_l0)
        sf = TS.compute_static_features(terr_l0, zones, zone_label)
        cp_set = TS.chokepoint_set_from_zones(zones)

        Y_grid, X_grid = np.mgrid[0:H, 0:W]
        X_grid = X_grid.astype(np.float32)
        Y_grid = Y_grid.astype(np.float32)

        objs_by_purpose: dict = collections.defaultdict(list)
        n_deco = 0
        for o in m["objects"]:
            if o.get("l", 0) != 0:
                continue
            x, y = o["x"], o["y"]
            if not (0 <= x < W and 0 <= y < H):
                continue
            p = _resolve_purpose(o)
            if p in PLACEMENT_ORDER_SET:
                objs_by_purpose[p].append((x, y))
            elif p == "DECORATION":
                n_deco += 1

        # Calibration data (collected once from full corpus)
        n_passable = sum(1 for yy in range(H) for xx in range(W)
                         if zone_label[yy][xx] != -1)
        deco_counts.append((n_deco, n_passable))
        for zid, zone in zones.items():
            band = _area_band(zone["area"])
            tt = zone["terrain_type"]
            zone_tiles_set = zone["tiles_set"]
            for P in PLACEMENT_ORDER:
                cnt = sum(1 for (xx, yy) in objs_by_purpose[P]
                          if (xx, yy) in zone_tiles_set)
                calib[tt][band][P].append(cnt)

        map_cache.append({
            "H": H, "W": W, "diag": diag,
            "zones": zones, "zone_label": zone_label,
            "sf": sf, "cp_set": cp_set,
            "X": X_grid, "Y": Y_grid,
            "objs": objs_by_purpose,
        })

    print(f"pre-compute done; starting {N_EPOCHS} epochs", flush=True)

    order_idx = list(range(len(names)))
    for epoch in range(N_EPOCHS):
        rng.shuffle(order_idx)
        print(f"epoch {epoch + 1}/{N_EPOCHS}", flush=True)

        for ii, mi in enumerate(order_idx):
            entry = map_cache[mi]
            if entry is None:
                continue

            H = entry["H"]; W = entry["W"]; diag = entry["diag"]
            zones = entry["zones"]; zone_label = entry["zone_label"]
            sf = entry["sf"]; cp_set = entry["cp_set"]
            X_grid = entry["X"]; Y_grid = entry["Y"]
            objs_by_purpose = entry["objs"]

            # Sequential replay
            placed: dict = collections.defaultdict(list)
            occupied: set = set()

            for P in PLACEMENT_ORDER:
                pi = PURPOSE_IDX[P]
                w = weights[pi]

                pos_list = objs_by_purpose[P]
                if not pos_list:
                    continue

                dyn = _dynamic_arrays(H, W, X_grid, Y_grid,
                                      placed, occupied, cp_set, diag)  # (5,H,W)

                for (x, y) in pos_list:
                    zid = zone_label[y][x]
                    if zid == -1:
                        continue
                    zone_tiles = zones[zid]["tiles"]
                    if not zone_tiles:
                        continue

                    pos_f = np.concatenate([sf[y, x], dyn[:, y, x]]).astype(np.float64)

                    k = min(N_NEG, len(zone_tiles))
                    negs = rng.sample(zone_tiles, k)

                    for nx, ny in negs:
                        if (nx, ny) == (x, y):
                            continue
                        neg_f = np.concatenate([sf[ny, nx],
                                                dyn[:, ny, nx]]).astype(np.float64)
                        _sgd_step(w, pos_f, neg_f)

                # L2 regularisation
                w -= L2 * w

                for (x, y) in pos_list:
                    placed[P].append((x, y))
                    occupied.add((x, y))

            if (ii + 1) % 40 == 0:
                print(f"  {ii + 1}/{len(names)}", flush=True)

    # Finalise calibration: convert defaultdict to plain dicts (JSON-serialisable)
    calib_plain: dict = {}
    for tt, bands in calib.items():
        calib_plain[int(tt)] = {}
        for band, purposes in bands.items():
            calib_plain[int(tt)][int(band)] = {P: list(v) for P, v in purposes.items()}

    # Decoration rate: mean decorations per passable tile
    deco_rate = (sum(n for n, _ in deco_counts) /
                 max(sum(t for _, t in deco_counts), 1))

    return weights.astype(np.float32), {"counts": calib_plain, "deco_rate": deco_rate}


# ---------------------------------------------------------------------------
# Rank-0 accuracy evaluation
# ---------------------------------------------------------------------------

def evaluate(weights: np.ndarray, names: list[str]) -> dict:
    """For each corpus map, replay placement and check argmax == original tile."""
    rng = random.Random(0)
    per_purpose: dict = collections.defaultdict(lambda: {"rank0": 0, "total": 0})

    for name in names:
        m = _load_map(name)
        if not m["terrain"]:
            continue
        terr_l0 = m["terrain"][0]
        H = len(terr_l0)
        W = len(terr_l0[0])
        diag = max(math.hypot(W, H), 1.0)

        zones, zone_label = TS.segment(terr_l0)
        sf = TS.compute_static_features(terr_l0, zones, zone_label)
        cp_set = TS.chokepoint_set_from_zones(zones)

        Y_grid, X_grid = np.mgrid[0:H, 0:W]
        X_grid = X_grid.astype(np.float32)
        Y_grid = Y_grid.astype(np.float32)

        objs_by_purpose: dict = collections.defaultdict(list)
        for o in m["objects"]:
            if o.get("l", 0) != 0:
                continue
            x, y = o["x"], o["y"]
            if not (0 <= x < W and 0 <= y < H):
                continue
            p = _resolve_purpose(o)
            if p in PLACEMENT_ORDER_SET:
                objs_by_purpose[p].append((x, y))

        placed: dict = collections.defaultdict(list)
        occupied: set = set()

        for P in PLACEMENT_ORDER:
            pi = PURPOSE_IDX[P]
            w = weights[pi].astype(np.float64)

            pos_list = objs_by_purpose[P]
            if not pos_list:
                continue

            dyn = _dynamic_arrays(H, W, X_grid, Y_grid,
                                  placed, occupied, cp_set, diag)

            for (x, y) in pos_list:
                zid = zone_label[y][x]
                if zid == -1:
                    continue
                zone_tiles = zones[zid]["tiles"]
                if len(zone_tiles) < 2:
                    continue

                # Score all zone tiles
                zone_xy = np.array(zone_tiles, dtype=np.int32)  # (n,2)
                xs, ys = zone_xy[:, 0], zone_xy[:, 1]
                static_blk = sf[ys, xs]                  # (n, 28)
                dyn_blk = dyn[:, ys, xs].T               # (n, 5)
                feat_blk = np.concatenate([static_blk, dyn_blk], axis=1)  # (n,33)
                scores = feat_blk @ w                    # (n,)

                # Find index of the actual tile in zone_tiles
                try:
                    pos_idx = zone_tiles.index((x, y))
                except ValueError:
                    continue

                best_idx = int(np.argmax(scores))
                # tie-break by (x, y) lex
                if best_idx != pos_idx:
                    max_score = scores[best_idx]
                    tied = [i for i, s in enumerate(scores) if abs(s - max_score) < 1e-9]
                    # pick lowest (x,y) among tied
                    best_idx = min(tied, key=lambda i: (zone_tiles[i][0], zone_tiles[i][1]))

                per_purpose[P]["total"] += 1
                if best_idx == pos_idx:
                    per_purpose[P]["rank0"] += 1

            for (x, y) in pos_list:
                placed[P].append((x, y))
                occupied.add((x, y))

    return {P: (v["rank0"] / max(v["total"], 1), v["total"]) for P, v in per_purpose.items()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Train rule-based placement scoring models.")
    ap.add_argument("--eval", action="store_true", help="evaluate saved model instead of training")
    ap.add_argument("--maps", type=int, default=0,
                    help="limit to first N maps (0 = all, for quick tests)")
    args = ap.parse_args()

    names = _all_map_names()
    if args.maps:
        names = names[:args.maps]
    print(f"corpus: {len(names)} maps")

    model_path = os.path.join(ROOT, "out", "rule_model.pkl")
    calib_path = os.path.join(ROOT, "out", "rule_calib.json")

    if args.eval:
        if not os.path.exists(model_path):
            sys.exit(f"model not found: {model_path}")
        weights = pickle.load(open(model_path, "rb"))["weights"]
    else:
        weights, calib = train(names)
        os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
        pickle.dump({"weights": weights, "order": PLACEMENT_ORDER, "dim": DIM},
                    open(model_path, "wb"))
        json.dump(calib, open(calib_path, "w"), indent=2)
        print(f"saved: {model_path}")
        print(f"saved: {calib_path}")

    print("\nRank-0 accuracy per purpose (train set):")
    stats = evaluate(weights, names)
    total_r0 = total_n = 0
    for P in PLACEMENT_ORDER:
        if P in stats:
            acc, n = stats[P]
            total_r0 += acc * n
            total_n += n
            print(f"  {P:20s}  {acc * 100:6.1f}%  (n={n})")
    if total_n:
        print(f"  {'OVERALL':20s}  {total_r0 / total_n * 100:6.1f}%  (n={total_n})")


if __name__ == "__main__":
    main()
