"""Archetype-driven rule-based map generation.

Pipeline:
    terrain (.json)
      → segment + subdivide          (terrain_segment)
      → assign each sub-zone an archetype   (archetype_model: MAIN by rule,
                                             rest distribution-matched + classifier)
      → per-archetype layout rule places objects   (this file)
          counts: sampled from the archetype's corpus count distribution
          where : radial loot profile (dist-to-boundary) + relational couplings
                  (guard↔mine, loot↔guard, mine↔chokepoint, dwelling↔town)
      → decorations (energy_place cluster sampler, boosted at zone boundaries)
      → to_fm → repair_reachability → to_vmap

Seed semantics (rules only — no memorization):
    seed=0 : deterministic — archetype argmax, mean counts, argmax tile → a valid
             canonical map (NOT the byte-identical original; region-grain fidelity).
    seed=N : sampled archetypes/counts, top-K tile pick → a valid visual sibling.

Usage:
    uv run python src/rule_gen.py --terrain "All for One" --seed 0 --emit-vmap
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import archetype_model as AM
import energy_place as EP
import faithful
import ontology as ON
import terrain_segment as TS
import traverse as TR

# All purposes the rules can emit, grouped by RMG role.
ANCHOR_PURPOSES = ["TOWN", "MINE", "DWELLING", "QUEST_GATE", "TRANSPORT"]
# Loot that gets bundled into guarded treasure groups (a tight blob + an access guard).
LOOT_PURPOSES = ["REWARD_PICKUP", "RESOURCE_PILE", "BANK", "STAT_PERMANENT",
                 "SPELL_SKILL", "BONUS_TEMP", "MANA", "INFO", "TERRAIN_MODIFIER"]
ALL_PURPOSES = ANCHOR_PURPOSES + ["GUARD"] + LOOT_PURPOSES


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
# Per-archetype rules (count + radial profile), keyed by label
# ---------------------------------------------------------------------------

def build_rules_by_label() -> dict:
    """label -> {count_dist: {P: [counts]}, radial: {P: [mean, std]}}."""
    raw = AM.load_rules()    # cluster_id -> stats
    by_label: dict = {}
    for _cid, stats in raw.items():
        lab = stats["label"]
        # If two clusters share a label, the first wins (labels are unique here).
        if lab not in by_label:
            by_label[lab] = {
                "count_dist": stats.get("count_dist", {}),
                "radial": stats.get("radial_profile", {}),
            }
    return by_label


def zone_counts(rule: dict, label: str, seed: int, rng: random.Random,
                arch_cum: dict) -> dict:
    """{purpose: n} for one zone of this archetype.

    seed!=0 → sample from the per-zone count histogram (variation).
    seed=0  → deterministic largest-remainder distribution of the archetype mean
              across its zones, so a purpose with mean 0.5 places 0,1,0,1,… instead
              of round(0.5)=0 everywhere (which would erase rare purposes like BANK).
    """
    counts: dict = {}
    cd = rule["count_dist"]
    for P in ALL_PURPOSES:
        hist = cd.get(P)
        if not hist:
            continue
        # Towns are a fixed-count design element: only MAIN zones place them, so the
        # total matches n_towns (TOWN_OUTSKIRTS zones contained towns in the corpus
        # but here their town is the adjacent MAIN's).
        if P == "TOWN" and label != "MAIN":
            continue
        if seed != 0:
            counts[P] = int(rng.choice(hist))
        else:
            mu = sum(hist) / len(hist)
            prev = arch_cum[(label, P)]
            n = int(round(prev + mu)) - int(round(prev))
            arch_cum[(label, P)] = prev + mu
            if n > 0:
                counts[P] = n
    return counts


# ---------------------------------------------------------------------------
# RMG-style guarded placement
# ---------------------------------------------------------------------------
#
# This mirrors how VCMI's RMG builds a zone:
#   * anchors first (town, mines), each economy anchor gets a guard on its approach;
#   * loot is bundled into GUARDED TREASURE GROUPS — a tight cluster of pickups with
#     one monster guard on the tile facing the zone interior (you must beat the guard
#     to reach the loot). This is what creates the guard↔treasure coupling that real
#     maps have and that scatter/ring placement destroys.
#   * groups are spread across the zone by farthest-point sampling so they don't pile
#     up; tiles are consumed as used (RMG's used-tile bookkeeping).
#
# Determinism: seed=0 takes argmax/sorted choices with lexicographic tie-breaks;
# seed!=0 jitters group seeds and object order for siblings.

NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _nearest_norm(pts: list, X: np.ndarray, Y: np.ndarray, diag: float,
                  H: int, W: int) -> np.ndarray:
    """Normalized min distance from each tile to any point in pts (1.0 if none)."""
    if not pts:
        return np.ones((H, W), dtype=np.float32)
    cap = float(H + W)
    md = np.full((H, W), cap, dtype=np.float32)
    for px, py in pts:
        np.minimum(md, np.hypot(X - px, Y - py).astype(np.float32), out=md)
    return np.clip(md, 0.0, cap) / diag


def _grow_blob(seed_tile: tuple, k: int, free: set) -> list:
    """BFS from seed_tile over `free` tiles, collecting up to k tiles (tight blob).
    Deterministic: neighbours visited in fixed NB8 order."""
    if seed_tile not in free or k <= 0:
        return []
    out = []
    seen = {seed_tile}
    dq = collections.deque([seed_tile])
    while dq and len(out) < k:
        x, y = dq.popleft()
        out.append((x, y))
        for dx, dy in NB8:
            nb = (x + dx, y + dy)
            if nb in free and nb not in seen:
                seen.add(nb)
                dq.append(nb)
    return out


def _farthest_points(free_list: list, g: int, start: tuple) -> list:
    """Greedy farthest-point sampling: g spread-out tiles, deterministic."""
    chosen = [start]
    if g <= 1:
        return chosen
    # precompute squared dist to chosen incrementally
    best_d = {t: (t[0] - start[0]) ** 2 + (t[1] - start[1]) ** 2 for t in free_list}
    while len(chosen) < g:
        cand = max(free_list, key=lambda t: (best_d.get(t, -1), -t[0], -t[1]))
        if best_d.get(cand, -1) <= 0:
            break
        chosen.append(cand)
        cx, cy = cand
        for t in free_list:
            d = (t[0] - cx) ** 2 + (t[1] - cy) ** 2
            if d < best_d.get(t, 1e18):
                best_d[t] = d
    return chosen


def _guard_tile(blob: set, free: set, ref: tuple):
    """Pick the free tile 4-adjacent to the blob that is closest to `ref` (the
    approach direction) — the guard sits between the loot and the hero's approach."""
    cands = set()
    for x, y in blob:
        for dx, dy in NB4:
            nb = (x + dx, y + dy)
            if nb in free:
                cands.add(nb)
    if not cands:
        return None
    rx, ry = ref
    return min(cands, key=lambda t: ((t[0] - rx) ** 2 + (t[1] - ry) ** 2, t[0], t[1]))


