"""Genetic-algorithm decoder for the vegetation GAN — phase 1.

Turns the generator's continuous per-tile category-occupancy **field** (C×H×W of
probabilities, from :func:`veg_gan.sample_field`) into concrete, footprint-valid
DECORATION objects. The GA searches placements scored by

    fitness = learned_realism(occupancy)  +  hard-validity / aesthetic penalties

where ``learned_realism`` is the discriminator (passed in as a batched ``score_fn`` so this
module stays torch-free); when no score_fn is given it falls back to a numpy proxy = how well
the placement agrees with the generator's field.

Two entry points share one genome / operators / fitness:
  * :func:`decode_zone`        — best layout → concrete objects (inference).
  * :func:`evolve_negatives`   — top-K layouts → anchor-occupancy grids, fed to D as hard
                                 negatives during the training coevolution loop.

PHASE-2 SEAM: :func:`_fitness` cleanly separates the *learned score* (``score_fn``) from the
*hard-validity gate* (:func:`_decode_genome`). A future gameplay proposer reuses this exact
skeleton by swapping the field source and adding gameplay validity terms.
"""
from __future__ import annotations

import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import veg_data as VD

CAND_FLOOR = 0.02     # ignore a category at a tile only if its field prob is below this
TOPK_CAT = 3          # at most this many candidate categories per tile (bound branching)
DENSITY_TARGET = 0.18  # target occupied-land fraction (the corpus mean ≈ 0.20) — the GAN's
                       # softmax spreads mass over 38 classes so absolute probs are small;
                       # we place at the model's most-preferred tiles UP TO this density
                       # rather than thresholding the (tiny) marginal probabilities.
MAX_BLOCKED_FRAC = 0.6  # openness guard: keep this fraction of land traversable
W_RIM = 0.3           # reward dense blocking near the rim (low depth)
W_OPEN = 1.0          # openness-budget penalty weight
W_DROP = 0.02         # per-dropped-gene penalty (discourage over-placement)
W_COUNT = 1.0         # penalty for deviating from the target anchor count


# ---------------------------------------------------------------------------
# Candidate structure (shared by both entry points)
# ---------------------------------------------------------------------------

class _Cands:
    """Per-tile candidate categories derived from the field, plus region context."""

    def __init__(self, field, land_mask, gameplay_hard, depth, terrain_grid,
                 density=DENSITY_TARGET):
        C, H, W = field.shape
        self.C, self.H, self.W = C, H, W
        self.field = field
        self.land = land_mask
        self.hard = gameplay_hard
        self.depth = depth
        self.terr = terrain_grid
        self.land_area = max(int(land_mask.sum()), 1)
        tiles, allowed, probs, score = [], [], [], []
        for y in range(H):
            for x in range(W):
                if not land_mask[y, x] or gameplay_hard[y, x]:
                    continue
                col = field[:, y, x]
                cs = [c for c in range(C) if col[c] >= CAND_FLOOR]
                if not cs:
                    continue
                cs.sort(key=lambda c: -col[c])
                cs = cs[:TOPK_CAT]
                tiles.append((x, y))
                allowed.append(cs)
                probs.append([float(col[c]) for c in cs])
                score.append(float(col[cs[0]]))   # the tile's "wants vegetation" strength
        self.tiles = tiles
        self.allowed = allowed
        self.probs = probs
        self.score = score
        self.n = len(tiles)
        # how many anchors to place: the corpus density over the candidate area.
        self.target_n = int(round(density * len(tiles)))


# ---------------------------------------------------------------------------
# Genome: gene[i] ∈ {-1 (empty)} ∪ allowed[i];  aligned to cands.tiles
# ---------------------------------------------------------------------------

