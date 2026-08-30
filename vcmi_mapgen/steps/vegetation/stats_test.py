"""Reliability tests for steps.vegetation.stats (corpus vegetation statistics)."""
from vcmi_mapgen.steps.vegetation import stats as PS


def test_pair_denominator_matches_bruteforce():
    """D[r] (numpy shifted-mask count) == brute-force ordered pair count at Chebyshev r."""
    ts = {(x, y) for x in range(7) for y in range(5)} | {(9, 2), (10, 2)}
    D = PS._pair_denominator(ts)
    for r in range(PS.RMAX + 1):
        if r == 0:
            brute = len(ts)
        else:
            brute = sum(1 for a in ts for b in ts
                        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) == r)
        assert D[r] == brute, f"ring {r}: {D[r]} != {brute}"


def test_ring_offsets_partition_window():
    """Rings 0..RMAX exactly tile the (2R+1)^2 window."""
    seen = {(0, 0)}
    for r in range(1, PS.RMAX + 1):
        offs = PS._OFFS[r]
        assert len(offs) == 8 * r
        seen.update(offs)
    assert len(seen) == (2 * PS.RMAX + 1) ** 2
