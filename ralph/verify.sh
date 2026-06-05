#!/usr/bin/env bash
# Fitness gate for the Ralph loop. Exit 0 = PASS, non-zero = regression.
# Set RALPH_SKIP_LOADTEST=1 to skip the slow headless-editor load test.
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[verify] 1/3 generation runs, is fully traversable, and writes a vmap"
python3 - <<'PY' || { echo "[verify] FAIL: generation/traversability"; exit 1; }
import sys; sys.path.insert(0, 'src')
import deps_realize as R, faithful, traverse as T
fm, tree, em = R.realize(seed=4)
# global traversability gate: every zone, town and mine reachable on foot from
# the starting town (catches sealed chokepoints / blocked entrances).
r = T.traverse(fm, em)
print('   objects=%d  reachable %d/%d zones, towns/mines ok=%s'
      % (len(fm['objects']), r['zones_reached'], r['zones_total'], r['ok']))
if not r['ok']:
    print('   UNREACHABLE bad_zones=%s towns=%s mines=%s'
          % (r['bad_zones'], r['unreachable_towns'], r['unreachable_mines']))
    sys.exit(1)
faithful.save(fm, 'out/_ralph_gen.json')
faithful.to_vmap(fm, 'out/_ralph_gen.vmap', name='ralph gen')
PY

echo "[verify] 1b/3 two-level underground is reachable through the gate"
python3 - <<'PY' || { echo "[verify] FAIL: underground traversability"; exit 1; }
import sys; sys.path.insert(0, 'src')
import deps_fit as F, deps_realize as R, traverse as T, h3m, deps_spatial
p = '/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/Marshland Menace.h3m'
m = h3m.parse_file(p); tgt = F.feats_real(m)
params = F.params_from_target(tgt, 72, 72, True); params['sig'] = deps_spatial.signature(F.points_real(m))
assert params.get('two_level'), 'target should be two-level'
fully_ok = 0
for seed in range(8):
    fm, tree, em = R.realize(72, 72, seed=seed, params=params)
    r = T.traverse(fm, em)
    if r['levels'] < 2:
        continue                                   # this seed embedded as 1 level
    # underground guarantee: every level-1 mine/town must be reachable from the
    # start town THROUGH the subterranean-gate pair (cavern not sealed off).
    ug_bad = [o for o in r['unreachable_mines'] + r['unreachable_towns'] if o[2] == 1]
    if ug_bad:
        print('   seed %d UNREACHABLE underground objects: %s' % (seed, ug_bad)); sys.exit(1)
    ug_objs = sum(1 for o in fm['objects'] if o.get('l', 0) == 1
                  and o['type'] != 'subterraneanGate')
    if ug_objs and not r['cavern_reached_tiles']:
        print('   seed %d cavern has %d objects but 0 reachable tiles' % (seed, ug_objs)); sys.exit(1)
    fully_ok += r['ok']
print('   8 seeds: 0 stranded underground objects, %d fully traversable' % fully_ok)
sys.exit(0 if fully_ok > 0 else 1)
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
r = gate.load_test('out/_ralph_gen.vmap', wait=95)
print('   %s' % r)
sys.exit(0 if (r['loaded'] and not r['fatal']) else 1)
PY

echo "VERIFY PASS"