def place_in_zone(zone: dict, sf: np.ndarray, rule: dict, counts: dict,
                  placed: dict, occupied: set, cp_dist: np.ndarray,
                  X: np.ndarray, Y: np.ndarray, diag: float,
                  H: int, W: int, seed: int, rng: random.Random,
                  town_pts: list) -> list:
    """RMG-style placement for one zone. Returns [(0,x,y,purpose)]."""
    zone_tiles = zone["tiles"]
    free = set(zone_tiles) - occupied
    if not free:
        return []
    cx, cy = zone["centroid"]
    # approach reference: nearest existing town, else zone centroid
    if town_pts:
        ref = min(town_pts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    else:
        ref = (int(round(cx)), int(round(cy)))
    pts: list = []

    def emit(x, y, P):
        pts.append((0, x, y, P))
        placed[P].append((x, y))
        occupied.add((x, y))
        free.discard((x, y))

    # --- 1. TOWN: central free tile ---
    for _ in range(counts.get("TOWN", 0)):
        if not free:
            break
        t = min(free, key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[0], p[1]))
        emit(t[0], t[1], "TOWN")
        ref = t   # subsequent loot in a MAIN zone is approached from its town

    # --- 2. MINES: spread out, each gets an adjacent guard ---
    n_mines = counts.get("MINE", 0)
    n_guards_left = counts.get("GUARD", 0)
    if n_mines and free:
        free_list = sorted(free)
        start = min(free_list, key=lambda p: ((p[0] - ref[0]) ** 2 + (p[1] - ref[1]) ** 2))
        mine_seeds = _farthest_points(free_list, n_mines, start)
        for (mx, my) in mine_seeds:
            if (mx, my) not in free:
                continue
            emit(mx, my, "MINE")
            if n_guards_left > 0:
                gt = _guard_tile({(mx, my)}, free, ref)
                if gt:
                    emit(gt[0], gt[1], "GUARD")
                    n_guards_left -= 1

    # --- 3. DWELLINGS near town/ref ---
    for _ in range(counts.get("DWELLING", 0)):
        if not free:
            break
        t = min(free, key=lambda p: ((p[0] - ref[0]) ** 2 + (p[1] - ref[1]) ** 2, p[0], p[1]))
        emit(t[0], t[1], "DWELLING")

    # --- 4. QUEST_GATE / TRANSPORT at chokepoints ---
    for P in ("QUEST_GATE", "TRANSPORT"):
        for _ in range(counts.get(P, 0)):
            if not free:
                break
            t = min(free, key=lambda p: (float(cp_dist[p[1], p[0]]), p[0], p[1]))
            emit(t[0], t[1], P)

    # --- 5. GUARDED TREASURE GROUPS ---
    loot = []
    for P in LOOT_PURPOSES:
        loot.extend([P] * counts.get(P, 0))
    if loot:
        if seed != 0:
            rng.shuffle(loot)
        # one group per remaining guard (each treasure group is guarded); if no guards
        # remain, make a few unguarded groups so loot still clusters.
        g = max(1, n_guards_left) if n_guards_left > 0 else max(1, len(loot) // 5)
        g = min(g, len(loot))
        free_list = sorted(free)
        if free_list:
            start = min(free_list,
                        key=lambda p: ((p[0] - ref[0]) ** 2 + (p[1] - ref[1]) ** 2))
            seeds = _farthest_points(free_list, g, start)
            if seed != 0:
                rng.shuffle(seeds)
            # distribute loot tokens round-robin across groups
            groups = [[] for _ in range(len(seeds))]
            for i, tok in enumerate(loot):
                groups[i % len(seeds)].append(tok)
            for s_tile, toks in zip(seeds, groups):
                blob = _grow_blob(s_tile, len(toks), free)
                if not blob:
                    continue
                blob_set = set(blob)
                # guard on the approach side BEFORE consuming the blob tiles
                if n_guards_left > 0:
                    gt = _guard_tile(blob_set, free - blob_set, ref)
                    if gt:
                        emit(gt[0], gt[1], "GUARD")
                        n_guards_left -= 1
                for (bx, by), tok in zip(blob, toks):
                    emit(bx, by, tok)

    # --- 6. any leftover guards: drop near the zone's loot/centroid ---
    while n_guards_left > 0 and free:
        t = min(free, key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[0], p[1]))
        emit(t[0], t[1], "GUARD")
        n_guards_left -= 1

    return pts


