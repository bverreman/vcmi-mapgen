"""VegetationStep — place terrain-matched decorative vegetation per zone."""
from __future__ import annotations

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen.pipeline import MapState, PipelineStep, PlacementWorkspace
from vcmi_mapgen.steps.vegetation import sample as PP


class VegetationStep(PipelineStep):
    """Corpus-fitted Gibbs marked-point-process vegetation, per zone.

    Config:
        seed       RNG seed.
        workspace  Shared ``PlacementWorkspace`` written by GameplayStep; each zone's
                   ``ZoneWorkspace`` supplies ``prot``/``occupied``/``gblocked``/
                   ``approaches``/``gobjs``/``rim8``/``ent_bands``, and this step writes
                   ``blocked``/``open_set``/``passable`` back into the same object for
                   PickupStep/RepairStep.

    Writes: ``state.objs`` (vegetation objects appended, all levels).
    """

    def __init__(self, seed: int = 3, workspace: PlacementWorkspace | None = None) -> None:
        self.seed = seed
        self.workspace = workspace

    def run(self, state: MapState, ontology) -> None:
        if self.workspace is None:
            return

        models: dict = {}
        new_objs: list = []

        for level, lvl_ws in self.workspace.levels.items():
            for zid, zw in lvl_ws.zones.items():
                zones = state.zones[level]
                terrain = zw.terrain
                ts = zw.ts
                ts_full = zw.ts_full

                if terrain not in models:
                    models[terrain] = PP.build_model(terrain)
                model = models[terrain]
                if not model["cats"]:
                    continue

                # Seaport footprint in this zone must be excluded from vegetation
                zone_seaport_cells = (lvl_ws.seaport_blk | lvl_ws.seaport_appr) & ts_full
                forbid = frozenset(zw.occupied) | frozenset(zw.approaches) | zone_seaport_cells
                mine_cells = {(mcx, mcy) for o in zw.gobjs if o.get("purpose") == "MINE"
                              for mcx, mcy, mblk in OR.mask_cells(o["mask"], o["x"], o["y"])
                              if mblk}
                # annulus 2..3: greenery frames the mine without sprite canopies overhanging
                # its visual
                attract = frozenset(
                    t for t in ts if t not in forbid
                    and 2 <= min(max(abs(t[0] - mx), abs(t[1] - my))
                                 for mx, my in mine_cells) <= 3
                ) if mine_cells else frozenset()
                # zone-isolation border belt: the whole 8-connected rim minus the planned
                # entrance bands (those sit in `prot` as hard zeros) gets the +BORDER_W
                # vegetation bias — both zones densify their own side, so the border reads
                # as a ~2-thick ridge.
                border = frozenset(zw.rim8 - zw.ent_bands - forbid)
                zobjs, blocked, _ = PP.sample_zone(ts, zones, zid, model, seed=self.seed,
                                                   prot=zw.prot, forbid=forbid,
                                                   attract=attract, border=border)
                if level == 1:   # sample_zone always tags l=0; retag the underground level
                    for o in zobjs:
                        o["l"] = 1
                new_objs.extend(zobjs)

                open_set = (ts - blocked - zw.gblocked - set(zw.occupied)
                           - set(zw.approaches) - zone_seaport_cells)
                passable = ts - blocked - zw.gblocked

                zw.blocked = frozenset(blocked)
                zw.open_set = frozenset(open_set)
                zw.passable = frozenset(passable)

        state.objs.extend(new_objs)
