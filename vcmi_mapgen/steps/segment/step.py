"""SegmentStep — flood-fill zone segmentation per level."""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen.kit.segmentation import _segment_level


def _warn_sliver_zones(zones, level, protect=frozenset()):
    MIN_AREA = 25
    for zid, z in zones.items():
        if z["area"] < MIN_AREA and not (frozenset(z.get("tiles_set", [])) & protect):
            print(f"  WARNING: level {level} zone {zid} is very small "
                  f"({z['area']} tiles, terrain {z.get('terrain_type')})")


class SegmentStep(PipelineStep):
    """Segment each active level's tile grid into same-terrain zones.

    Reads ``state.cells`` (set by TileStep) and writes ``state.zones``.
    """

    def run(self, state: MapState, ontology) -> None:
        protect = frozenset(state.tunnel_protect)
        for level, cells in state.cells.items():
            zones, _zl, _ = _segment_level(cells)
            _warn_sliver_zones(zones, level,
                               protect=protect if level == 1 else frozenset())
            state.zones[level] = zones