# ---------------------------------------------------------------------------
# Decoration rate (terrain_type -> objects per tile) from corpus
# ---------------------------------------------------------------------------

def _build_deco_rate(names: list[str]) -> dict:
    terr_tiles: collections.Counter = collections.Counter()
    deco_on: collections.Counter = collections.Counter()
    for name in names:
        m = _load_map(name)
        if not m["terrain"]:
            continue
        terr_l0 = m["terrain"][0]
        H = len(terr_l0)
        W = len(terr_l0[0])
        for row in terr_l0:
            for c in row:
                terr_tiles[c["t"]] += 1
        for o in m["objects"]:
            if o.get("l", 0) != 0:
                continue
            x, y = o["x"], o["y"]
            if 0 <= x < W and 0 <= y < H and _resolve_purpose(o) == "DECORATION":
                deco_on[terr_l0[y][x]["t"]] += 1
    return {t: deco_on[t] / max(terr_tiles[t], 1) for t in terr_tiles}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Archetype rule-based map generation.")
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--emit-vmap", action="store_true")
    ap.add_argument("--n-towns", type=int, default=0,
                    help="override town count (0 = infer from source map)")
    args = ap.parse_args()

    names = _all_map_names()
    model = AM.load()
    rules_by_label = build_rules_by_label()

    m = _load_map(args.terrain)
    terr = m["terrain"]
    terr_l0 = terr[0]
    H = len(terr_l0)
    W = len(terr_l0[0])
    diag = max(math.hypot(W, H), 1.0)

    # infer town count from the source map unless overridden
    if args.n_towns:
        n_towns = args.n_towns
    else:
        n_towns = sum(1 for o in m["objects"] if o.get("l", 0) == 0
                      and _resolve_purpose(o) == "TOWN") or 2

    print(f"terrain='{args.terrain}'  seed={args.seed}  ({H}×{W})  n_towns={n_towns}")

    # segment + subdivide
    zones, zone_label = TS.segment(terr_l0, subdivide=True)
    sf = TS.compute_static_features(terr_l0, zones, zone_label)
    print(f"sub-zones: {len(zones)}")

    # assign archetypes
    rng = random.Random(args.seed)
    labels = AM.assign_archetypes(zones, sf, n_towns, model, args.seed, rng)
    mix = collections.Counter(labels.values())
    print("archetype mix:", dict(mix))

    # chokepoint distance field (normalized): distance from each tile to the nearest
    # chokepoint, used by MINE/GUARD/QUEST_GATE couplings.
    cp_set = TS.chokepoint_set_from_zones(zones)
    Y_grid, X_grid = np.mgrid[0:H, 0:W]
    X_grid = X_grid.astype(np.float32)
    Y_grid = Y_grid.astype(np.float32)
    cp_dist = _nearest_norm(list(cp_set), X_grid, Y_grid, diag, H, W)

    # place objects zone by zone (towns first across all zones so loot can couple)
    placed: dict = collections.defaultdict(list)
    occupied: set = set()
    points: list = []
    # Process MAIN zones first so towns exist before other zones place dwellings etc.
    zone_order = sorted(zones.keys(),
                        key=lambda z: (labels[z] != "MAIN", labels[z] != "TOWN_OUTSKIRTS"))
    arch_cum: dict = collections.defaultdict(float)   # (label, purpose) -> expected so far
    for zid in zone_order:
        lab = labels[zid]
        rule = rules_by_label.get(lab)
        if rule is None:
            continue
        counts = zone_counts(rule, lab, args.seed, rng, arch_cum)
        pts = place_in_zone(zones[zid], sf, rule, counts, placed, occupied,
                            cp_dist, X_grid, Y_grid, diag, H, W, args.seed, rng,
                            placed.get("TOWN", []))
        points.extend(pts)

    gp_counts = collections.Counter(p for _, _, _, p in points)
    print(f"placed {len(points)} gameplay objects:", dict(gp_counts))

    # decorations — count from corpus per-terrain rate, but discount for the
    # gameplay objects already occupying ground, and cluster tightly (small sigma)
    # so groves wall the zone edges while interiors stay OPEN, matching real maps.
    deco_rate = _build_deco_rate(names)
    raw_deco = np.mean([deco_rate.get(terr_l0[y][x]["t"], 0.0)
                        for y in range(H) for x in range(W)]) * H * W
    n_deco = max(0, int(round(0.7 * raw_deco)))   # leave open ground between groves
    occ_3d = {(0, x, y) for (_, x, y, _) in points}
    zone_label_2d = [[zone_label[y][x] for x in range(W)] for y in range(H)]
    EP.sample_tiles_deco(terr, {"DECORATION": deco_rate}, rng, occ_3d, points, n_deco,
                         L=len(terr), H=H, W=W, zone_label=zone_label_2d,
                         sigma_override=max(diag / 26.0, 2.0), boundary_boost=5.0)
    print(f"placed {sum(1 for p in points if p[3] == 'DECORATION')} decorations")

    # to_fm → repair → traverse
    fm = EP.to_fm(terr, points, name=f"RuleGen {args.terrain} s{args.seed}")
    n_rem, ok = EP.repair_reachability(fm)
    rr = TR.traverse(fm)
    print(f"reachable: {rr['ok']}  (repair removed {n_rem}; "
          f"reached {rr['reached_tiles']}/{rr['passable_tiles']} tiles)")

    if args.emit_vmap:
        safe = args.terrain.replace(" ", "_")
        out = os.path.join(ROOT, "out", f"RuleGen-{safe}-s{args.seed}.vmap")
        faithful.to_vmap(fm, out, name=fm["name"])
        print("vmap:", out)


if __name__ == "__main__":
    main()
