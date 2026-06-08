"""Extract zone-type TEMPLATES (recipes) from the byte-exact faithful corpus.

A template is NOT a fixed-shape patch and NOT a probabilistic model — it is a
per-zone-type *recipe* the deterministic filler (zone_fill.py) reads:

  * which gameplay objects a zone of this type holds, as a DENSITY (objects per
    tile) so it scales to any zone area;
  * how many guards relative to visitables;
  * how many connections (entries) the zone typically has;
  * the zone-area distribution;
and globally:
  * how much of each terrain's passable area real maps cover with BLOCKING
    decoration (the "wall" target).

Zone TYPES (assigned from object composition — castle/quest/dwelling by content,
passage by geometry, thematic otherwise):
    castle   — contains a town / player start
    quest    — contains a quest gate / seer / border gate
    dwelling — creature-dwelling dominant
    passage  — small, well-connected, few objects (a connector)
    thematic — everything else (treasure / economy / wilderness flavour)

Run:  uv run python src/zone_templates.py        -> out/zone_templates.json
"""
from __future__ import annotations

import collections
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import terrain_segment as TS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATER, ROCK = 8, 9

# Visitable resources the player travels TO (the filler carves paths to these).
VISITABLE = ["MINE", "RESOURCE_PILE", "REWARD_PICKUP", "BANK", "DWELLING",
             "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA", "INFO",
             "TERRAIN_MODIFIER", "QUEST_GATE", "SPECIAL"]
# Purposes whose density a recipe records (visitables + anchors + guards).
RECIPE_PURPOSES = ["TOWN", "GUARD", "TRANSPORT"] + VISITABLE

ZONE_TYPES = ["castle", "quest", "dwelling", "passage", "thematic"]


def _pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _classify(counts: dict, area: int, n_adj: int, small_area: int) -> str:
    """Assign a zone type from its object composition and geometry."""
    if counts.get("TOWN", 0) > 0:
        return "castle"
    if counts.get("QUEST_GATE", 0) > 0:
        return "quest"
    n_visit = sum(counts.get(p, 0) for p in VISITABLE)
    if counts.get("DWELLING", 0) >= 2 and counts.get("DWELLING", 0) >= 0.5 * max(n_visit, 1):
        return "dwelling"
    if area <= small_area and n_adj >= 2 and n_visit <= 2:
        return "passage"
    return "thematic"


def main() -> None:
    names = OR.all_map_names()

    # accumulators
    type_areas: dict = collections.defaultdict(list)
    type_adj: dict = collections.defaultdict(list)
    type_density: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    type_guard_ratio: dict = collections.defaultdict(list)
    type_n: collections.Counter = collections.Counter()
    # decoration coverage: per terrain, blocking-deco-covered passable tiles / passable tiles
    deco_covered: collections.Counter = collections.Counter()
    terr_passable: collections.Counter = collections.Counter()
    gate_pairs_per_map: list = []

    # First pass over a sample to set the "small zone" threshold (p25 area).
    all_areas: list = []

    per_map_zones: list = []  # cache (name, level, zones, objs_by_zone) for second pass

    for name in names:
        m = OR.load_faithful(name)
        terr = m["terrain"]
        for l, lvl in enumerate(terr):
            zones, zone_label = TS.segment(lvl, subdivide=True)
            H = len(lvl)
            W = len(lvl[0])

            # bucket objects into zones by anchor tile; tally deco coverage
            objs_by_zone: dict = collections.defaultdict(lambda: collections.Counter())
            for o in m["objects"]:
                if o.get("l", 0) != l:
                    continue
                x, y = o["x"], o["y"]
                p = OR.purpose_of(o)
                if p == "DECORATION":
                    for tx, ty, blocking in OR.mask_cells(o["mask"], x, y):
                        if 0 <= tx < W and 0 <= ty < H and blocking:
                            t = lvl[ty][tx]["t"]
                            if t not in (WATER, ROCK):
                                deco_covered[t] += 1
                    continue
                if 0 <= x < W and 0 <= y < H:
                    z = zone_label[y][x]
                    if z != -1:
                        objs_by_zone[z][p] += 1

            # passable tiles per terrain (denominator for deco coverage)
            for row in lvl:
                for c in row:
                    if c["t"] not in (WATER, ROCK):
                        terr_passable[c["t"]] += 1

            for zid, zone in zones.items():
                all_areas.append(zone["area"])
            per_map_zones.append((zones, objs_by_zone))

    small_area = _pct(all_areas, 0.25) or 60

    # second pass: classify + accumulate (uses cached zones/objs)
    for zones, objs_by_zone in per_map_zones:
        for zid, zone in zones.items():
            counts = dict(objs_by_zone.get(zid, {}))
            area = zone["area"]
            n_adj = len(zone["adjacent_zones"])
            zt = _classify(counts, area, n_adj, small_area)
            type_n[zt] += 1
            type_areas[zt].append(area)
            type_adj[zt].append(n_adj)
            for p in RECIPE_PURPOSES:
                type_density[zt][p].append(counts.get(p, 0) / max(area, 1))
            n_visit = sum(counts.get(p, 0) for p in VISITABLE)
            type_guard_ratio[zt].append(counts.get("GUARD", 0) / max(n_visit, 1))

    # build output
    out: dict = {"types": {}, "decoration": {}, "visitable_purposes": VISITABLE,
                 "small_zone_area": small_area}
    for zt in ZONE_TYPES:
        dens = {}
        for p in RECIPE_PURPOSES:
            vals = type_density[zt][p]
            # MEAN (not median): its sum over zones equals the real corpus total, so
            # the filler's largest-remainder accumulation preserves rare purposes
            # (BANK/SHRINE/INFO) whose median is 0 but mean is > 0.
            dens[p] = statistics.mean(vals) if vals else 0.0
        out["types"][zt] = {
            "n_zones": type_n[zt],
            "area": {"p25": _pct(type_areas[zt], 0.25),
                     "p50": _pct(type_areas[zt], 0.50),
                     "p75": _pct(type_areas[zt], 0.75)},
            "connections": {"p25": _pct(type_adj[zt], 0.25),
                            "p50": _pct(type_adj[zt], 0.50),
                            "p75": _pct(type_adj[zt], 0.75)},
            "density": dens,   # objects per tile, per purpose
            "guard_per_visitable": statistics.median(type_guard_ratio[zt]) if type_guard_ratio[zt] else 0.0,
        }

    cov = {}
    for t, denom in terr_passable.items():
        cov[t] = round(deco_covered[t] / denom, 4) if denom else 0.0
    out["decoration"] = {"coverage_by_terrain": cov}

    path = os.path.join(ROOT, "out", "zone_templates.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"maps={len(names)}  zones={sum(type_n.values())}  small_zone_area={small_area}")
    for zt in ZONE_TYPES:
        ti = out["types"][zt]
        topd = sorted(ti["density"].items(), key=lambda kv: -kv[1])[:4]
        print(f"  {zt:9s} n={ti['n_zones']:4d}  area_p50={ti['area']['p50']:4d}  "
              f"conn_p50={ti['connections']['p50']}  g/v={ti['guard_per_visitable']:.2f}  "
              f"top-density={[(p, round(d,4)) for p,d in topd]}")
    print("deco coverage by terrain:", out["decoration"]["coverage_by_terrain"])
    print("wrote", path)


if __name__ == "__main__":
    main()
