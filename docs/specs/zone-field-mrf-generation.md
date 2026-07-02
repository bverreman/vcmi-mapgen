# Zone Field Generation via Gibbs/Markov Random Field

**Status:** Design specification for conditional binary field model of open/blocked tiles.  
**Applies to:** `vcmi_mapgen/zone_field.py` generation pipeline.

---

## Problem Statement

Corpus zones exhibit a structured pattern of open/blocked tiles:
- Open space forms a **thin reticulated web** (96.5% of tiles have distance-to-edge = 1).
- Open runs are **short and unimodal** (mode ≈ 2–3 tiles; rarely >5).
- Zones are **budget-constrained** (mean open fraction 0.36–0.46, varying by terrain).
- **Forest belts ring zone edges** (high probability of blocked near rim).

Current one-pass raster generation (v1 `zone_field.py`) makes each tile independently from left/up neighbors only, producing:
- **Salt-and-pepper fragmentation** (43.4% k=1 runs vs corpus 26.2%).
- **Open fraction drift** (generation 0.33 vs target 0.46).
- **Loss of web coherence** — no time for open regions to coalesce.

**Solution:** Use a **Gibbs Markov Random Field (MRF)** to define the joint distribution $P(X)$ implicitly and sample via iterative sweeps, allowing tiles to be reconsidered in context of their full neighborhood until equilibrium.

---

## Markov Random Field Formalism

### 1. Joint Distribution

The field is a Gibbs distribution over binary variables $X = \{x_t : t \in \text{tiles}\}$, $x_t \in \{0, 1\}$:

$$P(X) = \frac{1}{Z} \exp(-E(X))$$

where:
- $E(X) = \sum_{\text{cliques}} \psi_c(X_c)$ is the energy (sum of clique potentials).
- $Z = \sum_X \exp(-E(X))$ is the partition function (intractable; never computed).

**Key insight:** You do not work with $P(X)$ or $Z$ directly. The MRF is defined through its **local conditionals** (Hammersley–Clifford theorem), which are tractable.

### 2. Local Full Conditional

The **Markov property** states: a tile's conditional on all others equals its conditional on its **neighborhood** $N(t)$:

$$P(x_t \mid X_{\setminus t}) = P(x_t \mid X_{N(t)}) = \sigma(h_t + \textstyle\sum_{s \in N(t)} J_{st} \, x_s)$$

where $\sigma(u) = 1/(1+e^{-u})$ is the logistic sigmoid.

**In words:** the probability tile $t$ is open is a logistic function of:
1. A **bias term** $h_t$ (encodes edge proximity, budget pressure).
2. A **coupling term** $J_{st}$ (interaction strength with each neighbor).

**Neighborhood structure:** For run-length modeling, $N(t)$ must include neighbors far enough to distinguish "run=1" from "run=2" from "run=3". Use a **cross of radius R=3–4**:
- 4-neighbors: $(t \pm 1, 0)$, $(0, t \pm 1)$ (distance 1).
- Extended cross: adds $(t \pm 2, 0)$, $(0, t \pm 2)$ (distance 2), etc.

Equivalently, the "run context" for tile $t$ is the longest open run immediately to its left and above (raster-order causal), captured as:
$$\text{ctx}(t) = (\ell_O(t), u_O(t), e(t))$$
where $\ell_O(t)$ = run of open tiles immediately left, $u_O(t)$ = run above, $e(t)$ = edge distance bin.

### 3. Why Pairwise MRF Is Insufficient

A **nearest-neighbor pairwise MRF** (only 4-neighbors, $J_{st}$ between adjacent tiles) gives **geometric run-length**:
$$P(\text{run}=k) \propto (1-p)^{k-1} p$$
with mode at $k=1$. This matches if isolated tiles are most common, but the **corpus has interior mode** ($k=2$–3).

**Solution:** Either:
1. Use a **higher-order MRF** with cliques of size 3–4 (capture "three tiles in a row"). Formalism is the same; potentials now include triples.
2. Or equivalently, encode the context directly as a **count-based conditional** $P(x_t \mid \ell_O, u_O, e)$ learned from data (the Efros–Leung / n-gram approach).

