# The PP map generator — full solution reference

**Status:** implemented and working end-to-end (2026-07); v5 change set applied 2026-07-03.
**Scope:** this document describes the complete marked-point-process ("pp") map generator —
every layer, every constant, and the reasoning behind each design decision. The companion
design/roadmap spec is [marked-point-process-generation.md](marked-point-process-generation.md);
this file is the *as-built* reference. The v5 change set (gate bands, map-level mine
economy, area-scaled counts, land banks, loot scatter, variety audit) is recorded in
detail — what changed, how, and the measured results — in
[pp-generator-v5-changes.md](pp-generator-v5-changes.md).

**One-command demo:**

```bash
uv run python -m vcmi_mapgen.pp_map --seed 7 --size 72 --vmap --players 4 --teams 2v2
uv run python -m vcmi_mapgen.pp_map --seed 7 --size 144 --vmap --players 4 --teams 2v2
uv run python -m vcmi_mapgen.pp_map --seed 40 --batch 9 --players 2 --install   # 9 maps → editor
uv run python -m vcmi_mapgen.zone_engine generate --layout pp --seed 7          # same pipeline
```

---

## 1. What it produces

A playable Heroes 3 / VCMI map from a single integer seed:

- **terrain**: a handful of large designed-looking zones with corpus-textured borders,
  optional seas/lakes or an island archipelago;
- **gameplay objects**: towns, mines, dwellings, visitables — corpus densities, corpus
  *placement tendencies*, H3 mapmaking conventions (random-class objects, guarded mines,
  guarded zone gates);
- **vegetation**: a corpus-fitted Gibbs marked point process per zone — forests, mountain
  stands, clearings — that never buries gameplay and never disconnects the map;
- **pickups**: unguarded resources along routes, guarded caches in pockets, artifacts by
  tier, roaming monsters;
- **water**: flotsam/sea chests/buoys/boats/whirlpools/wrecks + coastal shipyards;
- **playability**: N player slots wired to owned start towns at zone centroids, a team
  matrix, victory = defeat all;
