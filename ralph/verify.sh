#!/usr/bin/env bash
# Fitness gate for the Ralph loop. Exit 0 = PASS, non-zero = regression.
# Set RALPH_SKIP_LOADTEST=1 to skip the slow headless-editor load test.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[verify] 1/3 generation runs and writes a vmap"
python3 - <<'PY' || { echo "[verify] FAIL: generation"; exit 1; }
import sys; sys.path.insert(0, 'src')
import deps_realize as R, faithful
fm, tree, em = R.realize(seed=4)
faithful.save(fm, 'out/_ralph_gen.json')
faithful.to_vmap(fm, 'out/_ralph_gen.vmap', name='ralph gen')
print('   objects=%d' % len(fm['objects']))
PY

echo "[verify] 2/3 object-distance <= 3 on held-out map (Dawn of War)"
python3 - <<'PY' || { echo "[verify] FAIL: object-distance > 3"; exit 1; }
import sys; sys.path.insert(0, 'src')
import deps_fit as F
m, tgt, params, sig, best = F.fit(
    '/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/Dawn of War.h3m', seeds=4)
sp = best[2]
print('   object-distance=%.2f  total=%.1f' % (sp, best[0]))
sys.exit(0 if sp <= 3.0 else 1)
PY

if [ "${RALPH_SKIP_LOADTEST:-0}" = "1" ]; then
  echo "[verify] 3/3 load-test SKIPPED (RALPH_SKIP_LOADTEST=1)"
  echo "VERIFY PASS (no load-test)"; exit 0
fi

echo "[verify] 3/3 headless editor load-test (slow ~90s)"
python3 - <<'PY' || { echo "[verify] FAIL: editor load-test"; exit 1; }
import sys; sys.path.insert(0, 'src')
import gate
r = gate.load_test('out/_ralph_gen.vmap', wait=70)
print('   %s' % r)
sys.exit(0 if (r['loaded'] and not r['fatal']) else 1)
PY

echo "VERIFY PASS"
