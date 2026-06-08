# Rule-Based Placement with Learned Ranking — Design Spec

## Goal

Given a HoMM3 map terrain, produce a `.vmap` such that:

- `B(terrain, seed=0)` → **exact original map** (object-for-object identical)
- `B(terrain, seed=N)` → **valid visual sibling** (different but same quality)

The rules are **learned from the 159-map corpus**, not hand-coded. The seed is a
pure variability knob — the deterministic core at seed=0 must reproduce the
original exactly, which serves as the correctness criterion for the learned rules.

---

## Why the Previous Approach Failed

The statistical sampling approach (zone_realize.py) failed on three counts:

1. **Zone shapes wrong** — Voronoi BFS from town positions gives geometric blobs;
   real zones follow terrain-type boundaries (connected snow = snow zone, etc.).
2. **Decoration placement wrong** — random Gaussian clusters have no knowledge of
   terrain transitions; real maps place forests/rocks *along* terrain-type edges to
   visually wall off zones.
3. **No invertibility** — no seed value could reproduce the original; every seed
   produces a random sample from a learned distribution, not a reconstruction.

---

## Algorithm Overview

```
terrain (.h3m or .json)
    │
    ▼
[1] Terrain Segmentation
    flood-fill by terrain type → zones
    zones: {id, terrain_type, area, centroid, boundary_tiles, adjacent_zones}
    │
    ▼
[2] Feature Extraction (per tile, per zone, per partially-placed state)
    static: terrain_type, dist_to_zone_boundary, dist_to_zone_centroid,
            dist_to_water_edge, is_chokepoint, local_openness
    dynamic: dist_to_nearest_town, dist_to_nearest_mine, dist_to_nearest_guard,
             occupation status
    │
    ▼
[3] Sequential Placement  (fixed purpose order, one object at a time)
    for each purpose P in ORDER:
        for each object of purpose P to place in zone Z:
            score all candidate tiles using score_P(tile_features, zone_features)
            seed=0 → pick argmax  (deterministic, reproduces original)
            seed=N → pick from top-K using rng(N)  (controlled variation)
            mark tile occupied, update dynamic features
    │
    ▼
[4] to_fm → repair_reachability → to_vmap  (reuse existing machinery)
```

---

## Step 1 — Terrain Segmentation

Segment the terrain grid into **natural zones** by terrain type:

```python
zones = flood_fill_by_terrain_type(terrain_grid)
# each zone: contiguous region of same terrain code (0–9)
# zones separated by water/rock are always different zones
# water and rock are NOT zones (they are barriers)
```

Zone attributes extracted at this stage:
- `terrain_type`: the terrain code of all tiles in the zone
- `area`: number of tiles
- `centroid`: (mean_x, mean_y) of all tiles
- `boundary_tiles`: tiles that have ≥1 4-neighbor in a different zone or water/rock
- `chokepoints`: tiles where the passage between two zones narrows to ≤3 tiles wide
  (detected by computing BFS width of the boundary crossing)
- `adjacent_zones`: list of zone ids sharing a boundary

**Note:** This is the inverse of the current approach. Here, zones are derived from
terrain first; towns are placed inside zones afterwards. Zone shapes are correct by
construction.

---

## Step 2 — Feature Extraction

For each tile `(x, y)` in zone `Z`, compute a feature vector used by the scoring
functions. Features are divided into static (terrain geometry only) and dynamic
(depend on what has already been placed).

### Static features (computed once per terrain)

| Feature | Description |
|---|---|
| `terrain_type` | one-hot(10) of tile's terrain code |
| `zone_terrain_type` | one-hot(10) of the zone's terrain code |
| `dist_boundary` | BFS distance to nearest zone boundary tile (normalized by sqrt(area)) |
| `dist_centroid` | Euclidean distance to zone centroid (normalized) |
| `dist_water` | BFS distance to nearest water tile (normalized) |
| `dist_edge` | distance to nearest map edge (normalized) |
| `is_chokepoint` | 1 if this tile is on or adjacent to a detected chokepoint |
| `local_openness` | fraction of tiles within radius 3 that are passable |
| `zone_area_log` | log(area) of the containing zone |
| `n_adjacent_zones` | number of zones this zone borders |

### Dynamic features (recomputed after each placed object)

| Feature | Description |
|---|---|
| `dist_nearest_town` | BFS dist to nearest placed town entrance |
| `dist_nearest_mine` | BFS dist to nearest placed mine |
| `dist_nearest_guard` | BFS dist to nearest placed guard |
| `dist_nearest_boundary_guard` | BFS dist to nearest guard on a chokepoint |
| `is_occupied` | tile already taken by a placed object |

Dynamic features are what encode the **relational structure** (mine near town, guard
at chokepoint, loot behind guard) — the knowledge the statistical approach lost.

---

## Step 3 — Sequential Placement

### Placement order

Objects are placed in a fixed order that matches real dependency structure:

```
TOWN → MINE → DWELLING → GUARD → BANK → QUEST_GATE →
REWARD_PICKUP → RESOURCE_PILE → STAT_PERMANENT → SPELL_SKILL →
BONUS_TEMP → MANA → TRANSPORT → TERRAIN_MODIFIER → INFO →
DECORATION (last, non-blocking)
```

Towns first (they anchor zone identity and define the dynamic distance features
everything else depends on). Mines second (economy anchors). Guards after mines
(they guard mines and zone entrances). Loot after guards (loot is behind guards).
Decorations last (non-blocking, cosmetic).

### Scoring function

For each purpose `P`, a scoring function:

```
score_P(tile_features, zone_features, dynamic_features) → float
```