- **outputs**: an editor-quality PNG render (real 32 px H3 sprites) and an editor-valid
  `.vmap` (optionally installed into the VCMI editor's Maps folder).

Everything is deterministic in the seed and verified by 36 tests (`uv run pytest`).

---

## 2. Design principles (the decisions that shaped everything)

1. **Learn statistics from the corpus; take identities from the ontology.**
   The 159-map `.h3m` corpus tells us *how much* and *where* (densities, covariate
   histograms, pair correlations, animation frequencies). It is never allowed to define
   *what an object is* — identity, footprint mask, terrain coupling and category always
   resolve through `ontology.py` (the single source of truth, re-derived from the editor's
   `objects.txt`). **Why:** corpus data is noisy and map-specific; the ontology is the
   game's ground truth. Mixing them caused subtle identity bugs before this rule existed.

2. **Fit models where the corpus has signal; use explicit rules where it doesn't.**
   Placement intensities and vegetation interactions are *fitted* (counting estimators —
   closed-form, no optimizer, deterministic). But e.g. "monsters guard zone passages"
   is a *rule*: the corpus gate-distance histogram for guards is nearly flat because corpus
   zone "gates" are wide terrain borders. Since v5 the generator reproduces exactly that
   topology — gates are corpus-wide open BANDS of the contact front (§7.1), not 1-tile
   corridors — and the passage-guard convention stays a rule asserted at each band's
   centre.

3. **Hard guarantees are constructed, not hoped for.**
   Reachability is guaranteed by construction (a protected walkable web that vegetation
   may never block) and then *verified* by a map-level gate (G2) with a repair fallback.
   In practice all seeds pass "G2 clean" — the repair is a safety net, which is exactly
   the point: statistical layers get freedom because the constructive layer owns safety.

4. **Determinism everywhere.** One seed → one map, byte-for-byte. Every RNG is
   `random.Random(seed ^ zone-specific-constant)`; no wall-clock, no unseeded randomness.
   This makes bugs reproducible and lets tests assert exact re-runs.

5. **Follow real H3 mapmaking conventions** (user-mandated, validated against the corpus
   where measurable): most placed objects are the editor's RANDOM classes; guard strength
   tracks guarded value; mines are guarded (corpus: 64%); zone entrances are guarded;
   only top-tier fixed dwellings are placed deliberately.

---

## 3. Architecture and data flow

```
                 maps/*.h3m ──extract_faithful──▶ maps_json/*.json  (the corpus)
                                                        │
              ┌──────────────── corpus mining (cached in data/pp/) ────────────────┐
              │  macro_stats.json      veg_<terrain>.json      gameplay_stats.json │
              │  (macro_topo.mine)     (pp_stats.mine)         (pp_gameplay v5)    │
              └────────────────────────────────────────────────────────────────────┘
                                                        │
 seed ──▶ L0  macro_topo.generate      zones, water mask, textured borders
             │
          zone_engine.tile_terrain     corpus-learned autotiling (transition views)
          zone_engine._segment_level   same-terrain flood-fill zones (water/rock = barriers)
             │
          per land zone:
 L3          pp_gameplay.place_zone    towns/mines/dwellings/visitables + guards + shipyard
             pp_sample.protected_web   walkable backbone + gate BANDS + every approach tile
 L2          pp_sample.sample_zone     Gibbs marked point process vegetation
 L4          pp_pickup.place_pickups   resources/artifacts/caches/roaming guards
          per water body:
             pp_pickup.place_water     flotsam/chests/buoys/boats/whirlpools/wrecks
             │
          guard dedupe  ──▶  g2_repair (map-level reachability gate)
             │
          render_editor.render_map ──▶ out/render/pp/ppmap_s<seed>.png
          pp_map.export_vmap        ──▶ out/vmap/ppmap_s<seed>.vmap
          pp_map.apply_playability  ──▶ players / teams / victory / town ownership
          (--install)               ──▶ ~/.var/app/eu.vcmi.VCMI/.../Maps/pp-gen/
```

**Module inventory** (the solution proper):

| module | role |
|---|---|
| `macro_topo.py` | L0 macro terrain: mining, water mask, zone growth, border texture |
| `pp_stats.py` | corpus vegetation statistics (intensity, pair correlation, coverage) |
| `pp_sample.py` | Gibbs vegetation sampler + the protected walkable web |
| `pp_gameplay.py` | L3 gameplay stats mining + placement + guards + shipyards |
| `pp_pickup.py` | L4 pickups, guarded caches, water-body population |
| `pp_map.py` | orchestration, G2 gate, export, playability overlay, CLI |
| `test_pp.py` | 20 tests: legality, determinism, G2, export, playability, selection, gate bands, mine economy, banks, loot, variety audit |

Shared infrastructure (pre-existing, reused): `zone_engine.py` (autotiling, segmentation,
`generate --layout pp` CLI), `zone_field.py` (`edge_dist`, `_zone_gates`,
`_farthest_points`, geodesic paths), `markov_terrain.py` (learned terrain conditionals,
used *only* for border texture), `ontology.py`, `obj_resolve.py`, `faithful.py` +
`vmapwrite.py` (`.vmap` writer), `render_editor.py` (sprite-true renderer).

---

## 4. L0 — macro terrain (`macro_topo.py`)

**Problem.** The earlier causal 3-tile Markov chain reproduced *border texture* well but
its patch sizes decay geometrically — generated maps fragmented into many small zones,
while corpus maps hold a handful of LARGE designed regions.

**Decision: plan macro structure first, use the Markov chain only where it is good.**

1. **Mine** (`mine_macro` → `data/pp/macro_stats.json`): corpus zone-area distribution,
   per-terrain area shares, terrain adjacency mix, per-map water fraction.
2. **Water mask** (`_water_mask`): low-frequency value noise thresholded at the water-
   fraction quantile → coherent seas/lakes rather than salt-and-pepper.
   - `water_mode="none"`: fraction 0 (pure land).
   - `water_mode="normal"`: fraction drawn from the corpus distribution, noise wavelength
     `min(W,H)//5` (large blobs — seas and lakes).
   - `water_mode="islands"`: fraction `uniform(0.45, 0.60)`, wavelength `min(W,H)//10`.
     **Why the finer wavelength:** water share alone doesn't make an archipelago; halving
     the noise cell fragments the land into separate islands.
3. **Plan**: zone target areas are drawn from the corpus area distribution and rescaled to
   exactly fill the land budget (floor `MIN_ZONE_AREA=40`) — the corpus zone-size
   distribution is imposed **by construction**. Seeds are spread with a minimum-distance
   rejection sampler; terrains are assigned by Metropolis on the seed k-NN graph with
   energy −log A[tᵢ][tⱼ] (adjacent zones prefer corpus-frequent terrain pairs; same-terrain
   contact is corpus-impossible and strongly repelled — otherwise segmentation would merge
   the zones and the size distribution would drift).
4. **Grow** (`_grow`): capacity-constrained multi-source Dijkstra with jittered edge costs
   (`JITTER=1.4`). Each zone claims tiles by increasing noisy distance until its target
   area is reached. **Why jitter:** zero jitter gives Voronoi-straight fronts; the noise
   produces organic interlocking borders like hand-drawn maps.
5. **Texture** (`_texture_boundaries`): isotropic Gibbs sweeps of the learned 4-neighbour
   terrain conditional, **restricted to a `BAND=2` tile band around zone borders** with
   everything else clamped. Boundaries get corpus transition texture; interiors can never
   fragment. This is why `markov_terrain.py` is retained as a dependency.

---

## 5. Tiling and segmentation (`zone_engine`)

`tile_terrain` converts the macro grid to writer-ready cells, despeckling and choosing the
H3-correct **transition view** per tile (shorelines, dirt-grass edges) from corpus-learned
autotiling. `_segment_level` then flood-fills same-terrain zones.

**Important semantics: water and rock are segmentation *barriers*, never zones.** Every
consumer must handle this — it bit us twice (see the water layer, §11, and corpus water
mining, §6.3).

---

## 6. L3 — gameplay objects (`pp_gameplay.py`)

### 6.1 Corpus statistics (v4, `data/pp/gameplay_stats.json`)

One pass over the corpus, per land terrain (zones ≥ `MIN_AREA_STATS=60`):

- per-purpose **counts** and **animation frequencies** (density and mix);
- **border open fraction** (v5): the share of a zone's contact-front tiles (tiles
  4-touching another zone) not covered by any blocking footprint — measured ≈ 0.39–0.54
  per terrain. This is the statistic that sizes the generated **gate bands** (§7.1);
