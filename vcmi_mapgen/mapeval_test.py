"""Tests for the map-level quality yardstick (mapeval).

Torch-free and fast: synthetic maps exercise determinism, the corpus-vs-empty ordering, the
reachability term, relational-pair completeness, and feature-vector stability. Separate from the
bit-exact identity / render tests (untouched).
"""
import os

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import mapeval as ME

GRASS = 2


def _grid(W, H, t=GRASS):
    cell = {"t": t, "view": 0, "rt": 0, "rd": 0, "ot": 0, "od": 0, "m": 0}
    return [[dict(cell) for _ in range(W)] for _ in range(H)]


def _fm(objects=None, W=48, H=48):
    return {"name": "synthetic", "width": W, "height": H, "twoLevel": False,
            "players": 1, "terrain": [_grid(W, H)], "objects": objects or []}


def _obj(typ, x, y, sub="object", mask=("A",), purpose=None):
    o = {"type": typ, "subtype": sub, "animation": "X", "mask": list(mask), "x": x, "y": y, "l": 0}
    if purpose:
        o["_purpose"] = purpose
    return o


def test_score_deterministic():
    fm = _fm([_obj("town", 10, 10, purpose="TOWN"), _obj("town", 30, 30, purpose="TOWN")])
    a = ME.score_map(fm)
    b = ME.score_map(fm)
    assert a == b


def test_corpus_beats_empty():
    corpus = ME.score_map(OR.load_faithful("All for One"))
    empty = ME.score_map(_fm([]))
    assert corpus["total"] > empty["total"]
    assert corpus["dist"] > empty["dist"]      # an empty map is far from the corpus distribution


def test_reach_zero_without_start():
    # no town ⇒ no start seed ⇒ reachability term is zero
    reach, rep = ME._reach_score(_fm([]))
    assert reach == 0.0
    assert rep["start"] is None


def test_relational_pair_completeness():
    lone = _fm([_obj("monolithOneWayEntrance", 5, 5, sub="blue")])
    assert ME.relational_complete(lone) is False
    paired = _fm([_obj("monolithOneWayEntrance", 5, 5, sub="blue"),
                  _obj("monolithOneWayExit", 40, 40, sub="blue")])
    assert ME.relational_complete(paired) is True
    # a two-way monolith with a single end is also incomplete
    half = _fm([_obj("monolithTwoWay", 5, 5, sub="red")])
    assert ME.relational_complete(half) is False


def test_feature_keys_stable():
    ka = list(ME.features(_fm([_obj("town", 10, 10, purpose="TOWN")])).keys())
    kb = list(ME.features(OR.load_faithful("All for One")).keys())
    assert ka == kb                            # identical ordered schema for any map


def test_value_gradient_sign():
    # rewards far from the lone town ⇒ positive outward gradient; near town ⇒ not positive
    far = _fm([_obj("town", 2, 2, purpose="TOWN"),
               _obj("treasureChest", 45, 45, purpose="REWARD_PICKUP")])
    near = _fm([_obj("town", 24, 24, purpose="TOWN"),
                _obj("treasureChest", 25, 25, purpose="REWARD_PICKUP")])
    assert ME.features(far)["value_gradient"] > ME.features(near)["value_gradient"]


def test_traverse_monolith_network():
    """Two-way monoliths of one subtype form a teleport network for the reachability
    walk — a rock-severed area counts as reachable iff BOTH ends exist (this is how
    pp_map rescues unreachable zones as guarded portal reward zones)."""
    from vcmi_mapgen import traverse as TR

    def wall_fm(objects):
        fm = _fm(objects)
        for y in range(48):                       # rock column severs east from west
            fm["terrain"][0][y][24]["t"] = 9
        return fm

    town = _obj("town", 5, 5, purpose="TOWN")
    mine = _obj("mine", 40, 40, purpose="MINE")   # sits east of the wall
    near = _obj("monolithTwoWay", 10, 10, sub="monolith1")
    far = _obj("monolithTwoWay", 40, 30, sub="monolith1")

    linked = TR.traverse(wall_fm([town, mine, near, far]))
    assert linked["unreachable_mines"] == [], "paired monoliths must bridge the wall"

    lone = TR.traverse(wall_fm([town, mine, near]))
    assert lone["unreachable_mines"] == [(40, 40, 0)], \
        "a single monolith end must NOT teleport anywhere"

    from vcmi_mapgen import mapeval as ME
    assert ME.relational_complete(wall_fm([town, mine, near])) is False
    assert ME.relational_complete(wall_fm([town, mine, near, far])) is True
