"""GameplayStep — towns, mines, border sealing, and zone-level ledger."""
from __future__ import annotations

import collections

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen import pp_map as PM
from vcmi_mapgen import pp_gameplay as PG


class GameplayStep(PipelineStep):
    """Place towns, mines, dwellings, and border seals per zone.

    Config:
        seed     RNG seed.
        players  Number of player zones to designate (0 = neutral map).

    Reads: ``state.grids``, ``state.zones``, ``state.gate_occ/blk/appr``,
           ``state.gate_objs``, ``state.subterrain``, ``state.tunnel_protect``.

    Writes: ``state.objs`` (all levels, underground tagged ``l=1``),
            ``state.targets``, ``state.zone_records``,
            ``state.player_zids``, ``state.player_towns``, ``state.ledger``.

    Stores in ``state.extras``: ``"gameplay.ridge"``, ``"gameplay.border_guards"``,
    and ``"gameplay.town_of_zone"`` for RepairStep consumption.
    """

    def __init__(self, seed: int = 3, players: int = 0) -> None:
        self.seed = seed
        self.players = players

    def run(self, state: MapState, ontology) -> None:
        W = H = state.size

        player_zids = PM.select_player_zones(state.zones, self.players)
        if self.players and len(player_zids) < self.players:
            print(f"  WARNING: only {len(player_zids)} zones can host a player town "
                  f"(requested {self.players})")

        zids_by_level: dict = collections.defaultdict(set)
        for lvl, zid in player_zids:
            zids_by_level[lvl].add(zid)

        ledger = {
            "missing": set(PG.BASIC_MINE_RES),
            "towns": len(player_zids),
            "gold": 0,
        }

        all_targets: dict = {}
        all_zone_records: dict = {}
        all_town_of_zone: dict = {}
        all_ridge: dict = {}
        all_border_guards: dict = {}
        all_objs: list = []

        for level in sorted(state.grids):
            grid = state.grids[level]
            zones = state.zones[level]
            gstats = PG.mine_gameplay(level=level)

            gate_occ = state.gate_occ.get(level, frozenset())
            gate_blk = state.gate_blk.get(level, frozenset())
            gate_appr = state.gate_appr.get(level, ())
            tunnel_protect = frozenset(state.tunnel_protect) if level == 1 else frozenset()

            (objs, targets, zone_records, town_of_zone,
             _has_water, _nz, ridge, border_guards) = PM._run_level(
                level, W, H, grid, zones, zids_by_level[level],
                ledger, gstats, self.seed, state.subterrain,
                gate_occ=gate_occ, gate_blk=gate_blk, gate_appr=gate_appr,
                tunnel_protect=tunnel_protect,
            )

            # merge pre-placed gate objects for this level
            level_gate_objs = [o for o in state.gate_objs
                               if o.get("l", 0) == level]
            objs.extend(level_gate_objs)

            # retag underground objects so RepairStep can partition by level
            if level == 1:
                for o in objs:
                    o["l"] = 1

            all_objs.extend(objs)
            all_targets[level] = targets
            all_zone_records[level] = zone_records
            all_town_of_zone[level] = town_of_zone
            all_ridge[level] = ridge
            all_border_guards[level] = border_guards

        state.objs = all_objs
        state.targets = all_targets
        state.zone_records = all_zone_records
        state.player_zids = player_zids
        state.ledger = ledger

        # store inter-step handoff data for RepairStep
        state.extras["gameplay.ridge"] = all_ridge
        state.extras["gameplay.border_guards"] = all_border_guards
        state.extras["gameplay.town_of_zone"] = all_town_of_zone

        # resolve player towns from town_of_zone
        player_towns = []
        for lvl, zid in player_zids:
            t = all_town_of_zone.get(lvl, {}).get(zid)
            if t is not None:
                player_towns.append(t)
        state.player_towns = player_towns

        if ledger["missing"]:
            print(f"  WARNING: mine coverage incomplete — missing "
                  f"{sorted(ledger['missing'])}")
