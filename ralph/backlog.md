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
- [ ] Multi-map fit report: script `src/deps_report.py` that fits N corpus maps and
      writes `out/fit_report.md` (table: total/density/terrain/struct/obj-dist per
      map + averages). Use it to track regressions.
- [ ] Underground level: generate 2-level maps (surface + underground rock + a few
      subterranean gates) so 2-level targets (e.g. "All for One") match on terrain.
- [ ] Cosmetic: resolve the 2 "animation failed" load warnings (identify the object
      type/animation that fails and substitute a valid one from the corpus pools).