---

## Learning via Pseudo-Likelihood

### Besag's Pseudo-Likelihood

The joint $P(X)$ is intractable, but the **conditionals** are:

$$\text{PL}(\theta) = \prod_t P(x_t \mid X_{N(t)}; \theta)$$

**Maximum pseudo-likelihood** is consistent and often near-optimal in practice (especially when variables are not too long-range dependent).

### Count-Based Estimation (Non-Parametric)

Instead of fitting a parametric model (e.g., logistic regression), count directly over the corpus:

$$\hat P(\text{open} \mid \text{ctx}) = \frac{\#\{\text{corpus tiles with ctx, } x_t=1\} + \alpha}{\#\{\text{corpus tiles with ctx}\} + 2\alpha}$$

where $\alpha$ is a Laplace smoothing constant (typically 1).

**Vocabulary:** ctx = $(l, u, e)$ ∈ $\{0..R\} \times \{0..R\} \times \{0..E\}$. Over the corpus (e.g., ~388 grass zones), count every tile's context and open/blocked outcome, per terrain. Rare contexts get smoothing; abundant contexts converge to empirical rates.

**Backoff for sparsity:** If a context has zero counts, back off to fewer features (e.g., drop edge bin, or collapse run bins). This is **n-gram language-modeling** logic.

### Implementation

```python
def learn(terrain, nmaps=159):
    """Learn P(open | left_run, up_run, edge_bin) over corpus zones."""
    op, tot = Counter(), Counter()        # open counts, total counts
    fracs = []
    
    for map_name in corpus_maps[:nmaps]:
        fm = load_faithful(map_name)
        zones, zl, _ = segment_level(fm["terrain"][0])
        
        for z in zones.values():
            if terrain_of(z) != terrain or z["area"] < 60:
                continue
            
            ts = set(z["tiles_set"])
            O = open_set(fm, ts)           # real open tiles
            edist = edge_dist(ts)
            openmap = {t: (1 if t in O else 0) for t in ts}
            
            fracs.append(len(O) / len(ts))
            
            for (x, y) in ts_in_raster_order(ts):
                ctx = _feat(x, y, openmap, edist)  # (left_run, up_run, edge_bin)
                tot[ctx] += 1
                op[ctx] += openmap[(x, y)]
    
    return {
        "op": op,
        "tot": tot,
        "budget": mean(fracs),       # target open fraction
        "nzones": len(fracs)
    }

def p_open(ctx, model, bias=0.0):
    o, t = model["op"].get(ctx, 0), model["tot"].get(ctx, 0)
    p = (o + 1) / (t + 2)            # Laplace
    return clamp(p + bias, 0.0, 1.0)
```

---

## Generation via Gibbs Sampling

### Algorithm

**Gibbs sampling** is a Markov chain that converges to the joint distribution $P(X)$:

```python
def generate(fm, zid, zones, model, seed=1, bias=0.0):
    import random
    
    z = zones[zid]
    ts = set(z["tiles_set"])
    edist = edge_dist(ts)
    cx, cy = z["centroid"]
    
    # Nearest in-zone tile to centroid = forced-open SEED
    seedt = closest_to_centroid(ts, (cx, cy))
    
    rng = Random(seed)
    openmap = {}
    
    # Gibbs sweep (multiple passes)
    for sweep in range(NUM_SWEEPS):  # typically 5-10
        for (x, y) in random_order(ts, rng):  # shuffle tile order each sweep
            
            # Skip the seed (clamped = always open)
            if (x, y) == seedt:
                openmap[(x, y)] = 1
                continue
            
            # Resample from local conditional
            ctx = _feat(x, y, openmap, edist)
            p = p_open(ctx, model, bias)
            openmap[(x, y)] = 1 if rng.random() < p else 0
    
    return ts, {t for t in ts if openmap[t] == 1}, seedt
```

### Key Properties

1. **Clamped seed:** The centroid-nearest tile is set $x_t = 1$ (always open) and never resampled. This is a **boundary condition** — all generation flows from this guaranteed-open source.

2. **Multiple sweeps:** Each sweep rescans all tiles. The **first sweep** is autoregressive (left/up only); **subsequent sweeps** see the full neighborhood (including right/down), allowing **corrections** and **coalescence**.

3. **Convergence:** After $\approx 5$ sweeps, the tile field equilibrates (correlations stabilize). This is much faster than Metropolis–Hastings because there is no rejection.

4. **Randomization:** Shuffling tile order each sweep prevents artifacts from raster bias.

### Implementation Note: Symmetric Conditional (max-in-either-direction)

The learned model is **causal** — `P(open | left_run, up_run, edge)` — because counting is done in raster
order (right/down not yet decided). Plugging this one-sided feature into a **shuffled** Gibbs sweep
*increases* fragmentation (measured k=1 jumped from 43% → 55%): a tile resampled before its left
neighbor reads a stale value, so runs never reinforce. **Averaging** the model over the four corner
orientations is also wrong — it halves the run-continuation probability (a tile mid-run on one side but
blocked on the other gets penalized) and reverts to salt-and-pepper.

The correct symmetric conditional takes the run that **continues in either direction** (the corpus
field is direction-symmetric, and this is the original "maximum in either direction" design):
$$h = \max(\ell_O, r_O), \quad v = \max(u_O, d_O), \quad P(\text{open}) = \hat P(\text{open} \mid h, v, e)$$
Max (not average) preserves the sharp ~0.63 continuation probability, so open regions grow and
coalesce. With it, the generated run-length histogram tracks the corpus (grass k1 33%/26%, k3 14%/15%;
dirt k1 49%/49%, k2 26%/26%, k3 14%/14%). See `p_open_sym` in `zone_field.py`.

### Implementation Note: Budget Is Bistable → Use Auto-Bias (Option B, not C)

The max-in-either-direction conditional has **positive feedback** (open begets open), making the field
**bistable**: open fraction rises sharply with `bias` and *collapses* below a threshold (e.g. grass
zone 11: bias −0.10 → frac 0.34, but −0.18 → 0.06). A single hand-tuned `bias` (spec Option C) is
therefore too touchy and does not transfer across zones/terrains. **Use Option B (auto-bias):** bisect
`bias` to hit the target open fraction (`generate_budgeted` in `zone_field.py`). Open fraction is
monotone in bias, so ~14 bisection steps land the budget robustly. `--bias` still overrides for manual
control; omitting it auto-tunes to the learned terrain budget.

### Why Gibbs Fixes Fragmentation

- **v1 (one-pass):** Each tile, once decided, is never reconsidered. Open regions can't merge because neighbors aren't re-evaluated in context of each other → salt-and-pepper.
- **Gibbs (multi-sweep):** An open tile with mostly open neighbors gets reconsidered and reinforced (via the coupling $J>0$); an isolated open tile surrounded by blocks gets reconsidered and may flip. The field self-organizes into coherent masses and thin webs.

---

## Encoding Budget and Edge Constraints

### Edge Bias via Distance-to-Rim

Compute **Chebyshev distance** from each tile to the nearest non-zone tile:

$$e(x,y) = \min\{\text{Chebyshev distance to rim}\}$$

Bin into $E$ levels (e.g., 6 bins: 0, 1, 2, 3, 4, 5+).

**Bias term:** Set $h_t = g(e(x,y))$ negative (especially for $e=0$), so:
$$P(\text{open} \mid e=0) < P(\text{open} \mid e=3)$$
This creates **forest belts** at zone boundaries.

### Budget Constraint via Global Multiplier

The learned model gives a **per-context prior**. To hit the target **budget** $b$ (e.g., 0.40 open fraction):

1. **Option A — Enforce via sampling:** Run Gibbs with Metropolis swaps to maintain a fixed open count. More complex; guaranteed budget.
2. **Option B — Annealing:** Start with $h_t$ at baseline, observe the generated open fraction, then add a global $\Delta h$ uniformly to all tiles, re-run sweeps, and iterate until $\text{frac}(\text{open})$ converges to target. Simpler; converges in a few iterations.
3. **Option C — Bias parameter:** Add a global `bias` to all $P(\text{open} \mid \text{ctx})$ calls. User tunes this at generation time to dial in the budget.

**Recommended:** Option C (bias parameter) — computationally cheap, easy to understand, and user-controllable.

---

## Validation and Verification

### Morphology Metrics

Compare generated and corpus zones on:

1. **Open fraction:** $\frac{\#\text{open}}{\#\text{tiles}}$. Target: within ±0.05 of corpus mean.
2. **Run-length distribution:** Histogram $P(k)$ for $k=1,2,3,...$ over horizontal and vertical runs.
   - Corpus: mode ≈ 2–3, tail to ~8.
   - Generated (v1): mode ≈ 1, rapid decay.
   - **Target (Gibbs):** match corpus histogram shape.
3. **Clump size:** Number of connected-component tiles. Corpus shows a few large (100–500 tile) masses and many singletons.
4. **Distance-to-edge distribution:** Should show concentration at $e=0$ (rim) and thin interior.

### Reachability

- Ensure the centroid-seeded open region is **a single connected component** (4-neighbor reachability), so all gameplay objects in the zone can be reached from the starting position.

### Test Harness

```python
def validate_generation(fm, zid, zones, model, seed, bias):
    ts, O_gen, seedt = generate(fm, zid, zones, model, seed, bias)
    O_real = open_set(fm, ts)
    
    # Morphology
    frac_gen = len(O_gen) / len(ts)
    hr_real = run_lengths(ts, O_real)
    hr_gen = run_lengths(ts, O_gen)
    
    # Reachability
    cc = connected_components_4nb(O_gen)
    seedt_reachable = any(seedt in c for c in cc)
    
    assert frac_gen >= 0.3 and frac_gen <= 0.5, f"budget out of range: {frac_gen}"
    assert seedt_reachable, f"seed tile {seedt} not reachable"
    assert ks_divergence(hr_gen, hr_real) < THRESHOLD, "run-length distribution mismatch"
    
    return frac_gen, hr_gen, cc
```

---

## Implementation Roadmap

1. **Current v1** (`zone_field.py`): One-pass raster, learned conditionals via counting.
   - Diagnosis: fragmentation, no multi-sweep.

2. **Gibbs generation (v2):** ✅ implemented in `zone_field.py`.
   - `NUM_SWEEPS=5` outer loop after one autoregressive seed pass.
   - Shuffle tile order per sweep.
   - Clamp centroid seed.
   - **Symmetric `p_open_sym`** conditional (max-in-either-direction) — see note above; the naive
     causal/averaged feature fragments.
   - **`generate_budgeted`** auto-bias bisection for the budget (the field is bistable) — see note above.
   - Result: run-length histogram tracks corpus; budget hits target ±0.03.

3. **Integration:**
   - `zone_field.generate` becomes the zone-fill step in `zone_engine.py`.
   - Coordinate with `gameplay_hard`, `protected` (visit tiles), reachability carve.

4. **Validation:**
   - Run `zone_field --map "All for One" --zone 11 --bias 0.05` to render red/white real vs gen.
   - Check morphology metrics and render quality.

---

## Related Concepts

- **Ising model:** Pairwise nearest-neighbor MRF; geometric run-length (mode=1).
- **Potts model:** Multi-state generalization (here, binary).
- **Renewal process:** Hazard function $\lambda_O(k)$ for open runs; higher-order Markov equivalent.
- **Efros–Leung texture synthesis:** k×k neighborhood texture-match, count-based; same pseudo-likelihood idea.
- **n-gram language models:** Condition on a fixed-size history; backoff for sparsity.

---

## References

- **Hammersley–Clifford theorem:** MRF ↔ Gibbs distribution equivalence (Besag 1974).
- **Gibbs sampling:** Geman & Geman (1984); Casella & George (1992).
- **Pseudo-likelihood:** Besag (1975).
- **Efros–Leung:** Image quilting via k×k neighborhood statistics (Efros & Leung 2001).
