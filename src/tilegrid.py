"""Per-tile token grid for VISUAL reconstruction (absolute-position fidelity).

The object-distance signature (deps_spatial.py) measures RELATIVE spatial statistics
and discards absolute position, so two different-looking maps can score identically.
This module models the map the way the eye reads it: a grid where each cell carries

  - a terrain token  (always present): terrain id 0..N
  - an object token  (EMPTY or a purpose): the object anchored at that tile

EMPTY is a first-class object token -- "this tile has no object" is most of the map
and carries the layout structure. The per-tile metric is then literal: fraction of
cells where generated token == original token at the SAME (x,y).

Anchor convention: an object is recorded at its stored (x, y) (VCMI bottom-right
anchor). Consistency between real and generated tokenization is what matters for the
metric, not which corner; both use this module.
"""

import sys, os, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON

EMPTY = "."

# Priority when two objects anchor on the same tile: keep the most gameplay-salient.
# (Collisions are rare; this only decides the single token a cell shows.)
PURPOSE_PRIORITY = [
    "TOWN", "MINE", "DWELLING", "BANK", "GUARD", "QUEST_GATE", "TRANSPORT",
    "WATER_TRANSPORT", "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA",
    "REWARD_PICKUP", "RESOURCE_PILE", "TERRAIN_MODIFIER", "INFO", "HERO",
    "SPECIAL", "DECORATION", "UNKNOWN",
]
_PRI = {p: i for i, p in enumerate(PURPOSE_PRIORITY)}

# Purposes that drive gameplay (used for the "gameplay-only" accuracy view; the
# full view includes DECORATION, which is ~69% of objects and dominates the visual).
GAMEPLAY = {
    "TOWN", "MINE", "DWELLING", "BANK", "GUARD", "QUEST_GATE", "TRANSPORT",
    "WATER_TRANSPORT", "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA",
    "REWARD_PICKUP", "RESOURCE_PILE",
}


def _purpose(o):
    return ON.resolve(o["class"], o["subclass"]).get("purpose", "UNKNOWN")


def tokenize(m):
    """corpus map dict -> {'H','W','levels', 'terrain':[l][y][x] int,
    'obj':[l][y][x] token}. obj token is EMPTY or a purpose string."""
    terr = m["terrain"]
    levels = len(terr)
    H = len(terr[0])
    W = len(terr[0][0])
    terrain = [[[terr[l][y][x]["t"] for x in range(W)] for y in range(H)] for l in range(levels)]
    obj = [[[EMPTY for _ in range(W)] for _ in range(H)] for _ in range(levels)]
    for o in m["objects"]:
        l = o.get("l", 0)
        x, y = o["x"], o["y"]
        if not (0 <= l < levels and 0 <= y < H and 0 <= x < W):
            continue
        p = _purpose(o)
        cur = obj[l][y][x]
        if cur == EMPTY or _PRI.get(p, 999) < _PRI.get(cur, 999):
            obj[l][y][x] = p
    return {"H": H, "W": W, "levels": levels, "terrain": terrain, "obj": obj}


def accuracy(gen, real):
    """Per-tile token accuracy between two tokenized grids (same dims).

    Returns terrain accuracy, object-channel accuracy, and per-purpose
    precision/recall/F1 (treating each purpose as a detection problem over cells)."""
    assert (gen["H"], gen["W"], gen["levels"]) == (real["H"], real["W"], real["levels"]), "dim mismatch"
    H, W, L = real["H"], real["W"], real["levels"]
    n = H * W * L
    terr_hit = obj_hit = obj_hit_gp = 0
    # detection bookkeeping per purpose
    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
    real_obj_cells = gen_obj_cells = 0
    for l in range(L):
        for y in range(H):
            for x in range(W):
                if gen["terrain"][l][y][x] == real["terrain"][l][y][x]:
                    terr_hit += 1
                rp = real["obj"][l][y][x]
                gp = gen["obj"][l][y][x]
                if gp == rp:
                    obj_hit += 1
                # gameplay-only object accuracy: ignore cells where both are non-gameplay
                if (rp in GAMEPLAY) or (gp in GAMEPLAY):
                    if gp == rp:
                        obj_hit_gp += 1
                if rp != EMPTY:
                    real_obj_cells += 1
                if gp != EMPTY:
                    gen_obj_cells += 1
                if rp != EMPTY and gp == rp:
                    tp[rp] += 1
                else:
                    if gp != EMPTY:
                        fp[gp] += 1
                    if rp != EMPTY:
                        fn[rp] += 1
    gp_cells = sum(1 for l in range(L) for y in range(H) for x in range(W)
                   if real["obj"][l][y][x] in GAMEPLAY or gen["obj"][l][y][x] in GAMEPLAY)
    per_purpose = {}
    for p in set(list(tp) + list(fp) + list(fn)):
        t, f_p, f_n = tp[p], fp[p], fn[p]
        prec = t / (t + f_p) if (t + f_p) else 0.0
        rec = t / (t + f_n) if (t + f_n) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_purpose[p] = {"tp": t, "fp": f_p, "fn": f_n, "precision": round(prec, 3),
                          "recall": round(rec, 3), "f1": round(f1, 3)}
    return {
        "terrain_acc": round(terr_hit / n, 4),
        "obj_acc": round(obj_hit / n, 4),
        "obj_acc_gameplay": round(obj_hit_gp / gp_cells, 4) if gp_cells else None,
        "real_obj_cells": real_obj_cells,
        "gen_obj_cells": gen_obj_cells,
        "per_purpose": per_purpose,
    }


if __name__ == "__main__":
    import json
    target = sys.argv[1] if len(sys.argv) > 1 else "All for One"
    m = json.load(open(f"{ROOT}/out/maps/{target}.json"))
    g = tokenize(m)
    print(f"map={target}  {g['levels']}x{g['H']}x{g['W']}")
    hist = collections.Counter()
    for l in range(g["levels"]):
        for row in g["obj"][l]:
            for tok in row:
                hist[tok] += 1
    total_cells = g["levels"] * g["H"] * g["W"]
    print(f"EMPTY cells: {hist[EMPTY]}/{total_cells} ({100*hist[EMPTY]/total_cells:.1f}%)")
    print("object-token histogram:", dict(hist.most_common()))
    # self-check: tokenizing the same map twice scores 1.0
    a = accuracy(g, tokenize(m))
    print("self-accuracy (sanity, expect 1.0):",
          {"terrain": a["terrain_acc"], "obj": a["obj_acc"], "gameplay": a["obj_acc_gameplay"]})
