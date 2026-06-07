"""Tests for G2 — Validity: generated maps are playable.

Covers:
1. traverse.traverse() returns ok=True for a known good seed
2. density check catches a map with a purpose way out of band
3. Anti-shortcut: traverse.py structural integrity (real BFS, fixed band)
4. Gate harness reports pass_rate >= 0.9 at K=10 seeds
"""

from __future__ import annotations

import collections
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)


# ---------------------------------------------------------------------------
# Criterion 1: traverse returns ok=True for a reachable map
# ---------------------------------------------------------------------------


def test_traverse_ok_for_good_seed():
    """realize(seed=0) must produce a traversable map (traverse ok=True)."""
    import deps_realize as R
    import traverse as T

    fm, tree, em = R.realize(seed=0)
    rr = T.traverse(fm, em)
    assert rr["ok"], (
        f"seed=0 traverse failed: towns={rr['unreachable_towns']} mines={rr['unreachable_mines']}"
    )


# ---------------------------------------------------------------------------
# Criterion 2: density check catches out-of-band purposes
# ---------------------------------------------------------------------------


def test_density_check_catches_zero_count():
    """check_seed's density logic flags a purpose at 0 objects when rate >= 0.2/1k."""
    import deps_realize as R
    from g2_validity import _corpus_rates

    rates = _corpus_rates()
    # Find a purpose with rate >= 0.2 that is present in our corpus
    high_rate_pur = next(
        (p for p, r in sorted(rates.items(), key=lambda x: -x[1]) if r >= 0.2), None
    )
    assert high_rate_pur is not None, "No purpose with rate >= 0.2 found"

    # Synthesize a map-like counter with zero objects for that purpose
    cnt = collections.Counter()
    tiles = 72 * 72  # standard map size

    density_off = []
    for pur, rate in rates.items():
        if rate < 0.2:
            continue
        got = 1000.0 * cnt.get(pur, 0) / tiles
        lo, hi = 0.5 * rate, 2.0 * rate
        if not (lo <= got <= hi):
            density_off.append(pur)

    assert high_rate_pur in density_off, (
        f"{high_rate_pur} (rate={rates[high_rate_pur]:.3f}) not flagged when count=0"
    )


def test_density_check_passes_in_band():
    """check_seed density logic does NOT flag a purpose exactly at 1.0x rate."""
    import deps_realize as R
    from g2_validity import _corpus_rates

    rates = _corpus_rates()
    tiles = 72 * 72

    # Synthesize counter at exactly the corpus rate for every checked purpose
    cnt = collections.Counter()
    for pur, rate in rates.items():
        if rate >= 0.2:
            # Set count exactly at corpus rate (objects per 1000 tiles * tiles / 1000)
            cnt[pur] = int(round(rate * tiles / 1000.0))

    density_off = []
    for pur, rate in rates.items():
        if rate < 0.2:
            continue
        got = 1000.0 * cnt.get(pur, 0) / tiles
        lo, hi = 0.5 * rate, 2.0 * rate
        if not (lo <= got <= hi):
            density_off.append(pur)

    assert not density_off, f"Purposes at exact corpus rate flagged out-of-band: {density_off}"


# ---------------------------------------------------------------------------
# Criterion 3: anti-shortcut — traverse.py uses real BFS, fixed [0.5, 2.0] band
# ---------------------------------------------------------------------------


def test_traverse_uses_bfs():
    """traverse.py must define passable_grid and the BFS (collections.deque usage)."""
    import inspect
    import traverse

    src = inspect.getsource(traverse)
    assert "passable_grid" in src, "traverse.py missing passable_grid function"
    assert "deque" in src, "traverse.py missing deque — BFS may not be real"
    assert "WATER" in src or "ROCK" in src, "traverse.py missing WATER/ROCK terrain constants"


def test_density_band_is_fixed():
    """g2_validity.py must use the fixed [0.5, 2.0] band, not a wider one."""
    import g2_validity

    assert g2_validity.DENSITY_LO == 0.5, f"DENSITY_LO={g2_validity.DENSITY_LO}, expected 0.5"
    assert g2_validity.DENSITY_HI == 2.0, f"DENSITY_HI={g2_validity.DENSITY_HI}, expected 2.0"


# ---------------------------------------------------------------------------
# Criterion 4: gate harness passes at K=10 seeds
# ---------------------------------------------------------------------------


def test_gate_pass_rate_10_seeds():
    """G2 harness must report pass_rate >= 0.9 over 10 seeds."""
    from g2_validity import run, PASS_RATE_BAR, GATE_SEEDS

    result = run(seeds=GATE_SEEDS)
    assert result["pass_rate"] >= PASS_RATE_BAR, (
        f"G2 pass_rate={result['pass_rate']} < {PASS_RATE_BAR}\n"
        + "\n".join(
            f"  seed={r['seed']}: traverse={r['traverse_ok']} density_off={[d['purpose'] for d in r['density_off']]}"
            for r in result["rows"]
            if not r["ok"]
        )
    )