- per-purpose **covariate histograms** — counts by:
  - edge-distance bin (`EB=6`, BFS distance to the zone rim),
  - gate-distance bin (`GB=4`, `min(d//3, 3)` — since v5 measured from the **full contact
    front**, matching the generator's wide bands),
  - openness bin (`OB=4`, `min(n//7, 3)` where n = open tiles in the 5×5 window of the
    veg-only open field) — used for pickups only;
- **tiles per bin** (the exposure normalizers — without them the histogram just reflects
  how much area each bin has);
- **guardedness**: fraction of resource piles / reward pickups / **mines** with a GUARD
  anchored within Chebyshev 3 (proxy for "this object is guarded"). Measured values on
  grass: piles 0.54, pickups 0.59, mines 0.64 — the corpus confirms the H3 convention
  that mines are usually guarded;
- a **water pass** (see §11): purpose densities over raw water tiles per map, because
  water zones don't exist in segmentation.

The stats file is versioned (`STATS_VERSION=5`); a version mismatch triggers a re-mine.
**Why a version field:** the mining schema evolved five times; silent reuse of a stale
cache produced confusing "missing key" failures until versioning made staleness explicit.

### 6.2 The intensity fit (where objects *go*)

Placement anchors are sampled from a per-purpose log-linear intensity

```
lam_p(u) ∝ exp( th_e[ebin(u)] + th_g[gbin(u)] (+ th_o[obin(u)] for pickups) )
th_x[b]  = clip( log( ((cnt[b]+0.5) / (tiles[b]+0.5/base)) / base ), ±2 ),  base = count/tiles
```

a **counting (Poisson regression with one-hot covariates) fit**: each θ is the
Laplace-smoothed log relative intensity of that covariate bin versus the zone-wide
average. **Why this estimator:** it is closed-form, deterministic, robust on small
per-terrain counts, and directly interpretable (θ_e for TOWN peaks deep in the zone;
shrines peak near routes — the fit reproduces what mapmakers do without hand-tuned
rules). Clipping at ±2 stops empty bins from producing infinite repulsion/attraction.

Anchors: `rng.choices(zone_tiles, weights=exp(θ...), k=80)` with a 25-step spiral nudge
around each failed candidate. Sampling (not argmax) keeps variety across seeds.

### 6.3 Counts

Per purpose: `stoch(density × area, scaled_cap)` — stochastic rounding of the corpus
expectation with **area-scaled** soft caps: `scaled_cap(base, x) = max(base,
ceil(1.5 · x))` with bases `TOWN 1, MINE 8, DWELLING 6, VISIT 12, BANK 4`. The corpus
expectation `density × area` is the real driver; the cap only stops outliers.
**Why area-scaled (v5):** the old fixed caps clamped every large zone to the same ~27
objects — a 5,000-tile zone on a 144×144 map got no more than a 600-tile one, leaving big
maps visibly empty. A town additionally requires `area ≥ TOWN_MIN_AREA=150` (unless
forced — §13), and a zone **with a town always gets `n_mine ≥ 2`** (its economy pair,
§6.5). **Why stochastic rounding:** deterministic rounding would give every 300-tile
grass zone the identical object count; the Bernoulli fraction keeps the corpus mean while
varying per zone/seed.

### 6.4 Identity selection — the RANDOM-class convention

**Decision (user-mandated, corpus-consistent): favour the editor's RANDOM classes.**

| purpose | random share | random identity |
|---|---|---|
| TOWN | 70 % | `randomTown` (avcranx0) |
| DWELLING | 80 % | 30 % generic `randomDwelling`, 70 % `randomDwellingLvl 1–7`, level weights (22,18,15,13,12,10,10) — skewed low like real maps |
| RESOURCE_PILE | 60 % | `randomResource` (avtrndm0) |
| REWARD_PICKUP | 45 % | random artifact by tier: treasure 50 / minor 30 / major 15 / any 5 |
| GUARD | 100 % | `randomMonsterLevel 1–7` (avwmon1..7), level computed from guarded value |

**Why:** (a) it is how real H3 maps are authored — the game rolls concrete objects at
start, giving replayability; (b) leveled random monsters are what makes *guard strength ∝
reward* implementable without a creature-strength database. Mines stay concrete (a
sawmill is a sawmill).

**Lesson learned (recorded in memory so it is never "fixed" backwards):** the labeled
colored-disc sprites these render as (red "MON 4", green "DWELLING LEVEL 2") are the
**genuine editor sprites** present in the H3 LODs — an early version misdiagnosed them as
missing-sprite placeholders and filtered every random class out with
`"random" not in type`, which was exactly wrong.

Fixed identities (the remaining shares) come from the ontology pools weighted by corpus
animation frequency, **square-root damped with a 20× penalty for repeats within a zone**.
**Why:** raw frequency weighting made rare visitables (Star Axis, Garden of Revelation,
Library of Enlightenment) lose the lottery to fountains every single time — the user
noticed they *never* appeared. Damping + no-repeat gives them a real chance while keeping
common objects common. `VISIT_PURPOSES` covers STAT_PERMANENT, SPELL_SKILL, BONUS_TEMP,
MANA and INFO (witch huts, observatories, huts of the magi).

**Creature banks on land (v5).** `BANK` (Dragon Utopia, the seven creature banks, Crypt,
Pyramid — 1,285 land occurrences in the corpus) is placed like a visitable with its own
corpus density (~1–3 per 1000 tiles per terrain) and intensity fit, identities from
`gameplay_pool(terrain, "BANK")` with the same damping. Banks get **no approach guard** —
the bank IS the fight. Before v5 `BANK` was only reachable on water (shipwrecks), so
utopias/conservatories/crypts never appeared on generated land.

**Variety audit.** `python -m vcmi_mapgen.pp_gameplay --audit` (and the matching test)
verifies every corpus (purpose, animation) on land resolves through the ontology and is
reachable through a generator pool — whitelisting TRANSPORT (relational) and GUARD
(random monsters by design), and mapping the fort-less town sprite variants to their
canonical `..x0` ontology towns. The audit is green.

### 6.5 The mine economy

A zone with ≥ 2 mines is guaranteed a **sawmill + ore pit** first (the H3 economy
convention); a zone with a **town** always has ≥ 2 mines, and its sawmill + ore pit are
anchored by an exhaustive nearest-first scan **around the town** (typically Chebyshev
4–7) — every start economy is next to its town. Further mines are drawn over **distinct**
resource types without replacement, weighted by corpus frequency. **Why distinct:**
corpus zones essentially never hold three sawmills; without the constraint frequency
weighting produced exactly that.

**Map-level coverage (v5).** `pp_map.build` threads a shared `ledger` through the zones
(sorted-zid order, deterministic): while any of the six basic resources (wood, ore,
mercury, sulfur, crystal, gems — `BASIC_MINE_RES`) is missing map-wide, zones draw their
next mine type from the **missing** set first — so every map covers all six minerals.
**Gold is the deliberate exception**: a gold mine may only be drawn while
`gold_placed < towns − 1` (player towns pre-counted, neutral towns added as zones roll
them) — gold mines pay off only on multi-town maps. Both abandoned-mine variants are
excluded from the rotation. The build log prints `mines all-basics=yes gold=G/quota`.

**Mines sit in vegetation.** Mines are placed before vegetation (L3 before L2), so
instead of moving mines to the greenery, the vegetation is attracted to the mines: the
Gibbs sampler applies a `+ATTRACT=0.7` log-intensity bonus in a Chebyshev-3 annulus
around every MINE footprint (`attract` tiles; approaches and the protected web stay hard
zeros). Sawmills end up nestled in forest, gem ponds in growth — the H3 look — without
any reachability cost.

### 6.6 Hard placement rules

Gameplay objects are rigid (unlike vegetation): `_fits` requires the full footprint
in-zone, at least `GAP=2` free tiles from every other gameplay footprint (via an inflated
`near` set), no squatting on an earlier object's reserved approach tile, and the object's
own approach tile in-zone and standable. Masks anchor bottom-right; `'X'` cells are
entered from below, `'A'` cells are walk-on (`_cells` computes the approach).
**Why GAP=2:** gameplay's neighbour should be *vegetation* (which fills the gap
organically), not another building — corpus maps don't pack buildings wall-to-wall.

