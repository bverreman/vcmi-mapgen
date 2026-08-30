"""RepairStep — unreachable-zone rescue, G2 repair, island fill, pocket caches."""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen import pp_map as PM
from vcmi_mapgen import zone_engine as ZE


def _find_start(state: MapState):
    """Return (level, (x, y)) for the first player town, or centroid of the
    largest surface land zone when there are no players."""
    town_of_zone = state.extras.get("gameplay.town_of_zone", {})
    for lvl, zid in state.player_zids:
        t = town_of_zone.get(lvl, {}).get(zid)
        if t is not None:
            return (lvl, (t["x"], t["y"]))
    # No players — use nearest-to-centroid tile of the largest surface land zone
    zones0 = state.zones.get(0, {})
    big = max(
        (z for z in zones0.values()
         if ZE.TNAME.get(z["terrain_type"]) not in (None, "water", "rock")),
        key=lambda z: z["area"],
        default=None,
    )
    if big is not None:
        bx, by = big["centroid"]
        return (0, min(big["tiles_set"],
                       key=lambda t: ((t[0] - bx) ** 2 + (t[1] - by) ** 2, t)))
    return None


class RepairStep(PipelineStep):
    """Post-placement repair: rescue unreachable zones, G2 repairs, island fill,
    guarded pocket caches, and seerhut deduplication.

    Config:
        seed  RNG seed.

    Reads: ``state.objs`` (partitioned by ``o.get("l", 0)``), ``state.targets``,
           ``state.zone_records``, ``state.grids``, ``state.zones``,
           ``state.player_zids``, ``state.player_towns``, ``state.gate_objs``,
           ``state.subterrain``, ``state.size``.
           Reads ``state.extras["gameplay.ridge"]``,
           ``state.extras["gameplay.border_guards"]``,
           ``state.extras["gameplay.town_of_zone"]`` when provided by GameplayStep.

    Writes: ``state.objs`` (repaired flat list). Mutates ``state.targets`` and
            ``state.zone_records`` in place. Appends to ``state.log``.
    """

    def __init__(self, seed: int = 3) -> None:
        self.seed = seed

    def run(self, state: MapState, ontology) -> None:
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

        ridge = state.extras.get("gameplay.ridge", {})
        border_guards = state.extras.get("gameplay.border_guards", {})

        gate_xy = {(o["x"], o["y"]) for o in state.gate_objs if o.get("l", 0) == 0}
        start = _find_start(state)

        if start is not None:
            n_portals = PM.rescue_unreachable_zones(
                size, grids, zones_by_level, objs_by_level, targets_by_level,
                zone_records_by_level, start, gate_xy, self.seed)
            if n_portals:
                state.log.append(f"RepairStep: {n_portals} portal rescue(s) added")

        seerhut_artifacts: set = set()
        for level in sorted(grids):
            objs = objs_by_level[level]
            targets = targets_by_level[level]
            zone_records = zone_records_by_level[level]
            lvl_ridge = ridge.get(level, frozenset())
            lvl_border_guards = border_guards.get(level, frozenset())
            boat_ok = (level == 0)

            (repaired, ncarved, nreconn, nfilled,
             npockets, ndrop) = PM._repair_and_finish_level(
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
