"""G1 — Coverage: hand-made maps lie in the distribution.

Measures whether fitting the generator to each of N corpus maps produces
maps whose spatial object-distance beats the shuffled control by >= 1.0 tile,
using the visitable-tile footprint fix in deps_adj.

Usage:
    uv run python src/experiments/g1_coverage.py [--maps N] [--seeds K]

Gate scale: --maps 12 --seeds 10
Smoke scale: --maps 2 --seeds 2

See src/experiments/g1_coverage.md for the full spec.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
BENCHMARK = os.path.join(SRC, "benchmark.py")
DEPS_ADJ = os.path.join(SRC, "deps_adj.py")
ADJ_JSON = os.path.join(ROOT, "out", "adjacency.json")
PYTHON = sys.executable

# Gate thresholds (from research/G1_coverage.md)
MEAN_OBJDIST_BAR = 2.5
PCT_BAR = 80.0
CONTROL_MARGIN_PASS = 1.0
CONTROL_MARGIN_WEAK = 0.5
OBJDIST_BAR = 3.0

GATE_MAPS = 12
GATE_SEEDS = 10


def rebuild_adjacency() -> None:
    """Rebuild out/adjacency.json using the fixed footprint (visitable tiles)."""
    result = subprocess.run(
        [PYTHON, DEPS_ADJ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"deps_adj.py failed:\n{result.stderr}")
    print(result.stdout.strip())


def run_benchmark(maps: int, seeds: int) -> dict:
    result = subprocess.run(
        [PYTHON, BENCHMARK, "--maps", str(maps), "--seeds", str(seeds)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"benchmark.py exited {result.returncode}\nSTDERR: {result.stderr}")
    with open(os.path.join(ROOT, "out", "benchmark.json")) as f:
        return json.load(f)


def check_anti_shortcut() -> list[str]:
    """Verify no anti-shortcut violations: deps_spatial.py must be unmodified."""
    violations = []
    spatial = os.path.join(SRC, "deps_spatial.py")
    with open(spatial) as f:
        src = f.read()
    if "PURPOSES" not in src or "def signature" not in src or "def compare" not in src:
        violations.append("deps_spatial.py appears structurally altered — anti-shortcut violation")
    return violations


def classify(c: dict) -> str:
    mean_od = c["mean_objdist"]
    pct = c["pct_within_bar"]
    margin = c["mean_control_objdist"] - mean_od
    first_three = mean_od <= MEAN_OBJDIST_BAR and pct >= PCT_BAR and c["n"] >= GATE_MAPS
    if first_three and margin >= CONTROL_MARGIN_PASS:
        return "PASS"
    if first_three and margin >= CONTROL_MARGIN_WEAK:
        return "WEAK_PASS"
    return "FAIL"


def main(maps: int = GATE_MAPS, seeds: int = GATE_SEEDS, skip_rebuild: bool = False) -> None:
    print(f"=== G1 coverage  maps={maps}  seeds={seeds} ===\n")

    violations = check_anti_shortcut()
    if violations:
        for v in violations:
            print(f"  ANTI-SHORTCUT: {v}")
        sys.exit(1)
    print("Anti-shortcut check: OK (deps_spatial.py unmodified)")

    if not skip_rebuild:
        print("\nRebuilding out/adjacency.json with visitable-tile footprint …")
        rebuild_adjacency()
    else:
        print("(adjacency rebuild skipped)")

    print(f"\nRunning benchmark --maps {maps} --seeds {seeds} …")
    data = run_benchmark(maps, seeds)
    c = data["coverage"]

    margin = round(c["mean_control_objdist"] - c["mean_objdist"], 2)
    verdict = classify(c)

    print("\n=== G1 METRICS ===")
    print(f"n                  = {c['n']}")
    print(f"mean_objdist       = {c['mean_objdist']} tiles")
    print(f"pct_within_3.0     = {c['pct_within_bar']}%")
    print(f"mean_control_objdist = {c['mean_control_objdist']} tiles")
    print(f"control_margin     = {margin} tiles  (positive = graph wins)")
    print(f"\nVERDICT: {verdict}")
    print(
        f"\nThresholds: mean_objdist <= {MEAN_OBJDIST_BAR}, "
        f"pct >= {PCT_BAR}%, margin >= {CONTROL_MARGIN_PASS}"
    )

    if c.get("rows"):
        print("\nPer-map rows:")
        for row in c["rows"]:
            flag = "" if row["objdist"] <= OBJDIST_BAR else " [OVER BAR]"
            print(
                f"  {row['map'][:40]:40}  objdist={row['objdist']}  ctrl={row['control_objdist']}{flag}"
            )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", type=int, default=GATE_MAPS)
    ap.add_argument("--seeds", type=int, default=GATE_SEEDS)
    ap.add_argument("--skip-rebuild", action="store_true", help="skip adjacency rebuild")
    a = ap.parse_args()
    main(maps=a.maps, seeds=a.seeds, skip_rebuild=a.skip_rebuild)
