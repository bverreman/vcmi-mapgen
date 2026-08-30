"""TerrainGenStep — macro terrain generation for surface and underground levels."""
from __future__ import annotations

import collections

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen import macro_topo as MTOPO
from vcmi_mapgen import terrain_segment as TSG


def _gate_anchor_points(W, H, seed, n_sites=8, margin=8, pad=4):
    import random
    rng = random.Random(seed ^ 0xA7E5)
    cols = max(1, round(n_sites ** 0.5))
    rows = -(-n_sites // cols)
    lo, hi_x, hi_y = margin, W - margin, H - margin
    anchors = []
    for i in range(n_sites):
        gx = lo + (hi_x - lo) * ((i % cols) + 0.5) / cols
        gy = lo + (hi_y - lo) * ((i // cols) + 0.5) / rows
        ax = min(W - 2 - pad, max(margin, int(gx) + rng.randint(-3, 3)))
        ay = min(H - 3 - pad, max(margin, int(gy) + rng.randint(-3, 3)))
        anchors.append((ax, ay))
    return anchors


def _gate_site_cells(ax, ay, pad=4):
    r2 = (pad + 0.5) ** 2
    return {(ax + dx, ay + dy) for dy in range(-pad, pad + 1) for dx in range(-pad, pad + 1)
            if dx * dx + dy * dy <= r2}


def _carve_gate_sites(grid0, grid1, W, H, anchors, seed, pad=4):
    import random
    land0 = collections.Counter(grid0[y][x] for y in range(H) for x in range(W)
                                if grid0[y][x] != TSG.WATER)
    fill0 = land0.most_common(1)[0][0] if land0 else 2
    protect1 = set()
    if grid1 is None:
        for ax, ay in anchors:
            for x, y in _gate_site_cells(ax, ay, pad):
                if 0 <= x < W and 0 <= y < H:
                    grid0[y][x] = fill0
        return protect1

    land1 = collections.Counter(grid1[y][x] for y in range(H) for x in range(W)
                                if grid1[y][x] not in (TSG.WATER, TSG.ROCK))
    fill1 = land1.most_common(1)[0][0] if land1 else 6
    land1_before = {(x, y) for y in range(H) for x in range(W)
                    if grid1[y][x] not in (TSG.WATER, TSG.ROCK)}
    rng = random.Random(seed ^ 0xC0DE)
    for ax, ay in anchors:
        for x, y in _gate_site_cells(ax, ay, pad):
            if 0 <= x < W and 0 <= y < H:
                grid0[y][x] = fill0
                grid1[y][x] = fill1
        if land1_before:
            tx, ty = min(land1_before, key=lambda t: (t[0] - ax) ** 2 + (t[1] - ay) ** 2)
            land_bool = [[grid1[y][x] not in (TSG.WATER, TSG.ROCK) for x in range(W)]
                         for y in range(H)]
            MTOPO._carve_corridor(land_bool, (ax, ay), (tx, ty), W, H, rng,
                                  half_w=1, protect=protect1)
            for y in range(H):
                for x in range(W):
                    if land_bool[y][x] and grid1[y][x] in (TSG.WATER, TSG.ROCK):
                        grid1[y][x] = fill1
        land1_before.add((ax, ay))
    return protect1


class TerrainGenStep(PipelineStep):
    """Generate macro terrain grids for surface (level 0) and optionally underground (level 1).

    Config:
        size        Map side length in tiles (square).
        seed        RNG seed.
        water       Explicit water fraction override (None = corpus-drawn).
        water_mode  'none' | 'normal' | 'islands'
        subterrain  Whether to generate a second underground level.
    """

    def __init__(
        self,
        size: int = 72,
        seed: int = 3,
        water: float | None = None,
        water_mode: str = "normal",
        subterrain: bool = False,
    ) -> None:
        self.size = size
        self.seed = seed
        self.water = water
        self.water_mode = water_mode
        self.subterrain = subterrain

    def run(self, state: MapState, ontology) -> None:
        W = H = self.size
        state.seed = self.seed
        state.size = self.size
        state.subterrain = self.subterrain
        state.water_mode = self.water_mode

        grid0 = MTOPO.generate(
            W, H, seed=self.seed, water=self.water,
            water_mode=self.water_mode, level=0,
        )

        tunnel_protect: set = set()
        grid1 = None
        if self.subterrain:
            grid1 = MTOPO.generate(
                W, H, seed=self.seed ^ 0x51E9, level=1,
                protect_out=tunnel_protect,
            )
            gate_anchors = _gate_anchor_points(W, H, self.seed)
            tunnel_protect |= _carve_gate_sites(
                grid0, grid1, W, H, gate_anchors, self.seed
            )

        state.grids[0] = grid0
        if grid1 is not None:
            state.grids[1] = grid1
        state.tunnel_protect = tunnel_protect
