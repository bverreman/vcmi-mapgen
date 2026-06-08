"""Deterministic per-zone filler — the heart of the template engine.

For one terrain level it:
  1. places each zone's gameplay objects per its type recipe (town, mines,
     dwellings, visitables, guards) at deterministic spread positions;
  2. carves a narrow OPEN corridor network connecting every object's approach
     tile (so the start town reaches every town & mine) — a shortest-path MST
     over passable terrain, routed around object footprints;
  3. fills every remaining passable tile with BLOCKING decoration up to the
     terrain's corpus coverage — these obstacles ARE the zone-bounding walls,
     leaving only the carved paths open.

The only stochastic part is which interchangeable SUBTYPE fills a planned slot
(seeded); positions/counts/structure are fully deterministic.

Coordinates are (x, y). A "placement" is a dict
{x,y,l,purpose,type,subtype,animation,mask}.
"""
from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import terrain_segment as TS

WATER, ROCK = 8, 9
MARGIN = 2          # wall band (tiles) inside each zone boundary -> bounded pockets
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

VISITABLE = ["MINE", "RESOURCE_PILE", "REWARD_PICKUP", "BANK", "DWELLING",
             "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA", "INFO",
             "TERRAIN_MODIFIER", "QUEST_GATE", "SPECIAL"]
# order objects are dropped in a zone (anchors first so loot couples to them)
PLACE_ORDER = ["TOWN", "MINE", "DWELLING", "QUEST_GATE", "BANK", "STAT_PERMANENT",
               "SPELL_SKILL", "BONUS_TEMP", "MANA", "INFO", "TERRAIN_MODIFIER",
               "SPECIAL", "RESOURCE_PILE", "REWARD_PICKUP"]


# ---------------------------------------------------------------------------
# grid helpers
# ---------------------------------------------------------------------------

def terrain_ints(level: list) -> list:
    return [[c["t"] for c in row] for row in level]


def passable_set(tgrid: list) -> set:
    H, W = len(tgrid), len(tgrid[0])
    return {(x, y) for y in range(H) for x in range(W)
            if tgrid[y][x] not in (WATER, ROCK)}


def components(passable: set) -> list:
    """Connected components (4-conn) of a passable tile set, largest first."""
    seen: set = set()
    comps: list = []
    for t in sorted(passable):
        if t in seen:
            continue
        comp = []
        dq = collections.deque([t])
        seen.add(t)
        while dq:
            x, y = dq.popleft()
            comp.append((x, y))
            for dx, dy in NB4:
                nb = (x + dx, y + dy)
                if nb in passable and nb not in seen:
                    seen.add(nb)
                    dq.append(nb)
        comps.append(set(comp))
    comps.sort(key=len, reverse=True)
    return comps


def shortest_path(allowed: set, start: tuple, goal_set: set) -> list:
    """BFS shortest path from start to the nearest tile in goal_set over `allowed`
    (4-conn, deterministic neighbour order). Returns the tile list incl. ends, or []."""
    if start in goal_set:
        return [start]
    prev: dict = {start: None}
    dq = collections.deque([start])
    while dq:
        cur = dq.popleft()
        for dx, dy in NB4:
            nb = (cur[0] + dx, cur[1] + dy)
            if nb in prev or nb not in allowed:
                continue
            prev[nb] = cur
            if nb in goal_set:
                path = [nb]
                while path[-1] is not None:
                    path.append(prev[path[-1]])
                return list(reversed(path[:-1]))
            dq.append(nb)
    return []


def farthest_points(tiles: list, k: int, start: tuple) -> list:
    """Greedy farthest-point spread: k tiles, deterministic."""
    if k <= 0 or not tiles:
        return []
    chosen = [start]
    best = {t: (t[0] - start[0]) ** 2 + (t[1] - start[1]) ** 2 for t in tiles}
    while len(chosen) < k:
        cand = max(tiles, key=lambda t: (best.get(t, -1), -t[0], -t[1]))
        if best.get(cand, -1) <= 0:
            break
        chosen.append(cand)
        cx, cy = cand
        for t in tiles:
            d = (t[0] - cx) ** 2 + (t[1] - cy) ** 2
            if d < best.get(t, 1 << 60):
                best[t] = d
    return chosen


