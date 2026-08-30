"""Loot-zone access mechanic (gate+keymaster / sealed+monolith) for small single-entrance
zones — a global, per-level pass that runs once every zone's scatter is placed.

Also owns `_solo_visit_pool`/`_shrine_spell_level`, needed by pp_pickup.place_pocket_caches
too (which stays in pp_pickup.py until Repair's own caches.py move in a later phase) —
Pickup is the first step in pipeline order to need them.
"""
import collections

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen.steps.gameplay import mines as PG
from vcmi_mapgen.steps.gameplay.water import _legal
from vcmi_mapgen.steps.pickup.scatter import _place_one

LOOT_ZONE_MAX_TILES = 80        # land zone with ≤ this many tiles, exactly one entrance cluster, no town

_LOOT_COLORS = [                # (border_gate_anim, keymaster_anim); index == VCMI subtype 0-7
    ("avxbgt00", "avxkey00"),   # 0 light blue
    ("avxbgt10", "avxkey10"),   # 1 green
    ("avxbgt20", "avxkey20"),   # 2 red
    ("avxbgt30", "avxkey30"),   # 3 dark blue
    ("avxbgt40", "avxkey40"),   # 4 brown
    ("avxbgt50", "avxkey50"),   # 5 purple
    ("avxbgt60", "avxkey60"),   # 6 white
    ("avxbgt70", "avxkey70"),   # 7 black
]
_LOOT_ART_W = {"avarnd1": 5, "avarnd2": 15, "avarnd3": 35, "avarand": 45}
_LOOT_EXCL_DECOR = frozenset({"LAKE", "FROZEN_LAKE", "RIVER_DELTA", "KELP", "REEF", "LAKE_2"})
# Visitable structures excluded from BOTH pocket caches and loot zone fill.
_FILL_EXCL_ANIMS = frozenset({"avsfntn0", "avsidol0"})  # Fountain of Fortune, Idol of Fortune
# Only shrines teaching spells at level ≥ 3 are placed in loot zones (level 1-2 are too weak).
_LOOT_SHRINE_MIN_LEVEL = 3
# Vis-pool entries excluded from loot zones only (still allowed in pockets with sep. constraint).
_LOOT_VIS_EXCL_ANIMS = frozenset({"avxwelg0", "avxwelr0", "avxwlsn0"})  # Magic Well
# REWARD_PICKUP types excluded from loot zone art/chest fill (pool_art + pool_chest).
_LOOT_ART_EXCL_TYPES = frozenset({"leanTo", "wagon", "warriorTomb", "denOfThieves"})
# Two-way monolith pairs for sealed teleport loot zones (ci > 0).
# Both ends of each pair use the SAME animation → same subtype → they teleport to each other.
# Subtypes monolith1-4 (simple 1-4 cell, no blocking body) suit small pockets best.
_LOOT_MONOLITHS = ["avxmn2g0", "avxmn2o0", "avxmn2p0", "avxmn4b0"]

_SOLO_VIS_PURPOSES = ("BONUS_TEMP", "SPELL_SKILL", "MANA", "STAT_PERMANENT")


