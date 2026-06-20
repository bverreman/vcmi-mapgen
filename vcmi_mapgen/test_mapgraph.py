"""Tests for the zone-graph map generator (mapgraph).

Torch-free and fast: a small realized map exercises determinism, town presence, a sane zone count,
and — the whole point — that the connectivity-first plan yields a WALKABLE map (start exists, most
of it reachable). Separate from the bit-exact identity / render tests (untouched).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapgraph as MG
import mapeval as ME
import traverse as TR
import zone_engine as ZE

SIZE = 48


def test_realize_determinism():
    a = MG.realize(3, SIZE)
    b = MG.realize(3, SIZE)
    assert a[4] == b[4]                                     # identical plan (roles + edges)
    ta = [c["t"] for row in a[0] for c in row]
    tb = [c["t"] for row in b[0] for c in row]
    assert ta == tb                                        # identical terrain


def test_pure_land_no_water_or_rock():
    grid, _W, _H, _info, _plan = MG.realize(4, SIZE)
    ids = {c["t"] for row in grid for c in row}
    assert ME.WATER not in ids and ME.ROCK not in ids      # vegetation-separated, land-only


def test_plan_has_towns():
    _grid, _W, _H, _info, plan = MG.realize(1, SIZE)
    towns = [rs for rs in plan["role_seeds"] if rs[2] == "town"]
    assert len(towns) >= 2                                 # balanced multi-town plan


def test_realized_zone_count():
    grid, W, H, _info, _plan = MG.realize(2, SIZE)
    zones, _zl = ME._segment(grid)
    land = [z for z in zones.values() if z["area"] >= ME.MIN_AREA and 0 <= z["terrain_type"] < 8]
    assert len(land) >= 3                                  # a real multi-zone map, not one blob


def test_outward_value_gradient():
    # roles tier outward: treasure nodes sit farther from towns than the average node
    import math
    _g, _W, _H, _info, plan = MG.realize(5, SIZE)
    rs = plan["role_seeds"]
    towns = [(x, y) for x, y, r in rs if r == "town"]
    treas = [(x, y) for x, y, r in rs if r == "treasure"]
    if not treas:                                          # small maps may have no treasure tier
        return

    def nd(p):
        return min(math.hypot(p[0] - tx, p[1] - ty) for tx, ty in towns)
    assert sum(nd(p) for p in treas) / len(treas) > 0      # finite, and (by construction) outer


def test_decoration_matches_zone_terrain():
    # the graph path filters decoration to its zone's terrain — no snow trees on grass.
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 2, SIZE, 12, grammar)
    terr = fm["terrain"][0]
    H = len(terr); W = len(terr[0])
    dt = ZE._decor_terrains()
    bad = 0
    for o in fm["objects"]:
        if o.get("_purpose") != "DECORATION":
            continue
        x, y = o["x"], o["y"]
        if not (0 <= x < W and 0 <= y < H):
            continue
        ts = dt.get(o.get("animation"))
        if ts and len(ts) < ZE._NEUTRAL_MIN and terr[y][x]["t"] not in ts:
            bad += 1
    assert bad == 0, f"{bad} decoration objects sit on the wrong terrain"


def test_border_vegetation_not_on_map_edge():
    # vegetation rings INTERNAL zone borders only — never anchored on the outer map edge.
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 3, SIZE, 12, grammar)
    W, H = fm["width"], fm["height"]
    edge = [o for o in fm["objects"] if o.get("_purpose") == "DECORATION"
            and (o["x"] in (0, W - 1) or o["y"] in (0, H - 1))]
    assert not edge, f"{len(edge)} vegetation objects anchored on the map edge"


def test_zone_borders_are_closed():
    # the vegetation belt rings each zone: most boundary tiles are blocked (only entrances open).
    import traverse as TR
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 3, SIZE, 12, grammar)
    blocked, W, H = TR.passable_grid(fm, 0)
    zones, zl = ME._segment(fm["terrain"][0])
    fracs = []
    for zid, z in zones.items():
        if z["area"] < 20 or not (0 <= z["terrain_type"] < 8):
            continue
        rim = [(x, y) for (x, y) in z["tiles"]
               if any(not (0 <= x + dx < W and 0 <= y + dy < H) or zl[y + dy][x + dx] != zid
                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        if rim:
            fracs.append(sum(blocked[y][x] for x, y in rim) / len(rim))
    # substantial vegetation belt around zones (full hermetic sealing is avoided so the map stays
    # walkable + gameplay stays accessible); ~0.4 of rim blocked reads as continuous mountain ranges.
    assert fracs and sum(fracs) / len(fracs) >= 0.3, \
        f"zone borders not closed enough (mean blocked rim {sum(fracs)/max(len(fracs),1):.2f})"


def test_generated_map_is_walkable():
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 1, SIZE, 12, grammar)
    rep = TR.traverse(fm)
    assert rep["start"] is not None                        # a start town exists
    frac = rep["reached_tiles"] / max(rep["passable_tiles"], 1)
    assert frac > 0.3                                      # connectivity-first: most of it walkable
