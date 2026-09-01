"""Hardcoded rendering-presentation constants for terrain — NOT sourced from VCMI config
(unrelated to kit.vcmi_config's identifier resolution, despite sharing a terrain-code key)."""

# Terrain palette: terrain code (int) → RGB tuple.
# Used by schematic renders and overlays.
TERRAIN_RGB = {
    0: (120, 92, 56),    # dirt
    1: (214, 191, 130),  # sand
    2: (86, 140, 56),    # grass
    3: (225, 232, 238),  # snow
    4: (78, 108, 80),    # swamp
    5: (150, 124, 70),   # rough
    6: (92, 78, 104),    # subterranean
    7: (70, 60, 58),     # lava
    8: (54, 104, 168),   # water
    9: (64, 60, 64),     # rock
    10: (120, 150, 70),  # highlands (HotA)
    11: (150, 95, 70),   # wasteland (HotA)
}

# Pixels per tile used by the schematic (non-sprite) renderers.
TERRAIN_TILE_PX = 9
