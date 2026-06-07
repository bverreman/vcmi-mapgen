"""G3 — Reconstruction: a seed reproduces "All for One" and beats the shuffled control.

Runs deps_fit.fit() on "All for One" over GATE_SEEDS seeds, computes the
shuffled-control object-distance, and classifies the result as PASS / WEAK_PASS
/ FAIL per research/G3_reconstruct.md.

Usage:
    uv run python src/experiments/g3_reconstruct.py [--seeds K]

Gate scale: --seeds 10
Smoke scale: --seeds 2

See src/experiments/g3_reconstruct.md for the full spec.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import deps_fit as F
import deps_realize as R
import deps_spatial

MAPS = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps"
TARGET = "All for One"
ADJ_JSON = os.path.join(ROOT, "out", "adjacency.json")

GATE_SEEDS = 10

OBJDIST_PASS = 2.5
CONTROL_MARGIN_PASS = 1.0
OBJDIST_WEAKPASS = 3.0
CONTROL_MARGIN_WEAK = 0.5


def _shuffled_objdist(fm: dict, sig_real: dict) -> float:
    """Same shuffled-control calculation as benchmark.py."""
    free = [
        (x, y) for y, row in enumerate(fm["terrain"][0]) for x, c in enumerate(row) if c["t"] != 8
    ]
    types = tuple(o["type"] for o in fm["objects"] if o.get("l", 0) == 0)
    _seed = int(hashlib.md5(",".join(types).encode()).hexdigest(), 16) & 0xFFFFFFFF
    rnd = random.Random(_seed)
    pts = []
    for o in fm["objects"]:
        if o.get("l", 0) != 0:
            continue
        x, y = rnd.choice(free)
        pts.append((x, y, R.TYPE2PURPOSE.get(o["type"], "?")))
    return deps_spatial.compare(sig_real, deps_spatial.signature(pts))[0]


def check_anti_shortcut() -> list[str]:
    """Return a list of anti-shortcut violations (empty = clean)."""
    violations: list[str] = []

    # 1. adjacency.json must exist and have profiles
    if not os.path.isfile(ADJ_JSON):
        violations.append(f"adjacency.json not found at {ADJ_JSON}")
    else:
        with open(ADJ_JSON) as fh:
            adj = json.load(fh)
        profiles = adj.get("profiles", {})
        if not profiles:
            violations.append("adjacency.json has no profiles — graph not learned")
        total_neighbors = sum(len(p.get("neighbours", [])) for p in profiles.values())
        if total_neighbors == 0:
            violations.append("adjacency.json profiles have zero neighbours — zero-weights")

    # 2. deps_fit.fit() must call R.realize() (not copy positions)
    import inspect

    fit_src = inspect.getsource(F.fit)
    if "realize" not in fit_src:
        violations.append("deps_fit.fit() does not call realize() — position-copy shortcut?")
    if "sig_real" not in fit_src and "sig_target" not in fit_src:
        violations.append("deps_fit.fit() does not use a spatial signature knob")

    return violations


def run(seeds: int = GATE_SEEDS, target: str = TARGET) -> dict:
    """Run the G3 reconstruction measurement and return structured results."""
    target_path = os.path.join(MAPS, f"{target}.h3m")
    if not os.path.isfile(target_path):
        return {"error": f"target map not found: {target_path}"}

    m, tgt, params, sig_real, best = F.fit(target_path, seeds=seeds)
    total, dist, sp_mean, sp_diffs, best_seed, fm, tree, g, sig_gen = best

    ctrl = _shuffled_objdist(fm, sig_real)
    margin = round(ctrl - sp_mean, 3)
    verdict = classify(sp_mean, margin, seeds)

    return {
        "target": target,
        "seeds_searched": seeds,
        "best_seed": best_seed,
        "best_objdist": round(sp_mean, 3),
        "control_objdist": round(ctrl, 3),
        "margin": margin,
        "verdict": verdict,
    }


def classify(objdist: float, margin: float, seeds: int) -> str:
    if seeds < GATE_SEEDS:
        return "SMOKE"
    if objdist <= OBJDIST_PASS and margin >= CONTROL_MARGIN_PASS:
        return "PASS"
    if objdist <= OBJDIST_WEAKPASS and margin >= CONTROL_MARGIN_WEAK:
        return "WEAK_PASS"
    return "FAIL"


def main(seeds: int = GATE_SEEDS) -> None:
    print(f"=== G3 reconstruct  target='{TARGET}'  seeds={seeds} ===\n")

    violations = check_anti_shortcut()
    if violations:
        for v in violations:
            print(f"  ANTI-SHORTCUT: {v}")
        sys.exit(1)
    print("Anti-shortcut check: OK (adjacency loaded, realize() used, no position copy)")

    print(f"\nSearching {seeds} seeds vs '{TARGET}' …")
    result = run(seeds=seeds)

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    print("\n=== G3 METRICS ===")
    print(f"seeds_searched     = {result['seeds_searched']}")
    print(f"best_seed          = {result['best_seed']}")
    print(f"best_objdist       = {result['best_objdist']} tiles")
    print(f"control_objdist    = {result['control_objdist']} tiles")
    print(f"margin (ctrl-best) = {result['margin']} tiles  (positive = graph wins)")
    print(f"\nVERDICT: {result['verdict']}")
    print(f"\nThresholds: best_objdist <= {OBJDIST_PASS}, margin >= {CONTROL_MARGIN_PASS}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=GATE_SEEDS)
    a = ap.parse_args()
    main(seeds=a.seeds)
