"""GameplayStep — towns, mines, dwellings, water bodies, seaports, and the zone-level ledger."""
from __future__ import annotations

import collections

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen.steps.vegetation import sample as PP  # protected_web
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen import zone_field as ZF
from vcmi_mapgen.pipeline import LevelWorkspace, MapState, PipelineStep, PlacementWorkspace, ZoneWorkspace
from vcmi_mapgen.steps.gameplay import mines as MN
from vcmi_mapgen.steps.gameplay import water as WT

MIN_AREA = 25          # vegetate even smallish zones (the stats floor stays 60 in vegetation)


def _rim8(zones):
    """The 8-connected inter-zone rim: every tile with an 8-neighbour in another zone
    (both sides of every border). Diagonal contact counts — corner-cutting is a legal
    hero move in H3, so a diagonal-only touch leaks exactly like a shared edge."""
    owner = {}
    for zid, z in zones.items():
        for t in z["tiles_set"]:
            owner[t] = zid
    return {t for t, zid in owner.items()
            if any(owner.get((t[0] + dx, t[1] + dy), zid) != zid for dx, dy in ZF.NB8)}


def _run_level_gameplay(level, W, H, grid, zones, player_zids, ledger, gstats, seed,
                        has_subterrain, gate_occ=frozenset(), gate_blk=frozenset(),
                        gate_appr=(), tunnel_protect=frozenset()):
    """Gameplay-only half of the map-generation pass: water-body population (surface only),
    per-zone ``mines.place_zone`` + protected web, and the seaport guarantee. Originally
    split out of the (now-retired) legacy ``pp_map._run_level``/``build()`` so both paths
    called the identical logic during the migration (pipeline-refactor-v2-folders.md);
    ``GameplayStep.run()`` is its only caller now that path is gone.

    Returns (objs, zone_cache, entrance_plan, has_water, town_of_zone, ridge, seaport_blk,
    seaport_appr, water_tiles): ``zone_cache`` is ``{zid: {...}}`` with the same keys
    `_run_level`'s Pass 2 (vegetation/pickup, still legacy for now) already expects."""
    objs = []
    town_of_zone = {}

    # map-level isolation plan: 1-2 aligned narrow crossings per adjacent zone pair,
    # computed ONCE over all zones so both sides agree where the entrances are. Everything
    # downstream keys off it: gameplay keeps footprints off the bands and guards the reps,
    # the protected web keeps only the bands vegetation-free (not the legacy wide corpus-open
    # share of the front), and the vegetation sampler actively densifies the rest of the
    # border (`border=` bias) so zones read as isolated regions with a few real entrances.
    # `seal_zone_borders` below then closes whatever aligned holes the statistics left.
    entrance_plan = ZF.plan_entrances(zones)
    ridge = set()                                    # all rim tiles minus entrance bands
    rim_all = _rim8(zones)                           # 8-connected inter-zone rim, both sides

    has_water = False
    water_tiles = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == 8}
    if level == 0:
        # water is a segmentation BARRIER (never a zone) — populate its connected bodies
        # directly: flotsam / sea chests / buoys / boats / whirlpools / wrecks / sea guards
        water = water_tiles
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
                wobjs = WT.place_water(comp, zones, 1000 + wi, seed=seed)
                objs.extend(wobjs)
                print(f"  sea  {wi:>3} water    {len(comp):>5} tiles: {len(wobjs):>3} sea objects")
            wi += 1

    # ── Pass 1: L3 gameplay for all zones ─────────────────────────────────────
    # Seaports are placed after all gameplay objects are known (so conflict
    # detection is complete), but BEFORE vegetation so veg forbids their footprint.
    zone_cache = {}   # zid → per-zone data needed for pass 2
    for zid, z in sorted(zones.items()):
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < MIN_AREA:
            continue
        ts_full = set(z["tiles_set"])
        ts = ts_full
        z_gate_occ = ts_full & gate_occ
        z_gate_blk = ts_full & gate_blk
        z_gate_appr = tuple(a for a in gate_appr if a in ts_full)
        coastal = frozenset(t for t in ts
                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                            if 0 <= t[0] + dx < W and 0 <= t[1] + dy < H
                            and grid[t[1] + dy][t[0] + dx] == 8)

        # L3 gameplay first: rigid objects at spread nodes (corpus densities, ontology pools)
        # `avoid` keeps every footprint/approach off the corridor protect set — gameplay
        # runs before `protected_web`, so without this a town/mine/monster could wall off
        # a tunnel that vegetation-forbidding alone could never have touched.
        z_entr = entrance_plan.get(zid, [])
        gobjs, occupied, gblocked, approaches = MN.place_zone(
            ts, zones, zid, terrain, seed=seed, coastal=coastal,
            force_town=zid in player_zids, ledger=ledger, has_water=has_water,
            level=level, has_subterrain=has_subterrain, avoid=tunnel_protect & ts,
            preoccupied=z_gate_occ, preblocked=z_gate_blk, preapproaches=z_gate_appr,
            entrances=z_entr)
        objs.extend(gobjs)
        if zid in player_zids:
            t = next((o for o in gobjs if o.get("purpose") == "TOWN"), None)
            if t is not None:
                town_of_zone[zid] = t
            else:
                print(f"  WARNING: player zone {zid} (level {level}) could not fit its town")

        # protected walkable web: backbone + gates + gameplay approaches, routed around the
        # IMPASSABLE gameplay cells (approach tiles themselves are passable and stay nodes)
        edist = ZF.edge_dist(ts)
        zcx, zcy = z["centroid"]
        seedt = min(ts, key=lambda t: (t[0] - int(round(zcx))) ** 2
                    + (t[1] - int(round(zcy))) ** 2)
        # the zone's 8-connected rim: every tile with an 8-neighbour in ANOTHER zone
        # (diagonal corner-cutting is a legal hero move, so diagonal-only contact leaks
        # like a front tile). The web routes off it, scatter skips it, the sampler's
        # border bias targets it, and repair prices carving it at 400.
        ent_bands = set().union(*(b for _r, b, _o in z_entr)) if z_entr else set()
        rim8 = rim_all & ts
        ridge |= rim8 - ent_bands
        prot = PP.protected_web(ts, zones, zid, edist, seedt,
                                extra_nodes=approaches, avoid=gblocked,
                                open_frac=gstats[terrain].get("border_open_frac", 0.5),
                                entrances=z_entr, keep_off=rim8)
        prot = prot | (tunnel_protect & ts)
        zone_cache[zid] = dict(
            terrain=terrain, ts=ts, ts_full=ts_full,
            gobjs=gobjs, occupied=occupied, gblocked=gblocked, approaches=approaches,
            z_entr=z_entr, prot=prot, rim8=rim8, ent_bands=ent_bands,
        )

    # ── Seaport placement: after all gameplay objects, before vegetation ────────
    # Seaports are treated as gameplay objects: they block vegetation, their
    # approach tile is added to targets, and their footprint is excluded from
    # scatter open sets.  Placed here so the veg pass below can forbid their cells.
    if level == 0:
        ship_objs = WT._ensure_water_seaports(W, H, grid, zones, objs, seed)
        if ship_objs:
            objs.extend(ship_objs)
            print(f"  L{level} seaport guarantee: {len(ship_objs)} shipyard(s) added")

    # Seaport blocking cells + approach tile — exclude from vegetation in pass 2.
    # The approach tile (one tile south of the X cell) must stay walkable so a
    # hero can board the ship.
    seaport_blk = set()
    seaport_appr = set()
    for _so in objs:
        if _so.get("type") == "shipyard":
            for _scx, _scy, _sblk in OR.mask_cells(_so["mask"], _so["x"], _so["y"]):
                if _sblk:
                    seaport_blk.add((_scx, _scy))
            seaport_appr.add((_so["x"] - 1, _so["y"] + 1))

    return (objs, zone_cache, entrance_plan, has_water, town_of_zone, frozenset(ridge),
            frozenset(seaport_blk), frozenset(seaport_appr), frozenset(water_tiles))


