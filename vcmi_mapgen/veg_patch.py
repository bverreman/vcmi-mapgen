"""Patch-quilt genetic-algorithm vegetation — phase 2 (GAN-free).

Assembles a target zone's DECORATION by QUILTING real corpus patch fragments. The zone's
shape is cut into ``(depth-bin x sweep-bin)`` cells (the same frame ``deco_quilt`` uses); a
genetic algorithm chooses, per cell, WHICH same-terrain corpus patch donates that cell's
decoration so the result matches the corpus decoration distribution (per-depth-bin areal
density + per-terrain category mix), rewards seam coherence (neighbouring cells from the same
patch read as a continuous wall, not patchwork), and obeys the same hard-validity gate the GAN
decoder uses (in-bounds, on-land, never bury a gameplay/passage tile, no blocking overlap).

Decoration identities are reused VERBATIM from the patches — no ontology, no synthesis — so the
look is made of real corpus pieces. Torch-free and deterministic for a fixed seed; the decode is
a pure function of (genome, seed) so the GA's fitness is stable.

Reuses ``zone_engine`` helpers (the patch pool, ``_patch_source``, ``_obj_canon``, the depth/sweep
bins, ``decor_bins``, ``nearest_patch_paths``) and the ``veg_data`` taxonomy for the category
target. Mirrors the GA skeleton of :mod:`veg_decode` (genome / crossover / mutation / tournament /
elitism) but the genome is one patch index per shape cell rather than one category per tile.

Public entry: :func:`decorate_zone`.
"""
from __future__ import annotations

import collections
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import veg_data as VD
import veg_decode as DEC
import zone_engine as ZE

K_CAND = 8            # candidate same-terrain patches per zone (the genome's alphabet)
POP = 40             # GA population
GENS = 30            # GA generations
ELITES = 2           # carried verbatim to the next generation
MUT_RATE = 0.15      # per-cell mutation probability

W_DENS = 1.0         # match the corpus per-depth-bin density profile
W_MIX = 0.5          # match the corpus per-terrain category mix
W_SEAM = 0.4         # reward neighbouring cells sharing a patch (continuous walls)
# openness + dropped-object penalties reuse veg_decode's tuned constants/weights:
MAX_BLOCKED_FRAC = DEC.MAX_BLOCKED_FRAC
W_OPEN = DEC.W_OPEN
W_DROP = DEC.W_DROP

_IDENT_KEYS = ("type", "subtype", "animation", "mask")


# ---------------------------------------------------------------------------
# Candidate corpus patches (the building material)
# ---------------------------------------------------------------------------

def _ident(o):
    return {k: o[k] for k in _IDENT_KEYS}


def _dedup_idents(idents):
    """[(ident, weight)] by exact identity, most common first (deterministic)."""
    c = collections.Counter()
    store = {}
    for d in idents:
        key = (d["type"], d["subtype"], d["animation"], tuple(tuple(r) for r in d["mask"]))
        c[key] += 1
        store[key] = d
    return [(store[k], w) for k, w in c.most_common()]


class _Candidate:
    """One same-terrain corpus patch, pre-binned into (depth,sweep) decoration fragments."""

    def __init__(self, patch: dict):
        src_zone, src_canon, objs = ZE._patch_source(patch)
        ts = src_zone["tiles_set"]
        decor = [o for o in objs if o.get("_purpose") == "DECORATION"]
        # decoration identities grouped by the cell they sit in (shape-intrinsic)
        self.cell_idents = collections.defaultdict(list)
        for o in decor:
            d, s = ZE._obj_canon(o, src_canon, ts)
            self.cell_idents[(ZE._depth_bin(d), ZE._sweep_bin(s))].append(_ident(o))
        # how many of the source's own tiles fall in each cell (for an areal density)
        self.cell_tiles = collections.Counter()
        for t in src_zone["tiles"]:
            d, s = src_canon[t]
            self.cell_tiles[(ZE._depth_bin(d), ZE._sweep_bin(s))] += 1
        # per-depth-bin density profile (resolution-stable), for the corpus target
        self.dens = np.asarray(ZE.decor_bins(decor, src_canon, ts)["dens"], np.float64)
        # cache the dedup'd identity pool per cell (weighted by corpus frequency)
        self._pool = {cell: _dedup_idents(ids) for cell, ids in self.cell_idents.items()}

    def cell_density(self, cell):
        objs = self.cell_idents.get(cell)
        tc = self.cell_tiles.get(cell, 0)
        return (len(objs) / tc) if (objs and tc) else 0.0

    def cell_pool(self, cell):
        return self._pool.get(cell, [])