### 6.7 Guards (the crosscutting system)

All guards are leveled random monsters (`rnd_monster(lvl)` → avwmon1..7), single-tile
passable-visitable `['A']` masks, purpose `GUARD` — which the G2 gate treats as
*removable* (a hero can fight through), so guards may legally sit **on** the walkable web.

- **Mine guards (v5.3 — always guarded, never bypassable)**: placed **on the mine's
  approach tile unconditionally** (the corpus-probability roll was removed — a user
  bug report found unguarded mines), level from resource rarity:
  `MINE_GUARD_LVL = {sawmill/orePit/waterWheel/windmill/mysticalGarden 3,
  alchemistLab/sulfurDune 4, gemPond/crystalCavern 5, goldMine 6, abandoned 5}` (+1 with
  25 % chance). **Why on the approach:** in H3 you must fight the guard to flag the mine;
  the approach tile is exactly that chokepoint, and being a removable GUARD it never
  breaks reachability. The other 4 of the 5 `visitableFrom` approach-grid tiles (the
  ones the guard does NOT sit on) are each sealed with a single-cell blocking decoration
  (`seal_cell`, purpose `MINE_SEAL`, from `ontology.decor_pool(terrain, blocking=True,
  max_cells=1)`) — otherwise a hero could walk around the guard from the side, which a
  user playtest caught.
- **Gate guards**: each zone gate **band** (§7.1) is guarded at its **narrowest**
  (openness-minimal) tile — not the band centroid, which is usually the widest, most
  open part of the front — with probability 0.65, level `min(7, 1 + area//250 (+1 with
  40 % chance))` — bigger, richer zones defend harder. One guard per passage (a single
  monster cannot cork a corpus-wide band, and being a removable GUARD it never breaks
  reachability). **Why a rule, not a fit:** measured per-tile guard rates by corpus gate
  distance are flat (9.9/13.4/12.2/11.6 per 1000 tiles) — corpus borders are wide; the
  rule instead asserts guards gate the true chokepoint within that wide border.
- **Corridor dedupe** (in `pp_map.build`): both zones flanking one corridor may guard the
  same passage; any two GUARDs within Chebyshev 2 keep only the stronger
  (`randomMonsterLevelN` compares lexically for N=1..7). Removed 16–19 duplicates per
  72×72 map.
- **Cache guards and roaming guards**: §9.

### 6.8 Shipyards

If the zone has coastal tiles (land 4-adjacent to water), a shipyard is placed on the
shore with probability `min(0.8, dens_WATER_TRANSPORT × area × 3)` — the ×3 boost
compensates for conditioning on coastal zones only (the mined density is diluted over all
zones). Identity from the ontology pool (fallback `avxshyd0`); placement tries up to 150
shuffled coastal anchors through the same `_fits` gate. **Why:** the user's map review
("no shipyards, water unusable") — shipyards + boats + whirlpools make water part of the
game graph.

---

## 7. The protected web (`pp_sample.protected_web`)

### 7.1 Gate bands — corpus-wide zone borders (v5)

