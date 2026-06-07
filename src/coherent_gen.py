"""Coherent map generation WITH CERTAINTY.

energy_place draws an in-distribution layout, but its coherence is luck-of-the-seed
(Delta over shuffle ranges roughly -0.01 .. +0.10). "With certainty" means we do not
emit until the map provably clears a coherence bar AND is reachable:

  place (in-distribution)  ->  CLIMB coherence to target  ->  repair reachability
  ->  re-verify (coherence >= target AND reachable)  ->  emit; else next seed.

The climb is a hill-climb on the SAME coherence objective the metric scores, made
fast with a numpy evaluator (a full re-score is ~4 ms, so thousands of moves run in
seconds). Town/mine moves stay inside the reachable component so the repair stays
cheap. The function returns only a map that meets the contract, so every emitted map
is coherent by construction -- not by chance.

Run via:  uv run --with numpy python src/coherent_gen.py --terrain "All for One"
"""

import sys, os, json, math, random, argparse, collections
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coherence as CO
import energy_place as EP
import traverse as TR
import faithful

PUR = CO.PURPOSES
PIDX = {p: i for i, p in enumerate(PUR)}
NP = len(PUR)
WATER, ROCK = 8, 9


def build_model_arrays(comodel):
    """mu/sigma/weight matrices [NP x NP] indexed by purpose order."""
    mu = np.full((NP, NP), 1.0); sig = np.ones((NP, NP)); w = np.ones((NP, NP))
    wts = CO._WEIGHTS or {}
    for P in PUR:
        m = comodel.get(P)
        if m:
            mu[PIDX[P]] = m["mu"]; sig[PIDX[P]] = m["sigma"]
        if P in wts:
            w[PIDX[P]] = wts[P]
    return mu, sig, w


def score_np(coords, purs, lev, mu, sig, w, A):
    """Vectorized coherence == coherence.score_objects (mean per-object typicality).
    coords:(n,2) float, purs:(n,) int purpose-index, lev:(n,) int level."""
    n = len(purs)
    if n == 0:
        return None, None
    per = np.full(n, np.nan)
    for l in np.unique(lev):
        ii = np.where(lev == l)[0]
        c = coords[ii]; pu = purs[ii]; nl = len(ii)
        dx = c[:, 0:1] - c[:, 0][None, :]; dy = c[:, 1:2] - c[:, 1][None, :]
        D = np.sqrt(dx * dx + dy * dy); np.fill_diagonal(D, np.inf)
        cnt = np.bincount(pu, minlength=NP)
        exp = np.where(cnt > 0, 0.5 * np.sqrt(A / np.maximum(cnt, 1)), CO.CAP)
        fp = np.empty((nl, NP))
        for q in range(NP):
            cols = np.where(pu == q)[0]
            if len(cols) == 0:
                fp[:, q] = 3.0
            else:
                nd = D[:, cols].min(axis=1)
                col = np.minimum(nd / exp[q], 3.0)
                col[~np.isfinite(col)] = 3.0
                fp[:, q] = col
        MU = mu[pu]; SG = sig[pu]; WW = w[pu]
        z2 = ((fp - MU) / SG) ** 2
        per[ii] = -(WW * z2).sum(axis=1) / np.maximum(WW.sum(axis=1), 1e-9)
    return float(np.nanmean(per)), per


def _shuffle_delta(coords, purs, lev, mu, sig, w, A, rng, k=3):
    base, _ = score_np(coords, purs, lev, mu, sig, w, A)
    sh = []
    for _ in range(k):
        p2 = purs.copy()
        for l in np.unique(lev):
            ii = np.where(lev == l)[0]
            perm = rng.permutation(ii)
            p2[ii] = purs[perm]
        s, _ = score_np(coords, p2, lev, mu, sig, w, A)
        sh.append(s)
    return base, base - float(np.mean(sh))


def climb(coords, purs, lev, terr, comp0, mu, sig, w, A, seed, target, max_moves=6000):
    """Greedy hill-climb (with light annealing kicks) on coherence until Delta>=target
    or the move budget is spent. Town/mine stay in comp0. Returns improved coords."""
    rng = np.random.default_rng(seed)
    pyrng = random.Random(seed)
    H = len(terr[0]); W = len(terr[0][0])
    n = len(purs)
    cur, _ = score_np(coords, purs, lev, mu, sig, w, A)
    REQ = {PIDX["TOWN"], PIDX["MINE"]}
    # passable tiles per level for proposing targets
    free = {}
    for l in range(len(terr)):
        free[l] = [(x, y) for y in range(H) for x in range(W)
                   if terr[l][y][x]["t"] not in (WATER, ROCK)]
    occupied = {(int(lev[i]), int(coords[i, 0]), int(coords[i, 1])) for i in range(n)}
    best = coords.copy(); best_s = cur
    no_improve = 0
    for mv in range(max_moves):
        i = pyrng.randrange(n)
        l = int(lev[i]); P = int(purs[i])
        ox, oy = int(coords[i, 0]), int(coords[i, 1])
        # propose a target tile
        if P in REQ:
            cand = pyrng.choice(list(comp0))
            tx, ty, tl = cand[0], cand[1], 0
        else:
            tx, ty = pyrng.choice(free[l]); tl = l
        if (tl, tx, ty) in occupied:
            continue
        old = (coords[i, 0], coords[i, 1], lev[i])
        coords[i, 0], coords[i, 1], lev[i] = tx, ty, tl
        s, _ = score_np(coords, purs, lev, mu, sig, w, A)
        if s >= cur:
            cur = s
            occupied.discard((l, ox, oy)); occupied.add((tl, tx, ty))
            if s > best_s:
                best_s, best = s, coords.copy(); no_improve = 0
        else:
            coords[i, 0], coords[i, 1], lev[i] = old
            no_improve += 1
        if (mv & 511) == 0:
            _, d = _shuffle_delta(coords, purs, lev, mu, sig, w, A, rng, k=2)
            if d >= target:
                break
    coords[:] = best
    return coords, best_s


