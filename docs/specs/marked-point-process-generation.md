# Map Generation as a Layered Marked Point Process

**Status:** Design specification — supersedes the lattice-MRF object model of
`zone-field-mrf-generation.md` (whose field survives here as a *covariate surface*, not as
the generative model). The **as-built reference** documenting the implemented solution in
full detail (every layer, constant and decision) is
[pp-map-generator-solution.md](pp-map-generator-solution.md).
**Applies to:** `vcmi_mapgen/` generation pipeline (`markov_terrain.py`, `mapgraph.py`,
`zone_field.py`, `veg_*.py`, `zone_engine.py`).
**Constraints honored:** `ontology.py` is the single source of truth for object identity /
mask / category; the corpus informs *spatial statistics only*; everything is seeded and
deterministic; stdlib + numpy + Pillow only; the same-shape identity replay path is untouched.

---

## 1. Problem Statement and Diagnosis

### 1.1 Why the lattice MRF fights the data

The Gibbs-field spec models the open/blocked mask as a binary lattice field
`X = {x_t ∈ {0,1}}`. But in the corpus the blocked mask is not a latent field — it is the
**union of the blocking footprints of placed objects**:

```
blocked(t)  =  1[ t ∈ ⋃_i F(m_i) + x_i ]
```

where object *i* has anchor `x_i` and identity (mark) `m_i`, and `F(m)` is the footprint
mask that `ontology.mask_of` returns. A 3-tile-wide pine cluster *forces* a blocked run of
length ≥ 3; a 15-tile mountain forces a blob. The statistics the MRF struggled to match —
interior run-length mode at k = 2–3 instead of the geometric mode at k = 1, clump-size
distribution, the thin reticulated web — are **emergent from footprint geometry plus object
placement**, not properties of a tile-level field. Hence the accumulated patches: the
max-run symmetric conditional, the bistable budget requiring bisection, the constructive
backbone carve. Each patch compensates for modeling the *shadow* (the rasterized mask)
instead of the *object process* that casts it.

### 1.2 Why the Markov terrain is too sparse

`markov_terrain.py` learns `P(t[x,y] | left, up, upleft)` — a causal 3-tile context. This
reproduces local border texture but has **no mechanism for large-scale coherence**: patch
size under a short-range causal chain decays geometrically, while corpus terrain regions are
huge (hundreds to thousands of tiles), few per map, and arranged by *design* (a grass
heartland, a swamp barrier, a lava enemy region). Measured symptom: markov-generated maps
segment into many small zones (most below the `area ≥ 60` threshold), whereas real maps
segment into a handful of large ones. Local texture models cannot fix this — macro topology
must be **planned first** (§4, building on `mapgraph.py`), with the Markov chain demoted to
boundary texturing.

### 1.3 Overlap is a feature, not a violation

VCMI decoration sprites **may overlap each other** (dense forests are stacked trees; the
editor and the corpus both do this). Only gameplay objects must not be buried, and
required paths must stay passable. Therefore the object model must NOT impose a hard-core
(no-overlap) pair potential on vegetation. The correct classical model for
"random shapes thrown down with overlap, union defines coverage" is the **Boolean model /
germ–grain model**, with the **dead-leaves model** supplying the occlusion (draw-order)
semantics. Both are standard objects in stochastic geometry (Matheron 1975; Chiu, Stoyan,
Kendall & Mecke 2013).

### 1.4 The layered decomposition (user-confirmed)

```
P(map) = P(G)                        macro zone graph + terrain          (L0)
       · P(C | G)                    covariate fields, backbone, gates   (L1, constructive)
       · P(V | C, G)                 vegetation / obstacle point process (L2)
       · P(P | V, C, G)              gameplay point process              (L3)
       · P(R | P, V, C, G)           resources/artifacts/monsters        (L4, cluster process)
```

Each layer is fit and validated independently against corpus statistics, then conditioned
on the realized layers below it. Global properties that local potentials cannot express
(connectivity, traversability, zone adjacency) are enforced **constructively** in L0/L1 and
**verified/repaired** after L2–L4 (the existing G2 reachability-repair gate).

---

## 2. Mathematical Foundations

This section is self-contained: every formula used later is stated here, with the standard
reference and a worked example on repo-realistic numbers.

### 2.1 Point processes and intensity

