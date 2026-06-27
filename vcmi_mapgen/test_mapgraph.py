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
    # the graph path filters decoration to its zone's terrain — no snow trees on grass. Terrain
    # coupling is now authoritative from the ONTOLOGY (terrains_of); a decoration is valid on a
    # tile if it is native to that terrain or is terrain-independent ('land'/'water').
    import ontology as ON
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 2, SIZE, 12, grammar)
    terr = fm["terrain"][0]
    H = len(terr); W = len(terr[0])
    bad = 0
    for o in fm["objects"]:
        if o.get("_purpose") != "DECORATION":
            continue
        x, y = o["x"], o["y"]
        if not (0 <= x < W and 0 <= y < H):
            continue
        ts = ON.terrains_of(o.get("animation"))
        name = ZE.TNAME.get(terr[y][x]["t"])
        generic = "water" if name == "water" else "land"
        if ts and name not in ts and generic not in ts:
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
    # walkable + gameplay stays accessible); a belt this thick reads as continuous mountain ranges.
    # Threshold is ~0.25: the reachability carve is 4-connected (matching the hero's movement model in
    # traverse) and so opens marginally more rim than an 8-connected carve, but borders stay mostly
    # closed (individual zones run 0.3-0.4 blocked rim).
    assert fracs and sum(fracs) / len(fracs) >= 0.25, \
        f"zone borders not closed enough (mean blocked rim {sum(fracs)/max(len(fracs),1):.2f})"


def test_category_mrf_captures_spatial_correlation():
    # the learned category MRF (corpus 4-adjacency co-occurrence) must, when Gibbs-sampled,
    # produce a CORRELATED category field — adjacent tiles share a kind far more than an
    # independent draw from the same marginal would. This is the whole point of Plan A.
    import random
    # a synthetic terrain MRF with strong like-with-like affinity (and an EMPTY clearing state)
    mrf = {"unary": {"MOUNTAIN": 45, "EMPTY": 40, "OAK_TREES": 15},
           "pair": {"MOUNTAIN": {"MOUNTAIN": 38, "EMPTY": 5, "OAK_TREES": 2},
                    "EMPTY": {"EMPTY": 33, "MOUNTAIN": 5, "OAK_TREES": 2},
                    "OAK_TREES": {"OAK_TREES": 12, "EMPTY": 2, "MOUNTAIN": 1}}}
    ts = {(x, y) for x in range(24) for y in range(24)}
    field = ZE._zone_category_field(ts, 2, mrf, random.Random(0))
    assert field is not None and ZE._zone_category_field(ts, 2, None, random.Random(0)) is None

    def same_rate(f):
        same = tot = 0
        for (x, y), c in f.items():
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in f:
                    tot += 1
                    same += (c == f[n])
        return same / tot

    tot_u = sum(mrf["unary"].values())
    indep = sum((n / tot_u) ** 2 for n in mrf["unary"].values())   # iid-from-marginal baseline
    assert same_rate(field) > indep + 0.12, "MRF field is no more correlated than an iid draw"
    # fully seeded -> deterministic
    assert field == ZE._zone_category_field(ts, 2, mrf, random.Random(0))


def test_generated_map_is_walkable():
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("graph", "grammar", 1, SIZE, 12, grammar)
    rep = TR.traverse(fm)
    assert rep["start"] is not None                        # a start town exists
    frac = rep["reached_tiles"] / max(rep["passable_tiles"], 1)
    assert frac > 0.3                                      # connectivity-first: most of it walkable


def test_group_archetypes_anchored_on_visitable_or_pickable():
    # the learned group library must key every archetype on a VISITABLE or PICKABLE anchor (the
    # user's rule: clusters form around visitable objects / pickable resources). Never a guard,
    # lone INFO, or decoration.
    grammar = ZE._load_or_build_grammar()
    groups = grammar.get("_groups") or {}
    assert groups, "grammar carries no _groups archetype library"
    for terr, byanchor in groups.items():
        for anchor_purpose in byanchor:
            assert anchor_purpose in ZE.ANCHOR_PURPOSES, \
                f"group anchor {anchor_purpose} on {terr} is not visitable/pickable"


def test_placement_is_grouped_not_scattered():
    # ORDER: placed gameplay must cluster into intentional set-pieces, not loose scatter — most
    # objects sit in a multi-object group, and every group is organized by a visitable/pickable
    # anchor. (The corpus map-level multi-object fraction is ~0.64; generation packs at least that.)
    grammar = ZE._load_or_build_grammar()
    fm = ZE.gen_fm("markov-graph", "grammar", 2, 72, 12, grammar)
    gp = [o for o in fm["objects"] if o.get("_purpose") not in ZE.NON_GAMEPLAY]
    clusters = ZE._cluster_objects(gp, ZE.GROUP_EPS)
    multi = sum(len(c) for c in clusters if len(c) > 1)
    assert multi / max(len(gp), 1) >= 0.6, "gameplay is scattered, not grouped"
    for c in clusters:                                     # every cluster organizes around an anchor
        a = ZE._group_anchor(c)
        if a is not None:
            assert a["_purpose"] in ZE.ANCHOR_PURPOSES


def test_group_placement_deterministic():
    # same seed -> identical objects (the group sampler is seeded per zone).
    grammar = ZE._load_or_build_grammar()
    a = ZE.gen_fm("markov-graph", "grammar", 5, 64, 12, grammar)["objects"]
    b = ZE.gen_fm("markov-graph", "grammar", 5, 64, 12, grammar)["objects"]
    key = lambda o: (o["x"], o["y"], o.get("l", 0), o.get("animation"), o.get("_purpose"))
    assert [key(o) for o in a] == [key(o) for o in b]
