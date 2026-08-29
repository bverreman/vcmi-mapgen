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
| 8 | **Corpus-wide run** | batch extract + rebuild --identity --verify across all 159 maps; zone catalog with stats | Done — 159/159 OK, 2989 zones, 355 273 objects; `zone_engine corpus` |
| 9 | **PP-map generation pipeline** | `generate --layout pp` → playable `.vmap` with loot zones, pocket caches, guards, seaports, vegetation | In progress |

### M9 progress

`zone_engine generate --layout pp` produces a `.vmap` + zone-overlay PNG. Features landed:

- **Loot zones** — interior sealed by blocking vegetation on all passable boundary tiles; no interior guards; strong exterior guard; filled with high-value treasures, artifacts, and level-3/4 shrines; accessible only from exterior zone via a single keyed gate.
- **Pocket caches** — 2–14-tile concave pockets detected and filled; 2-tile pockets unguarded; ≥3-tile pockets get a guard + artifact; footprint overlap with gameplay objects prevented.
- **Zone-border guards** — every zone-crossing passable tile blocked by a guard; guard footprints cleared of overhead vegetation.
- **Seaports** — ≥1 placed per zone that borders a water body >30 tiles; restricted to ≤4 tiles from coastline and exactly 1 tile from shore; rendered in the overlay.
- **Vegetation fill** — dense decoration fill with interactive-object priority for loot zones and pockets; wide-mouth and open-field pockets excluded from artifact drops.
- **Zone overlay** — auto-rendered alongside plain PNG; build cached after first generate so re-renders are fast.

Remaining for M9: mine distribution, hero start-zone tuning, batch verify across seeds.

## Standing constraints

- **Same-shape identity is bit-exact** — never re-roll object identity (no subtype resampling),
  so relational portals/quest links survive. `rebuild --identity --verify` is the gate.
- **VCMI objects are fixed-size tile objects** — a stretch scales positions, never sprites; no
  illegal overlaps, gameplay must stay reachable.
- **Inspection is real editor sprites** in the VCMI editor / `render_editor.py`, not dots.