A **point process** on a window `W ⊂ ℤ²` (a zone's tile set) is a random finite set of
points `X = {x₁, …, x_n}` (object anchors), `n` itself random. Its first-moment description
is the **intensity function** `λ(u)`: the expected number of points per unit area at
location `u`. For the **inhomogeneous Poisson process**, points are independent given λ:

```
P(N(B) = k) = e^{−Λ(B)} Λ(B)^k / k! ,   Λ(B) = Σ_{u∈B} λ(u)
```

and the number of points in disjoint regions are independent. The Poisson process is the
"no interaction" null model — everything else is measured as a departure from it.
*Reference: Møller & Waagepetersen (2004), ch. 2; Baddeley, Rubak & Turner (2015), ch. 5.*

**Example.** A 40×40 grass zone (1600 tiles) with fitted tree-anchor intensity
λ = 0.05 anchors/tile carries on average 80 tree anchors, Poisson-distributed.

### 2.2 Marked point processes

A **marked point process** attaches to each point a mark `m_i ∈ M`. Here
`M` = the ontology's object identities (per terrain, per category:
`decor_pool`, `veg_categories`, `category_of`), and each mark determines a footprint
`F(m) ⊂ ℤ²` (from `mask_of`, anchored bottom-right per `obj_resolve.mask_cells`) and a
blocking/visitable classification (`is_blocking`). A configuration is
`ω = {(x₁,m₁), …, (x_n,m_n)}`.

The mark distribution may depend on location: `λ(u, m) = λ(u) · p(m | u)`, e.g. pines
where interior depth is high, scrub near the rim.
*Reference: Chiu et al. (2013), ch. 4; Baddeley et al. (2015), ch. 14.*

### 2.3 Gibbs (interacting) point processes and the Papangelou intensity

Interactions are introduced by giving the process a density with respect to the unit
Poisson process:

```
f(ω)  ∝  exp( Σ_i  V₁(x_i, m_i)  +  Σ_{i<j} V₂((x_i,m_i), (x_j,m_j)) )
```

- `V₁` — the **first-order (intensity/covariate) term**: log-linear in per-tile covariates
  (§2.4).
- `V₂` — the **pairwise interaction**: attraction (clustering) or inhibition between marks.

The workhorse quantity is the **Papangelou conditional intensity**

```
λ*( (u,m) ; ω )  =  f(ω ∪ {(u,m)}) / f(ω)
                 =  exp( V₁(u,m) + Σ_{(x,m')∈ω} V₂((u,m),(x,m')) )
```

— "how much the configuration ω welcomes a new object of mark m at u". It is computable
without the (intractable) normalizing constant, and it is the single object both the
fitting method (§2.6) and the sampler (§2.7) consume. This is the point-process analogue of
the lattice MRF's local conditional `P(x_t | X_{N(t)})` — the Hammersley–Clifford role is
played by the Georgii–Nguyen–Zessin equation.
*Reference: Baddeley et al. (2015), ch. 13; van Lieshout (2000).*

**Multi-type pairwise example (piecewise-constant potentials).** With distance rings
`r ∈ {0, 1, 2–3, 4–6}` (Chebyshev) and mark categories `a, b`:

```
V₂ = θ[a,b,r]   when the pair's categories are (a,b) and their distance falls in ring r
```

θ[tree,tree,1] = +0.9 says "a tree adjacent to a tree is e^0.9 ≈ 2.5× more welcome" —
forest clumping. θ[mine,mine,r≤6] = −2.0 says mines repel. **No hard core anywhere for
decoration** (per §1.3): overlap is legal and its actual frequency is *learned* through
the r = 0 ring (θ[a,b,0] captures how often footprints stack in the corpus).

### 2.4 First-order term: log-linear intensity on covariates

```
V₁(u, m)  =  α_m  +  Σ_k β_{c(m),k} · z_k(u)
```

where `c(m)` is the ontology category of mark m, and `z_k(u)` are **per-tile covariate
fields**, all already computable in the repo:

| covariate | source |
|---|---|
| edge distance (Chebyshev to zone rim), binned | `zone_field.edge_dist` |
| interior depth (normalized BFS) | `terrain_segment.compute_static_features[:,:,20]` |
| distance to backbone / road corridor | L1 backbone (§5) |
| distance to nearest gate | `zone_field._zone_gates` |
| terrain type | segmentation |
| zone role / value tier | `mapgraph` roles (L0) |

This replaces the hand-tuned `EDGE_W`/`edge_penalty` forest belt: "vegetation intensity
rises near the rim" becomes a **fitted coefficient** β_{veg,edge} < 0 on edge distance.

### 2.5 Summary statistics: how to *measure* pattern (the missing extraction step)

These are the mathematical descriptors of "what the corpus pattern is" — computable
numbers per category pair, per terrain, that either confirm structure or don't.

**Ripley's K and the pair correlation function g.** For a stationary process of intensity
λ, `λK(r)` = expected number of *further* points within distance r of a typical point.
Estimator on a zone with tile set `ts` (minus-sampling edge correction — count only pairs
whose first point is ≥ r from the rim, using the already-computed `edge_dist`):

