"""Map-level quality yardstick — score a GENERATED map against the corpus.

Every generator we have (grammar / gan / patch / markov) is a *local-texture* imitator and
nothing ever measured a generated map back against the real corpus. This module is that missing
measurement: a single :func:`score_map` that combines

    total = W_REACH·reach  +  W_DIST·dist  +  W_BAL·balance

where
  * **reach**   — playability, reusing :func:`traverse.traverse` (can a hero walk the map?),
  * **dist**    — distance of the map's *global* feature vector to the corpus mean (z-distance
                  over `maps_json/`), the half nothing modelled before,
  * **balance** — town count / spacing, guard↔reward coupling, an outward value gradient, and
                  relational-pair completeness (no lone half of a portal/gate pair).

Torch-free, deterministic, numpy + stdlib only. Corpus statistics are aggregated once over the
159 faithful maps and cached (in memory + ``out/corpus_features.json``). ``zone_engine`` imports
this lazily for the ``compare`` CLI; this module lazily imports ``zone_engine`` for segmentation —
no import cycle.
"""
from __future__ import annotations

import collections
import json
import math
import os

import numpy as np

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import traverse as TR
from vcmi_mapgen.kit.paths import project_root

ROOT = project_root()
WATER, ROCK = 8, 9
MIN_AREA = 12                       # match generate_map's default min zone area
LAND_TERRAINS = list(range(8))     # ids 0..7 are land; 8=water, 9=rock
COUPLE_R = 6                       # a reward is "guarded" if a GUARD sits within this radius

# Purpose groups used by the global features (vocabulary from data/objlib.json).
REWARD_PURPOSES = {"REWARD_PICKUP", "RESOURCE_PILE", "BANK", "STAT_PERMANENT",
                   "SPELL_SKILL", "BONUS_TEMP", "MANA"}
GUARD_PURPOSES = {"GUARD"}
# Relational families that must appear as COMPLETE matched sets (subtype = colour / portal id).
RELATIONAL_PAIRS = [("monolithOneWayEntrance", "monolithOneWayExit"),
                    ("borderGate", "keymasterTent"), ("borderGuard", "keymasterTent")]
RELATIONAL_SELF = ["monolithTwoWay"]   # ≥2 ends sharing the same subtype

# Score weights (tunable — documented so the leaderboard is interpretable).
W_REACH, W_DIST, W_BAL = 0.4, 0.4, 0.2
_STD_FLOOR = 1e-6
_CACHE_PATH = str(ROOT / "out" / "corpus_features.json")
_CORPUS = None                     # in-memory memo of (names, mean, std)


# ---------------------------------------------------------------------------
# Map → ordered global feature vector (identical for corpus and generated maps)
# ---------------------------------------------------------------------------

def _purpose(o):
    """Purpose that works on BOTH corpus objects (cls/sub) and generated ones (type only)."""
    return o.get("_purpose") or TR.TYPE2PURPOSE.get(o.get("type"), "UNKNOWN")


def _segment(level_grid):
    """zones, zone_label for one terrain level (lazy import avoids a cycle with zone_engine)."""
    from vcmi_mapgen import terrain_segment as TS
    return TS.segment(level_grid, subdivide=False)


def _adjacency(zones, zone_label, W, H):
    """Land-zone adjacency: {zid -> set(neighbour zids)} via 4-neighbour zone-label contact."""
    adj = collections.defaultdict(set)
    big = {zid for zid, z in zones.items() if z["area"] >= MIN_AREA}
    for y in range(H):
        for x in range(W):
            a = zone_label[y][x]
            if a < 0 or a not in big:
                continue
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H:
                    b = zone_label[ny][nx]
                    if b >= 0 and b in big and b != a:
                        adj[a].add(b)
                        adj[b].add(a)
    return adj, big


def _largest_component_frac(adj, nodes):
    if not nodes:
        return 0.0
    seen, best = set(), 0
    for s in nodes:
        if s in seen:
            continue
        comp, stack = 0, [s]
        seen.add(s)
        while stack:
            u = stack.pop()
            comp += 1
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        best = max(best, comp)
    return best / len(nodes)