def _init_genome(cn: _Cands, rng: random.Random) -> list[int]:
    """Place a decoration on the target_n most-preferred tiles (weighted random so the
    population is diverse), category sampled from the field at each chosen tile."""
    g = [-1] * cn.n
    if cn.n == 0 or cn.target_n <= 0:
        return g
    # Efraimidis–Spirakis weighted sampling without replacement (key = u^(1/w)).
    keys = sorted(range(cn.n),
                  key=lambda i: rng.random() ** (1.0 / max(cn.score[i], 1e-6)),
                  reverse=True)
    for i in keys[:min(cn.target_n, cn.n)]:
        g[i] = rng.choices(cn.allowed[i], weights=cn.probs[i], k=1)[0]
    return g


def _crossover(a, b, rng):
    return [a[i] if rng.random() < 0.5 else b[i] for i in range(len(a))]


def _mutate(g, cn: _Cands, rng, rate=0.04):
    for i in range(cn.n):
        if rng.random() < rate:
            if g[i] != -1 and rng.random() < 0.3:
                g[i] = -1
            else:
                g[i] = rng.choices(cn.allowed[i], weights=cn.probs[i], k=1)[0]
    return g


def _ident_rng(seed, x, y, c):
    return random.Random(f"{seed}:{x}:{y}:{c}")


def _decode_genome(g, cn: _Cands, seed, ox=0, oy=0, level=0):
    """Genome → (objects, anchor-occupancy [C,H,W], n_dropped, blocked_count).

    Hard-validity gate: a placed decoration's blocking footprint must stay in-bounds, on
    land, and never cover a gameplay-hard tile or another decoration's blocking cell.
    Identity per (tile, category) is deterministic (seeded) so fitness is stable.
    """
    occ = np.zeros((cn.C, cn.H, cn.W), dtype=np.float32)
    blocked = np.zeros((cn.H, cn.W), dtype=bool)
    objs = []
    dropped = 0
    for i, c in enumerate(g):
        if c == -1:
            continue
        x, y = cn.tiles[i]
        terr = int(cn.terr[y, x])
        ident = VD.decode_identity(c, terr, _ident_rng(seed, x, y, c))
        if ident is None:
            dropped += 1
            continue
        cells = list(OR.mask_cells(ident["mask"], x, y))
        ok = True
        for tx, ty, blk in cells:
            if not blk:
                continue
            if not (0 <= tx < cn.W and 0 <= ty < cn.H) or not cn.land[ty, tx] \
                    or cn.hard[ty, tx] or blocked[ty, tx]:
                ok = False
                break
        if not ok:
            dropped += 1
            continue
        for tx, ty, blk in cells:
            if blk:
                blocked[ty, tx] = True
        occ[c, y, x] = 1.0
        objs.append({**ident, "x": x + ox, "y": y + oy, "l": level, "_purpose": "DECORATION"})
    return objs, occ, dropped, int(blocked.sum())


def _penalties(cn: _Cands, occ, dropped, blocked_count):
    blocked_frac = blocked_count / cn.land_area
    open_pen = W_OPEN * max(0.0, blocked_frac - MAX_BLOCKED_FRAC)
    drop_pen = W_DROP * dropped
    # keep the placed-anchor count near the corpus-density target
    count_pen = W_COUNT * abs(int(occ.sum()) - cn.target_n) / max(cn.target_n, 1)
    # rim coherence: reward anchored objects sitting at low depth (near the rim wall)
    anchored = occ.sum(axis=0) > 0
    if anchored.any():
        rim_bonus = W_RIM * float((1.0 - cn.depth)[anchored].mean())
    else:
        rim_bonus = 0.0
    return rim_bonus - open_pen - drop_pen - count_pen


def _proxy_score(cn: _Cands, occ_batch):
    """D-free realism proxy: agreement between placement and the generator's field."""
    # sum of field probability at placed anchors, normalized by land area
    return (occ_batch * cn.field[None]).sum(axis=(1, 2, 3)) / cn.land_area


