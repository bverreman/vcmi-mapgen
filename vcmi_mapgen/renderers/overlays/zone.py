"""ZoneOverlay — colored zone fills and borders drawn over the base map."""
from __future__ import annotations

import colorsys

from PIL import Image, ImageDraw, ImageFont

from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen.renderers.overlays.base import MapOverlay, TILE

_FILL_ALPHA = 55     # zone fill opacity
_BORDER_ALPHA = 160  # zone border opacity
_LABEL_ALPHA = 220   # zone-id label opacity


def _zone_color(zid: int, n_zones: int) -> tuple[int, int, int]:
    hue = (zid * 0.618033988749895) % 1.0  # golden-ratio hue spread
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


class ZoneOverlay(MapOverlay):
    """Draw per-zone colored fills and 1-tile-wide borders.

    Reads ``state.zones[level]`` which must be populated by ``SegmentStep``.
    If zones are absent (e.g. the state came from VmapReader) the overlay is
    a no-op.  Optionally renders zone-id text labels at each zone's centroid.

    Args:
        labels: whether to draw zone-id labels (default True).
        fill_alpha: zone fill opacity 0–255 (default 55).
        border_alpha: border opacity 0–255 (default 160).
    """

    def __init__(
        self,
        labels: bool = True,
        fill_alpha: int = _FILL_ALPHA,
        border_alpha: int = _BORDER_ALPHA,
    ) -> None:
        self._labels = labels
        self._fill_alpha = fill_alpha
        self._border_alpha = border_alpha

    def apply(self, state: MapState, level: int) -> Image.Image:
        zones = state.zones.get(level)
        if not zones:
            surf = state.surfs.get(level) or state.cells.get(level)
            W = len(surf[0]) if surf and surf[0] else state.size
            H = len(surf) if surf else state.size
            return Image.new("RGBA", (W * TILE, H * TILE), (0, 0, 0, 0))

        surf = state.surfs.get(level) or state.cells.get(level)
        W = len(surf[0]) if surf and surf[0] else state.size
        H = len(surf) if surf else state.size

        img = Image.new("RGBA", (W * TILE, H * TILE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        n_zones = len(zones)

        # build a fast tile→zid lookup for border detection
        tile_zone: dict[tuple[int, int], int] = {}
        for zid, z in zones.items():
            for tx, ty in z.get("tiles_set", ()):
                tile_zone[(tx, ty)] = zid

        _DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        for zid, z in zones.items():
            r, g, b = _zone_color(zid, n_zones)
            fill_c = (r, g, b, self._fill_alpha)
            border_c = (r, g, b, self._border_alpha)

            for tx, ty in z.get("tiles_set", ()):
                if not (0 <= tx < W and 0 <= ty < H):
                    continue
                # border: at least one neighbour belongs to a different zone
                on_border = any(
                    tile_zone.get((tx + dx, ty + dy), -1) != zid
                    for dx, dy in _DIRS
                )
                color = border_c if on_border else fill_c
                x0, y0 = tx * TILE, ty * TILE
                draw.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=color)

            if self._labels:
                centroid = z.get("centroid")
                if centroid is not None:
                    px = int(centroid[0]) * TILE + TILE // 2
                    py = int(centroid[1]) * TILE + TILE // 2
                    draw.text((px, py), str(zid), fill=(255, 255, 255, _LABEL_ALPHA),
                               anchor="mm")

        return img