def features(fm) -> dict:
    """Ordered global feature dict for a faithful-map dict (surface level 0 only)."""
    terr = fm["terrain"][0]
    H = len(terr)
    W = len(terr[0]) if H else 0
    ids = [[c["t"] for c in row] for row in terr]
    land = [(x, y) for y in range(H) for x in range(W) if ids[y][x] in LAND_TERRAINS]
    land_area = max(len(land), 1)

    zones, zone_label = _segment(terr)
    big = [zid for zid, z in zones.items() if z["area"] >= MIN_AREA]
    areas = np.array([zones[z]["area"] for z in big], dtype=float)
    adj, bigset = _adjacency(zones, zone_label, W, H)

    f = collections.OrderedDict()
    # --- structure ---
    f["zone_count"] = float(len(big))
    f["mean_zone_area_frac"] = float(areas.mean() / land_area) if len(areas) else 0.0
    f["zone_area_cv"] = float(areas.std() / areas.mean()) if len(areas) and areas.mean() else 0.0
    # --- terrain mix (fraction of land tiles per land terrain) ---
    tcount = collections.Counter(ids[y][x] for x, y in land)
    for t in LAND_TERRAINS:
        f[f"terr_{t}"] = tcount.get(t, 0) / land_area
    # --- object purpose density (per land tile) ---
    surf = [o for o in fm["objects"] if o.get("l", 0) == 0]
    pcount = collections.Counter(_purpose(o) for o in surf)
    for p in sorted(TR.OBJ.keys()):
        f[f"obj_{p}"] = pcount.get(p, 0) / land_area
    # --- connectivity ---
    deg = [len(adj.get(z, ())) for z in big]
    f["mean_degree"] = float(np.mean(deg)) if deg else 0.0
    f["conn_frac"] = _largest_component_frac(adj, bigset)
    # --- coupling + value gradient (the global signals nothing modelled) ---
    towns = [(o["x"], o["y"]) for o in surf if _purpose(o) == "TOWN"]
    guards = [(o["x"], o["y"]) for o in surf if _purpose(o) in GUARD_PURPOSES]
    rewards = [(o["x"], o["y"]) for o in surf if _purpose(o) in REWARD_PURPOSES]
    f["guard_reward_coupling"] = _coupling(rewards, guards)
    f["value_gradient"] = _value_gradient(rewards, towns, land, W, H)
    # --- balance helpers (also used directly by score) ---
    f["town_count"] = float(len(towns))
    f["town_spacing"] = _min_spacing(towns, W, H)
    return f


def _coupling(rewards, guards):
    if not rewards or not guards:
        return 0.0
    n = 0
    for rx, ry in rewards:
        if any((rx - gx) ** 2 + (ry - gy) ** 2 <= COUPLE_R ** 2 for gx, gy in guards):
            n += 1
    return n / len(rewards)


def _value_gradient(rewards, towns, land, W, H):
    """Signed, scale-free: how much farther rewards sit from the nearest town than an average
    land tile. Positive ⇒ treasure rises outward from starts (the hallmark of a good map)."""
    if not rewards or not towns:
        return 0.0
    diag = math.hypot(W, H) or 1.0

    def ndist(px, py):
        return min(math.hypot(px - tx, py - ty) for tx, ty in towns)
    r = np.mean([ndist(x, y) for x, y in rewards])
    base = np.mean([ndist(x, y) for x, y in land]) if land else 0.0
    return float((r - base) / diag)


def _min_spacing(towns, W, H):
    if len(towns) < 2:
        return 0.0
    diag = math.hypot(W, H) or 1.0
    m = min(math.hypot(ax - bx, ay - by)
            for i, (ax, ay) in enumerate(towns) for bx, by in towns[i + 1:])
    return float(m / diag)


# ---------------------------------------------------------------------------
# Corpus aggregation (cached)
# ---------------------------------------------------------------------------

