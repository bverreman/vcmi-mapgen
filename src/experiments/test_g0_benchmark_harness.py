"""Tests for G0 — Trustworthy benchmark harness.

These tests verify the four G0 success criteria at reduced scale so they
run in reasonable time.  All structural assertions are independent of real
map corpus paths.
"""

from __future__ import annotations

import os
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

PYTHON = "/usr/bin/python3"
BENCHMARK_SCRIPT = os.path.join(SRC, "benchmark.py")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_fm(n_objects: int = 3) -> dict:
    """Synthetic map fixture: 4×4 land surface with n objects of mixed types."""
    return {
        "terrain": [[[{"t": 2} for _ in range(4)] for _ in range(4)]],
        "objects": [
            {"type": "goldMine", "l": 0},
            {"type": "randomMonsterLevel3", "l": 0},
            {"type": "goldMine", "l": 0},
        ][:n_objects],
    }


# ---------------------------------------------------------------------------
# Criterion 1: exit 0 at minimal scale
# ---------------------------------------------------------------------------


def test_benchmark_exits_zero_minimal():
    """Benchmark runs to completion with exit 0 at smallest useful scale."""
    result = subprocess.run(
        [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"benchmark exited {result.returncode}\nSTDERR: {result.stderr}"


# ---------------------------------------------------------------------------
# Criterion 2: required output fields are present
# ---------------------------------------------------------------------------


def test_output_schema_coverage():
    import json

    subprocess.run(
        [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
        capture_output=True,
        cwd=ROOT,
    )
    with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
        data = json.load(f)
    c = data["coverage"]
    for field in ("mean_objdist", "mean_control_objdist", "pct_within_bar", "n"):
        assert field in c, f"coverage missing '{field}'"


def test_output_schema_validity():
    import json

    subprocess.run(
        [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
        capture_output=True,
        cwd=ROOT,
    )
    with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
        data = json.load(f)
    v = data["validity"]
    for field in ("pass_rate", "seeds"):
        assert field in v, f"validity missing '{field}'"


def test_output_schema_reconstruct():
    import json

    subprocess.run(
        [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
        capture_output=True,
        cwd=ROOT,
    )
    with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
        data = json.load(f)
    r = data["reconstruct"]
    for field in ("best_objdist", "control_objdist", "target"):
        assert field in r, f"reconstruct missing '{field}'"


# ---------------------------------------------------------------------------
# Criterion 3: determinism of mean_objdist and pass_rate
# ---------------------------------------------------------------------------


def test_mean_objdist_deterministic():
    """Two identical runs produce the same mean_objdist."""
    import json

    def run():
        subprocess.run(
            [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
            capture_output=True,
            cwd=ROOT,
        )
        with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
            return json.load(f)["coverage"]["mean_objdist"]

    assert run() == run(), "mean_objdist differs between two identical runs"


def test_pass_rate_deterministic():
    """Two identical runs produce the same validity pass_rate."""
    import json

    def run():
        subprocess.run(
            [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
            capture_output=True,
            cwd=ROOT,
        )
        with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
            return json.load(f)["validity"]["pass_rate"]

    assert run() == run(), "pass_rate differs between two identical runs"


def test_control_objdist_deterministic():
    """After the seeded-RNG fix, control_objdist is also reproducible."""
    import json

    def run():
        subprocess.run(
            [PYTHON, BENCHMARK_SCRIPT, "--maps", "1", "--seeds", "1"],
            capture_output=True,
            cwd=ROOT,
        )
        with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
            return json.load(f)["coverage"]["mean_control_objdist"]

    assert run() == run(), "mean_control_objdist differs between two identical runs"


# ---------------------------------------------------------------------------
# Criterion 4: shuffled control is a fair within-map control
# ---------------------------------------------------------------------------


def test_shuffled_uses_same_object_type_multiset():
    """_shuffled_objdist builds pts from fm['objects'] with unchanged type multiset."""
    import benchmark as bm
    import deps_realize as R

    fm = _minimal_fm()
    expected_types = sorted(
        R.TYPE2PURPOSE.get(o["type"], "?") for o in fm["objects"] if o.get("l", 0) == 0
    )

    # Patch random to track what types are passed to signature.
    captured_pts: list = []
    _orig_sig = bm.deps_spatial.signature

    def _capture_sig(pts, **kwargs):
        captured_pts.extend(pts)
        return _orig_sig(pts, **kwargs)

    bm.deps_spatial.signature = _capture_sig
    try:
        bm._shuffled_objdist(fm, {})
    finally:
        bm.deps_spatial.signature = _orig_sig

    actual_types = sorted(p for _, _, p in captured_pts)
    assert actual_types == expected_types, (
        f"shuffled pts types {actual_types} != generated types {expected_types}"
    )


def test_shuffled_picks_from_free_land_tiles():
    """Shuffled pts land only on non-water tiles (t != 8)."""
    import benchmark as bm

    # Build a map with one water tile and a mix of land tiles
    fm = {
        "terrain": [
            [
                [{"t": 2}, {"t": 8}, {"t": 2}, {"t": 2}],
                [{"t": 2}, {"t": 2}, {"t": 2}, {"t": 2}],
                [{"t": 2}, {"t": 2}, {"t": 2}, {"t": 2}],
                [{"t": 2}, {"t": 2}, {"t": 2}, {"t": 2}],
            ]
        ],
        "objects": [{"type": "goldMine", "l": 0}],
    }
    captured_pts: list = []
    _orig_sig = bm.deps_spatial.signature

    def _capture_sig(pts, **kwargs):
        captured_pts.extend(pts)
        return _orig_sig(pts, **kwargs)

    bm.deps_spatial.signature = _capture_sig
    try:
        bm._shuffled_objdist(fm, {})
    finally:
        bm.deps_spatial.signature = _orig_sig

    # The water tile is at x=1,y=0 (t=8) — must never appear in shuffled pts
    assert all(not (x == 1 and y == 0) for x, y, _ in captured_pts), (
        "shuffled pt landed on a water tile"
    )


def test_shuffled_deterministic_with_same_fm():
    """Same fm produces same control value (seeded local RNG)."""
    import benchmark as bm

    fm = _minimal_fm()
    r1 = bm._shuffled_objdist(fm, {})
    r2 = bm._shuffled_objdist(fm, {})
    assert r1 == r2, "shuffled control should be deterministic for the same fm"