# ---------------------------------------------------------------------------
# object footprint placement
# ---------------------------------------------------------------------------

def _fits(mask: list, x: int, y: int, passable: set, occupied: set, W: int, H: int) -> bool:
    """All non-empty mask cells land on passable, unoccupied, in-bounds tiles."""
    for tx, ty, _blocking in OR.mask_cells(mask, x, y):
        if not (0 <= tx < W and 0 <= ty < H):
            return False
        if (tx, ty) not in passable or (tx, ty) in occupied:
            return False
    return True


def _occupy(mask: list, x: int, y: int, occupied: set) -> None:
    for tx, ty, _b in OR.mask_cells(mask, x, y):
        occupied.add((tx, ty))


def _a_cells(mask: list, x: int, y: int) -> list:
    """World tiles of the mask's 'A' (visitable) cells."""
    hh = len(mask)
    ww = max((len(r) for r in mask), default=0)
    out = []
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            if ch == "A":
                out.append((x - (ww - 1 - c), y - (hh - 1 - r)))
    return out


def approach_tiles(mask: list, x: int, y: int, passable: set, occupied: set,
                   W: int, H: int) -> set:
    """Open tiles from which the object can be entered: its 'A' cells (if open)
    plus their 4-neighbours that are passable and not occupied."""
    res: set = set()
    for ax, ay in _a_cells(mask, x, y):
        for dx, dy in ((0, 0), *NB4):
            nx, ny = ax + dx, ay + dy
            if (0 <= nx < W and 0 <= ny < H and (nx, ny) in passable
                    and (nx, ny) not in occupied):
                res.add((nx, ny))
    return res


# ---------------------------------------------------------------------------
# per-level fill
# ---------------------------------------------------------------------------