def _corpus_stats():
    """(feature_names, mean[], std[]) over all maps_json/ maps; cached in memory + on disk."""
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS
    if os.path.exists(_CACHE_PATH):
        d = json.load(open(_CACHE_PATH))
        _CORPUS = (d["names"], np.array(d["mean"]), np.array(d["std"]))
        return _CORPUS
    rows, names = [], None
    for nm in OR.all_map_names():
        try:
            fm = OR.load_faithful(nm)
            f = features(fm)
        except Exception:
            continue
        if names is None:
            names = list(f.keys())
        rows.append([f[k] for k in names])
    arr = np.array(rows, dtype=float)
    mean, std = arr.mean(axis=0), arr.std(axis=0)
    _CORPUS = (names, mean, std)
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    json.dump({"names": names, "mean": mean.tolist(), "std": std.tolist(),
               "n_maps": len(rows)}, open(_CACHE_PATH, "w"))
    return _CORPUS


def feature_distance(fm):
    """Mean per-feature z-distance of fm's global features to the corpus mean (0 = identical)."""
    names, mean, std = _corpus_stats()
    f = features(fm)
    x = np.array([f.get(k, 0.0) for k in names])
    return float(np.mean(np.abs(x - mean) / np.maximum(std, _STD_FLOOR)))


# ---------------------------------------------------------------------------
# Relational-pair completeness
# ---------------------------------------------------------------------------

def relational_complete(fm):
    """True iff every placed relational object is part of a complete matched set."""
    by_type = collections.defaultdict(collections.Counter)   # type -> {subtype: count}
    for o in fm["objects"]:
        by_type[o.get("type")][o.get("subtype")] += 1
    for a, b in RELATIONAL_PAIRS:
        for sub, n in by_type.get(a, {}).items():
            if n > 0 and by_type.get(b, {}).get(sub, 0) == 0:
                return False
    for a in RELATIONAL_SELF:
        for sub, n in by_type.get(a, {}).items():
            if n == 1:                          # a two-way end with no partner
                return False
    return True


# ---------------------------------------------------------------------------
# Public score
# ---------------------------------------------------------------------------

def _reach_score(fm):
    rep = TR.traverse(fm)
    if rep["start"] is None or not rep["passable_tiles"]:
        return 0.0, rep
    frac_tiles = rep["reached_tiles"] / rep["passable_tiles"]
    towns_total = sum(1 for o in fm["objects"] if TR.TYPE2PURPOSE.get(o.get("type")) == "TOWN")
    town_frac = 1.0
    if towns_total:
        town_frac = (towns_total - len(rep["unreachable_towns"])) / towns_total
    return float(max(0.0, min(1.0, frac_tiles)) * town_frac), rep


def _balance_score(fm):
    f = features(fm)
    tc = f["town_count"]
    town_ok = 1.0 if 2 <= tc <= 8 else (0.5 if tc == 1 else 0.0)
    spacing = min(1.0, f["town_spacing"] / 0.4)            # ~0.4·diag apart ⇒ full marks
    coupling = f["guard_reward_coupling"]
    gradient = max(0.0, min(1.0, f["value_gradient"] / 0.15 + 0.5))  # 0 grad ⇒ 0.5
    rel = 1.0 if relational_complete(fm) else 0.0
    parts = [town_ok, spacing, coupling, gradient, rel]
    return float(np.mean(parts)), {"town_ok": town_ok, "spacing": spacing,
                                   "coupling": coupling, "gradient": gradient, "rel_pairs": rel}


def score_map(fm) -> dict:
    """Score a faithful-map dict. Higher ``total`` ⇒ closer to the corpus AND playable."""
    reach, rep = _reach_score(fm)
    dist_z = feature_distance(fm)
    dist = math.exp(-dist_z)                   # (0,1]; 1 = identical to corpus mean
    balance, bal_break = _balance_score(fm)
    total = W_REACH * reach + W_DIST * dist + W_BAL * balance
    return {
        "ok": bool(rep["ok"]),
        "reach": round(reach, 4),
        "dist": round(dist, 4),
        "dist_z": round(dist_z, 4),
        "balance": round(balance, 4),
        "total": round(total, 4),
        "breakdown": {"reach": rep, "balance": bal_break},
    }


if __name__ == "__main__":   # quick check: score a corpus map vs an empty map
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="All for One")
    a = ap.parse_args()
    fm = OR.load_faithful(a.name)
    print(a.name, "->", {k: v for k, v in score_map(fm).items() if k != "breakdown"})
