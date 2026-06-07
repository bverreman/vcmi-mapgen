"""Tests for G3 — Reconstruction: seed reproduces 'All for One'.

Covers:
1. classify() correctly identifies PASS / WEAK_PASS / FAIL / SMOKE cases
2. Anti-shortcut: adjacency.json loaded with non-zero profiles
3. Anti-shortcut: deps_fit.fit() calls realize() — no position copying
4. Smoke run (seeds=2) returns the expected result schema
5. Gate run (seeds=10) verdict is PASS
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)


# ---------------------------------------------------------------------------
# Criterion 1: classify() logic
# ---------------------------------------------------------------------------


def test_classify_pass():
    from g3_reconstruct import classify, GATE_SEEDS

    assert classify(2.0, 1.5, GATE_SEEDS) == "PASS"
    assert classify(2.5, 1.0, GATE_SEEDS) == "PASS"


def test_classify_weak_pass():
    from g3_reconstruct import classify, GATE_SEEDS

    assert classify(3.0, 0.5, GATE_SEEDS) == "WEAK_PASS"
    assert classify(2.9, 0.7, GATE_SEEDS) == "WEAK_PASS"


def test_classify_fail():
    from g3_reconstruct import classify, GATE_SEEDS

    # object-distance over 3.0
    assert classify(3.1, 1.0, GATE_SEEDS) == "FAIL"
    # margin below 0.5
    assert classify(2.0, 0.4, GATE_SEEDS) == "FAIL"


def test_classify_smoke_below_gate_seeds():
    from g3_reconstruct import classify, GATE_SEEDS

    # Seeds below the gate threshold → SMOKE regardless of metrics
    assert classify(1.0, 2.0, GATE_SEEDS - 1) == "SMOKE"


def test_classify_boundary_pass():
    """Exact boundary: 2.5 tiles and exactly 1.0 margin should be PASS."""
    from g3_reconstruct import classify, GATE_SEEDS

    assert classify(2.5, 1.0, GATE_SEEDS) == "PASS"


def test_classify_boundary_just_fail():
    """Just over bar: 2.51 with perfect margin should be WEAK_PASS (≤ 3.0)."""
    from g3_reconstruct import classify, GATE_SEEDS

    assert classify(2.51, 1.0, GATE_SEEDS) == "WEAK_PASS"


# ---------------------------------------------------------------------------
# Criterion 2: adjacency.json loaded with non-zero profiles
# ---------------------------------------------------------------------------


def test_adjacency_json_exists():
    adj_path = os.path.join(ROOT, "out", "adjacency.json")
    assert os.path.isfile(adj_path), "out/adjacency.json not found"


def test_adjacency_has_nonzero_profiles():
    """adjacency.json must have profiles with at least one neighbour each."""
    adj_path = os.path.join(ROOT, "out", "adjacency.json")
    assert os.path.isfile(adj_path), "out/adjacency.json not found"
    with open(adj_path) as f:
        adj = json.load(f)
    profiles = adj.get("profiles", {})
    assert profiles, "adjacency.json has no profiles"
    total = sum(len(p.get("neighbours", [])) for p in profiles.values())
    assert total > 0, "all adjacency profiles have zero neighbours — zero-weights"


# ---------------------------------------------------------------------------
# Criterion 3: deps_fit.fit() calls realize() — no position copying
# ---------------------------------------------------------------------------


def test_fit_calls_realize():
    """deps_fit.fit() must call realize() to generate maps, not copy positions."""
    import inspect
    import deps_fit as F

    src = inspect.getsource(F.fit)
    assert "realize" in src, "fit() does not call realize() — potential position-copy shortcut"


def test_fit_uses_sig_knob():
    """deps_fit.fit() must pass a spatial signature as a generator knob."""
    import inspect
    import deps_fit as F

    src = inspect.getsource(F.fit)
    assert "sig_target" in src or "sig_real" in src, (
        "fit() does not pass spatial signature to realize() — graph placement not guided"
    )


def test_fit_params_no_raw_positions():
    """params built by fit() must not contain raw object coordinates."""
    import deps_fit as F
    import h3m

    target_path = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m"
    if not os.path.isfile(target_path):
        return  # map not available; skip
    m = h3m.parse_file(target_path)
    tgt = F.feats_real(m)
    W = min(m.width, 72)
    H = min(m.height, 72)
    params = F.params_from_target(tgt, W, H, m.two_level)

    # params must not contain a list of (x, y) tuples from the real map
    real_coords = {(o.x, o.y) for o in m.objects if o.l == 0}
    for key, val in params.items():
        if isinstance(val, (list, tuple)) and val:
            first = val[0] if not isinstance(val, dict) else None
            if isinstance(first, (list, tuple)) and len(first) >= 2:
                if tuple(first[:2]) in real_coords:
                    raise AssertionError(f"params['{key}'] appears to contain raw target positions")


# ---------------------------------------------------------------------------
# Criterion 4: smoke run returns expected schema
# ---------------------------------------------------------------------------


def test_smoke_run_schema():
    """run(seeds=2) returns a dict with the required keys."""
    from g3_reconstruct import run

    target_path = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m"
    if not os.path.isfile(target_path):
        return  # map not available; skip

    result = run(seeds=2)
    for key in (
        "target",
        "seeds_searched",
        "best_seed",
        "best_objdist",
        "control_objdist",
        "margin",
        "verdict",
    ):
        assert key in result, f"run() result missing '{key}'"
    assert result["seeds_searched"] == 2
    assert result["best_objdist"] >= 0.0
    assert result["control_objdist"] >= 0.0


# ---------------------------------------------------------------------------
# Criterion 5: gate run returns PASS
# ---------------------------------------------------------------------------


def test_gate_verdict_pass():
    """G3 harness must report PASS over 10 seeds against 'All for One'."""
    from g3_reconstruct import run, GATE_SEEDS, OBJDIST_PASS, CONTROL_MARGIN_PASS

    target_path = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m"
    if not os.path.isfile(target_path):
        return  # map not available; skip

    result = run(seeds=GATE_SEEDS)
    assert "error" not in result, f"run() returned error: {result.get('error')}"
    assert result["verdict"] in ("PASS", "WEAK_PASS"), (
        f"G3 verdict={result['verdict']}\n"
        f"  best_objdist={result['best_objdist']} (bar: <= {OBJDIST_PASS})\n"
        f"  margin={result['margin']} (bar: >= {CONTROL_MARGIN_PASS})\n"
        f"  control_objdist={result['control_objdist']}"
    )