def fill_level(level: list, l: int, zones: dict, zone_label: list, ztypes: dict,
               templates: dict, rng, extra: list | None = None) -> dict:
    """Place gameplay + carve corridors + fill decoration for one level.

    `extra` is a list of pre-decided objects (x, y, purpose, identity) — e.g.
    subterranean gates whose (x, y) is coordinated across levels — placed before
    the zone loop so the corridor network carves to them.

    Returns {placements, town_anchors, reserved_open}.
    `rng` is None for the deterministic (seed 0) path, else a seeded Random whose
    only use is subtype variety.
    """
    tgrid = terrain_ints(level)
    H, W = len(tgrid), len(tgrid[0])
    passable = passable_set(tgrid)
    comps = components(passable)
    main_comp = comps[0] if comps else set()
    comp_of = {}
    for i, comp in enumerate(comps):
        for t in comp:
            comp_of[t] = i

    occupied: set = set()         # tiles covered by any placed object footprint
    placements: list = []
    obj_records: list = []        # (placement, approach_set, purpose, comp_idx)
    town_anchors: list = []

    def emit(x, y, purpose, ident):
        p = {"x": x, "y": y, "l": l, "purpose": purpose, **ident}
        placements.append(p)
        return p

    # ---- 0. pre-placed extras (e.g. coordinated subterranean gates) ----
    for (ex, ey, epurpose, eident) in (extra or []):
        if not _fits(eident["mask"], ex, ey, passable, occupied, W, H):
            continue
        _occupy(eident["mask"], ex, ey, occupied)
        p = emit(ex, ey, epurpose, eident)
        appr = approach_tiles(eident["mask"], ex, ey, passable, occupied, W, H)
        obj_records.append((p, appr, epurpose, comp_of.get((ex, ey), -1)))

    # ---- 1. place gameplay per zone ----
    # Streaming largest-remainder: a purpose's fractional expectation accumulates
    # across zones, so rare purposes (BANK/SHRINE/INFO with density << 1/area) are
    # preserved instead of rounding to 0 in every zone.
    frac: dict = collections.defaultdict(float)

    def take(P, expected):
        before = int(frac[P])
        frac[P] += expected
        return int(frac[P]) - before

    for zid in sorted(zones):
        zone = zones[zid]
        ztype = ztypes.get(zid, "thematic")
        rec = templates["types"].get(ztype, templates["types"]["thematic"])
        dens = rec["density"]
        area = zone["area"]
        cx, cy = zone["centroid"]
        zfree = [t for t in zone["tiles"] if t in passable]
        if not zfree:
            continue
        # center reference
        ref = min(zfree, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)

        # how many of each purpose (largest-remainder accumulation)
        counts: dict = {}
        for P in PLACE_ORDER:
            if P == "TOWN":
                counts[P] = 1 if ztype == "castle" else 0
            else:
                counts[P] = take(P, dens.get(P, 0.0) * area)
        n_guard = take("GUARD", dens.get("GUARD", 0.0) * area)

        # spread anchors for everything we will place this zone
        total = sum(counts.values())
        if total <= 0 and n_guard <= 0:
            continue
        spread = farthest_points(sorted(zfree), max(total, 1), ref)
        si = 0
        guarded_first: list = []     # placements that should get a guard (high value)

        for P in PLACE_ORDER:
            terr_id = tgrid[int(round(cy))][int(round(cx))]
            for _ in range(counts.get(P, 0)):
                ident = OR.pick_variant(P, terr_id, rng)
                if not ident:
                    continue
                # find an anchor (prefer the spread points) where the footprint fits
                placed = False
                cand_order = spread[si:] + spread[:si] if spread else []
                for anch in cand_order:
                    ax, ay = anch
                    if _fits(ident["mask"], ax, ay, passable, occupied, W, H):
                        _occupy(ident["mask"], ax, ay, occupied)
                        p = emit(ax, ay, P, ident)
                        appr = approach_tiles(ident["mask"], ax, ay, passable, occupied, W, H)
                        ci = comp_of.get((ax, ay), -1)
                        obj_records.append((p, appr, P, ci))
                        if P == "TOWN":
                            town_anchors.append((ax, ay, ci))
                        if P in ("BANK", "MINE", "DWELLING", "QUEST_GATE"):
                            guarded_first.append((p, appr))
                        placed = True
                        si = (si + 1) % max(len(spread), 1)
                        break
                if not placed:
                    continue

        # ---- guards: on the approach of the most valuable visitables ----
        for p, appr in guarded_first[:n_guard]:
            terr_id = tgrid[p["y"]][p["x"]]
            gident = OR.pick_variant("GUARD", terr_id, rng)
            if not gident:
                break
            # a free tile adjacent to the object, toward the zone centre
            cands = sorted(appr, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
            for gt in cands:
                if _fits(gident["mask"], gt[0], gt[1], passable, occupied, W, H):
                    _occupy(gident["mask"], gt[0], gt[1], occupied)
                    emit(gt[0], gt[1], "GUARD", gident)
                    break

    # tiles blocked by a gameplay 'B' footprint (corridors route around these)
    block = set()
    for p in placements:
        for tx, ty, b in OR.mask_cells(p["mask"], p["x"], p["y"]):
            if b:
                block.add((tx, ty))

    # ---- 2. OPEN POCKETS: each zone's interior stays walkable ----
    # Interior = tiles at BFS-distance >= MARGIN from the zone boundary, so a band
    # of `MARGIN` tiles around every zone becomes wall — neighbouring pockets are
    # separated by ~2*MARGIN of decoration, pierced only by the carved corridors.
    reserved: set = set()                 # OPEN tiles kept clear of decoration
    zone_target: dict = {}                # zid -> open tiles to connect to
    zone_comp: dict = {}
    for zid, zone in zones.items():
        dist = TS._bfs_within_zone(zone["tiles_set"], zone["boundary_tiles"])
        interior = {t for t, d in dist.items() if d >= MARGIN and t in passable}
        if not interior:                   # passage / thin zone: keep a 1-tile spine
            interior = {t for t, d in dist.items() if d >= 1 and t in passable} \
                or (zone["tiles_set"] & passable)
        reserved |= interior
        ztiles = [t for t in zone["tiles"] if t in passable]
        zone_comp[zid] = comp_of.get(ztiles[0], -1) if ztiles else -1
        zone_target[zid] = interior or set(ztiles)

    # object approaches must stay open and reach a pocket
    for _p, appr, _P, _c in obj_records:
        reserved |= appr

    # ---- 3. carve narrow corridors: a spanning tree over adjacent zone pockets ----
    by_comp: dict = collections.defaultdict(list)
    for zid in zones:
        by_comp[zone_comp[zid]].append(zid)

    for ci, zids in by_comp.items():
        if ci < 0 or not zids:
            continue
        comp = comps[ci]
        allowed = comp - block
        zset = set(zids)
        # root the spanning tree at the largest zone in the component
        root = max(zids, key=lambda z: zones[z]["area"])
        # BFS over the zone-adjacency graph, carving each tree edge
        seen = {root}
        dq = collections.deque([root])
        while dq:
            a = dq.popleft()
            for b in sorted(zones[a]["adjacent_zones"]):
                if b not in zset or b in seen:
                    continue
                seen.add(b)
                dq.append(b)
                ta = (zone_target[a] & reserved) or zone_target[a]
                tb = (zone_target[b] & reserved) or zone_target[b]
                if not ta or not tb:
                    continue
                start = min(ta, key=lambda t: min((t[0] - g[0]) ** 2 + (t[1] - g[1]) ** 2
                                                  for g in tb))
                path = shortest_path(allowed | ta | tb, start, tb)
                if path:
                    reserved.update(path)

    # connect any object approach not yet touching an open pocket to the nearest one
    for ci, zids in by_comp.items():
        if ci < 0:
            continue
        allowed = comps[ci] - block
        anchors = reserved & comps[ci]
        for p, appr, P, c in obj_records:
            if c != ci or not appr or (appr & reserved & comps[ci]):
                continue
            if not anchors:
                break
            start = min(appr, key=lambda t: 0)
            path = shortest_path(allowed | appr | anchors, start, anchors)
            if path:
                reserved.update(path)
                anchors |= set(path)

    # ---- 4. fill the rest of the passable area with BLOCKING decoration (walls) ----
    coverable = passable - reserved - occupied
    covered: set = set()
    deco_cache: dict = {}

    def deco_variants(terr_id):
        if terr_id not in deco_cache:
            vs = OR.variants("DECORATION", terr_id)
            vs = sorted(vs, key=lambda e: -sum(1 for r in e["mask"] for ch in r if ch != " "))
            deco_cache[terr_id] = vs
        return deco_cache[terr_id]

    for (x, y) in sorted(coverable):
        if (x, y) in covered:
            continue
        t = tgrid[y][x]
        for v in deco_variants(t):
            mask = v["mask"]
            cells = list(OR.mask_cells(mask, x, y))
            ok = True
            for tx, ty, b in cells:
                if not (0 <= tx < W and 0 <= ty < H):
                    ok = False; break
                if (tx, ty) in reserved or (tx, ty) in occupied or (tx, ty) in covered \
                        or (tx, ty) not in passable:
                    ok = False; break
            if not ok:
                continue
            if rng is not None:
                alt = OR.pick_variant("DECORATION", t, rng)
                if alt and sum(1 for r in alt["mask"] for ch in r if ch != " ") == \
                        sum(1 for r in mask for ch in r if ch != " "):
                    v = alt
            emit(x, y, "DECORATION", {k: v[k] for k in ("type", "subtype", "animation", "mask")})
            for tx, ty, b in cells:
                covered.add((tx, ty))
                occupied.add((tx, ty))
            break

    return {"placements": placements, "town_anchors": town_anchors,
            "reserved_open": reserved}
