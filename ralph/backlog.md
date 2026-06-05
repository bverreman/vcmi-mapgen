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
- [ ] Tree-depth fidelity: the generator caps depth ~4 vs real 5–7, inflating the
      `struct` distance. Raise `max_depth`/branching in `src/deps_gen.py` so a fit to
      "Dawn of War" lowers struct distance, WITHOUT raising object-distance above 3.
- [ ] Density outliers: some maps fit poorly on density (e.g. "King of Pain" ~35).
      Add a small script to print per-purpose real-vs-gen residuals for a given map,
      find the worst purposes, and adjust the scatter so they match.
- [ ] Multi-map fit report: script `src/deps_report.py` that fits N corpus maps and
      writes `out/fit_report.md` (table: total/density/terrain/struct/obj-dist per
      map + averages). Use it to track regressions.
- [ ] Underground level: generate 2-level maps (surface + underground rock + a few
      subterranean gates) so 2-level targets (e.g. "All for One") match on terrain.
- [ ] Cosmetic: resolve the 2 "animation failed" load warnings (identify the object
      type/animation that fails and substitute a valid one from the corpus pools).