def gather_candidates(pool, terrain_type, area, k=K_CAND):
    """The k same-terrain library patches nearest in area, each pre-binned for quilting."""
    out = []
    for path in ZE.nearest_patch_paths(pool, terrain_type, area, k):
        try:
            cand = _Candidate(json.load(open(path)))
        except (OSError, ValueError, KeyError):
            continue
        if cand.cell_idents:                 # skip patches with no usable decoration
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Target-zone quilt context (shared by decode + fitness)
# ---------------------------------------------------------------------------

class _Quilt:
    """The target zone cut into (depth,sweep) cells + the corpus targets to match."""

    def __init__(self, zone, canon, cands, gameplay_hard, passage_tiles):
        self.zone = zone
        self.canon = canon
        self.tiles_set = zone["tiles_set"]
        self.terrain_type = zone["terrain_type"]
        self.cands = cands
        self.hard = set(gameplay_hard) | set(passage_tiles or ())
        self.land_area = max(len(self.tiles_set), 1)

        # target shape cells (= genome positions, in a stable order)
        cell_tiles = collections.defaultdict(list)
        for t in sorted(self.tiles_set):
            d, s = canon[t]
            cell_tiles[(ZE._depth_bin(d), ZE._sweep_bin(s))].append(t)
        self.cell_tiles = cell_tiles
        self.cells = sorted(cell_tiles)
        self.n = len(self.cells)
        self.cellpos = {cell: i for i, cell in enumerate(self.cells)}

        # target #tiles per depth bin (denominator of the placed density profile)
        self.tile_bins = np.zeros(ZE.DEPTH_BINS, np.float64)
        for (dk, _sk), tiles in cell_tiles.items():
            self.tile_bins[dk] += len(tiles)

        # corpus target: mean per-depth-bin density over the candidate patches
        if cands:
            self.target_dens = np.mean([c.dens for c in cands], axis=0)
        else:
            self.target_dens = np.zeros(ZE.DEPTH_BINS, np.float64)

        # corpus target: per-terrain category share (frequency, normalized)
        row = VD.terrain_category_counts()[self.terrain_type]
        tot = row.sum()
        self.target_cat = (row / tot) if tot else np.zeros(VD.num_categories())


def _decode(q: _Quilt, genome, seed, level=0):
    """Genome → (objects, blocked_count, placed_density_profile, dropped). Pure in (genome,seed):
    identities + tile order come from a cell/patch-keyed RNG, so fitness is stable."""
    used, blocked, objs = set(), set(), []
    dropped = 0
    obj_bins = np.zeros(ZE.DEPTH_BINS, np.float64)
    for ci, cell in enumerate(q.cells):
        p = genome[ci]
        if p < 0:
            continue
        cand = q.cands[p]
        dens = cand.cell_density(cell)
        if dens <= 0:
            continue
        tgt_tiles = q.cell_tiles[cell]
        n = int(round(dens * len(tgt_tiles)))
        if n <= 0:
            continue
        pool = cand.cell_pool(cell)
        if not pool:
            continue
        dk = cell[0]
        idents = [d for d, _w in pool]
        weights = [max(w, 1) for _d, w in pool]
        rng = random.Random(f"{seed}:{cell[0]}:{cell[1]}:{p}")
        order = list(tgt_tiles)
        rng.shuffle(order)
        placed_here = 0
        for t in order:
            if placed_here >= n:
                break
            if t in used:
                continue
            ident = rng.choices(idents, weights=weights, k=1)[0]
            cells_fp = list(OR.mask_cells(ident["mask"], t[0], t[1]))
            ok = True
            for tx, ty, blk in cells_fp:
                if not blk:
                    continue
                if (tx, ty) not in q.tiles_set or (tx, ty) in q.hard or (tx, ty) in blocked:
                    ok = False
                    break
            if not ok:
                dropped += 1
                continue
            for tx, ty, blk in cells_fp:
                if blk:
                    blocked.add((tx, ty))
            used.add(t)
            objs.append({**ident, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})
            obj_bins[dk] += 1
            placed_here += 1
    placed_dens = np.divide(obj_bins, q.tile_bins,
                            out=np.zeros_like(obj_bins), where=q.tile_bins > 0)
    return objs, len(blocked), placed_dens, dropped


