"""Legacy compatibility shim.

Everything that used to live directly in this module now lives in:
  - steps/gameplay/water.py    (_pick/_legal/place_water — Gameplay-timing)
  - steps/pickup/scatter.py    (place_scatter/_place_one/_web_dist + pandoraBox helpers)
  - steps/pickup/loot_zones.py (place_loot_zones/_solo_visit_pool/_shrine_spell_level)
  - steps/repair/caches.py     (place_pocket_caches/place_seer_hut_quests/place_pickups)
  - steps/repair/geometry.py   (place_reward_zone)

Re-exported here under the names `pp_map.py` (still on the legacy, pre-pipeline `build()`
path) imports as `PK`, and retired once that path is (pipeline-refactor-v2-folders.md,
Phase 6).
"""
from __future__ import annotations

from vcmi_mapgen.steps.gameplay.water import _legal, _pick, place_water
from vcmi_mapgen.steps.pickup.scatter import (
    CAPS, PANDORA_CREATURES, SCATTER_ART_SHARE, _RW_LIMITER, _RW_REWARD, _RW_TEXT,
    _pandora_reward, _place_one, _web_dist, place_scatter,
)
from vcmi_mapgen.steps.pickup.loot_zones import (
    LOOT_ZONE_MAX_TILES, _FILL_EXCL_ANIMS, _solo_visit_pool, place_loot_zones,
)
from vcmi_mapgen.steps.repair.caches import (
    MAX_SEER_HUTS, SEERHUT_MIN_REACH, SEERHUT_ZONE_RATIO, _ART_BY_LVL,
    _POCKET_SPACED_TYPES, _dedupe_pockets, _reach8, _seerhut_quest, _seerhut_reward,
    place_pickups, place_pocket_caches, place_seer_hut_quests,
)
from vcmi_mapgen.steps.repair.geometry import REWARD_ZONE_ART_W, place_reward_zone

LOOT_FLOOR_AREA = 300           # a real zone always yields a couple of unguarded loots
POCKET_MIN_SEP = 4              # Chebyshev distance between accepted cache guards, applied
                                # to TINY (1-2 tile) pockets only: the ZoC-neck detector
                                # legitimately flags every concave wall corner as a 1-tile
                                # nook (locally identical to a flat-face recess), so without
                                # thinning, a long wall run grows a guard at every kink.
                                # Real (3+ tile) pockets stay deterministic — every one gets
                                # its cache, per the module doctrine above.
