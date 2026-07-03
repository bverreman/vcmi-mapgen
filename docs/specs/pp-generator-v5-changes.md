# PP generator v5 — change record (2026-07-03)

**What this is:** the implementation record of the v5 change set — six user requests, what
changed for each, exactly how it was changed (functions, constants, mechanisms), and the
measured results. The as-built reference is
[pp-map-generator-solution.md](pp-map-generator-solution.md) (updated in place); this file
is the delta and the reasoning trail.

**The six requests:**

1. Zone gates should be wide like corpus terrain borders, not 1-tile corridors.
2. Every town must have an ore pit and a sawmill close to it.
3. Mines must cover ALL mineral categories map-wide; ideally sit next to vegetation;
   gold mines optional, tied to town count.
4. More gameplay objects — 144×144 maps stood mostly empty; libraries, dragon utopias,
   witch huts, monster dens (creature banks) should appear.
5. Treasure chests and campfires as unguarded loot.
6. The generated maps should reach the corpus's variety of visitable objects — audit that
   everything is in the ontology.

**Verification state:** full suite 36/36 passing (7 new tests), `pp_gameplay --audit`
green, 7 verification maps (144×144 4p + 6-map batch across water modes) all `G2 clean`
with `mines all-basics=yes` and gold within quota.

---

## 1. Wide zone borders — gate bands (`zone_field.py`, `pp_sample.py`, `pp_gameplay.py`)

**Problem found.** `zone_field._zone_gates` computed the *full* per-neighbour contact
front (`contacts[o]`, every zone tile 4-touching that neighbour) and then collapsed it to
a single representative tile (the front tile nearest the front centroid). The protected
web protected only that tile, so the L2 vegetation sampler legally walled every border
down to a 1-tile corridor. Corpus zone "gates" are wide open terrain borders — the very
reason the corpus gate-distance histogram is flat (solution spec §2, principle 2).

**How it was changed:**

- `zone_field._zone_fronts(ts, zones, zid)` (new) — extracts the old contact-front
  computation into a reusable helper returning `{neighbour_zid: [front tiles]}`.
  `_zone_gates` is unchanged in behaviour, now implemented on top of it.
- `zone_field._zone_gate_bands(ts, zones, zid, open_frac=0.5, min_w=3)` (new) — returns
  `[(rep, band)]` per neighbour: `rep` is the same representative tile as `_zone_gates`,
  `band` is the `k = min(front_len, max(min_w, round(open_frac × front_len)))` front tiles
  nearest `rep` (Chebyshev, tie-broken by tile tuple — deterministic). Isolated pockets
  keep the synthesized antipodal gate pair, each with a small Chebyshev-`min_w//2` border
  band.
- **`open_frac` is mined, not invented**: `pp_gameplay.mine_gameplay` (stats **v5**) adds
  `border_open_frac` per terrain — the fraction of corpus zone contact-front tiles not
  covered by any blocking footprint (measured over ALL objects, not just decoration).
  Measured: dirt 0.50, grass 0.42, snow 0.41, rough 0.43, swamp 0.39, lava 0.41,
  sand 0.49, subterr 0.54.
- `pp_sample.protected_web(..., open_frac=0.5)` — gate-band representatives are the
  spanning-tree nodes (as before), and after the tree is built **every band tile is added
  to `prot`**, so no blocking vegetation cell may ever cover the band. The border cannot
  collapse below the corpus open fraction by construction.
- **Gate-distance covariate consistency**: corpus mining now measures `gate_dist` from
  the **full contact front** (`_zone_fronts` union) instead of the single collapsed tile,
  and generation (`place_zone`, `place_pickups`) measures it from the band union. The
  fitted `th_g` intensities therefore describe the same geometry on both sides.
- **Gate guards** (`place_zone`): the old loop guarded every `gd == 0` tile — correct
  when a gate was one tile, but it would have spammed a guard per band tile. Now exactly
  one guard per passage, at the band's `rep` (probability 0.65 and the
  `1 + area//250` level rule unchanged). A single monster cannot cork a corpus-wide band;
  it stands in the open border like corpus guards do. Corridor dedupe in `pp_map.build`
  (Chebyshev-2, keep the stronger) is unchanged and still catches facing pairs.

