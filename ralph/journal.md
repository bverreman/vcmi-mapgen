# Ralph journal

Append-only. Each loop iteration adds a short note: what task, what changed, the
verify result (object-distance, load-test). Newest at the bottom.

- (seed) Ralph loop scaffolded. Baseline: standalone map loads with 2 cosmetic
  warnings; fit to "Dawn of War" gives object-distance ~2.4, total ~34.
- Playability: realizer tags root town as fm["main_town"] (anchor + (-2,-2), the
  empirically-measured corpus mainTown->town-anchor offset). to_vmap gives player 0
  (blue) mainTown={generateHero:True,...}, others None. Verify PASS: loaded:True,
  fatal:False, 2 warns, object-distance 2.43. (load-test needs ~110s wait on a cold
  mod cache; verify.sh's wait=70 can flake on the first cold run, passes warm.)
- Traversability gate: added src/traverse.py (tile-level BFS from start town over
  passable land; asserts all zones/towns/mines reachable). Wired into verify.sh
  step 1. It exposed a real stranding bug -- town entrance tiles weren't reserved,
  so a dwelling could seal a town's only approach (seeds 23/100 even sealed the
  START town -> whole map unreachable). Fixed: realize.reserve_approach() reserves
  town side/below entry tiles. Verify PASS: 24/24 zones reachable, obj-distance
  2.32 (was 2.43), load loaded:True/2 warns. (seed=23 still has 1 rare blocked
  mine -- gate flags it; mine-approach reservation left for a later pass.)
- Tree-depth fidelity: BFS fill ran out of node budget before descending, capping
  depth ~5 vs real 7. deps_gen now extends the deepest leaf into a thin chain down
  to max_depth when BFS falls short (no-op when depth already met). Dawn of War fit
  reaches depth 7: struct 4.5->2.0, obj-distance 2.18, load loaded:True/2 warns,
  seed=4 24/24 reachable. First tried a root-seeded "spine" but its bushy fill made
  many parent->child zones non-adjacent (unrealizable chokepoints -> 6/12 seeds
  sealed, incl. seed=4); also a decor-accounting tweak that didn't net-help on
  density -- both reverted. The leaf-extension reshapes only one branch, so the
  organic layout keeps each new node adjacent to its parent. Density rose slightly
  (~+2 on the forest-heavy Dawn of War, from the few extra deep barrier zones).
- Density outliers: added src/deps_density.py (fits a map, prints per-purpose
  real-vs-gen density residuals sorted by |diff|). On "King of Pain" it pinpointed
  GUARD as the outlier: gen 23.1 vs real 11.8/1k -- the per-treasure guard layer
  force-guarded every mine/dwelling/bank (88 of them) regardless of budget, so 107
  guards landed where ~61 were wanted. Fixed: guards now cap at the GUARD budget,
  spending it on must-objects (mine/dwell/bank) first. King of Pain density
  17.0->10.6, total 32.7->25.7, obj-dist 2.9->2.5 (slightly better). Dawn of War
  unchanged (obj-dist 2.18, total 34.4), seed=4 24/24 reachable, loads/2 warns.
  Guards stay passable-after-combat so the traversability gate is unaffected.
- Multi-map fit report: added src/deps_report.py. Fits an evenly-spaced, sorted
  slice of the corpus (deterministic, stable across runs) and writes a markdown
  table to out/fit_report.md (per-map total/density/terrain/struct/obj-dist +
  best seed, column averages, worst obj-dist flagged). Read-only tooling; does
  not touch generation. Baseline N=12/seeds=4: avg obj-dist 2.72, avg total 78.4.
  It surfaces the large-map weak spot that the single held-out gate (Dawn of War,
  2.18) hides: The Five Rings 3.98, Twins 3.36, Search for the Grail 3.24 all
  exceed the 3-tile bar -- a candidate next target. Verify PASS: seed=4 24/24
  reachable, obj-dist 2.18, load loaded:True/2 warns.
- Underground level: 103/159 corpus maps are TWO-LEVEL, and rock(9) is the single
  biggest terrain (31% of all tiles) -- our surface-only generator produced 0% of
  it, so 2-level targets had huge terrain AND density distance (gen divided object
  counts by 1x tiles, real by 2x). Added: feats_gen counts both levels; tiles now
  2x for 2-level. params_from_target(two_level): land biomes exclude subterranean(6)
  & rock(9) (previously 6 leaked onto the surface), surface water frac doubled.
  New build_underground() builds level 1 = rock fill + one connected subterranean
  cavern grown by noise-weighted BFS from the gate tile, a subterranean-gate PAIR
  sharing (x,y) (VCMI auto-links across levels), and the second half of each gameplay
  purpose's per-1x budget scattered + guarded inside the cavern (entrances forced
  into the cavern so they're reachable). All guarded by params["two_level"], so the
  1-level default path is byte-identical (seed=4: 1 level, 1402 objs, 24/24). Marshland
  Menace fit total 208.2->47.4 (density 102->4, terrain 81->18, obj-dist 4.8->3.6;
  struct 1.1->7.6 from the 2x-tile region rate, net hugely positive). 2-level map
  load-tested: loaded:True, 0 fatal, 0 visitable warns (980 surface + 164 cavern
  objs; underground 4387 rock + 797 subterranean). Dawn of War gate held at 2.18.
- Two-level playability gate: made traverse.py level-aware -- passable_grid(fm,l)
  blocks water AND rock(9), counts only objects on level l (old code blocked the
  surface with underground footprints). New _gate_links() reads subterranean-gate
  pairs (both ends share x,y) and BFS now teleports across levels at gate approach
  tiles, so cavern objects are reachable only THROUGH a reachable surface gate.
  obj_reachable / zone / town / mine checks all carry the object's level. This
  exposed a cavern-stranding bug: in build_underground the scattered MINE/DWELLING/
  BANK objects (the only ones with B footprints; guards/most rewards are 'A'-only)
  walled the gate off from the cavern body -- the gate's own B cells pinch its single
  'BAB' entrance, so seed 0 reached cav=1/797 and seed 10 cav=1. Fixed two ways:
  (1) reserve the gate A-cell + its 8-ring so nothing seals the descend point;
  (2) connectivity-preserving placement -- track `reach` (cavern tiles walkable from
  the gate), require each object's entrance in `reach`, and reject any footprint
  whose B cells would strand an already-reachable tile. Result: 0 stranded underground
  objects across all 8 Marshland seeds (vs up to 28 before), and seeds 4/6/11 fully
  traversable end-to-end. Wired into verify.sh as step 1b. The change is neutral-to-
  positive on fit (Marshland obj-dist 3.6->2.76, total 47->45; Dawn of War 2.18
  unchanged) and both 1-level (seed=4) and 2-level (Marshland seed=4) maps load
  (loaded:True, fatal:False, 2 known mod warns). Remaining 2-level ok=False seeds are
  SURFACE mine stranding (separate from the underground; reachability_repair tail).
- Cosmetic warnings probe (closed, non-actionable): the 2 "Animation  failed to load"
  warnings are NOT from our objects -- a zero-object map still emits them. They trace
  to the user's broken mods (hota/phoenixHorde.def not found, third-upgrades dup
  buildings) during mod init, independent of the map. No generator fix applies.