```
K̂(r) = |ts| / (n(n−1)) · Σ_{i≠j, edist(x_i) ≥ r} 1[ d(x_i, x_j) ≤ r ]
```

The **pair correlation function** is its derivative form; on the tile lattice use the ring
version directly:

```
ĝ(r) = ( #ordered pairs at distance exactly r / #tile pairs at distance r ) / λ̂²
```

Interpretation: `g(r) = 1` ⇒ Poisson (no structure) at range r; `g(r) > 1` ⇒ clustering;
`g(r) < 1` ⇒ inhibition. The **multitype** versions `K_{ab}(r)`, `g_{ab}(r)` restrict the
first point to category a and the counted points to category b — this is how
"monsters sit near mines", "trees clump", "obelisks avoid each other" become measurements.
*Reference: Ripley (1977); Baddeley et al. (2015), ch. 7; Illian, Penttinen, Stoyan &
Stoyan (2008).*

**Worked example.** Grass zones corpus-wide: n = 4200 tree anchors on 90 000 zone tiles ⇒
λ̂ = 0.047. Suppose at r = 1 (8 neighboring tiles per tile) we count 2 950 ordered
same-category pairs over 690 000 valid ordered tile pairs at distance 1:
ĝ(1) = (2950/690000) / 0.047² ≈ 1.94 ⇒ trees are twice as likely to be adjacent to a tree
as Poisson predicts — strong clumping, and the initial value for θ[tree,tree,1] ≈ log 1.94.

**Mark-connection and nearest-neighbor statistics.** For the relational layer (L4):
the distribution of the *vector offset* from each resource to its nearest monster, and the
fraction of resources with a monster within radius r (a mark-connection function). These
capture "the guard stands in front of the gold pile" — a parent–child geometry no field
model can express.

**Blocked-mask statistics as validation (not model).** Open fraction, run-length
histogram, clump sizes, distance-to-edge profile (all already implemented in
`zone_field.py`) are retained — but demoted to **goodness-of-fit checks** computed on the
*realized union mask* after sampling. They are now emergent.

### 2.6 Fitting: logistic composite likelihood (pseudo-likelihood on the anchor lattice)

The Gibbs density's normalizing constant is intractable; **Besag pseudo-likelihood**
(Besag 1975) sidesteps it, and its modern, numerically clean form for point processes is
the **logistic composite likelihood** (Baddeley, Coeurjolly, Rubak & Waagepetersen 2014):

> Fit θ = (α, β, θ₂) by logistic regression where **presence points** are the corpus
> anchors and **absence (dummy) points** are sampled lattice tiles, and each point's
> feature vector is exactly the terms of `log λ*`.

Concretely, for category c on terrain τ:

1. Presence set `D` = every corpus anchor (u, m) with `category_of(m) = c` in every
   τ-zone (identity read from the faithful maps *only to classify via the ontology* —
   spatial statistics, not identity reuse, per the repo rules).
2. Dummy set `Q` = per zone, a deterministic stratified sample of tiles with dummy
   intensity ρ (e.g. every tile, or every 2nd tile; seeded).
3. For each point in `D ∪ Q`, compute features
   `φ(u) = ( 1, z₁(u), …, z_K(u), n_{c'}(u, r) for each category c' and ring r )`
   where `n_{c'}(u,r)` = number of corpus objects of category c' at ring-distance r
   from u (excluding the point itself when u ∈ D).
4. Maximize the logistic likelihood

```
ℓ(θ) = Σ_{u∈D} log σ(θ·φ(u) − log ρ)  +  Σ_{u∈Q} log(1 − σ(θ·φ(u) − log ρ))
```

This is consistent for Gibbs processes and reduces to standard, dependency-free math:
implement with a ~40-line Newton/IRLS loop in numpy (no sklearn). With
piecewise-constant potentials and binned covariates it *further* reduces to **counting
ratios with Laplace smoothing** — the same estimator style as `zone_field.learn`, so the
non-parametric fallback is:

```
θ̂[bin] = log( (#presence in bin + α) / (#dummy in bin + α) · ρ⁻¹ )
```

**Worked example (one coefficient).** Grass trees, feature "edge_bin = 0 (rim tile)":
presence rate at rim = 620/9 000 tiles, presence rate elsewhere = 3 580/81 000 tiles.
θ̂ ≈ log( (620/9000) / (3580/81000) ) = log(0.0689/0.0442) ≈ +0.44 — trees are e^0.44 ≈
1.6× denser on the rim (the forest belt), now *measured* rather than imposed by `EDGE_W`.

