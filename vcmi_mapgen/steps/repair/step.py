"""RepairStep — border seal, unreachable-zone rescue, G2 repair, island fill, pocket caches."""
from __future__ import annotations

import collections

from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen.kit.terrain_lookup import TNAME
from vcmi_mapgen.kit.topology import find_pockets
from vcmi_mapgen.pipeline import MapState, PipelineStep, PlacementWorkspace
from vcmi_mapgen.steps.repair import border_seal as BS
from vcmi_mapgen.steps.repair import caches as CA
from vcmi_mapgen.steps.repair import geometry as GEO


def _find_start(state: MapState, workspace: PlacementWorkspace):
    """Return (level, (x, y)) for the first player town, or centroid of the
    largest surface land zone when there are no players."""
    for lvl, zid in state.player_zids:
        lvl_ws = workspace.levels.get(lvl)
        t = lvl_ws.town_of_zone.get(zid) if lvl_ws is not None else None
        if t is not None:
            return (lvl, (t["x"], t["y"]))
    # No players — use nearest-to-centroid tile of the largest surface land zone
    zones0 = state.zones.get(0, {})
    big = max(
        (z for z in zones0.values()
         if TNAME.get(z["terrain_type"]) not in (None, "water", "rock")),
        key=lambda z: z["area"],
        default=None,
    )
    if big is not None:
        bx, by = big["centroid"]
        return (0, min(big["tiles_set"],
                       key=lambda t: ((t[0] - bx) ** 2 + (t[1] - by) ** 2, t)))
    return None


