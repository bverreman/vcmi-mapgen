"""Reliability tests for steps.gameplay.water (water-body population + seaport guarantee)."""
import random
import zlib


def _water_and_land_zone():
    """A real (small, texture-skipped for speed) generated grid with a big water body and
    a big bordering land zone — real terrain generation gives an organic, jagged coastline,
    which `_ensure_water_seaports`'s anchor search needs (a perfectly straight synthetic
    coastline can leave its 3x3-footprint anchor just out of that search's 1-hop reach)."""
    from vcmi_mapgen.steps.gameplay import water as WT
    from vcmi_mapgen.steps.terrain_gen import macro_topo as MT

    WATER = 8
    grid = MT.generate(40, 40, seed=2, water_mode="islands", level=0, texture=False)
    H, W = len(grid), len(grid[0])
    water_tiles = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == WATER}
    land_tiles = {(x, y) for y in range(H) for x in range(W) if grid[y][x] != WATER}
    NB4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    border_land = {
        (x + dx, y + dy) for x, y in water_tiles for dx, dy in NB4
        if (x + dx, y + dy) in land_tiles
    }
    start = next(iter(border_land))
    zone_tiles = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in NB4:
            n = (x + dx, y + dy)
            if n in land_tiles and n not in zone_tiles:
                zone_tiles.add(n)
                stack.append(n)
    zones = {0: {"terrain_type": 2, "tiles_set": zone_tiles, "area": len(zone_tiles),
                 "centroid": (W / 2, H / 2)}}
    assert len(water_tiles) >= WT._WATER_BODY_MIN and len(zone_tiles) >= WT._WATER_BODY_MIN
    return WT, W, H, grid, zones


def test_ensure_water_seaports_places_at_least_one():
    WT, W, H, grid, zones = _water_and_land_zone()
    objs = WT._ensure_water_seaports(W, H, grid, zones, [], seed=2)
    assert objs, "a land zone bordering a >= _WATER_BODY_MIN water body must get a seaport"


def test_seaport_rng_seed_is_not_derived_from_builtin_hash(monkeypatch):
    """_try_place used to seed its RNG with `seed ^ hash(label) ^ 0x53A9`. Python salts
    str hash() per-process (PYTHONHASHSEED), so the SAME map seed could place seaports in
    different spots on different process launches — a determinism break the project's
    seed contract (AGENTS.md: "the terrain generator is seeded") rules out. Regression for
    the bug fixed by seeding from zlib.crc32(label.encode()) instead: rather than guess the
    exact label strings _place_for_zone builds (they depend on which water-tile/zone-id the
    map happens to pick), this wraps the real zlib.crc32 and random.Random to observe what
    the code actually feeds each, and cross-checks the two — robust to geometry, and it
    fails immediately if the code reverts to hash(label) (crc32 would simply never fire)."""
    from vcmi_mapgen.steps.gameplay import water as WT

    WT_module = WT.__name__  # "vcmi_mapgen.steps.gameplay.water"
    import sys
    wt_mod = sys.modules[WT_module]

    WT_obj, W, H, grid, zones = _water_and_land_zone()

    crc_calls = []
    real_crc32 = zlib.crc32

    def recording_crc32(data, *a):
        result = real_crc32(data, *a)
        crc_calls.append(result)
        return result

    monkeypatch.setattr(wt_mod.zlib, "crc32", recording_crc32)

    seeds_seen = []
    real_random_cls = random.Random

    class RecordingRandom(real_random_cls):
        def __init__(self, seed_arg=None):
            seeds_seen.append(seed_arg)
            super().__init__(seed_arg)

    monkeypatch.setattr(random, "Random", RecordingRandom)

    map_seed = 2
    WT_obj._ensure_water_seaports(W, H, grid, zones, [], seed=map_seed)

    assert crc_calls, (
        "_ensure_water_seaports never called zlib.crc32 — did the seaport RNG regress "
        "back to the process-salted hash(label)?"
    )
    derived_seeds = {map_seed ^ crc ^ 0x53A9 for crc in crc_calls}
    assert derived_seeds & set(seeds_seen), (
        f"none of the crc32-derived seeds {derived_seeds} were actually used to seed a "
        f"random.Random() (saw {seeds_seen}) — crc32 is computed but not wired into the RNG"
    )