def _fitness(q: _Quilt, genome, seed, level=0):
    objs, blocked_count, placed_dens, dropped = _decode(q, genome, seed, level)
    # 1. per-depth-bin density profile match (the corpus 'look': rim wall, sparse core)
    dens_pen = W_DENS * float(np.abs(placed_dens - q.target_dens).sum())
    # 2. category-mix match vs the corpus per-terrain shares (real identities keep this small)
    if W_MIX > 0 and objs:
        placed_cat = np.zeros(VD.num_categories())
        for o in objs:
            placed_cat[VD.category_of(o)] += 1
        placed_cat /= placed_cat.sum()
        mix_pen = W_MIX * float(np.abs(placed_cat - q.target_cat).sum())
    else:
        mix_pen = 0.0
    # 3. seam coherence: neighbouring cells donated by the SAME patch read as a wall
    seam, pairs = 0, 0
    for (dk, sk), i in q.cellpos.items():
        for nb in ((dk, sk + 1), (dk + 1, sk)):
            j = q.cellpos.get(nb)
            if j is not None:
                pairs += 1
                if genome[i] >= 0 and genome[i] == genome[j]:
                    seam += 1
    seam_bonus = W_SEAM * (seam / pairs if pairs else 0.0)
    # 4. hard penalties (reuse veg_decode weights): keep the zone traversable, discourage drops
    open_pen = W_OPEN * max(0.0, blocked_count / q.land_area - MAX_BLOCKED_FRAC)
    drop_pen = W_DROP * dropped
    fit = seam_bonus - dens_pen - mix_pen - open_pen - drop_pen
    return fit, objs


# ---------------------------------------------------------------------------
# GA (mirrors veg_decode._evolve; genome = one patch index per shape cell)
# ---------------------------------------------------------------------------

def _init_genome(q: _Quilt, rng: random.Random):
    """Each cell starts from a random candidate (occasionally empty) for diversity."""
    K = len(q.cands)
    return [(-1 if rng.random() < 0.1 else rng.randrange(K)) for _ in range(q.n)]


def _crossover(a, b, rng):
    return [a[i] if rng.random() < 0.5 else b[i] for i in range(len(a))]


def _mutate(g, K, rng, rate=MUT_RATE):
    for i in range(len(g)):
        if rng.random() < rate:
            g[i] = -1 if rng.random() < 0.1 else rng.randrange(K)
    return g


def _tournament(fit, rng, k=3):
    best = rng.randrange(len(fit))
    for _ in range(k - 1):
        c = rng.randrange(len(fit))
        if fit[c] > fit[best]:
            best = c
    return best


def _evolve(q: _Quilt, seed, level=0, pop=POP, gens=GENS, elites=ELITES):
    rng = random.Random(seed)
    K = len(q.cands)
    if q.n == 0 or K == 0:
        return []
    population = [_init_genome(q, rng) for _ in range(pop)]
    best_objs, best_fit = [], -1e30
    for _ in range(gens):
        scored = [_fitness(q, g, seed, level) for g in population]
        fit = np.array([s[0] for s in scored], np.float64)
        order = np.argsort(-fit)
        if fit[order[0]] > best_fit:
            best_fit = float(fit[order[0]])
            best_objs = scored[int(order[0])][1]
        nxt = [list(population[order[i]]) for i in range(min(elites, pop))]
        while len(nxt) < pop:
            a = population[_tournament(fit, rng)]
            b = population[_tournament(fit, rng)]
            nxt.append(_mutate(_crossover(a, b, rng), K, rng))
        population = nxt
    return best_objs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def decorate_zone(z, canon, pool, passage_tiles, gameplay_hard, *, seed=0, level=0, k=K_CAND):
    """Decorate one target zone by GA-quilting real same-terrain corpus patches.

    ``z`` is a segmentation zone dict (``tiles_set``/``area``/``terrain_type``); ``canon`` its
    shape-intrinsic ``{(x,y):(depth,sweep)}`` map; ``pool`` the loaded patch library
    (``zone_engine.load_patch_pool``); ``passage_tiles``/``gameplay_hard`` are protected from any
    blocking footprint. Returns concrete DECORATION object dicts (real corpus identities).
    """
    cands = gather_candidates(pool, z["terrain_type"], z["area"], k=k)
    if not cands:
        return []
    q = _Quilt(z, canon, cands, gameplay_hard, passage_tiles)
    if q.n == 0:
        return []
    return _evolve(q, seed, level)
