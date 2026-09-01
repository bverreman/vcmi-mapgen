"""Reliability tests for kit.tiling (terrain despeckle / sliver absorption)."""


def test_despeckle_absorbs_tiny_zones():
    """Shape-aware sliver rule: a terrain patch (= future zone) survives despeckle only when
    it has >4 tiles or is a compact 2x2 square; narrow 4-tile shapes and anything smaller are
    absorbed into the dominant LAND neighbour (water/rock only when no land borders it)."""
    from vcmi_mapgen.kit import tiling as ZE

    GRASS, DIRT, WATER = 2, 0, 8
    W, H = 24, 14
    ids = [[GRASS] * W for _ in range(H)]
    for x in range(3, 7):                 # 1x4 dirt line — narrow, must be absorbed
        ids[3][x] = DIRT
    for x, y in ((10, 5), (11, 5), (10, 6), (11, 6)):   # 2x2 dirt square — a fine zone, kept
        ids[y][x] = DIRT
    ids[9][3] = ids[9][4] = ids[10][3] = DIRT           # 3-tile L — absorbed
    # 4-tile dirt line hugging a water edge: water borders it on more sides than grass does,
    # but land preference must still repaint it GRASS, not water
    for x in range(16, 22):
        for y in range(2, 5):
            ids[y][x] = WATER
    for x in range(17, 21):
        ids[5][x] = DIRT
    # 2x2 grass island fully inside water — compact, must survive as its own zone
    for x in range(16, 22):
        for y in range(8, 13):
            ids[y][x] = WATER
    for x, y in ((18, 10), (19, 10), (18, 11), (19, 11)):
        ids[y][x] = GRASS

    out = ZE._despeckle_ids(ids, W, H)
    assert out == ZE._despeckle_ids(ids, W, H), "despeckle must be deterministic"

    assert all(out[3][x] == GRASS for x in range(3, 7)), "1x4 line absorbed into grass"
    assert all(out[y][x] == DIRT for x, y in ((10, 5), (11, 5), (10, 6), (11, 6))), \
        "2x2 square kept"
    assert out[9][3] == out[9][4] == out[10][3] == GRASS, "3-tile patch absorbed"
    assert all(out[5][x] == GRASS for x in range(17, 21)), \
        "water-hugging sliver becomes LAND (grass), never water"
    assert all(out[y][x] == GRASS for x, y in ((18, 10), (19, 10), (18, 11), (19, 11))), \
        "2x2 island in water survives"

    # no surviving land patch violates the rule
    seen = set()
    for y in range(H):
        for x in range(W):
            if (x, y) in seen or out[y][x] >= WATER:
                continue
            t, stack, tiles = out[y][x], [(x, y)], [(x, y)]
            seen.add((x, y))
            while stack:
                a, b = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = a + dx, b + dy
                    if (0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen
                            and out[ny][nx] == t):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
                        tiles.append((nx, ny))
            assert ZE._keep_patch(tiles), f"sliver patch survived: {sorted(tiles)}"
