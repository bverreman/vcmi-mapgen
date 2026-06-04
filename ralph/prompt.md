# Ralph loop — VCMI hand-made map generator

You are running inside an autonomous loop. **Each run is a FRESH session with no
memory of previous runs.** All state lives in files and git history. Do ONE useful
unit of work, verify it, commit it, and exit. Be decisive; don't ask questions.

Working directory: `/mnt/data/workspace/vcmi-mapgen`

## Mission
Improve the dependency-tree map generator (`src/deps_*.py`) so its output is
statistically and structurally indistinguishable from the 159 real HoMM3 maps in
`~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/*.h3m`, and is fully playable. The
architecture: corpus stats → abstract dependency tree → organic 2D embedding
(zones + mountain barriers + guarded chokepoints + portals) → realization
(biomes, water, guards, gated rewards, ambient density, roads, vegetation) →
editor-valid `.vmap`. See `docs/` for the design.

## Do this, every iteration
1. Read `ralph/backlog.md`. Pick the SINGLE highest-priority unchecked `[ ]` task.
2. Read the relevant code. Make the SMALLEST change that advances that one task.
3. Run the fitness gate: `bash ralph/verify.sh`. It must exit 0.
4. If it fails, fix it or `git checkout -- .` to revert. NEVER commit a failing state.
5. If it passes: mark the task `[x]` in `ralph/backlog.md` with a one-line result,
   append a 2-3 line note to `ralph/journal.md`, then:
   `git add -A && git commit -m "ralph: <short task description>"`.
6. If every task is `[x]`: append one new high-value task to the backlog (something
   that measurably reduces the fit distance in `src/deps_fit.py` or improves
   playability), OR if you judge the work complete, create an empty `ralph/STOP`
   file and stop.

## Hard rules — never violate
- `bash ralph/verify.sh` MUST pass (exit 0) before any commit.
- The **object-distance** metric must stay **≤ 3 tiles** — it is the core quality bar.
- The generated map MUST load in the editor (`loaded:True`, no fatal errors).
- Do NOT copy generated maps into `~/.var/app/.../Maps/` except the single temp file
  the verify/load-test uses. Generated artifacts live in `out/`.
- Do NOT delete or regenerate from scratch: `out/deps.json`, `out/objlib.json`,
  `out/factors.json`, `out/spatial_prior.json` (the corpus-derived priors).
- One task, one commit, per iteration. Keep diffs small and reviewable.

## Where things are
- Fitness/metrics: `src/deps_fit.py` (object-distance, density, terrain, struct).
- Spatial signature: `src/deps_spatial.py`.
- Generator: `src/deps_gen.py` (tree), `src/deps_embed.py` (2D), `src/deps_realize.py`
  (realization), `src/faithful.py` (writer), `src/deps_prior.py` (corpus prior).
- Load test: `src/gate.py` `load_test()` (headless editor; needs xvfb + flatpak).
- Renders for inspection: `src/deps_render.py`, `src/deps_showcase.py`.
