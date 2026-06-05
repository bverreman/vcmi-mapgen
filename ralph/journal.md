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