**Measured result:** generated border open fraction 0.69 on seed 7 / 72×72 (corpus
0.39–0.54 — the band is a guaranteed *floor*; the Gibbs process leaves some extra front
open since the zone-wide coverage target, not the rim, is what it steers).

## 2. Town ⇒ sawmill + ore pit adjacent (`pp_gameplay.place_zone`)

**Finding first:** the planned "key bug" was NOT real — `ON.mines_by_resource` keys by
resolved *subtype* (`"sawmill"`, `"orePit"`, `"goldMine"`, …), not by resource name, so
the existing `n_mine >= 2 ⇒ sawmill + orePit first` guarantee did fire. What was missing
was the town linkage: no forced mine count for town zones and no spatial relation.

**How it was changed:**

- `if n_town: n_mine = max(n_mine, 2)` — a town zone always has its economy pair.
- The placement loop tracks `town_center` (footprint centre, set when the TOWN settles;
  masks anchor bottom-right, hence the `−(w−1)/2, −(h−1)/2` offset) and
  `town_mines_left = 2`. The first two MINE entries — by construction the guaranteed
  sawmill + ore pit, they head the `wanted` list right after TOWN — use an **exhaustive
  nearest-first scan** of all zone tiles sorted by squared distance to `town_center`
  (the same pattern as the forced-town centroid scan) instead of the intensity lottery.
  They land as close as legality (GAP=2 separation, approach standability) admits;
  anything else falls through to the fitted-intensity draw unchanged.
- Mine approach guards apply unchanged (sawmill lvl 1 / ore pit lvl 2 — cheap early
  flags, as in real maps).

**Measured result:** seed 7 / 72×72 — all three towns have both mines within
Chebyshev 4–7. Guarded by `test_town_zone_gets_wood_and_ore_next_to_town` (≤ 12 across
three seeds).

## 3. Map-wide mineral coverage, rationed gold, mines-in-vegetation

**Problem.** Mine types were drawn per zone independently (distinct within a zone,
corpus-frequency weighted) — nothing guaranteed mercury/sulfur/crystal/gems existed
anywhere on a map. Gold was in the normal rotation regardless of town count. Mines had no
relation to vegetation (which is sampled *after* them).

**How it was changed:**

- **`BASIC_MINE_RES`** (new constant, `pp_gameplay.py`): `sawmill, orePit, alchemistLab,
  sulfurDune, crystalCavern, gemPond` — the six basics. (Verified: every land terrain's
  ontology pool carries all of them, so coverage is feasible on any terrain mix.)
- **The ledger** (`pp_map.build` → `place_zone(..., ledger=...)`): one shared dict
  `{"missing": set(BASIC_MINE_RES), "towns": len(player_zids), "gold": 0}` threaded
  through zones in sorted-zid order (deterministic). In the mine-type rotation:
  - while `ledger["missing"] ∩ available` is non-empty, the draw is **restricted to the
    missing set** (still corpus-frequency weighted within it) — so the map covers all six
    basics as early as its mine slots allow; the guaranteed sawmill/orePit also tick off
    wood/ore;
  - **gold gate**: `goldMine` is only in the draw pool while
    `ledger["gold"] < max(0, ledger["towns"] − 1)`. Player towns are pre-counted;
    neutral towns increment `towns` as their zones roll them. Gold on a 1-town map: none.
  - both abandoned-mine variants are excluded from the rotation (the pre-existing
    `"abandoned"` key, plus the `"mine"` subtype of the AB `abandonedMine` class that had
    been slipping through).
- `place_zone` keeps working with `ledger=None` (single-zone tests/tools unaffected).
- `build` logs `mines all-basics=yes|NO gold=G/quota towns=N` in the info line and prints
  a WARNING when a (tiny) map could not cover the basics.
