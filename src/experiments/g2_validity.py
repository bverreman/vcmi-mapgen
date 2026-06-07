"""G2 — Validity: generated maps are playable without a target map.

Checks that plain realize(seed) passes both:
  - traverse.traverse(fm, em)["ok"] == True (all zones/towns/mines reachable)
  - every purpose with corpus rate >= 0.2/1k is within [0.5, 2.0]x that rate

Usage:
    uv run python src/experiments/g2_validity.py [--seeds K]

Gate scale: --seeds 10
Smoke scale: --seeds 2

See src/experiments/g2_validity.md for the full spec.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import deps_realize as R
import traverse as T

# Gate thresholds (from research/G2_validity.md)
PASS_RATE_BAR = 0.9
DENSITY_LO = 0.5
DENSITY_HI = 2.0
CORPUS_RATE_MIN = 0.2  # purposes below this rate are not checked

GATE_SEEDS = 10


def _corpus_rates() -> dict[str, float]:
    """Per-purpose corpus rate in objects per 1000 tiles (same as benchmark.py)."""
    return {
        p: 1000.0 * sum(v["counts"]) / sum(R.FAC["proportions"].values())
        for p, v in R.FAC["budget"].items()
    }


def check_seed(seed: int, rates: dict[str, float]) -> dict:
    """Generate one map and return per-seed validity result."""
    fm, tree, em = R.realize(seed=seed)
    rr = T.traverse(fm, em)

    cnt = collections.Counter(R.TYPE2PURPOSE.get(o["type"], "?") for o in fm["objects"])
    tiles = sum(1 for lvl in fm["terrain"] for row in lvl for _ in row)

    density_off = []
    for pur, rate in rates.items():
        if rate < CORPUS_RATE_MIN:
            continue
        got = 1000.0 * cnt.get(pur, 0) / tiles
        lo, hi = DENSITY_LO * rate, DENSITY_HI * rate
        if not (lo <= got <= hi):
            density_off.append(
                {"purpose": pur, "got": round(got, 4), "lo": round(lo, 4), "hi": round(hi, 4)}
            )

    ok = rr["ok"] and not density_off
    return {
        "seed": seed,
        "ok": ok,
        "traverse_ok": rr["ok"],
        "unreachable_towns": rr["unreachable_towns"],
        "unreachable_mines": rr["unreachable_mines"],
        "density_off": density_off,
    }


def run(seeds: int = GATE_SEEDS) -> dict:
    rates = _corpus_rates()
    rows = []
    n_pass = 0
    for seed in range(seeds):
        row = check_seed(seed, rates)
        rows.append(row)
        n_pass += row["ok"]

    pass_rate = round(n_pass / max(1, seeds), 3)
    verdict = "PASS" if seeds >= GATE_SEEDS and pass_rate >= PASS_RATE_BAR else "FAIL"

    return {
        "seeds": seeds,
        "n_pass": n_pass,
        "pass_rate": pass_rate,
        "verdict": verdict,
        "rows": rows,
    }


def main(seeds: int = GATE_SEEDS) -> None:
    print(f"=== G2 validity  seeds={seeds} ===\n")
    result = run(seeds)

    print(f"pass_rate = {result['pass_rate']} ({result['n_pass']}/{result['seeds']} seeds)")
    print(f"\nVERDICT: {result['verdict']}")
    print(f"Thresholds: seeds >= {GATE_SEEDS}, pass_rate >= {PASS_RATE_BAR}")

    print("\nPer-seed rows:")
    for row in result["rows"]:
        status = "PASS" if row["ok"] else "FAIL"
        details = []
        if not row["traverse_ok"]:
            details.append(f"traverse FAIL (mines={row['unreachable_mines']})")
        if row["density_off"]:
            details.append(f"density_off={[d['purpose'] for d in row['density_off']]}")
        detail_str = "  " + "; ".join(details) if details else ""
        print(f"  seed={row['seed']}: {status}{detail_str}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=GATE_SEEDS)
    a = ap.parse_args()
    main(seeds=a.seeds)