class GameplayStep(PipelineStep):
    """Place towns, mines, dwellings, water bodies, seaports, and border seals per zone.

    Config:
        seed       RNG seed.
        players    Number of player zones to designate (0 = neutral map).
        workspace  Shared ``PlacementWorkspace``, written here for Vegetation/Pickup/Repair.

    Reads: ``state.grids``, ``state.zones``, ``state.gate_occ/blk/appr``,
           ``state.gate_objs``, ``state.subterrain``, ``state.tunnel_protect``.

    Writes: ``state.objs`` (all levels, underground tagged ``l=1``),
            ``state.targets``, ``state.zone_records`` (empty per level — populated by
            VegetationStep/PickupStep), ``state.player_zids``, ``state.player_towns``,
            ``state.ledger``, and ``self.workspace.levels[level]`` (a ``LevelWorkspace``
            with a ``ZoneWorkspace`` per zone, ``ridge``, and ``town_of_zone`` for
            RepairStep — ``guard_tiles``/``seal_avoid``/``hard_avoid`` come from later
            steps' own border-seal pass, not from here).
    """

    def __init__(self, seed: int = 3, players: int = 0,
                 workspace: PlacementWorkspace | None = None) -> None:
        self.seed = seed
        self.players = players
        self.workspace = workspace

    def run(self, state: MapState, ontology) -> None:
        W = H = state.size

        player_zids = MN.select_player_zones(state.zones, self.players)
        if self.players and len(player_zids) < self.players:
            print(f"  WARNING: only {len(player_zids)} zones can host a player town "
                  f"(requested {self.players})")

        zids_by_level: dict = collections.defaultdict(set)
        for lvl, zid in player_zids:
            zids_by_level[lvl].add(zid)

        ledger = {
            "missing": set(MN.BASIC_MINE_RES),
            "towns": len(player_zids),
            "gold": 0,
        }

        all_town_of_zone: dict = {}
        all_ridge: dict = {}
        all_objs: list = []

        for level in sorted(state.grids):
            grid = state.grids[level]
            zones = state.zones[level]
            gstats = MN.mine_gameplay(level=level)

            gate_occ = state.gate_occ.get(level, frozenset())
            gate_blk = state.gate_blk.get(level, frozenset())
            gate_appr = state.gate_appr.get(level, ())
            tunnel_protect = frozenset(state.tunnel_protect) if level == 1 else frozenset()

            (objs, zone_cache, entrance_plan, _has_water, town_of_zone, ridge,
             seaport_blk, seaport_appr, water_tiles) = _run_level_gameplay(
                level, W, H, grid, zones, zids_by_level[level],
                ledger, gstats, self.seed, state.subterrain,
                gate_occ=gate_occ, gate_blk=gate_blk, gate_appr=gate_appr,
                tunnel_protect=tunnel_protect,
            )

            # merge pre-placed gate objects for this level
            level_gate_objs = [o for o in state.gate_objs
                               if o.get("l", 0) == level]
            objs.extend(level_gate_objs)

            # retag underground objects so downstream steps can partition by level
            if level == 1:
                for o in objs:
                    o["l"] = 1

            all_objs.extend(objs)
            all_town_of_zone[level] = town_of_zone
            all_ridge[level] = ridge

            if self.workspace is not None:
                zone_workspaces = {
                    zid: ZoneWorkspace(
                        terrain=c["terrain"], ts=frozenset(c["ts"]),
                        ts_full=frozenset(c["ts_full"]), gobjs=c["gobjs"],
                        occupied=frozenset(c["occupied"]),
                        gblocked=frozenset(c["gblocked"]),
                        approaches=tuple(c["approaches"]), entrances=c["z_entr"],
                        prot=frozenset(c["prot"]), rim8=frozenset(c["rim8"]),
                        ent_bands=frozenset(c["ent_bands"]),
                    )
                    for zid, c in zone_cache.items()
                }
                self.workspace.levels[level] = LevelWorkspace(
                    zones=zone_workspaces, entrance_plan=entrance_plan, ridge=ridge,
                    seaport_blk=seaport_blk, seaport_appr=seaport_appr,
                    water_tiles=water_tiles, town_of_zone=town_of_zone,
                )

        state.objs = all_objs
        state.targets = {level: [] for level in state.grids}
        state.zone_records = {level: [] for level in state.grids}
        state.player_zids = player_zids
        state.ledger = ledger

        # resolve player towns from town_of_zone; top up from surplus neutral towns if a
        # forced placement failed (rare: no legal anchor in the zone) — matches legacy
        # build()'s fallback so a player is never left without a start town.
        player_towns = []
        for lvl, zid in player_zids:
            t = all_town_of_zone.get(lvl, {}).get(zid)
            if t is not None:
                player_towns.append(t)
        if self.players:
            spare = [o for o in all_objs if o.get("purpose") == "TOWN" and o not in player_towns]
            player_towns += spare[:max(0, self.players - len(player_towns))]
            player_towns = player_towns[:self.players]
        state.player_towns = player_towns

        if ledger["missing"]:
            print(f"  WARNING: mine coverage incomplete — missing "
                  f"{sorted(ledger['missing'])}")
