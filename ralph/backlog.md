# Ralph backlog

Highest priority first. The loop works the top unchecked `[ ]` item each iteration,
marks it `[x]` with a one-line result, and commits. Add new tasks at the bottom.

- [x] Playability: set a player-owned starting town (+ generated hero). Realizer now
      records the root-zone town as `fm["main_town"]` (anchor-2,-2 = corpus offset);
      `faithful.to_vmap` sets player 0's `mainTown={generateHero:True,...}` and nulls
      the rest. Loads (loaded:True, 2 cosmetic warns), object-distance 2.43.
- [x] Global traversability gate: new `src/traverse.py` BFS-walks passable land
      (through carved chokepoints) from the start town and asserts every zone, town
      and mine is reachable; wired into `verify.sh` step 1 (fails on unreachable).
      Exposed + fixed a stranding bug: town entrance tiles weren't reserved, so a
      later dwelling could seal a town (even the start town -> whole map unreachable).
      `realize` now reserves town approach tiles. seed=4 24/24 zones, obj-dist 2.32.
- [x] Tree-depth fidelity: BFS fill exhausted the node budget on shallow levels and
      capped depth ~5. `deps_gen` now extends the single deepest leaf into a thin
      chain down to `max_depth` when BFS falls short (no-op once depth is met, so
      shallow maps + seed=4 are byte-identical and stay reachable). Dawn of War fit
      now reaches depth 7 (was 5): struct 4.5->2.0, obj-distance 2.18. A root-seeded
      spine was tried first but reshaped the tree enough to seal zones (6/12 seeds
      unreachable) -- the leaf-extension avoids that.
- [x] Density outliers: new `src/deps_density.py` prints per-purpose real-vs-gen
      residuals (sorted) for any map. It exposed GUARD as the dominant outlier on
      "King of Pain" (real 11.8 vs gen 23.1/1k): the realizer force-guarded EVERY
      mine/dwelling/bank, ~doubling guard count. Fixed: the per-treasure guard layer
      now caps total guards at the GUARD budget, spending it on must-objects first.
      King of Pain density 17.0->10.6, total 32.7->25.7, obj-dist 2.9->2.5; Dawn of
      War held at obj-dist 2.18; seed=4 24/24 reachable, loads (2 warns).
- [x] Multi-map fit report: `src/deps_report.py` fits an evenly-spaced slice of the
      sorted corpus and writes `out/fit_report.md` (per-map total/density/terrain/
      struct/obj-dist + seed + column averages, worst obj-dist flagged). Baseline
      N=12/seeds=4: avg obj-dist 2.72, avg total 78.4. Exposes the large-map weak
      spot (The Five Rings 3.98, Twins 3.36, Search for the Grail 3.24 all > 3).
- [x] Underground level: 65% of the corpus is two-level. `deps_fit.params_from_target`
      now detects `m.two_level` (routes subterranean/rock off the surface, doubles the
      surface water frac) and `feats_gen` counts both levels. New
      `deps_realize.build_underground` builds level 1 (rock fill + one connected
      subterranean cavern grown from the gate) + a linked subterranean-gate pair, and
      scatters the second half of each gameplay purpose's budget into the cavern.
      Guarded by `two_level` so the 1-level default is byte-identical. Marshland Menace
      fit total 208->47 (terrain 81->18, density 102->4, obj-dist 4.8->3.6); 2-level map
      loads (loaded:True, 0 fatal/visit warns); Dawn of War held at 2.18; seed=4 24/24.
- [x] Cosmetic: NOT a generator bug. The 2 "Animation  failed to load" warnings persist
      with a ZERO-object map -> they come from the user's broken HotA/third-upgrades mods
      (hota/phoenixHorde.def missing etc.), not our objects. No substitution possible or
      needed; loaded:True/fatal:False regardless. Left as an environment artifact.
- [x] Two-level playability: `traverse.py` is now level-aware (per-level passable
      grid, rock(9) walls the underground) and BFS-walks ACROSS levels by teleporting
      at subterranean-gate approach tiles (`_gate_links`), so cavern objects count as
      reachable only if the surface gate is reachable and the cavern is connected to
      it. Exposed + fixed a cavern-stranding bug: scattered MINE/DWELLING/BANK
      footprints (the only B-carrying underground objects) walled the cavern off from
      the gate (seed 0 cav=1). Fix in `build_underground`: reserve the gate's A-ring,
      and connectivity-preserving placement -- an object's entrance must be reachable
      FROM the gate and its footprint must not strand any already-reachable cavern
      tile. Now 0 stranded underground objects across all 8 Marshland seeds; new
      verify.sh step 1b asserts this. Marshland obj-dist 3.6->2.76; Dawn of War held
      at 2.18; seed=4 24/24; both 1- and 2-level maps load (loaded:True, 2 warns).
- [ ] Underground object-distance: 2-level fits still sit ~3.5 obj-dist (Marshland 3.6).
      The cavern scatter ignores the target's spatial signature (sep / town-bias) that
      the surface scatter uses -- carry the signature into build_underground.
