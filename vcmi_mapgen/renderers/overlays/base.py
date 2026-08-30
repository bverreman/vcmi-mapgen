"""Base class for semi-transparent map overlays."""
from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from vcmi_mapgen.pipeline import MapState

TILE = 32  # pixels per tile — matches render_editor


class MapOverlay(ABC):
    """Return an RGBA image the same size as the base map (W*TILE × H*TILE).

    Each pixel with alpha > 0 is alpha-composited over the base sprite render,
    so the H3 terrain and objects show through at reduced opacity.  Use low
    alpha values (60–120 / 255) so the underlying sprites remain legible.
    """

    @abstractmethod
    def apply(self, state: MapState, level: int) -> Image.Image:
        """Produce the overlay layer.  Must return an RGBA image."""

    @staticmethod
    def _blank(w: int, h: int) -> Image.Image:
        return Image.new("RGBA", (w * TILE, h * TILE), (0, 0, 0, 0))

    @staticmethod
    def _grid_size(state: MapState, level: int) -> tuple[int, int]:
        surf = state.surfs.get(level) or state.cells.get(level)
        if surf:
            return len(surf[0]), len(surf)
        return state.size, state.size
