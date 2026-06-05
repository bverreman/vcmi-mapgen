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