- **Mines next to vegetation** — inverted: mines are L3, vegetation is L2, so instead of
  moving mines to greenery the greenery is attracted to the mines.
  `pp_sample.sample_zone(..., attract=...)` (new param) applies a `+ATTRACT = 0.7`
  log-intensity bonus (`att` grid added inside the exp of the Papangelou intensity, in
  BOTH the birth and death evaluations — detailed balance preserved). `pp_map.build`
  computes `attract` = zone tiles within Chebyshev 3 of any MINE blocking cell, minus
  `forbid`. Approach tiles and the protected web remain hard zeros, so the attraction
  can never cost reachability.

**Measured result:** all 7 verification maps `all-basics=yes`; gold 0/2, 1/4, 1/2, 2/3,
0/2, 2/3, 8/12 — always within quota. Guarded by
`test_mine_ledger_covers_basics_and_rations_gold` (invariant `gold ≤ max(0, towns−1)`,
missing-first draws).

## 4. Full maps: area-scaled caps + creature banks on land

**Problem.** Counts were `stoch(density × area, cap)` with FIXED caps (`MINE 8,
DWELLING 6, VISIT 12`, pickups `16/8/9`). Intended as pathological-zone guards, on a
144×144 map they clamped every large zone to the same ~27 gameplay objects a 600-tile
zone gets — the "mostly empty" symptom. Separately, purpose `BANK` (Dragon Utopia, the 7
creature banks, Crypt, Pyramid — 1,285 land anchors in the corpus) was mined into the
stats but never placed on land: it appeared only in `WATER_PURPOSES` (shipwrecks).

**How it was changed:**

- **`scaled_cap(base, expectation) = max(base, ceil(1.5 × expectation))`**
  (`pp_gameplay.py`, used by both `place_zone` and `place_pickups`): the corpus
  expectation `density × area` is now the real driver of counts; the cap only stops
  outliers at 1.5× it. TOWN stays hard-capped at 1 per zone.
- **BANK on land** (`place_zone`): `n_bank = stoch(dens["BANK"] × area, scaled_cap(4, ·))`
  (corpus land densities ≈ 1–3 per 1000 tiles); identities via
  `pick(ON.gameplay_pool(terrain, "BANK"), "BANK")` — corpus-frequency weighted with the
  same √-damping + within-zone repeat penalty as visitables; placed through the standard
  `_fits` gate (footprint in-zone, GAP=2, approach standable) between dwellings and
  visitables in the `wanted` order. **No approach guard** — a bank is its own fight; its
  approach tile still joins the protected web and the G2 target set like any gameplay
  object. The e/g covariate histograms for BANK already existed in the stats (BANK was in
  `ALL_PURPOSES`), so the intensity fit needed no schema change.

**Measured result:** 144×144 seed 7 went from ~27-object big zones to 45–67 gameplay
objects per big zone — 1,402 gameplay+pickups total, G2 clean. Seed 7 / 72×72 banks:
2× griffinConservatory, impCache, dwarvenTreasury, medusaStore, crypt (+1 shipwreck on
water). Guarded by `test_banks_placed_on_land_and_legal`.

## 5. Treasure chests + campfires as unguarded loot (`pp_pickup.py`)

**Problem.** Both are `REWARD_PICKUP` and the treasure chest dominates the corpus mix
(9,071 of ~14.5k reward anchors; campfires 790), but 45 % of every reward draw became a
tiered random artifact — scatter and caches alike — so plain loot was diluted.

**How it was changed:**

- `_pick(..., art_share=0.45)` (new param): the random-artifact branch probability for
  REWARD_PICKUP is now caller-controlled.
- **Unguarded scatter** passes `SCATTER_ART_SHARE = 0.15`: 85 % of scatter rewards come
  from the fixed pool weighted by corpus `anim_w` — which the chest dominates, campfires,
  scholars, sea-chest-likes behind it. **Guarded caches keep 0.45** (they draw the tiered
  random artifacts explicitly anyway) — artifacts sit behind the cache guards where the
  guard-value model prices them.
- **Loot floor**: `LOOT_FLOOR_AREA = 300`; any zone at least that big gets
  `n_art = max(n_art, 2)` — a real zone never generates loot-free.
- Pickup counts use `scaled_cap` (see §4).