def _repair_one_level(level, size, grid, objs, targets, zone_records, seed,
                      boat_ok=True, ridge=frozenset(), seerhut_artifacts=None,
                      border_guards=frozenset()):
    """G2 map-level gate + island repair + guarded pocket caches + dup-guard cleanup for ONE
    already-fully-populated level (gates included, border already sealed). MUST run before
    pocket detection (user-mandated: "the pocket detection should run after the map is fully
    crafted" — a pocket is a zone-independent property of the FINAL, fully-repaired
    passability field, not of the raw per-zone snapshot taken while vegetation/gameplay were
    still being placed).
    `boat_ok` is forwarded to `fill_open_islands` — False for the underground level, which has
    no boat mechanic to excuse a stranded-target component (see that function's docstring).
    Returns (objs, ncarved, nreconn, nfilled, n_pockets, ndrop)."""
    objs_before_g2 = list(objs)
    objs, ncarved = GEO.g2_repair(size, grid, objs, targets, costly=ridge)
    removed_g2 = [o for o in objs_before_g2
                  if id(o) not in {id(x) for x in objs}]

    objs_before_fill = list(objs)
    ids_before_fill = {id(o) for o in objs_before_fill}
    # Loot zone interiors are accessible only via gate/monolith (teleportation), not physical
    # traversal.  Two problems without intervention:
    #   1. The interior itself looks like a disconnected island → fill_open_islands carves through
    #      the seal to reach it.  Adding interior tiles to `fill_targets` tells fill_open_islands
    #      they are already-reachable, removing them from the island list.
    #   2. The seal veg at loot-zone boundary tiles can create tiny isolated pockets in the
    #      EXTERNAL zone (1-tile notches that had only the now-sealed tile as their escape).
    #      fill_open_islands would carve through the seal (cost 40 < cap 120) to reconnect
    #      them.  Pricing loot-zone tiles at 400 makes those paths exceed the cap, so
    #      fill_open_islands fills the pocket with blocking decoration instead of carving.
    loot_interior = set()
    for zr in zone_records:
        if zr.get("loot_zone"):
            loot_interior |= zr["ts"]
    fill_targets = targets + [t for t in sorted(loot_interior) if t not in set(targets)]
    objs, nreconn, nfilled = GEO.fill_open_islands(size, grid, objs, fill_targets, seed=seed,
                                                   boat_ok=boat_ok, costly=ridge | loot_interior)
    ids_after_fill = {id(o) for o in objs}
    removed_fill = [o for o in objs_before_fill if id(o) not in ids_after_fill]
    added_fill = [o for o in objs if id(o) not in ids_before_fill]

    zone_of_tile = {}
    zr_by_zid = {zr["zid"]: zr for zr in zone_records}
    for zr in zone_records:
        for t in zr["ts"]:
            zone_of_tile[t] = zr["zid"]
    for o in removed_g2 + removed_fill:               # vegetation carved away -> walkable again
        for cx, cy in BS._blocking_cells(o):
            zr = zr_by_zid.get(zone_of_tile.get((cx, cy)))
            if zr is not None:
                zr["passable"].add((cx, cy))
                zr["open_set"].add((cx, cy))
    for o in added_fill:                               # new blocking filler -> now impassable
        for cx, cy in BS._blocking_cells(o):
            zr = zr_by_zid.get(zone_of_tile.get((cx, cy)))
            if zr is not None:
                zr["passable"].discard((cx, cy))
                zr["open_set"].discard((cx, cy))

    # L4a' Seer Hut quests: one fixed named artifact + a seer hut whose mission gates on it
    # (VCMI RMG convention — "add seer hut with quest to the map like the vcmi generator
    # does"). Runs before pocket caches so its two footprints are already claimed in
    # `zone_records` when pocket geometry is judged.
    # Pre-compute global pocket geometry ONCE, shared by both the seer-hut quest pass
    # (artifact restricted to ≥3-tile pockets) and the pocket-cache pass (avoids a
    # second expensive find_pockets call on the same data).
    _global_true_pkt = set()
    for _zr_pkt in zone_records:
        _global_true_pkt |= _zr_pkt.get("passable", _zr_pkt["open_set"])
    _raw_pkt = find_pockets(_global_true_pkt)
    _pocket_tiles_pkt = set()
    for _g_pkt, (_pt_pkt, _mf_pkt) in _raw_pkt.items():
        if len(_pt_pkt) >= 3:
            _pocket_tiles_pkt |= set(_pt_pkt)
    qobjs, n_quests = CA.place_seer_hut_quests(zone_records, seed=seed, bounds=(size, size),
                                               used_artifacts=seerhut_artifacts,
                                               pocket_tiles=_pocket_tiles_pkt)
    objs.extend(qobjs)
    targets.extend((o["x"], o["y"]) for o in qobjs)
    if n_quests:
        print(f"  L{level} seer hut quests: {n_quests}")

    # L4b guarded pocket caches: ONE global, zone-independent pass over this level's whole
    # reachable field now that every zone's terrain/vegetation/scatter AND the map-level
    # repair passes above are finalized (user-mandated 2026-07-04 — see
    # steps.repair.caches.place_pocket_caches docstring for the rationale).
    cobjs, n_pockets = CA.place_pocket_caches(zone_records, seed=seed, bounds=(size, size),
                                              border_guards=border_guards,
                                              precomputed_pockets=_raw_pkt)
    objs.extend(cobjs)
    targets.extend((o["x"], o["y"]) for o in cobjs)
    ck = collections.Counter(o["purpose"] for o in cobjs)
    print(f"  L{level} pockets: {n_pockets} found, cache res={ck.get('RESOURCE_PILE', 0)} "
          f"art={ck.get('REWARD_PICKUP', 0)} guard={ck.get('GUARD', 0)}")

    # both sides of one corridor may have guarded the same gate — keep only the stronger
    # of any two GUARDs within Chebyshev 2 (deterministic scan order). A mine's own guard
    # must never be dropped this way (mines are user-mandated to always be guarded) — it
    # sits Chebyshev 1 from the mine's footprint, so protect any guard that close to one.
    # Dedup is per-LEVEL only: two guards that happen to share (x, y) on different levels
    # are not physically near each other.
    drop = set()
    guards = [(i, o) for i, o in enumerate(objs) if o.get("purpose") == "GUARD"]
    mine_cells = [
        (mx, my) for o in objs if o.get("purpose") == "MINE"
        for mx, my, _ in OR.mask_cells(o["mask"], o["x"], o["y"])
    ]
    protected = {
        ia for ia, oa in guards
        if oa.get("seal")                            # a border back-path guard IS the border:
        # dropping it re-opens an unsealable crossing (see seal_zone_borders)
        or any(max(abs(oa["x"] - mx), abs(oa["y"] - my)) <= 1 for mx, my in mine_cells)
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

    return objs, ncarved, nreconn, nfilled, n_pockets, len(drop)


class RepairStep(PipelineStep):
    """Post-placement repair: residual border-leak seal, unreachable-zone rescue, G2
    repairs, island fill, guarded pocket caches, and seerhut deduplication.

    Config:
        seed       RNG seed.
        workspace  Shared ``PlacementWorkspace``; reads ``ridge``/``town_of_zone`` (from
                   GameplayStep) and ``entrance_plan``/``seal_avoid``/``hard_avoid`` (from
                   PickupStep) per level, and performs its own border-seal pass here (the
                   tail that used to run inside legacy ``_run_level``, before Vegetation/
                   Pickup/Repair were split into their own steps).

    Reads: ``state.objs`` (partitioned by ``o.get("l", 0)``), ``state.targets``,
           ``state.zone_records``, ``state.grids``, ``state.zones``,
           ``state.player_zids``, ``state.player_towns``, ``state.gate_objs``,
           ``state.subterrain``, ``state.tunnel_protect``, ``state.size``.

    Writes: ``state.objs`` (repaired flat list). Mutates ``state.targets`` and
            ``state.zone_records`` in place. Appends to ``state.log``.
    """

    def __init__(self, seed: int = 3, workspace: PlacementWorkspace | None = None) -> None:
        self.seed = seed
        self.workspace = workspace

    def run(self, state: MapState, ontology) -> None:
        if self.workspace is None:
            return
        W = H = state.size
        size = state.size
        grids = state.grids
        zones_by_level = state.zones
        targets_by_level = state.targets
        zone_records_by_level = state.zone_records

        # partition flat objs list by level for per-level repair
        objs_by_level: dict = {lvl: [] for lvl in grids}
        for o in state.objs:
            lvl = o.get("l", 0)
            if lvl in objs_by_level:
                objs_by_level[lvl].append(o)

        # ── Residual border-leak seal (the tail that used to run inside _run_level, once
        # per level, right after that level's own vegetation+scatter finished) ───────────
        border_guards_by_level: dict = {}
        for level in sorted(grids):
            lvl_ws = self.workspace.levels[level]
            zone_records = zone_records_by_level[level]
            loot_ts = set()
            for zr in zone_records:
                if zr.get("loot_zone"):
                    loot_ts |= zr["ts"]
            tunnel_protect = frozenset(state.tunnel_protect) if level == 1 else frozenset()
            sobjs_seal, sealed, guard_tiles, n_open = BS.seal_zone_borders(
                W, H, grids[level], zones_by_level[level], lvl_ws.entrance_plan,
                objs_by_level[level], lvl_ws.seal_avoid | tunnel_protect,
                lvl_ws.hard_avoid, self.seed, level, skip_tiles=loot_ts)
            objs_by_level[level].extend(sobjs_seal)
            if sealed or guard_tiles or n_open:
                state.log.append(
                    f"L{level} border seal: {len(sealed)} cells closed, "
                    f"{len(guard_tiles)} back-path guards"
                    + (f", {n_open} crossings left free (unguardable)" if n_open else ""))
            for zr in zone_records:                      # keep pocket detection honest
                zr["passable"] -= sealed
                zr["open_set"] -= sealed | guard_tiles
            lvl_ws.guard_tiles = frozenset(guard_tiles)
            border_guards_by_level[level] = guard_tiles

        gate_xy = {(o["x"], o["y"]) for o in state.gate_objs if o.get("l", 0) == 0}
        start = _find_start(state, self.workspace)

        if start is not None:
            n_portals = GEO.rescue_unreachable_zones(
                size, grids, zones_by_level, objs_by_level, targets_by_level,
                zone_records_by_level, start, gate_xy, self.seed)
            if n_portals:
                state.log.append(f"RepairStep: {n_portals} portal rescue(s) added")

        seerhut_artifacts: set = set()
        for level in sorted(grids):
            objs = objs_by_level[level]
            targets = targets_by_level[level]
            zone_records = zone_records_by_level[level]
            lvl_ridge = self.workspace.levels[level].ridge
            lvl_border_guards = border_guards_by_level.get(level, frozenset())
            boat_ok = (level == 0)

            (repaired, ncarved, nreconn, nfilled,
             npockets, ndrop) = _repair_one_level(
                level, size, grids[level], objs, targets, zone_records, self.seed,
                boat_ok=boat_ok, ridge=lvl_ridge,
                seerhut_artifacts=seerhut_artifacts,
                border_guards=lvl_border_guards,
            )
            objs_by_level[level] = repaired

            state.log.append(
                f"L{level} repair: carved={ncarved} reconnected={nreconn} "
                f"filled={nfilled} pockets={npockets} dup_drops={ndrop}"
            )

        # retag all underground objects with l=1
        if state.subterrain and 1 in objs_by_level:
            for o in objs_by_level[1]:
                o["l"] = 1

        # flatten into state.objs
        state.objs = [o for lvl in sorted(objs_by_level)
                      for o in objs_by_level[lvl]]