Corpus zone "gates" are **wide terrain borders**, not corridors. The generator used to
collapse each border to a single representative tile (`zone_field._zone_gates`) and let
vegetation wall the rest — producing 1-tile passages the corpus never has.

`zone_field._zone_gate_bands(ts, zones, zid, open_frac)` returns, per neighbouring zone,
`(rep, band)`: the representative tile plus a **band** of the full contact front around
it — `max(3, round(open_frac × front_len))` tiles, where `open_frac` is the corpus
**border open fraction** mined per terrain (≈ 0.39–0.54; §6.1). The whole band is added
to the protected set, so vegetation may never seal it: generated borders measure ≈ 0.6–0.7
open (the band is a guaranteed floor; the Gibbs process leaves extra front open on its
own). Isolated pockets still get the synthesized antipodal pair. Gate-distance covariates
(mining and generation alike) measure from the full front/band, keeping the fitted
intensities consistent with the new topology.

### 7.2 The web

The constructive reachability skeleton, built **after** gameplay and **before**
vegetation:

- nodes = farthest-point samples over the zone interior (spacing `ZF.SPACING`) + every
  gate-band representative + **every gameplay approach tile** (`extra_nodes`);
- edges = geodesic paths connecting each remaining node to the nearest connected node
  (a spanning tree — no cycles needed for the guarantee);
- every gate-band tile is protected (the wide border stays open);
- gameplay footprints (`avoid`) are impassable, so corridors route *around* towns.

The resulting tile set `prot` is a **hard zero** for the vegetation sampler: no blocking
cell may cover it. Combined with bands on both sides of every border, this makes every
gameplay object reachable from every neighbouring zone *by construction*.
**Why farthest-point nodes:** uniform spacing covers the zone with minimal corridors,
leaving maximal freedom to the vegetation process.

---

## 8. L2 — vegetation (`pp_stats.py` + `pp_sample.py`)

The visual heart of the generator: a **Gibbs marked point process on object
configurations** (not tiles), fitted per terrain from the corpus.

### 8.1 Fitted statistics (`pp_stats.mine`, `data/pp/veg_<terrain>.json`)

- first-order intensity `lam[cat][edge_bin]` (anchors per tile by rim-distance bin,
  `EBINS` shared with `zone_field`);
- **multitype pair correlation** `g[a][b][r]` over Chebyshev rings r = 0..`RMAX=6`,
  with an exact pair-denominator (validated against brute force in tests). Ring r=0
  measures footprint **stacking** — overlap is corpus-legal for vegetation, so it is
  *learned and priced*, not banned;
- mark mix `anim_w[cat][anim]` (sprite frequency within a category — spatial statistic
  only; identity resolves through the ontology);
- coverage target `veg_blocked_frac` (fraction of zone tiles under a blocking cell);
- coarse-cell (`CELL=6`) count overdispersion — the Cox-field variance statistic.

### 8.2 The sampler (`sample_zone`)

Birth/death Metropolis–Hastings, `STEPS_PER_TILE=40` proposals per zone tile, with
Papangelou conditional intensity

```
lam*(u, c) = exp(alpha) · lam[c][ebin(u)] · M(u) · exp(E(u, c))
```

Three deliberate structural decisions:

1. **Two-scale split.** A purely attractive pairwise Gibbs process is **explosive** —
   corpus log g(r) > 0 at all ranges compounds into one runaway clump. So the large-scale
   density variation (forest masses vs clearings) is carried by `M(u)`, a **log-Gaussian
   Cox** modulation: seeded value noise (cell `COX_CELL=7`) whose σ is *fitted* by
   inverting the corpus coarse-cell Fisher index (`cox_sigma`). The pairwise term keeps
   only the **local** structure.
2. **Geyer saturation.** `E(u,c)` sums background-normalized potentials
   `theta = log(g(r)/g(4))` over rings r ≤ `RINT=2` with each (category, ring) neighbour
   count capped at `SAT=2` (Geyer 1999) — λ* stays bounded, the chain stays stable.
3. **Emergent coverage, steered.** There is **no vegetation hard core** — footprints may
   stack (r=0 potential prices it). The blocking-union coverage is steered to the corpus
   `veg_blocked_frac` by a global log-offset `alpha` updated on a Boolean-model
   (coverage-exponent) schedule. Typical zones land within a few points of target
   (logged as `blocked 0.52/0.53` per zone).

Hard zeros only where the game needs them: blocking cells may not leave the zone, may not
touch the protected web, and may not touch the `forbid` set (gameplay footprints +
approach tiles). Everything else is the process's freedom.

---

## 9. L4 — pickups (`pp_pickup.py`)

Runs over the **finished** open field (`zone − veg-blocked − gameplay − approaches`), so
it sees the actual pockets and corridors the vegetation created.

- **Counts** from the same stats (`stoch(dens × area)`, area-scaled caps with base
  floors RESOURCE 16 / REWARD 8 / GUARD 9 — §6.3); zones ≥ `LOOT_FLOOR_AREA=300` always
  yield ≥ 2 reward pickups.
