"""Terrain-code lookups shared across the CLI and the generation pipeline."""

# terrain code -> human name (for labels); water/rock never form a zone.
TNAME = {0: "dirt", 1: "sand", 2: "grass", 3: "snow", 4: "swamp",
         5: "rough", 6: "subterr", 7: "lava", 8: "water", 9: "rock"}

# Decoration categories that must NEVER be placed on any terrain: water-feature tiles (river
# deltas, lakes incl. the AB LAKE_2 avllk1r, reefs, kelp) that read as misplaced water cutting
# through land — water features are blocking in the catalog but do NOT read as an obstacle,
# never use them as a zone-border ridge (the belt must be real obstacles: mountains, trees,
# hills, rocks). These are the ONTOLOGY type-level (category) names for the catalog's water
# features. Excluded unconditionally in the pools, the MRF learning (so the category field
# can't assign them), and the MRF decode. NOTE: the AB class 199 (TREES_2, avlswt*/avltro* --
# swamp palms + rough trees, sprite-verified) was excluded here while it was the opaque
# CLASS_199; it is real vegetation and is now allowed.
EXCLUDE_DECOR_TYPES = {"LAKE", "FROZEN_LAKE", "RIVER_DELTA", "KELP", "REEF", "LAKE_2"}
