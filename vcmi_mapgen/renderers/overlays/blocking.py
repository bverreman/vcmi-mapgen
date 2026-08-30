"""BlockingOverlay — highlight blocked tiles in semi-transparent red."""
from __future__ import annotations

from PIL import Image, ImageDraw

from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen.renderers.overlays.base import MapOverlay, TILE

_COLOR = (220, 50, 50, 100)   # red, ~40 % opaque
_GATE_COLOR = (255, 160, 0, 120)  # amber for gate-blocked tiles


class BlockingOverlay(MapOverlay):
    """Draw a red tint over every tile that is blocked by a placed object.

    Uses the object's ``template.mask`` (the 'B'/'X' cells) anchored at the
    object's ``(x, y)`` position.  Also overlays ``state.gate_blk`` tiles in
    amber when present (subterranean gate ZoC).

    Works with both pipeline-generated states (full mask available) and states
    produced by VmapReader.
    """

    def apply(self, state: MapState, level: int) -> Image.Image:
        surf = state.surfs.get(level) or state.cells.get(level)
        W = len(surf[0]) if surf and surf[0] else state.size
        H = len(surf) if surf else state.size
        img = Image.new("RGBA", (W * TILE, H * TILE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for o in state.objs:
            if o.get("l", 0) != level:
                continue
            mask = (o.get("template") or {}).get("mask")
            if not mask:
                continue
            ox, oy = o.get("x", 0), o.get("y", 0)
            for tx, ty, blocking in _iter_mask(mask, ox, oy):
                if blocking and 0 <= tx < W and 0 <= ty < H:
                    _fill_tile(draw, tx, ty, _COLOR)

        # gate blocked tiles (only in subterrain maps)
        for tx, ty in state.gate_blk.get(level, ()):
            if 0 <= tx < W and 0 <= ty < H:
                _fill_tile(draw, tx, ty, _GATE_COLOR)

        return img


def _iter_mask(mask, x, y):
    hh = len(mask)
    for r, row in enumerate(mask):
        ww = len(row)
        for c, ch in enumerate(row):
            if ch == " ":
                continue
            yield x - (ww - 1 - c), y - (hh - 1 - r), (ch in ("B", "X"))


def _fill_tile(draw, tx, ty, color):
    x0, y0 = tx * TILE, ty * TILE
    draw.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=color)