def _fitness(cn: _Cands, genomes, seed, score_fn, ox, oy, level):
    decoded = [_decode_genome(g, cn, seed, ox, oy, level) for g in genomes]
    occ_batch = np.stack([d[1] for d in decoded])
    if score_fn is not None:
        realism = np.asarray(score_fn(occ_batch), dtype=np.float32)
    else:
        realism = _proxy_score(cn, occ_batch)
    fit = realism + np.array([_penalties(cn, d[1], d[2], d[3]) for d in decoded], np.float32)
    return fit, decoded


def _evolve(cn: _Cands, seed, score_fn, pop=40, gens=30, elites=2, ox=0, oy=0, level=0):
    rng = random.Random(seed)
    if cn.n == 0:
        return [], None
    population = [_init_genome(cn, rng) for _ in range(pop)]
    best_g, best_decoded, best_fit = None, None, -1e30
    for _ in range(gens):
        fit, decoded = _fitness(cn, population, seed, score_fn, ox, oy, level)
        order = np.argsort(-fit)
        if fit[order[0]] > best_fit:
            best_fit = float(fit[order[0]])
            best_g = list(population[order[0]])
            best_decoded = decoded[order[0]]
        nxt = [list(population[order[i]]) for i in range(min(elites, pop))]
        while len(nxt) < pop:
            a = population[_tournament(fit, rng)]
            b = population[_tournament(fit, rng)]
            child = _mutate(_crossover(a, b, rng), cn, rng)
            nxt.append(child)
        population = nxt
    return best_g, best_decoded


def _tournament(fit, rng, k=3):
    best = rng.randrange(len(fit))
    for _ in range(k - 1):
        c = rng.randrange(len(fit))
        if fit[c] > fit[best]:
            best = c
    return best


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def decode_zone(field, land_mask, gameplay_hard, depth, terrain_grid, *,
                seed=0, score_fn=None, level=0, ox=0, oy=0,
                pop=40, gens=30) -> list[dict]:
    """Best GA layout for one region → concrete DECORATION object dicts (global coords
    via ox/oy). Arrays are all [.,H,W]; ``field`` is [C,H,W]."""
    cn = _Cands(np.asarray(field, np.float32), np.asarray(land_mask, bool),
                np.asarray(gameplay_hard, bool), np.asarray(depth, np.float32),
                np.asarray(terrain_grid))
    _g, decoded = _evolve(cn, seed, score_fn, pop=pop, gens=gens, ox=ox, oy=oy, level=level)
    return decoded[0] if decoded else []


def evolve_negatives(field, land_mask, depth, terrain_grid, score_fn, *,
                     seed=0, pop=40, gens=10, topk=4) -> list[np.ndarray]:
    """Top-K discrete, valid layouts as anchor-occupancy grids [C,H,W] — hard negatives
    for the discriminator during the training coevolution loop. ``gameplay_hard`` is empty
    here (training windows carry no planned gameplay)."""
    field = np.asarray(field, np.float32)
    gh = np.zeros(field.shape[1:], dtype=bool)
    cn = _Cands(field, np.asarray(land_mask, bool), gh,
                np.asarray(depth, np.float32), np.asarray(terrain_grid))
    if cn.n == 0:
        return [np.zeros_like(field) for _ in range(topk)]
    rng = random.Random(seed)
    population = [_init_genome(cn, rng) for _ in range(pop)]
    for _ in range(gens):
        fit, decoded = _fitness(cn, population, seed, score_fn, 0, 0, 0)
        order = np.argsort(-fit)
        nxt = [list(population[order[i]]) for i in range(min(2, pop))]
        while len(nxt) < pop:
            a = population[_tournament(fit, rng)]
            b = population[_tournament(fit, rng)]
            nxt.append(_mutate(_crossover(a, b, rng), cn, rng))
        population = nxt
    fit, decoded = _fitness(cn, population, seed, score_fn, 0, 0, 0)
    order = np.argsort(-fit)
    return [decoded[order[i]][1] for i in range(min(topk, len(order)))]
