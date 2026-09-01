"""PickupStep — unguarded scatter loot per zone, then the global loot-zone access pass."""
from __future__ import annotations

import collections

from vcmi_mapgen.pipeline import MapState, PipelineStep, PlacementWorkspace
from vcmi_mapgen.steps.pickup import loot_zones as LZ
from vcmi_mapgen.steps.pickup import scatter as SC


class PickupStep(PipelineStep):
    """Resources/artifacts scattered over the finished open field (L4a), then the global,
    per-level loot-zone access pass (gate+keymaster / sealed+monolith).

    Config:
        seed       RNG seed.
        workspace  Shared ``PlacementWorkspace``; reads each zone's ``blocked``/``open_set``/
                   ``passable`` (written by VegetationStep) plus the gameplay fields
                   GameplayStep wrote, and writes ``reach``/``used`` back per zone and
                   ``seal_avoid``/``hard_avoid`` per level for RepairStep.

    Writes: ``state.objs`` (scatter + loot-zone objects appended, underground tagged
            ``l=1``), ``state.targets``, ``state.zone_records`` (the legacy list-of-dicts
            shape RepairStep still expects).
    """

    def __init__(self, seed: int = 3, workspace: PlacementWorkspace | None = None) -> None:
        self.seed = seed
        self.workspace = workspace

    def run(self, state: MapState, ontology) -> None:
        if self.workspace is None:
            return
        W = H = state.size

        # partition the flat objs list by level — place_loot_zones mutates its
        # objs_existing list in place (clearing vegetation/scatter under a sealed
        # loot zone), so each level needs its own real (not concatenated-copy) list.
        objs_by_level: dict = {level: [] for level in self.workspace.levels}
        for o in state.objs:
            lvl = o.get("l", 0)
            if lvl in objs_by_level:
                objs_by_level[lvl].append(o)

        for level, lvl_ws in self.workspace.levels.items():
            level_objs = objs_by_level[level]
            targets: list = []
            zone_records: list = []
            seal_avoid: set = set()
            hard_avoid: set = set()

            for zid, zw in sorted(lvl_ws.zones.items()):
                zone_seaport_cells = (lvl_ws.seaport_blk | lvl_ws.seaport_appr) & zw.ts_full
                forbid = zw.occupied | set(zw.approaches) | zone_seaport_cells

                # scatter loot never sits on the rim: a pickup there is a walkable,
                # unsealable hole
                sobjs, sused, reach = SC.place_scatter(
                    zw.ts, state.zones[level], zid, zw.terrain,
                    zw.open_set - (zw.rim8 - zw.ent_bands), zw.prot, seed=self.seed,
                    bounds=(W, H), entrances=zw.entrances)
                if level == 1:   # place_scatter always tags l=0; retag the underground level
                    for o in sobjs:
                        o["l"] = 1
                level_objs.extend(sobjs)

                zw.reach = frozenset(reach)
                zw.used = frozenset(sused)

                targets.extend(zw.approaches)
                targets.extend((o["x"], o["y"]) for o in sobjs)
                # every planned crossing must survive repair: its rep is a named G2
                # target, so g2_repair verifies the entrance stayed connected once the
                # level is finalized
                targets.extend(r for r, _b, _o in zw.entrances)
                # Seaport approach tile must stay reachable (hero boards ship from there)
                targets.extend(t for t in (lvl_ws.seaport_appr & zw.ts_full))
                seal_avoid |= zw.prot | forbid | sused | set(zw.approaches)
                hard_avoid |= set(zw.occupied) | sused | set(zw.approaches)

                # RepairStep mutates open_set/passable in place (.add/.discard) — these
                # must be plain sets, not the workspace's frozensets.
                zone_records.append({"zid": zid, "terrain": zw.terrain, "ts": zw.ts_full,
                                     "open_set": set(zw.open_set), "passable": set(zw.passable),
                                     "reach": reach, "used": sused})
                pk = collections.Counter(o["purpose"] for o in sobjs)
                print(f"  L{level} zone {zid:>3} {zw.terrain:<8} {len(zw.ts_full):>5} tiles: "
                      f"scatter res={pk.get('RESOURCE_PILE', 0)} art={pk.get('REWARD_PICKUP', 0)}")

            # place_loot_zones clears every object under a newly-sealed loot zone by
            # (x, y) alone (see loot_zones.py), which would sweep a pre-placed
            # Subterranean Gate (+ its approach guard) if one falls inside the zone's
            # tile set — a Gate pair shares its (x, y) with its OTHER-level counterpart.
            # Legacy build() only appended gate_objs to `objs` AFTER this pass ran, so
            # they were physically absent here and immune; this pipeline merges them in
            # earlier (GameplayStep, so downstream forbid/occupied sets see them), so we
            # must shield them from the sweep by pulling them out and restoring them.
            gate_ids = {id(o) for o in state.gate_objs if o.get("l", 0) == level}
            shielded = [o for o in level_objs if id(o) in gate_ids]
            level_objs[:] = [o for o in level_objs if id(o) not in gate_ids]

            loot_objs, n_loot, loot_zids = LZ.place_loot_zones(
                zone_records, lvl_ws.entrance_plan, level_objs, seed=self.seed,
                bounds=(W, H), water_tiles=lvl_ws.water_tiles)
            if level == 1:   # place_loot_zones always tags l=0; retag the underground level
                for o in loot_objs:
                    o["l"] = 1
            level_objs.extend(shielded)
            level_objs.extend(loot_objs)
            # Only add EXTERIOR loot zone objects to targets (keymaster, exterior monolith).
            # Interior objects (gate, interior monolith) are reachable via teleportation/
            # gate, not via physical traversal — adding them causes g2_repair to carve a
            # hole in the loot zone seal to make them physically reachable.
            loot_interior_tiles: set = set()
            for zr in zone_records:
                if zr["zid"] in loot_zids:
                    loot_interior_tiles |= zr["ts"]
            targets.extend((o["x"], o["y"]) for o in loot_objs
                           if o.get("purpose") and (o["x"], o["y"]) not in loot_interior_tiles)
            # Remove any stale scatter targets from loot zone interiors — scatter loot was
            # placed inside those zones before place_loot_zones cleared it; without this,
            # g2_repair sees those now-open positions as unreachable targets and carves a
            # path through the seal.
            targets[:] = [t for t in targets if t not in loot_interior_tiles]
            # Mark sealed loot zones so place_pocket_caches excludes them from its
            # detection universe — their interiors would otherwise appear as pockets and
            # receive a spurious interior guard (the access mechanic already provides the
            # gate keeper).
            for zr in zone_records:
                if zr["zid"] in loot_zids:
                    zr["loot_zone"] = True
            if n_loot:
                zid_str = ", ".join(str(z) for z in sorted(loot_zids))
                print(f"  L{level} loot zones: {n_loot} access pair(s) placed "
                      f"(1 gate+key, {n_loot - 1} sealed+monolith) zones=[{zid_str}]"
                      if n_loot > 1
                      else f"  L{level} loot zones: 1 gate+key pair placed zones=[{zid_str}]")

            lvl_ws.seal_avoid = seal_avoid
            lvl_ws.hard_avoid = hard_avoid
            state.targets[level] = targets
            state.zone_records[level] = zone_records

        state.objs = [o for lvl in sorted(objs_by_level) for o in objs_by_level[lvl]]