**Backoff for sparsity** as in the n-gram spec: drop rings, then collapse categories to
the ontology's coarser buckets, Laplace-smooth throughout.

### 2.7 Sampling: birth–death–move Metropolis–Hastings

Sample the fitted Gibbs process with the standard reversible-jump chain
(Geyer & Møller 1994; Møller & Waagepetersen 2004, ch. 7). One step, given current ω:

- **Birth** (prob ⅓): propose (u, m), u uniform on allowed tiles, m from the ontology
  pool weighted by the fitted mark distribution. Accept with probability
  `min(1, λ*((u,m); ω) · |W| · q_m⁻¹ / (n(ω)+1))`.
- **Death** (prob ⅓): pick an existing object uniformly; accept with
  `min(1, n(ω) / (λ*((x,m); ω∖{(x,m)}) · |W| · q_m⁻¹))`.
- **Move** (prob ⅓): pick an object, jitter its anchor within Chebyshev radius 2; accept
  with `min(1, λ*(new)/λ*(old))`.

Hard constraints (§5.3) enter as `λ* = 0` (auto-reject): burying a gameplay object,
blocking a backbone/gate tile, footprint leaving the zone. **Vegetation–vegetation overlap
is allowed** — it is priced by the fitted θ[·,·,0], not forbidden.

Determinism: fixed `random.Random(seed ^ zid·2654435761)`, fixed step count
(e.g. 200·|W| proposals), no wall-clock or unseeded state. Initialization: empty
configuration, or the current greedy packer's output (which is the zero-temperature
degenerate case of this chain) for faster burn-in.

**Budget calibration in closed form (Boolean-model coverage).** For a Poisson layer the
blocked fraction has an exact formula (Chiu et al. 2013, §3.1): with anchor intensity λ
and mean *blocking* footprint size E|F|,

```
P(tile blocked) = 1 − exp(−λ · E|F|)
```

**Example:** λ = 0.05, mean blocking footprint 4.2 tiles ⇒ blocked frac = 1 − e^{−0.21} ≈
0.19. Inverting: to hit the corpus grass budget (blocked ≈ 0.58 ⇒ open 0.42) with
E|F| = 4.2, first-order intensity must satisfy λ = −ln(0.42)/4.2 ≈ 0.21. This gives the
**initial** global intensity; interactions perturb it, so keep the existing bisection on a
global offset to α_m as the exact-budget outer loop (reusing `generate_budgeted`'s proven
monotone-bisection pattern, now on a quantity that is provably monotone and not bistable —
adding to α only ever adds expected objects).

### 2.8 Cluster processes for relational structure (guards, treasure piles)

A **Neyman–Scott / Cox cluster process** (Neyman & Scott 1958): **parent** points (here:
mines, artifacts, resource piles, dwellings from L3) each spawn a Poisson-distributed
number of **children** (guards, satellite loot) displaced by a learned offset kernel:

```
children of parent p  ~  Poisson(μ_{c(p)}) ,   offset  Δ ~ κ_{c(p)}(Δ)
```

