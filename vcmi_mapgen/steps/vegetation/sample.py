"""Marked-point-process vegetation sampler (spec §2.7 + M1/M2).

Samples a zone's decoration configuration from the Gibbs marked point process fitted by
`pp_stats` — birth/death Metropolis–Hastings on OBJECT configurations, not tiles:

  - marks = ontology decoration identities (`decor_pool`), weighted by corpus frequency
    (a spatial statistic; identity/mask/category always resolve through the ontology),
  - Papangelou conditional intensity
        lam*(u,c) = exp(alpha) * lam[c][ebin(u)] * M(u) * exp(E(u,c))
    where the pattern's two scales are split (a purely attractive pairwise Gibbs process is
    EXPLOSIVE — raw log g > 0 at all ranges compounds into one runaway clump):
      * M(u) — LOG-GAUSSIAN COX modulation: a smooth seeded value-noise log-field carrying the
        LARGE-SCALE density variation (forest masses vs clearings). Its std sigma is FITTED from
        corpus coarse-cell overdispersion (`pp_stats.cox_sigma`, Fisher-index inversion).
      * E(u,c) — LOCAL interaction only (rings r <= RINT), background-normalized potentials
        theta = log(g(r)/g(4)) (`pp_stats.theta_local`), GEYER-SATURATED: each (category, ring)
        neighbour count is capped at SAT so lam* stays bounded (Geyer 1999).
  - NO vegetation hard core — footprints may overlap/stack (corpus-legal); stacking is priced
    by the learned r=0 potential,
  - hard zeros only where the game needs them: a blocking cell off-zone or on the PROTECTED
    walkable web (spanning backbone + gates, kept constructive per spec §5),
  - budget: the realized blocking-union coverage is steered to the corpus `veg_blocked_frac`
    by a global log-offset alpha, corrected on a Boolean-model (coverage-exponent) schedule.

The blocked mask is EMERGENT (union of sampled footprints) — run-length stats become the
validation metric, per the M1 experiment:

    uv run python -m vcmi_mapgen.steps.vegetation.sample --map "All for One" --zone 11
"""
import argparse
import collections
import math
import os

from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen.steps.vegetation import stats as PS
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen import zone_field as ZF

EBINS = PS.EBINS
RINT = 2                        # local-interaction range (Chebyshev rings 0..RINT)
KW = 2 * RINT + 1               # interaction window (5x5)
STEPS_PER_TILE = 40             # MH proposals per zone tile
SAT = 2                         # Geyer saturation: neighbour count cap per (category, ring)
COX_CELL = 7                    # value-noise cell of the Cox log-field (~ the corpus CELL scale)
SPACING = ZF.SPACING            # backbone node spacing for the protected web
BASE_W = 0.3                    # base weight so native-but-corpus-rare sprites stay possible


def build_model(terrain):
    """Fitted per-terrain sampling model: category list, intensities, theta kernel, ident pools."""
    import numpy as np
    st = PS.load(terrain)
    th = PS.theta_local(st, rint=RINT)

    pool = ON.decor_pool(terrain, exclude_types=ZE.EXCLUDE_DECOR_TYPES)
    cats_all = ON.veg_categories()
    by_cat = collections.defaultdict(list)
    for ident in pool:
        ci = ON.category_of(ident["animation"])
        if ci is not None:
            by_cat[cats_all[ci]].append(ident)

    cats = [c for c in sorted(st["lam_tot"], key=st["lam_tot"].get, reverse=True)
            if st["lam_tot"][c] > 0 and by_cat.get(c)]
    A = len(cats)
    cidx = {c: i for i, c in enumerate(cats)}

    L = np.zeros((A, EBINS))
    for c in cats:
        L[cidx[c]] = st["lam"][c]

    T = np.zeros((A, A, RINT + 1))
    for key, row in th.items():
        ca, cb = key.split("|")
        if ca in cidx and cb in cidx:
            T[cidx[ca], cidx[cb]] = row

    idents, iweights, iblk = [], [], []
    for c in cats:
        w = st["anim_w"].get(c, {})
        ids = by_cat[c]
        idents.append(ids)
        iweights.append([w.get(i["animation"].lower(), 0) + BASE_W for i in ids])
        blk = []
        for i in ids:
            cells = [(cx, cy, b) for cx, cy, b in OR.mask_cells(i["mask"], 0, 0)]
            blk.append([(cx, cy) for cx, cy, b in cells if b])
        iblk.append(blk)

    return {"terrain": terrain, "cats": cats, "L": L, "T": T,
            "idents": idents, "iweights": iweights, "iblk": iblk,
            "sigma": PS.cox_sigma(st),
            "target": st["veg_blocked_frac"], "runs": st["runs"]}


