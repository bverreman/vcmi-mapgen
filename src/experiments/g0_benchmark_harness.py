"""G0 — Trustworthy benchmark harness verification.

Runs the benchmark at gate scale (--maps 8 --seeds 4), checks output schema,
verifies determinism of mean_objdist and pass_rate, and reports all required
metrics.  See src/experiments/g0_benchmark_harness.md for the full spec.

Usage:
    /usr/bin/python3 src/experiments/g0_benchmark_harness.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCHMARK = os.path.join(ROOT, "src", "benchmark.py")
PYTHON = sys.executable

REQUIRED_COVERAGE = {"mean_objdist", "mean_control_objdist", "pct_within_bar", "n"}
REQUIRED_VALIDITY = {"pass_rate", "seeds"}
REQUIRED_RECONSTRUCT = {"best_objdist", "control_objdist", "best_total", "target"}

MAPS = 8
SEEDS = 4


def _run_benchmark(maps: int = MAPS, seeds: int = SEEDS) -> dict:
    cmd = [PYTHON, BENCHMARK, "--maps", str(maps), "--seeds", str(seeds)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"benchmark exit {result.returncode}\nSTDERR: {result.stderr}")
    out_path = os.path.join(ROOT, "out", "benchmark.json")
    with open(out_path) as f:
        return json.load(f)


def check_schema(data: dict) -> list[str]:
    errors = []
    c = data.get("coverage", {})
    missing_c = REQUIRED_COVERAGE - set(c)
    if missing_c:
        errors.append(f"coverage missing fields: {missing_c}")
    v = data.get("validity", {})
    missing_v = REQUIRED_VALIDITY - set(v)
    if missing_v:
        errors.append(f"validity missing fields: {missing_v}")
    r = data.get("reconstruct", {})
    missing_r = REQUIRED_RECONSTRUCT - set(r)
    if missing_r:
        errors.append(f"reconstruct missing fields: {missing_r}")
    return errors


def check_determinism(maps: int = MAPS, seeds: int = SEEDS) -> tuple[bool, dict, dict]:
    run1 = _run_benchmark(maps, seeds)
    run2 = _run_benchmark(maps, seeds)
    ok = (
        run1["coverage"]["mean_objdist"] == run2["coverage"]["mean_objdist"]
        and run1["validity"]["pass_rate"] == run2["validity"]["pass_rate"]
    )
    return ok, run1, run2


def main() -> None:
    print(f"=== G0 verification  maps={MAPS}  seeds={SEEDS} ===\n")

    print("Step 1: run benchmark and check schema …")
    data = _run_benchmark()
    errors = check_schema(data)
    if errors:
        print("  FAIL schema errors:")
        for e in errors:
            print(f"    {e}")
        sys.exit(1)
    print("  schema OK")

    print("\nStep 2: determinism check (running benchmark twice) …")
    det_ok, run1, run2 = check_determinism()
    r1c, r2c = run1["coverage"], run2["coverage"]
    r1v, r2v = run1["validity"], run2["validity"]
    print(f"  run1 mean_objdist={r1c['mean_objdist']}  pass_rate={r1v['pass_rate']}")
    print(f"  run2 mean_objdist={r2c['mean_objdist']}  pass_rate={r2v['pass_rate']}")
    if not det_ok:
        print("  FAIL mean_objdist or pass_rate differ between runs")
        sys.exit(1)
    print("  determinism OK")

    c = data["coverage"]
    v = data["validity"]
    r = data["reconstruct"]
    print("\n=== G0 METRICS ===")
    print(
        f"COVERAGE   mean_objdist={c['mean_objdist']}  "
        f"control={c['mean_control_objdist']}  "
        f"pct<={3.0}={c['pct_within_bar']}%  n={c['n']}"
    )
    print(f"VALIDITY   pass_rate={v['pass_rate']}  seeds={v['seeds']}")
    print(
        f"RECONSTRUCT  best_objdist={r['best_objdist']}  "
        f"control={r['control_objdist']}  target={r['target']}"
    )
    print(f"\nDETERMINISM OK: {det_ok}")
    print("\nG0 PASS — harness is trustworthy")


if __name__ == "__main__":
    main()
