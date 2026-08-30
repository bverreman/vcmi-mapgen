"""TileTypeOverlay — color each tile by terrain type (semi-transparent)."""
from __future__ import annotations

from PIL import Image

from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen.vcmi_ids import TERRAIN_RGB
from vcmi_mapgen.renderers.overlays.base import MapOverlay, TILE

_ALPHA = 90  # overlay opacity; low enough to keep sprites legible

# tile-string 2-char prefix -> terrain code int (matches TERRAIN_RGB keys)
_PREFIX_TO_CODE = {
    "dt": 0,  # dirt
    "sa": 1,  # sand
    "gr": 2,  # grass
    "sn": 3,  # snow
    "sw": 4,  # swamp
    "rg": 5,  # rough
    "sb": 6,  # subterranean
    "lv": 7,  # lava
    "wt": 8,  # water
    "ro": 9,  # rock
    "hl": 10, # highlands (HotA)
    "wa": 11, # wasteland (HotA)
}


class TileTypeOverlay(MapOverlay):
    """Tint each tile with its terrain color.

    Useful for visualizing terrain-type boundaries, especially when zone fills
    would be misleading (e.g. mixed-terrain zones).

    Args:
        alpha: overlay opacity 0–255 (default 90 ≈ 35 % opaque).
    """

    def __init__(self, alpha: int = _ALPHA) -> None:
        self._alpha = alpha

    def apply(self, state: MapState, level: int) -> Image.Image:
        surf = state.surfs.get(level) or state.cells.get(level)
        if not surf:
            return Image.new("RGBA", (state.size * TILE, state.size * TILE), (0, 0, 0, 0))
        W, H = len(surf[0]), len(surf)
        img = Image.new("RGBA", (W * TILE, H * TILE), (0, 0, 0, 0))
        px = img.load()
        for y, row in enumerate(surf):
            for x, cell in enumerate(row):
                t = _terrain_code(cell)
                r, g, b = TERRAIN_RGB.get(t, (0, 0, 0))
                color = (r, g, b, self._alpha)
                x0, y0 = x * TILE, y * TILE
                for dy in range(TILE):
                    for dx in range(TILE):
                        px[x0 + dx, y0 + dy] = color
        return img


def _terrain_code(cell) -> int:
    """Extract the integer terrain code from a tile string or tile dict."""
    if isinstance(cell, dict):
        return int(cell.get("t", 0))
    if isinstance(cell, str) and len(cell) >= 2:
        return _PREFIX_TO_CODE.get(cell[:2], 0)
    return 0