def protected_web(ts, zones, zid, edist, seedt, spacing=SPACING, extra_nodes=(),
                  avoid=frozenset(), open_frac=0.5, entrances=None, keep_off=frozenset()):
    """The PROTECTED walkable set: spanning backbone over farthest-point nodes + rim gate
    BANDS (constructive global connectivity, reusing zone_field's helpers — spec §5).

    Gates are corpus-wide bands of the zone-contact front (`open_frac` = the mined fraction
    of corpus zone-border tiles left passable): the whole band is protected, so vegetation
    can never wall a border down to a 1-tile corridor — generated borders stay as open as
    real corpus borders. `extra_nodes` are mandatory destinations (gameplay approach tiles —
    every placed object stays reachable); `avoid` tiles (gameplay footprints) are
    impassable, so corridors route AROUND towns/mines instead of through them.

    `entrances` (this zone's `zone_field.plan_entrances` entries) switches the border model
    from corpus-open to ISOLATED: only the planned narrow entrance bands are protected —
    the rest of the front is left plantable, and `sample_zone`'s border bias actively
    densifies it (the map-level isolation redesign). `keep_off` (the caller's 8-connected
    rim: every tile with an 8-neighbour in another zone) further restricts backbone
    ROUTING in that mode — a web corridor pinned to the rim would both hold the ridge open
    and be unsealable by `pp_map.seal_zone_borders`."""
    ts_free = ts - set(avoid)
    if seedt not in ts_free:
        seedt = min(ts_free, key=lambda t: (t[0] - seedt[0]) ** 2 + (t[1] - seedt[1]) ** 2)
    if entrances is not None:
        gate_bands = [(rep, band) for rep, band, _other in entrances]
        # keep the backbone OFF the non-entrance front/rim: a path hugging the border would
        # hold a protected walkable lane exactly where the border bias is trying to grow
        # the isolation ridge. Entrance bands stay in the routing domain (a rep is reached
        # through its own band); fall back to the full zone if a node is only reachable
        # along the front.
        fronts = ZF._zone_fronts(ts, zones, zid)
        front = set().union(*fronts.values()) if fronts else set()
        band_all = set().union(*(b for _r, b in gate_bands)) if gate_bands else set()
        path_ts = ts_free - ((front | set(keep_off)) - band_all)
    else:
        gate_bands = ZF._zone_gate_bands(ts, zones, zid, open_frac=open_frac)
        path_ts = ts_free
    gates = [r for r, _b in gate_bands]
    interior = [t for t in ts_free if edist.get(t, 0) >= 2] or list(ts_free)
    nodes = ZF._farthest_points(ts_free, seedt, spacing, cand=interior)
    for g in list(gates) + [n for n in extra_nodes if n in ts_free]:
        if g in ts_free and g not in nodes:
            nodes.append(g)
    prot = {seedt}
    connected = [seedt]
    remaining = [n for n in nodes if n != seedt]
    while remaining:
        best_r = best_c = None
        bd = 1 << 60
        for r in remaining:
            for c in connected:
                d = (r[0] - c[0]) ** 2 + (r[1] - c[1]) ** 2
                if d < bd:
                    bd, best_r, best_c = d, r, c
        path = (ZF._geodesic_path(best_c, best_r, path_ts)
                or ZF._geodesic_path(best_c, best_r, ts_free)
                or ZF._geodesic_path(best_c, best_r, ts))
        prot.update(path)
        connected.append(best_r)
        remaining.remove(best_r)
    for _r, band in gate_bands:                      # the whole passage stays open
        prot.update(t for t in band if t in ts_free)
    return prot - set(avoid)


