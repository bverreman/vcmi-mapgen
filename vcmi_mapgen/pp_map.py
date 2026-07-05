"""End-to-end L0+L2+L3+L4 map render: terrain + gameplay + vegetation + pickups.

Pipeline:  macro_topo.generate (capacity-constrained zones, textured borders)
        -> zone_engine.tile_terrain (corpus-learned TRANSITION views: shores, terrain edges)
        -> segment into zones
        -> per land zone:
             pp_gameplay.place_zone  (towns/mines/dwellings/shrines; corpus densities,
                                      anchors SAMPLED from the fitted L3 intensity)
             protected walkable web  (backbone + gates + every gameplay approach tile,
                                      routed AROUND gameplay footprints)
             pp_sample.sample_zone   (corpus-fitted Gibbs marked point process; overlap
                                      legal; gameplay cells + web are hard zeros)
             pp_pickup.place_pickups (resources/artifacts over the OPEN field: unguarded
                                      scatter on routes, guarded caches in pockets with a
                                      monster on the mouth)
        -> render_editor.render_map with REAL 32px H3 sprites.

    uv run python -m vcmi_mapgen.pp_map --seed 3 --size 72
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faithful as FA           # noqa: E402
import macro_topo as MTOPO      # noqa: E402
import obj_resolve as OR        # noqa: E402
import ontology as ON           # noqa: E402
import pp_gameplay as PG        # noqa: E402
import pp_pickup as PK          # noqa: E402
import pp_sample as PP          # noqa: E402
import zone_engine as ZE        # noqa: E402
import zone_field as ZF         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_AREA = 25          # vegetate even smallish zones (the stats floor stays 60 in pp_stats)


def g2_repair(size, grid, objs, targets):
    """Map-level G2 validity gate + repair: every target tile (gameplay approach, pickup)
    must be reachable from every other across zone borders. Pickups/monsters count as
    passable (they are removable); vegetation is carvable; gameplay bodies and water/rock
    are not. Unreachable targets get a least-vegetation corridor carved to them (Dijkstra:
    open=1, veg-blocked=40) and the intersecting VEGETATION objects are deleted.
    Returns (objs, removed_count)."""
    import heapq
    import obj_resolve as OR
    W = H = size
    land = {(x, y) for y in range(H) for x in range(W) if grid[y][x] < 8}

    def veg_cells():
        cells = collections.defaultdict(list)        # blocking cell -> [veg obj idx]
        hard = set()                                 # gameplay bodies: never carved
        for i, o in enumerate(objs):
            purpose = o.get("purpose")
            removable_pickup = purpose in ("RESOURCE_PILE", "REWARD_PICKUP", "GUARD")
            for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if not blk or removable_pickup:
                    continue
                if purpose is None:
                    cells[(cx, cy)].append(i)
                else:
                    hard.add((cx, cy))
        return cells, hard

    removed = set()
    for _round in range(6):
        cells, hard = veg_cells()
        open_set = land - set(cells) - hard
        targets_in = [t for t in targets if t in open_set]
        if not targets_in:
            break
        root = targets_in[0]
        seen = {root}
        q = collections.deque([root])
        while q:
            x, y = q.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in open_set and n not in seen:
                        seen.add(n)
                        q.append(n)
        bad = [t for t in targets_in if t not in seen]
        if not bad:
            break
        # one Dijkstra over the whole field (veg priced high, gameplay/water = wall), then
        # carve to the first bad target that is land-connectable at all — targets on OTHER
        # ISLANDS are legitimately boat-reachable only and must not abort the repair loop
        dist = {root: 0.0}
        prev = {}
        heap = [(0.0, root)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, 1e18):
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (u[0] + dx, u[1] + dy)
                    if n not in land or n in hard:
                        continue
                    nd = d + (40.0 if n in cells else 1.0)
                    if nd < dist.get(n, 1e18):
                        dist[n] = nd
                        prev[n] = u
                        heapq.heappush(heap, (nd, n))
        tgt = next((t for t in bad if t in prev), None)
        if tgt is None:
            break                                    # all remaining bad targets are off-island
        node = tgt
        while node != root:
            for i in cells.get(node, ()):
                removed.add(i)
            node = prev[node]
    if removed:
        objs = [o for i, o in enumerate(objs) if i not in removed]
    return objs, len(removed)


def fill_open_islands(size, grid, objs, targets, seed=1):
    """User-mandated: no empty, unreachable open ground. `g2_repair` above only guards
    NAMED targets (gameplay approaches, pickups) — ordinary open tiles that vegetation
    happened to wall off entirely are invisible to it, and `pp_pickup` deliberately never
    scatters onto them (its own `_web_dist` reach check), so they end up walkable-looking
    yet permanently unreachable AND empty. Any open-set component that touches no `targets`
    tile is such an island: cheaply reconnect it (<=3 vegetation cells carved, reusing
    g2_repair's Dijkstra) when possible, else fill its own tiles with blocking decoration so
    the gap reads as a deliberate obstacle instead of an oversight.
    Returns (objs, n_reconnected, n_filled)."""
    import heapq
    import random
    rng = random.Random(seed ^ 0xF17)
    W = H = size
    land = {(x, y) for y in range(H) for x in range(W) if grid[y][x] < 8}
    terrain_of = {(x, y): ZE.TNAME.get(grid[y][x]) for (x, y) in land}

    cells = collections.defaultdict(list)         # blocking cell -> [veg obj idx]
    hard = set()                                  # gameplay/pickup bodies: never carved/filled
    for i, o in enumerate(objs):
        for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if not blk:
                continue
            if o.get("purpose") is None:
                cells[(cx, cy)].append(i)
            else:
                hard.add((cx, cy))
    open_set = land - set(cells) - hard
    reach_targets = {t for t in targets if t in open_set}

    seen, comps = set(), []
    for t0 in sorted(open_set):
        if t0 in seen:
            continue
        comp, q = {t0}, [t0]
        while q:
            x, y = q.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in open_set and n not in comp:
                    comp.add(n)
                    q.append(n)
        seen |= comp
        comps.append(comp)

    islands = [c for c in comps if not (c & reach_targets)]
    if not islands:
        return objs, 0, 0

    removed, filled_tiles = set(), []
    for comp in islands:
        root = sorted(comp)[0]
        dist, prev, best = {root: 0.0}, {}, None
        heap = [(0.0, root)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, 1e18):
                continue
            if u in reach_targets:
                best = u
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (u[0] + dx, u[1] + dy)
                if n not in land or n in hard:
                    continue
                nd = d + (40.0 if n in cells else 1.0)
                if nd < dist.get(n, 1e18):
                    dist[n] = nd
                    prev[n] = u
                    heapq.heappush(heap, (nd, n))
        if best is not None and dist[best] <= 120.0:      # cheap carve only: <=3 veg cells
            node = best
            while node != root:
                for i in cells.get(node, ()):
                    removed.add(i)
                node = prev[node]
            continue
        filled_tiles.extend(comp)

    if removed:
        objs = [o for i, o in enumerate(objs) if i not in removed]
    n_filled = 0
    if filled_tiles:
        by_terrain = collections.defaultdict(list)
        for t in filled_tiles:
            by_terrain[terrain_of.get(t)].append(t)
        for terrain, tiles in by_terrain.items():
            if terrain is None:
                continue
            pool = ON.decor_pool(terrain, blocking=True, max_cells=1,
                                 exclude_types=ZE.EXCLUDE_DECOR_TYPES)
            if not pool:
                continue
            for (x, y) in tiles:
                ident = rng.choice(pool)
                objs.append({"x": x, "y": y, "l": 0,
                            "type": ident.get("type"), "subtype": ident.get("subtype"),
                            "animation": ident["animation"], "mask": ident["mask"],
                            "template": {"animation": ident["animation"],
                                        "mask": ident["mask"]}})
                n_filled += 1
    return objs, len(removed), n_filled


def export_vmap(cells, objs, out_path, name="pp-map"):
    """Write the generated map as an editor-valid .vmap via the proven faithful writer.
    The first town becomes player 0's start (faithful.to_vmap wires mainTown slots)."""
    towns = [o for o in objs if o.get("purpose") == "TOWN"]
    fm = {"name": name, "terrain": [cells],
          "objects": [o for o in objs if o.get("type")],
          "main_town": ({"l": 0, "x": towns[0]["x"] - 2, "y": towns[0]["y"] - 2}
                        if towns else None)}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    return FA.to_vmap(fm, out_path, name=name)


def parse_teams(spec, n):
    """Team matrix from a CLI spec: 'ffa' (everyone alone), '2v2'/'1v3'-style groups, or an
    explicit comma list of team ids ('0,0,1,1'). Returns one team id per player."""
    if not spec or spec == "ffa":
        return list(range(n))
    if "v" in spec:
        sizes = [int(s) for s in spec.split("v")]
        if sum(sizes) != n:
            raise ValueError(f"teams {spec!r} sums to {sum(sizes)}, but players={n}")
        return [ti for ti, s in enumerate(sizes) for _ in range(s)]
    out = [int(s) for s in spec.split(",")]
    if len(out) != n:
        raise ValueError(f"teams {spec!r} lists {len(out)} ids, but players={n}")
    return out


def apply_playability(vmap_path, player_towns, teams):
    """Deterministic playability overlay on an exported .vmap:

      1. exactly len(player_towns) playable slots, slot i wired to its designated town
         (any faction allowed — the towns are usually randomTown) — AND the town OBJECT
         itself gets `options.owner = <player>` (the header's mainTown alone does NOT
         assign ownership; without the owner the town stays neutral),
      2. the team matrix (`teams[i]` = team id of player i; VCMI allies equal ids),
      3. victory = DEFEAT ALL (the canonical standardWin triggered event; standardDefeat =
         7 days without town), any special victory conditions stripped.
    """
    import json
    import zipfile
    from collections import defaultdict
    z = zipfile.ZipFile(vmap_path)
    files = {n: z.read(n) for n in z.namelist()}
    z.close()
    h = json.loads(files["header.json"].decode())
    vobjs = json.loads(files["objects.json"].decode())
    pids = sorted(p for p, pl in h["players"].items() if isinstance(pl, dict))
    for i, pid in enumerate(pids):
        pl = h["players"][pid]
        if i < len(player_towns):
            t = player_towns[i]
            pl["mainTown"] = {"generateHero": True, "l": t.get("l", 0),
                              "x": t["x"] - 2, "y": t["y"] - 2}
            pl["canPlay"] = "PlayerOrAI"
            pl["team"] = int(teams[i])
            if t.get("type") == "town":
                # concrete start town (spare-neutral top-up): the lobby must not offer
                # factions the map cannot honour — restrict to the authored one, exactly
                # like VCMI's own RMG maps do
                pl["allowedFactions"] = {"anyOf": [f"core:{t['subtype']}"]}
                pl.pop("randomFaction", None)
            else:
                # randomTown start: any faction; VCMI resolves the OWNED random town to
                # the lobby pick (CGTownInstance::randomizeFaction). PlayerInfo::defaultCastle()
                # only returns RANDOM when isFactionRandom is set — an absent/permissive
                # allowedFactions alone still defaults the lobby dropdown to the first
                # faction (Castle) sorted by id. Field name from MapFormatJson.cpp's
                # serializePlayerInfo: handler.serializeBool("randomFaction", ...).
                pl.pop("allowedFactions", None)
                pl["randomFaction"] = True
            for vo in vobjs:                         # ownership lives on the town object
                if (vo["x"] == t["x"] and vo["y"] == t["y"]
                        and vo.get("l", 0) == t.get("l", 0)
                        and vo.get("type") in ("town", "randomTown")):
                    vo.setdefault("options", {})["owner"] = pid
                    break
        else:
            pl["mainTown"] = None
            pl["canPlay"] = "false"
            pl.pop("team", None)
    # VCMI's lobby/map-select screen reads alliances from this top-level grouping —
    # not from each player's individual "team" int above — so it must be set for
    # the UI to show teams at all. Real VCMI RMG maps omit the key entirely for FFA.
    groups = defaultdict(list)
    for i, pid in enumerate(pids[:len(player_towns)]):
        groups[int(teams[i])].append(pid)
    allied = [members for members in groups.values() if len(members) > 1]
    if allied:
        h["teams"] = allied
    else:
        h.pop("teams", None)
    files["objects.json"] = json.dumps(vobjs, indent=1).encode()
    MSG = {"exactStrings": None, "localStrings": None, "message": [2], "numbers": None}
    h["triggeredEvents"] = {
        "standardVictory": {
            "condition": ["standardWin", {"type": "", "value": -1}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "victory"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.659"])},
        "standardDefeat": {
            "condition": ["daysWithoutTown", {"type": "", "value": 7}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "defeat"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.7"])}}
    h["victoryIconIndex"] = 11                       # "defeat all enemies"
    h["victoryMessage"] = dict(MSG, stringsTextID=["core.vcdesc.0"])
    h["defeatIconIndex"] = 3
    h["defeatMessage"] = dict(MSG, stringsTextID=["core.lcdesc.0"])
    files["header.json"] = json.dumps(h, indent=1).encode()
    with zipfile.ZipFile(vmap_path, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, data in files.items():
            zo.writestr(n, data)
    return vmap_path


def select_player_zones(zones, players):
    """Deterministic player-zone pick: big land zones that are MUTUALLY FAR APART, so the
    players do not all start next to each other. Candidates are land zones >= 60 tiles,
    preferring real zones (>= 100 tiles and >= 1/4 of the largest). The first pick is the
    largest zone; each next pick greedily maximizes the minimum centroid distance to the
    zones already chosen (tie-break: area desc, zid). Returns player_zids in player order."""
    cand = [(z["area"], zid, z["centroid"]) for zid, z in zones.items()
            if ZE.TNAME.get(z["terrain_type"]) in PG.LAND and z["area"] >= 60]
    if not cand or players <= 0:
        return []
    cand.sort(key=lambda c: (-c[0], c[1]))
    amax = cand[0][0]
    pool = [c for c in cand if c[0] >= max(100, amax // 4)]
    if len(pool) < players:                          # too few big zones: admit smaller ones
        pool = cand
    chosen = [pool[0]]
    rest = pool[1:]
    while len(chosen) < players and rest:
        best = max(rest, key=lambda c: (min((c[2][0] - ch[2][0]) ** 2
                                            + (c[2][1] - ch[2][1]) ** 2 for ch in chosen),
                                        c[0], -c[1]))
        chosen.append(best)
        rest.remove(best)
    return [zid for _a, zid, _c in chosen]


def build(seed=3, size=72, water=None, players=0, water_mode="normal"):
    """Generate macro terrain + gameplay + vegetation.
    Returns (cells, surf, objs, info, player_towns): with players > 0 the player zones are
    picked by `select_player_zones` (big AND mutually far apart) and each is guaranteed a
    town at its CENTROID; `player_towns` lists those town objects in player order.
    `water_mode`: 'none' | 'normal' | 'islands' (see macro_topo.generate)."""
    grid = MTOPO.generate(size, size, seed=seed, water=water, water_mode=water_mode)
    W = H = size
    # corpus-learned autotiling: despeckles + picks the H3-correct transition view per tile
    cells = ZE.tile_terrain(grid, W, H)
    grid = [[c["t"] for c in row] for row in cells]
    surf = [[FA.tile_string(c) for c in row] for row in cells]
    zones, zl, _ = ZE._segment_level(cells)

    player_zids = select_player_zones(zones, players)
    if players and len(player_zids) < players:
        print(f"  WARNING: only {len(player_zids)} zones can host a player town "
              f"(requested {players})")
    town_of_zone = {}

    # MAP-level mine economy: every basic resource covered somewhere, gold rationed to the
    # town count (zones are visited in sorted-zid order — deterministic)
    ledger = {"missing": set(PG.BASIC_MINE_RES), "towns": len(player_zids), "gold": 0}
    gstats = PG.mine_gameplay()

    models = {}
    objs = []
    targets = []                                     # G2: tiles that must stay reachable
    nz = ngame = npick = 0
    zone_records = []                                 # for the global pocket-cache pass
    # water is a segmentation BARRIER (never a zone) — populate its connected bodies directly:
    # flotsam / sea chests / buoys / boats / whirlpools / wrecks / sea guards
    water = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == 8}
    has_water = bool(water)
    seen_w = set()
    wi = 0
    for t0 in sorted(water):
        if t0 in seen_w:
            continue
        comp, q = {t0}, [t0]
        while q:
            x, y = q.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in water and n not in comp:
                    comp.add(n)
                    q.append(n)
        seen_w |= comp
        if len(comp) >= MIN_AREA:
            wobjs = PK.place_water(comp, zones, 1000 + wi, seed=seed)
            objs.extend(wobjs)
            npick += len(wobjs)
            print(f"  sea  {wi:>3} water    {len(comp):>5} tiles: {len(wobjs):>3} sea objects")
        wi += 1

    for zid, z in sorted(zones.items()):
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < MIN_AREA:
            continue
        ts = set(z["tiles_set"])
        coastal = frozenset(t for t in ts
                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                            if 0 <= t[0] + dx < W and 0 <= t[1] + dy < H
                            and grid[t[1] + dy][t[0] + dx] == 8)

        # L3 gameplay first: rigid objects at spread nodes (corpus densities, ontology pools)
        gobjs, occupied, gblocked, approaches = PG.place_zone(ts, zones, zid, terrain,
                                                              seed=seed, coastal=coastal,
                                                              force_town=zid in player_zids,
                                                              ledger=ledger, has_water=has_water)
        objs.extend(gobjs)
        ngame += len(gobjs)
        if zid in player_zids:
            t = next((o for o in gobjs if o.get("purpose") == "TOWN"), None)
            if t is not None:
                town_of_zone[zid] = t
            else:
                print(f"  WARNING: player zone {zid} could not fit its town")

        # protected walkable web: backbone + gates + gameplay approaches, routed around the
        # IMPASSABLE gameplay cells (approach tiles themselves are passable and stay nodes)
        edist = ZF.edge_dist(ts)
        cx, cy = z["centroid"]
        seedt = min(ts, key=lambda t: (t[0] - int(round(cx))) ** 2
                    + (t[1] - int(round(cy))) ** 2)
        prot = PP.protected_web(ts, zones, zid, edist, seedt,
                                extra_nodes=approaches, avoid=gblocked,
                                open_frac=gstats[terrain].get("border_open_frac", 0.5))

        # L2 vegetation: gameplay cells + approaches admit no vegetation at all; the
        # annulus around MINE footprints ATTRACTS vegetation (sawmills nestle in forest)
        if terrain not in models:
            models[terrain] = PP.build_model(terrain)
        model = models[terrain]
        if not model["cats"]:
            continue
        forbid = frozenset(occupied) | frozenset(approaches)
        mine_cells = {(cx, cy) for o in gobjs if o.get("purpose") == "MINE"
                      for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]) if blk}
        attract = frozenset(
            t for t in ts if t not in forbid
            and 2 <= min(max(abs(t[0] - mx), abs(t[1] - my)) for mx, my in mine_cells) <= 3
        ) if mine_cells else frozenset()             # annulus 2..3: greenery frames the
        # mine without sprite canopies overhanging its visual
        zobjs, blocked, _ = PP.sample_zone(ts, zones, zid, model, seed=seed,
                                           prot=prot, forbid=forbid, attract=attract)
        objs.extend(zobjs)

        # L4a scatter over the finished open field (unguarded resources/artifacts along
        # routes); approach tiles stay clear — mines may already carry their guard there.
        # Guarded pocket caches are NOT placed per zone — see the global pass below, which
        # must run once the whole map's zones are done (a pocket's neck is only genuine when
        # judged against TRUE map-wide passability, not one zone's reach alone).
        open_set = ts - blocked - gblocked - set(occupied) - set(approaches)
        # TRUE physical passability, for pocket GEOMETRY only: unlike `open_set` (placement
        # eligibility — excludes approach tiles and non-blocking occupied cells so new
        # objects can't stack on them), this only drops tiles that are actually impassable.
        # Approach tiles and non-blocking occupied footprint cells ARE walkable in-game.
        passable = ts - blocked - gblocked
        sobjs, sused, reach = PK.place_scatter(ts, zones, zid, terrain, open_set, prot, seed=seed,
                                              bounds=(W, H))
        objs.extend(sobjs)
        npick += len(sobjs)
        targets.extend(approaches)
        targets.extend((o["x"], o["y"]) for o in sobjs)
        nz += 1
        zone_records.append({"zid": zid, "terrain": terrain, "ts": ts,
                             "open_set": open_set, "passable": passable,
                             "reach": reach, "used": sused})
        pk = collections.Counter(o["purpose"] for o in sobjs)
        print(f"  zone {zid:>3} {terrain:<8} {z['area']:>5} tiles: {len(gobjs):>2} gameplay, "
              f"{len(zobjs):>4} veg (blocked {len(blocked) / len(ts):.2f}/"
              f"{model['target']:.2f}), scatter res={pk.get('RESOURCE_PILE', 0)} "
              f"art={pk.get('REWARD_PICKUP', 0)}")

    # G2 map-level gate + island repair MUST run before pocket detection (user-mandated:
    # "the pocket detection should run after the map is fully crafted" — a pocket is a
    # zone-independent property of the FINAL, fully-repaired passability field, not of the
    # raw per-zone snapshot taken while vegetation/gameplay were still being placed).
    # `g2_repair` carves vegetation to reconnect unreachable targets; `fill_open_islands`
    # either reconnects (cheap carve) or permanently fills any leftover empty, unreachable
    # open-set component. Diff `objs` by identity before/after so `zone_records`'
    # `passable`/`open_set` can be patched to match — otherwise pocket geometry would still
    # be judged against the stale, pre-repair snapshot even though the objects themselves
    # already changed.
    def _blocking_cells(o):
        return [(cx, cy) for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]) if blk]

    objs_before_g2 = list(objs)
    objs, ncarved = g2_repair(size, grid, objs, targets)
    removed_g2 = [o for o in objs_before_g2
                  if id(o) not in {id(x) for x in objs}]

    objs_before_fill = list(objs)
    ids_before_fill = {id(o) for o in objs_before_fill}
    objs, nreconn, nfilled = fill_open_islands(size, grid, objs, targets, seed=seed)
    ids_after_fill = {id(o) for o in objs}
    removed_fill = [o for o in objs_before_fill if id(o) not in ids_after_fill]
    added_fill = [o for o in objs if id(o) not in ids_before_fill]

    zone_of_tile = {}
    zr_by_zid = {zr["zid"]: zr for zr in zone_records}
    for zr in zone_records:
        for t in zr["ts"]:
            zone_of_tile[t] = zr["zid"]
    for o in removed_g2 + removed_fill:               # vegetation carved away -> walkable again
        for cx, cy in _blocking_cells(o):
            zr = zr_by_zid.get(zone_of_tile.get((cx, cy)))
            if zr is not None:
                zr["passable"].add((cx, cy))
                zr["open_set"].add((cx, cy))
    for o in added_fill:                               # new blocking filler -> now impassable
        for cx, cy in _blocking_cells(o):
            zr = zr_by_zid.get(zone_of_tile.get((cx, cy)))
            if zr is not None:
                zr["passable"].discard((cx, cy))
                zr["open_set"].discard((cx, cy))

    # L4b guarded pocket caches: ONE global, zone-independent pass over the whole map's
    # reachable field now that every zone's terrain/vegetation/scatter AND the map-level
    # repair passes above are finalized (user-mandated 2026-07-04 — see
    # pp_pickup.place_pocket_caches docstring for the rationale).
    cobjs, n_pockets = PK.place_pocket_caches(zone_records, seed=seed, bounds=(W, H))
    objs.extend(cobjs)
    npick += len(cobjs)
    targets.extend((o["x"], o["y"]) for o in cobjs)
    ck = collections.Counter(o["purpose"] for o in cobjs)
    print(f"  pockets: {n_pockets} found map-wide, cache res={ck.get('RESOURCE_PILE', 0)} "
          f"art={ck.get('REWARD_PICKUP', 0)} guard={ck.get('GUARD', 0)}")

    # both sides of one corridor may have guarded the same gate — keep only the stronger
    # of any two GUARDs within Chebyshev 2 (deterministic scan order). A mine's own guard
    # must never be dropped this way (mines are user-mandated to always be guarded) — it
    # sits Chebyshev 1 from the mine's footprint, so protect any guard that close to one.
    kept, drop = [], set()
    guards = [(i, o) for i, o in enumerate(objs) if o.get("purpose") == "GUARD"]
    mine_cells = [
        (mx, my) for o in objs if o.get("purpose") == "MINE"
        for mx, my, _ in OR.mask_cells(o["mask"], o["x"], o["y"])
    ]
    protected = {
        ia for ia, oa in guards
        if any(max(abs(oa["x"] - mx), abs(oa["y"] - my)) <= 1 for mx, my in mine_cells)
    }
    for a in range(len(guards)):
        ia, oa = guards[a]
        if ia in drop:
            continue
        for b in range(a + 1, len(guards)):
            ib, ob = guards[b]
            if ib in drop:
                continue
            if max(abs(oa["x"] - ob["x"]), abs(oa["y"] - ob["y"])) <= 2:
                if ia in protected and ib in protected:
                    continue  # both gate a real mine — never drop either
                if ia in protected:
                    drop.add(ib)
                elif ib in protected:
                    drop.add(ia)
                else:
                    # randomMonsterLevelN sorts by N lexically (levels 1..7)
                    drop.add(ib if str(oa.get("type")) >= str(ob.get("type")) else ia)
    if drop:
        objs = [o for i, o in enumerate(objs) if i not in drop]

    if ledger["missing"]:
        print(f"  WARNING: mine coverage incomplete — missing {sorted(ledger['missing'])} "
              f"(map too small / too few mine slots)")
    veg_n = sum(1 for o in objs if not o.get("purpose"))
    info = (f"pp-map s{seed} {W}x{H}: {nz} zones, {len(objs) - veg_n} gameplay+pickups "
            f"({len(drop)} dup guards removed), {veg_n} vegetation objects, "
            f"mines all-basics={'yes' if not ledger['missing'] else 'NO'} "
            f"gold={ledger['gold']}/{max(0, ledger['towns'] - 1)} towns={ledger['towns']}"
            + (f" (G2 repair carved {ncarved} veg)" if ncarved else " (G2 clean)")
            + (f" (islands: {nreconn} reconnected, {nfilled} filled)"
               if (nreconn or nfilled) else ""))
    # player towns in zone-rank order; top up from surplus neutral towns if a forced
    # placement failed (rare: no legal anchor in the zone)
    player_towns = [town_of_zone[z] for z in player_zids if z in town_of_zone]
    if players:
        spare = [o for o in objs if o.get("purpose") == "TOWN" and o not in player_towns]
        player_towns += spare[:max(0, players - len(player_towns))]
        player_towns = player_towns[:players]
    return cells, surf, objs, info, player_towns


VCMI_MAPS_DIR = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/pp-gen"


def gen_one(seed, size, water=None, water_mode="normal", players=2, teams_spec="ffa",
            vmap=True, install=False, tag="", name=None):
    """Generate one playable map: PNG render + (optionally) a playable .vmap; with
    `install` the .vmap is also copied into the VCMI Maps/pp-gen/ folder so the editor
    lists it. Returns (png_path, vmap_path_or_None)."""
    cells, surf, objs, info, ptowns = build(seed=seed, size=size, water=water,
                                            players=players, water_mode=water_mode)
    print(info)
    import render_editor as RED
    stem = f"ppmap_s{seed}{tag}"
    png = os.path.join(ROOT, "out", "render", "pp", f"{stem}.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    RED.render_map(surf, objs, title="").save(png)
    print("->", png)
    if not vmap:
        return png, None
    label = f"{name}{tag}" if name else f"pp {stem} {water_mode} {players}p"
    vp = export_vmap(cells, objs, os.path.join(ROOT, "out", "vmap", f"{stem}.vmap"),
                     name=label)
    if ptowns:
        try:
            teams = parse_teams(teams_spec, len(ptowns))
        except ValueError as e:
            print(f"  WARNING: {e} — falling back to ffa")
            teams = list(range(len(ptowns)))
        apply_playability(vp, ptowns, teams)
        print(f"   playable: {len(ptowns)} players, teams={teams}, victory=defeat-all")
    if install:
        import shutil
        os.makedirs(VCMI_MAPS_DIR, exist_ok=True)
        shutil.copy2(vp, os.path.join(VCMI_MAPS_DIR, os.path.basename(vp)))
        print(f"   installed -> {VCMI_MAPS_DIR}/{os.path.basename(vp)}")
    print("->", vp)
    return png, vp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--size", type=int, default=72)
    ap.add_argument("--water", type=float, default=None,
                    help="explicit water fraction override")
    ap.add_argument("--water-mode", choices=["none", "normal", "islands"], default=None,
                    dest="water_mode",
                    help="water style; in --batch, unset cycles all three for diversity")
    ap.add_argument("--vmap", action="store_true",
                    help="also export a playable .vmap to out/vmap/")
    ap.add_argument("--players", type=int, default=2,
                    help="number of players; the N largest zones each get a start town")
    ap.add_argument("--teams", default="ffa",
                    help="team matrix: 'ffa', '2v2'-style groups, or explicit '0,0,1,1'")
    ap.add_argument("--batch", type=int, default=0,
                    help="generate N maps (seeds seed..seed+N-1); water modes cycle unless "
                         "--water-mode is set; implies --vmap")
    ap.add_argument("--install", action="store_true",
                    help="copy the .vmap(s) into the VCMI Maps/pp-gen/ folder so the "
                         "editor can open them (explicit opt-in)")
    ap.add_argument("--name", default=None,
                    help="custom map display name shown in VCMI's map selector "
                         "(default: auto-generated from seed/water-mode/players)")
    args = ap.parse_args()

    if args.batch:
        modes = [args.water_mode] if args.water_mode else ["normal", "islands", "none"]
        for i in range(args.batch):
            seed = args.seed + i
            mode = modes[i % len(modes)]
            print(f"=== batch {i + 1}/{args.batch}: seed={seed} water={mode} ===")
            gen_one(seed, args.size, water=args.water, water_mode=mode,
                    players=args.players, teams_spec=args.teams, vmap=True,
                    install=args.install, tag=f"_{mode}", name=args.name)
        return
    gen_one(args.seed, args.size, water=args.water,
            water_mode=args.water_mode or "normal", players=args.players,
            teams_spec=args.teams, vmap=args.vmap or args.install, install=args.install,
            name=args.name)


if __name__ == "__main__":
    main()
