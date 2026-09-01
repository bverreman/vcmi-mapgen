"""Shared value-noise field generator (used by terrain-gen macro topology and by the
vegetation sampler's clumping bias)."""


def _value_noise(W, H, cell, rng):
    """Smooth value-noise field in [-1,1] (coarse random grid, bilinear upsample).
    Smooth (not white) noise is what makes warped borders wavy instead of fuzzy."""
    gw, gh = W // cell + 2, H // cell + 2
    g = [[rng.uniform(-1, 1) for _ in range(gw)] for _ in range(gh)]
    out = [[0.0] * W for _ in range(H)]
    for y in range(H):
        gy = y / cell; iy = int(gy); fy = gy - iy
        for x in range(W):
            gx = x / cell; ix = int(gx); fx = gx - ix
            a = g[iy][ix] * (1 - fx) + g[iy][ix + 1] * fx
            b = g[iy + 1][ix] * (1 - fx) + g[iy + 1][ix + 1] * fx
            out[y][x] = a * (1 - fy) + b * fy
    return out