- **Guarded caches in genuine pockets (v5.3 — fixed from a user bug report that
  resource pockets were "underutilized" with resources placed mostly in the open).**
  `guard_frac` floors were raised to **0.6** minimum for RESOURCE_PILE/REWARD_PICKUP, so
  most of the guarded budget now actually lands in pockets rather than open scatter.
  Pocket candidates are no longer picked by a soft openness proxy — a "nook" measured by
  openness alone was routinely just a sparse-but-open area, not a real bottleneck.
  Instead `zone_skeleton.distance_transform(reach)` computes true Chebyshev clearance
  from the zone's open set, and a pocket's mouth must have `distance_transform <=
  POCKET_DT=2` (a genuine 1–2 tile neck, matching `skeleton()`'s own chokepoint
  convention), still requiring web-distance `dweb ≥ POCKET_DWEB=3`; candidates sort
  deepest-first with used pockets suppressed within Chebyshev 6. The cache is 1–3
  resources + possibly a tiered random artifact within Chebyshev 2 of the pocket center;
  the **guard sits on the pocket's mouth** — the reachable spot nearest the web — so the
  reward is behind the fight. Cache-origin pickups are tagged `o["cache"]=True`
  (informational only; the vmap exporter ignores unknown keys) so tests and tooling can
  separate guarded-pocket loot from unguarded scatter. **Guard level tracks cache
  value**: value = 2 per resource + 3/5/8 per treasure/minor/major artifact;
  `lvl = 1 + (v≥4) + (v≥7) + (v≥10) + (v≥13)` (+1 with 25 % chance).
- **Unguarded scatter** near routes: intensity-weighted (edge + gate + openness
  covariates) draws with minimum separations (resources 3, rewards 5) — resources string
  along roadsides like in real maps because that is where the corpus puts them.
  **Scatter rewards are LOOT (v5):** the random-artifact share drops to
  `SCATTER_ART_SHARE=0.15` for unguarded draws, so scatter comes from the fixed
  REWARD_PICKUP pool weighted by the corpus mix — which the **treasure chest** dominates
  (9,071 of ~14.5k corpus reward pickups), with **campfires**, scholars and the rest
  behind it. Tiered random artifacts live behind cache guards where they belong.
- **Roaming guards**: off-web only (never on the protected corridors), separation 7,
  levels 1–4 weighted (30/30/25/15).

All pickups and guards are **removable** in the G2 model — they may sit anywhere open
without threatening connectivity.

---

## 10. Water layer (`pp_pickup.place_water`)

**Lesson learned:** water is a segmentation *barrier* — there are no water zones, so a
per-zone water pass silently mined **zero** tiles. Both the corpus miner and the
generator therefore treat water as **raw connected components**:

- mining: per map, all `t==8` tiles and the purposes anchored on them (337 k corpus water
  tiles → densities per 1000: REWARD_PICKUP 8.9 (flotsam-dominated), BANK 1.6 (wrecks),
  BONUS_TEMP 1.3 (buoys/mermaids), TRANSPORT 0.64 (whirlpools), WATER_TRANSPORT 0.21
  (boats), INFO 0.26 (bottles), GUARD 0.16);
- generation: each water body ≥ 25 tiles is populated by purpose using those densities,
  identities from the ontology's water pools weighted by corpus animation mix
  (`allow_random=False` — random artifacts on water would be wrong), uniform placement
  with separation 4, full-footprint legality. Sea guards are random monsters L2–5.

Boats + shipyards + whirlpools make the water **navigable**, answering the review point
"water is not navigable and left empty".

---

## 11. Guard-strength model (summary table)

| guard | level source |
|---|---|
| mine approach | resource rarity (`MINE_GUARD_LVL`, always-on floor: common 3 → goldMine 6) |
| zone gate (narrowest band tile) | zone size: `1 + area//250` (+1 @ 40 %) |
| cache mouth | cache value: resources + artifact tier |
| roaming | 1–4, weighted low |
| sea | 2–5, weighted low |

