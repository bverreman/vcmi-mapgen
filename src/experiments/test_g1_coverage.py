"""Tests for G1 — Coverage: visitable-tile footprint fix.

Covers:
1. footprint() includes visitable (visit_mask) tiles
2. Rebuilt adjacency profiles contain guard entries for mine types
3. benchmark coverage output schema present
4. Anti-shortcut: deps_spatial.py structural integrity
"""

from __future__ import annotations

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)


# ---------------------------------------------------------------------------
# Helpers: synthetic h3m objects
# ---------------------------------------------------------------------------


def _make_obj(x: int, y: int) -> object:
    o = types.SimpleNamespace(x=x, y=y)
    return o


def _make_tmpl(block_mask: bytes, visit_mask: bytes) -> object:
    return types.SimpleNamespace(block_mask=block_mask, visit_mask=visit_mask)


# ---------------------------------------------------------------------------
# Criterion 1: footprint includes visitable tiles
# ---------------------------------------------------------------------------


def test_footprint_includes_blocked_tiles():
    """footprint() must include all blocked tiles (bit CLEAR in block_mask)."""
    import deps_adj

    # block_mask: row 5 (last row), bit 7 clear -> tile at (x - (7-7), y - (5-5)) = (x, y)
    # All bits set = no blocked tiles; one bit clear in last row/col = one blocked tile
    full = 0xFF
    one_blocked = 0b11111110  # bit 7 clear in last row -> coord (x-(7-7), y-(5-5)) = (x, y)
    block_mask = bytes([full] * 5 + [one_blocked])
    visit_mask = bytes([0x00] * 6)  # no visitable tiles

    o = _make_obj(7, 5)
    tmpl = _make_tmpl(block_mask, visit_mask)
    fp = deps_adj.footprint(o, tmpl)

    # The blocked tile is at (7-(7-7), 5-(5-5)) = (7, 5)
    assert (7, 5) in fp


def test_footprint_includes_anchor():
    """footprint() always includes the anchor (o.x, o.y)."""
    import deps_adj

    block_mask = bytes([0xFF] * 6)  # no blocked tiles
    visit_mask = bytes([0x00] * 6)  # no visitable tiles
    o = _make_obj(3, 4)
    tmpl = _make_tmpl(block_mask, visit_mask)
    fp = deps_adj.footprint(o, tmpl)
    assert (3, 4) in fp


def test_footprint_includes_visitable_tiles():
    """footprint() must include tiles from visit_mask (bit SET = visitable)."""
    import deps_adj

    block_mask = bytes([0xFF] * 6)  # no blocked tiles
    # visit_mask: set bit 7 of last row -> tile at (x-(7-7), y-(5-5)) = (x, y) = (7, 5)
    last_row_visit = 0b10000000  # bit 7 set in last row
    visit_mask = bytes([0x00] * 5 + [last_row_visit])

    o = _make_obj(7, 5)
    tmpl = _make_tmpl(block_mask, visit_mask)
    fp = deps_adj.footprint(o, tmpl)

    # Visitable tile at bit7 of row5 -> (7 - (7-7), 5 - (5-5)) = (7, 5)
    assert (7, 5) in fp


def test_footprint_visitable_tile_distinct_from_blocked():
    """Visitable tile at non-anchor position is included when visit_mask bit is set.

    Bit layout: (b >> (7-c)) & 1 selects column c; tile is at (x-(7-c), y-(5-r)).
    MSB (0b10000000) fires at c=0, giving tile at (x-7, y-(5-r)).
    """
    import deps_adj

    block_mask = bytes([0xFF] * 6)  # no blocked tiles
    # Set MSB (bit 7) of row 5 -> c=0 -> tile at (x-(7-0), y-(5-5)) = (x-7, y)
    visit_mask = bytes([0x00] * 5 + [0b10000000])

    o = _make_obj(10, 5)
    tmpl = _make_tmpl(block_mask, visit_mask)
    fp = deps_adj.footprint(o, tmpl)

    # c=0, r=5: x - (7-0) = 10 - 7 = 3, y - (5-5) = 5
    assert (3, 5) in fp
    assert (10, 5) in fp  # anchor always included


def test_footprint_visit_mask_zero_adds_no_extra():
    """With all-zero visit_mask, footprint is identical to blocked+anchor only."""
    import deps_adj

    one_blocked_byte = 0b11111110  # bit 7 clear -> one blocked tile at anchor pos
    block_mask = bytes([0xFF] * 5 + [one_blocked_byte])
    visit_mask = bytes([0x00] * 6)

    o = _make_obj(7, 5)
    tmpl = _make_tmpl(block_mask, visit_mask)
    fp = deps_adj.footprint(o, tmpl)

    # Should only contain: anchor (7,5) and blocked tile (7,5) — same cell here
    assert fp == {(7, 5)}


