# VCMI Map Generation — Roadmap

## Goal

Reproduce and adapt hand-made VCMI (Heroes of Might & Magic 3) maps with a **shape-driven
zone-rebuilding engine**: segment a map into same-terrain zones, record each zone's object
pattern in a shape-relative frame, and replay it onto a target shape. Same shape ⇒ bit-exact;
larger shape ⇒ the same objects at the same relative placement on a larger tile grid. A
learned Markov terrain generator supplies new terrain.

## Read this first

The technical direction and the single idea behind the engine are in
**[`docs/architecture.md`](../architecture.md)**: *segment → record → replay*, with identity
as an integer-only guarantee. This roadmap is the milestone view.

## Source corpus

- 159 `.h3m` maps (RoE / AB / SoD; no HotA) in `maps/`, parsed to faithful JSON in
  `maps_json/` (`extract_faithful`). 378,699 objects, all resolved to exact identity.
- Editor-quality rendering reads the H3 sprite LOD files from a local VCMI install.

## Milestones

| # | Milestone | Outcome | Status |
|---|---|---|---|
| 1 | **Faithful corpus pipeline** | `.h3m` → faithful JSON with exact object identity + mask | Done |
| 2 | **Terrain segmentation** | same-terrain zones + interior-depth features | Done |
| 3 | **Record + bit-exact replay** | zone templates; `rebuild --identity --verify` = `IDENTITY OK 2027/2027` | Done |
| 4 | **Editor-quality rendering + reliability tests** | real 32px H3 sprites (DEF fmt 0/1/2/3); golden rebuilt==source pixel check | Done |
| 5 | **Stretch (larger shape)** | same objects at the same relative placement on a larger grid, VCMI-valid | Done |
| 6 | **Learned terrain generator** | Markov chain learned from the corpus (`markov_terrain`) | Done |
| 7 | **Arbitrary-outline warp** | oriented-bbox / depth-sweep shape coordinate (beyond rectangular stretch) | Skipped — stretch sufficient for M8; revisit if shape-transfer quality becomes a bottleneck |
| 8 | **Corpus-wide run** | every zone of every map; bridge recorded zones onto generated terrain | Next |

## Standing constraints

- **Same-shape identity is bit-exact** — never re-roll object identity (no subtype resampling),
  so relational portals/quest links survive. `rebuild --identity --verify` is the gate.
- **VCMI objects are fixed-size tile objects** — a stretch scales positions, never sprites; no
  illegal overlaps, gameplay must stay reachable.
- **Inspection is real editor sprites** in the VCMI editor / `render_editor.py`, not dots.