- `μ_{c}` = mean number of guards/satellites per parent category c (a corpus count).
- `κ_c(Δ)` = empirical 2-D histogram of child-minus-parent offsets over the corpus,
  Laplace-smoothed (this captures "guard stands *in front of* — between the object and the
  open web").

Fitting is direct counting (assign each monster to its nearest guarded object within a
radius, from the corpus; ambiguous assignments resolved by nearest distance). Sampling is
generative and trivially deterministic. Snap-to-legal-tile and visitability repair reuse
the stretch-placement machinery (rigid gameplay = one tile, no overlap, reachable).
*Reference: Møller & Waagepetersen (2004), ch. 5; Illian et al. (2008), §6.3.*

### 2.9 Dead-leaves / occlusion order for rendering-faithful overlap

When vegetation overlaps, draw order decides pixels. The **dead-leaves model**
(Matheron 1968; Lee, Mumford & Huang 2001) formalizes: objects arrive sequentially, later
ones partially occlude earlier ones. Corpus maps carry an implicit order (the object list
order + the renderer's y-sort). Adopt: sample the configuration (order-free), then emit
objects sorted by (y, x, size) exactly as `render_editor` and the editor expect — the
realized blocked mask is order-independent (a union), so gameplay is unaffected; only
pixels are.

### 2.10 Goodness of fit: minimum contrast and Monte-Carlo envelopes

Two standard tools close the loop (Diggle 2013; Baddeley et al. 2015, ch. 10):

- **Minimum contrast:** if logistic fitting proves awkward for some layer, choose θ to
  minimize `Σ_r ( ĝ_corpus(r) − ḡ_model(r; θ) )²` where ḡ_model is estimated from a few
  seeded simulations — a derivative-free 1–3 parameter search. This is also the fallback
  estimator if pseudo-likelihood is biased by strong clustering.
- **Envelope tests:** simulate 39 seeded realizations, compute each summary statistic
  (g_{ab}, run-lengths, open fraction, clump sizes, offset histograms); the corpus curve
  must lie inside the simulation envelope (a 5% Monte-Carlo test). This is the per-layer
  acceptance gate in §8.

---

## 3. Architecture Overview

```
L0  Macro topology     mapgraph zone graph  →  warped-Voronoi realization
                       →  boundary texturing (Markov chain demoted to edges)
                       →  zones, terrain, roles, water/rock seams
L1  Covariates         per-zone: edge_dist, interior depth, gates, spanning
    (constructive)     backbone (kept from zone_field), distance transforms
L2  Vegetation PP      marked Gibbs process, overlap allowed, fitted on corpus;
                       union mask must not cover backbone/gates; budget via
                       Boolean-model init + α-bisection
L3  Gameplay PP        sparse inhomogeneous PP per category (mine, dwelling,
                       town satellite, teleport, …), hard placement rules,
                       G2 reachability repair
L4  Relational layer   Neyman–Scott: parents = L3 objects + treasure parents,
                       children = guards / satellite loot with offset kernels
V   Validation         summary-statistic envelopes per layer + mapeval yardstick
```

Module plan (new files small and single-purpose, mirroring existing style):

| module | role |
|---|---|
| `pp_stats.py` | corpus extraction: anchors by category/terrain, covariates, K/g/offset estimators (§2.5) |
| `pp_fit.py` | logistic composite likelihood + counting fallback (§2.6), model files under `data/pp/` |
| `pp_sample.py` | birth–death–move sampler + hard constraints + budget bisection (§2.7) |
| `pp_cluster.py` | L4 parent–child fitting and generation (§2.8) |
| `macro_topo.py` (or extend `mapgraph.py`) | L0 refinement (§4) |
| `test_pp.py` | determinism, estimator sanity on synthetic Poisson/Strauss data, envelope harness |

---

## 4. L0 — Macro Topology Refinement

**Goal:** generated macro terrain whose *zone-size distribution, zone count, terrain
adjacency mix, and water/rock fraction* match the corpus — fixing "markov terrain is
sparse vs. the real corpus".

### 4.1 Mine macro statistics (extend `mapgraph.py` stage 1)

Per map (159 maps, per level), record:

1. zone count and **zone-area distribution** (log-scale histogram; corpus zones are
   heavy-tailed — a few 1000+-tile regions, few mid-size, few valid small),
2. terrain **adjacency matrix** A[τ₁][τ₂] = boundary-tile counts between terrain pairs
   (grass|dirt is common, snow|lava is not),
3. water fraction and water topology class (landlocked / lake / sea-rim / archipelago),
4. rock-seam fraction (underground) and per-terrain area shares by map size (S/M/L/XL),
5. zone **compactness** (area / bbox area) and boundary roughness (perimeter² / area —
   corpus boundaries are rough, Voronoi cells are too straight).

Deliverable: `data/pp/macro_stats.json` (regenerable; cached like `graph_stats.json`).

### 4.2 Plan: graph-first, then geometry, then texture

Keep `mapgraph.py`'s three stages and refine each:

1. **Graph sampling** (exists): draw zone count from the corpus distribution *conditioned
   on map size*; assign terrains so the implied adjacency mix matches A (greedy assignment
   scored by A, or Metropolis swaps on node terrains with energy
   `Σ_edges −log A[τ_i][τ_j]` — a tiny Potts model on the *zone graph*, where it is cheap
   and convergent, unlike on the tile lattice).
2. **Geometric realization** (exists: warped Voronoi): replace the single-seed cell per
   zone with **capacity-constrained growth** — multi-source BFS from zone seeds where each
   zone stops expanding once it reaches its sampled target area. This directly imposes the
   corpus zone-size distribution (the current Voronoi gives areas determined by seed
   spacing, which is why generated zones under-shoot corpus sizes). Noise the BFS costs
   with seeded per-tile jitter to get organic (non-straight) boundaries.
3. **Boundary texturing** (new home for the Markov chain): run `markov_terrain.gibbs`
   sweeps **restricted to tiles within distance ≤ 2 of a zone boundary, with zone labels
   clamped elsewhere** — local texture where it is good, zero chance to fragment the
   interior. Rivers/roads stay downstream.

### 4.3 Acceptance (per §2.10 envelopes)

Generated maps (39 seeds) vs. corpus: zone-count and zone-area KS distance within
envelope; adjacency-mix χ² within envelope; water fraction within ±0.05; **and** the
segmentation of a generated map must yield ≥ 80% of its area in zones with area ≥ 60
(the current markov pipeline's concrete failure).

---

## 5. L1 — Covariate Fields and Constructive Skeleton (kept)

Unchanged in spirit from `zone_field.py`, but re-scoped: these are **inputs** to L2–L4,
not a sampled field.

1. Per zone: `edge_dist` (exists), interior depth (exists), gates via `_zone_gates`
   (exists), spanning backbone via `_farthest_points` + Prim + geodesic corridors
   (exists in `generate_spanning` stage 1 — extract it into a reusable function).
2. New distance transforms: distance-to-backbone, distance-to-gate (multi-source BFS,
   ~10 lines each, same pattern as `edge_dist`).
3. The backbone + gate tiles form the **protected open set** `PROT`: L2's hard constraint
   surface. This keeps the global-connectivity guarantee constructive (proved necessary in
   `generate_grow`'s docstring) while everything probabilistic moves to the PP layers.

Covariates are binned (edge: 6 bins as today; depth: 6 quantile bins; backbone-dist:
0,1,2,3,4,5+) and cached per zone.

---

## 6. L2 — Vegetation / Obstacle Marked Point Process

### 6.1 Extraction (`pp_stats.py`)

For each terrain τ and each corpus zone of τ (area ≥ 60):

1. Enumerate decoration objects via the faithful map, classify with
   `ontology.category_of`; record (anchor, category, footprint size, blocking cells).
   Identity is used only through the ontology's own categories — the corpus contributes
   *positions and counts*.
2. Compute covariates at each anchor; compute `n_{c'}(u, r)` ring counts.
3. Estimate per-category λ̂, mark mix per covariate bin, and multitype ĝ_{ab}(r) for
   r ∈ {0,1,2,3,4–6} with minus-sampling correction (§2.5). The **r = 0 / overlap ring**
   is measured like any other — this is where "vegetation can overlap" enters the data.

### 6.2 Fitting (`pp_fit.py`)

Logistic composite likelihood (§2.6) per terrain with features:
category intercepts α_c, covariate bins (edge, depth, backbone-dist), ring counts by
category pair. Fallback: pure counting ratios per bin (§2.6, last formula). Persist to
`data/pp/veg_<terrain>.json` with the fit's provenance (nzones, npoints, date).

Sanity harness (in `test_pp.py`): simulate a known inhomogeneous Poisson process and a
known clustered process on synthetic zones; the fitter must recover intensities within
±10% and the sign/magnitude order of the interaction — this validates the estimator before
it ever touches the corpus.

### 6.3 Sampling (`pp_sample.py`)

Birth–death–move chain (§2.7) per zone with:

- mark pool = `decor_pool(terrain, exclude_types=EXCLUDE_DECOR_TYPES)` (ontology only),
- hard zeros: any blocking cell of the footprint on `PROT`, on a gate, outside the zone;
  (later, once L3 exists and vegetation is resampled around it: on a gameplay footprint
  or its visitable-approach tiles),
- **no vegetation–vegetation hard core** — stacking priced by θ[·,·,0],
- budget outer loop: Boolean-model initial λ (§2.7 example) then ≤ 8 bisection steps on a
  global α offset targeting the corpus open fraction of the terrain, where
  open = zone ∖ (union of blocking cells); measured on the realized mask,
- after sampling: connectivity check `reach_from_seed`; any open tile cut off from the
  backbone is *fine* (holes in the web are corpus-real) but every gate and every PROT tile
  must be mutually reachable by construction — assert it.

Ordering for emission: dead-leaves sort (§2.9).

### 6.4 Acceptance

39-seed envelopes vs. corpus, per terrain: open fraction ±0.05; run-length histogram
(reusing `run_lengths`) within envelope; clump-size distribution within envelope;
per-category ĝ_{ab}(r) of *generated* maps within envelope of corpus curves; and the
render (`render_markov_sprites` successor) reads as forest belts + clumps + web, judged
against real-map crops side by side.

The decisive experiment (run first, cheap): fit intensity + interactions on grass, sample
with **no lattice field at all**, and compare the emergent run-length histogram to corpus.
If it matches within the envelope, §1.1's diagnosis is confirmed and `zone_field`'s
sampler is retired from the generation path (its statistics functions live on as metrics).

---

## 7. L3 — Gameplay Point Process, L4 — Relational Layer

### 7.1 L3 gameplay objects

Same machinery, different regime: counts are small (a handful of mines/dwellings per
zone), so interactions are mostly *inhibition and rules*, and covariates dominate.

1. Extraction: anchors by ontology purpose/category (mine, dwelling, shrine, teleport,
   town-adjacent, …) with covariates + zone role from L0 (`role_seeds`).
2. Fit: intensity per (category, zone-role, covariate bins); pair inhibition
   θ[c,c,r ≤ 6] (mines repel mines); cross terms to gates (mines sit deep, passages
   shallow — the depth covariate carries this).
3. Sample: birth–death–move with hard rules — one-tile snap-to-free semantics from the
   stretch pipeline: gameplay never overlaps anything, its `visitableFrom` approach tile
   must be open and reachable from the backbone (reject otherwise), min-distance rules
   (e.g. town spacing) as hard zeros.
4. Repair/verify: the existing G2 reachability-repair pass, then re-run vegetation *local*
   deaths where a gameplay object landed (delete any decoration whose blocking cell now
   buries a gameplay approach — a targeted death sweep, deterministic).

### 7.2 L4 resources, artifacts, monsters (Neyman–Scott, §2.8)

1. Extraction: parent–child assignment (each monster/loose resource → nearest L3-class
   object within radius 8, else "free-standing" parent class); offset histograms κ_c(Δ);
   children counts μ_c; guard-strength vs. zone value tier (from `mapgraph` roles) as a
   conditional table.
2. Generation: for each realized L3 parent, draw children counts and offsets; snap to
   legal open tiles (nearest free, deterministic spiral); free-standing treasure as its
   own inhomogeneous PP (covariate: depth — treasure sits deep, corpus-fitted).
3. Guards block their approach by game semantics, not the mask — no L2 interaction
   needed; only legality checks.

Acceptance: offset-histogram envelope (the "guard in front" shape must reproduce);
guarded-fraction per category ±10%; mapeval yardstick score not below the corpus band.

---

## 8. Implementation Roadmap

Ordered so every phase lands something runnable and testable; each phase's gate is the
envelope test of its section.

| phase | deliverable | gate |
|---|---|---|
| **M0** ✅ | `pp_stats.py`: anchors, covariates, ĝ estimators + `--report` CLI printing per-terrain λ̂ and ĝ_{ab}(r) tables (cached `data/pp/veg_<terrain>.json`) | estimator tests in `test_pp.py` (pair-denominator vs brute force) pass |
| **M1** ✅ | decisive experiment (`pp_sample --map … --zone …`): counting fit + sampler on a real grass zone, no lattice field | PASSED — emergent run-lengths track corpus (33/24/16 vs gen 38/27/16), coverage 0.56 vs 0.53 |
| **M2** ✅ | `pp_sample.py`: birth–death MH with **two-scale model** (see note below) + PROT web + per-category intercept & Boolean-coverage corrections; counting fit used, logistic CL (`pp_fit.py`) still open | determinism + PROT-hard-zero tests pass |
| **M3** ✅ | `macro_topo.py`: macro stats mining, capacity-constrained Dijkstra growth, boundary-band Markov texturing | §4.3 gate in `test_pp.py`: big-zone share ≥ 0.7 (measured 0.99) |
| **M4** ✅ | `pp_map.py`: end-to-end L0→L2 sprite render (successor of `render_markov_sprites`) → `out/render/pp/ppmap_s<seed>.png` | visual review on seeds 3/7/11; per-zone coverage within ±0.12 of corpus |
| **M5** ◐ | L3+L4 implemented: `pp_gameplay.py` (towns/mines/dwellings/shrines — corpus per-purpose densities, anchors SAMPLED from the counting-fitted log-linear intensity over edge-dist × gate-dist bins, GAP separation, approach tiles as mandatory web nodes) + `pp_pickup.py` (resources/artifacts/guards over the post-vegetation open field: unguarded scatter on routes via the same intensity fit + an OPENNESS covariate, guarded caches in deep low-openness pockets with the GUARD on the pocket mouth; corpus guardedness ≈ 0.54 for resource piles) | legality + determinism tests in `test_pp.py` pass; REMAINING: mapeval ≥ corpus band floor. **H3-convention layer** (user-mandated, 2026-07): TOWN/DWELLING/RESOURCE/ARTIFACT/GUARD favour the editor's RANDOM classes (real sprites: `avwmon1..7`, `avarnd1..4`, `avtrndm0`, `avcranx0`, `avrcgen0..7`); guard level ∝ guarded value (cache value, mine rarity `MINE_GUARD_LVL`, zone size at gates); mines guarded with corpus prob ≈ 0.64; every zone gate guarded with prob 0.65, duplicate guards on a shared corridor deduped map-level; coastal shipyards; water bodies populated from the corpus water pass (flotsam/sea chests/buoys/boats/whirlpools/wrecks/sea guards) |
| **M6** ✅ | full pipeline into `zone_engine generate --layout pp` + `.vmap` export (`pp_map.export_vmap` via the proven `faithful.to_vmap` writer: full ontology identities on every object, `visitableFrom` on visitables, player slots wired to towns) + map-level **G2 gate** (`pp_map.g2_repair`: every approach/pickup reachable across zones treating pickups as removable; unreachable targets get a least-vegetation corridor carved — vegetation only, never gameplay) | round-trip + G2 + export tests pass; seeds 3/7/11 generate **G2 clean** (zero carving needed — the constructive web already guarantees it); editor open check pending manual verification |

**Implementation note (M1/M2 finding).** The raw fitted potentials θ = log ĝ are positive at
*all* ranges (corpus zones mix dense forest masses with clearings), and a purely attractive
pairwise Gibbs process is **explosive** — the first sampler collapsed into one runaway clump.
The stable, corpus-faithful form splits the two scales: **local** rings r ≤ 2 with
background-normalized θ = log(ĝ(r)/ĝ(4)) under **Geyer saturation** (neighbour counts capped),
plus a **log-Gaussian Cox modulation** whose σ is fitted from coarse-cell overdispersion
(Fisher-index inversion, `pp_stats.cox_sigma`). Per-category intercept corrections during the
chain match corpus per-category counts; a global offset then lands the Boolean coverage budget.
Also fixed en route: ontology classes 177/199/206–211 were opaque (`CLASS_*`) and partly
mis-excluded — renamed (LAKE_2, TREES_2, *_HILLS, SUBTERRANEAN_ROCKS, SWAMP_FOLIAGE, verified
against sprites) and TREES_2 (6 k corpus swamp/rough trees) re-admitted to the decor pools.

Every new CLI runs via `uv run python -m vcmi_mapgen.<module>`; every corpus scan caches
under `data/pp/` (committed) or `out/` (transient) consistent with current conventions;
all samplers take `--seed` and are bit-reproducible for a fixed seed (test in `test_pp.py`).

**Non-goals / invariants:** the extract → `rebuild --identity --verify` replay path is
untouched (`IDENTITY OK` stays bit-exact); no new dependencies; ontology remains the sole
identity source; no unseeded randomness.

---

## 9. References

- J. Besag (1975), *Statistical analysis of non-lattice data*, The Statistician 24 —
  pseudo-likelihood.
- A. Baddeley, J.-F. Coeurjolly, E. Rubak, R. Waagepetersen (2014), *Logistic regression
  for spatial Gibbs point processes*, Biometrika 101(2) — the fitting method of §2.6.
- A. Baddeley, E. Rubak, R. Turner (2015), *Spatial Point Patterns: Methodology and
  Applications with R* (the `spatstat` book) — K, g, multitype statistics, Gibbs models,
  validation; the single most useful practical reference for this spec.
- J. Møller, R. Waagepetersen (2004), *Statistical Inference and Simulation for Spatial
  Point Processes* — birth–death–move MCMC (ch. 7), cluster/Cox processes (ch. 5).
- B. D. Ripley (1977), *Modelling spatial patterns*, JRSS-B 39 — K function.
- J. Illian, A. Penttinen, H. Stoyan, D. Stoyan (2008), *Statistical Analysis and
  Modelling of Spatial Point Patterns* — mark correlation, worked ecology examples that
  mirror the vegetation layer.
- S. N. Chiu, D. Stoyan, W. S. Kendall, J. Mecke (2013), *Stochastic Geometry and its
  Applications*, 3rd ed. — Boolean model coverage formula (§3.1), germ–grain models,
  dead-leaves.
- G. Matheron (1975), *Random Sets and Integral Geometry* — foundations of the Boolean
  model; Matheron (1968) — dead-leaves ("feuilles mortes").
- A. B. Lee, D. Mumford, J. Huang (2001), *Occlusion models for natural images*, IJCV 41 —
  dead-leaves as an image model.
- J. Neyman, E. L. Scott (1958), *Statistical approach to problems of cosmology*, JRSS-B
  20 — cluster processes.
- C. J. Geyer, J. Møller (1994), *Simulation procedures and likelihood inference for
  spatial point processes*, Scand. J. Statist. 21 — the birth–death MH sampler.
- M. N. M. van Lieshout (2000), *Markov Point Processes and Their Applications* —
  Papangelou conditional intensity, GNZ equation.
- P. J. Diggle (2013), *Statistical Analysis of Spatial and Spatio-Temporal Point
  Patterns*, 3rd ed. — minimum contrast, envelope tests.
- D. Geman, S. Geman (1984); J. Besag (1974) — retained from the prior spec for the
  lattice-MRF lineage that L0's boundary texturing still uses.