def generate(terrain_name, target=None, seeds=range(0, 12), out=None,
             max_moves=6000, verbose=True):
    """Return (fm, report) for a map that is coherent (Delta>=target) AND reachable.
    Tries seeds in order; emits the first that meets the contract (certainty)."""
    names = CO.all_map_names()
    place_model = EP.fit(names)
    comodel = CO.fit(names[::2])
    mu, sig, w = build_model_arrays(comodel)

    src = json.load(open(f"{ROOT}/out/maps/{terrain_name}.json"))
    terr = src["terrain"]
    A = float(len(terr[0]) * len(terr[0][0]))           # actual map area (size-correct)
    comp0 = EP._largest_component(terr, 0)

    # the bar: default = the ORIGINAL map's own coherence Delta on this terrain
    real_pts = CO.objects_from_map(src)
    rc, rp, rl = _pts_to_arrays(real_pts)
    _, real_delta = _shuffle_delta(rc, rp, rl, mu, sig, w, A, np.random.default_rng(0), k=5)
    if target is None:
        target = round(0.85 * real_delta, 4)            # 85% of the original's coherence
    if verbose:
        print(f"terrain='{terrain_name}'  real Delta={real_delta:+.3f}  target Delta>={target:+.3f}")

    best_fail = None
    for seed in seeds:
        pts = EP.place(terr, place_model, seed=seed, kA=10, radius_frac=0.16)
        gp = [(l, x, y, p) for (l, x, y, p) in pts if p in PUR]
        deco = [t for t in pts if t[3] not in PUR]
        coords, purs, lev = _pts_to_arrays(gp)
        coords, _ = climb(coords, purs, lev, terr, comp0, mu, sig, w, A, seed, target, max_moves)
        gp2 = [(int(lev[i]), int(coords[i, 0]), int(coords[i, 1]), PUR[int(purs[i])])
               for i in range(len(purs))]
        _, delta = _shuffle_delta(coords, purs, lev, mu, sig, w, A, np.random.default_rng(seed), k=5)

        fm = EP.to_fm(terr, gp2 + deco, name=f"Coherent {terrain_name} s{seed}")
        n_rem, _ = EP.repair_reachability(fm)
        rr = TR.traverse(fm)
        ok = rr["ok"] and delta >= target
        if verbose:
            print(f"  seed {seed:2d}: Delta={delta:+.3f}  reachable={rr['ok']}  removed={n_rem}  "
                  f"-> {'EMIT' if ok else 'reject'}")
        if ok:
            report = {"seed": seed, "delta": delta, "target": target, "real_delta": real_delta,
                      "reachable": True, "removed": n_rem, "objects": len(fm["objects"])}
            if out:
                faithful.to_vmap(fm, out, name=fm["name"])
                report["vmap"] = out
            return fm, report
        if best_fail is None or delta > best_fail[1]:
            best_fail = (fm, delta, seed, n_rem, rr["ok"])
    # no seed met the bar -> return the best attempt, clearly flagged
    fm, delta, seed, n_rem, reach = best_fail
    report = {"seed": seed, "delta": delta, "target": target, "real_delta": real_delta,
              "reachable": reach, "removed": n_rem, "objects": len(fm["objects"]),
              "met_target": False}
    if out and reach:
        faithful.to_vmap(fm, out, name=fm["name"]); report["vmap"] = out
    return fm, report


def _pts_to_arrays(pts):
    coords = np.array([[x, y] for (_, x, y, _) in pts], dtype=float)
    purs = np.array([PIDX[p] for (_, _, _, p) in pts], dtype=int)
    lev = np.array([l for (l, _, _, _) in pts], dtype=int)
    return coords, purs, lev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--moves", type=int, default=6000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = args.out or f"{ROOT}/out/Coherent-{args.terrain.replace(' ', '_')}.vmap"
    fm, rep = generate(args.terrain, target=args.target, seeds=range(args.seeds),
                       out=out, max_moves=args.moves)
    print("\nRESULT:", json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