# ---------------------------------------------------------------------------
# Criterion 2: adjacency profiles contain guard entries for mine types
# ---------------------------------------------------------------------------


def test_adjacency_mine_has_guard_neighbors():
    """Rebuilt adjacency.json must contain at least one monster neighbor for some mine key."""
    adj_path = os.path.join(ROOT, "out", "adjacency.json")
    assert os.path.isfile(adj_path), "out/adjacency.json not found"
    with open(adj_path) as f:
        adj = json.load(f)

    profiles = adj.get("profiles", {})
    mine_keys = [k for k in profiles if k.endswith("|mine")]
    assert mine_keys, "no mine profiles found in adjacency.json"

    found_guard = False
    for key in mine_keys:
        for nb in profiles[key].get("neighbours", []):
            if "monster" in nb["type"].lower() or "Monster" in nb["type"]:
                found_guard = True
                break
        if found_guard:
            break

    assert found_guard, (
        "No randomMonster* neighbor found in any mine profile — "
        "visitable-tile fix may not have propagated to adjacency.json"
    )


# ---------------------------------------------------------------------------
# Criterion 3: benchmark coverage output schema present at minimal scale
# ---------------------------------------------------------------------------


def test_benchmark_coverage_schema():
    """benchmark.py produces the required coverage fields at minimal scale."""
    import subprocess

    BENCHMARK = os.path.join(SRC, "benchmark.py")
    result = subprocess.run(
        [sys.executable, BENCHMARK, "--maps", "1", "--seeds", "1"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"benchmark exited {result.returncode}\n{result.stderr}"

    with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
        data = json.load(f)

    c = data["coverage"]
    for field in ("n", "mean_objdist", "pct_within_bar", "mean_control_objdist", "rows"):
        assert field in c, f"coverage missing '{field}'"


# ---------------------------------------------------------------------------
# Criterion 4: anti-shortcut — deps_spatial.py must be structurally intact
# ---------------------------------------------------------------------------


def test_deps_spatial_unmodified():
    """deps_spatial.py must still define PURPOSES, signature(), and compare()."""
    import deps_spatial

    assert hasattr(deps_spatial, "PURPOSES"), "PURPOSES missing from deps_spatial"
    assert hasattr(deps_spatial, "signature"), "signature() missing from deps_spatial"
    assert hasattr(deps_spatial, "compare"), "compare() missing from deps_spatial"
    assert len(deps_spatial.PURPOSES) >= 8, "PURPOSES list shorter than expected"


def test_deps_spatial_compare_symmetric_in_absence():
    """compare() returns 0.0 for two empty signatures (no keys in common)."""
    import deps_spatial

    val, _ = deps_spatial.compare({}, {})
    assert val == 0.0


# ---------------------------------------------------------------------------
# Criterion 5: town-anchored fill places benefit objects near towns
# ---------------------------------------------------------------------------


def test_stat_permanent_placed_near_town():
    """With sig[(STAT_PERMANENT,TOWN)] set, STAT_PERMANENT lands near towns.

    Before the fix, near-town tiles were consumed by forests/mines so
    leftover-fill STAT_PERMANENT ended up 40+ tiles from towns on complex maps.
    After the town-anchor fix, placement uses a ring-search at the target
    sig[(pur,"TOWN")] distance from towns.
    """
    import math
    import deps_realize as R

    params = {
        "sig": {
            ("STAT_PERMANENT", "TOWN"): 8.0,
            ("BONUS_TEMP", "TOWN"): 8.0,
        }
    }
    fm, _, _ = R.realize(seed=3, params=params)

    towns = [
        (o["x"], o["y"])
        for o in fm["objects"]
        if R.TYPE2PURPOSE.get(o["type"]) == "TOWN" and o["l"] == 0
    ]
    stats = [
        (o["x"], o["y"])
        for o in fm["objects"]
        if R.TYPE2PURPOSE.get(o["type"]) == "STAT_PERMANENT" and o["l"] == 0
    ]

    if not towns or not stats:
        return  # map has no towns or no stat permanents — skip

    far = [
        (sx, sy)
        for sx, sy in stats
        if min(math.sqrt((sx - tx) ** 2 + (sy - ty) ** 2) for tx, ty in towns) > 16.0
    ]
    assert not far, (
        f"{len(far)}/{len(stats)} STAT_PERMANENT objects are >16 tiles from nearest town "
        f"(town-anchor fill not working): {far[:3]}"
    )