**Measured result:** seed 7 / 72×72 reward mix — 24 treasureChest, 3 campfire,
2 scholar, vs 18/10/3 random treasure/minor/major artifacts (mostly cache-bound).
Guarded by `test_scatter_rewards_are_mostly_loot` (≥ 50 % fixed loot, chests present).

## 6. Corpus-variety audit (`pp_gameplay.audit_variety`, `--audit`)

**How it works.** `python -m vcmi_mapgen.pp_gameplay --audit` (plus
`test_audit_variety_green`): for every `(purpose, animation)` with a nonzero corpus count
on any land terrain (from the v5 stats `anim_w`), assert

1. the animation resolves through the ontology (`has_animation`), and
2. it is reachable through a generator pool — its purpose is in the placed set and the
   animation is in `gameplay_pool(t, purpose)` for at least one terrain (water included),
   or it is an editor RANDOM class (placed by convention).

Documented equivalences/exclusions instead of false alarms:

- `AUDIT_EXCLUDED`: **TRANSPORT** (monoliths/portals are relational — pairing is out of
  scope, solution spec §19) and **GUARD** (guards are leveled random monsters by design;
  corpus guard identities are never reproduced).
- `TOWN_SPRITE_VARIANTS`: the corpus's fort-less town sprites (`avccast0`, `avcramp0`, …
  and `avcrand0`) are the same gameplay objects as the ontology's forted `..x0` towns —
  fort state is a town option, not a distinct visitable. The audit maps them to their
  canonical before checking.

**What it caught while being built** (each fixed, then re-run to green):

- the **BANK gap** — utopias/creature banks/crypts unreachable on land (fixed in §4);
- boats (`avxboat0/1/2`) flagged because reachability originally checked land pools only
  — fixed by including the water pool (boats place via `place_water`);
- the town sprite variants above.

**Result:** `AUDIT OK: every corpus (purpose, animation) on land is reachable`. Exit code
1 on any gap, so it can gate CI/batches.

---

## Stats schema: v4 → v5 (`data/pp/gameplay_stats.json`)

`STATS_VERSION = 5`; a version mismatch triggers an automatic re-mine (done, committed
alongside). Changes:

| field | change |
|---|---|
| `border_open_frac` | NEW per terrain — corpus zone-border open fraction (sizes gate bands) |
| `g`, `tiles_g` | re-measured — gate distance now from the FULL contact front, not the collapsed 1-tile gate |
| everything else | unchanged semantics, re-mined values |

## Files touched

| file | change |
|---|---|
| `zone_field.py` | `_zone_fronts` + `_zone_gate_bands` (new); `_zone_gates` refactored on top, behaviour unchanged |
| `pp_gameplay.py` | stats v5 (border openness, full-front gd); `scaled_cap`; town⇒`n_mine≥2` + town-adjacent economy pair; ledger-driven mine types + gold gate; BANK placement; band-centre gate guards; `audit_variety` + `--audit` CLI |
| `pp_sample.py` | `protected_web` protects gate bands (`open_frac` param); `sample_zone` `attract` bonus (birth AND death) |
| `pp_pickup.py` | `scaled_cap` counts; `art_share` split (`SCATTER_ART_SHARE=0.15`); `LOOT_FLOOR_AREA=300`; band-based gate distance |
| `pp_map.py` | mine ledger + coverage warning + info-line report; `open_frac` pass-through; mine-annulus `attract` computation |
| `test_pp.py` | 7 new tests (gate bands ×2, town economy pair, mine ledger, land banks, loot scatter, audit green) — suite 29 → 36 |
| `docs/specs/pp-map-generator-solution.md` | updated in place: §2, §6.1, §6.3–6.5, §6.7, §7 (new 7.1), §9, §17, decision log 17–22 |

## Determinism

All new draws go through the existing per-zone RNG streams; the ledger mutates only
across the deterministic sorted-zid zone order; `_zone_gate_bands` sorts neighbours and
tie-breaks band tiles by tuple. Same seed ⇒ same map, asserted by the pre-existing
determinism tests (unchanged and passing).