ATTRACT = 0.7                   # log-intensity bonus on `attract` tiles (mine surroundings)
BORDER_W = 2.5                  # log-intensity bonus on `border` tiles (zone-front belt):
#                                 e^2.5 ~ 12x Papangelou intensity, so growth concentrates
#                                 along zone borders and reads as a natural ridge. Each side
#                                 only reaches ~70-90% front coverage (Geyer saturation), but
#                                 BOTH zones densify their own side and a crossing needs an
#                                 aligned open pair — measured on the 2-zone probe, every
#                                 surviving crossing is the planned entrance band. The global
#                                 coverage correction (alpha -> corpus veg_blocked_frac) keeps
#                                 TOTAL vegetation corpus-like, so this REDISTRIBUTES mass to
#                                 the border rather than inflating overall density.


def sample_zone(ts, zones, zid, model, seed=1, steps_per_tile=STEPS_PER_TILE, prot=None,
                forbid=frozenset(), attract=frozenset(), border=frozenset()):
    """Birth/death MH over decoration configurations in one zone. Returns
    (objects, blocked_set, prot) with objects = [{x, y, l, template:{animation, mask}}].
    `forbid` tiles (gameplay footprints + approach tiles) admit NO vegetation at all —
    neither an anchor nor any footprint cell (decor must not bury gameplay, per the repo rule).
    `attract` tiles carry a +ATTRACT log-intensity bonus — used for the annulus around MINE
    footprints so sawmills nestle in forest and gem ponds in growth (approaches and the
    protected web stay hard zeros, so attraction never costs reachability).
    `border` tiles carry a +BORDER_W log-intensity bonus — the zone-isolation lever: the
    zone's contact front (minus its planned entrance bands, which sit in `prot` as hard
    zeros) densifies into a vegetation ridge with corpus-correct species/clumping, leaving
    only the planned entrances open."""
    import numpy as np
    import random
    A = len(model["cats"])
    if A == 0:
        return [], set(), set()
    rng = random.Random(seed ^ (zid * 2654435761 & 0xFFFFFFFF))

    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    x0, y0 = min(xs), min(ys)
    W = max(xs) - x0 + 1
    H = max(ys) - y0 + 1
    inz = np.zeros((H, W), dtype=bool)
    for (x, y) in ts:
        inz[y - y0, x - x0] = True
    edist = ZF.edge_dist(ts)
    eb = np.zeros((H, W), dtype=np.int8)
    for (x, y) in ts:
        eb[y - y0, x - x0] = min(edist[(x, y)], EBINS - 1)

    z = zones[zid]
    cx, cy = z["centroid"]
    seedt = min(ts, key=lambda t: (t[0] - int(round(cx))) ** 2 + (t[1] - int(round(cy))) ** 2)
    if prot is None:
        prot = protected_web(ts, zones, zid, edist, seedt)
    protm = np.zeros((H, W), dtype=bool)
    for (x, y) in prot:
        protm[y - y0, x - x0] = True

    L, T = model["L"], model["T"]
    tiles = sorted(ts)
    Nt = len(tiles)
    # birth proposal: category mix from the zone's total first-order mass; `nexp` is also the
    # corpus-expected object count per category for this zone (the intercept-correction target)
    ebin_tiles = np.bincount(eb[inz].ravel(), minlength=EBINS)
    nexp = (L * ebin_tiles[None, :]).sum(axis=1)
    qc = nexp / nexp.sum()
    qc_cum = np.cumsum(qc)

    # ring masks over the interaction window: RM[r] selects the Chebyshev ring r
    RM = np.zeros((RINT + 1, KW, KW))
    for dy in range(-RINT, RINT + 1):
        for dx in range(-RINT, RINT + 1):
            RM[max(abs(dx), abs(dy)), dy + RINT, dx + RINT] = 1.0

    # log-Gaussian Cox modulation: smooth value noise, standardized over the zone, then
    # M = exp(sigma*G - sigma^2/2)  (mean-one lognormal -> carries forest-mass/clearing scale)
    sigma = model.get("sigma", 0.0)
    M = np.ones((H, W))
    if sigma > 0:
        noise = ZE._value_noise(W, H, COX_CELL, rng)
        G = np.array(noise)
        v = G[inz]
        G = (G - v.mean()) / max(v.std(), 1e-6)
        M = np.exp(sigma * G - 0.5 * sigma * sigma)

    att = np.zeros((H, W))                           # additive log-bonus grid
    for (x, y) in attract:                           # mine-surround attraction
        if 0 <= x - x0 < W and 0 <= y - y0 < H:
            att[y - y0, x - x0] = ATTRACT
    for (x, y) in border:                            # zone-front densification
        if 0 <= x - x0 < W and 0 <= y - y0 < H:
            att[y - y0, x - x0] += BORDER_W

    # padded per-category anchor-count grid (padding = no bounds checks on the window)
    C = np.zeros((A, H + 2 * RINT, W + 2 * RINT), dtype=np.int16)
    blkcnt = np.zeros((H, W), dtype=np.int32)        # blocking multiplicity per tile
    objs = []                                        # [x, y, cat, ident_idx]
    ncat = np.zeros(A, dtype=np.int64)               # objects per category (correction target)
    nblocked = 0
    alpha = 0.0                                      # global coverage offset
    alpha_c = np.zeros(A)                            # per-category intercept corrections
    target = model["target"]

    def energy(c, x, y, self_present=False):
        """Geyer-saturated local interaction  sum_co,r theta[c][co][r] * min(n_co(r), SAT)."""
        ly, lx = y - y0, x - x0                      # padded window: [ly, ly+KW) x [lx, lx+KW)
        win = C[:, ly:ly + KW, lx:lx + KW]
        rc = np.einsum("ijk,rjk->ir", win, RM)       # (A cats, RINT+1 rings) neighbour counts
        if self_present:
            rc[c, 0] -= 1                            # death eval: exclude the object itself
        return float((T[c] * np.minimum(rc, SAT)).sum())

    def blocked_cells(c, ii, x, y):
        """Absolute blocking cells of ident ii of category c anchored at (x,y); None = illegal."""
        cells = []
        for (dx, dy) in model["iblk"][c][ii]:
            bx, by = x + dx, y + dy
            lx, ly = bx - x0, by - y0
            if (not (0 <= lx < W and 0 <= ly < H) or not inz[ly, lx] or protm[ly, lx]
                    or (bx, by) in forbid):
                return None
            cells.append((bx, by))
        return cells

    total = steps_per_tile * Nt
    cat_correct_at = {int(total * f) for f in (0.2, 0.35, 0.5)}
    cov_correct_at = {int(total * f) for f in (0.65, 0.8)}
    for step in range(total):
        if step in cat_correct_at:                   # per-category intercepts -> corpus counts
            alpha_c += np.clip(np.log(np.maximum(nexp, 1e-3) / np.maximum(ncat, 0.5)),
                               -0.9, 0.9)
        if step in cov_correct_at and nblocked > 0:  # Boolean coverage-exponent correction
            f_cur = nblocked / Nt
            c_cur = -math.log(max(1e-6, 1.0 - min(f_cur, 0.999)))
            c_tgt = -math.log(max(1e-6, 1.0 - min(target, 0.999)))
            alpha += max(-0.9, min(0.9, math.log(c_tgt / max(c_cur, 1e-6))))
        if rng.random() < 0.5:                       # ---- birth
            x, y = tiles[rng.randrange(Nt)]
            if (x, y) in forbid:
                continue
            r = rng.random()
            c = min(int(np.searchsorted(qc_cum, r)), A - 1)
            ws = model["iweights"][c]
            ii = rng.choices(range(len(ws)), weights=ws, k=1)[0]
            cells = blocked_cells(c, ii, x, y)
            if cells is None:
                continue
            lam_star = (math.exp(alpha + alpha_c[c] + att[y - y0, x - x0] + energy(c, x, y))
                        * L[c, eb[y - y0, x - x0]] * M[y - y0, x - x0])
            acc = lam_star * Nt / ((len(objs) + 1) * qc[c])
            if rng.random() < acc:
                objs.append([x, y, c, ii])
                ncat[c] += 1
                C[c, y - y0 + RINT, x - x0 + RINT] += 1
                for (bx, by) in cells:
                    blkcnt[by - y0, bx - x0] += 1
                    if blkcnt[by - y0, bx - x0] == 1:
                        nblocked += 1
        else:                                        # ---- death
            n = len(objs)
            if n == 0:
                continue
            j = rng.randrange(n)
            x, y, c, ii = objs[j]
            lam_star = (math.exp(alpha + alpha_c[c] + att[y - y0, x - x0]
                                 + energy(c, x, y, self_present=True))
                        * L[c, eb[y - y0, x - x0]] * M[y - y0, x - x0])
            acc = (n * qc[c]) / max(lam_star * Nt, 1e-300)
            if rng.random() < acc:
                objs[j] = objs[-1]
                objs.pop()
                ncat[c] -= 1
                C[c, y - y0 + RINT, x - x0 + RINT] -= 1
                for (dx, dy) in model["iblk"][c][ii]:
                    bx, by = x + dx - x0, y + dy - y0
                    blkcnt[by, bx] -= 1
                    if blkcnt[by, bx] == 0:
                        nblocked -= 1

    out = []
    for (x, y, c, ii) in sorted(objs, key=lambda o: (o[1], o[0])):
        ident = model["idents"][c][ii]
        out.append({"x": x, "y": y, "l": 0,
                    "type": ident.get("type"), "subtype": ident.get("subtype"),
                    "animation": ident["animation"], "mask": ident["mask"],
                    "template": {"animation": ident["animation"], "mask": ident["mask"]}})
    blocked = {(x0 + lx, y0 + ly) for ly, lx in zip(*blkcnt.nonzero())}
    return out, blocked, prot