def _shrine_spell_level(anim):
    """Spell level a shrine teaches from its animation name (avxlNsh0 → N), or 0 if not a shrine."""
    import re
    m = re.match(r"avxl(\d)sh", anim, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _solo_visit_pool(terrain, exclude_anims=frozenset(), min_shrine_level=None):
    """Objects with exactly one visit tile and no blocking body cells — the 'christmas-green'
    category (shrines, magic wells, fountains, etc.).  These fit inside a single open tile
    and are safe to cache inside pockets.

    exclude_anims: animation names to skip entirely.
    min_shrine_level: when set, shrines teaching spells below this level are excluded
        (non-shrine objects are unaffected)."""
    pool = []
    seen = set()
    for purpose in _SOLO_VIS_PURPOSES:
        for ident in ON.gameplay_pool(terrain, purpose):
            anim = ident.get("animation", "").lower()
            if anim in seen or anim in exclude_anims:
                continue
            if min_shrine_level is not None:
                lvl = _shrine_spell_level(anim)
                if lvl > 0 and lvl < min_shrine_level:
                    continue
            mask = ident.get("mask", [])
            n_visit = sum(1 for row in mask for ch in row if ch in "AX")
            n_body  = sum(1 for row in mask for ch in row if ch == "B")
            if n_visit == 1 and n_body == 0:
                seen.add(anim)
                pool.append(ident)
    return pool


def place_loot_zones(zone_records, entrance_plan, objs_existing, seed=1, bounds=None,
                     water_tiles=None):
    """Loot-zone access mechanic for small single-entrance zones.

    A 'loot zone' has ≤ LOOT_ZONE_MAX_TILES tiles, exactly one 8-connected cluster of
    'blue' passage tiles at its boundary (physical single-entrance check), and no town.
    Dense fill (hero-strengthening structures, major/relic artifacts, resource piles) is
    placed in every qualifying zone.  Access mechanic is chosen 50/50 per zone:

      gate   (50 %): BORDER_GATE placed at the entrance + matching-colour KEYMASTER in a
               non-loot zone far from castles and far from other exterior partners.  The
               hero must first find the tent then return to the gate.  All other passage
               tiles are sealed with vegetation.

      mono   (50 %): all passage tiles are FULLY sealed — the zone becomes a walled
               pocket.  A TWO-WAY MONOLITH is placed inside and a matching one outside
               (far from castles and other exterior partners), so the only way in is the
               external monolith.

    The outer object (keymaster / exterior monolith) is pre-checked before the inner
    object is committed, so no permanently impassable gate or unreachable interior is
    ever left on the map.  Returns (objs, n_placements, sealed_zid_set).
    """
    import random

    town_tiles = {(o["x"], o["y"]) for o in objs_existing if o.get("purpose") == "TOWN"}

    # Pre-compute full tile set of all zones for boundary detection.
    # water_tiles comes from the caller (grid-level water, never in zone_records).
    water_ts = set(water_tiles) if water_tiles else set()
    _all_ts = set()
    for _zr in zone_records:
        _all_ts |= _zr["ts"]

    def _passage_components(zr):
        """Count 8-connected clusters of zone tiles that border any tile of another
        zone (terrain-tile adjacency, independent of placed vegetation).  This is the
        topological single-entrance check: 1 cluster = 1 direction of connectivity.
        Returns (n_clusters, frozenset_of_boundary_tiles)."""
        ts = zr["ts"]
        ext_ts = _all_ts - ts
        boundary = {t for t in ts
                    if any((t[0] + dx, t[1] + dy) in ext_ts
                           for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                                          (1, 1), (1, -1), (-1, 1), (-1, -1)])}
        seen, n = set(), 0
        for s in sorted(boundary):
            if s in seen:
                continue
            n += 1
            q = collections.deque([s])
            seen.add(s)
            while q:
                cx, cy = q.popleft()
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                    nb = (cx + dx, cy + dy)
                    if nb in boundary and nb not in seen:
                        seen.add(nb)
                        q.append(nb)
        return n, frozenset(boundary)

    _DIRS8 = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]

    loot_zrs = []   # list of (zone_record, passage_tile_frozenset)
    for zr in zone_records:
        if len(zr["ts"]) > LOOT_ZONE_MAX_TILES:
            continue
        if any(t in town_tiles for t in zr["ts"]):
            continue
        n_clusters, passage_tiles = _passage_components(zr)
        if n_clusters != 1:
            continue
        # No water adjacency: no boundary tile may be 8-adjacent to a water tile.
        if water_ts and any(
            (t[0]+dx, t[1]+dy) in water_ts
            for t in passage_tiles
            for dx, dy in _DIRS8
        ):
            continue
        loot_zrs.append((zr, passage_tiles))

    if not loot_zrs:
        return [], 0, set()

    loot_zids = {zr["zid"] for zr, _ in loot_zrs}
    ext_no_castle = [zr for zr in zone_records
                     if zr["zid"] not in loot_zids
                     and not any(t in town_tiles for t in zr["ts"])]
    ext_any = [zr for zr in zone_records if zr["zid"] not in loot_zids]

    placed_ext_tiles = []   # positions of exterior partners already placed

    def _far_score(zr):
        free = zr["reach"] - zr["used"]
        if not free:
            return (-1, 0, 0)
        cx = sum(x for x, _ in zr["ts"]) / len(zr["ts"])
        cy = sum(y for _, y in zr["ts"]) / len(zr["ts"])
        d_castle = (min((cx - tx) ** 2 + (cy - ty) ** 2
                        for tx, ty in town_tiles) ** 0.5
                    if town_tiles else 1e9)
        d_partner = (min((cx - px) ** 2 + (cy - py) ** 2
                         for px, py in placed_ext_tiles) ** 0.5
                     if placed_ext_tiles else 1e9)
        return (d_castle + d_partner, len(free))

    def _find_ext_spot(ext_ident, ext_pool):
        """Return (zone_record, tile) farthest from castles and from existing
        exterior partners (keymasters / exterior monoliths already placed)."""
        for cand in sorted(ext_pool, key=_far_score, reverse=True):
            free = sorted(cand["reach"] - cand["used"])
            if not free:
                continue
            def _tscore(t):
                d_c = (min((t[0] - tx) ** 2 + (t[1] - ty) ** 2
                           for tx, ty in town_tiles) ** 0.5
                       if town_tiles else 1e9)
                d_p = (min((t[0] - px) ** 2 + (t[1] - py) ** 2
                           for px, py in placed_ext_tiles) ** 0.5
                       if placed_ext_tiles else 1e9)
                return d_c + d_p
            free.sort(key=_tscore, reverse=True)
            ts_set = cand["ts"]
            op_set = cand["open_set"]
            for t in free:
                tx, ty = t
                # [N N]   (tx-1,ty-1) (tx,  ty-1)
                # [N X]   (tx-1,ty)   (tx,  ty)  ← anchor
                # All three N-cells must be clear: either outside this zone or
                # inside it and in open_set (not occupied by vegetation or objects).
                if not all(
                    (cx, cy) not in ts_set
                    or ((cx, cy) in op_set and (cx, cy) not in cand["used"])
                    for cx, cy in ((tx - 1, ty - 1), (tx, ty - 1), (tx - 1, ty))
                ):
                    continue
                if _legal(ext_ident, tx, ty, cand["reach"],
                          cand["used"], bounds=bounds) is not None:
                    return cand, t
        return None, None

    def _seal_all_passages(ts, open_set, used, terrain, rng, skip_cells=frozenset()):
        """Fill EVERY boundary tile of the loot zone (tile in ts that is 8-adjacent
        to a tile outside ts) with a single-cell blocking vegetation object, perfectly
        sealing the perimeter including any passable V-overlay cells of the gate.

        skip_cells: the gate's or monolith's interactive tile(s) — the one spot a hero
        must stand on to activate the access object; these are NOT sealed."""
        ext_ts = _all_ts - ts
        veg_pool = ON.decor_pool(terrain, blocking=True, max_cells=1,
                                 exclude_types=_LOOT_EXCL_DECOR)
        if not veg_pool:
            return
        for t in sorted(ts):
            if t in skip_cells:
                continue  # access object's interactive tile — must stay passable
            tx, ty = t
            if not any((tx + dx, ty + dy) in ext_ts
                       for dx, dy in [(1,0),(-1,0),(0,1),(0,-1),
                                      (1,1),(1,-1),(-1,1),(-1,-1)]):
                continue  # interior tile — left for loot
            objs[:] = [o for o in objs if not (o.get("purpose") == "GUARD"
                                                and o["x"] == tx and o["y"] == ty)]
            iv = rng.choice(veg_pool)
            used.add(t)
            objs.append({"x": tx, "y": ty, "l": 0,
                         "type": iv.get("type"), "subtype": iv.get("subtype"),
                         "animation": iv["animation"], "mask": iv["mask"],
                         "template": {"animation": iv["animation"],
                                      "mask": iv["mask"]}})

    def _fill_loot(terrain, st, reach, used, rng):
        """Three-pass loot fill: hero-strengthening structures → mixed rewards → background decor.

        Pass 0 (bg): non-blocking terrain decor on interior tiles (under gameplay objects).
        Pass 1 (30 %): solo-visitable hero-strengthening structures.
        Pass 2: 30 % major/relic artifact, 30 % chest/campfire, 40 % rare resource pile
                (mercury, sulfur, crystal, gems, gold — no wood/ore)."""
        # Pass 0: background — non-blocking terrain decor on interior (non-boundary) tiles.
        ext_ts_inner = _all_ts - reach
        interior = {t for t in reach
                    if not any((t[0]+dx, t[1]+dy) in ext_ts_inner for dx, dy in _DIRS8)}
        pool_bg = ON.decor_pool(terrain, blocking=False, max_cells=1,
                                exclude_types=_LOOT_EXCL_DECOR)
        if pool_bg:
            for t in sorted(interior):
                if rng.random() < 0.5:
                    iv = rng.choice(pool_bg)
                    objs.append({"x": t[0], "y": t[1], "l": 0,
                                 "type": iv.get("type"), "subtype": iv.get("subtype"),
                                 "animation": iv["animation"], "mask": iv["mask"],
                                 "template": {"animation": iv["animation"],
                                              "mask": iv["mask"]}})

        pool_vis = _solo_visit_pool(terrain,
                                    exclude_anims=_FILL_EXCL_ANIMS | _LOOT_VIS_EXCL_ANIMS,
                                    min_shrine_level=_LOOT_SHRINE_MIN_LEVEL)
        pool_art = [i for i in ON.gameplay_pool(terrain, "REWARD_PICKUP")
                    if i.get("type") not in _LOOT_ART_EXCL_TYPES]
        pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
        # chest-type pickups: treasure chests, campfires — 'ava*' is the artifact namespace.
        # chest-type only: treasure chests, campfires — explicitly exclude artifacts so
        # named artifacts with non-'ava' animations (e.g. 'avssword0') can't slip in here.
        pool_chest = [i for i in pool_art if i.get("type") != "artifact"]
        # High-tier artifacts only (major + relic).
        arts_high = [(a, _LOOT_ART_W[a]) for a in ("avarnd3", "avarand") if a in _LOOT_ART_W]
        # Rare resources: mercury(1), sulfur(3), crystal(4), gems(5), gold(6) — no wood(0)/ore(2).
        pool_rare = [i for i in pool_res if i.get("subtype") not in {0, 2}]

        free = sorted(reach - used)
        rng.shuffle(free)

        # Pass 1: hero-strengthening structures — 30 % of available tiles
        n_vis = max(1, len(free) * 3 // 10)
        vis_placed = 0
        for t in free:
            if not pool_vis or vis_placed >= n_vis:
                break
            iv = rng.choice(pool_vis)
            if _place_one(objs, used, reach, rng, st,
                          iv.get("purpose", "BONUS_TEMP"), None,
                          t[0], t[1], ident=iv, cache=True, bounds=bounds,
                          interactive_only=True):
                vis_placed += 1

        # Pass 2: 30 % major/relic artifact | 30 % chest/campfire | 40 % rare resource
        for t in sorted(reach - used):
            roll = rng.random()
            if roll < 0.3:
                if arts_high:
                    ai = ON.identity_of(rng.choices(
                        [a for a, _ in arts_high],
                        weights=[w for _, w in arts_high], k=1)[0])
                    if ai:
                        _place_one(objs, used, reach, rng, st, "REWARD_PICKUP", pool_art,
                                   t[0], t[1], ident=ai, cache=True, bounds=bounds,
                                   interactive_only=True)
            elif roll < 0.6:
                ai = rng.choice(pool_chest) if pool_chest else None
                if ai:
                    _place_one(objs, used, reach, rng, st, "REWARD_PICKUP", pool_art,
                               t[0], t[1], ident=ai, cache=True, bounds=bounds,
                               interactive_only=True)
            else:
                ri = rng.choice(pool_rare) if pool_rare else (rng.choice(pool_res) if pool_res else None)
                if ri:
                    _place_one(objs, used, reach, rng, st, "RESOURCE_PILE", pool_res,
                               t[0], t[1], ident=ri, cache=True, bounds=bounds,
                               interactive_only=True)

    objs, n_placed = [], 0
    processed_loot_zids = set()   # zones whose entrance was actually sealed this run
    gate_count, mono_count = 0, 0

    for loot_zr, passage_tiles in sorted(loot_zrs, key=lambda x: x[0]["zid"]):
        zid      = loot_zr["zid"]
        terrain  = loot_zr["terrain"]
        st       = PG.mine_gameplay()[terrain]
        ts       = loot_zr["ts"]
        reach    = loot_zr["reach"]
        used     = loot_zr["used"]
        open_set = loot_zr.get("open_set")
        rng      = random.Random(seed ^ (zid * 92821) ^ 0xA117)
        ext_pool = ext_no_castle or ext_any
        passage_cx = sum(t[0] for t in passage_tiles) / len(passage_tiles)
        passage_cy = sum(t[1] for t in passage_tiles) / len(passage_tiles)

        # Clear scatter vegetation so the whole interior is available for loot.
        objs_existing[:] = [o for o in objs_existing if (o["x"], o["y"]) not in ts]
        objs[:] = [o for o in objs if (o["x"], o["y"]) not in ts]
        used.clear()
        # After clearing, all zone tiles are passable (loot zones have no gameplay
        # blockers — no town, no mine).  The stored open_set/reach were computed with
        # dense vegetation in place (~70 % blocking) so they cover only ~30 % of ts.
        # Reset both to the full tile set so seal and fill can reach every tile.
        open_set = ts
        reach    = ts

        # Determine which side of the loot zone's bounding box the passage is on.
        ts_xs = [t[0] for t in ts]; ts_ys = [t[1] for t in ts]
        bbox_x0, bbox_x1 = min(ts_xs), max(ts_xs)
        bbox_y0, bbox_y1 = min(ts_ys), max(ts_ys)
        d_top    = passage_cy - bbox_y0
        d_bottom = bbox_y1 - passage_cy
        d_left   = passage_cx - bbox_x0
        d_right  = bbox_x1 - passage_cx
        passage_side = min(
            [("top", d_top), ("bottom", d_bottom), ("left", d_left), ("right", d_right)],
            key=lambda s: s[1]
        )[0]

        use_gate = rng.random() < 0.5
        if use_gate:
            # ── Border Gate + Keymaster ──────────────────────────────────────
            # Gate mask ['VVVV','VBXB']: 4-wide × 2-tall, anchor = bottom-right.
            # V-row at y-1 (passable/exterior), blocking-row at y (loot zone side).
            # Sort candidates so the blocking-row aligns with the passage side:
            #   top/bottom → anchor y at boundary row, x centred on passage
            #   left/right → anchor y at passage cy, x so the gate span covers passage x
            gate_anim, key_anim = _LOOT_COLORS[gate_count % len(_LOOT_COLORS)]
            gate_ident = ON.identity_of(gate_anim)
            key_ident  = ON.identity_of(key_anim)
            if gate_ident is None or key_ident is None:
                continue

            km_zr, km_t = _find_ext_spot(key_ident, ext_pool)
            if km_t is None:
                continue

            def _gate_score(t):
                gx, gy = t
                if passage_side in ("top", "bottom"):
                    bnd_y = bbox_y0 if passage_side == "top" else bbox_y1
                    return (abs(gy - bnd_y), abs((gx - 1.5) - passage_cx))
                else:  # left / right
                    # anchor x so gate's span (gx-3 .. gx) covers the passage x
                    ideal_x = bbox_x0 + 3 if passage_side == "left" else bbox_x1
                    return (abs(gy - passage_cy), abs(gx - ideal_x))

            gate_tile = None
            for t in sorted(ts, key=_gate_score):
                gx, gy = t
                gate_cells = [(cx, cy)
                              for cx, cy, _ in OR.mask_cells(gate_ident["mask"], gx, gy)]
                if bounds:
                    bw, bh = bounds
                    if any(not (0 <= cx < bw and 0 <= cy < bh) for cx, cy in gate_cells):
                        continue
                interactive = OR.mask_interactive_cells(gate_ident["mask"], gx, gy)
                if not all(c in open_set for c in interactive):
                    continue
                # Clear any object (vegetation, guard) whose footprint overlaps the gate's
                # full cell set — including V-row cells that may be in the exterior zone.
                fp = set(gate_cells)
                cleared = set()
                for src in (objs_existing, objs):
                    victims = [o for o in src
                               if any((cx, cy) in fp
                                      for cx, cy, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]
                    for o in victims:
                        src.remove(o)
                        for cx, cy, _ in OR.mask_cells(o["mask"], o["x"], o["y"]):
                            cleared.add((cx, cy))
                for zr in zone_records:
                    zr["used"] -= cleared
                used.update(gate_cells)
                objs.append({"x": gx, "y": gy, "l": 0, "purpose": "QUEST_GATE",
                             "type": gate_ident.get("type"),
                             "subtype": gate_ident.get("subtype"),
                             "animation": gate_ident["animation"],
                             "mask": gate_ident["mask"],
                             # Allow approach from all 8 directions so the gate is
                             # visitable from the exterior (above the VVVV row), not
                             # only from the interior side.
                             "visitableFrom": ["+++", "+-+", "+++"],
                             "template": {"animation": gate_ident["animation"],
                                          "mask": gate_ident["mask"]}})
                gate_tile = t
                break
            if gate_tile is None:
                continue

            # Verify seal leaves the gate's interactive tile with both an interior
            # neighbor (so loot is reachable) and at least one passable exterior
            # neighbor (so the gate can be approached and activated from outside).
            sealed_boundary = passage_tiles - set(interactive)
            passable_after_seal = ts - sealed_boundary
            if not any((sk[0]+dx, sk[1]+dy) in passable_after_seal
                       for sk in interactive for dx, dy in _DIRS8):
                continue
            # Check exterior access: at least one non-loot-zone tile adjacent to
            # the interactive cell must be passable (not occupied/blocked by objects).
            ext_blocked = set()
            for o in objs + objs_existing:
                for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                    if blk:
                        ext_blocked.add((cx, cy))
            has_ext_access = any(
                (sk[0]+dx, sk[1]+dy) not in ts and (sk[0]+dx, sk[1]+dy) not in ext_blocked
                and 0 <= sk[0]+dx < (bounds[0] if bounds else 999)
                and 0 <= sk[1]+dy < (bounds[1] if bounds else 999)
                for sk in interactive for dx, dy in _DIRS8
            )
            if not has_ext_access:
                continue

            _seal_all_passages(ts, open_set, used, terrain, rng,
                               skip_cells=set(interactive))
            processed_loot_zids.add(zid)
            _fill_loot(terrain, st, open_set, used, rng)

            km_rng = random.Random(seed ^ (zid * 131071) ^ 0xCEBF)
            km_st  = PG.mine_gameplay()[km_zr["terrain"]]
            placed = _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                "QUEST_GATE", None, km_t[0], km_t[1],
                                ident=key_ident, bounds=bounds)
            if not placed:
                for t in sorted(km_zr["reach"] - km_zr["used"]):
                    if _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                 "QUEST_GATE", None, t[0], t[1],
                                 ident=key_ident, bounds=bounds):
                        placed = True
                        break
            if placed:
                n_placed += 1
                gate_count += 1
                placed_ext_tiles.append(km_t)
                gident_km = PG.rnd_monster(7)
                for t in sorted(km_zr["reach"] - km_zr["used"],
                                key=lambda t: max(abs(t[0] - km_t[0]),
                                                  abs(t[1] - km_t[1]))):
                    if max(abs(t[0] - km_t[0]), abs(t[1] - km_t[1])) > 1:
                        break
                    if _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                  "GUARD", None, t[0], t[1], ident=gident_km,
                                  bounds=bounds):
                        break

        else:
            # ── Fully sealed + Two-Way Monolith pair ─────────────────────────
            mono_anim  = _LOOT_MONOLITHS[mono_count % len(_LOOT_MONOLITHS)]
            mono_ident = ON.identity_of(mono_anim)
            if mono_ident is None:
                continue

            ext_zr, ext_t = _find_ext_spot(mono_ident, ext_pool)
            if ext_t is None:
                continue

            # Place monolith at zone centroid (deepest interior tile).
            ts_cx = sum(t[0] for t in ts) / len(ts)
            ts_cy = sum(t[1] for t in ts) / len(ts)
            int_t = None
            for t in sorted(reach - used,
                            key=lambda t: (t[0] - ts_cx) ** 2 + (t[1] - ts_cy) ** 2):
                if _legal(mono_ident, t[0], t[1], reach, used, bounds=bounds) is not None:
                    int_t = t
                    break
            if int_t is None:
                continue

            _place_one(objs, used, reach, rng, st, "TRANSPORT", None,
                      int_t[0], int_t[1], ident=mono_ident, bounds=bounds)
            mono_interactive = set(OR.mask_interactive_cells(mono_ident["mask"],
                                                             int_t[0], int_t[1]))
            _seal_all_passages(ts, open_set, used, terrain, rng,
                               skip_cells=mono_interactive)
            processed_loot_zids.add(zid)
            _fill_loot(terrain, st, open_set, used, rng)

            ext_rng = random.Random(seed ^ (zid * 131071) ^ 0xCEBF)
            ext_st  = PG.mine_gameplay()[ext_zr["terrain"]]
            placed  = _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                 "TRANSPORT", None, ext_t[0], ext_t[1],
                                 ident=mono_ident, bounds=bounds)
            if not placed:
                for t in sorted(ext_zr["reach"] - ext_zr["used"]):
                    if _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                 "TRANSPORT", None, t[0], t[1],
                                 ident=mono_ident, bounds=bounds):
                        placed = True
                        break
            if placed:
                n_placed += 1
                mono_count += 1
                placed_ext_tiles.append(ext_t)
                gident_ext = PG.rnd_monster(7)
                for t in sorted(ext_zr["reach"] - ext_zr["used"],
                                key=lambda t: max(abs(t[0] - ext_t[0]),
                                                  abs(t[1] - ext_t[1]))):
                    if max(abs(t[0] - ext_t[0]), abs(t[1] - ext_t[1])) > 1:
                        break
                    if _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                  "GUARD", None, t[0], t[1], ident=gident_ext,
                                  bounds=bounds):
                        break

    return objs, n_placed, processed_loot_zids