Implemented as a **small linear model** (or shallow 2-layer MLP if linear underfits):
- Input: concatenation of static + dynamic feature vectors (~25 features)
- Output: scalar score (higher = better tile for this purpose)
- Separate model per purpose (or shared trunk + purpose embedding)

### Seed mechanism

```python
def pick_tile(candidates, scores, purpose, seed, rng):
    if seed == 0:
        return candidates[argmax(scores)]   # deterministic: exact original
    else:
        K = 10                              # top-K for variation
        top_k = argtopk(scores, K)
        return candidates[rng.choice(top_k)]
```

- `seed=0` always picks the highest-scored tile → must reproduce original
- `seed=N` picks uniformly from top-10 using `rng(N)` → valid sibling
- `K=10` is a hyperparameter; smaller = closer to original, larger = more variation

### Count determination

How many objects of each purpose per zone?

- At `seed=0`: use the **exact counts from the original map** (parsed from .h3m)
- At `seed=N`: sample counts from the corpus distribution for this (zone_type, zone_area)
  band, using `rng(N)`

For new terrains (not in corpus), always use corpus distribution.

---

## Step 4 — Decoration Placement (special case)

Decorations are placed last and follow terrain-transition geometry:

```python
# Boost cluster center weight 4× for tiles on a terrain-type boundary
# (tile has ≥1 neighbor with different terrain_type)
center_weight[tile] *= 4.0 if is_terrain_transition(tile) else 1.0
```

This ensures forest clusters accumulate along zone edges (where terrain changes),
reproducing the visual zone-isolation walls seen in real maps.

Cluster sigma = `diag/18` (large patches, as validated against corpus).

---

## Learning the Scoring Function

### Training data

For every map in the corpus, for every object in that map:

- **Positive example**: the actual tile where the object was placed
  - features: static + dynamic (dynamic computed by replaying placement in ORDER)
- **Negative examples**: all other tiles in the same zone
  - same dynamic context (same partial placement state)

### Loss function

**Pairwise ranking loss** (BPR / margin ranking):

```
loss = sum over (pos, neg) pairs: max(0, margin - (score(pos) - score(neg)))
margin = 1.0
```

This directly optimizes: score(original tile) > score(any other tile in zone).

At convergence on the training set: `argmax(score) = original tile` → seed=0 reproduces the original.

### Training procedure

1. For each map, replay placement in ORDER to build the correct dynamic context
   at the moment each real object was placed.
2. For each placed object, collect (positive tile, all negative tiles in zone).
3. Fit the linear model by SGD on the pairwise loss.
4. Validate: for training maps, check that argmax = original tile for each object
   (rank=0 accuracy). Target: >80% rank-0 accuracy on training set.

### Output artifacts

```
out/rule_model.pkl    # scoring function weights per purpose
out/rule_calib.json   # count distribution per (zone_terrain_type, area_band)
```

---

## Validation Criterion

The primary test: render `B(terrain, seed=0)` side-by-side with the original using
`render_editor.py`. They must be **visually identical** (same object positions, same
decoration clusters).

Secondary: `B(terrain, seed=N)` for N=1..5 must pass `traverse.py` reachability
and look like a valid HoMM3 map (reasonable zone structure, no object clutter).

The rank-0 accuracy on the training set (>80%) is a proxy for correctness but the
render comparison is the ground truth, per the proxy-trap lesson.

---

## Implementation Plan

### New files

| File | Purpose |
|---|---|
| `src/terrain_segment.py` | Flood-fill segmentation, chokepoint detection, zone features |
| `src/rule_learn.py` | Feature extraction, training loop, saves `out/rule_model.pkl` |
| `src/rule_gen.py` | Generation pipeline: segment → sequential place → to_vmap |

### Reused files (unchanged)

| File | Role |
|---|---|
| `src/energy_place.py` | `to_fm`, `repair_reachability`, `sample_tiles_deco` |
| `src/faithful.py` | `to_vmap` |
| `src/render_editor.py` | Validation renders |
| `src/traverse.py` | Reachability check |
| `src/h3m.py` | Parse original .h3m to get exact positions for training |

### Run commands

```bash
# 1. Train
uv run python src/rule_learn.py
# → prints rank-0 accuracy per purpose, saves out/rule_model.pkl

# 2. Generate and validate (seed=0 should reproduce original)
uv run python src/rule_gen.py --terrain "All for One" --seed 0 --emit-vmap
uv run python src/render_editor.py out/RuleGen-All_for_One-s0.vmap --compare "All for One"

# 3. Check variation
uv run python src/rule_gen.py --terrain "All for One" --seed 1 --emit-vmap
uv run python src/rule_gen.py --terrain "All for One" --seed 2 --emit-vmap
```

---

## Open Questions

1. **Linear vs MLP**: Start with linear (interpretable, fast). Upgrade to 2-layer MLP
   if rank-0 accuracy < 60% on training set.

2. **Tied scores**: If two tiles score identically (e.g., symmetric map), argmax is
   arbitrary. Break ties by (x, y) lexicographic order for determinism.

3. **New terrains** (not in the 159): The scoring function generalizes via features.
   Count distribution falls back to the corpus mean for the matching terrain type +
   area band. This is untested until a new terrain is tried.

4. **Underground level**: Spec covers surface (level 0) only. Underground follows the
   same pipeline but is out of scope for the first implementation.

5. **Terrain segmentation granularity**: A large grass map might produce one giant
   grass zone. May need to subdivide large zones by natural barriers (rivers, isolated
   water bodies) using the river/road layer from the terrain data.