def m1_experiment(map_name, zid, seed=1):
    """The spec's decisive M1 test: sample vegetation for a REAL corpus zone with NO lattice
    field and compare the EMERGENT run-length histogram + coverage against the corpus."""
    fm = OR.load_faithful(map_name)
    zones, zl, _ = ZE._segment_level(fm["terrain"][0])
    z = zones[zid]
    terrain = ZE.TNAME.get(z["terrain_type"])
    ts = set(z["tiles_set"])
    model = build_model(terrain)
    print(f"model[{terrain}]: {len(model['cats'])} categories, "
          f"target veg_blocked_frac={model['target']:.3f}")

    objs, blocked, prot = sample_zone(ts, zones, zid, model, seed=seed)
    frac = len(blocked) / len(ts)
    print(f"zone {zid} ({terrain}, {len(ts)} tiles): {len(objs)} objects, "
          f"blocked frac gen={frac:.3f} corpus={model['target']:.3f}")

    hg = ZF.run_lengths(ts, ts - blocked)
    sg = sum(hg.values()) or 1
    print("veg-only open run-length  k:  corpus%   gen%")
    for k in range(1, 9):
        cor = 100 * float(model["runs"].get(str(k), 0.0))
        print(f"   {k}: {cor:6.1f}  {100 * hg.get(k, 0) / sg:6.1f}")

    rdir = os.path.join(PS.ROOT, "out", "render", "pp")
    os.makedirs(rdir, exist_ok=True)
    base = f"{map_name.replace(' ', '_')}_z{zid}_pp"
    # corpus veg-only mask for the same zone, side by side
    real_blocked = set()
    for (x, y, _c, anim) in PS._anchors_of_zone(fm, ts):
        for cx2, cy2, blk in OR.mask_cells(ON.mask_of(anim), x, y):
            if blk and (cx2, cy2) in ts:
                real_blocked.add((cx2, cy2))
    ZF.render_rw(ts, ts - real_blocked, os.path.join(rdir, base + "_real.png"))
    ZF.render_rw(ts, ts - blocked, os.path.join(rdir, base + "_gen.png"))
    from PIL import Image
    a = Image.open(os.path.join(rdir, base + "_real.png"))
    b = Image.open(os.path.join(rdir, base + "_gen.png"))
    combo = Image.new("RGB", (a.width + b.width + 12, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0)); combo.paste(b, (a.width + 12, 0))
    combo.save(os.path.join(rdir, base + ".png"))
    print(f"-> {rdir}/{base}.png  (real | gen, white=open red=veg-blocked)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=11)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    m1_experiment(args.map, args.zone, seed=args.seed)


if __name__ == "__main__":
    main()