This is the direct answer to review point 1 ("monster strength disproportionate to
reward") — every guard's level is *computed from what it guards*, which is only possible
because guards are leveled random monsters.

---

## 12. G2 — the map-level validity gate (`pp_map.g2_repair`)

**Model.** Open land = all land tiles minus blocking cells, where pickups/GUARDs count as
passable (removable) and gameplay bodies/water/rock are walls. Targets = every gameplay
approach tile + every pickup anchor.

**Gate.** 8-connected BFS from the first target; all targets must be reached.

**Repair.** Up to 6 rounds: one Dijkstra over the whole field pricing vegetation-blocked
tiles at 40 and open tiles at 1 (gameplay = wall), then carve the cheapest corridor to the
first *land-connectable* unreachable target, deleting only DECORATION objects.
**Islands decision:** targets on other islands are legitimately boat-reachable only —
they must *not* abort the loop, so the carver picks the first bad target that is actually
in the Dijkstra tree and gives up only when none is. **Why vegetation-only carving:**
deleting gameplay would silently change the map's content; vegetation is the only layer
that is both expendable and dense enough to be the actual blocker.

**Empirical result:** every seed tested generates "G2 clean" — zero carving needed —
because the protected web already guarantees connectivity. The gate stays as the
verification layer (principle 3).

---

## 13. Playability overlay

### 13.1 Player-zone selection (`select_player_zones`)

Candidates: land zones ≥ 60 tiles, preferring "real" zones (≥ 100 tiles **and** ≥ ¼ of the
largest — the pool widens only if that leaves fewer than N). The first pick is the largest
zone; each next pick **greedily maximizes the minimum centroid distance** to the zones
already chosen (tie-break: area desc, then zid).
**Why greedy farthest-point:** the previous top-N-by-area rule could start all players in
one corner (the user noticed). Max-min distance is the standard dispersion heuristic —
deterministic, O(N·|zones|), and measured on seed 7 (4 players, 72×72) it yields starts in
four different quadrants with min pairwise distance 29.5 tiles.

### 13.2 Guaranteed centroid towns

A designated player zone passes `force_town=True` into `place_zone`: the town is anchored
so its **footprint center** sits on the zone centroid (masks anchor bottom-right, hence a
+(w−1)/2, +(h−1)/2 target offset), scanning zone tiles **nearest-first, exhaustively**.
**Why exhaustive:** the normal 80-sample intensity lottery could fail to place a forced
town; the exhaustive scan succeeds whenever the footprint fits anywhere in the zone, which
is what "ensure there are enough towns" requires. A failed forced town prints a warning
and the surplus-neutral-town top-up fills the slot as last resort.
**Why the centroid:** the start town should anchor its zone (H3 layout convention and the
user's explicit request); neutral density-rolled towns still use the *fitted* deep+gate-far
intensity, preserving the learned look elsewhere.

### 13.3 Header + object patch (`apply_playability`)

Applied to the exported `.vmap` (a zip of JSON):

1. **Slots**: exactly N playable (`canPlay: "PlayerOrAI"`), slot i's `mainTown` pointed at
   town i (−2,−2 anchor offset), all other slots hard-disabled. `allowedFactions` is
   removed from playable slots (starts are usually `randomTown` — any faction).
2. **Ownership — the critical fix**: the town **object** gets `options.owner = <slot
   color>` in `objects.json`. The header's `mainTown` alone does **not** assign ownership
   — without the owner field the town stays neutral and the player spawns town-less
   (found by comparing against a real RandomMaps template after the user reported towns
   not assigned).
3. **Teams**: `team` int per playable slot; VCMI allies equal ids. CLI specs: `ffa`,
   `2v2`-style group notation, or an explicit `0,0,1,1` list (`parse_teams`); an invalid
   spec falls back to FFA with a warning rather than crashing a long batch.
4. **Victory = defeat all**: `triggeredEvents` set to exactly the canonical
   `standardVictory` (`standardWin` condition) + `standardDefeat` (7 days without town),
   icons/messages to the stock "Defeat all enemies" ids, any special conditions stripped.

**Why a post-export patch rather than extending the writer:** `faithful.to_vmap` is the
proven, identity-critical writer shared with the rebuild pipeline — the overlay keeps
playability concerns out of it entirely and owns them deterministically.

---

## 14. Export and rendering

- **`.vmap`** via `pp_map.export_vmap` → `faithful.to_vmap`: every object carries its full
  ontology identity (`type`, `subtype`, `animation`, `mask`); visitables get
  `visitableFrom` (else the editor warns); the header template comes from a real VCMI
  RandomMaps map. Round-trip verified (re-read: correct levels, objects, slots).
- **PNG** via `render_editor.render_map`: real 32 px H3 sprites decoded from the local
  LODs (all four DEF compression formats), painter's order by (y, x). What you see is
  what the editor shows — including the colored-disc random-class sprites.
- Outputs land in gitignored `out/render/pp/` and `out/vmap/`. `--install` (explicit
  opt-in) copies `.vmap`s into `~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/pp-gen/` — a
  dedicated subfolder so the whole batch is trivially removable.

---

## 15. CLI reference

```
uv run python -m vcmi_mapgen.pp_map
  --seed N          base seed (default 3)
  --size N          map W=H (default 72)
  --water F         explicit water fraction override
  --water-mode M    none | normal | islands   (default normal; batch cycles all if unset)
  --players N       playable slots; N zones get guaranteed centroid towns (default 2)
  --teams SPEC      ffa | 2v2 | 0,0,1,1       (default ffa)
  --vmap            export playable .vmap
  --batch N         generate N maps (seeds seed..seed+N-1), water modes cycling; implies --vmap
  --install         copy .vmap(s) into VCMI Maps/pp-gen/ for the editor

uv run python -m vcmi_mapgen.zone_engine generate --layout pp \
  --seed N --size N --players N --teams SPEC --water-mode M
```

**Batch diversity decision:** each batch entry varies the seed (terrain, zones, every
placement) *and* cycles the water mode normal → islands → none unless pinned — so a batch
of 9 gives three visibly different map archetypes, three seeds each.

---

## 16. Determinism

- Every layer derives its RNG as `random.Random(seed ^ zone_or_body_id · large-odd ^ salt)`
  with distinct salts per module (`0x5EED` gameplay, `0x5EA` water, …) — zones are
  independent of each other's draw order.
- Corpus mining is pure aggregation (no RNG); fitted parameters are closed-form.
- No wall-clock, no unseeded `random`, no dict-order dependence on iteration where it
  matters (`sorted()` before every weighted draw).
- Tests assert exact equality of two same-seed runs at each layer.

---

## 17. Testing (`test_pp.py`, 20 tests; full suite 36)

| test | guards |
|---|---|
| pair-denominator vs brute force, ring partition | the pair-correlation estimator's geometry |
| sampler determinism | L2 exact reproducibility |
| protected-web hard zero | no blocking cell ever on the web |
| gameplay legality + determinism | footprints in-zone, no overlap, approaches standable, GAP separation; GUARD exemptions (guards sit on approaches/gates by design, must be random monsters) |
| forced town at centroid | footprint center within 1.5 tiles of the centroid |
| player-zone selection | far corners beat a big adjacent zone; small zones excluded; deterministic |
| pickup legality + determinism, guards off-web | L4 rules |
| G2 carves vegetation only | repair never deletes gameplay |
| vmap export round-trip | visitableFrom, slot wiring |
| playability overlay | exact N slots, mainTown wiring, teams, **town object owners**, standardWin only |
| team parsing | ffa / NvN / explicit list / mismatch error |
| macro determinism + big-zone share | L0 |
| gate bands wide + deterministic, protected web covers bands | §7.1 — borders never collapse to 1-tile corridors |
| town zone holds sawmill + ore pit near the town | §6.5 start economy |
| mine ledger covers basics, gold ≤ towns − 1 | §6.5 map-level economy |
| banks placed on land, legal footprints | §6.4 BANK placement |
| scatter rewards mostly fixed loot (chests present) | §9 loot policy |
| variety audit green | §6.4 corpus variety reachable |

Renderer reliability (10 tests) and mapeval (6) round out the suite. LOD-dependent tests
skip cleanly on machines without the H3 sprite files.

---

## 18. Decision log (chronological, with the evidence)

1. **Markov chain demoted to border texture** — generated patch sizes decay
   geometrically; corpus zones are large. Macro planning imposes the size distribution by
   construction (§4).
2. **Counting fits over learned models with optimizers** — closed-form, deterministic,
   inspectable; the corpus is small per terrain (§6.2).
3. **Two-scale vegetation (Cox × Geyer-saturated Gibbs)** — a purely attractive pairwise
   process explodes; splitting scales is the textbook stabilization and it matched corpus
   overdispersion (§8.2).
4. **Vegetation overlap is learned, not banned** — corpus decorations legally stack;
   banning it flattened the texture (§8.1).
5. **Constructive web + verification gate, not repair-first** — all seeds are G2-clean;
   repair exists but almost never fires (§7, §12).
6. **RANDOM-class objects favoured; disc sprites are genuine** — user mandate matching
   real mapmaking; enables value-scaled guards; the placeholder misdiagnosis is
   documented so it never regresses (§6.4).
7. **Guard strength computed from guarded value** — tables in §11; the only principled
   way to satisfy "strength ∝ reward" without a creature database.
8. **Gate guards are a rule, not a fit** — corpus gate histogram is flat; the convention
   is topology-relative (§6.7).
9. **Visit-pool damping (√ + repeat penalty)** — rare visitables never appeared under raw
   frequency weighting (§6.4).
10. **Water mined and populated as raw components** — water zones don't exist;
    the per-zone pass silently returned zero tiles (§10).
11. **Town ownership lives on the object** — `mainTown` alone leaves towns neutral;
    verified against a real template map (§13.3).
12. **Centroid towns via exhaustive nearest-first scan** — sampling could fail a forced
    placement; exhaustive scan makes the guarantee real (§13.2).
13. **Player zones by greedy max-min centroid distance** — top-N-by-area clustered
    starts; dispersion is what "not next to each other" means (§13.1).
14. **Playability as a post-export patch** — keeps the proven identity-critical writer
    untouched (§13.3).
15. **G2 must tolerate islands** — off-island targets are boat-reachable, not failures
    (§12).
16. **Repo pruned to this solution + the core engine** (2026-07): region-graph
    prototypes, the GAN/patch-quilt vegetation stack and the zone-graph planner were
    deleted; `markov_terrain` (border texture), `zone_skeleton` (zone_field dependency),
    `render`, and `mapeval` (the planned M5 yardstick) were deliberately kept.
17. **Gate bands replace 1-tile corridors** (v5, 2026-07) — corpus zone gates are wide
    terrain borders; the generator now protects a corpus-open-fraction band of every
    contact front (§7.1). Gate guards stand at band centres; gate-distance covariates
    re-mined from full fronts (STATS_VERSION 5).
18. **Area-scaled caps** (v5) — fixed per-zone caps left 144×144 maps mostly empty; the
    corpus expectation `density × area` now drives counts, caps only stop outliers (§6.3).
19. **BANK placed on land** (v5) — utopias/creature banks/crypts existed only on water;
    now corpus-density land placement, no approach guard (§6.4).
20. **Map-level mine economy** (v5) — a shared ledger guarantees all six basic resources
    map-wide, rations gold to towns − 1, and anchors every town's sawmill + ore pit next
    to the town; vegetation is attracted to mine surroundings (§6.5).
21. **Scatter rewards are loot** (v5) — unguarded scatter draws chests/campfires (corpus
    mix), random artifacts drop to a 15 % share outside caches (§9).
22. **Variety audit** (v5) — `pp_gameplay --audit` proves every corpus (purpose, anim) is
    generator-reachable; found and closed the BANK gap, documented the TRANSPORT/GUARD
    exclusions and the fort-less town sprite variants (§6.4).

---

## 19. Known limitations / future work

- **mapeval acceptance band** (spec M5 remainder): score generated maps against the
  corpus reach/dist/balance bands and gate batches on it — `mapeval.py` is kept for this.
- **Start fairness** is dispersion-only: no resource/value mirroring between player zones
  yet. The natural next step is balancing zone value vectors (mines by type, dwelling
  levels) across players.
- **Single surface level** — no underground, hence no subterranean gates/two-level play.
- **No roads/rivers**, no placed heroes, no quest/portal relational chains (the corpus
  *replay* pipeline handles those; the generator does not synthesize them yet).
- **Random dwellings carry no faction/level linkage options** in the export; VCMI applies
  its defaults. If the editor complains, extend the object options in
  `apply_playability`-style post-processing.
- **Guard levels are heuristic bands**, not a fitted value model — good enough to satisfy
  the convention, but a corpus fit of guard-strength-vs-guarded-value would be more
  principled.
