"""Legacy compatibility shim.

Gate-only logic (`place_gates`/`mine_gate_stats`/`GATE_ANIM`) now lives in
`steps/gate/gates.py`; gameplay placement (`place_zone`/`mine_gameplay` and everything
else) now lives in `steps/gameplay/mines.py`. This module re-exports both under the names
`pp_map.py` (still on the legacy, pre-pipeline `build()` path) imports as `PG`, and is
retired once that path is (pipeline-refactor-v2-folders.md, Phase 6).
"""
from __future__ import annotations

from vcmi_mapgen import ontology as ON  # PG.ON is accessed directly by pp_test.py
from vcmi_mapgen.steps.gate.gates import (
    GAP, GATE_ANIM, GATE_STATS_PATH, GATE_STATS_VERSION, MIN_AREA_STATS, RND_MON,
    _cells, _fits, mine_gate_stats, place_gates, rnd_monster,
)
from vcmi_mapgen.steps.gameplay.mines import (
    ALL_PURPOSES, AUDIT_EXCLUDED, BASIC_MINE_RES, CAPS, CORE_SPELLS, EB, ENTRANCE_GUARD_PROB,
    GB, LAND, MINED_TERR, MINE_GUARD_LVL, OB, PICKUP_PURPOSES, PLACED_PURPOSES, RANDOM_SHARE,
    RND_ART, RND_DWELL, RND_DWELL_L, RND_RES, RND_TOWN, STATS_PATH, STATS_PATH_UNDERGROUND,
    STATS_VERSION, TOWN_MIN_AREA, TOWN_SPRITE_VARIANTS, VISIT_PURPOSES, WATER_PURPOSES,
    _SPIRAL, _gbin, _info_pool, _intensity_weights, _obin, audit_variety, gate_dist,
    mine_gameplay, openness, place_zone, scaled_cap, theta_covariates,
)
