"""TileStep — corpus-learned autotiling: despeckle + H3-correct transition views."""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.kit import vmap_format as FA


class TileStep(PipelineStep):
    """Apply corpus-learned terrain autotiling to every active level.

    Reads ``state.grids`` (set by TerrainGenStep) and writes:
      - ``state.cells``  — 2-D tile-dict grids (t, view, rt, …)
      - ``state.surfs``  — 2-D tile-string grids (e.g. "gr2_")
      - ``state.grids``  — updated with post-despeckle terrain codes
    """

    def run(self, state: MapState, ontology) -> None:
        W = H = state.size
        protect = frozenset(state.tunnel_protect)

        for level, grid in state.grids.items():
            kw = {"protect": protect} if level == 1 else {}
            cells = ZE.tile_terrain(grid, W, H, **kw)
            state.cells[level] = cells
            state.surfs[level] = [[FA.tile_string(c) for c in row] for row in cells]
            # update grid with post-despeckle terrain codes
            state.grids[level] = [[c["t"] for c in row] for row in cells]
